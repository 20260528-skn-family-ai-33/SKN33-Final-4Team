from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from config.idempotency import idempotent
from finance.inputs import body_dict, reject_unknown_fields
from finance.models import FinancialTransaction
from users.models import FinancialProfile
from .api import account_json, lock_user
from .models import Budget, BudgetCycle, SalaryDecision
from .salary import (confirmation_snapshot, current_salary_profile, expire_salary_drafts, known_deposits, memo_key, next_payday_on,
                     recognized, refresh_auto, refresh_confirmed, salary_cycle_dates, salary_windows, store_income)
from .salary_serializers import SalaryGapSerializer, SalarySerializer


def salary_data(profile, today):
    pending = income_pending = None
    if profile and profile.payday_changes_on and today < profile.payday_changes_on:
        pending = {"payday": profile.scheduled_payday, "effective_on": profile.payday_changes_on}
    if profile and profile.income_changes_on and today < profile.income_changes_on:
        income_pending = {"take_home_income": profile.scheduled_take_home_income, "effective_on": profile.income_changes_on}
    return {
        "take_home_income": profile.monthly_take_home_income if profile else None,
        "payday": profile.payday if profile else None,
        "pending_payday_change": pending,
        "pending_income_change": income_pending,  # 11/12: 다음 주기부터 바뀔 실수령액
    }


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def salary(request):
    data = None
    if request.method == "PUT":
        serializer = SalarySerializer(data=body_dict(request))
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
    with transaction.atomic():
        lock_user(request.user)
        today = timezone.localdate()
        profile = current_salary_profile(request.user, today)
        if data is not None:
            profile = save_salary(request.user, profile, data, today)
        return Response(salary_data(profile, today))


def save_salary(user, profile, data, today):
    profile = profile or FinancialProfile(user=user)
    original = {field.name: getattr(profile, field.name) for field in profile._meta.fields}
    payday, income = data["payday"], data["take_home_income"]
    apply_from, key = data.get("apply_from"), data.get("apply_key", "apply_from")
    pending = profile.payday_changes_on is not None and today < profile.payday_changes_on
    income_pending = profile.income_changes_on is not None  # 전환일이 지난 예약은 current_salary_profile 이 이미 옮겼다
    started = None  # 첫 예산을 승인했는지 — 필요할 때만 한 번 센다

    def choice():
        """바뀐 값의 적용 시점. 첫 예산 승인 전(온보딩)에는 지키는 예산 주기가 없으니 묻지 않고 바로 바꾼다 (11/12 피드백)"""
        nonlocal started
        if apply_from is not None:
            return apply_from
        if started is None:
            started = Budget.objects.filter(user=user, approved_at__isnull=False).exists()
        if started:
            raise ValidationError({key: ["이번 주기부터인지 다음 주기부터인지 선택해 주세요."]})
        return "current_cycle"

    if profile.payday is None:
        if apply_from == "next_cycle":
            raise ValidationError({key: ["첫 급여일은 바로 적용해 주세요."]})
        profile.payday = payday
    elif payday != profile.payday:
        if choice() == "next_cycle":
            if not (pending and profile.scheduled_payday == payday):
                start = salary_cycle_dates(profile, today)[0]
                profile.scheduled_payday = payday
                profile.payday_changes_on = next_payday_on(payday, today)
                profile.payday_previous_start = start
        else:
            profile.payday = payday
            profile.scheduled_payday = profile.payday_changes_on = profile.payday_previous_start = None
    elif pending and apply_from is not None:
        # 지금 급여일로 되돌리고 시점을 골랐다 → 예약 취소 (시점 없이 보낸 예전 화면은 예약을 그대로 둔다)
        profile.scheduled_payday = profile.payday_changes_on = profile.payday_previous_start = None

    # 월 실수령액 (11/12): 급여일과 같은 선택으로 이번 주기부터 / 다음 주기부터
    if profile.monthly_take_home_income is None:
        profile.monthly_take_home_income = income  # 처음 등록은 바로
    elif income != profile.monthly_take_home_income:
        if choice() == "next_cycle":
            # 다음 주기 시작일 = 지금 주기 끝(급여일 예약이 있으면 그 전환일). 위에서 급여일을 바꾼 뒤에 센다
            profile.scheduled_take_home_income = income
            profile.income_changes_on = salary_cycle_dates(profile, today)[1]
        else:
            profile.monthly_take_home_income = income
            profile.scheduled_take_home_income = profile.income_changes_on = None
    elif income_pending and apply_from is not None:
        # 지금 금액으로 되돌리고 시점을 골랐다 → 예약 취소
        profile.scheduled_take_home_income = profile.income_changes_on = None
    changed = [name for name, value in original.items() if getattr(profile, name) != value]
    if original["payday"] is not None and any(name in changed for name in (
        "payday", "scheduled_payday", "payday_changes_on", "payday_previous_start",
    )):
        profile.payday_revision += 1
        changed.append("payday_revision")
    if changed:
        if profile._state.adding:
            profile.save()
        else:
            profile.save(update_fields=[*changed, "updated_at"])
        expire_salary_drafts(user)
        # 이미 조회한 주기가 있으면 새 경계도 바로 맞춘다.
        from .salary import sync_salary_cycle
        from .models import BudgetCycle
        if BudgetCycle.objects.filter(user=user, status="active").exists():
            sync_salary_cycle(user, profile, today)
    return profile


