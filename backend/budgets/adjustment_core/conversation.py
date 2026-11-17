"""확장 예산 조정 대화. 금액 제안은 서버 결과이며 이 모듈은 승인 포트를 갖지 않는다."""
from copy import deepcopy
import re

from .agent import APPLY, CANCEL, ENDED, _ask, _check, _finished, _lock_snapshot, _result, _save
from .interpretation import InterpretationFailed, read, dialogue_context
from .explanations import NAMES, step_explanation, unavailable_explanation
from .calculation import NON_BUDGET_CODES
from .advisor import PLAN_IDS
from .responses import amount_edit_label


SPECIAL_NAMES = {"LIVING": "생활비", "SAVINGS": "저축", "CUSHION_MONTHLY": "월 머니쿠션", "SPARE": "여윳돈"}
LIST_FIELDS = ("sources", "destinations", "excluded")


def hint_category(request, names):
    """소비 분석에서 돌아올 때 라우터가 넘긴 카테고리 (request["hints"] = {"from": "spending", "category": 코드}). 모르는 코드는 버린다"""
    hints = request.get("hints") or {}
    code = hints.get("category") if hints.get("from") == "spending" else None
    return code if isinstance(code, str) and code in names and code not in SPECIAL_NAMES else None


def take_from_label(code, names):
    """소비 분석 답의 [○○에서 가져오기] 버튼 이름 (라우터 문구와 같은 모양). 이 이름 그대로 오면 LLM 없이 재원으로 본다"""
    return f"{names[code]}에서 가져오기"


def not_mine(ref=None, to=None, category=None, names=()):
    """'내 일 아님' 결과. 조회 질문이면 어디로 갈지(suggest)를 같이 알려 라우터가 LLM 없이 넘긴다. 저장된 상태는 바꾸지 않는다
    to=budget_basis(예산 금액이 정해진 이유)는 소비 분석으로 넘기지 않고 예산 대화 연결부가 저장된 배정 근거로 직접 답한다"""
    if to not in ("spending", "budget_basis"):
        return _result("not_mine", work_ref=ref)
    known = category if category in names and category not in SPECIAL_NAMES else None
    return _result("not_mine", work_ref=ref, suggest={"to": to, "category": known})


def _pending_reply(state, ref=None, context=None):
    pending = state["pending"]
    response = _result("awaiting_input", [pending["line"]], work_ref=ref, choices=pending["choices"],
                       explanation=pending.get("explanation"), choice_descriptions=pending.get("choice_descriptions"))
    if pending["kind"] == "comparison" and state.get("comparison"):
        response["reply"]["comparison"] = deepcopy(state["comparison"]["comparison"])
    if pending["kind"] == "financial_selection" and state.get("financial_offer"):
        response["reply"]["comparison"] = deepcopy(state["financial_offer"]["comparison"])
    if pending['kind'] in {'distribution', 'manual_distribution'}:
        response['reply']['distribution_input'] = deepcopy(pending['distribution_input'])
    if pending["kind"] == "partial" and state.get("proposal"):
        response["reply"]["proposal"] = deepcopy(state["proposal"])
        for key in ("alternatives", "alternatives_notice"):
            if key in state.get("partial", {}):
                response["reply"][key] = deepcopy(state["partial"][key])
        if context is not None and state.get("partial", {}).get("context") != context:
            response["reply"].pop("alternatives", None)
            response["reply"]["alternatives_notice"] = "예산 조건이 바뀌었어요. 다른 재원을 비교하려면 최신 조건으로 다시 확인해 주세요."
    return response


def _explain_current(state, context, text, ref=None):
    if state.get("pending", {}).get("kind") == "comparison" and state.get("comparison"):
        return _explain_comparison(state, context, text, ref)
    from .explanations import step_explanation
    if state.get("pending"):
        response = _pending_reply(state, ref, context)
        explanation = state["pending"].get("explanation") or step_explanation(state["pending"]["kind"], state)
        if (state['pending']['kind'] in {'category', 'destinations'}
                and any(row.get('category') == 'LIVING' and row.get('direction') == 'up'
                        for row in state.get('requests', []))):
            explanation = {
                'why': '저축·월 머니쿠션에서 가져오면 이번 주기에 배정한 예산이 줄어요. 이미 모은 돈을 인출하는 것은 아니에요.',
                'effect': '여윳돈도 남겨 둘 돈을 제외한 범위에서만 사용할 수 있어요.',
                'next': '늘릴 생활비 항목을 고르면, 가져올 수 있는 금액으로 조정 예시를 보여드릴게요.',
            }
    elif state.get("proposal"):
        explanation = state["proposal"].get("explanation") or step_explanation("proposal", state)
        response = _result("replied", work_ref=ref, proposal=state["proposal"], explanation=explanation)
    else:
        explanation = step_explanation("request", state)
        response = _result("replied", explanation=explanation)
    response["reply"]["explanation"] = deepcopy(explanation)
    response["reply"]["detail"] = True
    response["reply"]["lines"] = [value for key in ("why", "effect", "next")
                                    if isinstance(value := explanation.get(key), str) and value]
    if state.get("pending", {}).get("kind") == "available_quote" and state.get("quote", {}).get("context") != context:
        response["reply"]["lines"].insert(0, "이 가능액은 이전 조건의 결과예요. 조건이 바뀌어 다시 확인해야 해요.")
    return response


def _inspect_sources(store, cid, state, context, allocator):
    requests = state.get('requests', [])
    if len(requests) == 1 and requests[0].get('amount') is None and requests[0].get('category') in context['allocations']:
        from .advisor import capacities, source_capacity_lines, money, _living_note
        from .responses import observation_notice
        calculation = _calculation(context, state)
        code = requests[0]['category']
        found = capacities(calculation, code, excluded=state.get('excluded', []),
                           sources=state.get('sources'), allocator=allocator, spare_minimum=state.get('spare_minimum'))
        name = context.get('category_names', {}).get(code, code)
        lines = [f'{name} · 확인된 증액 한도 {money(sum(found.values()))}' if found else
                 f'{name} · 재원 금액 확인 필요']
        lines += source_capacity_lines(calculation, code, found, sources=state.get('sources'),
            excluded=state.get('excluded', []), allocator=allocator, spare_minimum=state.get('spare_minimum'))
        if found.get('LIVING') == 0:
            lines.append(_living_note(calculation, None))
        notice = observation_notice(calculation)
        if notice:
            lines.append(notice)
        _save(store, cid, state)
        return _result('replied', lines, work_ref=state.get('work_ref'), detail=True, explanation={})
    return _show_comparison(store, cid, state, context, _calculation(context, state), allocator,
                            respect_selected=True)


def decision_help(store, cid, state, context, allocator=None):
    pending, ref = state.get('pending', {}), state.get('work_ref')
    kind = pending.get('kind')
    if kind == 'increase_review':
        from .funding import large_increase_warning
        response = _pending_reply(state, ref, context)
        warning = large_increase_warning(context, state.get('requests', []), reason=state.get('reason'))
        response['reply']['lines'] = ([warning] if warning else []) + ['필요한 금액을 줄이거나, 주의사항을 확인한 뒤 계속할 수 있어요. 아직 예산은 바뀌지 않았어요.']
        return response
    if kind in {'comparison', 'financial_selection'}:
        return recommendation_reply(state, context, ref)
    if kind in {'sources', 'funding_scope'} and state.get('requests') and state['requests'][0].get('direction') == 'up':
        return _inspect_sources(store, cid, state, context, allocator)
    response = _pending_reply(state, ref, context) if pending else _result('replied', work_ref=ref, proposal=state.get('proposal'))
    focus = state.get('requests', [{}])[pending.get('index', 0) or 0] if state.get('requests') else {}
    if kind in {'category', 'destinations', 'destination', 'request', 'discussion'}:
        codes = [code for code, _ in pending.get('choices', []) if code in context['allocations'] and code not in NON_BUDGET_CODES]
        examples = [f"{context.get('category_names', {}).get(code, NAMES.get(code, code))} 현재 예산 {context['allocations'][code]:,}원" for code in codes[:3]]
        lines = ['평소 돈이 모자라는 항목부터 골라 보세요. 식사비가 부족하면 식비, 이동비가 부담되면 교통·차량처럼요.']
        if kind in {'category', 'request', 'discussion'} and focus.get('direction') == 'down':
            lines = ['덜 써도 괜찮은 항목부터 골라 보세요. 예를 들어 이번 달 옷을 살 계획이 없다면 패션·쇼핑을 살펴볼 수 있어요.']
        if examples:
            lines.append('고를 때 참고할 현재 예산: ' + ' / '.join(examples))
    elif kind in {'distribution', 'manual_distribution'}:
        lines = ['돈을 더 쓰고 싶은 항목을 고른 뒤, 각 항목에 보탤 금액을 입력해 보세요.',
                 '예: 식비와 교통비를 고르고 각각 필요한 만큼 나누는 식이에요. 합계가 맞으면 미리보기로 확인할 수 있어요.']
    elif kind == 'funding_scope':
        lines = ['줄인 돈을 다른 지출에 쓰려면 “다른 생활비에 배분”, 당장 쓸 곳이 없으면 “여윳돈으로 남기기”를 골라 보세요.',
                 '미래를 위해 모으려면 저축이나 월 머니쿠션으로 옮길 수 있어요.']
    elif kind == 'direction':
        lines = ['이 항목에 쓸 돈이 부족하면 늘리기, 덜 쓸 계획이면 줄이기를 고르면 돼요.']
    elif kind == 'amount_mode':
        lines = ['“2만 원 늘리기”는 지금 예산에 2만 원을 더하는 뜻이고, “10만 원으로 바꾸기”는 바꾼 뒤 예산을 10만 원으로 맞추는 뜻이에요.']
    elif kind == 'available_quote':
        lines = ['표시된 금액은 옮길 수 있는 최대액이에요. 모두 옮길 필요는 없어요.',
                 '그 금액이 괜찮으면 미리보기를 선택하고, 너무 크면 원하는 금액을 직접 알려 주세요.']
    elif kind == 'scope':
        lines = ['이번 달에만 필요한 돈이면 “이번 주기만”, 매달 비슷하게 필요하면 “앞으로도 계속”을 고르면 돼요.']
    elif kind == 'unlock':
        lines = ['“잠금 유지”는 이 항목의 예산을 그대로 두는 선택이에요.',
                 '이 항목도 바꾸고 싶을 때만 잠금 해제를 선택하세요. 해제는 최종 확정 때 적용돼요.']
    elif kind == 'financial_data':
        lines = {
            'spare_minimum': ['이번 급여일까지 예산 밖에서 쓸 돈과 비상시에 남길 돈을 생각해 보세요.',
                             '예: 예정된 병원비나 경조사비가 있다면 그 돈부터 남겨 두는 식이에요. 금액을 정하기 어렵다면 다른 항목에서 가져오는 방법도 볼 수 있어요.'],
            'planned': ['이미 예산에 넣은 지출을 빼고, 추가로 나갈 병원비·경조사비 같은 돈을 묻는 거예요. 확실히 없을 때만 0원으로 알려 주세요.'],
            'period': ['다음 급여일까지 며칠 남았는지 묻는 거예요. 급여일을 확인한 뒤 남은 날짜 수를 알려 주세요.'],
        }.get(pending.get('field'), ['정확한 금액을 모르면 먼저 어느 예산을 유지하고 싶은지 알려 주세요. 그 조건으로 가능한 방법을 살펴볼게요.'])
    elif kind == 'one_off_review':
        lines = ['이번에만 낸 행사비처럼 다시 쓰지 않을 돈이면 “이번만”, 매달 내는 비용이면 반복 지출이에요.',
                 '기억나지 않으면 일단 지출에 포함해 계산해요. 없었던 지출로 처리하지 않아요.']
    elif kind in {'partial', 'financial_partial'}:
        lines = ['가능한 금액만 먼저 늘릴지, 다른 곳에서도 가져와 원하는 금액을 채울지 고르는 단계예요.',
                 '조금만 늘려도 괜찮으면 부분 반영을, 부족하면 다른 재원을 비교해 보세요.']
    elif state.get('proposal'):
        lines = ['아래에서 줄어드는 항목과 변경 후 금액을 먼저 보세요.',
                 '너무 적게 남는 항목이 있으면 “이 항목은 더 남기고 싶어”라고 알려 주세요. 괜찮을 때만 예산을 확정하면 돼요.']
    else:
        lines = ['어느 부분이 고민되시나요? 바꿀 항목, 금액, 보여 드린 방법 중 어려운 부분을 알려 주시면 예를 들어 설명할게요.']
    response['reply'].update(lines=lines, detail=True, explanation={})
    return response


def _explanation_only(data):
    return (data.get("action") == "explain" and not data.get('income_change') and data.get('one_off') is None and data.get('set_aside_amount') is None and not data.get("requests") and not data.get("clear_targets")
            and not any(data.get(field) for field in LIST_FIELDS) and not data.get("funding_scope")
            and not data.get("scope") and data.get("unlock") is None and data.get("use_available") is None
            and data.get("reference") != "new")


def has_alternative(comparison, selected=None):
    outcomes = []
    for plan in comparison.get('plans', []):
        result = plan.get('result') or {}
        if (plan.get('status') not in {'ready', 'partial'} or type(plan.get('amount')) is not int
                or plan['amount'] <= 0 or not result.get('changes')
                or result.get('status') not in {'proposed', 'partial'}):
            continue
        amounts = (result.get('allocations'), result.get('buckets'))
        if not all(isinstance(value, dict) for value in amounts):
            continue
        if amounts not in outcomes:
            outcomes.append(amounts)
    return len(outcomes) > 1 and (selected is None or (selected.get('allocations'), selected.get('buckets')) in outcomes)


