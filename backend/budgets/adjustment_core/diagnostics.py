"""Normalized-history calculations. No ORM, transaction edits or writes."""
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json

POLICY = "adjustment-diagnosis-20261110-30d-v1"
CHECKS = ("duplicates", "cancellations", "refunds")
DAY = timedelta(days=1)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def day(value):
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError("invalid_date")
    return date.fromisoformat(value)


def build_history(as_of, categories, records, complete_days, normalization_checks):
    """Upstream supplies normalized purchases and verified coverage, never allocation amounts.

    Read at most three completed months plus this month for recurrence; forecast
    only the contiguous verified suffix of the last 30 completed calendar days.
    """
    from scripts.budget_design.plan_b_history import detect_one_offs, completed_months
    today = day(as_of)
    if (not isinstance(records, list) or not isinstance(complete_days, (list, tuple))
            or any(normalization_checks.get(k) != "verified" for k in CHECKS)):
        return {"status": "unknown", "scope": "variable_living", "reason": "normalization_unverified"}
    covered = {day(d) for d in complete_days}
    dates = []
    for offset in range(1, 31):
        d = today - offset * DAY
        if d not in covered:
            break
        dates.append(d.isoformat())
    dates.reverse()
    by_day = {d: dict.fromkeys(categories, 0) for d in dates}
    detected, seen, transactions = [], set(), []
    history_start = day(completed_months(today)[0] + "-01")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid_transaction")
        tx_id = record.get("transaction_id")
        if not isinstance(tx_id, str) or not tx_id or tx_id in seen:
            raise ValueError("duplicate_or_missing_transaction_id")
        seen.add(tx_id)
        occurred = day(record.get("occurred_on"))
        amount = record.get("net_amount")
        if type(amount) is not int or amount < 0:
            raise ValueError("invalid_net_amount")
        if record.get("transaction_type") not in {"spending", "income", "transfer"}:
            raise ValueError("normalization_required")
        if record["transaction_type"] != "spending" or amount == 0:
            continue
        if record.get("funding") not in {"fixed", "living"}:
            raise ValueError("funding_required")
        if record["funding"] == "fixed":
            continue
        code = record.get("category")
        if code not in categories:
            # Unknown living purchases must be classified before a complete forecast.
            if occurred.isoformat() in by_day:
                return {"status": "unknown", "scope": "variable_living", "reason": "unclassified_spending"}
            continue
        if occurred.isoformat() in by_day:
            by_day[occurred.isoformat()][code] += amount
        if history_start <= occurred < today and occurred in covered:
            safe_id = digest({"transaction": tx_id})
            transactions.append({"id": safe_id, "month": occurred.strftime("%Y-%m"),
                                 "category": code, "amount": amount,
                                 "merchant_key": record.get("merchant_key")})
    found = {c["id"]: c for c in detect_one_offs(transactions)}
    for record in records:
        candidate = found.get(digest({"transaction": record["transaction_id"]}))
        if candidate and record["occurred_on"] in by_day and not candidate["excluded_by_recurrence"]:
            detected.append({"id": candidate["id"], "date": record["occurred_on"],
                             "category": candidate["category"], "amount": candidate["amount"]})
    return {"status": "ready" if len(dates) >= 14 else "unknown", "scope": "variable_living",
            "reason": None if len(dates) >= 14 else "insufficient_complete_days",
            "policy_version": POLICY, "dates": dates, "samples": list(by_day.values()),
            "normalization_checks": deepcopy(normalization_checks), "candidates": detected,
            "record_fingerprint": digest(records), "review_decisions": {}}


def resolve_history(history, as_of, categories):
    """Keep unknown candidates; remove only confirmed amounts, never their calendar days."""
    history = deepcopy(history)
    dates, samples = history.get("dates", []), history.get("samples", [])
    if (history.get("status") != "ready" or history.get("scope") != "variable_living"
            or history.get("policy_version") != POLICY or not 14 <= len(samples) <= 30
            or len(dates) != len(samples)
            or any(history.get("normalization_checks", {}).get(k) != "verified" for k in CHECKS)):
        raise ValueError("history_unverified")
    expected = [(day(as_of) - n * DAY).isoformat() for n in range(len(samples), 0, -1)]
    if dates != expected:
        raise ValueError("history_not_recent_contiguous")
    if any(not isinstance(s, dict) or set(s) != set(categories)
           or any(type(v) is not int or v < 0 for v in s.values()) for s in samples):
        raise ValueError("invalid_history_samples")
    decisions = history.get("review_decisions", {})
    if not isinstance(decisions, dict) or any(v not in {"one_off", "recurring", "unknown"} for v in decisions.values()):
        raise ValueError("invalid_one_off_decisions")
    candidates = history.get("candidates", [])
    ids = set()
    pending = []
    for candidate in candidates:
        key, d, code, amount = (candidate.get(k) for k in ("id", "date", "category", "amount"))
        if (not isinstance(key, str) or not key or key in ids or d not in dates
                or code not in categories or type(amount) is not int or amount <= 0):
            raise ValueError("invalid_candidate")
        ids.add(key)
        if decisions.get(key) == "one_off":
            sample = samples[dates.index(d)]
            if sample[code] < amount:
                raise ValueError("candidate_exceeds_spending")
            sample[code] -= amount
        elif key not in decisions:
            pending.append(deepcopy(candidate))
    return samples, pending


def with_overrides(context, overrides):
    """User answers may change plans/decisions, never replace observed spending samples."""
    out = deepcopy(context)
    info = out.setdefault("diagnostic_inputs", {})
    for key in ("remaining_days", "planned_spare"):
        if key in overrides:
            info[key] = deepcopy(overrides[key])
    if "history" in overrides:
        info.setdefault("history", {}).setdefault("review_decisions", {}).update(
            deepcopy(overrides["history"].get("review_decisions", {})))
    return out


def inputs_from_sources(as_of, history, *, cycle_ends_before=None, next_payday=None, planned=None):
    """Candidate lookup contract, not new DB columns. Unknown plans never mean none.

    `planned` is an owner-scoped, verified complete list; each item explicitly
    says its funding bucket and whether the forecast already includes it.
    """
    today = day(as_of)
    out = {"as_of": today.isoformat(), "history": deepcopy(history),
           "planned_spare": {"status": "unknown", "reason": "planned_expenses_unverified"}}
    for key, value in (("remaining_days", next_payday), ("cycle_remaining_days", cycle_ends_before)):
        if value is not None:
            days = (day(value) - today).days - 1
            if not 0 <= days <= 365:
                raise ValueError("invalid_forecast_period")
            out[key] = days
    if (not isinstance(planned, dict) or planned.get("status") != "complete"
            or planned.get("as_of") != today.isoformat() or not isinstance(planned.get("items"), list)
            or next_payday is None):
        return out
    total, seen = 0, set()
    for row in planned["items"]:
        key, amount, bucket = row.get("id"), row.get("amount"), row.get("funding_bucket")
        if (not isinstance(key, str) or not key or key in seen or type(amount) is not int or amount < 0
                or bucket not in {"fixed", "living", "savings", "cushion", "free"}):
            raise ValueError("invalid_or_duplicate_planned_expense")
        seen.add(key)
        due = day(row.get("due_on"))
        if not today < due < day(next_payday) or bucket != "free":
            continue
        included = row.get("included_in_forecast")
        if type(included) is not bool:
            return out
        if not included:
            total += amount
    out["planned_spare"] = {"status": "confirmed", "scope": "unbudgeted_only", "amount": total}
    return out
