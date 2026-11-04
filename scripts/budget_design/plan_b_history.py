"""Plan B normalized history and transaction outliers; no DB, ML or LLM calls.

months maps YYYY-MM to items (or monthly_totals), coverage_complete, normalization_checks,
actual_income and actual_savings. Items follow finance.api.transaction_item.
The caller must verify duplicates, cancellations and refunds; this module
does not discover missing accounts or normalize raw bank/card transactions.
Income is actual after-tax receipts; savings are actual net saving flows
(withdrawals may be negative), never a planned savings amount.
"""
from datetime import date, timedelta
from fractions import Fraction
from hashlib import sha256
import json
from math import log
from pathlib import Path
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from finance.spending import spending_summary


CATEGORY_CODES = (
    'FOOD', 'HOUSING_TELECOM', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING',
    'MEDICAL_HEALTH', 'BEAUTY', 'EDUCATION', 'CULTURE_LEISURE', 'TRAVEL_LODGING',
)
CHECKS = ('duplicates', 'cancellations', 'refunds')
DISCRETIONARY = ('FASHION_SHOPPING', 'BEAUTY', 'CULTURE_LEISURE')
# Wide prices and long-cycle purchases/treatments made dominance over-exclude in synthetic comparisons.
MAD_ONLY_CATEGORIES = frozenset(('FASHION_SHOPPING', 'BEAUTY'))
MAD_MIN_TRANSACTIONS = 10  # User decision; 5/8 are sensitivity experiments only.
DOMINANCE_MIN_TRANSACTIONS = 3  # User decision: compare the largest with at least two others.
# Iglewicz & Hoaglin (1993), How to Detect and Handle Outliers; NIST EDA 1.3.5.17.
MODIFIED_Z_SCALE = 0.6745
MODIFIED_Z_THRESHOLD = 3.5


def detect_one_offs(transactions, *, min_count=MAD_MIN_TRANSACTIONS):
    """Internal normalized positive living transactions. Never return merchant keys or source IDs."""
    if type(min_count) is not int or min_count < 1:
        raise ValueError('invalid_mad_min_count')
    recurrence = {}
    for row in transactions:
        if row['merchant_key']:
            recurrence.setdefault(row['merchant_key'], set()).add(row['month'])
    candidates = []
    for category in CATEGORY_CODES:
        rows = [r for r in transactions if r['category'] == category]
        if len(rows) < DOMINANCE_MIN_TRANSACTIONS:
            continue
        ys = [log(r['amount']) for r in rows]
        middle = median(ys)
        mad = median([abs(y - middle) for y in ys])
        required_count = MAD_MIN_TRANSACTIONS if category in MAD_ONLY_CATEGORIES else min_count
        if len(rows) >= required_count and mad > 0:
            selected = [(r, 'mad', MODIFIED_Z_SCALE * (y-middle) / mad)
                        for r, y in zip(rows, ys) if MODIFIED_Z_SCALE * (y-middle) / mad > MODIFIED_Z_THRESHOLD]
        elif category not in MAD_ONLY_CATEGORIES:
            largest = max(rows, key=lambda r: r['amount'])
            rest = sum(r['amount'] for r in rows) - largest['amount']
            selected = [(largest, 'dominance', largest['amount'] / rest)] if largest['amount'] > rest else []
        else:
            selected = []
        for row, method, score in selected:
            key = row['merchant_key']
            candidates.append({'id': row['id'], 'month': row['month'], 'category': category,
                'amount': row['amount'], 'method': method, 'score': score,
                'excluded_by_recurrence': bool(key and len(recurrence[key]) > 1),
                'detail': {'merchant_check': 'available' if key else 'unavailable', 'score_reason': None}})
    return candidates


def completed_months(as_of):
    """Oldest first; caller supplies the service-local date explicitly."""
    if type(as_of) is not date:
        raise ValueError('invalid_as_of_date')
    cursor = as_of.replace(day=1)
    result = []
    for _ in range(3):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        result.append(cursor.strftime('%Y-%m'))
    return list(reversed(result))


def overspending_index(finances):
    """Exactly three aligned monthly records; None means missing, not zero."""
    if not isinstance(finances, list) or len(finances) != 3:
        raise ValueError('three_financial_months_required')
    missing = []
    for row in finances:
        if not isinstance(row, dict):
            raise ValueError('invalid_financial_month')
        for field in ('actual_income', 'actual_savings'):
            value = row.get(field)
            if value is None:
                missing.append(field)
            elif type(value) is not int or (field == 'actual_income' and value < 0):
                raise ValueError('invalid_' + field)
    if missing:
        return {'status': 'unavailable', 'value': None, 'band': None,
                'reason': 'missing_actual_finances', 'missing_fields': sorted(set(missing))}
    income = sum(row['actual_income'] for row in finances)
    savings = sum(row['actual_savings'] for row in finances)
    if income == 0:
        return {'status': 'unavailable', 'value': None, 'band': None, 'reason': 'zero_income'}
    index = Fraction(income - savings, income)
    band = ('frugal' if index < Fraction(1, 2) else
            'adequate' if index < Fraction(7, 10) else
            'review_overspending' if index < 1 else 'review_cashflow')
    return {'status': 'available', 'value': float(index), 'band': band,
            'review_reductions': index >= Fraction(7, 10),
            'income_total': income, 'actual_savings_total': savings}


