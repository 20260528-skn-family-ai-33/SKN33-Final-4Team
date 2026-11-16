"""증액 요청의 재원 추천. 계산은 모두 simulate_funding 이 하고, 여기서는 순서와 설명만 정한다.

재원 순서 (사용자 확정 정책)
1. 다른 생활비 카테고리끼리 옮긴다. 1~6순위·잠금·최소금액·이미 쓴 돈·평소 소비(protection.py)는 지킨다.
2. 생활비로 다 못 채우면 부족분만 여윳돈에서 가져온다. 여윳돈은 30% 이상 남긴다.
3. 그래도 모자라면 월 머니쿠션, 마지막으로 저축을 줄인다. 저축을 쓰는 안은 추천하지 않고 대안으로만 보여 준다.
보여 주는 안은 예시이고, 사용자가 고른 뒤 확정해야 예산이 바뀐다.
"""
from copy import deepcopy

from .calculation import AdjustmentError, NON_BUDGET_CODES
from .funding import BUCKETS, LIVING, _data, _value, available_adjustment, available_bucket_source, simulate_funding
from .protection import expected_need, has_history, usage_floors

SOURCE_NAMES = {LIVING: '다른 생활비', 'SPARE': '여윳돈', 'CUSHION_MONTHLY': '월 머니쿠션', 'SAVINGS': '저축'}
TARGET_NAMES = {LIVING: '생활비', 'SPARE': '여윳돈', 'CUSHION_MONTHLY': '월 머니쿠션', 'SAVINGS': '저축'}
ORDER = (LIVING, 'SPARE', 'CUSHION_MONTHLY', 'SAVINGS')
TIERS = (('living', 1), ('spare', 2), ('cushion', 3), ('savings', 4))
PLAN_IDS = {'living', 'spare', 'cushion', 'savings', 'spare_only', 'chosen', 'extended', 'combined'}


def money(amount):
    """화면에 쓰는 짧은 금액: 100000 → '10만 원', 125000 → '12만 5천 원', 12345 → '12,345원'."""
    if amount >= 10000 and amount % 1000 == 0:
        man, rest = divmod(amount, 10000)
        return f"{man:,}만 원" if not rest else f"{man:,}만 {rest // 1000}천 원"
    return f"{amount:,}원"


def josa(word, pair):
    """받침에 맞는 조사. pair='은는'/'이가'/'을를'/'과와'/'으로로'."""
    if not word:
        return pair[1]
    last = word.rstrip()[-1]
    if not '가' <= last <= '힣':
        return pair[0] if last in '013678' else pair[1]
    final = (ord(last) - 0xAC00) % 28
    if pair == '으로로':
        return '로' if final in (0, 8) else '으로'
    return pair[0] if final else pair[1]


def names_of(context):
    return {**context.get('category_names', {}), **TARGET_NAMES}


def _target_text(context, data):
    names = names_of(context)
    codes = list(data['targets'])
    return '·'.join(names.get(code, code) for code in codes)


def _requested(data):
    return {code: target - _value(data, data['before'], code) for code, target in data['targets'].items()}


def _used(context, result):
    used = dict.fromkeys(ORDER, 0)
    for row in result.get('transfers', []):
        used[LIVING if row['from'] in context['allocations'] else row['from']] += row['amount']
    return used


def usable_sources(context, sources, excluded):
    """확인되지 않았거나 쓰지 않기로 한 재원은 처음부터 계산에서 뺀다."""
    usable, skipped = [], []
    for code in sources:
        if code in excluded:
            continue
        if code in BUCKETS and context.get('bucket_status', {}).get(code) != 'ready':
            skipped.append(code)
            continue
        usable.append(code)
    return usable, skipped


