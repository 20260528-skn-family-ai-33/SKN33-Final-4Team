"""예산 조정 DB 어댑터. 대화 중 쓰기는 보류하고 대화 rev 검수 후 함께 저장한다."""
from copy import deepcopy
import uuid

from django.conf import settings
from django.db import connection
from django.db.models import F, Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException, ValidationError

from chat.models import AsyncTask, Conversation
from users.models import FinancialProfile

from .adjustment_context import AdjustmentContextError, context_fingerprint, get_adjustment_context
from .adjustment_core.funding import validate_funding
from .adjustment_core.simulation import simulate as simulate_adjustment
from .models import Budget, BudgetCategorySetting, BudgetItem
from .adjustment_schedule import planning_context, schedule_settings


SCHEMA = "budget-adjust/1"
PERSISTENT_BUCKETS = {"LIVING": "living_budget", "SAVINGS": "savings_floor", "CUSHION_MONTHLY": "cushion_monthly"}


class AdjustmentExpired(APIException):
    status_code = 409
    default_code = "expired"
    default_detail = "예산 조건이 바뀌었어요. 최신 조건으로 변경안을 다시 만들어 주세요."


class AdjustmentNotReady(APIException):
    status_code = 409
    default_code = "not_ready"
    default_detail = "현재 상태에서는 이 예산 조정안을 적용할 수 없어요."


class AdjustmentConflict(APIException):
    status_code = 409
    default_code = "idempotency_conflict"
    default_detail = "이미 확정한 요청의 선택을 바꿔 다시 보낼 수 없어요."


def _configured(name):
    value = getattr(settings, name, None)
    return import_string(value) if isinstance(value, str) and value else value


def _atomic():
    if not connection.in_atomic_block:
        raise RuntimeError("예산 조정 저장은 회원 잠금·트랜잭션 안에서 실행해야 해요.")


def _unlocked(context, categories):
    if (not isinstance(categories, (list, tuple)) or len(set(categories)) != len(categories)
            or set(categories) - set(context.get("locks", {}))):
        raise ValueError("invalid_unlock")
    calculation = deepcopy(context)
    for code in categories:
        calculation["locks"].pop(code)
    return calculation


def _verify(context, result, unlock_categories, allocator):
    if result.get("status") not in {"proposed", "partial", "unchanged"}:
        raise ValueError("not_validated")
    calculation = _unlocked(context, unlock_categories)
    base, stripped = deepcopy(result["requests"]), deepcopy(result)
    base.pop("refinement", None)
    base.pop("analysis", None)
    stripped["requests"] = base
    if result['requests'].get('analysis') is not None:
        from .adjustment_core.financial_planner import recommendation_context
        protected = recommendation_context(calculation, result['requests']['analysis']['request'])
        validate_funding(protected, base, stripped)
    else:
        validate_funding(calculation, base, stripped)
    if simulate_adjustment(calculation, result["requests"], allocator=allocator) != result:
        raise ValueError("calculation_mismatch")


def _targets(context, result, scope, changes):
    candidates = {code: amount for code, amount in result["persistence_candidates"].items()
                  if code in context["allocations"] or code in PERSISTENT_BUCKETS}
    primary = result["requests"].get("refinement", {}).get("primary")
    if primary is None:
        primary = [i["category"] for i in result["requests"]["items"] if not i.get("automatic")]
    candidates = {c: n for c, n in candidates.items() if c in primary}
    if changes is None:
        changes = candidates if scope == "ongoing" else {}
    if not isinstance(changes, dict) or scope not in {"cycle", "ongoing", "next_cycle"}:
        raise ValueError("invalid_targets")
    old = {**context.get("category_targets", {}), **context.get("bucket_targets", {})}
    for code, amount in changes.items():
        if code not in context["allocations"] and code not in PERSISTENT_BUCKETS:
            raise ValueError("invalid_targets")
        if amount is None:
            if code in PERSISTENT_BUCKETS:
                raise ValueError("invalid_targets")
        elif scope != "ongoing" or type(amount) is not int or candidates.get(code) != amount:
            raise ValueError("invalid_targets")
    return ({code: amount for code, amount in changes.items() if old.get(code) != amount or amount is None and code in old},
            {code: amount for code, amount in candidates.items() if changes.get(code, 0) is not None})


