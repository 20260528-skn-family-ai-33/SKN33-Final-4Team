from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from finance.inputs import body_dict
from users.models import FinancialProfile
from .api import lock_user
from .salary import current_salary_profile, expire_salary_drafts, next_payday_on, salary_cycle_dates
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