# ---------------- 급여 확인 · 정정 (R12) — docs/api/salary.md ----------------

class SalaryDecisionSerializer(serializers.Serializer):
    transaction_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=31)
    decision = serializers.ChoiceField(choices=["confirmed", "rejected", "none"])
    use_pattern = serializers.BooleanField(required=False, default=False)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            reject_unknown_fields(data, self.fields)
        return super().to_internal_value(data)


def deposit_json(tx, cycle_on, income):
    decision = getattr(tx, "salary_decision", None)
    return {
        "transaction_id": str(tx.pk),
        "pay_cycle_on": cycle_on.isoformat(),
        "date": tx.occurred_on.isoformat(),
        "amount": tx.amount,
        "description": tx.source_description,
        "account": account_json(tx.account),
        "decision": decision.state if decision else None,
        "is_pattern": bool(decision and decision.is_pattern),
        "is_candidate": bool(decision and decision.state == "recheck") or (
            decision is None and bool(income) and tx.amount * 10 >= income * 8),
        "pattern_unusable_reason": memo_key(tx.source_description)[1],
    }


def confirmation_cycle(tx, windows, today):
    """재확인은 기존 귀속 회차를 유지한다. 날짜 정정으로 구간 밖이 되어도 다시 확인할 수 있다."""
    if tx.occurred_on > today:
        return None
    decision = getattr(tx, "salary_decision", None)
    if decision and decision.state in ["confirmed", "recheck"]:
        return decision.pay_cycle_on
    return next((c for c, low, high in windows if low <= tx.occurred_on <= high), None)


def deposits_data(user, profile, today):
    """급여 구간(이번 · 다음 조기)마다 고를 수 있는 입금 전부 + 지금 자동 인식 기준. 호출 쪽에서 회원 잠금"""
    windows = salary_windows(profile, today)
    refresh_auto(user, windows)
    store_income(user, windows[0][0])
    income = profile.monthly_take_home_income
    pool = known_deposits(user).select_related("account__connection", "salary_decision")
    base = SalaryDecision.objects.filter(user=user, is_pattern=True).select_related("transaction__account__connection").first()
    groups = {c: {"pay_cycle_on": c.isoformat(), "from": low.isoformat(), "to": high.isoformat(), "items": []}
              for c, low, high in windows if low <= high}
    recheck_cycles = SalaryDecision.objects.filter(user=user, state="recheck").values("pay_cycle_on")
    # 재확인 시 회차 전체를 다시 저장하므로, 같은 과거 회차의 정상 분할 급여도 함께 보여 준다.
    visible = Q(salary_decision__pay_cycle_on__in=recheck_cycles)
    for _, low, high in windows:
        if low <= high:
            visible |= Q(occurred_on__range=(low, high))
    for tx in pool.filter(visible, occurred_on__lte=today):
        cycle_on = confirmation_cycle(tx, windows, today)
        if cycle_on is None:
            continue
        day = tx.occurred_on.isoformat()
        group = groups.setdefault(cycle_on, {"pay_cycle_on": cycle_on.isoformat(), "from": day, "to": day, "items": []})
        group["from"], group["to"] = min(group["from"], day), max(group["to"], day)
        group["items"].append(deposit_json(tx, cycle_on, income))
    return {
        "windows": list(groups.values()),
        "pattern": deposit_json(base.transaction, base.pay_cycle_on, income) if base else None,
    }


