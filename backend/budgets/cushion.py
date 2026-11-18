"""쿠션 확보액과 현재 설정으로 재적립 계획을 계산한다."""
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, Q, Sum
from django.utils import timezone

from finance.models import BankAccount
from users.models import FinancialProfile
from .models import CushionEntry
from .salary import due_income

CUSHION_ACCRUAL_DAYS = 2


def cushion_total(user):
    return CushionEntry.objects.filter(user=user).aggregate(total=Sum("amount"))["total"] or 0


def cushion_data(user):
    profile = due_income(FinancialProfile.objects.filter(user=user).first(), timezone.localdate())  # 다음 주기 실수령액 전환일이 지났으면 새 금액 (11/12)
    income = profile.monthly_take_home_income if profile else None
    monthly = profile.cushion_monthly if profile else None
    if income is not None and income <= 0:
        income = None
    # 저장된 cushion_target 이 오래돼도 지금 목표를 다시 계산한다: 직접 정한 금액(11/13) 또는 현재 월 실수령액 × 3
    from .protect import DEFAULT_GOAL_MULTIPLE, cushion_goal
    custom = profile.cushion_goal_amount is not None if profile else False
    goal = cushion_goal(profile, income)
    default_goal = income * DEFAULT_GOAL_MULTIPLE if income is not None else None
    now = cushion_total(user)
    remain = max(goal - now, 0) if goal is not None else None
    pct = bar = months = None
    if goal is not None:
        percent = Decimal(now) * 100 / Decimal(goal)
        pct = int(percent.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        bar = float(min(Decimal(100), max(Decimal(0), percent)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP,
        ))
    if remain is None:
        eta = "급여를 먼저 입력해 주세요."
    elif remain == 0:
        months, eta = 0, "목표 달성"
    elif monthly is None:
        eta = "월 쿠션 적립금을 먼저 입력해 주세요."
    elif monthly == 0:
        eta = "월 적립금이 0원이라 계산할 수 없어요."
    else:
        months = (remain + monthly - 1) // monthly
        eta = f"{months}회 · 약 {months}개월"
    return {
        "missing": [key for key, value in (("takeHomeIncome", income), ("cushionMonthly", monthly))
                    if value is None],
        "cushion": {
            "now": now, "goal": goal, "goal_label": "직접 정한 금액" if custom else f"월급의 {DEFAULT_GOAL_MULTIPLE}배",
            "goal_custom": custom, "default_goal": default_goal, "bar": bar, "pct": pct,
            "monthly": monthly, "use_rule": None, "remain": remain, "eta": eta, "eta_months": months,
        },
    }


# ---------------- 저축 계좌 잔액 (11/18, 쿠션 화면 B안) ----------------
# 은행 연결 때 CODEF 가 입출금과 함께 예적금 계좌도 돌려줘서 이미 저장돼 있다 (finance/sync.py). 여기서는 저장된 값만 읽는다 — CODEF 를 부르지 않는다
# sync 는 아직 11(입출금) 외를 모두 other 로 저장한다. 그중 CODEF 예금 구분 12(예적금)만 저축 계좌로 본다
#   shortcut: 코드 12 매핑을 sync 에서 deposit 으로 바꾸면 이 OR 조건은 account_type="deposit" 하나로 줄인다

def savings_data(user):
    from finance.display import account_display, organization_name

    accounts = (BankAccount.objects
                .filter(user=user, connection__user=user, connection__business_type="bank",
                        connection__disconnected_at__isnull=True)
                .filter(Q(account_type="deposit") | Q(account_type="other", field_states__codef_deposit_code="12"))
                .select_related("connection").order_by("connection__created_at", "masked_number", "account_id"))
    items = [{
        **account_display(account),
        "organization_name": organization_name(account.connection),
        "balance": account.balance if account.currency == "KRW" else None,
        "fetched_at": account.fetched_at,
    } for account in accounts]
    known = [item["balance"] for item in items if item["balance"] is not None]
    fetched = [item["fetched_at"] for item in items if item["fetched_at"] is not None]
    return {
        "items": items,
        "total": sum(known) if known else None,       # 잔액을 하나도 모르면 0원이 아니라 null
        "unknown_count": len(items) - len(known),
        "as_of": max(fetched) if fetched else None,
    }


# ---------------- R27 보호 중인 쿠션: 예산 승인 때 쌓기 (11/13) ----------------
# 예산을 승인할 때 그 주기의 쿠션 배정액(BudgetItem bucket=cushion 합계)을 쿠션 기록(CushionEntry)에 반영한다.
# 같은 주기에 다시 승인(조정 · 분배 실패 복구 포함)하면 이미 반영한 금액과의 차액만 남긴다 (90만 + 30만 → 120만, 같은 주기 20만으로 → 110만).
# 추천 · 초안은 쌓지 않는다. 실제 이체 완료를 뜻하지 않는 '예산에서 보호한 기록'이라 종류는 planned(계획상 보호)다.
# 실제로 옮긴 적립(allocation)과 나눠 두어, 예산 조정이 '이미 적립한 돈'(allocation)만 줄이지 못하게 하는 계산과 섞이지 않는다 (검수 P2)
# 승인 경로(일반 승인 · 대화형 조정 · 분배 실패 복구)가 모두 Budget 을 status=approved 로 save 하므로
# signals.py 의 post_save 에서 한 곳으로 부른다 (검수 P1 — 조정 · 복구 승인도 반영)

def _cycle_recorded(user_id, cycle_id):
    return CushionEntry.objects.filter(user_id=user_id, cycle_id=cycle_id,
                                       entry_type="planned").aggregate(total=Sum("amount"))["total"] or 0


def cycle_allocated(user_id, cycle_id):
    """이번 주기에 실제로 옮긴 쿠션 적립(allocation) 합계. 승인 예산의 쿠션 금액은 이보다 낮출 수 없다 (PR #120 리뷰 P1)"""
    return CushionEntry.objects.filter(user_id=user_id, cycle_id=cycle_id,
                                       entry_type="allocation").aggregate(total=Sum("amount"))["total"] or 0


def cushion_notices(user, cycle, today):
    deposit_due = bool(cycle and today == cycle.starts_on + timedelta(days=1))
    estimate = cushion_total(user)
    balance_low = False
    if estimate > 0:
        accounts = BankAccount.objects.filter(
            user=user, currency="KRW", connection__user=user, connection__business_type="bank",
            connection__disconnected_at__isnull=True,
        ).aggregate(total=Sum("balance"), count=Count("pk"),
                    known=Count("balance", filter=Q(connection__status="connected")))
        balance_low = (accounts["total"] is not None and accounts["count"] == accounts["known"]
                       and accounts["total"] < estimate)
    return {"deposit_due": deposit_due, "balance_low": balance_low}


def cushion_notices_today(user, today):
    """쿠션 화면용 (11/18): 오늘이 든 급여 주기로 홈과 같은 쿠션 알림. 주기를 새로 만들지 않는다(없으면 적립일 알림 없음)"""
    from .models import BudgetCycle
    cycle = BudgetCycle.objects.filter(user=user, starts_on__lte=today, ends_before__gt=today).order_by("-starts_on").first()
    return cushion_notices(user, cycle, today)


def _planned(budget):
    from .models import BudgetItem
    return BudgetItem.objects.filter(budget=budget, bucket="cushion").aggregate(total=Sum("allocated_amount"))["total"] or 0


def record_due_cushion(user, today):
    from .models import Budget

    keys = set(CushionEntry.objects.filter(user=user, request_key__startswith="budget-approve:")
               .values_list("request_key", flat=True))
    budgets = Budget.objects.filter(
        user=user, status="approved", approved_at__isnull=False,
        cycle__starts_on__lte=today - timedelta(days=CUSHION_ACCRUAL_DAYS),
    ).select_related("cycle")
    for budget in budgets.iterator():
        if f"budget-approve:{budget.pk}" not in keys:
            record_approved_cushion(budget, today)


def record_approved_cushion(budget, today=None):
    """월급날 +2일이 지나면 같은 주기 내 차액을 한 번 반영한다. 회원 잠금 안에서 사용한다."""
    due = budget.cycle.starts_on + timedelta(days=CUSHION_ACCRUAL_DAYS)
    if (today or timezone.localdate()) < due:
        return None
    key = f"budget-approve:{budget.pk}"
    if CushionEntry.objects.filter(user_id=budget.user_id, request_key=key).exists():
        return None
    recorded = _cycle_recorded(budget.user_id, budget.cycle_id)
    diff = _planned(budget) - recorded
    if diff == 0:
        return None
    due_at = timezone.make_aware(datetime.combine(due, time.min))
    automatically_carried = (budget.validation_summary or {}).get("rollover")
    occurred_at = due_at if automatically_carried else max(budget.approved_at or timezone.now(), due_at)
    return CushionEntry.objects.create(
        user_id=budget.user_id, cycle_id=budget.cycle_id, budget=budget, entry_type="planned", amount=diff,
        request_key=key, occurred_at=occurred_at,
        note="예산 승인" if recorded == 0 else "같은 주기 예산 다시 승인 (차액)",
    )


def update_approved_split(user, budget, saving, cushion):
    """검수 P1 (11/13): 지킬 돈 설정에서 생활비는 그대로 두고 저축 · 월 쿠션 적립금만 바꿔 저장했을 때.
    이번 주기 승인 예산의 저축 · 쿠션 줄만 새 금액으로 바꾼다. 생활비 카테고리 배분은 그대로, 여윳돈은 '배분 대상 − 항목 합계'라 함께 맞춰진다.
    다시 추천(LLM)은 하지 않는다. 사용자가 저장으로 정한 금액이고 계산은 여기(서버)에서만 한다.
    만들어 둔 조정안 · 초안은 지킬 돈 저장(save_protect)이 이미 만료한다.
    반환: True = 반영(또는 이미 같음) · False = 반영할 수 없음(줄 모양이 다르거나 합계가 배분 대상을 넘음 → 화면이 다시 추천으로 보낸다)"""
    from .models import BudgetItem

    items = list(BudgetItem.objects.select_for_update().filter(budget=budget))
    saving_rows = [row for row in items if row.bucket == "savings"]
    cushion_rows = [row for row in items if row.bucket == "cushion"]
    if len(saving_rows) != 1 or len(cushion_rows) != 1 or saving is None or cushion is None:
        return False
    saving_row, cushion_row = saving_rows[0], cushion_rows[0]
    before = {"saving_floor": saving_row.allocated_amount, "cushion_monthly": cushion_row.allocated_amount}
    if before == {"saving_floor": saving, "cushion_monthly": cushion}:
        return True
    total = sum(row.allocated_amount for row in items) - saving_row.allocated_amount - cushion_row.allocated_amount + saving + cushion
    if budget.funding_amount is None or total > budget.funding_amount:
        return False
    saving_row.allocated_amount, cushion_row.allocated_amount = saving, cushion
    BudgetItem.objects.bulk_update([saving_row, cushion_row], ["allocated_amount"])
    summary = dict(budget.validation_summary or {})
    summary["protect_updates"] = [*summary.get("protect_updates", []), {
        "at": timezone.now().isoformat(), "before": before, "after": {"saving_floor": saving, "cushion_monthly": cushion},
    }]
    budget.validation_summary = summary
    budget.save(update_fields=["validation_summary"])
    recorded = _cycle_recorded(user.pk, budget.cycle_id)
    diff = cushion - recorded
    if diff and (recorded or timezone.localdate() >= budget.cycle.starts_on + timedelta(days=CUSHION_ACCRUAL_DAYS)):
        from uuid import uuid4
        CushionEntry.objects.create(
            user=user, cycle_id=budget.cycle_id, budget=budget, entry_type="planned", amount=diff,
            request_key=f"protect-save:{uuid4()}", occurred_at=timezone.now(), note="지킬 돈 설정에서 월 쿠션 적립금 변경 (차액)",
        )
    return True