def recommendation_reply(state, context, ref=None):
    stored = state.get('comparison') if state.get('pending', {}).get('kind') == 'comparison' else state.get('financial_offer')
    if not stored or stored.get('context') != context:
        return _result('awaiting_input', ['예산 조건이 바뀌었어요. 최신 금액으로 다시 비교해 주세요.'])
    comparison = deepcopy(stored['comparison'])
    recommendation = comparison.get('recommendation') or {}
    winner = next((p for p in comparison.get('plans', []) if p['id'] == recommendation.get('plan_id')
                   and p.get('status') == 'ready' and (p.get('result') or {}).get('changes')), None)
    response = _pending_reply(state, ref, context)
    if winner is None:
        title = '지금은 한 가지 방법을 추천하기 어려워요.'
        reasons = recommendation.get('reasons') or ['조건을 똑같이 만족하거나 요청한 금액을 모두 옮길 수 없어, 금액이나 유지할 항목을 먼저 정해야 해요.']
    else:
        title = f"‘{winner['label']}’을 추천해요."
        reasons = recommendation.get('reasons') or winner.get('assessment', {}).get('reasons') or []
        comparison['plans'] = [winner]
        response['reply']['comparison'] = comparison
        prefix = 'plan_' if state['pending']['kind'] == 'comparison' else 'financial_'
        response['reply']['choices'] = [c for c in response['reply']['choices']
                                       if not c['value'].startswith(prefix) or c['value'] == prefix + winner['id']]
    response['reply']['recommendation_advice'] = {'title': title, 'reasons': list(dict.fromkeys(reasons))[:3]}
    response['reply']['lines'] = [title, *response['reply']['recommendation_advice']['reasons']]
    response['reply']['explanation'] = None
    return response


def _explain_comparison(state, context, text, ref=None):
    response = _pending_reply(state, ref, context)
    stored = state["comparison"]
    plans = stored['comparison'].get('plans', [])
    if stored['comparison'].get('headline') is not None and not re.search(r"\d+\s*안|자세히|상세|변경\s*내역", text):
        # 추천 카드는 그대로 두고, 왜 이 순서로 계산했는지만 짧게 답한다.
        winner = next((plan for plan in plans if plan.get('recommended')), None)
        lines = list((winner or {}).get('assessment', {}).get('reasons', []))[:3] or [stored['comparison']['headline']]
        if stored['context'] != context:
            lines.insert(0, '예산 조건이 바뀌어 다시 계산해야 해요.')
        response['reply']['lines'] = lines
        response['reply']['detail'] = True
        return response
    response["reply"].pop("comparison", None)
    comparison, lines = stored["comparison"], []
    if stored["context"] != context:
        lines.append("아래는 이전 비교 당시의 근거예요. 예산 조건이 바뀌어 다시 비교해야 해요.")
    plans, recommendation = comparison["plans"], comparison.get("recommendation", {})
    numbers = {int(value) for value in re.findall(r"(?<!\d)(-?\d+)\s*(?:번\s*)?안", text)}
    target, ambiguous = None, False
    if numbers:
        if len(numbers) == 1 and 1 <= next(iter(numbers)) <= len(plans):
            target = plans[next(iter(numbers)) - 1]
        else:
            ambiguous = True
    else:
        named = {code for word, code in (("생활비", "LIVING"), ("여윳돈", "SPARE"),
                                         ("저축", "SAVINGS"), ("쿠션", "CUSHION_MONTHLY")) if word in text}
        if named:
            matching = [plan for plan in plans if named <= set((plan.get("envelope") or {}).get("sources", []))
                        | set((plan.get("envelope") or {}).get("destinations", []))]
            if len(matching) == 1:
                target = matching[0]
            else:
                ambiguous = True
        elif re.search(r"(?:그|이|저)\s*안|그거|그것", text):
            ambiguous = True
        elif "비추천" in text:
            matching = [plan for plan in plans if plan.get("assessment", {}).get("label") == "비추천"]
            if len(matching) == 1:
                target = matching[0]
            else:
                ambiguous = True
        else:
            target = next((plan for plan in plans if plan["id"] == recommendation.get("plan_id")), None)
    if ambiguous:
        lines.append("어느 안의 이유를 설명할까요? 비교표의 안 번호나 재원 이름을 알려 주세요.")
    elif target is not None:
        assessment = target.get("assessment", {})
        lines.append(f"{plans.index(target) + 1}안 {target['label']}: {assessment.get('label', '저장된 판단 근거')} 근거예요.")
        reasons = assessment.get("reasons", [])
        cautions = assessment.get("cautions", [])
        if not reasons and target["id"] == recommendation.get("plan_id"):
            reasons, cautions = recommendation.get("reasons", []), recommendation.get("cautions", [])
        lines.extend(reasons)
        lines.extend("확인할 점: " + caution for caution in cautions)
        if not reasons and not cautions:
            lines.append("이 비교안의 평가 근거가 저장되지 않았어요. 다시 비교해 주세요.")
    else:
        lines.extend(recommendation.get("reasons", []))
        lines.extend("확인할 점: " + caution for caution in recommendation.get("cautions", []))
        lines.extend("판단 기준: " + policy for policy in recommendation.get("policy", []))
        if not lines or stored["context"] != context and len(lines) == 1:
            lines.append("저장된 추천 근거가 없어요. 다시 비교해 주세요.")
    if not ambiguous and any(word in text for word in ("자세히", "상세", "예산", "변경 내역", "변경내역")):
        shown = [target] if target is not None and (numbers or any(word in text for word in ("생활비", "여윳돈", "저축", "쿠션"))) else plans
        names = {**context.get("category_names", {}), **SPECIAL_NAMES}
        for plan in shown:
            lines.append(f"{plans.index(plan) + 1}안 {plan['label']} 예산 변화:")
            changes = (plan.get("result") or {}).get("changes", [])
            lines.extend(f"{names.get(change['category'], change['category'])}: {change['before']:,} → {change['after']:,}원"
                         for change in changes)
            if not changes:
                lines.append("아직 검수된 전후 예산이 없어요. " + " ".join(plan.get("lines", [])))
    response["reply"]["lines"] = lines
    response["reply"]["detail"] = True
    return response


def distribution_fields(context, donors, excluded=()):
    from .funding import _data
    checked = _data(context, {'items': donors, 'sources': [], 'destinations': [], 'excluded': list(excluded)})
    sources = [{'category': item['category'], 'amount': checked['before'][item['category']] - checked['targets'][item['category']]}
               for item in donors]
    if not sources or any(row['amount'] <= 0 or row['amount'] % 5000 for row in sources):
        raise ValueError('invalid_distribution_amount')
    source_codes = {row['category'] for row in sources}
    categories = [code for code in context['allocations'] if code not in source_codes and code not in excluded
                  and 'LIVING' not in excluded and code not in context.get('locks', {}) and code not in NON_BUDGET_CODES]
    categories.extend(code for code in context['buckets'] if code not in source_codes and code not in excluded
                      and context.get('bucket_status', {}).get(code) == 'ready')
    return {'source': sources[0]['category'], 'sources': sources,
            'amount': sum(row['amount'] for row in sources), 'categories': categories,
            'before': {**context['allocations'], **context['buckets']}}


def _calculation(context, state):
    codes = state.get("unlock_categories", [])
    consents = state.get("unlock_consents", {})
    if (set(codes) != set(consents) or any(code not in context.get("locks", {}) for code in codes)
            or _lock_snapshot(context, codes) != consents):
        raise ValueError("stale_unlock_consent")
    from .diagnostics import with_overrides
    result = with_overrides(context, state.get('financial_overrides', {}))
    result["locks"] = {code: amount for code, amount in context.get("locks", {}).items() if code not in codes}
    return result


def _target_changes(context, result, scope, requests, clear):
    targets = {code: None for code in clear if code in context.get("category_targets", {})}
    if scope == "ongoing":
        targets.update({code: amount for code, amount in result.get("persistence_candidates", {}).items()
                        if code != "SPARE" and code not in clear})
        targets.update({item["category"]: result["allocations"][item["category"]] for item in requests
                        if item["category"] in result["allocations"] and item["category"] not in clear})
    return targets


def distribute(request, state, data, *, store, get_context, simulate, propose, **ports):
    context = get_context()
    cid = request['conversation_id']
    pending = state.get('pending', {})
    if pending.get('kind') in {'unlock', 'partial', 'financial_risk', 'financial_partial'}:
        return _pending_reply(state, state.get('work_ref'), context)
    try:
        calculation = _calculation(context, state)
    except ValueError:
        for key in ('unlock_categories', 'unlock_consents'):
            state.pop(key, None)
        return _ask(store, cid, state, 'request', '잠금 조건이 바뀌었어요. 잠금 해제를 다시 확인해 주세요.')
    names = {**context.get('category_names', {}), **SPECIAL_NAMES}
    excluded = set(state.get('excluded', [])) | set(data.get('excluded', []))
    specified = data.get('requests', [])
    receiving = [item for item in specified if item.get('direction') == 'up']
    donors = [item for item in (specified or state.get('requests', [])) if item.get('direction') == 'down']
    if not donors:
        donors = [item for item in state.get('requests', []) if item.get('direction') == 'down']
    if not donors and state.get('proposal'):
        donors = [{'category': row['category'], 'amount': -row['diff'], 'amount_mode': 'by', 'direction': 'down'}
                  for row in state['proposal']['changes'] if row['diff'] < 0]
    destinations = ([item.get('category') for item in receiving]
                    or data.get('destinations') or state.get('destinations', []))
    if (len(donors) == 1 and donors[0].get('amount') is None and donors[0].get('category') in names
            and len(destinations) == 1 and destinations[0] in names
            and destinations[0] != donors[0]['category']
            and all(item.get('amount') is None for item in receiving)):
        state.update(requests=deepcopy(donors), destinations=list(destinations), excluded=sorted(excluded))
        if data.get('scope'):
            state['scope'] = data['scope']
        limit_reply = check_amount_limit(store, cid, state, calculation)
        if limit_reply is not None:
            return limit_reply
        return _ask(store, cid, state, 'amount',
                    f"{names[donors[0]['category']]}에서 얼마를 줄여 {names[destinations[0]]}에 옮길까요?",
                    index=0, explanation={})
    if not donors or any(item.get('amount') is None for item in donors):
        return _ask(store, cid, state, 'request', '어느 항목에서 얼마를 줄여 나눌까요?')
    limit_reply = check_amount_limit(store, cid, state, calculation, requests=[*donors, *receiving])
    if limit_reply is not None:
        return limit_reply
    try:
        fields = distribution_fields(calculation, donors, excluded)
    except (ValueError, KeyError):
        return _ask(store, cid, state, 'request', '나눌 금액을 확인해 주세요. 5,000원 단위로 정할 수 있어요.')
    categories, total = fields['categories'], fields['amount']
    if not categories:
        return _ask(store, cid, state, 'request', '현재 받을 수 있는 항목이 없어요. 유지할 항목이나 옮길 곳을 다시 정해 주세요.')
    if data.get('scope'):
        state['scope'] = data['scope']
    if (len(destinations) == 1 and destinations[0] in categories
            and (receiving or data.get('destinations') or pending.get('kind') == 'amount')
            and (not receiving or len(receiving) == 1 and receiving[0].get('amount') is None)):
        receiving = [{'category': destinations[0], 'direction': 'up', 'amount_mode': 'by', 'amount': total}]
    def ask_distribution(line=None):
        state['requests'] = deepcopy(donors)
        state['excluded'] = sorted(excluded)
        source_names = '·'.join(names.get(row['category'], row['category']) for row in fields['sources'])
        from .protection import expected_need
        needs = ([(code, expected_need(calculation, code)) for code in categories if code in calculation['allocations']]
                 if calculation.get('spending_status', 'complete') == 'complete' or calculation.get('observed_spending', {}).get('transaction_count', 0) > 0 else [])
        needs = [(code, amount) for code, amount in needs if amount is not None and amount > 0]
        needs.sort(key=lambda row: -row[1])
        evidence = ('최근 소비 속도와 남은 기간으로 보면 ' + ', '.join(f'{names[code]} 약 {amount:,}원' for code, amount in needs[:3])
                    + '이 더 필요할 것으로 예상돼요. 받을 항목과 금액을 골라 주세요.' if needs else
                    '어느 항목에 더 필요한지 확인되지 않아 임의로 배분하지 않았어요. 받을 항목과 금액을 골라 주세요.')
        response = _ask(store, cid, state, 'distribution', line or f"{source_names}에서 줄일 {total:,}원을 어디에 나눌까요?",
                        distribution_input=fields, context=deepcopy(context),
                        explanation={'brief': evidence})
        response['reply']['distribution_input'] = fields
        return response
    if not receiving or any(item.get('amount') is None for item in receiving):
        return ask_distribution()
    codes = [item.get('category') for item in receiving]
    valid = (len(codes) == len(set(codes)) and all(code in categories for code in codes)
             and all(type(item.get('amount')) is int and item['amount'] > 0 and item['amount'] % 5000 == 0
                     and item.get('amount_mode') == 'by' for item in receiving)
             and sum(item['amount'] for item in receiving) == total)
    if not valid or pending.get('kind') == 'distribution' and pending.get('context') != context:
        return ask_distribution('항목과 금액을 다시 확인해 주세요. 나눈 금액의 합계가 줄일 금액과 같아야 해요.')
    envelope = {'items': [*deepcopy(donors), *deepcopy(receiving)], 'sources': [], 'destinations': [], 'excluded': sorted(excluded)}
    if state.get('spare_minimum') is not None:
        envelope['spare_minimum'] = state['spare_minimum']
    scope, clear = state.get('scope', 'cycle'), state.get('clear_targets', [])
    try:
        result = simulate(calculation, envelope)
        if result['status'] != 'proposed':
            raise ValueError('distribution_unavailable')
        card = propose(context, result, scope=scope, unlock_categories=state.get('unlock_categories', []),
                       target_changes=_target_changes(context, result, scope, envelope['items'], clear))
    except ValueError:
        return ask_distribution('이미 쓴 돈과 남겨 둘 돈을 확인해 보니 이 배분은 적용할 수 없어요. 항목이나 금액을 바꿔 주세요.')
    state.update(requests=envelope['items'], sources=[], destinations=[], excluded=sorted(excluded),
                 proposal=card, proposal_request=envelope, work_ref=card['task_ref'])
    for key in ('pending', 'comparison', 'financial_offer', 'primary_request', 'refinement', 'requested_view'):
        state.pop(key, None)
    _save(store, cid, state)
    return _result('replied', ['직접 나눈 금액으로 만든 변경안이에요. 아직 예산은 바뀌지 않았어요.'], proposal=card)


