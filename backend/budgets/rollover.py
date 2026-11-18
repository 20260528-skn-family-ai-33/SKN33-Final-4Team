"""Apply the last ongoing budget to the current pay cycle."""
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from users.models import Consent

from .cushion import record_due_cushion
from .models import Budget, BudgetAdjustmentSchedule, BudgetItem


def recurring_budget(user, cycle):
    earlier = Budget.objects.filter(
        user=user, cycle__starts_on__lt=cycle.starts_on, approved_at__isnull=False,
        status__in=("approved", "superseded"), schedule__isnull=True,
    ).select_related("cycle").order_by("-cycle__starts_on", "-version_no")
    baseline = None
    for budget in earlier.iterator():
        summary = budget.validation_summary or {}
        if (summary.get("rollover") or {}).get("one_off"):
            continue
        baseline = budget  # The first approved plan is the baseline even if made in chat.
        adjustment = summary.get("adjustment") or summary.get("design_recovery") or {}
        if (adjustment.get("approval") or {}).get("keep_for_next_cycle") is not False:
            break

    schedules = BudgetAdjustmentSchedule.objects.filter(
        user=user, status="applied", effective_on__lte=cycle.starts_on,
    ).select_related("proposal").order_by("-effective_on", "-applied_at")
    for schedule in schedules.iterator():
        one_off = bool(schedule.settings.get("one_off"))
        if one_off and schedule.effective_on != cycle.starts_on:
            continue
        if baseline is None or baseline.cycle.starts_on < schedule.effective_on:
            return schedule.proposal, one_off
        break
    return baseline, False


def ensure_current_budget(user, profile, cycle, today):
    """Called with the member locked, so the budget, its lines and the cushion are saved together."""
    if cycle is None:
        return
    budgets = Budget.objects.filter(user=user, cycle=cycle)
    if not budgets.filter(status="approved").exists():
        # shortcut: only materialize the current cycle on demand, pending a periodic rollover job.
        source, one_off = recurring_budget(user, cycle)
        if source is not None:
            carry_budget(user, profile, cycle, budgets, source, one_off)
    record_due_cushion(user, today)


def sync_current_budget(user, today):
    from .api import lock_user
    from .overview import current_cycle
    from .salary import current_salary_profile

    if not Budget.objects.filter(user=user, approved_at__isnull=False).exists():
        return
    with transaction.atomic():
        lock_user(user)
        profile = current_salary_profile(user, today)
        ensure_current_budget(user, profile, current_cycle(user, profile, today), today)


def carry_budget(user, profile, cycle, budgets, source, one_off):
    from .api import fixed_budget_lines
    from .home import balance_json
    from .protect import cushion_goal, cushion_required, fixed_breakdown, savings_min
    from .recommend import budget_settings
    from .salary import budget_income

    consents = Consent.objects.filter(user=user, purpose="finance_inquiry", product_scope="bank")
    if consents.exists() and not consents.filter(revoked_at__isnull=True).exists():
        return
    income = budget_income(cycle, profile)
    if not income or income < 0:
        return

    lines = list(source.items.filter(user=user, bucket__in=("living", "savings", "cushion")).values(
        "bucket", "category_id", "category_detail", "allocated_amount",
    ))
    living = {row["category_id"]: row["allocated_amount"] for row in lines if row["bucket"] == "living"}
    saved = sum(row["allocated_amount"] for row in lines if row["bucket"] == "savings")
    cushion = sum(row["allocated_amount"] for row in lines if row["bucket"] == "cushion")
    minimums, _ = budget_settings(user, cycle)
    if (not living or any(row["allocated_amount"] < 0 for row in lines) or saved < savings_min(user)
            or any(living.get(code, 0) < amount for code, amount in minimums.items())):
        return
    if cushion == 0 and cushion_required(balance_json(user)["total"], income, cushion_goal(profile, income)):
        return

    fixed = fixed_breakdown(user, cycle)
    if fixed["card_reserved"] is None or fixed["planned_reserve"] and not fixed["planned_reserve_id"]:
        return
    lines += fixed_budget_lines(fixed)
    if sum(row["allocated_amount"] for row in lines) > income:
        return

    stored = source.input_snapshot or {}
    reference = stored.get("transport_history_reference") or (stored.get("adjustment_context") or {}).get("transport_history_reference")
    snapshot = {"fixed": fixed, "transport_history_reference": reference}
    version = (budgets.aggregate(last=Max("version_no"))["last"] or 0) + 1
    budget = Budget.objects.create(
        user=user, cycle=cycle, version_no=version, funding_amount=income, status="approved",
        approved_at=timezone.now(), engine_version=source.engine_version, policy_version=source.policy_version,
        input_snapshot=snapshot,
        validation_summary={"rollover": {"source_budget_id": str(source.pk), "one_off": one_off}},
    )
    BudgetItem.objects.bulk_create([BudgetItem(user=user, budget=budget, **line) for line in lines])