def _metadata(budget):
    meta = (budget.validation_summary or {}).get("adjustment")
    context = (budget.input_snapshot or {}).get("adjustment_context")
    if (not isinstance(meta, dict) or meta.get("schema") != SCHEMA or not isinstance(context, dict)
            or not isinstance(meta.get("result"), dict) or str(budget.base_budget_id) != context.get("base_budget_id")
            or not budget.task_id or budget.task.task_type != "budget_adjust" or budget.task.user_id != budget.user_id
            or (budget.task.result or {}).get("proposal_id") != str(budget.pk)):
        raise AdjustmentNotReady()
    return context, meta


def _proposal(user, proposal, *, for_update=False, conversation=None):
    query = Budget.objects.select_related("task", "base_budget").filter(user=user, task__task_type="budget_adjust")
    if for_update:
        query = query.select_for_update(of=("self",))
    if conversation is not None:
        query = query.filter(task__conversation=conversation)
    return get_object_or_404(query, pk=getattr(proposal, "pk", proposal))


def _partial_selected(budget, meta):
    risk = meta["result"]["requests"].get("analysis", {}).get("request", {}).get("allow_reserve_risk")
    if meta["result"].get("status") != "partial" and not risk:
        return True
    talk = budget.task.conversation
    state = ((talk.slots or {}).get("state") or {}).get("agent:budget", {}) if talk else {}
    return (state.get("proposal", {}).get("proposal_id") == str(budget.pk)
            and not state.get("pending") and not state.get("pending_edit")
            and (not risk or state.get("risk_acknowledged") == str(budget.pk)))


def _task_result(budget, state, **extra):
    return {**(budget.task.result or {}), "state": state, "proposal_id": str(budget.pk), **extra}


def _mark_expired(budget):
    if budget.approved_at is None and budget.status == "validated":
        budget.status = "expired"
        budget.save(update_fields=["status"])
        budget.task.result = _task_result(budget, "expired")
        budget.task.save(update_fields=["result"])


def _lines(base, result, reserve_id=None):
    kept = base.items.exclude(bucket__in=["living", "savings", "cushion"])
    if 'TRAVEL_LODGING' in result['allocations']:
        kept = kept.exclude(bucket='reserve', category_id='TRAVEL_LODGING')
    lines = list(kept.values(
        "bucket", "category_id", "category_detail", "reserve_id", "allocated_amount"))
    lines.extend({"bucket": "reserve" if code == 'TRAVEL_LODGING' else "living", "category_id": code, "category_detail": None,
                  "reserve_id": reserve_id if code == 'TRAVEL_LODGING' else None, "allocated_amount": amount}
                 for code, amount in result["allocations"].items() if code != 'TRAVEL_LODGING' or amount)
    lines.extend({"bucket": bucket, "category_id": None, "category_detail": None, "reserve_id": None,
                  "allocated_amount": result["buckets"][code]} for bucket, code in (("savings", "SAVINGS"), ("cushion", "CUSHION_MONTHLY")))
    return lines


def _same_lines(budget, result):
    def ordered(lines):
        return sorted((line["bucket"], str(line["category_id"] or ""), str(line["category_detail"] or ""),
                       str(line["reserve_id"] or ""), line["allocated_amount"]) for line in lines)
    actual = budget.items.values("bucket", "category_id", "category_detail", "reserve_id", "allocated_amount")
    return ordered(actual) == ordered(_lines(budget.base_budget, result, (budget.validation_summary or {})['adjustment'].get('reserve_id')))


