from datetime import date
from types import SimpleNamespace

from users.models import FinancialProfile


def uses_monthly_dues(user):
    return FinancialProfile.objects.filter(user_id=getattr(user, "pk", user), payday_revision__gt=0).exists()


def next_month(day):
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def occurrence_cycles(user, cycle):
    if cycle is None:
        return []
    if getattr(cycle, "monthly_occurrence", False) or not uses_monthly_dues(user):
        return [cycle]
    result = []
    month = cycle.starts_on.replace(day=1)
    while month < cycle.ends_before:
        end = next_month(month)
        result.append(SimpleNamespace(
            user_id=cycle.user_id, starts_on=max(cycle.starts_on, month),
            ends_before=min(cycle.ends_before, end), monthly_occurrence=True,
        ))
        month = end
    return result


def monthly_due(queryset, due_date):
    """예전 주기 키도 같은 납부 월이면 그대로 재사용한다."""
    month = due_date.replace(day=1)
    return queryset.filter(due_date__gte=month, due_date__lt=next_month(month)).order_by(
        "-paid_amount", "obligation_state", "created_at", "pk",
    ).first()


def occurrence_key(kind, pk, cycle, due_date):
    if getattr(cycle, "monthly_occurrence", False):
        return f"{kind}:{pk}:month:{due_date:%Y-%m}"
    return f"{kind}:{pk}:{cycle.starts_on.isoformat()}"
