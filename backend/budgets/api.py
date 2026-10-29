"""
budgets 앱 API: React 화면에 필요한 데이터를 JSON으로 준다.
- 홈 · 고정지출 · 정기 저축 · 예산: DB에 저장하고 읽는 실제 기능
- 예산 추천은 임시 비율 엔진, 쿠션 화면은 아직 mock.py 데이터를 사용한다
"""
import calendar
import copy
import uuid
from datetime import date, timedelta
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import APIException, ValidationError
from config.idempotency import idempotent
from .models import Budget, BudgetCycle, Category, SpendingAllocation
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Sum
from django.contrib.auth import get_user_model
from django.utils import timezone
from finance.codef import BANKS
from finance.models import BankAccount, Card, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense, RecurringSaving
from users.models import FinancialProfile

from . import mock
from .salary import current_salary_profile, salary_cycle_dates


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def home(request):
    """GET /api/home/ — 홈 화면 데이터 (docs/api/home.md, 계산은 home.py)"""
    from .home import home_data  # home.py 가 이 파일의 납부 예정 함수를 쓰므로 여기서 불러옴
    return Response(home_data(request.user, timezone.localdate()))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def budget(request):
    """GET /api/budgets/ — 카테고리 예산 화면 데이터"""
    from .overview import budget_data
    with transaction.atomic():
        lock_user(request.user)
        data = budget_data(request.user, timezone.localdate())
    return Response(data)


@api_view(["GET"])
def cushion(request):
    """GET /api/budgets/cushion/ — 머니 쿠션 화면 데이터"""
    return Response({"cushion": mock.CUSHION})


# ==================================================================
#  지킬 돈 API (docs/api/budgets.md, 계산 · 합계 조회는 protect.py)
# ==================================================================

class ProtectNotReady(APIException):
    status_code = 409
    default_code = "not_ready"
    default_detail = "내 정보 · 급여를 먼저 입력해 주세요."


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def protect(request):
    with transaction.atomic():
        lock_user(request.user)  # 홈 · 고정지출 · 정기 저축과 같은 잠금 순서
        today = timezone.localdate()
        profile = current_salary_profile(request.user, today)
        data = protect_json(request.user, profile, today)
        if request.method == "PUT":
            values = clean_protect(request, data)
            data["locks_released"] = save_protect(request.user, profile, values, data["cushion"]["target"])
            data["saved"] = protect_saved_json(profile, data["base"])
    return Response(data)


def clean_protect(request, state):
    """검사 1~6. 앞 단계가 실패하면 뒤 단계는 검사하지 않는다."""
    if state["missing"]:
        raise ProtectNotReady()
    if card_pending(state):
        raise ProtectNotReady(CARD_PENDING_MESSAGE)
    if state["blocked"]:
        raise ProtectNotReady("정기 저축이나 고정지출을 먼저 고쳐 주세요.")

    data = body_dict(request)
    fields = ("living_budget", "saving_floor", "cushion_monthly")
    errors = {key: ["이 칸은 보낼 수 없어요."] for key in data if key not in fields}
    for key in fields:
        if type(data.get(key)) is not int or data[key] < 0:
            errors[key] = ["금액은 0 이상의 정수로 입력해 주세요."]
    if errors:
        raise ValidationError(errors)
    if data["saving_floor"] < state["savings_min"]:
        raise ValidationError({
            "saving_floor": [f"저축 확인에서 고른 {state['savings_min']:,}원보다 적게 정할 수 없어요."],
        })
    if state["cushion"]["required"] and data["cushion_monthly"] == 0:
        raise ValidationError({"cushion_monthly": ["잔액이 월급의 3배보다 적어서 쿠션을 꼭 적립해야 해요."]})
    excess = sum(data[key] for key in fields) - state["base"]["available"]
    if excess > 0:
        raise ValidationError({
            "non_field_errors": [f"배분 가능한 금액보다 {excess:,}원 많아요. 입력 금액을 줄여 주세요."],
        })
    return {key: data[key] for key in fields}


def save_protect(user, profile, values, target):
    """검사 7. 호출 쪽의 트랜잭션 안에서 설정 변경과 초안 만료를 함께 저장한다."""
    fields = {
        "living_budget": values["living_budget"],
        "savings_floor": values["saving_floor"],
        "cushion_monthly": values["cushion_monthly"],
        "cushion_target": target,
    }
    changed = []
    for key, value in fields.items():
        if getattr(profile, key) != value:
            setattr(profile, key, value)
            changed.append(key)
    if changed:
        profile.save(update_fields=changed + ["updated_at"])
        Budget.objects.filter(user=user, status__in=["draft", "validated"]).update(status="expired")
    return release_stale_locks(user, profile) if "living_budget" in changed else []


def release_stale_locks(user, profile):
    """P-13: 생활비가 바뀌어 이번 주기 자물쇠가 새 생활비와 안 맞으면(추천이 막히면) 이번 주기 자물쇠를 모두 푼다.

    풀어 주지 않으면 초안이 없어서 자물쇠를 풀 곳이 없다. 사용자가 직접 바꾼 게 아니라서 바꾼 횟수(잔소리)는 늘리지 않는다.
    반환: 푼 카테고리 코드 (화면 순서)
    """
    from .overview import current_cycle
    from .recommend import locks_block_recommendation

    cycle = current_cycle(user, profile, timezone.localdate())
    if cycle is None or not locks_block_recommendation(user, cycle, profile.living_budget):
        return []
    return release_locks(user, cycle)


def release_locks(user, cycle):
    """이번 주기 자물쇠를 모두 푼다. 바꾼 횟수(잔소리)는 늘리지 않는다. 반환: 푼 카테고리 코드 (화면 순서)"""
    from .models import BudgetCategorySetting

    locked = BudgetCategorySetting.objects.filter(user=user, cycle=cycle, locked=True)
    codes = list(locked.order_by("category__display_order", "category_id").values_list("category_id", flat=True))
    locked.update(locked=False, locked_amount=None, updated_at=timezone.now())
    return codes


def release_locks_without_budget(user, cycle, living_budget):
    """P-13 ②: 이번 주기에 예산(초안 · 승인)이 하나도 없는데 자물쇠 때문에 추천이 막히면 자물쇠를 푼다.

    자물쇠는 예산 화면에서만 풀 수 있어서, 예산이 없으면 빠져나갈 길이 없다
    (예: 지난달 자물쇠가 넘어온 뒤 내 정보에서 최소 금액을 올림). 예산이 있으면 그 화면에서 풀 수 있으므로 그대로 둔다.
    """
    from .recommend import locks_block_recommendation

    if Budget.objects.filter(user=user, cycle=cycle, status__in=["validated", "approved"]).exists():
        return []
    if not locks_block_recommendation(user, cycle, living_budget):
        return []
    return release_locks(user, cycle)


def protect_saved_json(profile, base):
    if profile is None or any(value is None for value in (
        profile.living_budget, profile.savings_floor, profile.cushion_monthly,
    )):
        return None
    return {
        "living_budget": profile.living_budget,
        "saving_floor": profile.savings_floor,
        "cushion_monthly": profile.cushion_monthly,
        # 필수 정보가 지워지면 배분 기준도 모르므로 여윳돈은 계산할 수 없다.
        "spare": base["available"] - profile.living_budget - profile.savings_floor - profile.cushion_monthly if base else None,
        "updated_at": timezone.localtime(profile.updated_at).isoformat(),
    }


_COMPUTE = object()