class AdjustmentAdapter:
    def __init__(self, user, conversation=None, today=None, base_budget_id=None, mode=None):
        self.user, self.conversation, self.today = user, conversation, today
        self.base_budget_id, self.mode = base_budget_id, mode
        self.allocator = _configured("BUDGET_ADJUST_ALLOCATOR")
        self.reference_provider = _configured("BUDGET_ADJUST_REFERENCE_PROVIDER")
        self.reference_context = None
        self.llm_call_count = 0
        self._context, self._reference_cache = None, {}
        self._staged, self._canceled = {}, set()
        self._replaced = set()

    def _read_context(self, base_budget_id=None, mode=None):
        return get_adjustment_context(self.user, today=self.today,
            base_budget_id=base_budget_id or self.base_budget_id, mode=mode or self.mode,
            reference_provider=self.reference_provider, reference_cache=self._reference_cache)

    def get_context(self):
        if self._context is None:
            saved = self.reference_context
            reusable = (isinstance(saved, dict) and saved.get("base_budget_id")
                        and (self.base_budget_id is None or str(self.base_budget_id) == saved["base_budget_id"])
                        and (self.mode is None or self.mode == saved.get("mode")))
            if reusable:
                frozen = {key: deepcopy(saved[key]) for key in ("ml_reference", "ml_reference_meta") if key in saved}
                self._context = get_adjustment_context(self.user, today=self.today,
                    base_budget_id=self.base_budget_id or saved["base_budget_id"], mode=self.mode or saved.get("mode"),
                    reference_provider=(lambda inputs: deepcopy(frozen)) if frozen else None,
                    reference_cache=self._reference_cache)
            else:
                self._context = self._read_context()
            self.reference_context = {key: deepcopy(self._context[key]) for key in (
                "base_budget_id", "mode", "engine_input", "ml_reference", "ml_reference_meta", "ml_reference_status", "ml_not_applicable")
                if key in self._context}
        return deepcopy(self._context)

    def simulate(self, context, request):
        return simulate_adjustment(context, request, allocator=self.allocator)

    def propose(self, context, result, *, scope, unlock_categories=(), target_changes=None):
        expected = planning_context(self.get_context(), context.get('planning'))
        if context_fingerprint(expected) != context_fingerprint(context):
            raise ValueError("expired")
        _verify(context, result, unlock_categories, self.allocator)
        changes, candidates = _targets(context, result, scope, target_changes)
        if not result.get("changes") and not changes and not unlock_categories:
            raise ValueError("unchanged")
        task_id, proposal_id = str(uuid.uuid4()), str(uuid.uuid4())
        meta = {"schema": SCHEMA, "result": deepcopy(result), "scope": scope,
                "unlock_categories": list(unlock_categories), "target_changes": changes,
                "persistence_candidates": candidates, "keep_for_next_cycle": scope == "ongoing"}
        if context.get('planning'):
            meta['effective_on'] = context['effective_on']
            meta['income_change'] = {'before': context['income_before'], 'after': context['monthly_income']}
        if self.conversation is not None:
            for old in Budget.objects.filter(user=self.user, task__conversation=self.conversation,
                                             task__task_type="budget_adjust", status="validated"):
                self.cancel(old.task_id)
                self._replaced.add(str(old.task_id))
        self._staged[task_id] = {"task_id": task_id, "proposal_id": proposal_id,
                                 "context": deepcopy(context), "meta": meta}
        return _card(proposal_id, task_id, context, meta, "awaiting_approval", can_approve=result["status"] != "partial")

    def _fresh(self, context):
        frozen_reference = {key: deepcopy(context[key]) for key in ("ml_reference", "ml_reference_meta") if key in context}
        try:
            # ML 참조는 제안 당시 값을 유지한다. 잠금 안에서는 금융 상태만 다시 읽는다.
            fresh = get_adjustment_context(self.user, today=self.today, base_budget_id=context["base_budget_id"],
                mode=context["mode"], reference_provider=(lambda inputs: deepcopy(frozen_reference)) if frozen_reference else None,
                reference_cache=self._reference_cache, allow_reference_backfill=False)
        except AdjustmentContextError:
            raise AdjustmentExpired() from None
        try:
            fresh = planning_context(fresh, context.get('planning'))
        except ValueError:
            raise AdjustmentExpired() from None
        if context_fingerprint(fresh) != context_fingerprint(context):
            raise AdjustmentExpired()
        return fresh

    def task_status(self, task_ref):
        if str(task_ref) in self._staged:
            return {"task_ref": str(task_ref), "state": "canceled" if str(task_ref) in self._canceled else "awaiting_approval"}
        tasks = AsyncTask.objects.filter(pk=task_ref, user=self.user, task_type="budget_adjust")
        tasks = tasks.filter(conversation=self.conversation) if self.conversation is not None else tasks.filter(conversation__isnull=True)
        task = get_object_or_404(tasks)
        budget = get_object_or_404(Budget.objects.select_related("task", "base_budget"), user=self.user, task=task)
        context, meta = _metadata(budget)
        if budget.approved_at is not None and budget.status in {"approved", "superseded"}:
            return {"task_ref": str(task.pk), "state": "saved", "saved_ref": str(budget.pk)}
        if str(task_ref) in self._canceled or task.status == "canceled":
            state = "canceled"
        elif task.status == "failed":
            state = "failed"
        elif budget.status in {"expired", "rejected"}:
            state = budget.status
        else:
            try:
                self._fresh(context)
                state = "awaiting_approval"
            except (AdjustmentContextError, AdjustmentExpired):
                state = "expired"
        return {"task_ref": str(task.pk), "state": state}

    def cancel(self, task_ref):
        checked = self.task_status(task_ref)
        if checked["state"] in {"awaiting_approval", "proposing"}:
            self._canceled.add(str(task_ref))
            return {"task_ref": str(task_ref), "state": "canceled"}
        return checked

    def flush(self, conversation):
        _atomic()
        if conversation.user_id != self.user.pk:
            raise AdjustmentNotReady()
        if self._context is not None or self._staged:
            from users.privacy import require_bank_not_revoked
            require_bank_not_revoked(self.user)
        # 먼저 모두 재검수해 취소와 새 제안 중 일부만 저장되는 것을 막는다.
        for stage in self._staged.values():
            self._fresh(stage["context"])
            _verify(stage["context"], stage["meta"]["result"], stage["meta"]["unlock_categories"], self.allocator)
        cancellations = []
        for ref in self._canceled - self._staged.keys():
            budget = _proposal(self.user, get_object_or_404(Budget.objects.filter(user=self.user), task_id=ref),
                               for_update=True, conversation=conversation)
            if budget.approved_at is not None:
                raise AdjustmentExpired()
            if budget.status not in {"validated", "rejected"}:
                raise AdjustmentExpired()
            cancellations.append(budget)
        for budget in cancellations:
            replaced = str(budget.task_id) in self._replaced
            budget.status = "expired" if replaced else "rejected"
            budget.save(update_fields=["status"])
            budget.task.status = "succeeded" if replaced else "canceled"
            budget.task.result = _task_result(budget, "expired" if replaced else "canceled")
            budget.task.completed_at = timezone.now()
            budget.task.save(update_fields=["status", "result", "completed_at"])
        for ref, stage in self._staged.items():
            context, meta = stage["context"], stage["meta"]
            base = get_object_or_404(Budget.objects.select_for_update(), user=self.user, pk=context["base_budget_id"], cycle_id=context["cycle_id"])
            canceled = ref in self._canceled
            now = timezone.now()
            task = AsyncTask.objects.create(task_id=ref, user=self.user, conversation=conversation,
                request_key="budget-adjust-" + ref, task_type="budget_adjust",
                status="canceled" if canceled else "succeeded", input_hash=context_fingerprint(context),
                llm_call_count=self.llm_call_count, started_at=now, completed_at=now,
                result={"state": "canceled" if canceled else "awaiting_approval", "proposal_id": stage["proposal_id"], "saved_ref": None})
            version = (Budget.objects.filter(user=self.user, cycle=base.cycle).aggregate(last=Max("version_no"))["last"] or 0) + 1
            snapshot = {**deepcopy(base.input_snapshot or {}), "adjustment_context": deepcopy(context)}
            if meta['result']['allocations'].get('TRAVEL_LODGING'):
                from .models import ReservePlan
                reserve = ReservePlan.objects.create(user=self.user, name='여행·숙박 일회성 예산',
                    target_amount=meta['result']['allocations']['TRAVEL_LODGING'], confirmed_reserved_amount=0,
                    due_date=context.get('effective_on'), confirmation_state='planned')
                meta['reserve_id'] = str(reserve.pk)
            budget = Budget.objects.create(budget_id=stage["proposal_id"], user=self.user, cycle=base.cycle,
                base_budget=base, task=task, version_no=version, status="rejected" if canceled else "validated",
                funding_amount=context["funding_amount"], input_hash=context_fingerprint(context), input_snapshot=snapshot,
                engine_version=SCHEMA, policy_version=context["policy_version"], validation_summary={"adjustment": meta}, validated_at=now)
            BudgetItem.objects.bulk_create([BudgetItem(user=self.user, budget=budget, **line) for line in _lines(base, meta["result"], meta.get('reserve_id'))])
        self.conversation = conversation
        self._staged.clear()
        self._canceled.clear()
        self._replaced.clear()