def _living_target(store, cid, state, context):
    """생활비 전체가 부족할 때: 가져올 수 있는 곳과 모자라 보이는 항목을 먼저 알려 주고 받을 항목을 고르게 한다."""
    from .advisor import josa, living_sources, money, names_of, shortfall_categories, SOURCE_NAMES
    item = (state.get('requests') or [{}])[0]
    amount = item.get('amount') if item.get('amount_mode') in (None, 'by') else None
    if state.get('living_total') and amount is None:
        return _ask(store, cid, state, 'amount', '생활비 전체를 얼마 늘리고 싶으세요?', index=0)
    try:
        calculation = _calculation(context, state)
    except ValueError:
        calculation = context
    names = names_of(context)
    found = living_sources(calculation, state.get('excluded', []))
    parts = [f"{SOURCE_NAMES[code]} 최대 {money(value)}" for code, value in found.items() if value and code != 'SAVINGS']
    total = '생활비 전체를 늘리려면 여윳돈·월 머니쿠션에서 가져와야 해요. ' + (
        ('지금은 ' + ', '.join(parts) + '까지 가능해요' + ('(여윳돈은 30% 이상 남겨요).' if found.get('SPARE') else '.'))
        if parts else '지금은 여기서 더 가져올 수 있는 돈이 없어요.')
    short = shortfall_categories(calculation)
    if amount is None:
        # 부족한 항목을 고르면 다른 생활비 → 여윳돈 순서로 계산한다. 전체를 늘릴 때만 여윳돈이 필요하다.
        lines = ['부족한 항목을 고르면 다른 생활비에서 먼저 옮기고, 모자라면 여윳돈에서 채울게요.']
    else:
        lines = [total]
    if short:
        listed = ', '.join(f"{names.get(code, code)} 약 {money(value)}" for code, value in short)
        lines.append(f"최근 쓰는 속도로 보면 {listed}{josa(money(short[-1][1]), '이가')} 모자라요.")
    lines.append('어느 항목이 부족한가요?' if amount is None else '어느 항목에 넣을까요?')
    codes = [code for code, _ in short] + [code for code in context['allocations']
                                           if code not in NON_BUDGET_CODES and code not in dict(short)
                                           and code not in state.get('excluded', [])]
    choices = [(code, f"{names.get(code, code)} (약 {money(dict(short)[code])} 부족)" if code in dict(short)
                else names.get(code, code)) for code in codes]
    if amount is None:
        choices.append(('LIVING', '생활비 전체'))
        return _ask(store, cid, state, 'category', ' '.join(lines), choices, index=0, explanation={})
    return _ask(store, cid, state, 'destinations', ' '.join(lines), choices, index=0, explanation={})


def is_salary_resume(request, state):
    return bool(state.get('salary_handoff') and (request.get('text') or '').strip() in {
        'salary_resume', '이전 예산 조정 이어하기'})


def salary_reply(store, cid, state, context, data):
    """Keep the adjustment while salary edits are handled by MyPage."""
    from .funding import _data
    pending = deepcopy(state.get('pending'))
    incoming = deepcopy(data)
    incoming.pop('income_change', None)
    incoming.pop('action', None)
    # A salary-only follow-up must not replace the expense or its intended period.
    conditions = {field: incoming[field] for field in (*LIST_FIELDS, 'funding_scope') if incoming.get(field)}
    if incoming.get('requests'):
        _merge(state, incoming, pending or {})
        pending = None
    elif conditions:
        _merge(state, conditions, pending or {})
        pending = None
    if not state.get('requests'):
        state.pop('planning', None)
        state.pop('scope', None)
    state.get('planning', {}).pop('income_change', None)
    if not state.get('salary_handoff') or incoming.get('requests'):
        items = deepcopy(state.get('requests', []) if incoming.get('requests') else
                         (state.get('primary_request') or state.get('plan_request') or {}).get('items')
                         or state.get('requests', []))
        requested = {item.get('category') for item in items}
        for code, condition in state.get('refinement', {}).get('donors', {}).items():
            if condition.get('explicit') and type(condition.get('target')) is int and code not in requested:
                items.append({'category': code, 'amount': condition['target'], 'amount_mode': 'to', 'direction': 'down'})
        targets = {}
        for item in items:
            try:
                targets.update(_data(context, {'items': [item]})['targets'])
            except ValueError:
                pass  # An unfinished request keeps its missing input for the return step.
        state['salary_handoff'] = {'pending': pending, 'context': deepcopy(context),
                                   'items': items, 'targets': targets, 'rebuild': bool(incoming.get('requests') or conditions)}
    if conditions:
        state['salary_handoff'].update(pending=None, rebuild=True)
    future = data.get('scope') == 'next_cycle'
    state['salary_handoff']['future_income'] = future
    line = '월급은 마이페이지에서 수정할 수 있어요. 저장한 뒤 돌아오면 이전 예산 조정을 이어갈 수 있어요.'
    if future:
        line = ('월급 변경은 실제 변경 시점에 마이페이지에서 하실 수 있어요. '
                '진행 중인 예산 조정은 그대로 유지해 둘게요.')
    return _ask(store, cid, state, 'salary_handoff', line,
                [('salary_resume', '이전 예산 조정 이어하기')], explanation={})


def resume_salary(store, cid, state, context, task_status, cancel):
    """Return the saved screen, or rebase the original targets on the current budget."""
    handoff = state['salary_handoff']
    reference = state.get('work_ref')
    checked = _check(reference, task_status) if reference else None
    if not handoff.get('rebuild') and handoff['context'] == context and (not checked or checked['state'] not in ENDED):
        state.pop('salary_handoff')
        if handoff.get('pending'):
            state['pending'] = handoff['pending']
        else:
            state.pop('pending', None)
        _save(store, cid, state)
        if state.get('pending'):
            return _pending_reply(state, reference, context)
        if state.get('proposal'):
            return _result('replied', ['이전 예산 조정을 이어갈게요. 아직 예산은 바뀌지 않았어요.'],
                           work_ref=reference, proposal=state['proposal'])
        if not state.get('requests') and not state.get('clear_targets'):
            return _ask(store, cid, state, 'request', '어떤 예산을 조정하고 싶으세요?', explanation={})
        return None
    if checked and checked['state'] == 'saving':
        return _result('awaiting_input', ['이전 조정안의 저장 결과를 확인한 뒤 이어갈 수 있어요.'], work_ref=reference)
    if checked and checked['state'] not in ENDED:
        cancel(reference)
        if _check(reference, task_status)['state'] not in ENDED:
            return _result('awaiting_input', ['이전 미리보기를 정리한 뒤 최신 예산으로 이어갈게요.'], work_ref=reference)
    before = {**context['allocations'], **context['buckets'], 'LIVING': sum(context['allocations'].values())}
    requests, completed_reductions = [], []
    for item in handoff['items']:
        item = deepcopy(item)
        code = item.get('category')
        target = handoff['targets'].get(code)
        if target is not None and code in before:
            fulfilled = (before[code] >= target if item.get('direction') == 'up' else
                         before[code] <= target if item.get('direction') == 'down' else before[code] == target)
            if fulfilled:
                if item.get('direction') == 'down':
                    completed_reductions.append({**item, 'amount': before[code], 'amount_mode': 'to', 'direction': None})
                continue
            item.update(amount=target, amount_mode='to')
        requests.append(item)
    if requests:
        # A previously fulfilled cut is still a limit, not permission to cut the same source again.
        requests.extend(completed_reductions)
    state['requests'] = requests
    planned = deepcopy(state.get('financial_overrides', {}).get('planned_spare', {}))
    confirmed_planned = (planned.get('status') == 'user_confirmed' and planned.get('scope') == 'unbudgeted_only'
                         and type(planned.get('amount')) is int and planned['amount'] >= 0)
    same_period = (handoff['context'].get('cycle_id') == context.get('cycle_id')
                   and handoff['context'].get('effective_on') == context.get('effective_on'))
    expense = state.get('expense_request') or {}
    expense_target = handoff['targets'].get(expense.get('category'))
    if state.get('one_off') and expense_target is not None and type(state.get('set_aside_amount')) is int:
        state['expense_budget_base'] = expense_target - (expense['amount'] - state['set_aside_amount'])
    for key in ('salary_handoff', 'pending', 'proposal', 'work_ref', 'reference_context', 'pending_edit',
                'comparison', 'financial_offer', 'quote', 'partial', 'primary_request', 'plan_request',
                'proposal_request', 'refinement', 'risk_acknowledged', 'plan_selected', 'history',
                'financial_overrides', 'requested_view'):
        state.pop(key, None)
    codes = state.get('unlock_categories', [])
    valid = [code for code in codes if code in context.get('locks', {})
             and state.get('unlock_consents', {}).get(code) == _lock_snapshot(context, [code])[code]]
    state['unlock_categories'] = valid
    state['unlock_consents'] = {code: state['unlock_consents'][code] for code in valid}
    if confirmed_planned and same_period:
        state['financial_overrides'] = {'planned_spare': planned}
    _save(store, cid, state)
    if confirmed_planned and not same_period and requests:
        return _ask(store, cid, state, 'financial_data',
                    '예산 주기가 바뀌었어요. 이번 주기에 여윳돈으로 따로 쓸 예정인 금액이 있나요?',
                    field='planned', explanation={})
    if not requests and not state.get('clear_targets'):
        line = ('이전 요청은 최신 예산에 이미 반영되어 있어요. 같은 금액을 다시 조정하지 않을게요.'
                if handoff['items'] else '등록된 최신 정보로 이어갈게요. 어떤 예산을 조정하고 싶으세요?')
        return _ask(store, cid, state, 'request', line, explanation={})
    return None


def _manual_living(store, cid, state, context):
    from .funding import _data
    state['manual_living'] = True
    state['sources'], state['destinations'] = [], []
    items = state.get('requests', [])
    if len(items) == 1 and items[0].get('category') == 'LIVING' and items[0].get('direction') == 'up':
        if items[0].get('amount') is not None:
            state.pop('manual_living', None)
            return _route_question(store, cid, state, {**context.get('category_names', {}), **SPECIAL_NAMES},
                                   items[0], 'up', context=context)
        return _living_target(store, cid, state, context)
    previous = state.get('pending', {}).get('distribution_input', {})
    if not items or any(item.get('category') not in context['allocations'] for item in items):
        return _ask(store, cid, state, 'request', '어느 생활비를 늘리거나 줄이고 싶으세요?')
    item = items[0]
    if len(items) == 1 and item.get('amount') is None:
        return _ask(store, cid, state, 'amount', '얼마를 조정할까요?', index=0)
    try:
        if len(items) == 1 and item.get('direction') == 'down':
            # 받는 항목은 기존 배분 편집기로 선택한다.
            fields = distribution_fields(context, items, state.get('excluded', []))
            line = '줄인 돈을 받을 항목과 금액을 골라 주세요.'
        else:
            data = _data(context, _envelope(state))
            receivers = [c for c, n in data['targets'].items() if n > data['before'][c]]
            if len(receivers) != 1:
                return _ask(store, cid, state, 'request', '먼저 늘릴 생활비 항목 하나를 정해 주세요.')
            code = receivers[0]
            amount = data['targets'][code] - data['before'][code]
            maximums = {c: max(n - max(context.get(f, {}).get(c, 0) for f in ('spent', 'minimums', 'system_floors')), 0) // 5000 * 5000
                        for c, n in context['allocations'].items() if c != code and c not in state.get('excluded', []) and c not in NON_BUDGET_CODES}
            fields = {'target': code, 'amount': amount, 'categories': [c for c, n in maximums.items() if n > 0],
                      'maximums': maximums, 'before': {**context['allocations'], **context['buckets']}}
            values = [{'category': c, 'amount': data['before'][c] - n} for c, n in data['targets'].items() if n < data['before'][c]]
            if not values and previous.get('target') == code and previous.get('amount') == amount:
                values = previous.get('values', [])
            if values:
                fields['values'] = values
            state['requests'] = [{'category': code, 'amount': amount, 'amount_mode': 'by', 'direction': 'up'}]
            line = '줄여도 괜찮은 항목과 금액을 골라 주세요.'
    except (ValueError, KeyError):
        return _ask(store, cid, state, 'request', '현재 예산과 조정할 금액을 다시 확인해 주세요.')
    if fields['amount'] <= 0 or not fields['categories']:
        return _ask(store, cid, state, 'request', '이미 쓴 돈과 최소 금액을 남기면 지금 옮길 수 있는 생활비가 없어요. 금액이나 보호 설정을 확인해 주세요.')
    # 내역이 덜 모였어도 예산 미리보기(budget_preview)면 추천안처럼 지금까지 확인된 기록으로 계산해 고를 수 있다
    incomplete = context.get('spending_status', 'complete') != 'complete'
    fields['needs_refresh'] = incomplete and not context.get('budget_preview')
    fields['preview'] = incomplete and bool(context.get('budget_preview'))
    choices = [('manual', amount_edit_label(state))]
    if fields['needs_refresh']:
        choices.insert(0, ('manual_living', '내역 조회 후 다시 확인'))
    response = _ask(store, cid, state, 'manual_distribution', line,
                    choices, distribution_input=fields, index=0)
    response['reply']['distribution_input'] = fields
    return response


def _increase_only(calculation, envelope):
    from .funding import _data, _value
    items = envelope.get('items', [])
    if not items or any(row.get('amount') is None or row.get('amount_mode') == 'available' for row in items):
        return False
    try:
        checked = _data(calculation, envelope)
    except ValueError:
        return False
    return all(target > _value(checked, checked['before'], code) for code, target in checked['targets'].items())


