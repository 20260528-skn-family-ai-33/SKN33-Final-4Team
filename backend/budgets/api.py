"""
budgets 앱 API: React 화면에 필요한 데이터를 JSON으로 준다.
- 고정지출 · 정기 저축: DB에 저장하고 읽는 실제 기능
- 홈 · 예산 · 쿠션: 아직 mock.py(가짜 데이터)를 그대로 보낸다
"""
import uuid
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from config.idempotency import idempotent
from .models import Budget, BudgetCycle, Category
from django.db import transaction
from django.db.models import F, Max, Sum
from django.utils import timezone
from finance.codef import BANKS
from finance.models import BankAccount, Card, FinancialTransaction, PaymentDue, RecurringExpense, RecurringSaving

from . import mock


def _must_pay():
    """'꼭 나갈 돈' — link 는 누르면 갈 React 화면 주소 (없으면 null = 카드 청구서 팝업)"""
    return [{**p, "link": p.get("link")} for p in mock.MUST_PAY]


@api_view(["GET"])
def home(request):
    """GET /api/home/ — 홈 화면 데이터"""
    return Response({
        "period": mock.PERIOD,
        "home": mock.HOME,
        "must_pay": _must_pay(),
        # 1차는 항상 null(예산 API 는 ML 후). 시안은 mock.BUDGET 이 None 일 때만 null 을 보내 안내 카드를 보여 준다
        "budget": None if mock.BUDGET is None else {"sample": True},
    })


@api_view(["GET"])
def budget(request):
    """GET /api/budgets/ — 카테고리 예산 화면 데이터"""
    return Response({
        "period": mock.PERIOD,
        "monthly": mock.MONTHLY,
        "month_split": mock.MONTH_SPLIT,
        "categories": mock.CATEGORIES,
        "living_used": mock.HOME["living_used"],
        "living_budget": mock.HOME["living_budget"],
        "unclassified": mock.UNCLASSIFIED,
    })


@api_view(["GET"])
def cushion(request):
    """GET /api/budgets/cushion/ — 머니 쿠션 화면 데이터"""
    return Response({"cushion": mock.CUSHION})

# ==================================================================
#  고정지출 · 정기 저축 API   (명세: docs/api/fixed.md · savings.md)
#
#   기능                   고정지출                       정기 저축
#   목록 보기 · 직접 추가    GET · POST    /fixed/          /savings/
#   수정 · 중지             PATCH · DELETE /fixed/<id>/     /savings/<id>/
#   후보 확정              POST          /fixed/confirm/   /savings/confirm/
#   후보 무시              POST          /fixed/ignore/    /savings/ignore/
#
#  읽는 순서: 주소 입구 함수 → 값 검사 함수 → 저장 함수 → 응답 모양 함수
# ==================================================================


# ---------------- 고정지출: 주소 입구 · 중지 · 후보 확정/무시 ----------------

# 고정지출 /fixed/ — GET 목록(후보 → 확정, 확정 합계) · POST 직접 추가
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def fixed_list(request):
    if request.method == "POST":
        return fixed_create(request)

    # 목록 : 후보 먼저 (최근에 찾은 순) -> 확정 (출금일 빠른 순, 모르면 아래)
    mine = RecurringExpense.objects.filter(user=request.user)
    suggested = mine.filter(status="suggested").order_by("-created_at")
    confirmed = mine.filter(status="confirmed").order_by(F("payment_day").asc(nulls_last=True))

    items = []
    for e in list(suggested) + list(confirmed):
        item = fixed_json(e)
        item["evidence_suggestion"] = None
        items.append(item)

    return Response({
        "items" : items,
        "summary" : {
            "confirmed_monthly_total" : confirmed.aggregate(total = Sum("amount"))["total"] or 0, # 확정 합계
            "suggested_count" : suggested.count()
        }
    })