def _card(proposal_id, task_ref, context, meta, state, *, can_approve=True):
    result = meta["result"]
    applied = meta.get("approval", {}) if state == "saved" else {}
    keep = applied.get("keep_for_next_cycle", meta["keep_for_next_cycle"])
    changes = applied.get("target_changes", meta["target_changes"])
    displayed_changes = deepcopy(result['changes'])
    if meta.get('effective_on') and context.get('planning_base'):
        before = context['planning_base']['buckets']['SPARE']
        after = result['buckets']['SPARE']
        displayed_changes = [row for row in displayed_changes if row['category'] != 'SPARE']
        if before != after:
            displayed_changes.append({'category': 'SPARE', 'before': before, 'after': after, 'diff': after - before})
    return {"kind": "funding", "proposal_id": str(proposal_id), "task_ref": str(task_ref),
            "mode": context["mode"], "base_budget_id": context["base_budget_id"],
            "scope": 'next_cycle' if meta.get('effective_on') else (("ongoing" if keep else "cycle") if state == "saved" else meta["scope"]),
            **({'effective_on': meta['effective_on'], 'income_change': meta['income_change'],
                'one_off': context.get('planning', {}).get('one_off', False), 'expense': context.get('planning', {}).get('expense')} if meta.get('effective_on') else {}),
            "state": state, "can_approve": state == "awaiting_approval" and can_approve and result.get('spending_verified', True),
            **({'verification_notice': '이번 주기 지출을 아직 모두 확인하지 못했어요. 예산 기준 미리보기이며, 내역 확인 후 다시 계산해야 확정할 수 있어요.'} if not result.get('spending_verified', True) else {}),
            "allocations": deepcopy(result["allocations"]), "buckets": deepcopy(result["buckets"]),
            "changes": displayed_changes, "shortfalls": deepcopy(result["shortfalls"]),
            "transfers": deepcopy(result["transfers"]), "target_changes": deepcopy(changes),
            "unlock_categories": list(meta["unlock_categories"]), "persistence_candidates": deepcopy(meta["persistence_candidates"]),
            "persistence_items": [{"category": code, "amount": amount} for code, amount in meta["persistence_candidates"].items()],
            "target_items": [{"category": code, "amount": amount} for code, amount in changes.items()],
            "keep_for_next_cycle": keep,
            **({"saved_ref": str(proposal_id)} if state == "saved" else {})}


