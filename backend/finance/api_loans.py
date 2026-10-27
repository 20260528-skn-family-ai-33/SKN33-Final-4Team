from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.models import Consent

from .api_connect import PURPOSE, ConsentRequired
from .models import FinancialTransaction, Loan


FIELD_NAMES = {
    "manual_balance": "manualBalance",
    "manual_rate": "manualRate",
    "repayment_method": "repaymentMethod",
    "monthly_payment": "monthlyPayment",
    "payment_day": "paymentDay",
}


def local_iso(value):
    if isinstance(value, str):
        try:
            value = parse_datetime(value)
        except ValueError:
            return None

    if not isinstance(value, datetime) or timezone.is_naive(value):
        return None

    return timezone.localtime(value).isoformat()

def loan_field_states(loan):
    stored = loan.field_states if isinstance(loan.field_states, dict) else {}
    result = {}

    for field, stored_key in FIELD_NAMES.items():
        state = {
            "source": None,
            "confirmation_status": "unknown",
            "confirmed_at": None,
        }

        if getattr(loan, field) is not None:
            saved = stored.get(stored_key, {})
            if not isinstance(saved, dict):
                saved = {}

            source = saved.get("source")
            if source in ("provider", "manual", "estimated"):
                state["source"] = source

            if field in ("manual_balance", "manual_rate"):
                state["source"] = "manual"

            confirmation = saved.get("confirmationStatus")
            if confirmation == "unconfirmed":
                state["confirmation_status"] = "unconfirmed"
            elif confirmation == "confirmed":
                confirmed_at = local_iso(saved.get("confirmedAt"))
                if confirmed_at is not None:
                    state["confirmation_status"] = "confirmed"
                    state["confirmed_at"] = confirmed_at

        result[field] = state

    return result


def loan_payment_history(loan):
    rows = (
        FinancialTransaction.objects.filter(
            user_id=loan.user_id,
            loan_id=loan.loan_id,
            transaction_type=FinancialTransaction.TransactionType.LOAN_REPAYMENT,
            direction=FinancialTransaction.Direction.OUT,
            is_canceled=False,
        )
        .order_by("-occurred_on", "-created_at", "-transaction_id")[:3]
    )

    history = [
        {
            "date": row.occurred_on,
            "principal": row.reported_principal,
            "interest": row.reported_interest,
        }
        for row in rows
    ]

    return history or None

def loan_item(loan):
    return {
        "loan_id": loan.loan_id,
        "lender_name": loan.lender_name,
        "product_name": loan.product_name,
        "origin": loan.origin,
        "status": loan.status,
        "reported_principal": loan.reported_principal,
        "reported_balance": loan.reported_balance,
        "reported_rate": loan.reported_rate,
        "manual_balance": loan.manual_balance,
        "manual_rate": loan.manual_rate,
        "maturity_date": loan.maturity_date,
        "reported_payment_date": loan.reported_payment_date,
        "repayment_method": loan.repayment_method,
        "monthly_payment": loan.monthly_payment,
        "payment_day": loan.payment_day,
        "payment_history": loan_payment_history(loan),
        # 추정액 계산은 상환 거래 판별·월별 집계 기준을 정한 뒤 연결한다.
        "estimated_monthly_payment": None,
        "overdue": loan.overdue,
        "field_states": loan_field_states(loan),
        "fetched_at": local_iso(loan.fetched_at),
    }

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def loans(request):
    has_consent = Consent.objects.filter(
        user=request.user,
        purpose=PURPOSE,
        product_scope=Consent.ProductScope.LOAN,
        revoked_at__isnull=True,
    ).exists()

    if not has_consent:
        raise ConsentRequired("대출 조회 동의가 필요해요.")

    rows = Loan.objects.filter(
        user=request.user,
        status=Loan.Status.REPAYING,
    ).order_by("loan_id")

    return Response({"items": [loan_item(loan) for loan in rows]})