def protect_json(user, profile, today, card_reserved=_COMPUTE):
    """GET · PUT 공통 응답. 주기 준비는 회원 잠금을 잡은 호출 쪽 트랜잭션에서 한다.

    card_reserved를 주면 카드 선차감을 새로 계산하지 않고 그 값을 쓴다 (승인 때 초안에 저장한 값).
    """
    from .home import balance_json, ensure_cycle
    from .protect import RATES, blocked, cushion_required, fixed_breakdown, fixed_sum, recommend, savings_min

    housing = profile.housing_type if profile else None
    income = profile.monthly_take_home_income if profile else None
    payday = profile.payday if profile else None
    missing = [key for key, value in (
        ("housingType", housing), ("takeHomeIncome", income), ("payday", payday),
    ) if value is None]
    minimum = savings_min(user)
    balance = balance_json(user)["total"]
    required = cushion_required(balance, income)
    base = recommendation = blocked_state = None
    pending_cards = []
    if not missing:
        start, next_payday, _ = salary_cycle_dates(profile, today)
        cycle = ensure_cycle(user, start, next_payday)
        breakdown = fixed_breakdown(user, cycle, card=card_reserved is _COMPUTE)
        if card_reserved is not _COMPUTE:
            breakdown["card_reserved"] = card_reserved
        pending_cards = breakdown["card_pending"]
        fixed = fixed_sum(breakdown)
        if fixed is None:
            # 카드 첫 청구서 선차감을 아직 계산할 수 없음 → 고정지출 합계 · 배분 기준도 모른다 (0원으로 치지 않음)
            blocked_state = {"reason": "card_bill_pending", "shortfall": None}
        else:
            available = income - fixed
            base = {"take_home_income": income, "fixed_total": fixed, "available": available}
            blocked_state = blocked(available, minimum)
            if blocked_state is None:
                recommendation = recommend(available, housing, minimum, cushion_required=required)
    return {
        "missing": missing,
        "housing_type": housing,
        "rates": RATES.get(housing),
        "base": base,
        "savings_min": minimum,
        "recommended": recommendation,
        "blocked": blocked_state,
        "saved": protect_saved_json(profile, base),
        "cushion": {"required": required, "target": income * 3 if income is not None else None, "balance_total": balance},
        "card_pending": pending_cards,
        "locks_released": [],  # PUT으로 저장할 때만 채운다 (P-13)
    }


CARD_PENDING_MESSAGE = "카드 청구서를 아직 확인하지 못했어요. 카드 내역을 다시 불러온 뒤 시도해 주세요."


def card_pending(state):
    return bool(state["blocked"]) and state["blocked"]["reason"] == "card_bill_pending"


def protect_ready(state):
    """저장된 지킬 돈이 지금 조건에서도 쓸 수 있는지 (추천 만들기 · 승인 공통)"""
    saved = state["saved"]
    if state["missing"] or state["blocked"] or saved is None:
        return False
    amounts = [saved[key] for key in ("living_budget", "saving_floor", "cushion_monthly")]
    return not (any(type(amount) is not int or amount < 0 for amount in amounts)
                or saved["saving_floor"] < state["savings_min"]
                or sum(amounts) > state["base"]["available"]
                # 잔액이 줄어 쿠션이 필수가 됐는데 저장된 쿠션이 0원이면 안 됨 (P-08)
                or (state["cushion"]["required"] and saved["cushion_monthly"] == 0))


# ==================================================================
#  추천 예산 API (엔진 · 입력 해시는 recommend.py, 작업 조회는 chat 담당)
# ==================================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent(required=True)
def recommend(request):
    from chat.models import AsyncTask
    from .recommend import budget_input_snapshot, hash_snapshot

    with transaction.atomic():
        lock_user(request.user)
        today = timezone.localdate()
        profile = current_salary_profile(request.user, today)
        state = protect_json(request.user, profile, today)  # home.ensure_cycle도 같은 잠금 안에서 재사용
        clean_recommend(request, state)
        pending = AsyncTask.objects.filter(
            user=request.user, task_type="budget_recommend", status__in=["queued", "running"],
        ).order_by("created_at", "pk").first()
        if pending:
            return Response(recommend_task_json(pending), status=202)

        cycle = active_cycle(request.user)
        released = release_locks_without_budget(request.user, cycle, profile.living_budget)
        snapshot = budget_input_snapshot(request.user, cycle, profile=profile, today=today)
        task = AsyncTask.objects.create(
            user=request.user, task_type="budget_recommend", request_key=request.headers["Idempotency-Key"],
            input_hash=hash_snapshot(snapshot), status="running", started_at=timezone.now(),
        )
        run_recommend(task, cycle, snapshot, state["saved"]["spare"], released)
    return Response(recommend_task_json(task), status=202)


def clean_recommend(request, state):
    if state["missing"] or state["saved"] is None:
        raise ProtectNotReady("지킬 돈을 먼저 정해 주세요.")
    if card_pending(state):
        raise ProtectNotReady(CARD_PENDING_MESSAGE)
    if not protect_ready(state):
        raise ProtectNotReady("지킬 돈을 다시 확인해 주세요.")
    data = body_dict(request)
    if data:
        raise ValidationError({key: ["이 칸은 보낼 수 없어요."] for key in data})


def run_recommend(task, cycle, snapshot, expected_spare, locks_released=()):
    from .recommend import EngineCheckFailed, check_result, infeasible, recommend_category_budgets

    try:
        # 엔진 검증 · 예산 · 항목 · 이전 초안 만료는 전부 성공하거나 전부 취소한다.
        # 실패 작업 자체는 바깥 트랜잭션에 남긴다.
        with transaction.atomic():
            etc = snapshot["etc"]
            etc_minimum = snapshot["minimums"].get("ETC", 0)
            if etc < etc_minimum:
                out = infeasible([{"type": "lock_below_minimum", "category": "ETC",
                                   "locked_amount": etc, "min_amount": etc_minimum}])
            elif etc > snapshot["saved"]["living_budget"]:
                out = infeasible([{"type": "minimums_over_living", "shortfall": etc - snapshot["saved"]["living_budget"]}])
            else:
                inp = snapshot["engine_input"]
                out = recommend_category_budgets(copy.deepcopy(inp))
                check_result(inp, out)
            budget = None
            if out["status"] == "proposed":
                budget = save_recommended_budget(task, cycle, snapshot, out, expected_spare)
            task.result = {
                "outcome": out["status"], "budget_id": str(budget.pk) if budget else None,
                "summary": out["summary"], "conflicts": out["conflicts"],
                "locks_released": list(locks_released),  # P-13 ②: 예산이 없어 막혀서 이번에 푼 자물쇠
            }
        task.status = "succeeded"  # infeasible도 조건을 확인한 정상 작업 결과
    except EngineCheckFailed:
        task.status = "failed"
        task.error_code = "engine_check_failed"
        task.result = None
    task.completed_at = timezone.now()
    task.save(update_fields=["status", "result", "error_code", "completed_at"])


def save_recommended_budget(task, cycle, snapshot, out, expected_spare):
    from .models import BudgetItem, Bucket
    from .recommend import CATEGORY_CODES, ENGINE_VERSION, POLICY_VERSION, EngineCheckFailed

    fixed, saved = snapshot["fixed"], snapshot["saved"]
    lines = [{"bucket": Bucket.FIXED, "category_id": code, "allocated_amount": amount}
             for code, amount in sorted(fixed["categories"].items()) if amount]
    lines += [{"bucket": Bucket.FIXED, "category_detail": detail, "allocated_amount": fixed[detail]}
              for detail in ("loan", "card_reserved") if fixed[detail]]
    lines += [
        {"bucket": Bucket.SAVINGS, "allocated_amount": saved["saving_floor"]},
        {"bucket": Bucket.CUSHION, "allocated_amount": saved["cushion_monthly"]},
    ]
    lines += [{"bucket": Bucket.LIVING, "category_id": code, "allocated_amount": out["allocations"][code]}
              for code in CATEGORY_CODES]
    lines.append({"bucket": Bucket.LIVING, "category_id": "ETC", "allocated_amount": snapshot["etc"]})
    if (any(type(line["allocated_amount"]) is not int or line["allocated_amount"] < 0 for line in lines)
            or snapshot["take_home_income"] - sum(line["allocated_amount"] for line in lines) != expected_spare):
        raise EngineCheckFailed("예산 항목 합계가 지킬 돈의 여윳돈과 맞지 않아요.")
    version = (Budget.objects.filter(user=task.user, cycle=cycle).aggregate(last=Max("version_no"))["last"] or 0) + 1
    budget = Budget.objects.create(
        user=task.user, cycle=cycle, task=task, version_no=version, status="validated",
        funding_amount=snapshot["take_home_income"], input_hash=task.input_hash, input_snapshot=snapshot,
        engine_version=ENGINE_VERSION, policy_version=POLICY_VERSION, validated_at=timezone.now(),
    )
    BudgetItem.objects.bulk_create([BudgetItem(user=task.user, budget=budget, **line) for line in lines])
    Budget.objects.filter(user=task.user, cycle=cycle, status__in=["draft", "validated"]).exclude(pk=budget.pk).update(status="expired")
    return budget