def proposal_card(user, proposal, *, conversation=None, today=None):
    budget = _proposal(user, proposal, conversation=conversation)
    if (budget.validation_summary or {}).get('design_recovery'):
        from .design_recovery_plans import proposal_card as recovery_card
        return recovery_card(user, budget, conversation=conversation, today=today)
    context, meta = _metadata(budget)
    adapter = AdjustmentAdapter(user, conversation=budget.task.conversation, today=today)
    checked = adapter.task_status(budget.task_id)
    card = _card(budget.pk, budget.task_id, context, meta, checked["state"], can_approve=_partial_selected(budget, meta))
    if meta.get('effective_on') and budget.approved_at:
        card['schedule_status'] = budget.schedule.status
    return card


def _persistence(context, changes):
    if not changes:
        return
    categories = {**context.get("category_targets", {})}
    buckets = {"LIVING": sum(context["allocations"].values()), **context["buckets"], **context.get("bucket_targets", {})}
    for code, amount in changes.items():
        if code in PERSISTENT_BUCKETS:
            buckets[code] = amount
        elif amount is None:
            categories.pop(code, None)
        else:
            categories[code] = amount
    if (any(type(buckets.get(code)) is not int or buckets[code] < 0 for code in PERSISTENT_BUCKETS)
            or sum(buckets[code] for code in PERSISTENT_BUCKETS) > context["funding_amount"] - context["fixed_amount"]
            or any(buckets[code] < context["bucket_floors"].get(code, 0) for code in ("SAVINGS", "CUSHION_MONTHLY"))):
        raise AdjustmentNotReady("다음 달 유지 금액이 기본 배분·최소 보장액과 맞지 않아요. 이번 주기만 적용하거나 목표를 수정해 주세요.")
    required = 0
    for code in context["allocations"]:
        floor = context.get("minimums", {}).get(code, 0)
        if code not in context.get("locks", {}):
            floor = max(floor, context.get("system_floors", {}).get(code, 0))
        target = categories.get(code, floor)
        if type(target) is not int or target < floor:
            raise AdjustmentNotReady("다음 달 유지 목표가 최소 보장액과 맞지 않아요.")
        required += max(floor, target, context.get("locks", {}).get(code, 0))
    if required > buckets["LIVING"]:
        raise AdjustmentNotReady("다음 달 유지 목표 합계가 기본 생활비를 넘어요. 목표를 조정해 주세요.")


