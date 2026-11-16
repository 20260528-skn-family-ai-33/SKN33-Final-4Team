"""승인된 예산 버전의 배정액 변화만 읽는다. 제안·예약·실제 저축 거래와 구분한다."""
from django.db import DatabaseError

from budgets.models import Budget, BudgetCycle, BudgetItem
from .reading import CATEGORY_NAMES


def _counts():
    return dict(increases=0, decreases=0, increase_amount=0, decrease_amount=0, net_change=0)


def _add(counts, delta):
    if delta:
        direction = 'increase' if delta > 0 else 'decrease'
        counts[direction + 's'] += 1
        counts[direction + '_amount'] += abs(delta)
        counts['net_change'] += delta


def for_user(user, today):
    """현재 급여 주기의 승인 이력 집계. ready의 0건과 unavailable을 구별한다."""
    try:
        cycles = list(BudgetCycle.objects.filter(user=user, status='active', starts_on__lte=today,
                                                ends_before__gt=today)
                      .values('cycle_id', 'starts_on', 'ends_before')[:2])
        if len(cycles) != 1:
            return {'state': 'unavailable', 'reason': 'overlapping_cycles' if cycles else 'no_active_cycle'}
        cycle = cycles[0]
        versions = list(Budget.objects.filter(user=user, cycle_id=cycle['cycle_id'],
                                              status__in=['approved', 'superseded'], approved_at__isnull=False,
                                              approved_at__date__lte=today)
                        .order_by('approved_at', 'version_no')
                        .values('budget_id', 'status', 'approved_at', 'version_no',
                                'validation_summary__adjustment__effective_on'))
        # 다음 주기 예약도 승인 시각과 superseded 상태를 갖는다. 현재 예산 변경으로 세지 않는다.
        versions = [v for v in versions if not v['validation_summary__adjustment__effective_on']]
        if not versions:
            return {'state': 'unavailable', 'reason': 'no_approved_history'}
        items = list(BudgetItem.objects.filter(user=user, budget_id__in=[v['budget_id'] for v in versions])
                     .values_list('budget_id', 'bucket', 'category_id', 'allocated_amount'))
    except DatabaseError:
        return {'state': 'unavailable', 'reason': 'db_error'}

    amounts = {v['budget_id']: {} for v in versions}
    for budget_id, bucket, code, amount in items:
        if type(amount) is not int or amount < 0:
            return {'state': 'unavailable', 'reason': 'invalid_allocations'}
        key = (bucket, code)
        amounts[budget_id][key] = amounts[budget_id].get(key, 0) + amount
    if any(not rows for rows in amounts.values()):
        return {'state': 'unavailable', 'reason': 'missing_allocations'}

    categories, savings, changed = {}, _counts(), 0
    for before, after in zip(versions, versions[1:]):
        left, right = amounts[before['budget_id']], amounts[after['budget_id']]
        keys = left.keys() | right.keys()
        changed += any(right.get(key, 0) != left.get(key, 0) for key in keys)
        # 생활비 합계가 늘어난 변경만 '추가 증액'(topups)으로 센다. 합계가 그대로면 항목끼리 옮긴 것이다
        living_net = sum(right.get(key, 0) - left.get(key, 0) for key in keys if key[0] == 'living')
        for bucket, code in keys:
            delta = right.get((bucket, code), 0) - left.get((bucket, code), 0)
            if bucket == 'living' and code in CATEGORY_NAMES and code not in ('ETC', 'UNCLASSIFIED') and delta:
                row = categories.setdefault(code, {'code': code, 'name': CATEGORY_NAMES[code], **_counts(), 'topups': 0})
                _add(row, delta)
                row['topups'] += delta > 0 and living_net > 0
        _add(savings, sum(amount for (bucket, _), amount in right.items() if bucket == 'savings')
             - sum(amount for (bucket, _), amount in left.items() if bucket == 'savings'))

    return {'state': 'ready', 'period': {'from': cycle['starts_on'].isoformat(),
                                       'to': cycle['ends_before'].isoformat(), 'as_of': today.isoformat()},
            'approved_revision_count': len(versions) - 1, 'change_count': changed,
            'categories': sorted(categories.values(), key=lambda row: (-(row['increases'] + row['decreases']), row['code'])),
            'savings': savings}
