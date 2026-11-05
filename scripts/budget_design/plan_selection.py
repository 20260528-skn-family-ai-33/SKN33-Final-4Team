"""Deterministic A/B routing from normalized completed-month history; no model calls."""
from plan_b_history import CATEGORY_CODES

AB_MAX_COUNT_THRESHOLD = 10  # User decision, separate from the one-off MAD sample-size rule.


def select_plan(history):
    """Count positive variable purchases before one-off removal, across all ten categories."""
    result = {'plan': 'a', 'reason': 'history_not_ready', 'max_category_count': None,
              'category_counts': {}, 'months': list(history['months'])}
    if history['status'] != 'ready' or len(history['months']) != 3:
        return result
    counts = {}
    for category in CATEGORY_CODES:
        monthly = history.get('categories', {}).get(category, {}).get('monthly_purchase_counts')
        if monthly is None or len(monthly) != 3 or any(v is None for v in monthly):
            return {**result, 'reason': 'transaction_counts_unavailable'}
        if any(type(v) is not int or v < 0 for v in monthly):
            raise ValueError('invalid_selection_purchase_counts')
        counts[category] = sum(monthly)
    maximum = max(counts.values())
    enough = maximum > AB_MAX_COUNT_THRESHOLD
    return {**result, 'plan': 'b' if enough else 'a',
            'reason': 'sufficient_transactions' if enough else 'sparse_transactions',
            'category_counts': counts, 'max_category_count': maximum}