def advised_comparison(store, cid, state, context, calculation, allocator, *, envelope=None, respect_selected=False,
                       lead=None):
    """증액은 한 가지 형식으로 답한다: 이해한 요청 → 서버가 계산한 추천안·대안 → 고르기."""
    from .advisor import plan_increase
    from .calculation import proportional_group_reduction
    envelope = _envelope(state) if envelope is None else envelope
    try:
        comparison = plan_increase(calculation, envelope, allocator or proportional_group_reduction,
                                   respect_selected=respect_selected)
    except ValueError:
        _save(store, cid, state)
        return _result('awaiting_input', ['조정 방법을 계산하는 중 문제가 생겼어요. 말씀하신 조건은 기억하고 있으니 잠시 후 다시 요청해 주세요.'],
                       error='planner_unavailable')
    if lead:
        comparison['lead'] = lead
    from .responses import observation_notice
    notice = observation_notice(calculation)
    if notice:
        comparison['notice'] = ' '.join(filter(None, [comparison.get('notice'), notice]))
    if comparison['status'] != 'ready':
        from .advisor import unavailable_reasons
        state.pop('comparison', None)
        reasons = unavailable_reasons(calculation, envelope)
        spending_missing = len(reasons) == 1
        line = ('지금까지 쓴 돈을 모두 확인하지 못해 자동으로 계산하지 못했어요. 줄일 항목과 금액은 직접 정할 수 있어요.'
                if spending_missing else '지금 조건으로는 자동으로 추천할 방법이 없어요. 줄여도 되는 항목과 금액은 직접 정할 수 있어요.')
        return _ask(store, cid, state, 'funding_scope', line, [('manual_living', '줄일 항목 직접 고르기')],
                    field='sources', index=0,
                    explanation={'title': '이번에 추천안을 보여 드리지 않은 이유',
                                 'brief': '지금은 자동으로 옮길 방법을 찾지 못해 추천에서 제외했어요.',
                                 'why': '\n'.join(reasons), 'effect': '직접 정한 금액으로는 조정할 수 있어요.',
                                 'next': '줄일 항목 직접 고르기에서 항목과 금액을 정해 주세요.'})
    choices = [('plan_' + plan['id'], plan['label']) for plan in comparison['plans']]
    single = len(envelope.get('items', [])) == 1
    if single:
        choices.append(('manual', amount_edit_label(state)))
    if any(plan['sources'].get('SPARE') for plan in comparison['plans']):
        choices.append(('keep_spare', '남길 여윳돈 정하기'))
    if single and envelope['items'][0].get('category') in context['allocations']:
        choices.append(('manual_living', '줄일 항목 직접 고르기'))
    state['comparison'] = {'context': deepcopy(context), 'request': deepcopy(envelope), 'comparison': deepcopy(comparison)}
    lines = [line for line in (lead, comparison['headline']) if line]
    index = 0 if len(envelope.get('items', [])) == 1 else None
    response = _ask(store, cid, state, 'comparison', ' '.join(lines), choices, index=index, explanation={})
    response['reply']['comparison'] = deepcopy(comparison)
    return response


def ask_missing_amount(store, cid, state, context, *, confirm_example=False):
    if state.get('use_available') is True or state.get('over_budget') and not confirm_example:
        return None
    names = {**context.get('category_names', {}), **SPECIAL_NAMES}
    for index, item in enumerate(state.get('requests', [])):
        code, direction = item.get('category'), item.get('direction')
        if (item.get('amount') is not None or item.get('amount_mode') == 'available'
                or code not in names or direction not in {'up', 'down'}):
            continue
        if code == 'LIVING' and direction == 'up' and not state.get('destinations'):
            return _living_target(store, cid, state, context)
        verb = '늘릴까요' if direction == 'up' else '줄일까요'
        line = f'{names[code]} 예산을 얼마 {verb}?'
        destinations = state.get('destinations', [])
        if direction == 'down' and len(destinations) == 1 and destinations[0] in names and destinations[0] != 'LIVING':
            line = f'{names[code]}에서 얼마를 줄여 {names[destinations[0]]}에 옮길까요?'
        return _ask(store, cid, state, 'amount', line,
                    [('available', '가능한 금액 확인')], index=index, explanation={})
    return None


def _show_comparison(store, cid, state, context, calculation, allocator, *, envelope=None, respect_selected=False):
    from .comparison import compare_adjustments
    envelope = _envelope(state) if envelope is None else envelope
    limit_reply = check_amount_limit(store, cid, state, calculation, requests=envelope.get('items', []))
    if limit_reply is not None:
        return limit_reply
    items = envelope.get('items', [])
    if (len(items) == 1 and items[0].get('category') == 'LIVING' and items[0].get('direction') == 'up'
            and not envelope.get('destinations')):
        return _living_target(store, cid, state, context)
    if _increase_only(calculation, envelope):
        return advised_comparison(store, cid, state, context, calculation, allocator, envelope=envelope,
                                  respect_selected=respect_selected and bool(envelope.get('sources')))
    if len(envelope.get('items', [])) > 1:
        from .funding import _data, _value
        for index, row in enumerate(envelope['items']):
            if row.get('amount') is None:
                return _ask(store, cid, state, 'amount', '이 항목은 얼마 조정할까요?', index=index)
        item = envelope['items'][0]
        direction = item.get('direction')
        if direction not in {'up', 'down'}:
            checked = _data(calculation, envelope)
            direction = 'up' if checked['targets'][item['category']] > _value(checked, checked['before'], item['category']) else 'down'
        names = {**context.get('category_names', {}), **SPECIAL_NAMES}
        return _route_question(store, cid, state, names, item, direction, context=context, allocator=allocator)
    try:
        comparison = compare_adjustments(calculation, envelope, allocate_group_reduction=allocator,
                                         respect_selected=respect_selected)
    except ValueError as error:
        return _calculation_failure(store, cid, state, error)
    if not isinstance(comparison, dict) or not isinstance(comparison.get("plans"), list):
        return _result("failed", ["비교 결과를 확인하지 못했어요."], error="invalid_comparison_result")
    from .responses import observation_notice
    notice = observation_notice(calculation)
    if notice:
        comparison['notice'] = ' '.join(filter(None, [comparison.get('notice'), notice]))
    if comparison.get('status') == 'needs_destination':
        state.pop('comparison', None)
        return _ask(store, cid, state, 'destinations', '늘릴 생활비 항목을 골라 주세요.',
                    [(code, context.get('category_names', {}).get(code, code)) for code in context['allocations']
                     if code not in NON_BUDGET_CODES and code not in state.get('excluded', [])],
                    index=0, compare_after_selection=True,
                    explanation={'brief': comparison['message']})
    choices = [("plan_" + plan["id"], plan["label"]) for plan in comparison["plans"]
               if plan.get("id") in PLAN_IDS and plan.get("envelope") is not None
               and (plan.get('id') == 'living' and plan.get('status') == 'needs_destination'
                    or plan.get("status") in {"ready", "partial"} and type(plan.get('amount')) is int
                    and plan['amount'] > 0 and (plan.get('result') or {}).get('changes'))]
    state["comparison"] = {"context": deepcopy(context), "request": envelope, "comparison": deepcopy(comparison)}
    direct = [('distribute', '다른 항목에 직접 분배')] if len(envelope.get('items', [])) == 1 and envelope['items'][0].get('direction') == 'down' and envelope['items'][0].get('amount') is not None else [('manual_living', '생활비 직접 조정')]
    if (any(plan.get('needs_spare_minimum') for plan in comparison['plans'])
            and context.get('bucket_status', {}).get('SPARE') == 'ready'
            and context.get('bucket_available', {}).get('SPARE', 0) > 0
            and (context.get('spending_status', 'complete') == 'complete' or context.get('budget_preview'))):
        direct.append(('keep_spare', '남길 여윳돈 정하기'))
    response = _ask(store, cid, state, "comparison", "가져올 곳과 금액을 계산한 예시예요. 아직 예산은 바뀌지 않았어요."
                    if choices else comparison.get("message") or "현재 검수된 조건에서 선택할 수 있는 비교안이 없어요. 제한 사유를 확인하고 조건을 바꿔 주세요.",
                    [*choices, *direct, *([("manual", amount_edit_label(state)), ("back", "재원 다시 선택")] if choices else [])], index=0,
                    explanation=unavailable_explanation(calculation, comparison))
    response["reply"]["comparison"] = deepcopy(comparison)
    return response


def _calculation_failure(store, cid, state, error):
    questions = {
        'negative_target': '줄이려는 금액이 현재 예산보다 커요. 줄일 금액을 현재 예산 이하로 정해 주세요.',
        'direction_conflict': '말씀하신 증감 방향과 최종 금액이 서로 달라요. 원하는 최종 예산은 얼마인가요?',
        'overlapping_living_requests': '생활비 전체 금액과 개별 항목 변경이 겹쳐요. 어느 쪽부터 바꿀까요?',
    }
    if str(error) in questions:
        return _ask(store, cid, state, 'clarification', questions[str(error)])
    reasons = {
        'funding_total_mismatch': '현재 항목별 예산의 합계가 전체 예산과 맞지 않아 계산을 멈췄어요.',
        'invalid_buckets': '저축·월 머니쿠션·여윳돈의 배정액을 확인해야 계산할 수 있어요.',
        'missing_buckets': '저축·월 머니쿠션·여윳돈 중 배정액이 빠진 항목이 있어요.',
    }
    _save(store, cid, state)
    return _result('failed', [reasons.get(str(error), '변경안을 계산하는 중 문제가 생겼어요.'),
                              '기존 예산과 요청 조건은 그대로 두었어요. 잠시 후 다시 시도해 주세요.'],
                   work_ref=state.get('work_ref'), error='simulation_failed')


def _envelope(state):
    return {"items": deepcopy(state.get("requests", [])),
            **({'spare_minimum': state['spare_minimum']} if state.get('spare_minimum') is not None else {}),
            **{key: list(state.get(key, [])) for key in LIST_FIELDS}}


def ask_spare_minimum(store, cid, state):
    resume = deepcopy(state['pending']) if state.get('pending', {}).get('kind') in {'sources', 'funding_scope', 'comparison'} else None
    return _ask(store, cid, state, 'financial_data', '여윳돈을 최소 얼마 남겨 둘까요?',
                [('cancel', '취소')], field='spare_minimum', resume_sources=resume,
                explanation={'brief': '남길 돈을 정한 뒤, 그 금액을 지키면서 변경안을 다시 계산할게요.'})


def check_amount_limit(store, cid, state, context, requests=None):
    from .funding import oversized_decrease, oversized_increase
    for index, item in enumerate(requests if requests is not None else state.get('requests', [])):
        current = (sum(context['allocations'].values()) if item.get('category') == 'LIVING' else
                   {**context['allocations'], **context['buckets']}.get(item.get('category')))
        reduction = ((0, 0) if current == 0 and item.get('direction') == 'down'
                     else oversized_decrease(context, item))
        if reduction is not None:
            amount, current = reduction
            if requests is not None:
                state['requests'] = deepcopy(requests)
            for key in ('comparison', 'financial_offer', 'quote'):
                state.pop(key, None)
            name = context.get('category_names', {}).get(item['category'], NAMES.get(item['category'], item['category']))
            if current == 0:
                other_codes = {row.get('category') for i, row in enumerate(state.get('requests', [])) if i != index}
                choices = [(code, context.get('category_names', {}).get(code, NAMES.get(code, code)))
                           for code, value in context['allocations'].items()
                           if value > 0 and code not in NON_BUDGET_CODES and code not in other_codes]
                return _ask(store, cid, state, 'category', f'{name}의 현재 예산은 0원이라 줄일 금액이 없어요. 다른 항목을 골라 주세요.',
                            choices, index=index, replace_category=True, explanation={})
            return _ask(store, cid, state, 'amount',
                        f'{name}의 현재 예산은 {current:,}원이에요. 요청하신 {amount:,}원은 현재 예산보다 커요. 얼마를 줄일까요?',
                        index=index, explanation={})
        exceeded = oversized_increase(context, item)
        if exceeded is None:
            continue
        increase, limit = exceeded
        if requests is not None:
            state['requests'] = deepcopy(requests)
        state['requests'][index].update(amount=None, amount_mode='by', direction='up')
        state['requests'][index].pop('automatic', None)
        state['use_available'] = False
        for key in ('comparison', 'financial_offer', 'quote'):
            state.pop(key, None)
        return _ask(store, cid, state, 'amount', '늘릴 금액을 다시 입력해 주세요.', index=index,
            max_amount=limit // 5000 * 5000,
            explanation={'brief': f'{increase:,}원을 늘리려면 다른 예산의 총액 {limit:,}원보다 더 많은 돈이 필요해요.',
                         'why': '현재 예산 안에서 돈을 옮기는 기능이라 전체 예산을 넘겨 늘릴 수는 없어요.',
                         'next': '금액을 다시 정하면 이미 쓴 돈과 남겨 둘 돈을 확인해 실제로 옮길 수 있는지 계산할게요.'})
    return None


def _remember(state):
    history = state.setdefault("history", [])
    history.append(deepcopy({key: value for key, value in state.items()
                             if key not in {"history", "dialogue_history", "proposal", "work_ref", "pending_edit"}}))
    del history[:-12]


def _edge_choices(names, category, *, categories_only=False):
    codes = [code for code in names if code not in SPECIAL_NAMES] if categories_only else list(SPECIAL_NAMES)
    return [(code, names[code]) for code in codes if code != category]


def _route_question(store, cid, state, names, item, direction, *, context=None, allocator=None):
    code = item["category"]
    field = "sources" if direction == "up" else "destinations"
    text = "늘릴 금액을 어디서 가져올까요?" if direction == "up" else "줄인 금액은 어떻게 할까요?"
    if context is not None and len(state.get('requests', [])) > 1:
        from .funding import simulate_funding
        calculation, choices = _calculation(context, state), []
        for value, label in _edge_choices(names, code):
            candidate = {**_envelope(state), field: [value]}
            result = simulate_funding(calculation, candidate, allocate_group_reduction=allocator)
            if result['status'] in {'proposed', 'partial'} and result.get('transfers'):
                choices.append((value, label))
        state.pop('comparison', None)
        return _ask(store, cid, state, field,
                    '각 항목의 요청 금액은 그대로 유지했어요. ' + text if choices else
                    '요청한 항목과 금액은 유지했어요. 현재 보호 조건 안에서 옮길 돈이 없어 금액이나 가져올 곳을 바꿔야 해요.',
                    choices)
    if context is not None and state.get('manual_living'):
        return _manual_living(store, cid, state, context)
    if code not in SPECIAL_NAMES and not state.get("funding_scope"):
        explanation = None
        choices = [("living", "생활비 내에서" if direction == "up" else "다른 생활비에 배분"),
                   ("spare", "여윳돈 가져오기" if direction == "up" else "여윳돈으로 남기기"),
                   ("extended", "저축·월 쿠션까지 재배분" if direction == "up" else "저축·월 쿠션에 배분"),
                   ("compare", "가능한 방법 추천받기")]
        if context is not None:
            from .comparison import compare_adjustments
            checked = compare_adjustments(_calculation(context, state), _envelope(state), allocate_group_reduction=allocator)
            available = {p['id'] for p in checked['plans'] if p.get('status') in {'ready', 'partial'} and p.get('amount', 0) > 0 and (p.get('result') or {}).get('changes')}
            choices = [(v, label) for v, label in choices if v in available or v == 'compare' and available]
            if (direction == 'up' and context.get('spending_status') == 'complete'
                    and 'spare' not in available and 'SPARE' not in state.get('excluded', [])
                    and context.get('bucket_status', {}).get('SPARE') == 'ready'
                    and context.get('bucket_available', {}).get('SPARE', 0) > 0):
                choices.append(('spare', '여윳돈에서 직접 정하기'))
            choices.append(('manual_living', '생활비 직접 조정'))
            explanation = unavailable_explanation(_calculation(context, state), checked)
            if not available:
                text = ('최신 내역을 확인해야 자동으로 추천할 수 있어요. 직접 조정할 항목과 금액은 정할 수 있어요.'
                        if context.get('spending_status') != 'complete' else '지금 조건으로는 자동으로 추천할 방법이 없어요. 줄여도 괜찮은 항목과 금액을 직접 정할 수 있어요.')
        return _ask(store, cid, state, "funding_scope", text,
                    choices, field=field, explanation=explanation)
    if code not in SPECIAL_NAMES and state.get("funding_scope") == "extended" and direction == "down":
        choices = [(candidate, names[candidate]) for candidate in ("SAVINGS", "CUSHION_MONTHLY")]
    else:
        choices = _edge_choices(names, code)
    if context is not None and code in context['allocations']:
        from .funding import available_adjustment
        calculation = _calculation(context, state)
        usable = []
        for value, label in choices:
            quote = available_adjustment(calculation, code, direction,
                sources=[value] if direction == 'up' else [], destinations=[value] if direction == 'down' else [],
                excluded=state.get('excluded', []), allocate_group_reduction=allocator, spare_minimum=state.get('spare_minimum'))
            if quote['status'] == 'ready' and quote['maximum'] > 0:
                usable.append((value, label))
        choices = [*usable, *([('compare', '가능한 방법 추천받기')] if usable else []), ('manual_living', '생활비 직접 조정')]
    elif code not in {"LIVING", "SPARE"}:
        choices.append(("compare", "가능한 방법 추천받기"))
    return _ask(store, cid, state, field, text, choices, index=state.get("focus", 0))