def recommend_task_json(task):
    return {"task": {"task_id": str(task.pk), "status": task.status}}


class BudgetExpired(APIException):
    status_code = 409
    default_code = "expired"
    default_detail = "조건이 바뀌어서 다시 추천받아야 해요."


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def budget_category(request, budget_id, code):
    from .models import BudgetCategorySetting, BudgetItem
    from .overview import LIVING_CODES, budget_nag, current_cycle

    with transaction.atomic():
        lock_user(request.user)
        today = timezone.localdate()
        profile = current_salary_profile(request.user, today)
        chosen = get_object_or_404(Budget.objects.select_for_update(), pk=budget_id, user=request.user)
        category = get_object_or_404(Category, pk=code, category_code__in=LIVING_CODES)
        cycle = current_cycle(request.user, profile, today)
        if cycle is None or chosen.cycle_id != cycle.pk or chosen.status not in ("validated", "approved"):
            raise ProtectNotReady("지난 예산은 바꿀 수 없어요.")
        data = body_dict(request)
        errors = {key: ["이 칸은 보낼 수 없어요."] for key in data if key != "locked"}
        if type(data.get("locked")) is not bool:
            errors["locked"] = ["자물쇠는 참 또는 거짓으로 보내 주세요."]
        if errors:
            raise ValidationError(errors)
        amount = None
        if data["locked"]:
            amount = get_object_or_404(BudgetItem, budget=chosen, user=request.user, bucket="living", category=category).allocated_amount
        setting, _ = BudgetCategorySetting.objects.get_or_create(user=request.user, cycle=cycle, category=category)
        if setting.locked != data["locked"] or setting.locked_amount != amount:
            setting.change_count += setting.locked != data["locked"]
            setting.locked = data["locked"]
            setting.locked_amount = amount
            setting.save(update_fields=["locked", "locked_amount", "change_count", "updated_at"])
        return Response({
            "category": category.pk, "category_name": category.display_name, "locked": setting.locked,
            "locked_amount": setting.locked_amount, "change_count": setting.change_count,
            "nag": budget_nag(request.user, cycle),
        })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent(required=True, commit_error_codes=("expired",))
def budget_approve(request, budget_id):
    from config.api import exception_handler
    from .overview import budget_data, current_cycle
    from .recommend import budget_input_hash

    with transaction.atomic():
        lock_user(request.user)
        today = timezone.localdate()
        profile = current_salary_profile(request.user, today)
        chosen = get_object_or_404(Budget.objects.select_for_update(), pk=budget_id, user=request.user)
        data = body_dict(request)
        if data:
            raise ValidationError({key: ["이 칸은 보낼 수 없어요."] for key in data})
        cycle = current_cycle(request.user, profile, today)
        this_cycle = cycle is not None and chosen.cycle_id == cycle.pk
        if chosen.status == "approved":
            # 이번 주기에 승인된 예산만 '이미 승인'으로 돌려준다. 지난 주기 승인 예산은 지난 예산이다.
            if this_cycle:
                return Response(budget_data(request.user, today, selected=chosen))
            raise ProtectNotReady("지난 예산은 승인할 수 없어요.")
        if chosen.status == "expired":
            raise BudgetExpired()
        if chosen.status != "validated":
            raise ProtectNotReady("지난 예산은 승인할 수 없어요.")
        # 카드 선차감은 초안을 만들 때 저장한 값을 쓴다 (카드로 쓸 때마다 바뀌는 값이라 다시 계산하면 초안이 계속 만료됨)
        frozen = ((chosen.input_snapshot or {}).get("fixed") or {}).get("card_reserved")
        state = protect_json(request.user, profile, today, card_reserved=frozen) if this_cycle else None
        if (not this_cycle or not protect_ready(state)
                or budget_input_hash(request.user, cycle) != chosen.input_hash):
            chosen.status = "expired"
            chosen.save(update_fields=["status"])
            # raise하면 이 트랜잭션도 롤백된다. 공통 에러 모양으로 반환하고 위 옵션으로 만료만 보존한다.
            return exception_handler(BudgetExpired(), {"request": request})
        Budget.objects.filter(user=request.user, cycle=cycle, status="approved").update(status="superseded")
        chosen.status = "approved"
        chosen.approved_at = timezone.now()
        chosen.approval_key = request.headers["Idempotency-Key"]
        chosen.save(update_fields=["status", "approved_at", "approval_key"])
        # auth.onboardingCompleted는 approved_at이 있는 예산의 존재 여부로 계산한다.
        return Response(budget_data(request.user, today, selected=chosen))

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

    cycle = active_cycle(request.user)
    today = timezone.localdate()
    items = []
    for e in list(suggested) + list(confirmed):
        item = fixed_json(e)
        item["evidence_suggestion"] = evidence_suggestion(e, cycle, today)  # 고를 수 있는 결제 · 출금 제안
        item["recent_dues"] = recent_dues_json(e, cycle)                     # 지난 · 이번 회차 납부 기록 (회차 지정용)
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
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        # 잠그고 찾기 (수정 · 중지가 동시에 와도 하나씩 처리) — 확정, 중지만, 없으면 404
        expense = get_object_or_404(
            RecurringExpense.objects.select_for_update(), # 고정지출 테이블
            pk = recurring_id,
            user = request.user,
            status__in = ["confirmed", "stopped"],
        )

        if expense.status == "confirmed":
            expense.status = "stopped"
            expense.save(update_fields=["status"])

            # 이번 주기부터 안 낸 납부 예정만 취소 (이미 낸 · 일부 낸 회차는 그대로)
            cancel_dues(open_dues(expense, current_since(request.user)))

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


# 내 고정지출 여러 개를 ID 순서대로 잠그고 가져오기 (확정 · 무시가 같은 순서로 잠가서 서로 막히지 않음)
def lock_expenses(user, ids, statuses):
    return list(RecurringExpense.objects.select_for_update().filter(
        user = user,
        pk__in = ids,
        status__in = statuses,
    ).order_by("pk"))


# 고정지출 후보 확정 [맞아요] — 후보만 확정, 못 고르는 ID가 섞이면 전부 취소
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def fixed_confirm(request):
    ids = clean_ids(request, "recurring_ids")  # 검사 (칸 이름만 저축과 다름)

    with transaction.atomic():
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        # 먼저 다 잠그고 찾기 - 내 것, 후보나 이미 확정인 것만
        found = lock_expenses(request.user, ids, ["suggested", "confirmed"])

        # 개수 비교 - 하나라도 못 찾았으면 아무것도 안 바꾸고 400
        if len(found) != len(ids):
            raise ValidationError({"recurring_ids": ["선택할 수 없는 항목이에요."]})

        # 후보만 확정으로 (이미 확정은 그대로 = 성공)
        cycle = active_cycle(request.user)
        today = timezone.localdate()
        changed = False
        for e in found:
            if e.status == "suggested":
                e.status = "confirmed"
                e.confirmed_at = timezone.now()
                e.save(update_fields=["status", "confirmed_at"])
                ensure_current_due(e, cycle, today)  # 이번 주기 납부 예정 1건
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
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        found = lock_expenses(request.user, ids, ["suggested", "ignored"])  # 후보, 이미 무시
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
            evidence = tx_brief(t)

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
FIXED_FIELDS = {"name", "amount", "payment_day", "payment_account_id", "payment_card_id", "category", "evidence_transaction_id",
                "evidence_due_id"}

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
    data = body_dict(request)  # [] · 42 · "hello" → 400
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

    # 근거 거래 (선택) — 고른 카드 · 계좌의 내 출금 하나. 결제 수단 검사가 통과했을 때만 봄
    values["evidence_tx"] = None
    tx_value = data.get("evidence_transaction_id")
    method_ok = not any(k in errors for k in ("payment_account_id", "payment_card_id", "non_field_errors"))
    if tx_value is not None and method_ok:
        tx = find_evidence(request.user, tx_value, values["payment_account"], values["payment_card"])
        if tx is None:
            errors["evidence_transaction_id"] = ["선택할 수 없는 거래예요."]
        else:
            values["evidence_tx"] = tx

    if errors:
        raise ValidationError(errors)
    return values

