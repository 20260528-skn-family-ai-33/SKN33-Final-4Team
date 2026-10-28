from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from budgets.api import day_in_cycle, lock_user
from budgets.models import BudgetCycle
from config.idempotency import idempotent
from users.models import Consent

from .api_connect import PURPOSE, ConsentRequired
from .models import FinancialTransaction, Loan, Origin, PaymentDue
from .serializers import LoanCreateSerializer, LoanPatchSerializer

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

def owned_loans(user):
    """단일 FK는 관련 객체의 소유권까지 보장하지 않으므로 함께 검사한다."""
    return Loan.objects.filter(
        Q(connection__isnull=True) | Q(connection__user=user),
        Q(linked_account__isnull=True)
        | Q(linked_account__user=user, linked_account__connection__user=user),
        user=user,
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def loans(request):
    if request.method == "POST":
        return loan_create(request)

    has_consent = Consent.objects.filter(
        user=request.user,
        purpose=PURPOSE,
        product_scope=Consent.ProductScope.LOAN,
        revoked_at__isnull=True,
    ).exists()

    if not has_consent:
        raise ConsentRequired("대출 조회 동의가 필요해요.")

    rows = owned_loans(request.user).filter(
        status=Loan.Status.REPAYING,
    ).order_by("loan_id")

    return Response({"items": [loan_item(loan) for loan in rows]})


@idempotent()
@transaction.atomic
def loan_create(request):
    """POST 전용 처리: 대출·확인 정보·납부 예정을 함께 저장한다."""
    lock_user(request.user)
    serializer = LoanCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    loan = Loan.objects.create(
        user=request.user,
        origin=Origin.MANUAL,
        status=Loan.Status.REPAYING,
        **serializer.validated_data,
    )
    # 입력한 값은 사용자 확인 완료, 생략·null인 값은 모름으로 기록한다.
    apply_loan_patch(loan, {field: getattr(loan, field) for field in FIELD_NAMES})
    sync_loan_due(loan)
    return Response(loan_item(loan), status=201)


def apply_loan_patch(loan, validated_data):
    if not validated_data:
        return loan

    states = dict(loan.field_states) if isinstance(loan.field_states, dict) else {}
    previous_states = loan_field_states(loan)
    confirmed_at = local_iso(timezone.now())
    fields_to_save = []

    for field, value in validated_data.items():
        stored_key = FIELD_NAMES[field]
        old_value = getattr(loan, field)
        previous = previous_states[field]

        if value is None:
            new_state = {
                "source": None,
                "confirmationStatus": "unknown",
                "confirmedAt": None,
            }
        else:
            same_value = old_value == value
            source = "manual"

            if same_value and previous["source"] is not None:
                source = previous["source"]

            if same_value and previous["confirmation_status"] == "confirmed":
                field_confirmed_at = previous["confirmed_at"]
            else:
                field_confirmed_at = confirmed_at

            new_state = {
                "source": source,
                "confirmationStatus": "confirmed",
                "confirmedAt": field_confirmed_at,
            }

        if old_value != value:
            setattr(loan, field, value)
            fields_to_save.append(field)

        states[stored_key] = new_state

    if states != loan.field_states:
        loan.field_states = states
        fields_to_save.append("field_states")

    if fields_to_save:
        loan.save(update_fields=fields_to_save)

    return loan


def sync_loan_due(loan):
    today = timezone.localdate()
    from budgets.salary import activate_salary_if_due
    activate_salary_if_due(loan.user_id, today)

    cycle = BudgetCycle.objects.filter(
        user_id=loan.user_id,
        status=BudgetCycle.Status.ACTIVE,
        starts_on__lte=today,
        ends_before__gt=today,
    ).order_by("-starts_on").first()

    if cycle is None:
        return None

    from budgets.due_periods import uses_monthly_dues
    if uses_monthly_dues(loan.user_id):
        return sync_monthly_loan_dues(loan, cycle, today)

    lookup = {
        "user_id": loan.user_id,
        "loan_id": loan.loan_id,
        "obligation_type": PaymentDue.ObligationType.LOAN,
        "occurrence_key": f"loan:{loan.pk}:{cycle.starts_on.isoformat()}",
    }

    locked_dues = PaymentDue.objects.select_for_update()
    due_date = (
        day_in_cycle(cycle, loan.payment_day)
        if loan.monthly_payment is not None and loan.payment_day is not None
        else None
    )
    # 값이 있어도 이번 주기 밖이면 기존 미납 예정을 취소하는 경로로 보낸다.
    has_schedule = due_date is not None

    if has_schedule:
        amount_state = loan_field_states(loan)["monthly_payment"]
        values = {
            "due_date": due_date,
            "confirmed_amount": loan.monthly_payment,
            "source_type": amount_state["source"] or "unknown",
            "confirmation_state": amount_state["confirmation_status"],
            "obligation_state": PaymentDue.ObligationState.ACTIVE,
            "canceled_at": None,
            "payment_state": (
                PaymentDue.PaymentState.UNPAID
                if due_date >= today
                else PaymentDue.PaymentState.NEEDS_REVIEW
            ),
        }

        due, created = locked_dues.get_or_create(
            **lookup,
            defaults=values,
        )

        if created:
            return due

    else:
        due = locked_dues.filter(**lookup).first()
        if due is None:
            return None

    if (
        due.payment_state in (
            PaymentDue.PaymentState.PARTIAL,
            PaymentDue.PaymentState.PAID,
        )
        or due.paid_amount > 0
        or due.matches.exists()
    ):
        return due

    if not has_schedule:
        if due.obligation_state != PaymentDue.ObligationState.CANCELED:
            due.obligation_state = PaymentDue.ObligationState.CANCELED
            due.canceled_at = timezone.now()
            due.save(update_fields=["obligation_state", "canceled_at"])

        return due

    changed_fields = []

    for field, value in values.items():
        if getattr(due, field) != value:
            setattr(due, field, value)
            changed_fields.append(field)

    if changed_fields:
        due.save(update_fields=changed_fields)

    return due


def sync_monthly_loan_dues(loan, cycle, today):
    from budgets.due_periods import monthly_due, occurrence_cycles, occurrence_key

    locked = PaymentDue.objects.select_for_update().filter(user_id=loan.user_id, loan=loan)
    def protected(due):
        return due.paid_amount > 0 or due.payment_state in ("partial", "paid") or due.matches.exists()
    parts = occurrence_cycles(loan.user_id, cycle)
    dates = [day_in_cycle(part, loan.payment_day) for part in parts] if loan.payment_day else []
    dates = {value for value in dates if value is not None} if loan.monthly_payment is not None else set()
    for due in locked.filter(due_date__gte=cycle.starts_on, due_date__lt=cycle.ends_before):
        if not dates or (due.due_date not in dates and not any(
            (value.year, value.month) == (due.due_date.year, due.due_date.month) for value in dates
        )):
            if not protected(due) and due.obligation_state != "canceled":
                due.obligation_state, due.canceled_at = "canceled", timezone.now()
                due.save(update_fields=["obligation_state", "canceled_at"])
    result = None
    for part in parts:
        due_date = day_in_cycle(part, loan.payment_day) if loan.payment_day else None
        if due_date not in dates:
            continue
        due = monthly_due(locked, due_date)
        if due is not None and protected(due):
            result = result or due
            continue
        amount_state = loan_field_states(loan)["monthly_payment"]
        values = {
            "due_date": due_date, "confirmed_amount": loan.monthly_payment,
            "source_type": amount_state["source"] or "unknown",
            "confirmation_state": amount_state["confirmation_status"],
            "obligation_state": "active", "canceled_at": None,
            "payment_state": "unpaid" if due_date >= today else "needs_review",
        }
        if due is None:
            due = PaymentDue.objects.create(
                user_id=loan.user_id, loan=loan, obligation_type="loan",
                occurrence_key=occurrence_key("loan", loan.pk, part, due_date), **values,
            )
        else:
            changed = [name for name, value in values.items() if getattr(due, name) != value]
            for name in changed:
                setattr(due, name, values[name])
            if changed:
                due.save(update_fields=changed)
        result = result or due
    return result


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def loan_detail(request, loan_id):
    with transaction.atomic():
        lock_user(request.user)
        loan = get_object_or_404(
            # nullable 관계의 OUTER JOIN까지 잠그면 PostgreSQL에서 실패한다.
            owned_loans(request.user).select_for_update(of=("self",)),
            loan_id=loan_id,
            status=Loan.Status.REPAYING,
        )

        serializer = LoanPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        if serializer.validated_data:
            apply_loan_patch(loan, serializer.validated_data)
            sync_loan_due(loan)

        body = loan_item(loan)

    return Response(body)