def _validate_items(items, month, seen):
    if not isinstance(items, list):
        raise ValueError('invalid_month_items')
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('invalid_transaction')
        day = item.get('occurred_on')
        if isinstance(day, str):
            try:
                day = date.fromisoformat(day)
            except ValueError:
                raise ValueError('invalid_transaction_date') from None
        if type(day) is not date or day.strftime('%Y-%m') != month:
            raise ValueError('transaction_month_mismatch')
        tx_id = item.get('transaction_id')
        if tx_id is not None:
            tx_id = str(tx_id)
            if not tx_id or tx_id in seen:
                raise ValueError('duplicate_or_empty_transaction_id')
            seen.add(tx_id)
        if item.get('transaction_type') not in ('spending', 'income', 'transfer'):
            raise ValueError('normalized_transaction_type_required')
        if type(item.get('net_amount')) is not int or item['net_amount'] < 0:
            raise ValueError('invalid_net_amount')
        if item['transaction_type'] == 'spending':
            if item.get('category') not in (*CATEGORY_CODES, 'ETC', 'UNCLASSIFIED'):
                raise ValueError('unknown_spending_category')
            if item.get('funding') not in ('fixed', 'living'):
                raise ValueError('invalid_spending_funding')


def summarize_history(months, *, as_of, baseline='one_off_adjusted', user_reincluded_one_offs=(),
                      one_off_decisions=None):
    """Unknown coverage/normalization blocks comparison; missing finances only blocks index.

    Empty items with verified complete coverage means zero spending. Missing
    months never become zero observations. Frequency counts positive-net
    purchases after normalization (a fully cancelled purchase counts as zero).
    Averages remain exact Fractions until the budget calculation stage.
    """
    if baseline not in ('average', 'median', 'one_off_adjusted'):
        raise ValueError('invalid_history_baseline')
    if (not isinstance(user_reincluded_one_offs, (list, tuple))
            or any(not isinstance(v, str) for v in user_reincluded_one_offs)
            or len(set(user_reincluded_one_offs)) != len(user_reincluded_one_offs)):
        raise ValueError('invalid_reincluded_one_off_ids')
    expected = completed_months(as_of)
    if not isinstance(months, dict) or set(months) - set(expected):
        raise ValueError('expected_completed_months_only')
    monthly, blockers, seen, transactions = {}, [], set(), []
    content_counts = {}
    totals_only = False
    for month in expected:
        if month not in months:
            blockers.append({'month': month, 'reason': 'missing_month'})
            continue
        record = months[month]
        if not isinstance(record, dict):
            raise ValueError('invalid_month_record')
        coverage = record.get('coverage_complete')
        if coverage is not None and type(coverage) is not bool:
            raise ValueError('invalid_coverage')
        if coverage is not True:
            blockers.append({'month': month, 'reason': 'coverage_not_complete'})
        checks = record.get('normalization_checks', {})
        if (not isinstance(checks, dict) or set(checks) - set(CHECKS)
                or any(v not in ('verified', 'unknown', 'unresolved') for v in checks.values())):
            raise ValueError('invalid_normalization_checks')
        checks = {key: checks.get(key, 'unknown') for key in CHECKS}
        for key, state in checks.items():
            if state != 'verified':
                blockers.append({'month': month, 'reason': key + '_' + state})
        if 'monthly_totals' in record:
            totals_only = True
            totals = record['monthly_totals']
            if ('items' in record or not isinstance(totals, dict)
                    or set(totals) - set((*CATEGORY_CODES, 'ETC', 'UNCLASSIFIED'))
                    or any(type(v) is not int or v < 0 for v in totals.values())):
                raise ValueError('invalid_monthly_totals')
            # Reuse aggregation only; these synthetic rows must never enter transaction detection/counts.
            items = [{'occurred_on': month+'-01', 'transaction_type': 'spending',
                      'funding': 'living', 'category': c, 'net_amount': v} for c, v in totals.items()]
        else:
            items = record.get('items')
        _validate_items(items, month, seen)
        variable = spending_summary(items, living_only=True)
        total = spending_summary(items)
        frequency = dict.fromkeys((*CATEGORY_CODES, 'ETC', 'UNCLASSIFIED'), 0)
        for item in items:
            if (item['transaction_type'] == 'spending' and item['funding'] == 'living'
                    and item['net_amount'] > 0):
                frequency[item['category']] += 1
                if 'monthly_totals' not in record:
                    key = item.get('merchant_key')
                    if key is not None and not isinstance(key, str):
                        raise ValueError('invalid_merchant_key')
                    key = key.strip().casefold() if key else None
                    if item.get('transaction_id') is not None:
                        identity = str(item['transaction_id'])
                    else:
                        content = json.dumps([month, item['category'], item['net_amount'], key,
                                              str(item['occurred_on'])], ensure_ascii=False)
                        occurrence = content_counts.get(content, 0)
                        content_counts[content] = occurrence + 1
                        identity = json.dumps([content, occurrence], ensure_ascii=False)
                    transactions.append({'id': 'oneoff_' + sha256(identity.encode()).hexdigest(),
                        'month': month, 'category': item['category'], 'amount': item['net_amount'], 'merchant_key': key})
        if 'monthly_totals' in record:
            frequency = dict.fromkeys(frequency, None)
        monthly[month] = {
            'variable_spending': variable, 'all_spending': total,
            'purchase_counts': frequency, 'normalization_checks': checks,
        }
    index = overspending_index([months.get(month, {}) for month in expected])
    candidates = detect_one_offs(transactions) if not blockers and not totals_only else []
    ids = {c['id'] for c in candidates}
    stale_reincluded_ids = [identifier for identifier in user_reincluded_one_offs if identifier not in ids]
    if one_off_decisions is not None and (not isinstance(one_off_decisions, dict)
            or set(one_off_decisions) != ids or any(v not in ('one_off', 'recurring') for v in one_off_decisions.values())):
        raise ValueError('invalid_one_off_decisions')
    selected_baseline = 'median' if totals_only else baseline
    for candidate in candidates:
        candidate['reincluded_by_user'] = candidate['id'] in user_reincluded_one_offs
        candidate['decision'] = (one_off_decisions or {}).get(candidate['id'], 'one_off')
        candidate['excluded'] = (not candidate['excluded_by_recurrence'] and not candidate['reincluded_by_user']
                                 and candidate['decision'] == 'one_off')
    categories, discretionary_trend = {}, []
    if not blockers:
        for code in (*CATEGORY_CODES, 'ETC', 'UNCLASSIFIED'):
            amounts = [monthly[m]['variable_spending']['by_category'].get(code, 0) for m in expected]
            counts = [monthly[m]['purchase_counts'][code] for m in expected]
            middle = median(amounts)
            previous_average = Fraction(amounts[0] + amounts[1], 2)
            categories[code] = {
                'monthly_amounts': amounts, 'average': Fraction(sum(amounts), 3),
                'median': middle, 'previous_two_month_average': previous_average,
                'latest_growth_rate': (Fraction(amounts[2]) / previous_average - 1
                                       if previous_average else None),
                'latest_growth_reason': None if previous_average else 'zero_previous_average',
                'max_to_median_ratio': Fraction(max(amounts), middle) if middle else None,
                'spike_ratio_reason': None if middle else 'zero_median',
                'month_changes': [amounts[1] - amounts[0], amounts[2] - amounts[1]],
                'monthly_purchase_counts': counts, 'months_with_spending': sum(v > 0 for v in amounts),
                'detail': ({'reason': 'too_few_transactions'}
                           if not totals_only and code in CATEGORY_CODES
                           and 0 < sum(counts) < DOMINANCE_MIN_TRANSACTIONS else {}),
            }
            adjusted = [amount - sum(c['amount'] for c in candidates
                        if c['category'] == code and c['month'] == m and c['excluded'])
                        for m, amount in zip(expected, amounts)]
            adjusted_average = Fraction(sum(adjusted), 3)
            values = {'average': Fraction(sum(amounts), 3), 'median': Fraction(middle),
                      'one_off_adjusted': adjusted_average}
            categories[code].update(adjusted_monthly_amounts=adjusted, one_off_adjusted_average=adjusted_average,
                                    baseline=selected_baseline, baseline_amount=values[selected_baseline])
        for month in expected:
            spending = monthly[month]['variable_spending']
            amount = sum(spending['by_category'].get(c, 0) for c in DISCRETIONARY)
            total = spending['spent_total']
            discretionary_trend.append({'month': month, 'amount': amount, 'total': total,
                'share': Fraction(amount, total) if total else None,
                'reason': None if total else 'zero_variable_spending'})
    return {'status': 'needs_data' if blockers else 'ready', 'months': expected,
            'baseline': selected_baseline, 'one_off_candidates': candidates,
            'user_reincluded_one_offs': list(user_reincluded_one_offs),
            'stale_reincluded_ids': stale_reincluded_ids,
            'one_off_unavailable': 'monthly_totals_only' if totals_only else None,
            'baseline_totals': {kind: sum((row[field] for row in categories.values()), Fraction(0))
                for kind, field in (('average', 'average'), ('median', 'median'), ('one_off_adjusted', 'one_off_adjusted_average'))},
            'missing_data': blockers, 'monthly': monthly, 'categories': categories,
            'discretionary_share_trend': discretionary_trend,
            'overspending_index': index}