# 고정지출 수정 값 검사 (보낸 칸만) → 통과값 / 틀리면 400
#   근거 거래는 values["evidence_tx"] 로 돌려줌: 칸 없음 = 유지, None = 비우기, 거래 = 바꾸기
def clean_fixed_update(request, expense):
    data = body_dict(request)  # 42 · "hello" 등 → 400
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

    # 저장 뒤 결제 수단 (보낸 칸이 없으면 지금 값)
    final_account = values.get("payment_account", expense.payment_account)
    final_card = values.get("payment_card", expense.payment_card)
    method_ok = not any(k in errors for k in ("payment_account_id", "payment_card_id"))

    # 계좌, 카드가 둘 다 선택되어 있으면
    if method_ok and final_account and final_card:
        errors["non_field_errors"] = ["계좌와 카드 중 하나만 골라 주세요."]
        method_ok = False

    # 근거 거래 — null 이면 비우기, ID 면 저장 뒤 결제 수단의 내 출금인지 검사
    if "evidence_transaction_id" in data:
        value = data["evidence_transaction_id"]
        if value is None:
            values["evidence_tx"] = None
        elif method_ok:
            tx = find_evidence(request.user, value, final_account, final_card, expense)
            if tx is None:
                errors["evidence_transaction_id"] = ["선택할 수 없는 거래예요."]
            else:
                values["evidence_tx"] = tx

    # 근거 거래를 붙이거나 풀 회차 (선택) — 목록 recentDues 의 회차 하나. 근거 거래 칸과 같이 보내야 함
    if "evidence_due_id" in data:
        if "evidence_transaction_id" not in data:
            errors["evidence_due_id"] = ["근거 거래와 같이 보내 주세요."]
        elif method_ok:
            due = find_recent_due(expense, data["evidence_due_id"], final_account)
            if due is None:
                errors["evidence_due_id"] = ["선택할 수 없는 회차예요."]
            else:
                values["evidence_due"] = due

    if errors:
        raise ValidationError(errors)
    return values

# 고정지출 직접 추가 저장 → 바로 확정 → 201
@idempotent() # 같은 요청 번호로 두 번 와도 한 번만 저장
def fixed_create(request):
    with transaction.atomic():
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        values = clean_fixed_create(request)  # 근거 거래를 잠그면서 검사하므로 묶음 안에서
        evidence_tx = values.pop("evidence_tx")

        expense = RecurringExpense.objects.create(
            user  = request.user,
            **values,
            status = "confirmed",
            source_type="manual",
            confirmed_at=timezone.now(),
        )

        cycle = active_cycle(request.user)
        today = timezone.localdate()
        if evidence_tx is not None:
            link_evidence(expense, evidence_tx, cycle, today)  # 근거 거래 연결 (계좌면 납부 예정과 짝)
        ensure_current_due(expense, cycle, today)               # 이번 주기 납부 예정 1건

        # 예산 초안 만료
        Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

    return Response(fixed_json(expense), status = 201)

# 고정지출 수정 저장 (보낸 칸만) → 이번 주기 안 낸 납부 예정도 맞춤 → 200
def fixed_update(request, recurring_id):
    with transaction.atomic():
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        # 잠그고 찾기 — 중지가 동시에 와도 기다렸다가 다시 확인 (중지됐으면 404)
        expense = get_object_or_404(
            RecurringExpense.objects.select_for_update(),
            pk = recurring_id,
            user = request.user,
            status__in = ["suggested", "confirmed"],
        )

        values = clean_fixed_update(request, expense)
        evidence_sent = "evidence_tx" in values
        evidence_tx = values.pop("evidence_tx", None)
        evidence_due = values.pop("evidence_due", None)   # 회차를 지정했으면 그 회차만 바꿈

        # 바뀌기 전에 뭐가 바뀌는지 기억
        amount_changed = "amount" in values and values["amount"] != expense.amount
        method_changed = (
            ("payment_account" in values and values["payment_account"] != expense.payment_account)
            or ("payment_card" in values and values["payment_card"] != expense.payment_card)
        )

        for key, value in values.items():
            setattr(expense, key, value)
        fields = list(values)  # 보낸 칸만 저장 (status 는 건드리지 않음)

        cycle = active_cycle(request.user)
        today = timezone.localdate()

        # 근거 거래: 이번 주기 연결만 고치고 지난 주기 기록은 그대로 (회차를 지정하면 아래에서 그 회차만)
        if evidence_sent and evidence_due is None:
            if evidence_tx is None:
                unlink_current(expense, cycle, today)    # 비우기: 연결 풀고
                expense.evidence = []                    # 가맹점 기억도 지움
                fields.append("evidence")
            elif str(evidence_tx.pk) not in id_set(expense.evidence):
                expense.evidence = unlink_current(expense, cycle, today)  # 바꾸기: 기존 연결 풀고 (새 거래는 아래에서)
                fields.append("evidence")
        elif method_changed:
            expense.evidence = []  # 결제 수단을 바꾸면 가맹점 기억은 비움 (이미 낸 기록은 그대로)
            fields.append("evidence")

        if fields:
            expense.save(update_fields=fields)

        if expense.status == "confirmed":
            # 안 낸 회차의 금액 · 날짜 · 계좌 맞춤, 필요하면 취소 · 새로 만들기 (사용자가 고른 거래는 아래에서 고른 회차에 붙임)
            sync_dues(expense, cycle, today, user_pick=evidence_tx is not None or evidence_due is not None)

            # 고정지출 합계가 바뀌면 승인 전 예산 초안 만료
            if amount_changed:
                Budget.objects.filter(user=request.user, status__in=["draft", "validated"]).update(status="expired")

        if evidence_due is not None:
            relink_due(expense, evidence_due, evidence_tx, cycle, today)   # 지정한 회차만 바꾸기 · 비우기
        elif evidence_tx is not None:
            link_evidence(expense, evidence_tx, cycle, today)  # 새 근거 거래 연결

    return Response(fixed_json(expense))


# ---------------- 고정지출: 납부 예정 · 근거 거래 ----------------
#  납부 예정(PaymentDue)은 이번 주기 1건씩 (C-15). 계좌 · 결제 수단 모름 + 출금일이 있는 확정 고정지출만.
#  '안 낸 회차' = 아직 짝지은 출금이 없는 unpaid · needs_review. 이미 낸(paid) · 일부 낸(partial) 회차는 건드리지 않는다.

NOT_PAID = ["unpaid", "needs_review"]

# 회원 잠금 — 거래 · 고정지출 · 저축 · 납부 예정을 바꾸는 모든 처리(홈 · 고정지출 · 저축 API, 거래 저장 뒤 처리 · 재시도)는
#   같은 트랜잭션에서 이걸 **가장 먼저** 부른다. 한 회원의 처리가 한 번에 하나씩 돌아서, 안에서 거래를 잠그는 순서가 달라도
#   서로 기다리다 멈추지(교착) 않는다. no_key: 회원을 참조하는 행 추가(납부 예정 · 납부 짝 등)는 막지 않음
def lock_user(user):
    get_user_model().objects.select_for_update(no_key=True).filter(pk=getattr(user, "pk", user)).first()
