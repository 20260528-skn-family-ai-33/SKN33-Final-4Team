"""Read-only history checks for budget routing; no guesses, writes or tolerance thresholds."""
from django.db import DatabaseError

CHECKS = ('duplicates', 'cancellations', 'refunds')
STATES = ('verified', 'unknown', 'unresolved')


def history_checks(months, proofs):
    """Missing or malformed evidence cannot become verified."""
    proofs = proofs if isinstance(proofs, dict) else {}
    result = {}
    for month in months:
        row = proofs.get(month)
        row = row if isinstance(row, dict) else {}
        result[month] = {key: row[key] if row.get(key) in STATES else 'unknown' for key in CHECKS}
    return result


def verify_budget_history(user, months):
    """Use the main verifier plus current, eligible user-confirmed salary evidence only."""
    try:
        from finance.history_verifier import verify_transaction_history
        proofs = verify_transaction_history(user, months, use_confirmed_salary=True)
    except (ImportError, DatabaseError):
        # shortcut: this worktree awaits main's SalaryDecision dependency; keep B closed until it is merged.
        proofs = {}
    return history_checks(months, proofs)