# 고정지출 중지 — 확정만 중지하고, 이번 주기 안 낸 납부 예정 취소 → 204
def fixed_stop(request, recurring_id):
    with transaction.atomic():
        # 확정, 중지만 찾기
        expense = get_object_or_404(
            RecurringExpense, # 고정지출 테이블
            pk = recurring_id,
            user = request.user,
            status__in = ["confirmed", "stopped"],
        )

        if expense.status == "confirmed":
            expense.status = "stopped"
            expense.save(update_fields=["status"])

            # 이번 주기 시작일 -- 아직 주기가 없으면(온보딩 전) 오늘
            cycle = BudgetCycle.objects.filter(user=request.user, status="active").first()
            since = cycle.starts_on if cycle else timezone.localdate()

            # 이번 주기부터 안 낸 납부 예정만 취소
            PaymentDue.objects.filter(
                recurring = expense, # 이 고정지출의 회차들 중
                obligation_state="active", # 아직 유효하고
                payment_state = "unpaid", # 미납 상태이고
                due_date__gte=since, # 이번 주기 시작일 이후인 것
            ).update(obligation_state="canceled", canceled_at=timezone.now())

            # 승인 전 예산 초안 만료
            Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")
    return Response(status=204)

# 고정지출 /fixed/<id>/ — PATCH 수정 · DELETE 중지
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def fixed_detail(request, recurring_id):
    if request.method == "PATCH":
        return fixed_update(request, recurring_id)   # 수정
    return fixed_stop(request, recurring_id)         # 중지


# 고정지출 후보 확정 [맞아요] — 후보만 확정, 못 고르는 ID가 섞이면 전부 취소
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def fixed_confirm(request):
    ids = clean_ids(request, "recurring_ids")  # 검사 (칸 이름만 저축과 다름)

    with transaction.atomic():
        # 먼저 다 찾기 - 내 것, 후보나 이미 확정인 것만
        found = list(RecurringExpense.objects.filter(
            user=request.user,
            pk__in=ids,
            status__in=["suggested", "confirmed"],
        ))

        # 개수 비교 - 하나라도 못 찾았으면 아무것도 안 바꾸고 400
        if len(found) != len(ids):
            raise ValidationError({"recurring_ids": ["선택할 수 없는 항목이에요."]})

        # 후보만 확정으로 (이미 확정은 그대로 = 성공)
        changed = False
        for e in found:
            if e.status == "suggested":
                e.status = "confirmed"
                e.confirmed_at = timezone.now()
                e.save(update_fields=["status", "confirmed_at"])
                # TODO: 이번 주기 납부 예정 1건 만들기 (급여 주기가 생기면)
                changed = True

        # 고정지출 합계가 늘었으니 승인 전 예산 초안 만료
        if changed:
            Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

    return Response({"items": [fixed_json(e) for e in found]})


# 고정지출 후보 무시 [아니에요] — 후보만 무시 (지우지 않고 남겨서 다시 묻지 않음)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def fixed_ignore(request):
    ids = clean_ids(request, "recurring_ids")

    with transaction.atomic():
        found = list(RecurringExpense.objects.filter(
            user=request.user,
            pk__in=ids,
            status__in=["suggested", "ignored"],  # 후보, 이미 무시
        ))
        if len(found) != len(ids):
            raise ValidationError({"recurring_ids": ["선택할 수 없는 항목이에요."]})

        for e in found:
            if e.status == "suggested":
                e.status = "ignored"  # 같은 후보를 다시 묻지 않으려고 지우지 않고 남김
                e.save(update_fields=["status"])
        # 확정된 게 없으니 예산, 납부 예정은 그대로

    return Response({"items": [fixed_json(e) for e in found]})


# ---------------- 응답 모양 만들기 (고정지출 · 저축 공통) ----------------

# 계좌 → "국민 급여통장 · ****5678" 이름표 (없으면 null)
def account_json(a):
    # 계좌가 안 정해졌으면 null
    if a is None:
        return None

    bank = BANKS.get(a.connection.organization_code, "")
    label = a.alias or a.name or "입출금"
    last4 = (a.masked_number or "")[-4:] # 끝 4자리
    return{
        "account_id" : str(a.pk),
        "display_name" : f"{bank} {label} · ****{last4}",
        "status" : a.connection.status, # 계좌 연결 상태
    }