SKIP_AUTO = "skip_auto_match"  # 거래 field_states: 사용자가 연결을 푼 회차(occurrence_key) 목록 → 그 회차에는 자동으로 다시 붙이지 않음
ORIGINAL_TYPE = "original_transaction_type"  # 거래 목록 API와 같은 '원래 이체였음' 표시 (finance/api.py, 10/24 AA⑥)
CONVERTED_BY_FIXED = "converted_by_fixed"    # 고정지출 연결이 이체를 소비로 바꿨다는 표시 (풀 때 되돌리려고)
FIXED_TX_TYPES = ["spending", "transfer"]  # 고정지출 근거가 될 수 있는 거래 종류 (소비 · 남에게 보낸 이체)

# 진행 중인 급여 주기 (온보딩 전이면 None)
def active_cycle(user):
    from .salary import activate_salary_if_due
    activate_salary_if_due(user, timezone.localdate())
    return BudgetCycle.objects.filter(user=user, status="active").first()

# 이번 주기 시작일 — 아직 주기가 없으면(온보딩 전) 오늘
def current_since(user):
    cycle = active_cycle(user)
    return cycle.starts_on if cycle else timezone.localdate()

# 그 달의 출금일 날짜 (그 달에 없는 날은 말일: 2월 31일 → 2월 28일)
def day_in_month(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))

# 주기(시작일 ~ 다음 시작일 전날) 안에서 출금일 날짜. 예: 10/25 ~ 11/24 주기, 출금일 5일 → 11/5
def day_in_cycle(cycle, day):
    year, month = cycle.starts_on.year, cycle.starts_on.month
    for _ in range(2):
        d = day_in_month(year, month, day)
        if cycle.starts_on <= d < cycle.ends_before:
            return d
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None

# 거래 ID 목록 → 모양을 맞춘 문자열 집합 (대시 유무 등이 달라도 같은 ID로 봄)
def id_set(ids):
    result = set()
    for value in ids or []:
        try:
            result.add(str(uuid.UUID(str(value))))
        except ValueError:
            pass
    return result

# 다른 고정지출의 근거 거래로 이미 쓰인 거래 ID
def evidence_of_others(user, expense=None):
    others = RecurringExpense.objects.filter(user=user)
    if expense is not None:
        others = others.exclude(pk=expense.pk)
    taken = set()
    for ids in others.values_list("evidence", flat=True):
        taken |= id_set(ids)
    return taken

# 보낸 거래 ID → 근거로 고를 수 있는 거래 (못 고르면 None)
#   고른 카드 · 계좌의 내 출금(취소 안 됨)이고, 다른 고정지출 · 다른 납부 예정에 연결 안 된 것
def find_evidence(user, value, account, card, expense=None):
    if account is None and card is None:  # 결제 수단 모름 → 고를 수 없음
        return None
    try:
        tx_id = uuid.UUID(str(value))
    except ValueError:
        return None

    found = FinancialTransaction.objects.select_for_update().filter(  # 같은 거래를 두 고정지출이 동시에 못 고르게 잠금
        user = user,
        pk = tx_id,
        direction = "out",
        is_canceled = False,
        transaction_type__in = FIXED_TX_TYPES,
    )
    found = found.filter(card=card) if card is not None else found.filter(account=account)
    tx = found.first()
    if tx is None or not claimable(tx, expense):
        return None
    return tx

# 이 거래를 이 고정지출에 연결해도 되는지 (잠근 뒤 다시 확인용 — 잠그는 동안 다른 요청이 먼저 연결했을 수 있음)
#   다른 고정지출의 근거 거래면 X · 다른 납부 예정(다른 고정지출 · 카드 청구서 등)과 짝지어진 출금이면 X
#   같은 고정지출에 이미 연결된 건 OK
def claimable(tx, expense=None):
    if str(tx.pk) in evidence_of_others(tx.user_id, expense):
        return False
    matched = PaymentMatch.objects.filter(transaction=tx)
    if expense is not None:
        matched = matched.exclude(due__recurring=expense)
    return not matched.exists()

# 맞는 출금 · 결제 후보 (잠그지 않음) — 금액 ±10%, 날짜 start ~ end, 아직 어디에도 연결 안 된 것
#   출금일과 날짜가 가까운 순 → 금액 차이가 작은 순
def matching_payments(expense, due_date, start, end):
    low = (expense.amount * 9 + 9) // 10   # 90% (올림)
    high = expense.amount * 11 // 10       # 110% (내림)
    found = FinancialTransaction.objects.filter(
        user = expense.user,
        direction = "out",
        is_canceled = False,
        transaction_type__in = FIXED_TX_TYPES,
        amount__gte = low,
        amount__lte = high,
        occurred_on__gte = start,
        occurred_on__lte = end,
    ).exclude(pk__in=PaymentMatch.objects.values("transaction"))
    if expense.payment_card_id is not None:
        found = found.filter(card_id=expense.payment_card_id)
    elif expense.payment_account_id is not None:
        found = found.filter(account_id=expense.payment_account_id)
    else:
        return []  # 결제 수단 모름 → 찾을 곳이 없음
    taken = evidence_of_others(expense.user_id, expense) | id_set(expense.evidence)
    found = [t for t in found if str(t.pk) not in taken]
    found.sort(key=lambda t: (abs((t.occurred_on - due_date).days), abs(t.amount - expense.amount), str(t.pk)))
    return found

# 출금일이 지난 회차에 맞는 출금 (자동 연결용) — 같은 계좌, 날짜 ±3일, 오늘까지 (미래 날짜 거래는 아직 안 나간 돈)
#   key: 그 회차의 occurrence_key — 사용자가 그 회차에서 연결을 푼 거래는 빼고 찾음
#   찾은 거래를 ID 순서로 잠그고, 잠근 뒤 아직 연결 가능한지 다시 확인 (동시에 등록한 다른 고정지출이 먼저 가져갔을 수 있음)
def find_withdrawals(expense, due_date, today, key=None):
    if expense.payment_account_id is None:
        return []
    end = min(due_date + timedelta(days=3), today)
    found = matching_payments(expense, due_date, due_date - timedelta(days=3), end)
    if not found:
        return []
    # 잠근 뒤 최신 값으로 다시 확인하고, 잠근(최신) 거래를 돌려줌 — 뒤에서 이 거래를 저장해도 다른 요청의 변경을 덮어쓰지 않게
    locked = {t.pk: t for t in FinancialTransaction.objects.select_for_update().filter(pk__in=[t.pk for t in found]).order_by("pk")}
    result = []
    for t in found:
        fresh = locked.get(t.pk)
        if fresh is None or not claimable(fresh, expense):
            continue
        if key is not None and key in (fresh.field_states or {}).get(SKIP_AUTO, []):
            continue  # 사용자가 이 회차에서 연결을 푼 거래
        result.append(fresh)
    return result

# 실제 지난 급여 주기 시작일 = 이번 주기 시작일의 한 달 전 월급날 (저장된 마지막 주기가 아님 — 한 달을 건너뛰고 접속해도 맞게)
#   예: 급여일 25일, 이번 주기 11/25 시작 → 10/25 (9/25 주기에 마지막으로 접속했어도)
def previous_start(cycle):
    profile = FinancialProfile.objects.filter(user_id=cycle.user_id).first()
    if profile and profile.payday_changes_on == cycle.starts_on:
        return profile.payday_previous_start
    payday = (profile.payday if profile else None) or cycle.starts_on.day
    year, month = cycle.starts_on.year, cycle.starts_on.month
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return day_in_month(year, month, payday)

