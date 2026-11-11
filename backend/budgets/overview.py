"""카테고리 예산 응답. 주기 준비가 있으므로 호출 쪽에서 회원 잠금 · 트랜잭션을 잡는다."""
from django.db.models import Sum

from finance.api import listed_transactions, transaction_item
from finance.spending import spending_summary
from users.models import CategoryMinimum

from .home import ensure_cycle, period_json
from .models import Budget, BudgetCategorySetting, Category
from .recommend import ALLOCATION_CODES
from .salary import current_salary_profile, salary_cycle_dates
from .scope import OUT_OF_BUDGET, living_spent, outside_spent


LIVING_CODES = (*ALLOCATION_CODES, "ETC")  # 예산 화면 · 자물쇠 대상. 예산 밖(scope.OUT_OF_BUDGET)은 없음
NAG = "자주 바꾸고 있어요. 이번 달 계획을 한 번 정리해 볼까요?"


def current_cycle(user, profile, today):
    if profile is None or profile.payday is None:
        return None
    start, end, _ = salary_cycle_dates(profile, today)
    return ensure_cycle(user, start, end)


def budget_nag(user, cycle):
    changed = BudgetCategorySetting.objects.filter(
        user=user, cycle=cycle, category_id__in=LIVING_CODES, change_count__gte=3,
    ).exclude(category_id="TRANSPORT_CAR").exists()
    if changed or Budget.objects.filter(user=user, cycle=cycle, approved_at__isnull=False).count() >= 3:
        return NAG
    return None


def out_of_budget_json(summary):
    """예산 밖 지출 안내 (계산에는 안 넣음). 채팅 outside_spent 와 같은 budgets.scope 계산"""
    outside = outside_spent(summary)
    return {"total": sum(outside.values()), "categories": [{"category": c, "spent": v} for c, v in outside.items()]}


def budget_data(user, today, *, selected=None):
    profile = current_salary_profile(user, today)
    cycle = current_cycle(user, profile, today)
    rows = listed_transactions(user)
    if cycle:
        # 채팅(spending_analysis budget_left)과 같은 기간: 주기 시작일 ~ 오늘
        rows = rows.filter(occurred_on__gte=cycle.starts_on, occurred_on__lt=cycle.ends_before, occurred_on__lte=today)
    else:
        # 급여일을 아직 정하지 않았으면 홈처럼 오늘까지 전체 기간의 확인할 소비를 센다.
        rows = rows.filter(occurred_on__lte=today)
    summary = spending_summary((transaction_item(tx) for tx in rows), living_only=True)
    data = {
        "cycle": {"starts_on": cycle.starts_on.isoformat(), "ends_before": cycle.ends_before.isoformat()} if cycle else None,
        "budget": None, "split": None, "living": None, "overspend": None,
        "categories": [], "unclassified_count": summary["needs_review_count"], "nag": None, "out_of_budget": None,
        "card_notes": [],
    }
    if cycle is None:
        return data
    if (selected is not None and selected.status != "approved"
            and (selected.base_budget_id is not None
                 or selected.task_id is not None and selected.task.task_type == "budget_adjust")):
        selected = None
    if selected is None or selected.cycle_id != cycle.pk:
        mine = Budget.objects.filter(user=user, cycle=cycle)
        selected = (mine.filter(status="validated", base_budget__isnull=True)
                    .exclude(task__task_type="budget_adjust").order_by("-version_no").first()
                    or mine.filter(status="approved").first())
    if selected is None:
        return data
    period = period_json(user, profile, today, cycle, cycle.starts_on, cycle.ends_before)
    totals = dict(selected.items.values("bucket").annotate(total=Sum("allocated_amount")).values_list("bucket", "total"))
    living = totals.get("living", 0)
    spare = selected.funding_amount - sum(totals.values())
    spent = living_spent(summary)  # 예산 밖 지출은 빼고 센다 (전체 소비 집계는 그대로)
    over = max(spent - living, 0)
    covered = min(over, spare)
    allocated = dict(selected.items.filter(bucket="living").values("category_id").annotate(total=Sum("allocated_amount")).values_list("category_id", "total"))
    minimums = dict(CategoryMinimum.objects.filter(user=user).values_list("category_id", "min_amount"))
    settings = {s.category_id: s for s in BudgetCategorySetting.objects.filter(user=user, cycle=cycle)}
    design = (selected.validation_summary or {}).get('design', {})
    reasons = {e['category']: e['text'] for e in design.get('explanations', [])}
    categories = []
    for category in Category.objects.filter(pk__in=LIVING_CODES).order_by("display_order", "pk"):
        setting = settings.get(category.pk)
        categories.append({
            "category": category.pk, "category_name": category.display_name,
            "allocated": allocated.get(category.pk, 0), "spent": summary["by_category"].get(category.pk, 0),
            "min_amount": minimums.get(category.pk), "locked": setting.locked if setting else False,
            "locked_amount": setting.locked_amount if setting else None, "reason": reasons.get(category.pk) or None,
        })
    data.update({
        # 현재 조회 결과가 아니라 선택한 예산의 계산 당시 안내. 새 청구서 조회만으로 미반영 안내를 지우지 않는다.
        "card_notes": ((selected.input_snapshot or {}).get("fixed") or {}).get("card_notes", []),
        "recommendation": {'spare_transfer': design.get('spare_transfer', 0),
                           'target_conflicts': design.get('target_conflicts', [])} if design else None,
        "budget": {"budget_id": str(selected.pk), "version_no": selected.version_no, "status": selected.status,
                   "salary_confirmed": period["salary_state"] == "received", "engine_version": selected.engine_version,
                   # 이전 기준으로 승인한 예산의 주거·통신 · 여행·숙박 배정 (생활비 총액에는 들어 있고 카테고리 줄에는 없음, N-F1)
                   "out_of_budget_allocated": sum(v for c, v in allocated.items() if c in OUT_OF_BUDGET)},
        "split": {"funding": selected.funding_amount, "fixed": totals.get("fixed", 0), "saving": totals.get("savings", 0),
                  "cushion": totals.get("cushion", 0), "living": living, "spare": spare},
        "living": {"budget": living, "spent": spent, "remaining": max(living - spent, 0)},
        "overspend": {"over": over, "covered_by_spare": covered, "spare_remaining": spare - covered, "shortfall": over - covered},
        "categories": categories, "nag": budget_nag(user, cycle),
        "out_of_budget": out_of_budget_json(summary),
    })
    return data