def _facts(context, result, primary):
    facts, cautions, tight = [], [], []
    names = names_of(context)
    floors = usage_floors(context) or {}
    spent = context.get('spent', {})
    verified = result.get('spending_verified', True)
    for change in result['changes']:
        code, cut = change['category'], -change['diff']
        if cut <= 0 or code in primary:
            continue
        name = names.get(code, code)
        if code in context['allocations']:
            remaining = change['after'] - spent.get(code, 0) if verified else None
            facts.append({'kind': 'category_reduction', 'category': code, 'before': change['before'],
                          'after': change['after'], 'decrease': cut, 'spent': spent.get(code, 0), 'remaining': remaining})
            if change['after'] == 0:
                cautions.append(f"{name} 예산이 0원이 돼요.")
            elif floors.get(code) is not None and change['after'] <= floors[code]:
                tight.append(name)
        else:
            available = context.get('bucket_available', {}).get(code, 0)
            facts.append({'kind': 'bucket_reduction', 'category': code, 'before': change['before'], 'after': change['after'],
                          'decrease': cut, 'available_before': available, 'available_after': max(available - cut, 0)})
            if code == 'SAVINGS':
                cautions.append(f"이번 달 저축이 {money(cut)} 줄어요. 저축은 꼭 필요할 때만 줄이는 걸 권해요.")
            elif code == 'CUSHION_MONTHLY':
                cautions.append(f"이번 달 머니쿠션 적립이 {money(cut)} 줄어요. 이미 모은 쿠션은 그대로예요.")
    if tight:
        listed = '·'.join(tight)
        cautions.insert(0, f"{listed}{josa(listed, '은는')} 다음 급여일까지 평소 쓰는 만큼만 남아요.")
    return facts, cautions


def _summary(context, result, data):
    """카드 한 줄 설명: 어디에서 얼마를 가져오는지."""
    names = names_of(context)
    used = _used(context, result)
    parts = []
    if used[LIVING]:
        donors = []
        for row in result['transfers']:
            if row['from'] in context['allocations'] and names.get(row['from'], row['from']) not in donors:
                donors.append(names.get(row['from'], row['from']))
        listed = '·'.join(donors[:3]) + (' 등' if len(donors) > 3 else '')
        parts.append(f"{listed}에서 {money(used[LIVING])}")
    for code in ('SPARE', 'CUSHION_MONTHLY', 'SAVINGS'):
        if used[code]:
            parts.append(f"{SOURCE_NAMES[code]}에서 {money(used[code])}")
    line = ', '.join(parts) + '을 가져와요.' if parts else ''
    if used['SPARE']:
        left = max(context.get('bucket_available', {}).get('SPARE', 0) - used['SPARE'], 0)
        line += f" 여윳돈은 {money(left)} 남아요."
    missing = sum(row['missing_amount'] for row in result.get('shortfalls', []))
    if missing:
        line += f" {money(missing)}{josa(money(missing), '은는')} 부족해요."
    return line.strip()


def _label(context, result):
    used = _used(context, result)
    names = {LIVING: '생활비', 'SPARE': '여윳돈', 'CUSHION_MONTHLY': '월 머니쿠션', 'SAVINGS': '저축'}
    parts = [f"{names[code]} {money(amount)}" for code, amount in used.items() if amount]
    return ' + '.join(parts) if parts else '변경 없음'


def _simulate(context, envelope, sources, allocator):
    candidate = {'items': deepcopy(envelope['items']), 'sources': list(sources),
                 'destinations': list(envelope.get('destinations', [])), 'excluded': list(envelope.get('excluded', []))}
    if envelope.get('spare_minimum') is not None:
        candidate['spare_minimum'] = envelope['spare_minimum']
    try:
        result = simulate_funding(context, candidate, allocate_group_reduction=allocator)
    except AdjustmentError:
        return None
    if result['status'] not in ('proposed', 'partial') or not result.get('changes'):
        return None
    return result


def _plan(context, plan_id, result, data, primary):
    requested = sum(_requested(data).values())
    missing = sum(row['missing_amount'] for row in result.get('shortfalls', []))
    facts, cautions = _facts(context, result, primary)
    return {'id': plan_id, 'scope': plan_id, 'label': _label(context, result),
            'status': 'ready' if result['status'] == 'proposed' else 'partial',
            'amount': requested - missing, 'maximum': None, 'result': result,
            'envelope': deepcopy(result['requests']), 'recommended': False, 'sources': _used(context, result),
            'assessment': {'label': '대안', 'reasons': [_summary(context, result, data)], 'cautions': cautions, 'facts': facts},
            'lines': []}


def _living_note(context, data):
    if usage_floors(context) is None:
        return '남은 기간을 확인하지 못해 다른 생활비는 자동으로 줄이지 않았어요. 직접 골라 줄일 수는 있어요.'
    if not has_history(context):
        return '최근 소비 기록이 부족해 남은 기간에 예산만큼 쓸 돈을 남기면 다른 생활비에서 더 옮길 여유가 없어요.'
    return '다른 생활비는 중요 항목(1~6순위)과 다음 급여일까지 쓸 돈을 남기면 더 줄일 여유가 없어요.'