# 지난 주기의 '미납 확인 필요' 회차 — 홈 '꼭 나갈 돈'에 남아 있는 것과 같음 (출금일 +3일 지남 · 짝 없음). 오래된 것부터
def late_dues(expense, cycle, today, lock=False):
    since = previous_start(cycle) if cycle is not None else None
    if since is None:
        return []
    dues = PaymentDue.objects.filter(
        recurring=expense, obligation_state="active", payment_state__in=NOT_PAID,
        due_date__gte=since, due_date__lt=min(cycle.starts_on, today - timedelta(days=3)),
    ).exclude(pk__in=PaymentMatch.objects.values("due")).order_by("due_date")
    if lock:
        dues = dues.select_for_update()
    return list(dues)

# 목록의 근거 거래 제안 (evidenceSuggestion) — 확정 · 출금일 있는 고정지출만, 최대 5건
#   카드: 근거 거래가 없을 때 이번 회차 출금일 ±3일 결제가 1건 이상
#   계좌: 이번 회차가 아직 안 냈고 출금일 ±3일 출금이 2건 이상 (1건이면 자동 연결 대상이라 묻지 않음)
#         출금일 +3일이 지난 회차(확인 필요)는 늦게 낸 출금도 고를 수 있게 출금일 −3일 ~ 오늘, 1건 이상
def evidence_suggestion(expense, cycle, today):
    if expense.status != "confirmed" or expense.payment_day is None:
        return None
    if expense.payment_card_id is None and expense.payment_account_id is None:
        return None
    if cycle is not None:
        due_date = day_in_cycle(cycle, expense.payment_day)
    else:
        due_date = day_in_month(today.year, today.month, expense.payment_day)
    if due_date is None:
        return None

    start, end, need = due_date - timedelta(days=3), due_date + timedelta(days=3), 1
    due_id = None
    if expense.payment_card_id is not None:
        if expense.evidence:
            return None  # 기억한 가맹점이 있으면 서버가 맞추고 묻지 않음
    elif late_dues(expense, cycle, today):
        # 지난 주기 미납 회차가 남아 있으면 그 회차부터: 출금일 −3일 ~ 오늘 (늦게 낸 출금)
        # 저장(link_evidence → evidence_target)도 같은 회차에 붙인다
        target = late_dues(expense, cycle, today)[0]
        due_date, due_id = target.due_date, target.pk
        start, end = due_date - timedelta(days=3), today
    else:
        due = PaymentDue.objects.filter(
            recurring=expense, obligation_state="active", due_date=due_date,
        ).first()
        if due is not None and (due.payment_state not in NOT_PAID or due.matches.exists()):
            return None  # 이미 낸 · 일부 낸 회차
        due_id = due.pk if due is not None else None
        if today > due_date + timedelta(days=3):
            end = today  # 늦게 낸 출금도 고를 수 있게
        else:
            need = 2

    found = matching_payments(expense, due_date, start, min(end, today))  # 미래 날짜 거래는 후보 아님
    if len(found) < need:
        return None
    return {
        "count": len(found),
        "candidates": [tx_brief(t) for t in found[:5]],
        "due_id": str(due_id) if due_id else None,   # 이 후보로 갚을 회차 (카드 · 아직 회차가 없으면 null)
        "due_date": due_date.isoformat(),
    }

# 거래 → 화면에 보여 줄 짧은 정보 (카드 = 가맹점명, 계좌 = 적요)
def tx_brief(t):
    return {
        "transaction_id" : str(t.pk),
        "merchant_name" : t.merchant_name or t.source_description,
        "date" : t.occurred_on.isoformat(),
        "amount" : t.amount,
    }

# 납부 상태 다시 계산 — 짝 합계 ≥ 예정 금액 paid, 일부 partial, 없으면 출금일 전 unpaid · 지남 needs_review
def refresh_due_state(due, today):
    summary = due.matches.aggregate(total=Sum("matched_amount"), last=Max("matched_at"))
    paid = summary["total"] or 0
    if paid >= due.confirmed_amount:
        state = "paid"
    elif paid > 0:
        state = "partial"
    elif today <= due.due_date:
        state = "unpaid"
    else:
        state = "needs_review"
    due.paid_amount = paid
    due.payment_state = state
    due.matched_at = summary["last"]
    due.save(update_fields=["paid_amount", "payment_state", "matched_at"])

# 그 거래의 재원 바꾸기 (예산이 있어 배분 기록이 있을 때만) — 연결: 생활비 → 고정비, 풀기: 고정비 → 생활비
def move_bucket(tx, from_bucket, to_bucket):
    SpendingAllocation.objects.filter(transaction=tx, funding_bucket=from_bucket).update(funding_bucket=to_bucket)

# 납부 예정 ↔ 출금 짝짓기 → 상태 다시 계산
def match_withdrawal(due, tx, today):
    amount = tx.amount - (tx.canceled_amount or 0)
    if amount > 0:
        PaymentMatch.objects.get_or_create(
            due = due,
            transaction = tx,
            defaults = {"user": due.user, "matched_amount": amount},
        )
    refresh_due_state(due, today)

# 이번 주기 '안 낸 회차' (잠금) — 이미 낸 · 일부 낸 회차와 짝이 있는 회차는 빼고
def open_dues(expense, since):
    return list(PaymentDue.objects.select_for_update().filter(
        recurring = expense,
        obligation_state = "active",
        payment_state__in = NOT_PAID,
        due_date__gte = since,
    ).exclude(pk__in=PaymentMatch.objects.values("due")))

# 납부 예정 취소 (지우지 않고 canceled 로 남김)
def cancel_dues(dues):
    PaymentDue.objects.filter(pk__in=[d.pk for d in dues]).update(
        obligation_state="canceled", canceled_at=timezone.now(),
    )

# 이번 주기 납부 예정 1건 — 있으면 그대로, 없으면 만들기 (만들 대상이 아니면 None)
#   출금일이 이미 지났으면 맞는 출금이 있을 때만 만든다: 1건이면 "냄"으로 연결, 여러 건이면 확인 필요([고르기])
#   withdrawal: 사용자가 근거로 고른 출금 (이때는 지났어도 만든다)
#   create_past: 홈을 열 때는 출금일이 지났어도 만든다 (home.md '주기 중간에 홈을 처음 열면')
#   user_pick: 같은 요청에서 사용자가 근거 거래를 고름 → 자동으로 찾아 붙이지 않음 (고른 거래를 고른 회차에 붙임)
def ensure_current_due(expense, cycle, today, withdrawal=None, create_past=False, user_pick=False):
    if (expense.status != "confirmed" or cycle is None
            or expense.payment_card_id is not None or expense.payment_day is None):
        return None
    from .due_periods import monthly_due, occurrence_cycles, occurrence_key
    periods = occurrence_cycles(expense.user_id, cycle)
    if periods != [cycle]:
        dues = [ensure_current_due(expense, part, today, withdrawal=withdrawal,
                                   create_past=create_past, user_pick=user_pick) for part in periods]
        return next((due for due in dues if due is not None), None)
    due_date = day_in_cycle(cycle, expense.payment_day)
    if due_date is None:
        return None

    key = occurrence_key("recurring", expense.pk, cycle, due_date)
    locked = PaymentDue.objects.select_for_update().filter(user=expense.user, recurring=expense)
    due = (monthly_due(locked, due_date) if getattr(cycle, "monthly_occurrence", False)
           else locked.filter(occurrence_key=key).first())
    if due is not None:
        key = due.occurrence_key
    if due is not None and due.obligation_state == "active":
        return due
    if due is not None and (due.matches.exists() or due.paid_amount > 0 or due.payment_state in ("partial", "paid")):
        return None  # 취소됐지만 낸 기록이 있는 회차는 건드리지 않음

    found = []
    if withdrawal is None and not user_pick and due_date < today:
        found = find_withdrawals(expense, due_date, today, key)
        if not found and not create_past:
            return None

    fields = {
        "due_date": due_date,
        "confirmed_amount": expense.amount,
        "payment_account": expense.payment_account,
        "obligation_state": "active",
        "canceled_at": None,
        "paid_amount": 0,
        "payment_state": "unpaid",
        "matched_at": None,
    }
    if due is not None:  # 취소했던 회차를 다시 살림 (카드 → 계좌로 되돌린 경우 등)
        for name, value in fields.items():
            setattr(due, name, value)
        due.save(update_fields=list(fields))
    else:
        try:
            with transaction.atomic():  # 다른 요청이 같은 회차를 먼저 만들면 UNIQUE 에 걸림 → 그걸 씀
                due = PaymentDue.objects.create(
                    user = expense.user,
                    obligation_type = "recurring",
                    recurring = expense,
                    occurrence_key = key,
                    source_type = "manual" if expense.source_type == "manual" else "estimated",
                    confirmation_state = "confirmed",
                    **fields,
                )
        except IntegrityError:
            return PaymentDue.objects.select_for_update().get(user=expense.user, occurrence_key=key)

    if len(found) == 1:
        link_evidence(expense, found[0], cycle, today, source="rule", due=due)  # 맞는 출금 1건 → 이 회차에 자동 연결
    elif found or not create_past:
        refresh_due_state(due, today)                   # 0건(미래) unpaid · 여러 건 needs_review
    # 홈이 지난 회차를 새로 만들고 출금이 0건이면 unpaid 로 두고, 출금일 +3일 규칙은 홈이 적용
    return due

