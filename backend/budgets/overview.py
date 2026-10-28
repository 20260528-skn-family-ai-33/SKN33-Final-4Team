"""카테고리 예산 응답. 주기 준비가 있으므로 호출 쪽에서 회원 잠금 · 트랜잭션을 잡는다."""
from django.db.models import Sum

from finance.api import listed_transactions, transaction_item
from finance.spending import spending_summary
from users.models import CategoryMinimum

from .home import ensure_cycle, period_json
from .models import Budget, BudgetCategorySetting, Category
from .recommend import CATEGORY_CODES
from .salary import current_salary_profile, salary_cycle_dates


LIVING_CODES = (*CATEGORY_CODES, "ETC")
NAG = "자주 바꾸고 있어요. 이번 달 계획을 한 번 정리해 볼까요?"


def current_cycle(user, profile, today):
    if profile is None or profile.payday is None:
        return None
    start, end, _ = salary_cycle_dates(profile, today)
    return ensure_cycle(user, start, end)


def budget_nag(user, cycle):
    changed = BudgetCategorySetting.objects.filter(
        user=user, cycle=cycle, category_id__in=LIVING_CODES, change_count__gte=3,
    ).exclude(category_id__in=["HOUSING_TELECOM", "TRANSPORT_CAR"]).exists()
    if changed or Budget.objects.filter(user=user, cycle=cycle, approved_at__isnull=False).count() >= 3:
        return NAG
    return None


def budget_data(user, today, *, selected=None):
    profile = current_salary_profile(user, today)
    cycle = current_cycle(user, profile, today)
    rows = listed_transactions(user)
    if cycle:
        rows = rows.filter(occurred_on__gte=cycle.starts_on, occurred_on__lt=cycle.ends_before)
    else:
        # 급여일을 아직 정하지 않았으면 홈처럼 오늘까지 전체 기간의 확인할 소비를 센다.
        rows = rows.filter(occurred_on__lte=today)
    summary = spending_summary((transaction_item(tx) for tx in rows), living_only=True)
    data = {
        "cycle": {"starts_on": cycle.starts_on.isoformat(), "ends_before": cycle.ends_before.isoformat()} if cycle else None,
        "budget": None, "split": None, "living": None, "overspend": None,
        "categories": [], "unclassified_count": summary["needs_review_count"], "nag": None,
    }
    if cycle is None:
        return data
    if selected is None or selected.cycle_id != cycle.pk:
        mine = Budget.objects.filter(user=user, cycle=cycle)
        selected = (mine.filter(status="validated").order_by("-version_no").first()
                    or mine.filter(status="approved").first())
    if selected is None:
        return data
    period = period_json(user, profile, today, cycle, cycle.starts_on, cycle.ends_before)
    totals = dict(selected.items.values("bucket").annotate(total=Sum("allocated_amount")).values_list("bucket", "total"))
    living = totals.get("living", 0)
    spare = selected.funding_amount - sum(totals.values())
    spent = summary["spent_total"]
    over = max(spent - living, 0)
    covered = min(over, spare)
    allocated = dict(selected.items.filter(bucket="living").values("category_id").annotate(total=Sum("allocated_amount")).values_list("category_id", "total"))
    minimums = dict(CategoryMinimum.objects.filter(user=user).values_list("category_id", "min_amount"))
    settings = {s.category_id: s for s in BudgetCategorySetting.objects.filter(user=user, cycle=cycle)}
    categories = []
    for category in Category.objects.filter(pk__in=LIVING_CODES).order_by("display_order", "pk"):
        setting = settings.get(category.pk)
        categories.append({
            "category": category.pk, "category_name": category.display_name,
            "allocated": allocated.get(category.pk, 0), "spent": summary["by_category"].get(category.pk, 0),
            "min_amount": minimums.get(category.pk), "locked": setting.locked if setting else False,
            "locked_amount": setting.locked_amount if setting else None, "reason": None,
        })
    data.update({
        "budget": {"budget_id": str(selected.pk), "version_no": selected.version_no, "status": selected.status,
                   "salary_confirmed": period["salary_state"] == "received"},
        "split": {"funding": selected.funding_amount, "fixed": totals.get("fixed", 0), "saving": totals.get("savings", 0),
                  "cushion": totals.get("cushion", 0), "living": living, "spare": spare},
        "living": {"budget": living, "spent": spent, "remaining": max(living - spent, 0)},
        "overspend": {"over": over, "covered_by_spare": covered, "spare_remaining": spare - covered, "shortfall": over - covered},
        "categories": categories, "nag": budget_nag(user, cycle),
    })
    return data
