"""Server-only category explanations; signed ledger amounts reconcile exactly."""
from copy import deepcopy
from fractions import Fraction

from .recommend import EngineCheckFailed

# User decision: suppress small category adjustments, retaining the full ledger.
ADJUSTMENT_NOTICE_THRESHOLD = 50_000
PRIORITY_REDUCTION_NOTICE_PERCENT = 30

CAUSE_CODES = frozenset((
    'shortfall_unranked_cut', 'shortfall_low_rank_cut', 'shortfall_top_rank_cut',
    'cut_limit_reached', 'priority_transfer_out', 'priority_transfer_in',
    'user_minimum_raise', 'system_floor_raise', 'system_floor_protected',
    'lock_fixed', 'user_protected', 'inactive_category', 'rounding', 'ml_base_ratio', 'constraint_redistribution',
    'one_off_excluded', 'history_baseline', 'unclassified_candidate_adjustment',
))


def money(value):
    value = Fraction(value)
    rounded = int(abs(value) + Fraction(1, 2))
    return ('약 ' if value.denominator != 1 else '') + f'{rounded:,}'


def priority_reduction_notices(explanations, allocations, priorities, locks, history, names):
    """Compare the final displayed budget with a verified history baseline."""
    if not isinstance(history, dict) or history.get('status') != 'ready':
        return explanations
    ranks = {r['category']: r['rank'] for r in priorities}
    rows = history.get('categories', {})
    for entry in explanations:
        category = entry['category']
        rank = ranks.get(category)
        if rank not in (1, 2, 3) or category in locks or category not in rows:
            continue
        basis = Fraction(rows[category]['baseline_amount'])
        final = allocations[category]
        if basis <= 0 or final >= basis:
            continue
        if (basis - final) * 100 > basis * PRIORITY_REDUCTION_NOTICE_PERCENT:
            entry['text'] = f"{names[category]}({rank}순위): 평소 기준보다 {money(basis - final)}원 줄었어요."
        else:
            entry['text'] = ''
    return explanations


def _conflict_text(category, final, causes, rank, stop_reason, names, reference_type, reference):
    if abs(final - reference) <= ADJUSTMENT_NOTICE_THRESHOLD:
        return ''
    messages = []
    one_offs = [c for c in sorted(causes, key=lambda c: -abs(c['amount'])) if c['code'] == 'one_off_excluded']
    if one_offs:
        # Explicit exception to conflict-only text. The ledger retains all remaining events.
        descriptions = []
        for cause in one_offs:
            detail = cause['detail']
            why = (f"3개월 동안의 다른 {names[category]} 결제를 모두 합친 것보다 커서"
                   if detail['method'] == 'dominance' else '평소 결제보다 크게 벗어나')
            descriptions.append((abs(cause['amount']), f"{int(detail['month'].split('-')[1])}월 {money(detail['amount'])}원 결제 1건이 {why}, 한 번만 있었던 지출로 보고 월 예산 기준에서 뺐어요."))
        other_causes = [c for c in causes if c['code'] != 'one_off_excluded']
        conflict = _conflict_text(category, final, other_causes, rank, stop_reason, names, reference_type, reference)
        if conflict:
            magnitude = max((abs(c['amount']) for c in other_causes if c['code'] in
                             ('shortfall_top_rank_cut', 'priority_transfer_out', 'constraint_redistribution')), default=0)
            descriptions.append((magnitude, conflict.split(': ', 1)[1]))
        descriptions.sort(key=lambda pair: -pair[0])
        return f"{names[category]}: " + ' '.join(text for _, text in descriptions[:2]) + ' 매달 나가는 돈이라면 다시 포함할 수 있어요.'
    if rank is not None and rank <= 3:
        redistributed = [c for c in causes if c['code'] == 'constraint_redistribution' and c['amount'] < 0]
        labels = {'system_floor': '하한', 'user_minimum': '최소 금액', 'lock': '자물쇠 금액'}
        constraints = list(dict.fromkeys(
            ('교통' if item['category'] == 'TRANSPORT_CAR' else names[item['category']]) + ' ' + labels[item['type']]
            for cause in redistributed for item in cause['detail'].get('constraints', [])
            if item['category'] != category and item['type'] in labels))
        if constraints:
            amount = -sum(c['amount'] for c in redistributed)
            displayed = money(amount)
            if amount < 1000 and not displayed.startswith('약 '):
                displayed = '약 ' + displayed
            conditions = '과 '.join(constraints[:2]) + (' 등' if len(constraints) > 2 else '')
            if amount > ADJUSTMENT_NOTICE_THRESHOLD:
                messages.append(conditions + f'을 확보하느라 {displayed}원 줄었어요.')
    for cause in sorted(causes, key=lambda c: -abs(c['amount'])):
        if cause['code'] == 'shortfall_top_rank_cut':
            if cause['detail'].get('reducible_before') == [category]:
                messages.append(f'{names[category]} 말고는 더 줄일 수 있는 항목이 없었어요.')
                continue
            reason = '생활비가 부족해'
            if reference_type == 'history_average' and reference > final:
                messages.append(f'{reason} 지난 3개월 평균보다 {money(reference - final)}원 줄였어요.')
            elif reference_type == 'history_average' and reference < final:
                messages.append(f'{reason} 일부 감액했지만 지난 3개월 평균보다는 {money(final - reference)}원 많아요.')
            else:
                messages.append(f'{reason} 일부 감액했어요.')
        elif (cause['code'] == 'priority_transfer_out' and rank is not None and rank <= 3
              and abs(cause['amount']) > ADJUSTMENT_NOTICE_THRESHOLD):
            detail = cause['detail']
            messages.append(f"{detail['receiver_rank']}순위 {names[detail['counterparty']]} 예산을 확보하느라 {money(cause['amount'])}원 줄였어요.")
    if stop_reason == 'no_funding':
        messages.append('옮길 수 있는 금액이 부족해 더 늘리지 못했어요.')
    elif stop_reason == 'total_limit':
        messages.append('전체 이동 한도에 걸려 더 늘리지 못했어요.')
    if not messages:
        return ''
    rank_text = f'({rank}순위)' if rank is not None else ''
    return f"{names[category]}{rank_text}: {messages[0]}"