# 이번 회차가 아직 안 냈으면 맞는 출금(출금일 ±3일, 같은 계좌)을 찾아 1건이면 자동 연결 (홈을 열 때)
#   여러 건이면 자동으로 고르지 않음 → 목록의 [고르기]. 지난 주기 회차는 늦게 낸 출금이라 자동 연결하지 않음
def match_current_due(expense, cycle, today):
    if expense.payment_account_id is None or cycle is None or expense.payment_day is None:
        return
    from .due_periods import occurrence_cycles
    periods = occurrence_cycles(expense.user_id, cycle)
    if periods != [cycle]:
        for part in periods:
            match_current_due(expense, part, today)
        return
    due_date = day_in_cycle(cycle, expense.payment_day)
    if due_date is None or today < due_date - timedelta(days=3):
        return
    due = PaymentDue.objects.select_for_update().filter(
        recurring=expense, obligation_state="active", due_date=due_date, payment_state__in=NOT_PAID,
    ).exclude(pk__in=PaymentMatch.objects.values("due")).first()
    if due is None:
        return
    found = find_withdrawals(expense, due_date, today, due.occurrence_key)
    if len(found) == 1:
        link_evidence(expense, found[0], cycle, today, source="rule", due=due)

# 근거 거래 연결 — evidence 에 더하고(가맹점 기억), 고정비로 셈. 계좌 출금이면 회차 1개와 짝
#   due: 붙일 회차. 자동 연결은 그 회차를 넘기고, 사용자가 고른 거래는 evidence_target 으로 정함
#   이미 납부로 연결된 거래면 아무것도 하지 않음 (같은 요청을 다시 보내도 결과가 같고, 한 출금이 두 회차를 갚지 않게)
def link_evidence(expense, tx, cycle, today, source="user", due=None):
    if tx.account_id is not None and PaymentMatch.objects.filter(transaction=tx).exists():
        return
    if str(tx.pk) not in id_set(expense.evidence):
        expense.evidence = [*(expense.evidence or []), str(tx.pk)]
        expense.save(update_fields=["evidence"])
    mark_fixed(tx, expense, source)
    move_bucket(tx, "living", "fixed")
    if tx.account_id is not None:
        if due is None:
            due = evidence_target(expense, cycle, today) or ensure_current_due(expense, cycle, today, withdrawal=tx)
        # 그 회차의 후보 기간(출금일 −3일 ~ 오늘)에 나간 출금만 납부로 셈. 그 밖이면 가맹점 기억으로만 남김
        if due is not None and tx.occurred_on <= today:  # 미래 날짜 출금은 아직 안 나간 돈
            match_withdrawal(due, tx, today)

# 목록의 recentDues 범위: 지난 주기 시작일 ~ 이번 주기 끝 (홈 '꼭 나갈 돈'과 같은 범위)
def recent_dues(expense, cycle):
    if cycle is None or expense.payment_account_id is None:
        return PaymentDue.objects.none()
    since = previous_start(cycle) or cycle.starts_on
    return PaymentDue.objects.filter(
        recurring=expense, obligation_state="active", due_date__gte=since, due_date__lt=cycle.ends_before,
    ).order_by("due_date")

# 회차별 납부 기록 → 응답 (화면이 회차를 골라 근거 거래를 바꾸거나 비울 때 dueId 를 씀)
def recent_dues_json(expense, cycle):
    result = []
    for due in recent_dues(expense, cycle).prefetch_related("matches__transaction"):
        result.append({
            "due_id": str(due.pk),
            "due_date": due.due_date.isoformat(),
            "payment_state": due.payment_state,
            "transactions": [tx_brief(m.transaction) for m in due.matches.all()],
        })
    return result

# 보낸 회차 ID → 고를 수 있는 회차 (못 고르면 None). 계좌로 내는 이 고정지출의 recentDues 회차만
def find_recent_due(expense, value, account):
    try:
        due_id = uuid.UUID(str(value))
    except ValueError:
        return None
    if account is None or expense.payment_account_id is None:
        return None
    return recent_dues(expense, active_cycle(expense.user)).select_for_update().filter(pk=due_id).first()

# 지정한 회차만 근거 거래 바꾸기 · 비우기 — 다른 회차의 납부 기록은 그대로
#   tx 가 있으면: 그 회차의 다른 연결을 풀고 tx 를 붙임 (tx 가 다른 회차에 붙어 있었으면 거기서 옮김)
#   tx 가 None 이면: 그 회차의 연결만 풂 (그 회차에 자동으로 다시 붙지 않게 표시)
def relink_due(expense, due, tx, cycle, today):
    old = due.matches.exclude(transaction=tx) if tx is not None else due.matches.all()
    old_ids = list(old.values_list("pk", flat=True))
    # 풀 거래를 ID 순서로 잠그고 최신 값으로 다시 읽음 (다른 요청이 저장한 표시를 옛 값으로 덮어쓰지 않게)
    release = list(FinancialTransaction.objects.select_for_update().filter(
        pk__in=PaymentMatch.objects.filter(pk__in=old_ids).values("transaction"),
    ).order_by("pk"))
    PaymentMatch.objects.filter(pk__in=old_ids).delete()
    for released in release:
        unmark_fixed(released)
        move_bucket(released, "fixed", "living")
        states = dict(released.field_states or {})
        states[SKIP_AUTO] = sorted(set(states.get(SKIP_AUTO, [])) | {due.occurrence_key})
        released.field_states = states
        released.save(update_fields=["field_states", "updated_at"])
    released_ids = {str(t.pk) for t in release}
    expense.evidence = [v for v in (expense.evidence or []) if not (id_set([v]) & released_ids)]
    expense.save(update_fields=["evidence"])

    if tx is not None:
        moved_from = list(PaymentDue.objects.select_for_update().filter(
            pk__in=PaymentMatch.objects.filter(transaction=tx).values("due"),
        ).exclude(pk=due.pk))
        PaymentMatch.objects.filter(transaction=tx).exclude(due=due).delete()   # 다른 회차에 붙어 있었으면 옮김
        for other in moved_from:
            refresh_due_state(other, today)
        if str(tx.pk) not in id_set(expense.evidence):
            expense.evidence = [*(expense.evidence or []), str(tx.pk)]
            expense.save(update_fields=["evidence"])
        mark_fixed(tx, expense, "user")
        move_bucket(tx, "living", "fixed")
        if tx.occurred_on <= today:  # 미래 날짜 출금은 아직 안 나간 돈
            match_withdrawal(due, tx, today)
            return
    refresh_due_state(due, today)

