"""마이페이지 요약. 금융기관을 재조회하지 않고 회원의 저장 자료를 집계한다."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from budgets.api_salary import salary_data
from budgets.salary import current_salary_profile
from finance.models import BankAccount, Card, Connection, RecurringExpense, RecurringSaving


def recurring_summary(model, user):
    result = model.objects.filter(user=user, status="confirmed").aggregate(count=Count("pk"), monthly_total=Sum("amount"))
    result["monthly_total"] = result["monthly_total"] or 0
    return result


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_summary(request):
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        today = timezone.localdate()
        profile = current_salary_profile(request.user, today)
        connections = Connection.objects.filter(user=request.user, disconnected_at__isnull=True)
        return Response({
            "salary": salary_data(profile, today),
            "fixed": recurring_summary(RecurringExpense, request.user),
            "savings": recurring_summary(RecurringSaving, request.user),
            "protect": {
                "living_budget": profile.living_budget if profile else None,
                "savings_floor": profile.savings_floor if profile else None,
                "cushion_monthly": profile.cushion_monthly if profile else None,
            },
            "connections": {
                "bank_count": connections.filter(business_type="bank").values("organization_code").distinct().count(),
                "account_count": BankAccount.objects.filter(user=request.user, connection__in=connections).count(),
                "card_count": Card.objects.filter(user=request.user, connection__in=connections).count(),
            },
        })