def _headline(context, plan, data, requested):
    target = _target_text(context, data)
    used = plan['sources']
    if plan['status'] == 'partial':
        missing = requested - plan['amount']
        return (f"지금 옮길 수 있는 돈은 최대 {money(plan['amount'])}이에요. "
                f"요청한 {money(requested)} 중 {money(missing)}{josa(money(missing), '이가')} 부족해요.")
    others = [(code, used[code]) for code in ('SPARE', 'CUSHION_MONTHLY', 'SAVINGS') if used[code]]
    if not others:
        kept = '·'.join(TARGET_NAMES[code] for code in ('SPARE', 'CUSHION_MONTHLY', 'SAVINGS') if code not in data['targets'])
        return (f"{target} {money(requested)}{josa(money(requested), '은는')} 다른 생활비에서 옮길 수 있어요. "
                f"{kept}{josa(kept, '은는')} 그대로 둬요.")
    if used[LIVING]:
        rest = requested - used[LIVING]
        where = (SOURCE_NAMES[others[0][0]] if len(others) == 1 else
                 '·'.join(f"{SOURCE_NAMES[code]} {money(amount)}" for code, amount in others))
        return (f"다른 생활비에서 {money(used[LIVING])}{josa(money(used[LIVING]), '을를')} 옮기고, "
                f"모자란 {money(rest)}{josa(money(rest), '은는')} {where}에서 채울 수 있어요.")
    where = '·'.join(f"{SOURCE_NAMES[code]} {money(amount)}" for code, amount in others)
    return f"{target} {money(requested)}{josa(money(requested), '은는')} {where}에서 가져올 수 있어요."


def plan_increase(context, envelope, allocator, *, respect_selected=False):
    """같은 요청을 재원 순서대로 계산해 추천 1개와 의미 있는 대안만 돌려준다."""
    data = _data(context, envelope)
    requested_by = _requested(data)
    if not requested_by or any(amount <= 0 for amount in requested_by.values()):
        raise AdjustmentError('increase_required')
    requested = sum(requested_by.values())
    primary = set(data['targets'])
    excluded = set(envelope.get('excluded', []))
    chosen = [code for code in envelope.get('sources', []) if code in ORDER or code in context['allocations']]
    order = [code for code in ORDER if code not in primary and not (code == LIVING and LIVING in primary)]
    usable, skipped = usable_sources(context, order, excluded)
    plans, notes = [], []
    if respect_selected and chosen:
        picked, _ = usable_sources(context, chosen, excluded)
        result = _simulate(context, envelope, picked, allocator) if picked else None
        if result is not None:
            plans.append(_plan(context, 'chosen', result, data, primary))
    else:
        seen = []
        for plan_id, size in TIERS:
            tier = [code for code in ORDER[:size] if code in usable]
            if not tier or tier in seen:
                continue
            seen.append(tier)
            result = _simulate(context, envelope, tier, allocator)
            if result is None or any(p['result']['transfers'] == result['transfers'] for p in plans):
                continue
            plans.append(_plan(context, plan_id, result, data, primary))
            if result['status'] == 'proposed':
                break
    recommended = None
    full = [p for p in plans if p['status'] == 'ready']
    safe = [p for p in plans if not p['sources']['SAVINGS']]
    if full and not full[0]['sources']['SAVINGS']:
        recommended = full[0]
    elif safe:
        recommended = max(safe, key=lambda p: p['amount'])
    elif plans:
        recommended = plans[-1]
    shown = []
    if recommended is not None:
        recommended.update(recommended=True)
        recommended['assessment']['label'] = '추천'
        shown.append(recommended)
        # 대안: 생활비만 쓰는 부분안, 또는 저축까지 써야 전액이 되는 안
        for plan in reversed(plans):
            if plan is recommended or len(shown) >= 3:
                continue
            if plan['amount'] > 0 and (plan['id'] == 'living' or plan['amount'] > recommended['amount']):
                shown.append(plan)
        # 생활비만으로 채울 수 있으면 여윳돈·저축·월 머니쿠션을 쓰는 대안은 만들지 않는다(정책).
    if recommended is not None:
        recommended['assessment']['reasons'] = [recommended['assessment']['reasons'][0], *why_recommended(context, recommended)]
    for plan in shown:
        if plan is not recommended and plan['sources']['SAVINGS']:
            plan['assessment']['cautions'].insert(0, '저축까지 줄여야 하는 방법이라 추천하지 않아요.')
    if skipped:
        names = '·'.join(SOURCE_NAMES[code] for code in skipped)
        notes.append(f"{names}{josa(names, '은는')} 이번 달 사용 가능액을 확인하지 못해 계산에서 뺐어요.")
    if recommended is not None and not recommended['sources'][LIVING] and LIVING in usable and not respect_selected:
        notes.append(_living_note(context, data))
    headline = (_headline(context, recommended, data, requested) if recommended is not None else
                '지금 조건으로는 옮길 수 있는 돈이 없어요. 금액을 줄이거나 줄여도 되는 생활비를 직접 골라 주세요.')
    return {'status': 'ready' if shown else 'unavailable', 'basis_amount': requested,
            'amount_was_given': True, 'basis_kind': 'requested', 'plans': shown, 'headline': headline,
            'notes': notes, 'notice': ' '.join(notes) or None,
            'recommendation': {'plan_id': recommended['id'] if recommended else None,
                               'reasons': recommended['assessment']['reasons'] if recommended else []},
            'message': headline}