def explain_allocations(references, allocations, ledger, *, reference_type, priorities, names, stop_reasons=None):
    if reference_type not in ('history_average', 'ml_share') or set(references) != set(allocations) or set(ledger) != set(allocations):
        raise EngineCheckFailed('invalid_explanation_reference')
    ranks = {row['category']: row['rank'] for row in priorities}
    result = []
    for category, final in allocations.items():
        reference = references[category]
        causes = deepcopy(ledger[category])
        if any(c['code'] not in CAUSE_CODES for c in causes):
            raise EngineCheckFailed('invalid_explanation_cause')
        diff = final - reference
        if sum((c['amount'] for c in causes), Fraction(0)) != diff:
            raise EngineCheckFailed('explanation_ledger_mismatch')
        text = _conflict_text(category, final, causes, ranks.get(category),
                              (stop_reasons or {}).get(category), names, reference_type, reference)
        result.append({'category': category, 'reference_type': reference_type,
                       'reference_amount': reference, 'final_amount': final, 'diff': diff,
                       'causes': causes, 'text': text})
    return result


def transfer_ledger(ledger, transfers, priorities):
    ledger = deepcopy(ledger)
    ranks = {row['category']: row['rank'] for row in priorities}
    for transfer in transfers:
        donor, receiver, amount = transfer['from'], transfer['to'], transfer['amount']
        for category, other, code, delta in ((donor, receiver, 'priority_transfer_out', -amount),
                                            (receiver, donor, 'priority_transfer_in', amount)):
            ledger[category].append({'code': code, 'amount': delta,
                'detail': {'counterparty': other, 'rank': ranks.get(other), 'receiver_rank': ranks[receiver]}})
    return ledger


def allocation_ledger(references, audit, inp, floors, *, reference_unavailable=False):
    """Explain observed allocator snapshots without recalculating the budget."""
    ledger = {c: [] for c in references}
    previous = dict(references)
    labels = {'inactive': '월 예산 제외', 'lock': '자물쇠', 'minimum': '최소금액·시스템 하한', 'minimum_rounding': '금액 단위 내림 후 최소금액·시스템 하한'}
    for snapshot, fixed in audit:
        constraints = []
        for c, kind in fixed.items():
            increase = snapshot[c] - previous[c]
            if increase <= 0:
                continue
            if kind == 'lock':
                constraints.append({'category': c, 'type': 'lock', 'amount': increase})
            elif kind in ('minimum', 'minimum_rounding'):
                user_raise = min(max(0, inp['minimums'].get(c, 0) - previous[c]), increase)
                if user_raise:
                    constraints.append({'category': c, 'type': 'user_minimum', 'amount': user_raise})
                if increase > user_raise:
                    constraints.append({'category': c, 'type': 'system_floor', 'amount': increase - user_raise})
        for category in references:
            delta = snapshot[category] - previous[category]
            kind = fixed.get(category)
            detail = {'reference_unavailable': True} if reference_unavailable else {}
            entries = []
            if kind == 'inactive':
                entries = [('inactive_category', delta, {'reference_amount': references[category]})]
            elif kind == 'lock':
                entries = [('lock_fixed', delta, {'locked': snapshot[category]})]
            elif kind in ('minimum', 'minimum_rounding') and (delta >= 0 or kind == 'minimum_rounding'):
                basis = previous[category]
                if kind == 'minimum_rounding':
                    rounded = int(basis // 10000) * 10000
                    if rounded != basis:
                        entries.append(('rounding', rounded - basis, {'unit': 10000, 'stage': 'R-05 before minimum'}))
                    basis = rounded
                minimum = inp['minimums'].get(category, 0)
                increase = snapshot[category] - basis
                user_raise = min(max(0, minimum - basis), max(0, increase))
                if user_raise or (minimum and not increase):
                    entries.append(('user_minimum_raise', user_raise, {'minimum': minimum}))
                if increase - user_raise:
                    entries.append(('system_floor_raise', increase - user_raise, {'floor': floors[category]}))
            elif kind == 'rounding':
                if delta:
                    entries = [('rounding', delta, {'unit': 10000, 'stage': 'R-05'})]
            elif delta:
                entries = [('constraint_redistribution', delta, {
                    'redistribution': True, 'categories': list(fixed),
                    'constraint': '·'.join(dict.fromkeys(labels[k] for k in fixed.values())),
                    'conditions': dict(fixed), 'constraints': deepcopy(constraints),
                    'minimums': {c: inp['minimums'][c] for c in fixed if c in inp['minimums']},
                    'system_floors': {c: floors[c] for c in fixed if c in floors},
                    'locks': {c: inp['locks'][c] for c in fixed if c in inp['locks']}})]
            for code, amount, context in entries:
                ledger[category].append({'code': code, 'amount': amount, 'detail': {**detail, **context}})
        previous = dict(snapshot)
    return ledger
