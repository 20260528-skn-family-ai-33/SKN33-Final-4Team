"""LLM requests adjustments; server tools alone calculate money."""
from copy import deepcopy
from http.client import HTTPSConnection
import json
import os
from time import monotonic

from .design_candidates import CATEGORY_NAMES, _weights, _check_system_floors, apply_priority_decisions, build_candidates, select_candidate
from .recommend import CATEGORY_CODES, check_result
from .design_tracing import span, token_usage, trace_budget

MODEL = 'gpt-4o-mini'
TIMEOUT_SECONDS = 10
TOTAL_SECONDS = 30
SYSTEM_PROMPT = """사용자 맞춤 월 생활비 예산을 조정하는 에이전트입니다.
완성된 후보를 고르거나 설명 문장을 쓰는 역할이 아닙니다. adjust_budget 도구로
현재 검토 항목의 증액(increase), 현재 조정 상태 유지 후 다음 항목 검토(keep), 전체 종료(finish)를 결정하세요.
금액은 모두 원 단위 서버 값입니다. 직접 금액·비율을 만들거나 수정하지 마세요.
입력에는 총생활비, 기타 확보액, 기타 제외 배분액, ML 초안, 최소·잠금 반영 기본안,
현재 금액, 사용자 우선순위, 최소금액, 잠금, 증액률·증감액 한도·상한, 경고,
서버가 계산한 다음 증액의 예상 결과와 이동 내역이 있습니다. 예산의 절대 규모도 보세요.
ML 원안은 개인별 정답이 아닙니다. 최소금액은 이미 충족됐고 잠금은 변경할 수 없습니다.
상위 1~3순위를 높은 순서대로 검토합니다. 미입력 항목 먼저, 그다음 후순위에서 조달하고,
각 그룹에서 쇼핑·뷰티·문화를 먼저 줄이는 순서는 서버가 강제합니다.
그 재원이 부족할 때만 현재 항목보다 순위가 낮은 상위 쇼핑·뷰티·문화를 마지막 재원으로 씁니다.
상위의 쇼핑·뷰티·문화도 사용자 선호이므로 이름만 보고 배제하지 마세요.
여행·숙박은 월 예산에서 항상 제외하며 순위·최소금액·잠금을 받지 않습니다.
주거·통신은 월 예산에서 제외하며 최소금액·잠금을 받지 않습니다.
시스템 하한은 서버가 강제하며 잠금이 우선입니다. 사용자 최소금액과 구별하세요.
판단은 현재 항목의 선호 반영 효과와 실제로 감액되는 항목의 남는 금액을 함께 비교하세요.
미입력·후순위의 선택 지출에서 조달 가능하면 상위 선호 반영을 우선 고려하세요.
서버 한도는 허용 최대치이지 반드시 소진할 목표가 아닙니다. 추가 증액보다 기존 배분 유지가
사용자 조건에 적합하다고 판단하면 keep을 사용하세요. 다른 항목은 이어서 검토할 수 있습니다.
추가 조정 전체가 불필요할 때만 finish를 사용하세요. 경고를 이유로 최소금액을 임의 삭감하지 마세요.
과거 소비·개인 사정·통계 적정선은 제공되지 않으면 추측하지 마세요. 크다/작다의 임의 숫자 기준을 만들지 마세요.
increase는 미리보기의 이동을 서버에 요청합니다. keep은 앞서 발생한 감액을 되돌리는 뜻이 아닙니다.
도구 결과를 확인한 뒤 다음 항목을 판단하세요. 현재 category와 action만 보내세요."""


def _request_action(messages, category, api_key, timeout):
    with span('openai.adjust_budget', 'llm', inputs={'category': category},
              metadata={'ls_provider': 'openai', 'ls_model_name': MODEL}) as trace:
        return _call_openai(messages, category, api_key, timeout, trace)


def _call_openai(messages, category, api_key, timeout, trace):
    parameters = {'type': 'object', 'properties': {
        'category': {'type': 'string', 'enum': [category]},
        'action': {'type': 'string', 'enum': ['increase', 'keep', 'finish']},
    }, 'required': ['category', 'action'], 'additionalProperties': False}
    body = {
        'model': MODEL, 'temperature': 0, 'max_completion_tokens': 128, 'store': False,
        'messages': messages, 'parallel_tool_calls': False, 'tool_choice': 'required',
        'tools': [{'type': 'function', 'function': {
            'name': 'adjust_budget', 'description': '현재 우선순위 항목을 서버 규칙 안에서 조정합니다.',
            'strict': True, 'parameters': parameters}}],
    }
    connection = HTTPSConnection('api.openai.com', timeout=timeout)
    try:
        connection.request('POST', '/v1/chat/completions',
                           body=json.dumps(body).encode('utf-8'), headers={
                               'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('llm_http_status')
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('llm_response_too_large')
        payload = json.loads(raw)
        trace['usage_metadata'] = token_usage(payload.get('usage'))
        choice = payload['choices'][0]
        message = choice['message']
        if (choice['finish_reason'] != 'tool_calls' or message.get('refusal')
                or message.get('content') not in (None, '')):
            raise ValueError('llm_tool_call_required')
        calls = message.get('tool_calls')
        if not isinstance(calls, list) or len(calls) != 1:
            raise ValueError('one_tool_call_required')
        call = calls[0]
        if (call['type'] != 'function' or call['function']['name'] != 'adjust_budget'
                or not isinstance(call['id'], str) or not 1 <= len(call['id']) <= 256):
            raise ValueError('invalid_adjustment_tool')
        decision = json.loads(call['function']['arguments'])
        if (not isinstance(decision, dict) or set(decision) != {'category', 'action'}
                or decision['category'] != category or decision['action'] not in ('increase', 'keep', 'finish')):
            raise ValueError('invalid_adjustment_action')
        trace.update(decision)
        return {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': call['id'], 'type': 'function', 'function': {
                'name': 'adjust_budget', 'arguments': json.dumps(decision)}}]}
    finally:
        connection.close()