def locked_profile(user, today):
    lock_user(user)
    profile = current_salary_profile(user, today)
    if not (profile and profile.payday):
        raise ValidationError({"payday": ["급여일을 먼저 등록해 주세요."]})
    return profile


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def salary_deposits(request):
    today = timezone.localdate()
    with transaction.atomic():
        return Response(deposits_data(request.user, locked_profile(request.user, today), today))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def salary_decide(request):
    """confirmed: 고른 입금들을 그 회차의 급여로 (그 회차 판단을 이 목록으로 바꿈, 분할 급여는 여러 건) ·
    rejected: 급여 아님 · none: 판단 취소. 저장 시점의 거래 상태를 다시 검증한다"""
    serializer = SalaryDecisionSerializer(data=body_dict(request))
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    ids = set(data["transaction_ids"])
    invalid = ValidationError({"transaction_ids": ["고를 수 없는 거래가 있어요."]})
    user, today = request.user, timezone.localdate()
    with transaction.atomic():
        profile = locked_profile(user, today)
        # 11/13: '이번 주기만 받은 금액으로' 기준이 있던 주기 — 이 판단으로 받은 금액이 바뀌어 기준이 풀리면 화면이 다시 짤지 묻는다
        based = set(BudgetCycle.objects.filter(user=user, income_basis__isnull=False).values_list("pk", flat=True))
        refresh_confirmed(user)
        windows = salary_windows(profile, today)
        touched = set(SalaryDecision.objects.filter(user=user, transaction_id__in=ids)
                      .exclude(pay_cycle_on=None).values_list("pay_cycle_on", flat=True))
        if data["decision"] == "confirmed":
            # 행 잠금: R11 자동 연결(transfer_rules.link_own_transfers)과 같은 거래를 두고 겹치면 한쪽만 확정된다.
            # 선택 해제 계좌라도 이미 확인한 입금은 다시 고를 수 있다 (새 입금은 관리 대상 계좌만)
            txs = list(known_deposits(user).filter(pk__in=ids).select_for_update(of=("self",)))
            cycles = {confirmation_cycle(tx, windows, today) for tx in txs}
            if len(txs) != len(ids) or len(cycles) != 1 or None in cycles:
                raise ValidationError({"transaction_ids": ["같은 급여 구간에서 고를 수 있는 입금만 함께 확인할 수 있어요."]})
            cycle_on = cycles.pop()
            if data["use_pattern"]:
                reason = "분할 급여는 자동 인식 기준으로 쓸 수 없어요" if len(txs) > 1 else memo_key(txs[0].source_description)[1]
                if reason:
                    raise ValidationError({"use_pattern": [reason]})
                SalaryDecision.objects.filter(user=user, is_pattern=True).exclude(transaction=txs[0]).update(is_pattern=False)
            elif len(txs) > 1:
                # 11/13: 월급을 여러 건으로 확인하면 예전 1건 기준을 끈다. 켜 두면 다음 달에 그 1건만 자동 인정돼
                # 나머지(식대 등)가 빠지고 '월급이 덜 들어왔어요'가 잘못 뜬다. 지난 확인 · 자동 인정 기록은 그대로
                SalaryDecision.objects.filter(user=user, is_pattern=True).update(is_pattern=False)
            # 이 회차의 판단을 고른 목록으로: 빠진 확인 · 자동 인정은 판단 없음으로 되돌린다
            SalaryDecision.objects.filter(user=user, pay_cycle_on=cycle_on).exclude(transaction_id__in=ids).delete()
            for tx in txs:
                SalaryDecision.objects.update_or_create(transaction=tx, defaults={
                    "user": user, "state": "confirmed", "pay_cycle_on": cycle_on, "is_pattern": data["use_pattern"],
                    **confirmation_snapshot(tx)})
            touched.add(cycle_on)
        else:
            owned = FinancialTransaction.objects.filter(user=user, source_kind="bank", direction="in", pk__in=ids)
            if owned.count() != len(ids):
                raise invalid
            if data["decision"] == "rejected":
                for tx in owned:
                    SalaryDecision.objects.update_or_create(transaction=tx, defaults={
                        "user": user, "state": "rejected", "pay_cycle_on": None, "is_pattern": False})
            else:
                SalaryDecision.objects.filter(user=user, transaction_id__in=ids).delete()
        result = deposits_data(user, profile, today)
        for cycle_on in touched:
            store_income(user, cycle_on)
        still = set(BudgetCycle.objects.filter(pk__in=based, income_basis__isnull=False).values_list("pk", flat=True))
        return Response({**result, "basis_reset": bool(based - still)})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def salary_pattern(request):
    """자동 인식 기준 해제. 지난 확인 · 자동 인정 기록은 그대로 두고, 아직 확인 전인 이번 · 다음 회차 자동 인정만 확인 대상으로 돌린다"""
    today = timezone.localdate()
    with transaction.atomic():
        profile = locked_profile(request.user, today)
        SalaryDecision.objects.filter(user=request.user, is_pattern=True).update(is_pattern=False)
        return Response(deposits_data(request.user, profile, today))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def salary_gap_answer(request):
    """월급 차이 안내에 답하기 (11/13, docs/api/salary.md). 화면이 본 받은 금액(salaryAmount)이 지금과 다르면 400.
    apply → 이번 주기 budget_cycle.income_basis = 받은 금액 (등록 실수령액은 그대로). keep → 기준은 그대로.
    둘 다 income_answered_for = 받은 금액이라 같은 금액으로는 다시 묻지 않는다. 같은 답을 다시 보내도 성공"""
    from .home import ensure_cycle

    serializer = SalaryGapSerializer(data=body_dict(request))
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    today = timezone.localdate()
    with transaction.atomic():
        profile = locked_profile(request.user, today)
        start, next_payday, _ = salary_cycle_dates(profile, today)
        cycle = ensure_cycle(request.user, start, next_payday)
        refresh_auto(request.user, salary_windows(profile, today))
        received = sum(tx.amount for tx in recognized(request.user, start))
        if received != data["salary_amount"]:
            raise ValidationError({"salary_amount": ["받은 월급이 바뀌었어요. 홈을 새로고침해 주세요."]})
        changed = ["income_answered_for"]
        if data["answer"] == "apply" and cycle.income_basis != received:
            cycle.income_basis = received
            changed.append("income_basis")
            expire_salary_drafts(request.user)  # 기준 금액이 바뀌면 만들어 둔 초안은 다시 추천
        cycle.income_answered_for = received
        cycle.save(update_fields=changed)
        return Response({"income_basis": cycle.income_basis, "salary_amount": received})