def approve_adjustment(user, proposal, *, keep_for_next_cycle, key, expected_mode, today=None):
    """호출자는 회원 잠금을 먼저 잡고, 만료 예외는 atomic 안에서 응답으로 바꿔 만료를 보존한다."""
    _atomic()
    if type(keep_for_next_cycle) is not bool:
        raise ValidationError({"keep_for_next_cycle": ["유지 여부는 참 또는 거짓으로 보내 주세요."]})
    if not isinstance(key, str) or not key or len(key) > 128:
        raise ValidationError({"request_key": ["요청 번호가 필요해요."]})
    budget = _proposal(user, proposal, for_update=True)
    context, meta = _metadata(budget)
    if context["mode"] != expected_mode:
        raise AdjustmentNotReady("이 조정안에 맞는 승인 경로를 사용해 주세요.")
    if budget.approved_at is not None:
        if meta.get("approval", {}).get("keep_for_next_cycle") != keep_for_next_cycle:
            raise AdjustmentConflict()
        return proposal_card(user, budget, today=today)
    if budget.status == "expired":
        raise AdjustmentExpired()
    if budget.status != "validated" or budget.task.status != "succeeded":
        raise AdjustmentNotReady()
    if not meta['result'].get('spending_verified', context.get('spending_status', 'complete') == 'complete'):
        raise AdjustmentNotReady('이번 주기 지출 조회가 누락되어 실제로 옮길 수 있는 금액은 미확인이에요. 미리보기와 입력은 유지했어요. 계좌·카드 내역 확인 후 다시 계산해 주세요.')
    if not _partial_selected(budget, meta):
        raise AdjustmentNotReady("가능한 금액만 반영할지 먼저 선택해 주세요.")
    adapter = AdjustmentAdapter(user, conversation=budget.task.conversation, today=today,
                                base_budget_id=context["base_budget_id"], mode=context["mode"])
    try:
        fresh = adapter._fresh(context)
        _verify(fresh, meta["result"], meta["unlock_categories"], adapter.allocator)
        _targets(context, meta["result"], meta["scope"], meta["target_changes"])
        if not _same_lines(budget, meta["result"]):
            raise ValueError("stored_proposal_changed")
    except (AdjustmentContextError, AdjustmentExpired, ValueError):
        _mark_expired(budget)
        raise AdjustmentExpired() from None
    changes = {code: None for code, amount in meta["target_changes"].items() if amount is None}
    if meta.get('effective_on'):
        if keep_for_next_cycle:
            raise AdjustmentNotReady('다음 주기 예약은 미리보기에 표시된 시작일부터 적용해요.')
        if Budget.objects.filter(user=user, approval_key=key).exclude(pk=budget.pk).exists():
            raise AdjustmentConflict()
        profile = get_object_or_404(FinancialProfile.objects.select_for_update(), user=user)
        schedule_settings(user, profile, budget, fresh, meta['result'], meta['unlock_categories'])
        meta['approval'] = {'keep_for_next_cycle': False, 'target_changes': {}}
        budget.validation_summary = {**(budget.validation_summary or {}), 'adjustment': meta}
        budget.status, budget.approved_at, budget.approval_key = 'superseded', timezone.now(), key
        budget.save(update_fields=['status', 'approved_at', 'approval_key', 'validation_summary'])
        budget.task.result = _task_result(budget, 'saved', saved_ref=str(budget.pk))
        budget.task.save(update_fields=['result'])
        return proposal_card(user, budget, today=today)
    if keep_for_next_cycle:
        changes.update(meta["persistence_candidates"])
    _persistence(_unlocked(fresh, meta["unlock_categories"]), changes)
    if Budget.objects.filter(user=user, approval_key=key).exclude(pk=budget.pk).exists():
        raise AdjustmentConflict()
    profile = get_object_or_404(FinancialProfile.objects.select_for_update(), user=user)
    fields, targets = set(), deepcopy(profile.budget_category_targets or {})
    for code, amount in changes.items():
        if code in PERSISTENT_BUCKETS:
            field = PERSISTENT_BUCKETS[code]
            setattr(profile, field, amount)
            fields.add(field)
        elif amount is None:
            targets.pop(code, None)
            fields.add("budget_category_targets")
        else:
            targets[code] = amount
            fields.add("budget_category_targets")
    if fields:
        profile.budget_category_targets = targets
        profile.save(update_fields=[*sorted(fields), "updated_at"])
    if meta["unlock_categories"]:
        BudgetCategorySetting.objects.filter(user=user, cycle=budget.cycle, category_id__in=meta["unlock_categories"], locked=True).update(
            locked=False, locked_amount=None, revision_no=F("revision_no") + 1, updated_at=timezone.now())
    Budget.objects.filter(user=user, cycle=budget.cycle, status="approved").update(status="superseded")
    Budget.objects.filter(user=user, cycle=budget.cycle, status__in=["draft", "validated"]).exclude(pk=budget.pk).update(status="expired")
    meta["approval"] = {"keep_for_next_cycle": keep_for_next_cycle, "target_changes": changes}
    budget.validation_summary = {**(budget.validation_summary or {}), "adjustment": meta}
    budget.status, budget.approved_at, budget.approval_key = "approved", timezone.now(), key
    budget.save(update_fields=["status", "approved_at", "approval_key", "validation_summary"])
    budget.task.result = _task_result(budget, "saved", saved_ref=str(budget.pk))
    budget.task.save(update_fields=["result"])
    return proposal_card(user, budget, today=today)