# 고정지출 한 건 → 응답 JSON (목록 · 추가 · 수정 · 확정 · 무시 공통)
def fixed_json(e):
    # 근거 거래 : evidence(거래 ID 목록) 중 가장 최근 거래 1건을 보여줌. 없으면 null
    evidence = None
    if e.evidence:
        t = FinancialTransaction.objects.filter(pk__in=e.evidence).order_by("-occurred_on").first()
        if t:
            evidence = {
                "transaction_id" : str(t.pk),
                "merchant_name" : t.merchant_name,
                "date" : t.occurred_on.isoformat(),
                "amount" : t.amount,
            }

    return{
        "recurring_id" : str(e.pk),
        "name" : e.name,
        "amount" : e.amount,
        "payment_day" : e.payment_day,
        "payment_account" : account_json(e.payment_account),
        "payment_card" : card_json(e.payment_card),
        "category" : e.category_id,
        "category_name" : e.category.display_name,
        "status" : e.status,
        "source_type" : e.source_type,
        "confirmed_at" : timezone.localtime(e.confirmed_at).isoformat(timespec="seconds") if e.confirmed_at else None,
        "evidence" : evidence,
    }


# ---------------- 고정지출: 값 검사 · 저장 ----------------

# 고정지출 추가 · 수정 때 보낼 수 있는 칸 (그 외 칸은 400)
FIXED_FIELDS = {"name", "amount", "payment_day", "payment_account_id", "payment_card_id", "category", "evidence_transaction_id"}

# 보낸 카드 ID → 고를 수 있는 내 카드 (못 고르면 None)
def find_card(user, value):
    try:
        card_id = uuid.UUID(str(value))
    except ValueError:
        return None
    return Card.objects.filter(
        user = user,
        pk = card_id,
        is_selected = True,
        connection__disconnected_at__isnull = True, # 연결해제 안 된 것만
    ).first()

# 고정지출 직접 추가 값 검사 → 통과값 / 틀리면 400
def clean_fixed_create(request):
    data = request.data
    errors = {}
    values = {}

    # 표에 없는 칸 -> 400
    for key in data:
        if key not in FIXED_FIELDS:
            errors[key] = ['이 칸은 보낼 수 없어요.']

    # 이름, 금액
    name = data.get("name")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 100):
        errors["name"] = ["이름은 1~100자로 입력해 주세요."]
    else:
        values["name"] = name.strip()

    amount = data.get("amount")
    if type(amount) is not int or not (1_000 <= amount <= 100_000_000):
        errors["amount"] = ["금액은 1,000원 ~ 1억 원으로 입력해 주세요."]
    else:
        values["amount"] = amount

    # 출금일 : 1~31 또는 비움
    day = data.get("payment_day")

    if day is not None and (type(day) is not int or not (1 <= day <= 31)):
        errors["payment_day"] = ["출금일은 1일부터 31일 사이로 입력해 주세요."]
    else:
        values["payment_day"] = day

    # 카테고리 : 코드 표에 있고, 쓰는 중이고, '확인 필요'가 아닌 것
    code = data.get("category")
    category = None

    if isinstance(code, str) and code != "UNCLASSIFIED":
        category = Category.objects.filter(pk = code, is_active = True).first()
    if category is None:
        errors["category"] = ["선택할 수 없는 카테고리예요."]
    else:
        values["category"] = category

    # 결제 계좌(선택)
    account_value = data.get("payment_account_id")
    values["payment_account"] = None
    if account_value is not None:
        account = find_account(request.user, account_value, sending=True)
        if account is None or account.budget_role == "savings":
            errors["payment_account_id"] = ["선택할 수 없는 계좌예요."]
        else:
            values["payment_account"] = account

    # 결제 카드
    card_value = data.get("payment_card_id")
    values["payment_card"] = None
    if card_value is not None:
        card = find_card(request.user, card_value)
        if card is None:
            errors["payment_card_id"] = ["선택할 수 없는 카드예요."]
        else:
            values["payment_card"] = card

    # 계좌, 카드 둘 다 보내면 안됨 (둘 다 null은 OK)
    if account_value is not None and card_value is not None:
        errors["non_field_errors"] = ["계좌와 카드 중 하나만 골라 주세요."]

    # 근거 거래
    if data.get("evidence_transaction_id") is not None:
        errors["evidence_transaction_id"] = ["선택할 수 없는 거래예요."]

    if errors:
        raise ValidationError(errors)
    return values

