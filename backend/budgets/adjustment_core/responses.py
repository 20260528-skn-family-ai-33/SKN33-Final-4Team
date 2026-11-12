"""One presentation contract for every budget dialogue result; no model calls or calculations."""
import re

from .explanations import NAMES, status_explanation


CONDITION_STEPS = {'unlock', 'partial', 'financial_partial', 'financial_risk', 'fixed_expense', 'financial_data'}


def focus(state, index=None):
    items = state.get('requests', [])
    index = index if index is not None else (state.get('pending') or {}).get('index', state.get('focus', 0))
    return items[index] if isinstance(index, int) and 0 <= index < len(items) else {}


def question(kind, state, fallback, index=None):
    item = focus(state, index)
    name = NAMES.get(item.get('category'), '해당 항목')
    direction = item.get('direction')
    if kind == 'category':
        return '어떤 예산을 조정하고 싶으세요?'
    if kind == 'direction':
        return f'{name} 예산을 늘릴까요, 줄일까요?'
    if kind == 'amount':
        if item.get('amount_mode') in {'to', 'percent_to'}:
            return f'{name} 예산을 얼마로 맞출까요?'
        if item.get('amount_mode') == 'remaining':
            return f'{name}에서 앞으로 쓸 돈이 얼마 남도록 맞출까요?'
        return f"{name} 예산을 얼마 {'늘릴까요' if direction == 'up' else '줄일까요' if direction == 'down' else '조정할까요'}?"
    if kind == 'amount_mode':
        return f'{name} 예산을 말씀한 금액만큼 바꿀까요, 전체 금액을 그만큼으로 맞출까요?'
    if kind == 'sources' or kind == 'funding_scope' and direction == 'up':
        return f'{name}에 더할 돈을 어디에서 가져올까요?'
    if kind in {'destinations', 'funding_scope'}:
        return ('생활비에 더할 돈을 어느 항목에 넣을까요?' if item.get('category') == 'LIVING' and direction == 'up'
                else f'{name}에서 줄일 돈을 어디에 넣을까요?')
    if kind == 'scope':
        return '이번 예산만 바꿀까요, 다음 예산에도 유지할까요?'
    return fallback


def request_context(state):
    items = state.get('requests', [])
    item = focus(state)
    if len(items) > 1 and (state.get('pending') or {}).get('index') is None:
        return ' · '.join(NAMES.get(row.get('category'), '항목 확인 중') for row in items) + ' 조정'
    name = NAMES.get(item.get('category'))
    if not name:
        return ''
    direction = {'up': '늘리기', 'down': '줄이기'}.get(item.get('direction'), '조정')
    amount, mode = item.get('amount'), item.get('amount_mode')
    if amount is None:
        return f'{name} {direction}'
    suffix = {'by': '원만큼', 'to': '원으로', 'remaining': '원이 남도록',
              'percent_by': '%만큼', 'percent_to': '%로'}.get(mode)
    return f'{name} {amount:,}{suffix} {direction}' if suffix else f'{name} {direction}'


def guidance(result, state):
    """Question + context; exceptions add a reason and action, explanations only on request."""
    reply, pending = result.get('reply', {}), state.get('pending') or {}
    explanation = reply.get('explanation') or {}
    out = {'kind': 'notice', 'context': request_context(state), 'message': '', 'reason': '', 'next': '', 'details': []}
    seen = []

    def fresh(text):
        parts = []
        for part in re.split(r'(?<=[.!?])\s+|\n+', text or ''):
            part = ' '.join(part.split())
            if part and not any(part in prior for prior in seen):
                seen.append(part)
                parts.append(part)
        return ' '.join(parts)

    if result.get('error'):
        facts = status_explanation(result['status'], error=result['error'])
        out.update(kind='error', message=fresh(facts.get('why')), next=fresh(facts.get('next')))
        # A failed save/check is different from a failed interpretation/calculation.
        if result['error'] in {'task_check_failed', 'invalid_saved_ref'}:
            out['reason'] = fresh(facts.get('effect'))
        return out
    out['message'] = '\n'.join(line for value in reply.get('lines', []) if (line := fresh(value)))
    if reply.get('detail') or reply.get('recommendation_advice'):
        out['kind'] = 'explanation'
        out['details'] = [text for key in ('why', 'effect', 'next') if (text := fresh(explanation.get(key)))]
    elif result['status'] == 'ended':
        out['kind'] = 'complete'
        out['context'] = ''
    elif reply.get('proposal'):
        out['kind'] = 'preview'
    elif reply.get('comparison'):
        out['kind'] = 'comparison'
    elif pending:
        out['kind'] = 'question'
    if explanation.get('brief'):
        out['reason'] = fresh(explanation['brief'])
        out['details'] += [text for key in ('why', 'next') if (text := fresh(explanation.get(key)))]
    if pending.get('kind') in CONDITION_STEPS:
        out['reason'] = fresh(explanation.get('why')) or out['reason']
        if pending['kind'] in {'unlock', 'financial_risk', 'partial', 'financial_partial'}:
            out['next'] = fresh(explanation.get('effect'))
    if result['status'] == 'failed' and out['kind'] == 'notice':
        out['kind'] = 'error'
    return out
