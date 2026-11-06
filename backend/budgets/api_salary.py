from django.db import transaction
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
from .models import SalaryDecision
from .salary import (current_salary_profile, expire_salary_drafts, known_deposits, memo_key, next_payday_on,
                     refresh_auto, salary_cycle_dates, salary_windows, store_income)
from .salary_serializers import SalarySerializer


def salary_data(profile, today):
    pending = None
    if profile and profile.payday_changes_on and today < profile.payday_changes_on:
        pending = {"payday": profile.scheduled_payday, "effective_on": profile.payday_changes_on}
    return {
        "take_home_income": profile.monthly_take_home_income if profile else None,
        "payday": profile.payday if profile else None,
        "pending_payday_change": pending,
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
    payday = data["payday"]
    apply_from = data.get("payday_apply_from")
    pending = profile.payday_changes_on is not None and today < profile.payday_changes_on
    if profile.payday is None:
        if apply_from == "next_cycle":
            raise ValidationError({"payday_apply_from": ["첫 급여일은 바로 적용해 주세요."]})
        profile.payday = payday
    elif payday != profile.payday:
        if apply_from is None:
            raise ValidationError({"payday_apply_from": ["이번 주기부터인지 다음 주기부터인지 선택해 주세요."]})
        if apply_from == "next_cycle":
            if not (pending and profile.scheduled_payday == payday):
                start = salary_cycle_dates(profile, today)[0]
                profile.scheduled_payday = payday
                profile.payday_changes_on = next_payday_on(payday, today)
                profile.payday_previous_start = start
        else:
            profile.payday = payday
            profile.scheduled_payday = profile.payday_changes_on = profile.payday_previous_start = None
    elif pending and apply_from == "current_cycle":
        profile.scheduled_payday = profile.payday_changes_on = profile.payday_previous_start = None

    profile.monthly_take_home_income = data["take_home_income"]
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
        "is_candidate": (decision is None or decision.state == "recheck") and bool(income) and tx.amount * 10 >= income * 8,
        "pattern_unusable_reason": memo_key(tx.source_description)[1],
    }


def deposits_data(user, profile, today):
    """급여 구간(이번 · 다음 조기)마다 고를 수 있는 입금 전부 + 지금 자동 인식 기준. 호출 쪽에서 회원 잠금"""
    windows = salary_windows(profile, today)
    refresh_auto(user, windows)
    store_income(user, windows[0][0])
    income = profile.monthly_take_home_income
    pool = known_deposits(user).select_related("account__connection", "salary_decision")
    base = SalaryDecision.objects.filter(user=user, is_pattern=True).select_related("transaction__account__connection").first()
    return {
        "windows": [{
            "pay_cycle_on": cycle_on.isoformat(), "from": low.isoformat(), "to": high.isoformat(),
            "items": [deposit_json(tx, cycle_on, income) for tx in pool.filter(occurred_on__range=(low, high))],
        } for cycle_on, low, high in windows if low <= high],
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
        windows = salary_windows(profile, today)
        touched = set(SalaryDecision.objects.filter(user=user, transaction_id__in=ids)
                      .exclude(pay_cycle_on=None).values_list("pay_cycle_on", flat=True))
        if data["decision"] == "confirmed":
            # 행 잠금: R11 자동 연결(transfer_rules.link_own_transfers)과 같은 거래를 두고 겹치면 한쪽만 확정된다.
            # 선택 해제 계좌라도 이미 확인한 입금은 다시 고를 수 있다 (새 입금은 관리 대상 계좌만)
            txs = list(known_deposits(user).filter(pk__in=ids).select_for_update(of=("self",)))
            cycles = {next((c for c, low, high in windows if low <= tx.occurred_on <= high), None) for tx in txs}
            if len(txs) != len(ids) or len(cycles) != 1 or None in cycles:
                raise ValidationError({"transaction_ids": ["같은 급여 구간에서 고를 수 있는 입금만 함께 확인할 수 있어요."]})
            cycle_on = cycles.pop()
            if data["use_pattern"]:
                reason = "분할 급여는 자동 인식 기준으로 쓸 수 없어요" if len(txs) > 1 else memo_key(txs[0].source_description)[1]
                if reason:
                    raise ValidationError({"use_pattern": [reason]})
                SalaryDecision.objects.filter(user=user, is_pattern=True).exclude(transaction=txs[0]).update(is_pattern=False)
            # 이 회차의 판단을 고른 목록으로: 빠진 확인 · 자동 인정은 판단 없음으로 되돌린다
            SalaryDecision.objects.filter(user=user, pay_cycle_on=cycle_on).exclude(transaction_id__in=ids).delete()
            for tx in txs:
                SalaryDecision.objects.update_or_create(transaction=tx, defaults={
                    "user": user, "state": "confirmed", "pay_cycle_on": cycle_on, "is_pattern": data["use_pattern"]})
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
        return Response(result)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def salary_pattern(request):
    """자동 인식 기준 해제. 지난 확인 · 자동 인정 기록은 그대로 두고, 아직 확인 전인 이번 · 다음 회차 자동 인정만 확인 대상으로 돌린다"""
    today = timezone.localdate()
    with transaction.atomic():
        profile = locked_profile(request.user, today)
        SalaryDecision.objects.filter(user=request.user, is_pattern=True).update(is_pattern=False)
        return Response(deposits_data(request.user, profile, today))