# 고정지출 수정 값 검사 (보낸 칸만) → 통과값 / 틀리면 400
def clean_fixed_update(request, expense):
    data = request.data
    errors = {}
    values = {}

    # {} -> 400
    if not data:
        raise ValidationError({"non_field_errors" : ["바꿀 값을 하나 이상 보내 주세요."]})

    # 표에 없는 칸 -> 400
    for key in data:
        if key not in FIXED_FIELDS:
            errors[key] = ['이 칸은 보낼 수 없어요.']

    # 이름 검사 (Null 불가능)
    if "name" in data:
        name = data["name"]
        if name is None:
            errors["name"] = ['이름은 비울 수 없어요.']
        elif not isinstance(name, str) or not (1 <= len(name.strip()) <= 100):
            errors["name"] = ['이름은 1~100자로 입력해 주세요.']
        else:
            values["name"] = name.strip()

    # 금액 검사 (Null 불가능)
    if "amount" in data:
        amount = data["amount"]
        if amount is None:
            errors["amount"] = ['금액은 비울 수 없어요.']
        elif type(amount) is not int or not(1_000 <= amount <= 100_000_000):
            errors["amount"] = ["금액은 1,000원 ~ 1억 원으로 입력해 주세요."]
        else:
            values["amount"] = amount

    # 출금일 검사
    if "payment_day" in data:
        day = data["payment_day"]
        if day is not None and (type(day) is not int or not (1 <= day <= 31)):
            errors["payment_day"] = ["출금일은 1일부터 31일 사이로 입력해 주세요."]
        else:
            values["payment_day"] = day

    # 카테고리 검사
    if "category" in data:
        code = data["category"]
        category = None
        if isinstance(code, str) and code != "UNCLASSIFIED":
            category = Category.objects.filter(pk = code, is_active = True).first()
        if code is None:
            errors["category"] = ["카테고리는 비울 수 없어요."]
        elif category is None:
            errors["category"] = ["선택할 수 없는 카테고리예요."]
        else:
            values["category"] = category

    # 계좌 검사
    if "payment_account_id" in data:
        value = data["payment_account_id"]
        if value is None:
            values["payment_account"] = None
        elif str(value) != str(expense.payment_account_id):  # 새로 고를 때만 검사
            account = find_account(request.user, value, sending=True)
            if account is None or account.budget_role == "savings":
                errors["payment_account_id"] = ["선택할 수 없는 계좌예요."]
            else:
                values["payment_account"] = account

    # 결제 카드
    if "payment_card_id" in data:
        value = data["payment_card_id"]
        if value is None:
            values["payment_card"] = None
        elif str(value) != str(expense.payment_card_id):
            card = find_card(request.user, value)
            if card is None:
                errors["payment_card_id"] = ["선택할 수 없는 카드예요."]
            else:
                values["payment_card"] = card

    # 근거 거래
    if "evidence_transaction_id" in data:
        if data["evidence_transaction_id"] is None:
            values["evidence"] = []
        else:
            errors["evidence_transaction_id"] = ["선택할 수 없는 거래예요."]

    # 계좌, 카드가 둘 다 선택되어 있으면
    if not errors:
        final_account = values.get("payment_account", expense.payment_account)
        final_card = values.get("payment_card", expense.payment_card)
        if final_account and final_card:
            errors["non_field_errors"] = ["계좌와 카드 중 하나만 골라 주세요."]

    if errors:
        raise ValidationError(errors)
    return values

