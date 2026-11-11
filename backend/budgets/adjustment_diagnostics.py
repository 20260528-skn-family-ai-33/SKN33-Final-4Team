"""Read-only current diagnosis sources. Coverage and normalization are independent evidence."""
from datetime import date, timedelta
from hashlib import sha256


from finance.api import listed_transactions, transaction_item
from finance.history_verifier import _report_for_months
from finance.merchant_rules import normalize_merchant
from finance.models import PaymentDue
from spending_analysis.coverage import coverage
from spending_analysis.periods import Period

from .adjustment_core.diagnostics import build_history, inputs_from_sources, digest
from .models import ReservePlan


def _recent_normalization_checks(user, today):
    months = sorted({(today - timedelta(days=n)).strftime("%Y-%m") for n in range(1, 31)})
    report = _report_for_months(user, months, use_confirmed_salary=True)
    return {key: "verified" if all(report[m][key]["status"] == "verified" for m in months) else "unknown"
            for key in ("duplicates", "cancellations", "refunds")}


def read_diagnostics(user, context, cycle, today):
    from scripts.budget_design.plan_b_history import completed_months
    start = date.fromisoformat(completed_months(today)[0] + "-01")
    collected = coverage(user, Period(start, today))
    covered_days = []
    if not collected.get("undetermined") and collected.get("state") != "error" and collected.get("targets"):
        for offset in range((today - start).days):
            d = start + timedelta(days=offset)
            if all(any(p.start <= d < p.end_before for p in target["covered"]) for target in collected["targets"]):
                covered_days.append(d.isoformat())
    rows = list(listed_transactions(user).filter(occurred_on__gte=start, occurred_on__lt=today).order_by("occurred_on", "pk"))
    records = []
    for tx in rows:
        row = transaction_item(tx)
        if row["transaction_type"] != "spending":
            continue
        merchant = normalize_merchant(row["merchant_name"])
        records.append({"transaction_id": str(tx.pk), "occurred_on": tx.occurred_on.isoformat(),
                        "transaction_type": "spending", "category": row["category"],
                        "net_amount": row["net_amount"], "funding": row["funding"],
                        "merchant_key": sha256(merchant.casefold().encode()).hexdigest() if merchant else None})
    checks = _recent_normalization_checks(user, today)
    history = build_history(today, context["allocations"], records, covered_days, checks)
    info = inputs_from_sources(today, history, cycle_ends_before=cycle.ends_before, next_payday=cycle.ends_before)
    info["sources"] = {"as_of": today.isoformat(), "coverage": collected["state"],
                       "normalization_checks": checks, "history_reason": history.get("reason"),
                       "planned_reason": "additional_unbudgeted_plans_require_user_confirmation"}
    return info


def reserved_spare(user, cycle, today):
    """Protect confirmed earmarks and unpaid manual obligations; never invent a reservation table."""
    plans = list(ReservePlan.objects.filter(user=user, confirmation_state="confirmed")
                 .values("reserve_id", "confirmed_reserved_amount", "updated_at"))
    if any(type(row["confirmed_reserved_amount"]) is not int or row["confirmed_reserved_amount"] < 0 for row in plans):
        return None, digest({"plans": [{"id": str(p["reserve_id"]), "amount": p["confirmed_reserved_amount"]} for p in plans]})
    dues = list(PaymentDue.objects.filter(user=user, obligation_type="manual", obligation_state="active",
                confirmation_state="confirmed", reserve__isnull=True, due_date__gte=today, due_date__lt=cycle.ends_before)
                .values("due_id", "confirmed_amount", "paid_amount"))
    amount = sum(p["confirmed_reserved_amount"] for p in plans) + sum(max(p["confirmed_amount"] - p["paid_amount"], 0) for p in dues)
    return amount, digest({"plans": [{"id": str(p["reserve_id"]), "amount": p["confirmed_reserved_amount"], "updated": p["updated_at"].isoformat()} for p in plans],
                           "dues": [{"id": str(p["due_id"]), "amount": p["confirmed_amount"], "paid": p["paid_amount"]} for p in dues]})