def _calculate(inp, ratios, policy, decisions, *, preview=False, reality_profile=None):
    with span('preview_budget' if preview else 'adjust_budget', 'tool',
              inputs={'decisions': decisions}) as trace:
        result = apply_priority_decisions(inp, ratios, policy=policy, decisions=decisions, reality_profile=reality_profile)
        if result.get('reason') == 'system_floor_unaffordable':
            return result
        trace.update(transfer_count=len(result['transfers']), pending_categories=result['pending_categories'])
        return result


@trace_budget
def choose_budget(inp, engine, *, ml_ratios, api_key=None, living_budget=None, other_reserved=0, reality_profile=None):
    """At most three decisions; failures discard partial work for a rule fallback."""
    inp, engine = deepcopy(inp), deepcopy(engine)
    if reality_profile is not None:
        previous_warnings = engine.get('warnings', [])
        engine = build_candidates(inp, ml_ratios, policy=engine.get('policy'), reality_profile=reality_profile)
        engine['warnings'].extend(w for w in previous_warnings if w['code'] == 'high_minimum' and w not in engine['warnings'])
    if engine.get('reason') == 'system_floor_unaffordable':
        return engine
    floors = engine.get('system_floors', {})
    for candidate in engine['candidates']:
        _check_system_floors(inp, candidate['result']['allocations'], floors)
    extra = {key: engine[key] for key in ('system_floors', 'floor_applied', 'floor_ignored_locked') if key in engine}
    selected = select_candidate(inp, engine['candidates'])
    fallback = {**extra, 'candidate_id': selected['candidate_id'], 'allocations': selected['result']['allocations'],
                'selection_source': 'rule_fallback', 'fallback': None, 'decisions': [],
                'explanations': selected['explanations']}
    if len(engine['candidates']) == 1:
        return {**fallback, 'selection_source': 'single_candidate'}
    if living_budget is None:
        living_budget = inp['target'] + other_reserved
    if (type(other_reserved) is not int or other_reserved < 0 or type(living_budget) is not int
            or living_budget != inp['target'] + other_reserved):
        raise ValueError('invalid_living_budget_context')
    key = api_key if api_key is not None else os.environ.get('OPENAI_API_KEY', '')
    if not key.strip():
        return {**fallback, 'fallback': 'missing_api_key'}
    base = next(c['result'] for c in engine['candidates'] if c['id'] == 'base')
    decisions = []
    deadline = monotonic() + TOTAL_SECONDS
    try:
        weights = _weights(ml_ratios)
        context = {
            'category_names': CATEGORY_NAMES, 'living_budget': living_budget,
            'other_reserved': other_reserved, 'allocation_budget': inp['target'],
            'ml_draft_amounts': {c: float(inp['target'] * weights[c] / sum(weights.values())) for c in CATEGORY_CODES},
            'base_allocations': base['allocations'], 'priorities': inp['priorities'],
            'minimums': inp['minimums'], 'locks': inp['locks'], 'policy': engine['policy'],
            'active_categories': engine['active_categories'], 'warnings': engine['warnings'],
            'system_floors': floors,
        }
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}]
        current = _calculate(inp, ml_ratios, engine['policy'], decisions, reality_profile=reality_profile)
        for _ in range(3):
            if not current['pending_categories'] or current['remaining_transfer_limit'] < 1000:
                break
            category = current['pending_categories'][0]
            preview = _calculate(inp, ml_ratios, engine['policy'],
                decisions + [{'category': category, 'action': 'increase'}], preview=True, reality_profile=reality_profile)
            if preview['allocations'] == current['allocations']:
                decisions.append({'category': category, 'action': 'keep'})
                current = _calculate(inp, ml_ratios, engine['policy'], decisions, reality_profile=reality_profile)
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError()
            messages.append({'role': 'user', 'content': json.dumps({
                'current_category': category,
                'current': {k: v for k, v in current.items() if k != 'explanations'},
                'increase_preview': {k: v for k, v in preview.items() if k != 'explanations'}})})
            message = _request_action(messages, category, key, min(TIMEOUT_SECONDS, remaining))
            if monotonic() > deadline:
                raise TimeoutError()
            call = message['tool_calls'][0]
            decision = json.loads(call['function']['arguments'])
            if decision['action'] == 'finish':
                break
            decisions.append(decision)
            current = _calculate(inp, ml_ratios, engine['policy'], decisions, reality_profile=reality_profile)
            messages.extend([message, {'role': 'tool', 'tool_call_id': call['id'],
                                       'content': json.dumps({k: v for k, v in current.items() if k != 'explanations'})}])
        with span('check_result', 'tool') as trace:
            check_result(inp, {**base, 'allocations': current['allocations']})
            _check_system_floors(inp, current['allocations'], floors)
            trace['valid'] = True
    except Exception as exc:
        # Transactional fallback: never return a partly applied model session.
        return {**fallback, 'fallback': f'adjustment_failed:{type(exc).__name__}'}
    return {**extra, 'candidate_id': 'llm_adjusted', 'allocations': current['allocations'],
            'selection_source': 'llm_tools', 'fallback': None, 'decisions': decisions,
            'explanations': current['explanations']}