# 고정지출 직접 추가 저장 → 바로 확정 → 201
@idempotent() # 같은 요청 번호로 두 번 와도 한 번만 저장
def fixed_create(request):
    values = clean_fixed_create(request)

    with transaction.atomic():
        expense = RecurringExpense.objects.create(
            user  = request.user,
            **values,
            status = "confirmed",
            source_type="manual",
            confirmed_at=timezone.now(),
        )

        # TODO: 이번 주기 납부 예정 1건 만들기 (급여 주기가 생기면)

        # 예산 초안 만료
        Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

    return Response(fixed_json(expense), status = 201)

# 고정지출 수정 저장 (보낸 칸만) → 이번 주기 안 낸 납부 예정도 맞춤 → 200
def fixed_update(request, recurring_id):
    with transaction.atomic():
        expense = get_object_or_404(
            RecurringExpense,
            pk = recurring_id,
            user = request.user,
            status__in = ["suggested", "confirmed"],
        )

        values = clean_fixed_update(request, expense)

        # 바뀌기 전에 뭐가 바뀌는지 기억
        amount_changed = "amount" in values and values["amount"] != expense.amount
        to_card = values.get("payment_card") is not None  # 새 카드로 바꿈
        day_cleared = "payment_day" in values and values["payment_day"] is None  # 출금일 비움

        for key, value in values.items():
            setattr(expense, key, value)
        expense.save()

        if expense.status == "confirmed":
            # 이번 주기부터 안 낸 납부 예정
            cycle = BudgetCycle.objects.filter(user=request.user, status="active").first()
            since = cycle.starts_on if cycle else timezone.localdate()
            unpaid = PaymentDue.objects.filter(
                recurring=expense,
                obligation_state="active",
                payment_state="unpaid",
                due_date__gte=since,
            )

            if to_card or day_cleared:
                unpaid.update(obligation_state="canceled", canceled_at=timezone.now())
            elif amount_changed:
                unpaid.update(confirmed_amount=expense.amount)
            # TODO: 출금일이 바뀌면 안 낸 회차 날짜 바꾸기 · 출금일이 새로 생기면 회차 만들기 (급여 주기가 생기면)

            # 고정지출 합계가 바뀌면 승인 전 예산 초안 만료
            if amount_changed:
                Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

    return Response(fixed_json(expense))

# 카드 → "신한 Deep Dream · ****1234" 이름표 (없으면 null)
def card_json(c):
    if c is None:
        return None
    label = c.name or "카드"
    last4 = (c.masked_number or "")[-4:]
    return{
        "card_id" : str(c.pk),
        "display_name" : f"{label} · ****{last4}",
        "status" : c.connection.status if c.connection else "connected",
    }



# 저축 한 건 → 응답 JSON (목록 · 추가 · 수정 · 확정 · 무시 공통)
def saving_json(s):
    last = None
    if s.evidence:
        last = FinancialTransaction.objects.filter(pk__in=s.evidence).aggregate(m=Max("occurred_on"))["m"]
    return{
        "saving_id" : str(s.pk), # 저축 ID
        "name" : s.name, # 이름
        "amount" : s.amount, # 매달 저축액
        "transfer_day" : s.transfer_day, # 매월 이체일
        "from_account" : account_json(s.from_account), # 보내는 계좌
        "to_account" : account_json(s.to_account), # 받는 계좌
        "status" : s.status,
        "source_type" : s.source_type,
        "confirmed_at" : timezone.localtime(s.confirmed_at).isoformat(timespec="seconds") if s.confirmed_at else None,
        "evidence_count" : len(s.evidence or []),
        "last_transfer_on" : last.isoformat() if last else None,
    }


# ---------------- 정기 저축: 값 검사 ----------------

# 저축 추가 · 수정 때 보낼 수 있는 칸 (그 외 칸은 400)
ALLOWED_FIELDS = {"name", "amount", "transfer_day", "from_account_id", "to_account_id"}

# 보낸 계좌 ID → 고를 수 있는 내 계좌 (못 고르면 None) · 고정지출도 같이 씀
#   sending=True(보내는 · 결제 계좌): 관리하는 입출금 통장만 / False(받는 계좌): 내 통장이면 OK
def find_account(user, value, sending):
    try:
        account_id = uuid.UUID(str(value))
    except ValueError:
        return None
    accounts = BankAccount.objects.filter(
        user=user,
        pk=account_id,
        connection__disconnected_at__isnull=True, # 연결 해제 안 된 것만
    )
    if sending:
        accounts = accounts.filter(is_selected = True, account_type = "demand")
    return accounts.first()

