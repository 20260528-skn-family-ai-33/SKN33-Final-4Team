"""Owner-scoped adjustment inputs; legacy ML references may be frozen once."""
import calendar
from copy import copy, deepcopy
from datetime import date, datetime, timedelta
from fractions import Fraction
import hashlib
import json
from math import ceil, isclose, isfinite
import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from finance.api import cancel_info, listed_transactions, transaction_item
from finance.models import FinancialTransaction, RecurringSaving
from finance.spending import spending_summary
from spending_analysis.coverage import coverage
from spending_analysis.periods import Period
from users.models import CategoryMinimum, CategoryPriority, FinancialProfile

from .home import balance_json
from .models import Budget, BudgetCategorySetting, BudgetCycle, Category, CushionEntry, SpendingAllocation
from .overview import LIVING_CODES
from .protect import cushion_required
from .recommend import budget_input_snapshot, hash_snapshot
from .salary import salary_cycle_dates
from .scope import OUT_OF_BUDGET, living_spent


POLICY_VERSION = "budget-adjust-20261106-transport-v2"
MODES = {"INITIAL_ADJUSTMENT": "validated", "NORMAL_ADJUSTMENT": "approved"}
ML_CATEGORIES = set(LIVING_CODES) - {"ETC"}


class AdjustmentContextError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def adjustment_today(today=None):
    if today is not None:
        return today
    fixed = getattr(settings, "CHAT_TODAY", None)
    return date.fromisoformat(fixed) if fixed else timezone.localdate()