def _resolve_living_target(state):
    items, destinations = state.get('requests', []), state.get('destinations', [])
    if len(destinations) != 1 or destinations[0] in SPECIAL_NAMES:
        return False
    unspecified = [item for item in items if item.get('category') == 'LIVING'
                   and item.get('direction') == 'up' and item.get('amount') is None]
    selected = [item for item in items if item.get('category') == destinations[0] and item.get('direction') == 'up']
    if len(unspecified) != 1 or len(selected) > 1 or len(items) != len(unspecified) + len(selected):
        return False
    state['requests'] = [deepcopy(selected[0]) if selected else {**unspecified[0], 'category': destinations[0]}]
    state['destinations'] = []
    state['living_total'] = False
    state['focus'] = 0
    state['use_available'] = False
    return True


def _merge(state, data, pending):
    # Empty structured-output placeholders carry no request; partial requests and zero amounts do.
    fields = ("category", "amount", "amount_mode", "direction")
    if data.get('requests') or data.get('reference') == 'new' or any(data.get(field) for field in LIST_FIELDS):
        state.pop('plan_selected', None)
        state.pop('plan_alternatives', None)
    state["requests"] = [item for item in state.get("requests", []) if any(item.get(key) is not None for key in fields)]
    data = {**data, "requests": [item for item in data.get("requests", []) if any(item.get(key) is not None for key in fields)]}
    if data.get('reference') != 'new' and _resolve_living_target(state):
        pending = {**pending, 'index': 0}
    if (pending.get('kind') in {'amount', 'comparison'} and data.get('reference') == 'previous'
            and len(state['requests']) == len(data['requests']) == 1
            and state['requests'][0].get('category') not in SPECIAL_NAMES
            and data['requests'][0].get('category') == 'LIVING'):
        data = deepcopy(data)
        data['requests'][0]['category'] = state['requests'][0]['category']
    if (data.get("reference") == "previous" and pending.get("index") is None
            and len(data.get("requests", [])) == len(state.get("requests", [])) == 1
            and data["requests"][0].get("category") is None):
        data = deepcopy(data)
        data["requests"][0]["category"] = state["requests"][0].get("category")
    incoming = deepcopy(data.get("requests", []))
    if incoming:
        state.pop('expense_budget_base', None)
    old = state.setdefault("requests", [])
    if data.get("reference") == "new":
        for key in ("requests", "sources", "destinations", "excluded", "funding_scope", "quote",
                    "unlock_categories", "unlock_consents", "clear_targets", "use_available", "scope", "comparison", "partial",
                    "requested_view", "reason", "manual_living", "living_total", "spare_minimum", "planning",
                    "one_off", "expense_request", "set_aside_amount", "expense_allowed_sources", "increase_acknowledged"):
            state.pop(key, None)
        for key in ('financial_offer', 'financial_overrides', 'primary_request', 'refinement', 'permitted_sources', 'pending', 'allow_reserve_risk', 'manual_funding'):
            state.pop(key, None)
        old = state.setdefault("requests", [])
    elif data.get('request_update') == 'replace':
        old = state['requests'] = []
        retained = {row.get('category') for row in incoming}
        for field in ('sources', 'destinations'):
            retained.update(data.get(field) or state.get(field, []))
        state['unlock_categories'] = [code for code in state.get('unlock_categories', []) if code in retained]
        state['unlock_consents'] = {code: value for code, value in state.get('unlock_consents', {}).items() if code in retained}
        for key in ('quote', 'comparison', 'partial', 'living_total'):
            state.pop(key, None)
    if data.get('scope') == 'next_cycle':
        state.setdefault('planning', {})['scope'] = 'next_cycle'
    elif data.get('scope') in {'cycle', 'ongoing'}:
        state.pop('planning', None)
    if data.get('income_change') is not None:
        state.setdefault('planning', {})['income_change'] = deepcopy(data['income_change'])
    if data.get('one_off') is not None:
        state['one_off'] = data['one_off']
        if not data['one_off']:
            for key in ('one_off', 'expense', 'reserve_category'):
                state.get('planning', {}).pop(key, None)
            for key in ('expense_request', 'set_aside_amount', 'expense_allowed_sources'):
                state.pop(key, None)
    if data.get('set_aside_amount') is not None:
        state['set_aside_amount'] = data['set_aside_amount']
    if state.get('one_off') and len(incoming) == 1 and incoming[0].get('direction') == 'up':
        state['expense_request'] = deepcopy(incoming[0])
    if incoming:
        if len(incoming) == 1 and old and pending.get("index") is not None and data.get("reference") != "new":
            index = pending["index"]
            item = incoming[0]
            if (item.get("category") in (None, old[index].get("category"))
                    or pending.get('kind') == 'category' and (old[index].get('category') in {None, 'LIVING'}
                                                            or pending.get('replace_category'))):
                if pending.get('kind') == 'category':
                    state['living_total'] = item.get('category') == 'LIVING'
                old[index].update({key: value for key, value in item.items() if value is not None})
                if item.get("amount_mode") in {"to", "remaining", "percent_to"}:
                    old[index]["direction"] = item.get("direction")
                if item.get("amount") is not None:
                    old[index].pop("automatic", None)
                    if pending.get("kind") in {"amount", "available_quote", "comparison"} and old[index].get("amount_mode") in (None, "available"):
                        old[index]["amount_mode"] = "by"
                incoming = []
        for item in incoming:
            previous = next((candidate for candidate in old if candidate.get("category") == item.get("category")
                             and item.get("category") is not None), None)
            if previous is None:
                old.append(item)
            else:
                previous.update({key: value for key, value in item.items() if value is not None})
                if item.get("amount_mode") in {"to", "remaining", "percent_to"}:
                    previous["direction"] = item.get("direction")
                if item.get("amount") is not None:
                    previous.pop("automatic", None)
    for field in ("sources", "destinations"):
        if data.get(field):
            state[field] = list(data[field])
    explicitly_selected = set(data.get("sources", [])) | set(data.get("destinations", []))
    explicitly_selected.update(item["category"] for item in data.get("requests", []) if item.get("category"))
    excluded = (set(state.get("excluded", [])) - explicitly_selected) | set(data.get("excluded", []))
    state["excluded"] = sorted(excluded)
    for field in ("sources", "destinations"):
        state[field] = [code for code in state.get(field, []) if code not in excluded]
    state["requests"] = [item for item in old if item.get("category") not in excluded]
    for field in ("scope", "funding_scope"):
        if data.get(field) is not None:
            state[field] = data[field]
    if data.get("funding_scope") in {"living", "spare"}:
        field = pending.get("field")
        if field not in {"sources", "destinations"} and state["requests"]:
            item = state["requests"][min(state.get("focus", 0), len(state["requests"]) - 1)]
            if item.get("category") not in SPECIAL_NAMES and item.get("direction") in {"up", "down"}:
                field = "sources" if item["direction"] == "up" else "destinations"
        if field in {"sources", "destinations"} and not data.get(field):
            state[field] = ["LIVING" if data["funding_scope"] == "living" else "SPARE"]
    if data.get("use_available") is not None:
        state["use_available"] = data["use_available"]
        if data["use_available"] is False:
            state.pop("quote", None)
    if data.get("clear_targets"):
        state["clear_targets"] = list(data["clear_targets"])
        if not data.get("requests"):
            state["requests"] = []
    _resolve_living_target(state)
    if state.get('one_off') and incoming and len(state.get('requests', [])) == 1:
        changed = state['requests'][0]
        if changed.get('direction') == 'up':
            state['expense_request'] = deepcopy(changed)


def choice_input(state, picked):
    """Buttons and interpreted selections use the same state transition."""
    pending = state.get("pending", {})
    if picked not in dict(pending.get("choices", [])):
        raise ValueError("invalid_choice")
    kind, data = pending["kind"], {}
    if picked == "cancel":
        return {"cancel": True}
    if picked == 'manual_living':
        return {'manual_living': True}
    if picked == 'keep_spare':
        return {'keep_spare': True}
    if kind == 'salary_change' and picked == 'salary_plan':
        return {'action': 'salary_change'}
    if kind == 'increase_review':
        if picked == 'continue_increase':
            return {'acknowledge_increase': True}
        if picked == 'revise_increase':
            return {'revise_increase': True}
    if kind == 'manual_distribution' and picked == 'manual':
        state['requests'][0].update(amount=None, amount_mode='by')
        return {'manual_living': True}
    if picked == "compare":
        state.pop('manual_living', None)
        data["action"] = "compare"
    elif picked == 'distribute':
        data['action'] = 'distribute'
    elif kind == "partial":
        data["partial_action"] = picked
    elif kind == "comparison":
        if picked.startswith("plan_"):
            data["comparison_plan"] = picked[5:]
        elif picked == "back":
            data["action"] = "back"
        else:
            data["use_available"] = False
            selected = state["requests"][0]
            selected.update(amount=None, amount_mode="by")
            selected.pop("automatic", None)
            state.pop("comparison", None)
    elif kind in ("sources", "destinations"):
        data[kind] = [picked]
    elif kind == "funding_scope":
        data["funding_scope"] = 'extended' if picked in {'SAVINGS', 'CUSHION_MONTHLY'} else picked
        if picked in {'SAVINGS', 'CUSHION_MONTHLY'}:
            data[pending['field']] = [picked]
        if picked in ("living", "spare"):
            data[pending["field"]] = ["LIVING" if picked == "living" else "SPARE"]
    elif kind in ("category", "direction", "amount_mode"):
        data["requests"] = [{"category": None, "amount": None, "amount_mode": None, "direction": None,
                             kind: picked}]
    elif kind == "unlock":
        data["unlock"] = picked == "unlock"
    elif kind == "scope":
        data["scope"] = picked
    elif kind in ("amount", "available_quote"):
        data["use_available"] = picked in ("available", "preview")
        if picked == "manual" and state.get("requests"):
            selected = state["requests"][pending["index"]]
            selected.update(amount=None, amount_mode="by")
            selected.pop("automatic", None)
        if picked == "choose_source":
            state.pop("funding_scope", None)
            state.pop("sources", None)
            state.pop("destinations", None)
    return data