# 저축 직접 추가 값 검사 → 통과값 / 틀리면 400 (틀린 칸은 모아서 한 번에)
def clean_create(request):
    data = request.data
    errors = {}
    values = {}

    # 표에 없는 칸 -> 400
    for key in data:
        if key not in ALLOWED_FIELDS:
            errors[key] = ['이 칸은 보낼 수 없어요.']

    # 이름 : 글자, 앞뒤 공백 빼고 1~100자 (필수)
    name = data.get("name")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 100):
        errors["name"] = ['이름은 1~100자로 입력해 주세요.']
    else:
        values["name"] = name.strip()

    # 금액 : 정수 1000 ~ 1억 (필수)
    amount = data.get("amount")
    if type(amount) is not int or not (1_000 <= amount <= 100_000_000):
        errors["amount"] = ["금액은 1,000원 ~ 1억 원으로 입력해 주세요."]
    else:
        values["amount"] = amount

    # 이체일 : 1~31 또는 비움(null) (선택)
    day = data.get("transfer_day")
    if day is not None and (type(day) is not int or not (1 <= day <= 31)): # 이체일 값이 들어있는데 1~31일 사이가 아니면
        errors["transfer_day"] = ['이체일은 1일부터 31일 사이로 입력해 주세요.']
    else:
        values["transfer_day"] = day

    # 보내는 계좌 / 받는 계좌 (선택) / 비우면 None, 고를 수 없는 계좌면 400
    for field, key, sending in (("from_account_id", "from_account", True),
                                ("to_account_id", "to_account", False)):
        value = data.get(field)

        if value is None:
            values[key] = None
        else:
            account = find_account(request.user, value, sending)
            if account is None:
                errors[field] = ['선택할 수 없는 계좌예요.']
            else:
                values[key] = account

    # 두 계좌가 같으면 안됨
    if (not errors
            and values["from_account"] and values["to_account"]
            and values["from_account"].pk == values["to_account"].pk):
        errors["non_field_errors"] = ['보내는 계좌와 받는 계좌가 같아요.']

    # 하나라도 틀렸으면 -> 400
    if errors:
        raise ValidationError(errors)
    return values

# 저축 수정 값 검사 (보낸 칸만) → 통과값 / 틀리면 400
def clean_update(request, saving):
    data = request.data
    errors = {}
    values = {}

    # {} -> 400
    if not data:
        raise ValidationError({"non_field_errors" : ["바꿀 값을 하나 이상 보내 주세요."]})

    # 표에 없는 칸 -> 400
    for key in data:
        if key not in ALLOWED_FIELDS:
            errors[key] = ['이 칸은 보낼 수 없어요.']

    # 이름 검사 (Null 불가능)
    if "name" in data:
        name = data["name"]
        if name is None:
            errors["name"] = ['이름은 비울 수 없어요.']
        elif not isinstance(name, str) or not (1 <= len(name.strip()) <= 100):
            errors["name"] = ['이름은 1~100자로 입력해 주세요.']
        else:
            values["name"] = name.strip()

    # 금액 검사 (Null 불가능)
    if "amount" in data:
        amount = data["amount"]
        if amount is None:
            errors["amount"] = ['금액은 비울 수 없어요.']
        elif type(amount) is not int or not(1_000 <= amount <= 100_000_000):
            errors["amount"] = ["금액은 1,000원 ~ 1억 원으로 입력해 주세요."]
        else:
            values["amount"] = amount

    # 이체일 검사
    if "transfer_day" in data:
        day = data["transfer_day"]

        if day is not None and (type(day) is not int or not (1 <= day <= 31)):
            errors["transfer_day"] = ["이체일은 1일부터 31일 사이로 입력해 주세요."]
        else:
            values["transfer_day"] = day

    # 계좌 검사
    for field, key, sending in (("from_account_id", "from_account", True),
                                ("to_account_id", "to_account", False)):

        if field not in data:
            continue

        value = data[field]
        account = None if value is None \
            else find_account(request.user, value, sending)
        if value is not None and account is None:
            errors[field] = ['선택할 수 없는 계좌예요']
            continue

        # 후보는 계좌를 바꿀 수 없음
        current = getattr(saving, key)
        if saving.status == "suggested" and account != current:
            errors[field] = ["찾은 저축의 계좌는 바꿀 수 없어요. [아니에요]를 누르고 직접 추가해 주세요."]
            continue

        values[key] = account

    # 바꾼 뒤 모양으로 두 계좌가 같은지 검사
    if not errors:
        new_form = values.get("from_account", saving.from_account)
        new_to = values.get("to_account", saving.to_account)
        if new_form and new_to and new_form.pk == new_to.pk:
            errors["non_field_errors"] = ["보내는 계좌와 받는 계좌가 같아요."]

    if errors:
        raise ValidationError(errors)
    return values