def _json(value):
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    if isinstance(value, (date, datetime, uuid.UUID)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(_json(value), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def context_fingerprint(context):
    return _hash({key: value for key, value in context.items() if key not in {"context_hash", "category_names"}})


def _money(value):
    return type(value) is int and value >= 0


def _money_map(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed) or any(not _money(v) for v in value.values()):
        raise AdjustmentContextError("invalid_budget_data")
    return deepcopy(value)


def _select_basis(user, today, base_budget_id, mode):
    profile = FinancialProfile.objects.filter(user=user).first()
    if profile is None or profile.payday is None:
        raise AdjustmentContextError("profile_not_ready")
    effective = copy(profile)
    if profile.payday_changes_on and today >= profile.payday_changes_on:
        effective.payday = profile.scheduled_payday
    if effective.payday is None:
        raise AdjustmentContextError("profile_not_ready")
    start, end, _ = salary_cycle_dates(effective, today)
    cycles = list(BudgetCycle.objects.filter(user=user, starts_on__lte=today, ends_before__gt=today)[:2])
    if len(cycles) != 1:
        raise AdjustmentContextError("no_cycle" if not cycles else "overlapping_cycles")
    cycle = cycles[0]
    if cycle.status != "active" or (cycle.starts_on, cycle.ends_before) != (start, end):
        raise AdjustmentContextError("cycle_changed")
    mine = Budget.objects.filter(user=user, cycle=cycle)
    approved = mine.filter(status="approved").first()
    ordinary = mine.filter(base_budget__isnull=True).exclude(task__task_type="budget_adjust")
    if base_budget_id is not None:
        try:
            chosen = mine.filter(pk=uuid.UUID(str(base_budget_id))).first()
        except (ValueError, TypeError):
            raise AdjustmentContextError("invalid_basis") from None
    else:
        chosen = approved or ordinary.filter(status="validated").order_by("-version_no").first()
    if chosen is None:
        raise AdjustmentContextError("no_budget" if base_budget_id is None else "invalid_basis")
    inferred = "NORMAL_ADJUSTMENT" if chosen.status == "approved" else "INITIAL_ADJUSTMENT"
    mode = inferred if mode is None else mode
    if mode not in MODES or chosen.status != MODES[mode]:
        raise AdjustmentContextError("invalid_basis")
    if mode == "INITIAL_ADJUSTMENT" and (chosen.base_budget_id or chosen.task_id and chosen.task.task_type == "budget_adjust"):
        raise AdjustmentContextError("invalid_basis")
    return profile, cycle, chosen, approved, mode


def _budget_amounts(user, chosen):
    allocations = dict.fromkeys(LIVING_CODES, 0)
    totals = {"fixed": 0, "savings": 0, "cushion": 0}
    lines = []
    for item in chosen.items.order_by("bucket", "category_id", "category_detail", "item_id"):
        if item.user_id != user.pk or not _money(item.allocated_amount):
            raise AdjustmentContextError("invalid_budget_data")
        lines.append({"bucket": item.bucket, "category": item.category_id, "category_detail": item.category_detail,
                      "reserve_id": str(item.reserve_id) if item.reserve_id else None, "amount": item.allocated_amount})
        if item.bucket == "living":
            if item.category_id in allocations:
                allocations[item.category_id] += item.allocated_amount
            elif item.allocated_amount:
                raise AdjustmentContextError("unsupported_budget_scope")
        elif item.bucket in totals:
            totals[item.bucket] += item.allocated_amount
        elif item.allocated_amount:
            raise AdjustmentContextError("unsupported_budget_scope")
    if not _money(chosen.funding_amount):
        raise AdjustmentContextError("invalid_budget_data")
    spare = chosen.funding_amount - sum(allocations.values()) - sum(totals.values())
    if spare < 0:
        raise AdjustmentContextError("invalid_budget_data")
    return allocations, totals, spare, lines


def _free_used(user, cycle, today):
    grouped = {}
    records = SpendingAllocation.objects.filter(user=user, cycle=cycle, funding_bucket="free",
        transaction__user=user, transaction__occurred_on__lte=today).select_related("transaction")
    for record in records:
        tx = record.transaction
        key = str(tx.pk)
        row = grouped.setdefault(key, {"amount": 0, "net": tx.amount - cancel_info(tx)["amount"],
                                       "outside": record.category_id in OUT_OF_BUDGET})
        if not _money(record.amount) or row["outside"] != (record.category_id in OUT_OF_BUDGET):
            raise AdjustmentContextError("invalid_spending_allocation")
        row["amount"] += record.amount
    inside = outside = 0
    for row in grouped.values():
        amount = min(row["amount"], row["net"])
        if row["outside"]:
            outside += amount
        else:
            inside += amount
    return inside, outside, _hash(grouped)


def _due_count(saving, cycle):
    if saving.transfer_day is None or saving.from_account_id is None:
        return None
    year, month = cycle.starts_on.year, cycle.starts_on.month
    count = 0
    while date(year, month, 1) < cycle.ends_before:
        due = date(year, month, min(saving.transfer_day, calendar.monthrange(year, month)[1]))
        count += cycle.starts_on <= due < cycle.ends_before
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return count


def _savings(user, cycle, today, amount, complete):
    registered = list(RecurringSaving.objects.filter(user=user, status="confirmed").order_by("pk"))
    minimum = sum(row.amount for row in registered)
    proof = {"registered": [{"id": str(row.pk), "amount": row.amount, "day": row.transfer_day,
                             "from": str(row.from_account_id), "evidence": row.evidence} for row in registered]}
    if not complete:
        return minimum, None, "spending_unverified", _hash(proof)
    period = FinancialTransaction.objects.filter(user=user, direction="out", occurred_on__gte=cycle.starts_on,
                                                 occurred_on__lt=cycle.ends_before, occurred_on__lte=today)
    if period.filter(transaction_type__in=["transfer", "own_transfer"], is_canceled=False).exists():
        return minimum, None, "savings_execution_unverified", _hash(proof)
    executed = {str(tx.pk): tx.amount - cancel_info(tx)["amount"] for tx in period.filter(transaction_type="saving")}
    proof["executed"] = executed
    remaining, taken = 0, set()
    for saving in registered:
        count = _due_count(saving, cycle)
        if count is None or not isinstance(saving.evidence, list):
            return minimum, None, "savings_schedule_unverified", _hash(proof)
        matched = set(map(str, saving.evidence)) & set(executed)
        if taken & matched:
            return minimum, None, "savings_execution_unverified", _hash(proof)
        taken |= matched
        remaining += max(saving.amount * count - sum(executed[key] for key in matched), 0)
    floor = max(minimum, sum(executed.values()) + remaining)
    return floor, max(amount - floor, 0), None, _hash(proof)


def _initial_budget(chosen):
    seen = set()
    while chosen.base_budget_id and chosen.pk not in seen:
        seen.add(chosen.pk)
        chosen = Budget.objects.get(pk=chosen.base_budget_id, user_id=chosen.user_id)
    return chosen


def _transport_reference(chosen):
    snapshot = chosen.input_snapshot or {}
    data = (snapshot.get('adjustment_context') or {}).get('transport_history_reference')
    if data is None:
        data = snapshot.get('transport_history_reference')
    if data is None and chosen.base_budget_id:
        data = (_initial_budget(chosen).input_snapshot or {}).get('transport_history_reference')
    try:
        if not isinstance(data, dict) or data.get('status') != 'ready':
            raise ValueError
        numerator, denominator = data['baseline_numerator'], data['baseline_denominator']
        months = data['months']
        dates = [date.fromisoformat(m+'-01') for m in months]
        numbers = [d.year*12+d.month for d in dates]
        digest = data['history_fingerprint']
        if (type(numerator) is not int or numerator < 0 or type(denominator) is not int or denominator <= 0
                or len(numbers) != 3 or numbers != list(range(numbers[0], numbers[0]+3))
                or any(d.strftime('%Y-%m') != m for d, m in zip(dates, months))
                or not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
                or not isinstance(data['version'], str) or not data['version']):
            raise ValueError
        return {k: deepcopy(data[k]) for k in ('status', 'months', 'baseline_numerator', 'baseline_denominator',
                                              'history_fingerprint', 'version')}
    except (KeyError, ValueError, TypeError):
        return {'status': 'unavailable'}


def _validated_reference(engine_input, required, prediction):
    try:
        source = prediction.get('source', '')
        if (prediction.get('used_fallback') is True or not isinstance(source, str)
                or source.strip().lower().startswith(('synthetic', 'fallback', 'temp'))):
            raise ValueError('non_model_reference')
        values, meta = prediction.get("ml_reference"), prediction.get("ml_reference_meta")
        if values is None:
            ratios, target = prediction.get("ratios"), engine_input.get("target")
            if (not _money(target) or not target or not isinstance(ratios, dict) or required - set(ratios)
                    or set(ratios) - (ML_CATEGORIES | set(OUT_OF_BUDGET))
                    or any(type(v) not in (int, float) or not isfinite(v) or not 0 <= v <= 1 for v in ratios.values())
                    or not isclose(sum(ratios.values()), 1, abs_tol=1e-12, rel_tol=0)):
                raise ValueError("invalid_ml_reference")
            values = {c: ceil(target * Fraction(str(ratios[c]))) for c in required}
            meta = {"source": "compatibility", "model_id": prediction.get("model_id"),
                    "model_checksum": prediction.get("model_checksum"), "target": target, "input_hash": _hash(engine_input),
                    "raw_ratios": {c: str(v) for c, v in ratios.items()}, "amount_rounding": "ceil_1_krw"}
        source = meta.get("source") if isinstance(meta, dict) else None
        if (not isinstance(source, str) or not source.strip()
                or source.strip().lower().startswith(("synthetic", "fallback", "temp"))
                or not isinstance(meta.get("model_id"), str) or not meta["model_id"].strip()
                or not isinstance(meta.get("model_checksum"), str) or len(meta["model_checksum"]) != 64
                or any(c not in "0123456789abcdef" for c in meta["model_checksum"])):
            raise ValueError("invalid_ml_provenance")
        values = _money_map(values, ML_CATEGORIES | set(OUT_OF_BUDGET))
        if required - set(values):
            raise ValueError("incomplete_ml_reference")
        if meta.get("raw_ratios"):
            raw = {c: Fraction(str(v)) for c, v in meta["raw_ratios"].items()}
            normalization = meta.get("normalization", {})
            denominator = Fraction(str(normalization.get("denominator", 1)))
            target = meta.get("target")
            if (not _money(target) or not target or denominator <= 0
                    or engine_input.get("target") != target or required - set(raw)
                    or any(v < 0 for v in raw.values())
                    or meta.get("amount_rounding") != "ceil_1_krw"
                    or any(values[c] != ceil(target * raw[c] / denominator) for c in required)):
                raise ValueError("reference_amount_mismatch")
            if normalization and (normalization.get("scope") != "all_10_categories"
                                  or normalization.get("active_categories_renormalized") is not False
                                  or sum(raw.values()) != denominator):
                raise ValueError("reference_normalization_mismatch")
        return {"engine_input": engine_input, "ml_reference": {c: values[c] for c in required},
                "ml_reference_meta": deepcopy(meta), "ml_reference_status": "ready", "ml_not_applicable": ["ETC"]}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, ZeroDivisionError):
        return {"engine_input": engine_input, "ml_reference_status": "unavailable", "ml_not_applicable": ["ETC"]}


def _references(chosen, allocations, provider, cache, *, allow_backfill=True):
    snapshot = chosen.input_snapshot or {}
    frozen = snapshot.get("adjustment_context") or snapshot
    engine_input = deepcopy(frozen.get("engine_input") or snapshot.get("engine_input") or {})
    required = set(allocations) & ML_CATEGORIES
    if frozen.get("ml_reference") is not None:
        return _validated_reference(engine_input, required, frozen)
    initial = _initial_budget(chosen)
    original = initial.input_snapshot or {}
    engine_input = deepcopy(original.get("engine_input")
                            or (original.get('adjustment_context') or {}).get('engine_input') or {})
    if original.get("ml_reference") is not None:
        return _validated_reference(engine_input, required, original)
    key = _hash({"base": str(initial.pk), "input": engine_input})
    if cache is not None and key in cache:
        return deepcopy(cache[key])
    unavailable = _validated_reference(engine_input, required, {})
    if not allow_backfill:
        return unavailable
    if provider is None:
        try:
            from .design_service import reference_from_initial_input
            provider = reference_from_initial_input
        except ImportError:
            pass
    result = unavailable
    if provider is not None and _money(engine_input.get('target')) and engine_input['target'] > 0:
        claimed = False
        # Claim once, then release the budget row before model inference.
        with transaction.atomic():
            owner = Budget.objects.select_for_update().get(pk=initial.pk, user_id=chosen.user_id)
            saved = deepcopy(owner.input_snapshot or {})
            if saved.get("ml_reference") is not None:
                result = _validated_reference(engine_input, required, saved)
            elif not saved.get("ml_reference_backfill_attempted"):
                saved["ml_reference_backfill_attempted"] = True
                saved["ml_reference_status"] = "unavailable"
                owner.input_snapshot = saved
                owner.save(update_fields=["input_snapshot"])
                claimed = True
        if claimed:
            try:
                result = _validated_reference(engine_input, required, provider(deepcopy(engine_input)))
            except Exception:
                result = unavailable
            with transaction.atomic():
                owner = Budget.objects.select_for_update().filter(pk=initial.pk, user_id=chosen.user_id).first()
                if owner is None:
                    result = unavailable
                else:
                    saved = deepcopy(owner.input_snapshot or {})
                    saved["ml_reference_status"] = result["ml_reference_status"]
                    if result["ml_reference_status"] == "ready":
                        saved.update({k: deepcopy(result[k]) for k in ("ml_reference", "ml_reference_meta")})
                    owner.input_snapshot = saved
                    owner.save(update_fields=["input_snapshot"])
    if cache is not None:
        cache[key] = deepcopy(result)
    return result


def get_adjustment_context(user, *, today=None, base_budget_id=None, mode=None,
                           reference_provider=None, reference_cache=None, allow_reference_backfill=True):
    """Read authoritative inputs; only legacy reference backfill may update the initial snapshot."""
    today = adjustment_today(today)
    profile, cycle, chosen, approved, mode = _select_basis(user, today, base_budget_id, mode)
    allocations, totals, spare, items = _budget_amounts(user, chosen)
    protection = budget_input_snapshot(user, cycle, profile=profile, today=today, card=False)
    protection_hash = hash_snapshot(protection)
    if mode == "INITIAL_ADJUSTMENT":
        from .design_service import approval_ready
        if (chosen.input_hash and chosen.input_hash != protection_hash) or not approval_ready(user, chosen, today):
            raise AdjustmentContextError("expired")
    span = Period(cycle.starts_on, min(today + timedelta(days=1), cycle.ends_before))
    collected = coverage(user, span)
    rows = listed_transactions(user).filter(occurred_on__gte=span.start, occurred_on__lt=span.end_before)
    summary = spending_summary((transaction_item(tx) for tx in rows), living_only=True)
    spent = {code: summary["by_category"].get(code, 0) for code in allocations}
    spent["UNCLASSIFIED"] = summary["by_category"].get("UNCLASSIFIED", 0)
    complete = collected["state"] == "complete"
    inside_free, outside_free, free_revision = _free_used(user, cycle, today)
    spare_used = outside_free + max(inside_free, max(living_spent(summary) - sum(allocations.values()), 0))
    savings_floor, savings_available, savings_reason, saving_revision = _savings(user, cycle, today, totals["savings"], complete)
    balance = balance_json(user)
    required = cushion_required(balance["total"], profile.monthly_take_home_income)
    accrued = CushionEntry.objects.filter(user=user, cycle=cycle, entry_type="allocation").aggregate(total=Sum("amount"))["total"] or 0
    cushion_floor = max(1 if required else 0, accrued)
    cushion_known = complete and balance["total"] is not None and not balance["unknown_count"]
    available = {}
    status = {code: "unknown" for code in ("SPARE", "SAVINGS", "CUSHION_MONTHLY")}
    reasons = {}
    if complete:
        available["SPARE"], status["SPARE"] = max(spare - spare_used, 0), "ready"
    else:
        reasons["SPARE"] = "spending_unverified"
    if savings_available is not None:
        available["SAVINGS"], status["SAVINGS"] = savings_available, "ready"
    else:
        reasons["SAVINGS"] = savings_reason
    if cushion_known:
        available["CUSHION_MONTHLY"], status["CUSHION_MONTHLY"] = max(totals["cushion"] - cushion_floor, 0), "ready"
    else:
        reasons["CUSHION_MONTHLY"] = "cushion_execution_unverified"
    settings = list(BudgetCategorySetting.objects.filter(user=user, cycle=cycle, category_id__in=LIVING_CODES))
    locks = _money_map({row.category_id: row.locked_amount for row in settings if row.locked}, allocations)
    system_floors = ({"TRANSPORT_CAR": 100000 if profile.has_car else 60000}
                     if type(profile.has_car) is bool else {})
    transport = _transport_reference(chosen)
    if transport['status'] == 'ready' and system_floors:
        amount = Fraction(transport['baseline_numerator'], transport['baseline_denominator'])
        system_floors['TRANSPORT_CAR'] = min(system_floors['TRANSPORT_CAR'], int(amount/1000+Fraction(1, 2))*1000)
    context = {
        "base_budget_id": str(chosen.pk), "cycle_id": str(cycle.pk), "mode": mode, "source_status": chosen.status,
        "policy_version": POLICY_VERSION, "automatic_unit": 10000, "fallback_unit": 5000,
        "allocations": allocations, "spent": spent, "spending_status": collected["state"],
        "spending_coverage": _json(collected), "minimums": dict(CategoryMinimum.objects.filter(user=user,
            category_id__in=LIVING_CODES).values_list("category_id", "min_amount")),
        "system_floors": system_floors,
        "transport_history_status": transport['status'], 'transport_history_reference': transport,
        "system_floor_ignored_locked": [{"category": code, "reference_amount": amount,
                                          "locked_amount": locks[code]}
                                         for code, amount in system_floors.items()
                                         if code in locks and locks[code] < amount],
        "locks": locks,
        "lock_revisions": {row.category_id: row.revision_no for row in settings},
        "priorities": dict(CategoryPriority.objects.filter(user=user, category_id__in=LIVING_CODES).values_list("category_id", "rank")),
        "category_names": dict(Category.objects.filter(pk__in=LIVING_CODES).values_list("pk", "display_name")),
        "category_targets": _money_map(getattr(profile, "budget_category_targets", {}) or {}, allocations),
        "funding_amount": chosen.funding_amount, "fixed_amount": totals["fixed"],
        "buckets": {"SAVINGS": totals["savings"], "CUSHION_MONTHLY": totals["cushion"], "SPARE": spare},
        "bucket_floors": {"SAVINGS": savings_floor, "CUSHION_MONTHLY": cushion_floor},
        "bucket_available": available, "bucket_status": status, "bucket_reasons": reasons,
        "bucket_revisions": {"SPARE": free_revision, "SAVINGS": saving_revision,
            "CUSHION_MONTHLY": _hash({"accrued": accrued, "required": required, "balance": balance})},
        "bucket_targets": {key: value for key, value in {"LIVING": profile.living_budget,
            "SAVINGS": profile.savings_floor, "CUSHION_MONTHLY": profile.cushion_monthly}.items() if _money(value)},
        "protection_hash": protection_hash, "basis_items": items,
        "basis": {"version_no": chosen.version_no, "input_hash": chosen.input_hash, "policy_version": chosen.policy_version,
            "cycle_revision": cycle.revision_no, "profile_revision": profile.updated_at,
            "current_approved": {"budget_id": str(approved.pk), "version_no": approved.version_no,
                "approved_at": approved.approved_at} if approved else None},
        "today": today,
    }
    context.update(_references(chosen, allocations, reference_provider, reference_cache,
                               allow_backfill=allow_reference_backfill))
    from .adjustment_diagnostics import read_diagnostics, reserved_spare
    reserved, reservation_revision = reserved_spare(user, cycle, today)
    context["bucket_revisions"]["SPARE"] = _hash({"usage": free_revision, "reservations": reservation_revision})
    if reserved is None:
        context["bucket_status"]["SPARE"] = "unknown"
        context["bucket_available"].pop("SPARE", None)
        context["bucket_reasons"]["SPARE"] = "reservation_unverified"
    elif context["bucket_status"]["SPARE"] == "ready":
        context["bucket_available"]["SPARE"] = max(context["bucket_available"]["SPARE"] - reserved, 0)
    context["diagnostic_inputs"] = read_diagnostics(user, context, cycle, today)
    context = _json(context)
    context["context_hash"] = context_fingerprint(context)
    return context