def handle_expanded(request, *, store, complete, get_context, simulate, propose, task_status, cancel, allocator=None,
                    comparison_handler=None, route_handler=None, discussion_handler=None, initial_data=None):
    """기존 라우터 응답 계약. 버튼 선택과 대기 상태도 서버 메모리에 보관한다."""
    cid = request["conversation_id"]
    state = deepcopy(store.get_agent_state(cid, "budget") or {})
    text, mode = (request.get("text") or "").strip(), request.get("mode", "continue")
    ref = request.get("work_ref") or state.get("work_ref")
    resumed = deepcopy(initial_data)
    if is_salary_resume(request, state):
        try:
            response = resume_salary(store, cid, state, get_context(), task_status, cancel)
        except Exception:
            return _result('awaiting_input', ['최신 예산을 확인하지 못했어요. 이전 조정 내용은 보관했어요. 다시 이어하기를 눌러 주세요.'],
                           choices=[('salary_resume', '이전 예산 조정 이어하기')],
                           work_ref=ref, error='salary_resume_unavailable')
        if response is not None:
            return response
        ref = state.get('work_ref')
        resumed = {}
    try:
        checked = _check(ref, task_status) if ref else None
    except Exception:
        return _result("failed", ["진행 중인 조정 상태를 확인하지 못했어요."], work_ref=ref, error="task_check_failed")
    if checked and checked["state"] in ENDED and not state.get("salary_handoff"):
        if checked["state"] == "canceled" and state.get("pending_edit") and mode != "cancel" and text not in CANCEL:
            resumed = state.pop("pending_edit")
            for key in ("proposal", "work_ref", "pending"):
                state.pop(key, None)
            ref = None
        else:
            return _finished(store, cid, ref, checked)
    if mode == "cancel" or text in CANCEL:
        if ref:
            try:
                cancel(ref)
                checked = _check(ref, task_status)
            except Exception:
                return _result("failed", ["취소 결과를 확인하지 못했어요."], work_ref=ref, error="task_check_failed")
            if checked["state"] not in ENDED:
                return _result("awaiting_input", ["취소 결과를 확인하고 있어요."], work_ref=ref,
                               explanation=step_explanation("canceling"))
            return _finished(store, cid, ref, checked)
        _save(store, cid, None)
        return _result("ended", ["예산 조정을 그만뒀어요."], explanation=step_explanation("canceled"))
    if checked and checked["state"] == "saving":
        return _result("awaiting_input", ["예산을 저장 중이에요. 결과 확인 뒤 이어갈게요."], work_ref=ref,
                       explanation=step_explanation("saving"))
    if state.get("pending_edit"):
        return _result("awaiting_input", ["이전 조정안의 취소 확인을 기다리고 있어요."], work_ref=ref,
                       explanation=step_explanation("canceling"))
    try:
        context = get_context()
        names = {**NAMES, **context.get("category_names", {}), **SPECIAL_NAMES}
        names.update({code: names.get(code, code) for code in context["allocations"]})
    except Exception:
        # Salary settings remain reachable even before the first budget exists.
        try:
            data = resumed or read(text, complete, categories={**NAMES, **SPECIAL_NAMES},
                                   **dialogue_context(state))
            if data.get('income_change') or data.get('action') == 'salary_change':
                return salary_reply(store, cid, state, {}, data)
        except InterpretationFailed:
            pass
        return _result("failed", ["현재 예산을 확인하지 못했어요."], work_ref=ref, error="context_unavailable")
    pending = state.get("pending", {})
    if not text and resumed is None:
        if pending:
            return _pending_reply(state, ref, context)
        if state.get("proposal"):
            return _result("replied", ["변경안을 확인해 주세요."], work_ref=ref, proposal=state["proposal"],
                           explanation=state["proposal"].get("explanation"))
        return _ask(store, cid, state, "request", "어떤 예산을 조정하고 싶으세요? 금액을 몰라도 괜찮아요.")
    if text in APPLY and pending.get("kind") != "partial":
        if pending:
            return _pending_reply(state, ref, context)
        if state.get("proposal"):
            return _result("replied", ["변경안을 확인한 뒤 확정 기능에서 적용해 주세요."], work_ref=ref,
                           proposal=state["proposal"], approve_required=True,
                           explanation=state["proposal"].get("explanation"))
        return _ask(store, cid, state, "request", "먼저 조정하고 싶은 내용을 알려 주세요.")
    picked = next((value for value, label in pending.get("choices", []) if text in (value, label)), None)
    data = resumed
    if data is None and picked is not None:
        data = choice_input(state, picked)
    if data is None and text == "뒤로":
        data = {"action": "back"}
    compact = text.translate(str.maketrans("", "", " ?!.,？"))
    if (data is None and pending.get("kind") == "partial"
            and (text in APPLY or re.fullmatch(r"(?:그냥)?(?:이렇게|이대로|그대로)(?:해줘|해주세요|진행해줘|진행해주세요)", compact))):
        data = {"partial_action": "partial_accept"}
    if (data is None and (pending or state.get("proposal"))
            and (compact in {"왜물어봐", "왜물어봐요", "왜이걸물어봐", "왜이걸물어봐요", "왜필요해", "왜필요해요",
                             "이선택하면뭐바뀌어", "이선택하면뭐가바뀌어", "이선택하면어떻게돼", "이카드설명해줘",
                             "자세히설명해줘", "상세근거보여줘", "자세히",
                             "추천이유", "추천이유알려줘", "추천이유설명해줘", "왜추천해", "왜추천해요"}
                 or re.fullmatch(r"(?:\d+안)?(?:예산|예산변화|변경내역)(?:보기|보여줘|알려줘)", compact)
                 or re.fullmatch(r"(?:왜)?\d+안(?:을|이|은|는)?(?:왜)?(?:추천|비추천)(?:해|해요|했어|했어요|이야|인가요)?", compact))):
        data = {"action": "explain"}
    hinted = hint_category(request, names)
    if data is None and hinted and compact == take_from_label(hinted, names).translate(str.maketrans("", "", " ?!.,？")):
        data = {"sources": [hinted], "reference": "previous"}  # [○○에서 가져오기] 버튼: 방금 본 카테고리를 재원으로
    if data is None:
        try:
            dialogue = dialogue_context(state)
            pending_context = dialogue['pending']
            recommendation = state.get('comparison', {}).get('comparison', {}).get('recommendation', {}).get('plan_id')
            if recommendation is not None:
                pending_context['recommended_choice'] = 'plan_' + recommendation
            data = read(text, complete, categories=names,
                        pending=pending_context,
                        previous=dialogue['previous'],
                        hints={"from": "spending", "category": hinted} if hinted else None)
        except InterpretationFailed as error:
            return _result("failed", ["요청을 이해하지 못했어요. 다시 알려 주세요."], work_ref=ref, error=str(error))
        if not data["mine"]:
            return not_mine(ref, data.get("handoff"), data.get("handoff_category"), names)
        if data["cancel"]:
            return handle_expanded({**request, "mode": "cancel"}, store=store, complete=complete,
                                   get_context=get_context, simulate=simulate, propose=propose,
                                   task_status=task_status, cancel=cancel, allocator=allocator)
        housing_outside_budget = ('HOUSING_TELECOM' not in context['allocations'] and
            any(item.get('category') == 'HOUSING_TELECOM' for item in data.get('requests', [])))
        if data.get('action') == 'fixed_expense' or housing_outside_budget:
            line = ('주거비·월세·관리비·통신요금은 고정지출로 따로 관리해서, 여기서 생활비 예산처럼 줄이거나 옮길 수 없어요. '
                    '실제 납부 금액이 바뀌었다면 예산의 고정지출 설정에서 수정해 주세요.'
                    if data.get('action') == 'fixed_expense' else
                    '주거·통신은 현재 조정할 수 있는 생활비 항목에 포함되어 있지 않아요. '
                    '월세·관리비·통신요금을 바꾸려는 경우에는 예산의 고정지출 설정에서 수정해 주세요.')
            return _ask(store, cid, state, 'fixed_expense',
                line, explanation={'title': '여기서 조정할 수 있는 예산',
                    'why': '생활비 예산은 월급에서 고정지출과 저축 등을 뺀 뒤 남은 돈으로 나눠요. 고정지출 금액을 바꾸는 작업과는 별개예요.',
                    'effect': '고정지출과 기존 예산은 아직 바뀌지 않았어요.',
                    'next': '이 대화에서는 식비·교통비·쇼핑 등 생활비 항목을 조정할 수 있어요.'})
        if data.get('income_change') or data.get('action') == 'salary_change':
            return salary_reply(store, cid, state, context, data)
        if state.get('manual_living') and data.get('requests'):
            data['action'] = None
            if len(data['requests']) > 1:
                state.pop('manual_living', None)
        if (pending.get('kind') == 'increase_review' and (data['unclear'] or data.get('reference') == 'unclear')
                and not data.get('requests') and not any(data.get(field) for field in LIST_FIELDS)
                and not data.get('scope') and not data.get('clear_targets') and data.get('unlock') is None):
            return decision_help(store, cid, state, context, allocator)
        if (data["unclear"] or data.get("reference") == "unclear") and not _explanation_only(data) and data.get('action') not in {'distribute', 'help_decide'}:
            if data.get('income_change'):
                state.setdefault('planning', {})['income_change'] = deepcopy(data['income_change'])
            if data.get('reference') == 'new' and data.get('requests') and not state.get('proposal'):
                _remember(state)
                _merge(state, data, pending)
            if not state.get("requests") and len(data.get("requests", [])) == 1:
                candidate = data["requests"][0]
                if (candidate.get("category") and candidate.get("direction") in {"up", "down"}
                        and candidate.get("amount") is None and data.get("reference") != "unclear"):
                    state["requests"] = deepcopy(data["requests"])
            return _ask(store, cid, state, "clarification",
                        (data["unclear"][0] if data["unclear"] else "앞선 요청을 바꾸는 말씀이신가요, 새 조정인가요?"))
    if NON_BUDGET_CODES.intersection([row.get('category') for row in data.get('requests', [])]
                                    + data.get('sources', []) + data.get('destinations', [])):
        return _result('unsupported', ['기타는 소비 내역을 분류하는 항목이라 예산을 배분하거나 가져올 수 없어요. 식비·교통비 같은 예산 항목을 골라 주세요.'], work_ref=ref)
    if (pending.get('kind') == 'increase_review' and data.get('action') in {'inspect', 'explain', 'help_decide'}
            and not data.get('requests') and not any(data.get(field) for field in LIST_FIELDS)
            and not data.get('income_change') and not data.get('scope')):
        return decision_help(store, cid, state, context, allocator)
    if data.get('action') == 'help_decide' and not (data.get('requests') or any(data.get(field) for field in LIST_FIELDS)
            or data.get('scope') or data.get('clear_targets') or data.get('unlock') is not None or data.get('approval')):
        if pending.get('kind') == 'amount' or pending.get('kind') == 'financial_data' and pending.get('field') == 'amount':
            data['action'] = 'compare'
        else:
            return decision_help(store, cid, state, context, allocator)
    if data.get('action') == 'help_decide':
        data['action'] = 'compare'
    if (data.get('action') == 'distribute' and len(data.get('destinations', [])) == 1
            and data.get('requests') and all(row.get('direction') == 'down' for row in data['requests'])):
        # A named single recipient needs calculation, not another allocation question.
        data['action'] = None
    if data.get('action') == 'distribute':
        return distribute(request, state, data, store=store, get_context=get_context, simulate=simulate, propose=propose)
    if (data.get('selected_choice') and data.get('request_update') != 'replace'
            and not data.get('requests') and not any(data.get(field) for field in LIST_FIELDS)):
        selected = data['selected_choice']
        if selected not in dict(pending.get('choices', [])):
            if not pending:
                return _ask(store, cid, state, 'request', '아직 고를 수 있는 조정안이 없어요. 어느 예산을 바꾸고 싶은지 알려 주세요.')
            return _pending_reply(state, ref, context)
        if not (data.get('scope') or data.get('clear_targets') or data.get('funding_scope') or data.get('unlock') is not None
                or data.get('action') in {'explain', 'recommend', 'inspect'}):
            data = choice_input(state, selected)
    if data.get('cancel'):
        return handle_expanded({**request, 'mode': 'cancel'}, store=store, complete=complete,
            get_context=get_context, simulate=simulate, propose=propose, task_status=task_status, cancel=cancel, allocator=allocator)
    if data.get('keep_spare'):
        return ask_spare_minimum(store, cid, state)
    if data.get('manual_living'):
        return _manual_living(store, cid, state, context)
    if data.get('income_change') or data.get('action') == 'salary_change' or data.get('salary_plan'):
        return salary_reply(store, cid, state, context, data)
    if data.get('action') == 'recommend':
        if _explanation_only({**data, 'action': 'explain'}) and state.get('comparison'):
            return recommendation_reply(state, context, ref)
        data['action'] = 'compare'
    if _explanation_only(data):
        if (pending.get('kind') in {'sources', 'funding_scope'} and state.get('requests')
                and state['requests'][0].get('direction') == 'up'):
            return _inspect_sources(store, cid, state, context, allocator)
        return _explain_current(state, context, text, ref)
    if pending.get("kind") == "partial":
        selection_only = (not data.get("requests") and not any(data.get(field) for field in LIST_FIELDS)
                          and not data.get("funding_scope") and not data.get("clear_targets")
                          and data.get("unlock") is None and data.get("scope") is None
                          and data.get("reference") != "new" and data.get("action") is None)
        if (selection_only and (data.get("approval") is True or data.get("use_available") is True)
                and compact.lower() not in {"네", "예", "응", "좋아", "좋아요", "ㅇㅇ", "yes", "ok"}):
            data["partial_action"] = "partial_accept"
        if data.get("partial_action") == "partial_accept":
            state.pop("pending", None)
            state.pop("partial", None)
            card = state["proposal"]
            card["can_approve"] = True
            card["explanation"] = {**card.get("explanation", {}),
                                   "next": "변경안을 최종 확정해 주세요. 다음 달 유지 여부도 확정 단계에서 선택해요."}
            _save(store, cid, state)
            return _result("replied", ["가능한 금액만 반영한 변경안을 확인했어요. 아직 저장하지 않았어요. 확정 기능에서 적용해 주세요."],
                           work_ref=ref, proposal=card, explanation=card.get("explanation"),
                           choices=[("apply", "이 안으로 적용"), ("cancel", "취소")])
        if data.get("action") == "compare" and not data.get("requests") and not any(data.get(field) for field in LIST_FIELDS):
            data["partial_action"] = "partial_compare"
        elif (data.get("approval") and not data.get("requests") and not any(data.get(field) for field in LIST_FIELDS)
              and not data.get("partial_action")):
            return _pending_reply(state, ref, context)
    if data.get("action") == "undo":
        return _ask(store, cid, state, "request", "이미 확정한 변경은 새 조정으로 되돌려요. 어떤 항목을 어느 금액으로 바꿀까요?")
    selected = set(data.get("sources", [])) | set(data.get("destinations", []))
    selected.update(item["category"] for item in data.get("requests", []) if item.get("category"))
    if selected & set(data.get("excluded", [])):
        return _ask(store, cid, state, "clarification", "같은 항목을 바꾸기와 제외하기로 함께 말씀하셨어요. 어느 쪽으로 할까요?")
    if (data.get('action') == 'inspect' and data.get('requests')
            and all(row.get('amount') is None and row.get('direction') == 'up' for row in data['requests'])):
        _merge(state, data, pending)
        return _inspect_sources(store, cid, state, context, allocator)
    if data.get('action') == 'inspect' and (any(data.get(field) for field in LIST_FIELDS)
            or data.get('scope') or data.get('funding_scope') or data.get('clear_targets')):
        data['action'] = 'compare'
    if data.get("action") == "inspect" and not data.get("requests"):
        if pending.get('kind') == 'destinations' and pending.get('compare_after_selection'):
            return _pending_reply(state, ref, context)
        if pending.get('kind') == 'comparison' and state.get('comparison', {}).get('context') == context:
            return _pending_reply(state, ref, context)
        if state.get('requests'):
            index = pending.get('index', state.get('focus', 0)) or 0
            if (state['requests'][index].get('direction') == 'up' and
                    (pending.get('kind') in {'sources', 'funding_scope'} or state['requests'][index].get('category') in context['allocations'])):
                return _inspect_sources(store, cid, state, context, allocator)
        from .funding import available_bucket_source
        lines = []
        for code in context.get('buckets', {}):
            if code in state.get('excluded', []) or any(
                    row.get('category') == code and row.get('direction') == 'up' for row in state.get('requests', [])):
                continue
            quote = available_bucket_source(_calculation(context, state), code,
                                            excluded=state.get('excluded', []), spare_minimum=state.get('spare_minimum'))
            amount = quote['maximum']
            lines.append(f"{names.get(code, code)} · 최대 {amount:,}원" if amount is not None
                         else f"{names.get(code, code)} · 사용 가능액 확인 필요")
        if lines:
            lines.append('저축·월 머니쿠션은 이번에 배정한 예산을 줄이는 방법이에요. 이미 모은 돈을 인출하지 않아요.')
        return _result("replied", lines or ["조정하고 싶은 항목을 알려 주시면 가능액을 확인할게요."], work_ref=ref, detail=True)
    if pending.get("kind") == "comparison" and not data.get("comparison_plan"):
        selecting = data.get("funding_scope") or data.get("sources") or data.get("destinations")
        if (selecting and not data.get("requests") and not data.get("excluded")
                and data.get("action") != "compare" and data.get("reference") != "new"):
            plans = state.get("comparison", {}).get("comparison", {}).get("plans", [])
            selectable = dict(pending.get("choices", []))
            matching = [plan for plan in plans if "plan_" + plan["id"] in selectable
                        and (not data.get("funding_scope") or plan.get("scope") == data["funding_scope"])
                        and all(not data.get(field) or set(plan.get("envelope", {}).get(field, [])) == set(data[field])
                                for field in ("sources", "destinations"))]
            if len(matching) == 1:
                data["comparison_plan"] = matching[0]["id"]
        elif data.get("approval") and not data.get("requests") and not any(data.get(field) for field in LIST_FIELDS):
            return _pending_reply(state, ref, context)
        elif data.get("excluded") or (data.get("requests") and not (
                (data.get('sources') or data.get('destinations'))
                and all(row.get('amount') is not None for row in data['requests']))):
            data["action"] = "compare"
    mutating = (data.get("requests") or data.get('income_change') or data.get('one_off') is not None or data.get('set_aside_amount') is not None or data.get("clear_targets") or data.get("funding_scope")
                or data.get('request_update') == 'replace'
                or any(data.get(field) for field in LIST_FIELDS) or data.get("scope") or data.get("action") == "back"
                or data.get("reference") == "new" or data.get("action") == "compare" or data.get("comparison_plan")
                or data.get("partial_action") in {"partial_amount", "partial_compare"})
    if state.get("proposal") and mutating:
        try:
            cancel(ref)
            checked = _check(ref, task_status)
        except Exception:
            return _result("failed", ["이전 변경안의 취소 결과를 확인하지 못했어요."], work_ref=ref, error="task_check_failed")
        if checked["state"] == "canceled":
            for key in ("proposal", "work_ref"):
                state.pop(key, None)
            ref = None
        elif checked["state"] in ENDED:
            return _finished(store, cid, ref, checked)
        else:
            state["pending_edit"] = deepcopy(data)
            _save(store, cid, state)
            return _result("awaiting_input", ["이전 변경안을 취소한 뒤 수정을 이어갈게요."], work_ref=ref,
                           explanation=step_explanation("canceling"))
    if state.get("proposal") and data.get("approval") and not mutating:
        return _result("replied", ["변경안의 확정 기능에서 적용해 주세요."], work_ref=ref,
                       proposal=state["proposal"], approve_required=True,
                       explanation=state["proposal"].get("explanation"))
    if data.get("action") == "back":
        history = state.get("history", [])
        if history:
            state = {**history.pop(), "history": history}
            state.pop("pending", None)
        else:
            return _ask(store, cid, state, "request", "돌아갈 선택이 없어요. 조정하고 싶은 내용을 알려 주세요.")
    else:
        _remember(state)
        if data.get("partial_action") in {"partial_amount", "partial_compare"}:
            partial = state.pop("partial")
            state["requests"] = deepcopy(partial["request"]["items"])
            for field in LIST_FIELDS:
                state[field] = list(partial["request"].get(field, []))
            state.pop("pending", None)
            if data["partial_action"] == "partial_amount":
                index = partial["index"]
                selected = state["requests"][index]
                selected["amount"] = None
                selected.pop("automatic", None)
                state["use_available"] = False
                return _ask(store, cid, state, "amount", "원하는 조정 금액을 다시 알려 주세요. 다른 유효한 조건은 유지할게요.", index=index)
            data = {"action": "compare"}
        elif mutating:
            state.pop("partial", None)
        if data.get("comparison_plan"):
            stored = state.get("comparison")
            if not stored or stored["context"] != context:
                state.pop("comparison", None)
                return _ask(store, cid, state, "request", "예산 조건이 바뀌었어요. 조정 방법을 다시 비교해 주세요.",
                            explanation={"why": "비교한 뒤 기준 예산·지출·설정이 바뀌었어요.",
                                         "effect": "이전 비교안은 적용하지 않았어요.",
                                         "next": "최신 조건으로 조정 방법을 다시 비교해 주세요."})
            if "plan_" + data["comparison_plan"] not in dict(pending.get("choices", [])):
                return _pending_reply(state, ref, context)
            selected_plan = next((plan for plan in stored["comparison"]["plans"]
                                  if plan.get("id") == data["comparison_plan"]
                                  and plan.get("id") in PLAN_IDS
                                  and plan.get("status") in {"ready", "partial", "needs_destination"}), None)
            if not selected_plan or not selected_plan.get("envelope"):
                return _pending_reply(state, ref, context)
            selected_request = selected_plan['envelope']
            state["requests"] = deepcopy(selected_plan["envelope"]["items"])
            for field in LIST_FIELDS:
                state[field] = list(selected_plan["envelope"].get(field, []))
            state["funding_scope"] = selected_plan.get("scope", selected_plan["id"])
            state['plan_selected'] = True
            state['plan_alternatives'] = {'context': deepcopy(context),
                'selected': {key: deepcopy((selected_plan.get('result') or {}).get(key)) for key in ('allocations', 'buckets')},
                'available': has_alternative(stored['comparison'], selected_plan.get('result'))}
            state['plan_request'] = deepcopy(stored.get('request') or selected_plan['envelope'])
            state.pop("comparison", None)
            state.pop("quote", None)
            if stored['comparison'].get('amount_was_given') is False:
                # Choosing a funding method does not confirm a server-generated example amount.
                for item in state['requests']:
                    item.update(amount=None, amount_mode='by')
                    item.pop('automatic', None)
                state['use_available'] = False
                state.pop('plan_selected', None)
                state.pop('plan_request', None)
                state.pop('plan_alternatives', None)
                return ask_missing_amount(store, cid, state, context, confirm_example=True)
            data = {key: data[key] for key in ("scope",) if data.get(key) is not None}
        elif mutating:
            state.pop("comparison", None)
        quote = state.get("quote")
        quote_accepted = (pending.get("kind") == "available_quote" and (data.get("use_available") or data.get("approval"))
                          and not data.get("requests") and not any(data.get(field) for field in LIST_FIELDS)
                          and not data.get("funding_scope") and data.get("reference") != "new")
        if quote_accepted:
            if not quote or quote["context"] != context:
                state.pop("quote", None)
                return _ask(store, cid, state, "request", "예산 조건이 바뀌었어요. 가능액을 다시 확인해 주세요.",
                            explanation={"why": "가능액을 계산한 뒤 기준 예산·지출·설정이 바뀌었어요.",
                                         "effect": "이전 가능액은 적용하지 않았어요.",
                                         "next": "최신 조건으로 가능액을 다시 확인해 주세요."})
            state["requests"] = deepcopy(quote["envelope"]["items"])
            for field in LIST_FIELDS:
                state[field] = list(quote["envelope"].get(field, []))
            state.pop("quote", None)
            data = {key: data[key] for key in ("scope",) if data.get(key) is not None}
        _merge(state, data, pending)
    requests = state.setdefault("requests", [])
    if state.get('one_off') and state.get('scope') == 'next_cycle':
        state.setdefault('planning', {})['one_off'] = True
        expense = state.get('expense_request')
        if expense and expense.get('category') == 'TRAVEL_LODGING':
            state['planning']['reserve_category'] = 'TRAVEL_LODGING'
        if expense and expense.get('amount') is None:
            return _ask(store, cid, state, 'amount', f'{NAMES.get(expense["category"], expense["category"])}에 총 얼마를 쓸 예정인가요?', index=0)
        if expense and expense.get('amount') is not None:
            if expense.get('amount_mode') != 'by':
                return _ask(store, cid, state, 'clarification', '이번 한 번의 지출에 필요한 총금액을 알려 주세요.')
            total = expense['amount']
            saved = state.get('set_aside_amount')
            if saved is None:
                return _ask(store, cid, state, 'prepared_funds',
                            f'{NAMES.get(expense["category"], expense["category"])}에 쓸 {total:,}원 중 따로 마련해 둔 돈이 있나요? 이번 예산에 포함되지 않은 돈 중 쓸 금액을 알려 주세요. 없으면 “없어”라고 해 주세요.')
            if saved > total:
                return _ask(store, cid, state, 'prepared_funds', f'필요한 금액은 {total:,}원이에요. 마련한 돈 중 실제로 쓸 금액을 그 안에서 정해 주세요.')
            state['planning']['expense'] = {'category': expense['category'], 'total': total, 'set_aside': saved, 'additional': total - saved}
            if saved == total:
                _save(store, cid, state)
                return _result('replied', [f'따로 마련한 {saved:,}원으로 이번 지출을 충당할 수 있어요. 추가 예산 조정은 필요하지 않아요.'], work_ref=ref)
            remaining = {**expense, 'amount': total - saved}
            if 'expense_budget_base' in state:
                remaining.update(amount=state['expense_budget_base'] + total - saved, amount_mode='to')
            state['requests'] = requests = [remaining]
        allowed_sources = set(state.get('expense_allowed_sources', [])) | set(data.get('sources', []))
        if data.get('funding_scope') == 'extended':
            allowed_sources.update(('SAVINGS', 'CUSHION_MONTHLY'))
        state['expense_allowed_sources'] = sorted(allowed_sources)
        state['excluded'] = sorted((set(state.get('excluded', [])) - allowed_sources) | set(data.get('excluded', [])) | ({'SAVINGS', 'CUSHION_MONTHLY'} - allowed_sources))
        _save(store, cid, state)
    if not state.get('planning') and context.get('planning_base'):
        context = deepcopy(context['planning_base'])
    if state.get('planning'):
        from ..adjustment_schedule import planning_context
        try:
            context = planning_context(context, state['planning'])
        except ValueError as error:
            lines = {'planning_income_required': '등록된 월 실수령액을 먼저 확인해 주세요.',
                     'planning_income_shortfall': '예상 월급보다 현재 배분 합계가 커요. 줄일 예산도 함께 정해야 해요.',
                     'planning_income_invalid': '변경 후 월급은 0원보다 커야 해요.',
                     'planning_date_required': '월급 변화는 다음 급여부터 적용할까요?'}
            return _ask(store, cid, state, 'clarification', lines.get(str(error), '예상 월급과 적용 시점을 확인해 주세요.'))
        if ((context['monthly_income'] < context['income_before'] or context['buckets']['SPARE'] < 0)
                and not state.get('destinations')
                and requests and all(row.get('direction') == 'down' for row in requests)):
            state['destinations'] = ['SPARE']
        if data.get('income_change') and context['monthly_income'] > context['income_before']:
            previous_spare = max(context['buckets']['SPARE'] - (context['monthly_income'] - context['income_before']), 0)
            state['spare_minimum'] = max(state.get('spare_minimum', 0), previous_spare)
            if (not state.get('sources') and 'SPARE' not in state.get('excluded', [])
                    and any(row.get('direction') == 'up' for row in requests)):
                state['sources'] = ['SPARE']
            if state.get('sources') == ['SPARE']:
                state['funding_scope'] = 'spare'
        _save(store, cid, state)
    if NON_BUDGET_CODES.intersection([row.get('category') for row in requests] + state.get('sources', []) + state.get('destinations', [])):
        return _result('unsupported', ['기타는 소비 내역을 분류하는 항목이라 예산을 배분하거나 가져올 수 없어요. 식비·교통비 같은 예산 항목을 골라 주세요.'], work_ref=ref)
    names = {code: name for code, name in names.items() if code not in NON_BUDGET_CODES}
    allowed = set(names) | NON_BUDGET_CODES
    if (any(item.get("category") is not None and item["category"] not in allowed for item in requests)
            or any(set(state.get(field, [])) - allowed for field in LIST_FIELDS)):
        return _result("unsupported", ["말씀하신 항목은 현재 예산에서 찾지 못했어요. 현재 예산에 표시된 이름으로 다시 알려 주세요."],
                       work_ref=ref,
                       explanation={'title': '조정할 항목 확인', 'why': '현재 예산에 있는 항목의 금액만 바꿀 수 있어요.',
                                    'effect': '기존 예산은 바뀌지 않았어요.', 'next': '조정할 수 있는 항목: ' + ' · '.join(names.values())})
    if discussion_handler:
        discussion = discussion_handler(store, cid, state, context, data)
        if discussion is not None:
            return discussion
    from .funding import large_increase_warning
    warning = large_increase_warning(context, requests, reason=state.get('reason'))
    if warning:
        review = {'requests': requests, 'income': context.get('monthly_income'), 'planning': state.get('planning')}
        if (data.get('acknowledge_increase') and pending.get('kind') == 'increase_review'
                and pending.get('review') == review):
            state['increase_acknowledged'] = deepcopy(review)
        if state.get('increase_acknowledged') != review:
            if data.get('revise_increase'):
                return _ask(store, cid, state, 'increase_review',
                            '어느 항목을 얼마 늘릴지 다시 알려 주세요. 기존 요청은 아직 적용하지 않았어요.',
                            review=deepcopy(review))
            return _ask(store, cid, state, 'increase_review', warning,
                        [('revise_increase', '금액 다시 정하기'), ('continue_increase', '주의사항 확인 후 계속')],
                        explanation={'title': '큰 증액을 먼저 확인하는 이유',
                                     'why': '같은 월급에서 한쪽 예산을 크게 늘리면 다른 곳에 쓸 돈이나 저축이 줄어들 수 있어요.',
                                     'effect': '계속해도 예산은 바로 바뀌지 않아요. 다음 단계에서 가능한 변경안을 확인해요.', 'next': ''},
                        review=deepcopy(review))
    else:
        state.pop('increase_acknowledged', None)
    if not requests and not state.get("clear_targets"):
        return _ask(store, cid, state, "category", "생활비·저축·월 쿠션 중 무엇을 조정할까요?", list(SPECIAL_NAMES.items()), index=0)
    consents = state.get("unlock_consents", {})
    if consents and (any(code not in context.get("locks", {}) for code in consents)
                     or _lock_snapshot(context, list(consents)) != consents):
        for key in ("unlock_categories", "unlock_consents"):
            state.pop(key, None)
        return _ask(store, cid, state, "request", "잠금 조건이 바뀌었어요. 잠금 해제를 다시 확인해 주세요.",
                    explanation={"why": "동의한 뒤 잠금 금액이나 잠금 버전이 바뀌었어요.",
                                 "effect": "이전 동의로 잠금을 해제하지 않아요.",
                                 "next": "현재 잠금 조건을 확인하고 해제 여부를 다시 선택해 주세요."})
    if pending.get("kind") == "unlock" and data.get("unlock") is not None:
        if not data["unlock"]:
            state["requests"] = []
            return _ask(store, cid, state, "request", "잠금은 유지할게요. 다른 항목을 조정하거나 취소해 주세요.")
        codes = pending["categories"]
        if any(code not in context.get("locks", {}) for code in codes) or _lock_snapshot(context, codes) != pending["lock_snapshot"]:
            return _ask(store, cid, state, "request", "잠금 조건이 바뀌었어요. 변경 요청을 다시 확인해 주세요.",
                        explanation={"why": "질문에 표시한 잠금과 현재 잠금의 금액 또는 버전이 달라졌어요.",
                                     "effect": "지금 답으로 이전 잠금을 해제하지 않아요.",
                                     "next": "현재 잠금 조건으로 변경 요청을 다시 확인해 주세요."})
        state["unlock_categories"] = list(dict.fromkeys([*state.get("unlock_categories", []), *codes]))
        state.setdefault("unlock_consents", {}).update(pending["lock_snapshot"])
    calculation = _calculation(context, state)
    limit_reply = check_amount_limit(store, cid, state, context)
    if limit_reply is not None:
        return limit_reply
    if pending.get('kind') == 'amount' and data.get('action') == 'compare':
        state['use_available'] = True
    amount_reply = ask_missing_amount(store, cid, state, context)
    if amount_reply is not None:
        return amount_reply
    if (state.get('destinations') == ['LIVING'] and requests
            and all(row.get('direction') == 'down' and row.get('category') in context['allocations']
                    and row.get('amount') is not None for row in requests)):
        return distribute(request, state, {}, store=store, get_context=lambda: context, simulate=simulate, propose=propose)
    if (len(requests) == 1 and requests[0].get('direction') == 'down' and requests[0].get('amount') is None
            and requests[0].get('category') in names and len(state.get('destinations', [])) == 1
            and state['destinations'][0] in names and state['destinations'][0] != requests[0]['category']
            and state['destinations'][0] != 'LIVING' and not state.get('use_available')
            and requests[0].get('amount_mode') != 'available' and data.get('action') != 'compare'):
        return distribute(request, state, {}, store=store, get_context=lambda: context, simulate=simulate, propose=propose)
    resume_comparison = (pending.get('compare_after_selection') and data.get('destinations')
                         and data.get('reference') != 'new' and data.get('action') not in {'back', 'undo'})
    if (data.get("action") == "compare" or resume_comparison) and not (len(requests) > 1 and all(item.get('amount') is not None for item in requests)):
        return (comparison_handler or _show_comparison)(store, cid, state, context, calculation, allocator)
    for index, item in enumerate(requests):
        state["focus"] = index
        code, direction = item.get("category"), item.get("direction")
        if item.get('amount') is not None and item.get('amount_mode') in {'to', 'remaining', 'percent_to'}:
            from .funding import _data, _value
            try:
                checked = _data(calculation, {'items': [item]})
                difference = checked['targets'][code] - _value(checked, checked['before'], code)
                direction = 'up' if difference > 0 else 'down' if difference < 0 else None
                item['direction'] = direction
            except ValueError:
                pass  # Invalid amounts are reported by the existing simulation validation.
        if code is None:
            return _ask(store, cid, state, "category", "어떤 항목을 조정할까요?", list(names.items()), index=index)
        if direction is None and item.get("amount_mode") in (None, "by", "percent_by", "available"):
            return _ask(store, cid, state, "direction", "늘릴까요, 줄일까요?", [("up", "늘리기"), ("down", "줄이기")], index=index)
        opposite = "down" if direction == "up" else "up"
        paired = any(other is not item and other.get("direction") == opposite and other.get("amount") is not None for other in requests)
        field = "sources" if direction == "up" else "destinations"
        missing_amount = item.get("amount") is None or item.get("amount_mode") == "available"
        if code in calculation.get("locks", {}):
            return _ask(store, cid, state, "unlock", f"{names[code]} {context['locks'][code]:,}원이 잠겨 있어요. 승인 시 해제할까요? 다음 주기에도 이어져요.",
                        [("unlock", "승인 시 잠금 해제"), ("keep_lock", "잠금 유지")],
                        categories=[code], lock_snapshot=_lock_snapshot(context, [code]))
        if (direction and not state.get(field) and not paired and (code not in SPECIAL_NAMES or not missing_amount)
                and not (missing_amount and state.get("use_available") is False)):
            return (route_handler or _route_question)(store, cid, state, names, item, direction, context=context, allocator=allocator)
        if state.get("destinations") == ["LIVING"]:
            return _ask(store, cid, state, "destinations", "어느 생활비 항목에 배분할까요?",
                        _edge_choices(names, code, categories_only=True), index=index)
        if code == 'LIVING' and direction == 'up' and missing_amount and not state.get('destinations'):
            return _living_target(store, cid, state, context)
        if code == "LIVING" and direction == "up" and not state.get("destinations"):
            return _living_target(store, cid, state, context)
        if missing_amount:
            use_available = (state.get("use_available") is True or item.get("amount_mode") == "available"
                             or state.get("use_available") is not False and code not in SPECIAL_NAMES and state.get(field))
            if (not use_available and direction == 'up' and route_handler is not None and code != 'LIVING'
                    and not state.get('over_budget')):  # 초과 진입은 희망 금액을 먼저 묻는다
                return route_handler(store, cid, state, names, item, direction, context=context, allocator=allocator)
            if not use_available:
                amount_question = ("새 목표 금액을 얼마로 할까요?" if item.get("amount_mode") in {"to", "remaining", "percent_to"}
                                   else "얼마나 " + ("늘릴까요?" if direction == "up" else "줄일까요?"))
                return _ask(store, cid, state, "amount", amount_question + " 금액을 몰라도 가능액을 확인할 수 있어요.",
                            [("available", "가능한 금액 확인")], index=index)
            if direction and not state.get(field) and not paired:
                return (route_handler or _route_question)(store, cid, state, names, item, direction, context=context, allocator=allocator)
            if len(requests) != 1:
                return _ask(store, cid, state, "amount", f"{names[code]}에서 옮길 금액을 얼마로 할까요?", index=index,
                            explanation={'brief': '여러 항목을 한 번에 조정할 때는 항목별 금액을 직접 정해야 해요.'})
            from .funding import available_adjustment
            try:
                available = available_adjustment(calculation, code, direction,
                    sources=state.get("sources", []), destinations=state.get("destinations", []),
                    excluded=state.get("excluded", []), allocate_group_reduction=allocator, spare_minimum=state.get('spare_minimum'))
            except ValueError:
                return _result('failed', ['옮길 수 있는 금액을 계산하는 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요.'],
                               error='simulation_failed')
            if available["status"] != "ready":
                from .explanations import result_explanation
                explanation = result_explanation(context, {**available, "requests": _envelope(state)})
                return _ask(store, cid, state, field, explanation['brief'] + ' ' + explanation['next'],
                            _edge_choices(names, code), index=index, explanation=explanation)
            maximum = available["maximum"]
            if type(maximum) is not int or maximum < 0 or maximum and not available.get("envelope"):
                return _result("failed", ["가능액 검수 결과를 확인하지 못했어요."], error="invalid_available_result")
            if not maximum:
                from .explanations import result_explanation
                return _ask(store, cid, state, field, "현재 선택한 범위에서 옮길 수 있는 금액은 0원이에요. 재원을 바꿀까요?",
                            _edge_choices(names, code), index=index,
                            explanation=result_explanation(context, {**available, "status": "infeasible", "requests": _envelope(state)}))
            state["quote"] = {"context": deepcopy(context), "envelope": deepcopy(available["envelope"])}
            return _ask(store, cid, state, "available_quote", f"보호 조건을 지키며 최대 {maximum:,}원까지 조정할 수 있어요. 필요한 금액의 권고가 아닌 최대 가능액이에요. 이 금액으로 변경안을 볼까요?",
                        [("preview", "이 금액으로 변경안 보기"), ("manual", "금액 직접 입력"), ("choose_source", "재원 다시 선택")], index=index)
        if item.get("amount_mode") is None:
            return _ask(store, cid, state, "amount_mode", "말씀한 금액은 어떤 뜻인가요?",
                        [("by", "말한 금액만큼 증감"), ("to", "말한 금액으로 변경"), ("remaining", "남은 금액으로 지정")], index=index)
    clear = state.get("clear_targets", [])
    if any(code not in context.get("category_targets", {}) and code not in context["allocations"] for code in clear):
        return _result("unsupported", ["해제할 목표 항목을 확인해 주세요."])
    scope = state.get("scope", "cycle")
    if clear and scope == "ongoing" and set(clear) & {item["category"] for item in requests}:
        return _ask(store, cid, state, "scope", "이번 예산만 바꿀까요?", [("cycle", "이번 주기만")],
                    explanation={'brief': '다음 예산에도 같은 금액을 유지하는 설정과, 그 설정을 해제하는 요청을 함께 받았어요.'})
    try:
        result = simulate(calculation, _envelope(state))
    except ValueError as error:
        return _calculation_failure(store, cid, state, error)
    if result.get("status") in {"needs_source", "needs_destination"}:
        if result["status"] == "needs_destination" and requests[0]["category"] == "LIVING":
            return _ask(store, cid, state, "destinations", "어느 생활비 항목에 배분할까요?",
                        _edge_choices(names, "LIVING", categories_only=True), index=0)
        direction = "up" if result["status"] == "needs_source" else "down"
        return (route_handler or _route_question)(store, cid, state, names, requests[0], direction, context=context, allocator=allocator)
    if result.get('status') == 'needs_data' and any(c.get('code') == 'spending_unverified' for c in result.get('conflicts', [])):
        return _manual_living(store, cid, state, context)
    if result.get("status") not in {"proposed", "partial", "unchanged"}:
        from .explanations import result_explanation
        explanation = result_explanation(context, result)
        return _ask(store, cid, state, "request", explanation['brief'] + ' ' + explanation['next'],
                    explanation=explanation)
    if (result['status'] == 'partial' and comparison_handler is not None and len(requests) == 1 and not state.get('plan_selected')
            and requests[0].get('direction') == 'up' and not state.get('manual_funding') and not state.get('manual_living')
            and not any(source in context['allocations'] for source in state.get('sources', []))):
        return comparison_handler(store, cid, state, context, calculation, allocator)
    targets = _target_changes(context, result, scope, requests, clear)
    if result["status"] == "unchanged" and not targets:
        _save(store, cid, None)
        return _result("ended", ["현재 예산과 같고 변경할 목표도 없어요."], explanation=step_explanation("unchanged"))
    try:
        card = propose(context, result, scope=scope, unlock_categories=state.get("unlock_categories", []), target_changes=targets)
        if not card.get("proposal_id") or not card.get("task_ref"):
            raise ValueError("invalid_proposal")
    except ValueError:
        return _result("failed", ["조정안을 만들지 못했어요. 기존 예산은 그대로예요."], error="proposal_failed")
    from .explanations import result_explanation
    explanation = result_explanation(context, {**result, "target_changes": targets,
                                               "unlock_categories": state.get("unlock_categories", [])})
    card["explanation"] = deepcopy(explanation)
    state.update(proposal=deepcopy(card), work_ref=card["task_ref"], proposal_request=deepcopy(_envelope(state)))
    for key in ('primary_request', 'refinement', 'permitted_sources', 'financial_offer'):
        state.pop(key, None)
    state.pop("pending", None)
    state.pop("quote", None)
    lines = ["검수한 변경안이에요. 확정 전까지 기존 예산은 그대로예요."]
    if result["status"] == "partial":
        shortfalls = deepcopy(result.get("shortfalls", []))
        for shortfall in shortfalls:
            lines.append(f"{names.get(shortfall['category'], shortfall['category'])}: 요청 {shortfall['requested_amount']:,}원 중 "
                         f"{shortfall['applied_amount']:,}원까지 가능하고 {shortfall['missing_amount']:,}원이 부족해요.")
        index = next((index for index, item in enumerate(requests)
                      if shortfalls and item["category"] == shortfalls[0]["category"]), 0)
        state["partial"] = {"request": _envelope(state), "shortfalls": shortfalls, "index": index,
                            "context": deepcopy(context), "explanation": deepcopy(explanation)}
        if len(requests) == 1:
            from .comparison import compare_adjustments
            try:
                alternatives = compare_adjustments(calculation, _envelope(state), allocate_group_reduction=allocator)
            except ValueError:
                alternatives = None
            if isinstance(alternatives, dict) and alternatives.get("plans"):
                state["partial"]["alternatives"] = deepcopy(alternatives)
            else:
                state["partial"]["alternatives_notice"] = "다른 재원의 변경 금액은 아직 확인하지 못했어요. 재원을 직접 선택해 주세요."
        else:
            state["partial"]["alternatives_notice"] = "여러 항목의 재원별 비교는 아직 지원하지 않아요. 항목 하나씩 비교할 수 있어요."
        response = _ask(store, cid, state, "partial", "가능한 금액만 반영할지, 희망 금액이나 재원을 바꿀지 골라 주세요.",
                        [("partial_accept", "가능한 금액만 반영"), ("partial_amount", "희망 금액 다시 입력"),
                         ("partial_compare", "다른 재원 비교")], explanation=explanation, index=index)
        response = _pending_reply(state, card["task_ref"], context)
        response["reply"]["lines"] = [*lines, state["pending"]["line"]]
        return response
    _save(store, cid, state)
    return _result("replied", lines, work_ref=card["task_ref"], proposal=card,
                   choices=[("apply", "이 안으로 적용"), ("cancel", "취소")], explanation=explanation)