def capacities(context, category, *, excluded=(), destinations=(), allocator=None, sources=None, spare_minimum=None):
    """재원별 최대 가능액(권장액 아님). 0원인 재원도 이유 설명에 쓰도록 남긴다."""
    out = {}
    for source in ORDER:
        if source == category or (source == LIVING and category == LIVING) or source in excluded:
            continue
        if sources and source not in sources:
            continue
        if source in BUCKETS and context.get('bucket_status', {}).get(source) != 'ready':
            continue
        quote = available_adjustment(context, category, 'up', sources=[source], destinations=list(destinations),
                                     excluded=list(excluded), allocate_group_reduction=allocator,
                                     spare_minimum=spare_minimum)
        out[source] = quote['maximum'] if quote['status'] == 'ready' else 0
    return out


def capacity_line(context, category, found):
    parts = [f"{SOURCE_NAMES[code]} 최대 {money(amount)}" for code, amount in found.items()
             if amount and code != 'SAVINGS']
    if not parts:
        if found.get('SAVINGS'):
            return (f"다른 생활비·여윳돈·월 머니쿠션에서는 옮길 돈이 없고, 저축에서는 최대 {money(found['SAVINGS'])}까지 가능해요. "
                    '저축은 꼭 필요할 때만 줄이는 걸 권해요.')
        return '지금은 다른 생활비·여윳돈·월 머니쿠션에서 옮길 수 있는 돈이 없어요.'
    line = '지금 옮길 수 있는 돈은 ' + ', '.join(parts) + '이에요.'
    if found.get('SPARE'):
        line += ' 여윳돈은 30% 이상 남겨요.'
    return line


