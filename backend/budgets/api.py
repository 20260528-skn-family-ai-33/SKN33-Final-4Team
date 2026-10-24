"""
budgets 앱 API: React 화면에 필요한 데이터를 JSON으로 준다.
지금은 mock.py(가짜 데이터)를 그대로 보낸다. DB가 생기면 이 파일 안쪽만 바꾸면 된다.
"""
import uuid
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from config.idempotency import idempotent
from .models import Budget
from django.db import transaction
from django.db.models import F, Max, Sum
from django.utils import timezone
from finance.codef import BANKS
from finance.models import BankAccount, FinancialTransaction, RecurringSaving # 정기 저축 표

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
        # H-06: 1차는 항상 null(예산 API 는 ML 후). 시안은 mock.BUDGET 이 None 일 때만 null 을 보내 안내 카드를 보여 준다
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

# ======== 껍데기 API (2026-10-24) — 명세 예시 응답을 그대로 돌려준다 ========
from . import examples


# ---- 고정지출 19~24 ----
@api_view(["GET", "POST"])
def fixed_list(request):
    if request.method == "GET":
        return Response(examples.FIXED_LIST_EXAMPLE)               # 19 목록 보기
    return Response(examples.FIXED_CREATED_EXAMPLE, status=201)    # 20 직접 추가


@api_view(["PATCH", "DELETE"])
def fixed_detail(request, recurring_id):
    if request.method == "PATCH":
        return Response(examples.FIXED_UPDATED_EXAMPLE)            # 21 수정
    return Response(status=204)                                    # 22 중지 (본문 없음)


@api_view(["POST"])
def fixed_confirm(request):
    return Response(examples.FIXED_CONFIRM_EXAMPLE)                # 23 후보 확정


@api_view(["POST"])
def fixed_ignore(request):
    return Response(examples.FIXED_IGNORE_EXAMPLE)                 # 24 후보 무시


# ==================================================================
#  정기 저축 API  ·  명세: docs/api/savings.md
#
#   목록 보기           GET     /api/budgets/savings/
#   직접 추가           POST    /api/budgets/savings/
#   수정                PATCH   /api/budgets/savings/<id>/
#   중지                DELETE  /api/budgets/savings/<id>/
#   후보 확정 (저축이에요)  POST    /api/budgets/savings/confirm/
#   후보 무시 (아니에요)    POST    /api/budgets/savings/ignore/
# ==================================================================


# ---------------- [공통] 응답 모양 만들기 ----------------

# 계좌 → "국민 급여통장 · ****5678" 이름표 (없으면 null)
def account_json(a):
    """계좌 하나를 화면에 보여 줄 모양으로 바꾸는 함수"""

    # 1) 계좌가 안 정해진 저축이면 Null
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

# 저축 한 건 → 명세 모양 JSON (목록 · 추가 · 수정 · 확정 · 무시 공통)
def saving_json(s):
    """정기 저축 한 건을 명세 모양 JSON으로 바꾸는 함수'"""
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


# ---------------- [공통] 보낸 값 검사 ----------------

# 추가 · 수정 때 보낼 수 있는 칸 (그 외 칸은 400)
ALLOWED_FIELDS = {"name", "amount", "transfer_day", "from_account_id", "to_account_id"}

# 보낸 계좌 ID → 고를 수 있는 내 계좌 (못 고르면 None)
def find_account(user, value, sending):
    """계좌 ID로 내 계좌 찾기
    - 보내는 계좌 : 내가 관리하는 입출금 통장
    - 받는 계좌 : 내 통장이면 OK
    """
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

# [직접 추가] 보낸 값 검사 → 통과값 반환 / 틀리면 400
def clean_create(request):
    """직접 추가 때 보낸 값 검사 -> 틀린 칸이 있으면 모아서 한번에 400"""
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

# [수정] 보낸 칸만 검사 → 통과값 반환 / 틀리면 400
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

# [확정 · 무시] 보낸 savingIds 검사 -> ID 목록 / 틀리면 400
def clean_ids(request):
    data = request.data
    errors = {}

    # 표에 없는 칸 -> 400 (savingIds 말고는 보내면 안 됨)
    for key in data:
        if key != "saving_ids":
            errors[key] = ['이 칸은 보낼 수 없어요.']

    ids = data.get("saving_ids")
    if not isinstance(ids, list) or not ids: # list가 아니거나 비어있으면
        errors["saving_ids"] = ['선택할 수 없는 항목이에요.']
    else:
        try:
            ids = [uuid.UUID(str(x)) for x in ids] # 하나씩 id 모양인지 확인
        except ValueError:
            errors["saving_ids"] = ["선택할 수 없는 항목이에요."]

    if errors:
        raise ValidationError(errors)

    return list(set(ids)) # 같은 ID를 두 번 보내도 한 번만 (set()은 중복을 없앰)


# ---------------- 저장하기 (직접 추가 · 수정) ----------------

# [직접 추가] 검사 → 확정 상태로 저장 → 201
@idempotent() # 같은 요청 번호로 두 번 와도 한 번만 저장
def savings_create(request):
    """저축 직접 추가"""
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

# [수정] 후보 · 확정만 찾기 → 보낸 칸만 바꿔 저장 → 200
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


# ---------------- 주소 입구 (api_urls.py 에서 연결) ----------------

# /api/budgets/savings/        GET = 목록 보기 · POST = 직접 추가
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

# /api/budgets/savings/<id>/   PATCH = 수정 · DELETE = 중지 (중지 코드는 이 안에)
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
        # 저축 최소 금액이 줄었으니 승인 전 예산 초안 만료 (S-12)
        Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")
    return Response(status=204)



# ---------------- 후보 확정 · 무시 (아직 껍데기) ----------------

# /api/budgets/savings/confirm/   POST = 후보 확정 [저축이에요]
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

# /api/budgets/savings/ignore/    POST = 후보 무시 [아니에요]
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