# 후보 확정 · 무시 때 보낸 ID 목록 검사 → 중복 뺀 ID 목록 / 틀리면 400 · 고정지출도 같이 씀
#   field: 저축 "saving_ids"(기본값) · 고정지출 "recurring_ids"
def clean_ids(request, field="saving_ids"):
    data = request.data
    errors = {}

    # 표에 없는 칸 -> 400 (ID 목록 칸 말고는 보내면 안 됨)
    for key in data:
        if key != field:
            errors[key] = ['이 칸은 보낼 수 없어요.']

    ids = data.get(field)
    if not isinstance(ids, list) or not ids: # list가 아니거나 비어있으면
        errors[field] = ['선택할 수 없는 항목이에요.']
    else:
        try:
            ids = [uuid.UUID(str(x)) for x in ids] # 하나씩 id 모양인지 확인
        except ValueError:
            errors[field] = ["선택할 수 없는 항목이에요."]

    if errors:
        raise ValidationError(errors)

    return list(set(ids)) # 같은 ID를 두 번 보내도 한 번만 (set()은 중복을 없앰)


# ---------------- 정기 저축: 저장 (직접 추가 · 수정) ----------------

# 저축 직접 추가 저장 → 바로 확정 → 201
@idempotent() # 같은 요청 번호로 두 번 와도 한 번만 저장
def savings_create(request):
    values = clean_create(request) # 검사

    with transaction.atomic(): # 아래 저장들을 한 묶음으로
        saving = RecurringSaving.objects.create(
            user = request.user,
            **values, # name, amount, transfer_day, from_account, to_account
            status = "confirmed",
            source_type = "manual",
            confirmed_at = timezone.now(),
        )

        # 받는 계좌를 골랐으면 그 계좌 용도를 '저축'으로
        if saving.to_account is not None: # 받는 계좌가 지정되어 있으면
            saving.to_account.budget_role = "savings"
            saving.to_account.save(update_fields=["budget_role"])
        # 저축 최소 금액이 늘었으니 승인 전 예산 초안 만료
        Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

    return Response(saving_json(saving), status=201)

# 저축 수정 저장 (보낸 칸만) → 200
def savings_update(request, saving_id):
    with transaction.atomic():
        saving = get_object_or_404(
            RecurringSaving,
            pk = saving_id,
            user = request.user,
            status__in = ["suggested", "confirmed"],
        )

        values = clean_update(request, saving)

        # 바꾸기 전에 뭐가 바뀌는지 기억해두는 용도
        amount_changed = "amount" in values and values["amount"] != saving.amount
        to_changed = "to_account" in values and values["to_account"] != saving.to_account

        # 보낸 칸만 덮어쓰기
        for key, value in values.items():
            setattr(saving, key, value)
        saving.save()

        if saving.status == "confirmed":
            if to_changed and saving.to_account is not None:
                saving.to_account.budget_role = "savings"
                saving.to_account.save(update_fields=["budget_role"])

            if amount_changed:
                Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

    return Response(saving_json(saving))