def suggested_amount(context, category):
    """최근 소비 속도로 본 부족액(1만 원 단위 올림). 근거가 없거나 부족하지 않으면 None/0."""
    if category not in context.get('allocations', {}) or category in NON_BUDGET_CODES:
        return None
    need = expected_need(context, category)
    if need is None:
        return None
    return -(-need // 10000) * 10000


def shortfall_categories(context, limit=2):
    """생활비 전체가 부족하다고 할 때, 최근 속도로 모자랄 것 같은 항목."""
    rows = []
    for code in context.get('allocations', {}):
        if code in NON_BUDGET_CODES or code in context.get('locks', {}):
            continue
        amount = suggested_amount(context, code)
        if amount:
            rows.append((code, amount))
    rows.sort(key=lambda row: -row[1])
    return rows[:limit]


def impact_line(context, items, allocator=None):
    """큰 증액 경고에 붙이는 근거: 생활비만으로 되는지, 무엇을 줄여야 하는지."""
    from .calculation import proportional_group_reduction
    try:
        envelope = {'items': deepcopy(items), 'sources': [], 'destinations': [], 'excluded': []}
        data = _data(context, envelope)
        if any(code == LIVING for code in data['targets']):
            found = capacities(context, LIVING, allocator=allocator or proportional_group_reduction)
            spare = found.get('SPARE', 0)
            return (f"여윳돈에서는 최대 {money(spare)}까지만 가져올 수 있어서, 나머지는 월 머니쿠션이나 저축을 줄여야 해요."
                    if spare else '여윳돈에서 더 가져올 수 있는 돈이 없어서 월 머니쿠션이나 저축을 줄여야 해요.')
        comparison = plan_increase(context, envelope, allocator or proportional_group_reduction)
    except AdjustmentError:
        return ''
    living = next((p for p in comparison['plans'] if p['id'] == 'living'), None)
    living_amount = living['amount'] if living else 0
    best = max((p['amount'] for p in comparison['plans']), default=0)
    requested = comparison['basis_amount']
    if best < requested:
        return (f"다른 생활비·여윳돈·월 머니쿠션·저축을 모두 줄여도 {money(requested - best)}{josa(money(requested - best), '이가')} 부족해요."
                if best else '지금 예산에서는 옮길 수 있는 돈이 없어요.')
    if living_amount >= requested:
        return ''
    return (f"다른 생활비에서는 최대 {money(living_amount)}만 옮길 수 있어서, 나머지는 여윳돈·월 머니쿠션·저축을 줄여야 해요."
            if living_amount else '다른 생활비에서는 더 줄일 여유가 없어서, 여윳돈·월 머니쿠션·저축을 줄여야 해요.')


def living_sources(context, excluded=()):
    """생활비 전체 증액에 쓸 수 있는 여윳돈·월 머니쿠션·저축의 최대 가능액."""
    out = {}
    for code in ('SPARE', 'CUSHION_MONTHLY', 'SAVINGS'):
        if code in excluded or context.get('bucket_status', {}).get(code) != 'ready':
            continue
        try:
            quote = available_bucket_source(context, code, excluded=list(excluded))
        except AdjustmentError:
            continue
        out[code] = quote['maximum'] or 0
    return out


def why_recommended(context, plan):
    """추천 이유: 정책 순서대로 무엇을 지켰는지."""
    used, reasons = plan['sources'], []
    if used[LIVING] and not any(used[code] for code in ('SPARE', 'CUSHION_MONTHLY', 'SAVINGS')):
        reasons.append('다른 생활비 안에서만 옮겨서 여윳돈·월 머니쿠션·저축은 그대로예요.')
    elif used[LIVING]:
        reasons.append('다른 생활비에서 옮길 수 있는 만큼 먼저 옮기고, 모자란 돈만 채워요.')
    if any(rank <= 6 for code, rank in context.get('priorities', {}).items() if code in context['allocations']):
        reasons.append('중요하게 고른 1~6순위 항목은 줄이지 않아요.')
    if used['SPARE']:
        reasons.append('여윳돈은 30% 이상 남겨요.')
    if not used['SAVINGS'] and plan['status'] == 'partial':
        reasons.append('저축은 지키면서 옮길 수 있는 가장 큰 금액이에요.')
    return reasons


def unavailable_reasons(context, envelope):
    """자동 추천이 없을 때 재원별로 왜 비었는지(사용자 말로)."""
    excluded = set(envelope.get('excluded', []))
    if context.get('spending_status', 'complete') != 'complete' and not context.get('budget_preview'):
        return ['지금까지 쓴 돈을 모두 확인하지 못해 옮길 수 있는 돈을 계산하지 못했어요.']
    lines = []
    if LIVING in excluded:
        lines.append('다른 생활비: 이번에는 쓰지 않기로 했어요.')
    elif usage_floors(context) is None:
        lines.append('다른 생활비: 남은 기간을 확인하지 못해 자동으로 줄이지 않았어요.')
    elif not has_history(context):
        lines.append('다른 생활비: 남은 기간에 예산만큼 쓸 돈을 남기면 줄일 여유가 없어요.')
    else:
        lines.append('다른 생활비: 1~6순위·잠금·최소금액·다음 급여일까지 쓸 돈을 지키면 줄일 여유가 없어요.')
    for code in ('SPARE', 'CUSHION_MONTHLY', 'SAVINGS'):
        name = SOURCE_NAMES[code]
        if code in excluded:
            lines.append(f"{name}: 이번에는 쓰지 않기로 했어요.")
        elif context.get('bucket_status', {}).get(code) != 'ready':
            lines.append(f"{name}: 이번 달 사용 가능액을 확인하지 못했어요.")
        else:
            lines.append(f"{name}: 남겨 둘 금액(30% 이상·최소금액)을 지키면 옮길 돈이 없어요.")
    return lines