# 사용자가 고른 근거 거래를 붙일 회차 — 목록 후보(evidence_suggestion)와 같은 기준
#   지난 주기 미납 회차가 남아 있으면 가장 오래된 그 회차, 없으면 None(→ 이번 회차)
def evidence_target(expense, cycle, today):
    late = late_dues(expense, cycle, today, lock=True)
    return late[0] if late else None

# 고정지출에 연결하면서 이체를 소비로 바꾸기 (월세 이체 등) — 소비 합계에 잡히고, 납부 짝이 있으면 고정비로 표시됨
#   거래 목록의 '이체 → 소비'와 같은 표시(original_transaction_type)를 남기고, 고정지출이 바꿨다는 표시도 남김
#   source: 사용자가 고르면 "user", 서버가 자동 연결하면 "rule"
def mark_fixed(tx, expense, source):
    if tx.transaction_type != "transfer":
        return
    states = dict(tx.field_states or {})
    states[ORIGINAL_TYPE] = "transfer"
    states[CONVERTED_BY_FIXED] = True
    tx.field_states = states
    tx.transaction_type = "spending"
    tx.confirmed_category_id = expense.category_id
    tx.confirmation_state = "confirmed"
    tx.classification_source = source
    tx.save(update_fields=["field_states", "transaction_type", "confirmed_category", "confirmation_state",
                           "classification_source", "updated_at"])

# 연결을 풀 때 고정지출이 바꿨던 이체를 원래 이체로 되돌리기 (사용자가 거래 목록에서 직접 바꾼 건 그대로)
def unmark_fixed(tx):
    states = dict(tx.field_states or {})
    if not states.pop(CONVERTED_BY_FIXED, False) or tx.transaction_type != "spending":
        return
    if states.get(ORIGINAL_TYPE) == "transfer":
        states.pop(ORIGINAL_TYPE)
        tx.transaction_type = "transfer"
        # 이체는 카테고리가 없지만 DB 칸은 비울 수 없어서 '확인 필요'로 둔다 (거래 목록과 같은 규칙)
        tx.confirmed_category_id = "UNCLASSIFIED"
        tx.confirmation_state = "needs_review"
        tx.classification_source = None
    tx.field_states = states
    tx.save(update_fields=["field_states", "transaction_type", "confirmed_category", "confirmation_state",
                           "classification_source", "updated_at"])

# 이번 주기 연결 풀기 → 남길 근거 ID(지난 주기 것) 목록
#   계좌: 이번 주기 회차의 짝을 지우고 납부 상태 다시 계산 · 카드: 재원을 생활비로 되돌림
def unlink_current(expense, cycle, today):
    since = cycle.starts_on if cycle else None
    current = FinancialTransaction.objects.filter(user=expense.user, pk__in=list(id_set(expense.evidence)))
    if since is not None:
        current = current.filter(occurred_on__gte=since)
        # 지난 주기 회차를 갚은 출금(늦게 낸 출금)은 그 회차의 납부 기록이라 건드리지 않음
        current = current.exclude(pk__in=PaymentMatch.objects.filter(due__due_date__lt=since).values("transaction"))
    current = list(current.select_for_update().order_by("pk"))  # 되돌릴 거래 잠금

    dues = PaymentDue.objects.select_for_update().filter(recurring=expense, obligation_state="active")
    if since is not None:
        dues = dues.filter(due_date__gte=since)
    dues = list(dues)
    matches = PaymentMatch.objects.filter(due__in=dues)
    released = {t.pk: t for t in current}
    matched_ids = [pk for pk in matches.values_list("transaction_id", flat=True) if pk not in released]
    released.update({t.pk: t for t in FinancialTransaction.objects.select_for_update().filter(pk__in=matched_ids).order_by("pk")})
    unlinked_keys = {}  # 거래 ID → 연결을 푼 회차 key들 (그 회차에 자동으로 다시 붙지 않게)
    for tx_id, key in matches.values_list("transaction_id", "due__occurrence_key"):
        unlinked_keys.setdefault(tx_id, set()).add(key)
    matches.delete()

    for due in dues:
        refresh_due_state(due, today)
    for tx in released.values():
        unmark_fixed(tx)                    # 고정지출이 바꿨던 이체는 이체로
        move_bucket(tx, "fixed", "living")  # 생활비로 되돌림
        if tx.pk in unlinked_keys:
            states = dict(tx.field_states or {})
            states[SKIP_AUTO] = sorted(set(states.get(SKIP_AUTO, [])) | unlinked_keys[tx.pk])
            tx.field_states = states
            tx.save(update_fields=["field_states", "updated_at"])

    current_ids = {str(t.pk) for t in current}
    return [value for value in (expense.evidence or []) if not (id_set([value]) & current_ids)]

# 수정 뒤 안 낸 회차 맞추기 (이번 주기 미납분과 앞으로의 회차만)
#   카드로 바꾸거나 출금일을 비우면 취소 · 아니면 금액 · 날짜 · 결제 계좌를 새 값으로 · 없으면 새로 만들기
def sync_dues(expense, cycle, today, user_pick=False):
    from .due_periods import uses_monthly_dues
    monthly = uses_monthly_dues(expense.user_id)
    since = cycle.starts_on if cycle else today
    dues = open_dues(expense, since)
    if expense.payment_card_id is not None or expense.payment_day is None:
        cancel_dues(dues)
        return

    for due in dues:
        if not monthly and cycle is not None and cycle.starts_on <= due.due_date < cycle.ends_before:
            due.due_date = day_in_cycle(cycle, expense.payment_day) or due.due_date
        else:
            due.due_date = day_in_month(due.due_date.year, due.due_date.month, expense.payment_day)
        due.confirmed_amount = expense.amount
        due.payment_account = expense.payment_account
        due.save(update_fields=["due_date", "confirmed_amount", "payment_account"])
        refresh_due_state(due, today)

    ensure_current_due(expense, cycle, today, user_pick=user_pick)

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

# 본문이 JSON 객체({...})가 아니면 400 ([] · 42 · "hello" 등)
def body_dict(request):
    data = request.data
    if not isinstance(data, dict):
        raise ValidationError({"non_field_errors": ["요청 본문은 JSON 객체로 보내 주세요."]})
    return data

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
    """직접 추가 때 보낸 값 검사 -> 틀린 칸이 있으면 모아서 한번에 400"""
    data = body_dict(request)
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
    data = body_dict(request)
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
    data = body_dict(request)
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
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
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
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        # 잠그고 찾기 -> 저장 끝날 때까지 다른 요청은 기다림
        saving = get_object_or_404(
            RecurringSaving.objects.select_for_update(),
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

    # DELETE : 중지와 예산 초안 만료를 한 묶음으로 (하나가 실패하면 둘 다 취소)
    with transaction.atomic():
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        saving = get_object_or_404( # 잠그고 찾기, 없으면 자동으로 404
            RecurringSaving.objects.select_for_update(),
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

# 내 저축 여러 개를 ID 순서대로 잠그고 가져오기 (확정 · 무시가 같은 순서로 잠가서 서로 막히지 않음)
def lock_savings(user, ids, statuses):
    return list(RecurringSaving.objects.select_for_update().filter(
        user = user,
        pk__in = ids,
        status__in = statuses,
    ).order_by("pk"))

# 저축 후보 확정 [저축이에요] — 받는 계좌는 저축 용도, 근거 이체는 '저축'으로
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def savings_confirm(request):
    ids = clean_ids(request)

    with transaction.atomic():
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        # 먼저 다 잠그고 찾기 - 내 것, 후보나 이미 확정인 것만
        found = lock_savings(request.user, ids, ["suggested", "confirmed"])

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
        lock_user(request.user)  # 잠금 순서: 회원 → 고정지출·저축 → 납부 예정 → 거래 (아래 lock_user 설명)
        found = lock_savings(request.user, ids, ["suggested", "ignored"])

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