# ---------------- 정기 저축: 주소 입구 ----------------

# 저축 /savings/ — GET 목록(후보 → 확정, 저축 최소 금액) · POST 직접 추가
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def savings_list(request):
    if request.method == "POST":
        return savings_create(request)

    # 목록 : 후보 먼저 -> 확정 (이체일 빠른 순, 모르면 제일 아래)
    mine = RecurringSaving.objects.filter(user = request.user) # user가 요청한 user와 동일한 객체
    suggested = mine.filter(status = "suggested").order_by("-created_at")
    confirmed = mine.filter(status = "confirmed").order_by(F("transfer_day").asc(nulls_last=True))

    return Response({
        "items" : [saving_json(s) for s in suggested] + [saving_json(s) for s in confirmed],
        "summary" : {
            "minimum_monthly" : confirmed.aggregate(total=Sum("amount"))["total"] or 0, # 확정 금액 합계
            "suggested_count" : suggested.count()
        }
    })

# 저축 /savings/<id>/ — PATCH 수정 · DELETE 중지 (확정만 중지, 이미 중지면 그대로)
@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def savings_detail(request, saving_id):
    if request.method == "PATCH":
        return savings_update(request, saving_id)

    # DELETE
    saving = get_object_or_404( # 찾아보고 없으면 자동으로 404
        RecurringSaving, # 정기 저축 표
        pk = saving_id,
        user = request.user,
        status__in = ["confirmed", "stopped"],
    )
    if saving.status == "confirmed":
        saving.status = "stopped"
        saving.save(update_fields=["status"])
        # 저축 최소 금액이 줄었으니 승인 전 예산 초안 만료
        Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")
    return Response(status=204)



# ---------------- 정기 저축: 후보 확정 · 무시 ----------------

# 저축 후보 확정 [저축이에요] — 받는 계좌는 저축 용도, 근거 이체는 '저축'으로
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def savings_confirm(request):
    ids = clean_ids(request)

    with transaction.atomic():
        # 먼저 다 찾기 - 내 것, 후보나 이미 확정인 것만
        found = list(RecurringSaving.objects.filter(
            user = request.user,
            pk__in = ids,
            status__in = ["suggested", "confirmed"],
        ))

        # 개수 비교
        if len(found) != len(ids):
            raise ValidationError({"saving_ids" : ['선택할 수 없는 항목이에요.']})

        # 후보만 확정으로 바꾸기
        changed = False
        for s in found:
            if s.status == "suggested":
                s.status = "confirmed"
                s.confirmed_at = timezone.now()
                s.save(update_fields=["status", "confirmed_at"])

                # 받는 계좌 -> "저축" 용도
                if s.to_account is not None:
                    s.to_account.budget_role = "savings"
                    s.to_account.save(update_fields=["budget_role"])

                # 근거 이체들 -> 거래 종류 "저축"
                if s.evidence:
                    FinancialTransaction.objects.filter(pk__in=s.evidence).update(transaction_type="saving")

                changed = True

        # 새로 확정된 게 있으면 저축 최소 금액이 늘었으니 예산 초안 만료
        if changed:
            Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

    return Response({"items" : [saving_json(s) for s in found]})

# 저축 후보 무시 [아니에요] — 근거 이체는 '내 계좌끼리 옮긴 돈'으로
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def savings_ignore(request):
    ids = clean_ids(request)

    with transaction.atomic():
        found = list(RecurringSaving.objects.filter(
            user = request.user,
            pk__in = ids,
            status__in = ["suggested", "ignored"],
        ))

        if len(found) != len(ids):
            raise ValidationError({"saving_ids" : ['선택할 수 없는 항목이에요.']})

        for s in found:
            if s.status == "suggested":
                s.status = "ignored"
                s.save(update_fields=["status"])
                # 근거 이체 -> '내 계좌끼리 옮긴 돈'
                if s.evidence:
                    FinancialTransaction.objects.filter(pk__in=s.evidence).update(transaction_type="own_transfer")

    return Response({"items" : [saving_json(s) for s in found]})