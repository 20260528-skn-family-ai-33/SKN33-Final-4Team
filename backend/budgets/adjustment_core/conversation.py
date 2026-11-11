"""확장 예산 조정 대화. 금액 제안은 서버 결과이며 이 모듈은 승인 포트를 갖지 않는다."""
from copy import deepcopy
import re

from .agent import APPLY, CANCEL, ENDED, _ask, _check, _finished, _lock_snapshot, _result, _save
from .interpretation import InterpretationFailed, read
from .explanations import step_explanation


SPECIAL_NAMES = {"LIVING": "생활비", "SAVINGS": "저축", "CUSHION_MONTHLY": "월 머니쿠션", "SPARE": "여윳돈"}
LIST_FIELDS = ("sources", "destinations", "excluded")
COMPARE = {"비교하고정하기", "잘모르겠어요", "잘모르겠어", "잘모르겠는데", "잘모르겠는데요", "모르겠어요", "모르겠어", "뭐가제일나아", "뭐가제일나아요",
           "어떤게제일나아", "어떤게나아", "세가지안비교", "세가지안비교해줘", "세가지안을비교해줘", "비교해줘", "추천해줘"}


def hint_category(request, names):
    """소비 분석에서 돌아올 때 라우터가 넘긴 카테고리 (request["hints"] = {"from": "spending", "category": 코드}). 모르는 코드는 버린다"""
    hints = request.get("hints") or {}
    code = hints.get("category") if hints.get("from") == "spending" else None
    return code if isinstance(code, str) and code in names and code not in SPECIAL_NAMES else None


def take_from_label(code, names):
    """소비 분석 답의 [○○에서 가져오기] 버튼 이름 (라우터 문구와 같은 모양). 이 이름 그대로 오면 LLM 없이 재원으로 본다"""
    return f"{names[code]}에서 가져오기"


def not_mine(ref=None, to=None, category=None, names=()):
    """'내 일 아님' 결과. 조회 질문이면 어디로 갈지(suggest)를 같이 알려 라우터가 LLM 없이 넘긴다. 저장된 상태는 바꾸지 않는다"""
    if to != "spending":
        return _result("not_mine", work_ref=ref)
    known = category if category in names and category not in SPECIAL_NAMES else None
    return _result("not_mine", work_ref=ref, suggest={"to": "spending", "category": known})


def _pending_reply(state, ref=None, context=None):
    pending = state["pending"]
    response = _result("awaiting_input", [pending["line"]], work_ref=ref, choices=pending["choices"],
                       explanation=pending.get("explanation"), choice_descriptions=pending.get("choice_descriptions"))
    if pending["kind"] == "comparison" and state.get("comparison"):
        response["reply"]["comparison"] = deepcopy(state["comparison"]["comparison"])
    if pending["kind"] == "financial_selection" and state.get("financial_offer"):
        response["reply"]["comparison"] = deepcopy(state["financial_offer"]["comparison"])
    if pending['kind'] == 'distribution':
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


def _explanation_only(data):
    return (data.get("action") == "explain" and not data.get("requests") and not data.get("clear_targets")
            and not any(data.get(field) for field in LIST_FIELDS) and not data.get("funding_scope")
            and not data.get("scope") and data.get("unlock") is None and data.get("use_available") is None
            and data.get("reference") != "new")


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
    response["reply"].pop("comparison", None)
    stored = state["comparison"]
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
                  and 'LIVING' not in excluded and code not in context.get('locks', {})]
    categories.extend(code for code in context['buckets'] if code not in source_codes and code not in excluded
                      and context.get('bucket_status', {}).get(code) == 'ready')
    return {'source': sources[0]['category'], 'sources': sources,
            'amount': sum(row['amount'] for row in sources), 'categories': categories}


def distribute(request, state, data, *, store, get_context, simulate, propose, **ports):
    context = get_context()
    cid = request['conversation_id']
    pending = state.get('pending', {})
    if pending.get('kind') in {'unlock', 'partial', 'financial_risk', 'financial_partial'}:
        return _pending_reply(state, state.get('work_ref'), context)
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
    if not donors or any(item.get('amount') is None for item in donors):
        return _ask(store, cid, state, 'request', '어느 항목에서 얼마를 줄여 나눌까요?')
    try:
        fields = distribution_fields(context, donors, excluded)
    except (ValueError, KeyError):
        return _ask(store, cid, state, 'request', '나눌 금액을 확인해 주세요. 5,000원 단위로 정할 수 있어요.')
    categories, total = fields['categories'], fields['amount']
    if not categories:
        return _ask(store, cid, state, 'request', '현재 받을 수 있는 항목이 없어요. 유지할 항목이나 옮길 곳을 다시 정해 주세요.')
    if data.get('scope'):
        state['scope'] = data['scope']
    if not receiving or any(item.get('amount') is None for item in receiving):
        state['requests'] = deepcopy(donors)
        state['excluded'] = sorted(excluded)
        source_names = '·'.join(names.get(row['category'], row['category']) for row in fields['sources'])
        response = _ask(store, cid, state, 'distribution', f"{source_names}에서 줄일 {total:,}원을 어디에 나눌까요?",
                        distribution_input=fields, context=deepcopy(context),
                        explanation={'brief': '받을 항목과 금액을 직접 정해 주세요.'})
        response['reply']['distribution_input'] = fields
        return response
    codes = [item.get('category') for item in receiving]
    valid = (len(codes) == len(set(codes)) and all(code in categories for code in codes)
             and all(type(item.get('amount')) is int and item['amount'] > 0 and item['amount'] % 5000 == 0
                     and item.get('amount_mode') == 'by' for item in receiving)
             and sum(item['amount'] for item in receiving) == total)
    if not valid or pending.get('kind') == 'distribution' and pending.get('context') != context:
        response = distribute(request, state, {**data, 'requests': donors}, store=store, get_context=get_context,
                              simulate=simulate, propose=propose, **ports)
        response['reply']['lines'] = ['항목과 금액을 다시 확인해 주세요. 나눈 금액의 합계가 줄일 금액과 같아야 해요.']
        return response
    envelope = {'items': [*deepcopy(donors), *deepcopy(receiving)], 'sources': [], 'destinations': [], 'excluded': sorted(excluded)}
    try:
        # 기존 잠금은 여기서 풀지 않는다. 현재 조건으로 전체 배분을 다시 검증한다.
        result = simulate(context, envelope)
        if result['status'] != 'proposed':
            raise ValueError('distribution_unavailable')
        card = propose(context, result, scope=state.get('scope', 'cycle'))
    except ValueError:
        response = distribute(request, state, {**data, 'requests': donors}, store=store, get_context=get_context,
                              simulate=simulate, propose=propose, **ports)
        response['reply']['lines'] = ['이미 쓴 돈과 남겨 둘 돈을 확인해 보니 이 배분은 적용할 수 없어요. 항목이나 금액을 바꿔 주세요.']
        return response
    state.update(requests=envelope['items'], sources=[], destinations=[], excluded=sorted(excluded),
                 proposal=card, proposal_request=envelope, work_ref=card['task_ref'])
    for key in ('pending', 'comparison', 'financial_offer', 'primary_request', 'refinement', 'requested_view'):
        state.pop(key, None)
    _save(store, cid, state)
    return _result('replied', ['직접 나눈 금액으로 만든 변경안이에요. 아직 예산은 바뀌지 않았어요.'], proposal=card)


def _show_comparison(store, cid, state, context, calculation, allocator, *, envelope=None, respect_selected=False):
    from .comparison import compare_adjustments
    envelope = _envelope(state) if envelope is None else envelope
    try:
        comparison = compare_adjustments(calculation, envelope, allocate_group_reduction=allocator, respect_selected=respect_selected)
    except ValueError:
        return _ask(store, cid, state, "request", "이 요청은 아직 방법별 비교를 할 수 없어요. 조정할 항목과 범위를 직접 선택해 주세요.")
    if not isinstance(comparison, dict) or not isinstance(comparison.get("plans"), list):
        return _result("failed", ["비교 결과를 확인하지 못했어요."], error="invalid_comparison_result")
    choices = [("plan_" + plan["id"], plan["label"]) for plan in comparison["plans"]
               if plan.get("id") in {"living", "spare", "extended"} and plan.get("status") in {"ready", "partial"}
               and plan.get("envelope") is not None
               and type(plan.get('amount')) is int and plan['amount'] > 0
               and (plan.get('result') or {}).get('changes')]
    state["comparison"] = {"context": deepcopy(context), "request": envelope, "comparison": deepcopy(comparison)}
    direct = [('distribute', '여러 항목에 직접 나누기')] if len(envelope.get('items', [])) == 1 and envelope['items'][0].get('direction') == 'down' and envelope['items'][0].get('amount') is not None else []
    response = _ask(store, cid, state, "comparison", "조정 방법을 같은 기준으로 비교했어요. 변경안을 볼 방법을 골라 주세요."
                    if choices else comparison.get("message") or "현재 검수된 조건에서 선택할 수 있는 비교안이 없어요. 제한 사유를 확인하고 조건을 바꿔 주세요.",
                    [*choices, *direct, ("manual", "금액 직접 입력"), ("back", "재원 다시 선택")], index=0,
                    explanation=None if choices else {'why': comparison.get('message'), 'effect': '기존 예산은 그대로예요.',
                        'next': '원하는 금액을 알려 주거나 돈을 옮길 곳을 다시 골라 주세요.'})
    response["reply"]["comparison"] = deepcopy(comparison)
    return response


def _envelope(state):
    return {"items": deepcopy(state.get("requests", [])),
            **{key: list(state.get(key, [])) for key in LIST_FIELDS}}


def _remember(state):
    history = state.setdefault("history", [])
    history.append(deepcopy({key: value for key, value in state.items()
                             if key not in {"history", "dialogue_history", "proposal", "work_ref", "pending_edit"}}))
    del history[:-12]


def _edge_choices(names, category, *, categories_only=False):
    codes = [code for code in names if code not in SPECIAL_NAMES] if categories_only else list(SPECIAL_NAMES)
    return [(code, names[code]) for code in codes if code != category]


def _route_question(store, cid, state, names, item, direction):
    code = item["category"]
    field = "sources" if direction == "up" else "destinations"
    text = "늘릴 금액을 어디서 가져올까요?" if direction == "up" else "줄인 금액은 어떻게 할까요?"
    if code not in SPECIAL_NAMES and not state.get("funding_scope"):
        choices = [("living", "생활비 내에서" if direction == "up" else "다른 생활비에 배분"),
                   ("spare", "여윳돈 가져오기" if direction == "up" else "여윳돈으로 남기기"),
                   ("extended", "저축·월 쿠션까지 재배분" if direction == "up" else "저축·월 쿠션에 배분"),
                   ("compare", "잘 모르겠어요")]
        return _ask(store, cid, state, "funding_scope", "어디서 조정할까요?" if direction == "up" else text,
                    choices, field=field)
    if code not in SPECIAL_NAMES and state.get("funding_scope") == "extended" and direction == "down":
        choices = [(candidate, names[candidate]) for candidate in ("SAVINGS", "CUSHION_MONTHLY")]
    else:
        choices = _edge_choices(names, code)
    if code not in {"LIVING", "SPARE"}:
        choices.append(("compare", "잘 모르겠어요"))
    return _ask(store, cid, state, field, text, choices, index=state.get("focus", 0))


def _merge(state, data, pending):
    # Empty structured-output placeholders carry no request; partial requests and zero amounts do.
    fields = ("category", "amount", "amount_mode", "direction")
    state["requests"] = [item for item in state.get("requests", []) if any(item.get(key) is not None for key in fields)]
    data = {**data, "requests": [item for item in data.get("requests", []) if any(item.get(key) is not None for key in fields)]}
    if (data.get("reference") == "previous" and pending.get("index") is None
            and len(data.get("requests", [])) == len(state.get("requests", [])) == 1
            and data["requests"][0].get("category") is None):
        data = deepcopy(data)
        data["requests"][0]["category"] = state["requests"][0].get("category")
    incoming = deepcopy(data.get("requests", []))
    old = state.setdefault("requests", [])
    if data.get("reference") == "new":
        for key in ("requests", "sources", "destinations", "excluded", "funding_scope", "quote",
                    "unlock_categories", "unlock_consents", "clear_targets", "use_available", "scope", "comparison", "partial",
                    "requested_view", "reason"):
            state.pop(key, None)
        old = state.setdefault("requests", [])
    if incoming:
        if len(incoming) == 1 and old and pending.get("index") is not None and data.get("reference") != "new":
            index = pending["index"]
            item = incoming[0]
            if item.get("category") in (None, old[index].get("category")):
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


def handle_expanded(request, *, store, complete, get_context, simulate, propose, task_status, cancel, allocator=None,
                    comparison_handler=None, route_handler=None, discussion_handler=None, initial_data=None):
    """기존 라우터 응답 계약. 버튼 선택과 대기 상태도 서버 메모리에 보관한다."""
    cid = request["conversation_id"]
    state = deepcopy(store.get_agent_state(cid, "budget") or {})
    text, mode = (request.get("text") or "").strip(), request.get("mode", "continue")
    ref = request.get("work_ref") or state.get("work_ref")
    resumed = deepcopy(initial_data)
    try:
        checked = _check(ref, task_status) if ref else None
    except Exception:
        return _result("failed", ["진행 중인 조정 상태를 확인하지 못했어요."], work_ref=ref, error="task_check_failed")
    if checked and checked["state"] in ENDED:
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
        names = {**context.get("category_names", {}), **SPECIAL_NAMES}
        names.update({code: names.get(code, code) for code in context["allocations"]})
    except Exception:
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
        kind = pending["kind"]
        data = {}
        if picked == "compare":
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
            data["funding_scope"] = picked
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
    if data is None and text == "뒤로":
        data = {"action": "back"}
    if (data is None and state.get("requests") and pending.get("kind") in {"funding_scope", "sources", "destinations"}
            and text.translate(str.maketrans("", "", " ?!.,？")) in COMPARE):
        data = {"action": "compare"}
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
            pending_context = {key: pending[key] for key in ('kind', 'choices', 'line') if key in pending}
            recommendation = state.get('comparison', {}).get('comparison', {}).get('recommendation', {}).get('plan_id')
            if recommendation is not None:
                pending_context['recommended_choice'] = 'plan_' + recommendation
            data = read(text, complete, categories=names,
                        pending=pending_context,
                        previous={key: state.get(key) for key in ("requests", "scope", "reason", *LIST_FIELDS)},
                        hints={"from": "spending", "category": hinted} if hinted else None)
        except InterpretationFailed as error:
            return _result("failed", ["요청을 이해하지 못했어요. 다시 알려 주세요."], work_ref=ref, error=str(error))
        if not data["mine"]:
            return not_mine(ref, data.get("handoff"), data.get("handoff_category"), names)
        if data["cancel"]:
            return handle_expanded({**request, "mode": "cancel"}, store=store, complete=complete,
                                   get_context=get_context, simulate=simulate, propose=propose,
                                   task_status=task_status, cancel=cancel, allocator=allocator)
        if data.get('action') == 'distribute':
            return distribute(request, state, data, store=store, get_context=get_context, simulate=simulate, propose=propose)
        if (data["unclear"] or data.get("reference") == "unclear") and not _explanation_only(data):
            if not state.get("requests") and len(data.get("requests", [])) == 1:
                candidate = data["requests"][0]
                if (candidate.get("category") and candidate.get("direction") in {"up", "down"}
                        and candidate.get("amount") is None and data.get("reference") != "unclear"):
                    state["requests"] = deepcopy(data["requests"])
            return _ask(store, cid, state, "clarification", "확인할 내용이 있어요: " +
                        (data["unclear"][0] if data["unclear"] else "앞선 요청을 바꾸는 말씀이신가요, 새 조정인가요?"))
    if data.get('action') == 'distribute':
        return distribute(request, state, data, store=store, get_context=get_context, simulate=simulate, propose=propose)
    if data.get('selected_choice') and not data.get('requests') and not any(data.get(field) for field in LIST_FIELDS):
        selected = data['selected_choice']
        if selected not in dict(pending.get('choices', [])):
            if not pending:
                return _ask(store, cid, state, 'request', '아직 고를 수 있는 조정안이 없어요. 어느 예산을 바꾸고 싶은지 알려 주세요.')
            return _pending_reply(state, ref, context)
        if pending.get('kind') == 'comparison' and selected.startswith('plan_'):
            data['comparison_plan'] = selected.removeprefix('plan_')
    if data.get('action') == 'recommend':
        if _explanation_only({**data, 'action': 'explain'}) and state.get('comparison'):
            return recommendation_reply(state, context, ref)
        data['action'] = 'compare'
    if _explanation_only(data):
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
    if data.get("action") == "inspect" and not data.get("requests") and not data.get("use_available"):
        lines = [f"{names.get(code, code)} 배정액은 {amount:,}원이에요." for code, amount in context.get("buckets", {}).items()]
        return _result("replied", lines or ["조정하고 싶은 항목을 알려 주시면 가능액을 확인할게요."], work_ref=ref)
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
            if len(matching) != 1:
                response = _pending_reply(state, ref, context)
                response["reply"]["lines"] = ["말씀하신 범위만으로 안을 하나로 고를 수 없어요. 사용할 재원·목적지가 표시된 안을 골라 주세요."]
                return response
            data["comparison_plan"] = matching[0]["id"]
        elif data.get("approval") and not data.get("requests") and not any(data.get(field) for field in LIST_FIELDS):
            return _pending_reply(state, ref, context)
        elif data.get("requests") or data.get("excluded"):
            data["action"] = "compare"
    mutating = (data.get("requests") or data.get("clear_targets") or data.get("funding_scope")
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
                                  and plan.get("id") in {"living", "spare", "extended"}
                                  and plan.get("status") in {"ready", "partial", "needs_destination"}), None)
            if not selected_plan or not selected_plan.get("envelope"):
                return _pending_reply(state, ref, context)
            state["requests"] = deepcopy(selected_plan["envelope"]["items"])
            for field in LIST_FIELDS:
                state[field] = list(selected_plan["envelope"].get(field, []))
            state["funding_scope"] = selected_plan.get("scope", selected_plan["id"])
            state.pop("comparison", None)
            state.pop("quote", None)
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
    allowed = set(names)
    if (any(item.get("category") is not None and item["category"] not in allowed for item in requests)
            or any(set(state.get(field, [])) - allowed for field in LIST_FIELDS)):
        return _result("unsupported", ["해당 항목은 현재 지원하는 예산 조정 범위에 없어요."], work_ref=ref)
    if discussion_handler:
        discussion = discussion_handler(store, cid, state, context, data)
        if discussion is not None:
            return discussion
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
    calculation = deepcopy(context)
    calculation["locks"] = {code: amount for code, amount in context.get("locks", {}).items()
                             if code not in state.get("unlock_categories", [])}
    if data.get("action") == "compare" and not (len(requests) > 1 and all(item.get('amount') is not None for item in requests)):
        return (comparison_handler or _show_comparison)(store, cid, state, context, calculation, allocator)
    for index, item in enumerate(requests):
        state["focus"] = index
        code, direction = item.get("category"), item.get("direction")
        if code is None:
            return _ask(store, cid, state, "category", "어떤 항목을 조정할까요?", list(names.items()), index=index)
        if direction is None and item.get("amount_mode") in (None, "by", "percent_by", "available"):
            return _ask(store, cid, state, "direction", "늘릴까요, 줄일까요?", [("up", "늘리기"), ("down", "줄이기")], index=index)
        opposite = "down" if direction == "up" else "up"
        paired = any(other is not item and other.get("direction") == opposite and other.get("amount") is not None for other in requests)
        field = "sources" if direction == "up" else "destinations"
        missing_amount = item.get("amount") is None or item.get("amount_mode") == "available"
        if (direction and not state.get(field) and not paired and (code not in SPECIAL_NAMES or not missing_amount)
                and not (missing_amount and state.get("use_available") is False)):
            return (route_handler or _route_question)(store, cid, state, names, item, direction)
        from .funding import priority_destination
        if state.get("destinations") == ["LIVING"] and priority_destination(calculation,
                excluded=state.get('excluded', []), requested=[r.get('category') for r in requests]) is None:
            return _ask(store, cid, state, "destinations", "어느 생활비 항목에 배분할까요?",
                        _edge_choices(names, code, categories_only=True), index=index)
        if code == "LIVING" and direction == "up" and not state.get("destinations"):
            return _ask(store, cid, state, "destinations", "어느 생활비 항목에 배분할까요?",
                        _edge_choices(names, code, categories_only=True), index=index)
        if code in calculation.get("locks", {}):
            return _ask(store, cid, state, "unlock", f"{names[code]} {context['locks'][code]:,}원이 잠겨 있어요. 승인 시 해제할까요? 다음 주기에도 이어져요.",
                        [("unlock", "승인 시 잠금 해제"), ("keep_lock", "잠금 유지")],
                        categories=[code], lock_snapshot=_lock_snapshot(context, [code]))
        if missing_amount:
            use_available = (state.get("use_available") is True or item.get("amount_mode") == "available"
                             or state.get("use_available") is not False and code not in SPECIAL_NAMES and state.get(field))
            if not use_available:
                amount_question = ("새 목표 금액을 얼마로 할까요?" if item.get("amount_mode") in {"to", "remaining", "percent_to"}
                                   else "얼마나 " + ("늘릴까요?" if direction == "up" else "줄일까요?"))
                return _ask(store, cid, state, "amount", amount_question + " 금액을 몰라도 가능액을 확인할 수 있어요.",
                            [("available", "가능한 금액 확인")], index=index)
            if direction and not state.get(field) and not paired:
                return (route_handler or _route_question)(store, cid, state, names, item, direction)
            if len(requests) != 1:
                return _ask(store, cid, state, "amount", "여러 항목의 가능액은 함께 확정할 수 없어요. 이 항목에 옮길 금액을 지정해 주세요.", index=index)
            from .funding import available_adjustment
            try:
                available = available_adjustment(calculation, code, direction,
                    sources=state.get("sources", []), destinations=state.get("destinations", []),
                    excluded=state.get("excluded", []), allocate_group_reduction=allocator)
            except ValueError:
                return _ask(store, cid, state, "request", "가능한 금액을 확인하지 못했어요. 재원과 요청을 다시 확인해 주세요.")
            if available["status"] != "ready":
                from .explanations import result_explanation
                message = {"needs_policy": "자동 배분 정책이 연결되지 않았어요. 재원 항목과 금액을 직접 선택해 주세요.",
                           "needs_data": "재원 자료 확인이 필요해요. 확인된 다른 재원을 선택해 주세요.",
                           "needs_confirmation": "기존 예산과 합계가 금액 단위에 맞는지 먼저 확인해야 해요. 기존 금액은 바꾸지 않았어요.",
                           "infeasible": "보호 조건 때문에 지금 옮길 수 있는 금액이 없어요. 재원을 다시 선택해 주세요."}
                return _ask(store, cid, state, field, message[available["status"]], _edge_choices(names, code), index=index,
                            explanation=result_explanation(context, {**available, "requests": _envelope(state)}))
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
        return _ask(store, cid, state, "scope", "같은 항목의 목표 해제와 계속 유지가 충돌해요. 이번 주기만 바꿀까요?", [("cycle", "이번 주기만")])
    try:
        result = simulate(calculation, _envelope(state))
    except ValueError:
        return _ask(store, cid, state, "request", "요청 금액이나 보호 조건을 확인해 주세요. 기존 예산은 그대로예요.")
    if result.get("status") in {"needs_source", "needs_destination"}:
        if result["status"] == "needs_destination" and requests[0]["category"] == "LIVING":
            return _ask(store, cid, state, "destinations", "어느 생활비 항목에 배분할까요?",
                        _edge_choices(names, "LIVING", categories_only=True), index=0)
        direction = "up" if result["status"] == "needs_source" else "down"
        return (route_handler or _route_question)(store, cid, state, names, requests[0], direction)
    if result.get("status") not in {"proposed", "partial", "unchanged"}:
        from .explanations import result_explanation
        messages = {"needs_policy": "자동 배분 정책이 연결되지 않았어요. 카테고리와 금액을 직접 지정해 주세요.",
                    "needs_data": "재원 자료 확인이 필요해요. 확인된 다른 재원을 선택해 주세요.",
                    "needs_confirmation": "기존 예산과 합계가 금액 단위에 맞는지 확인해야 해요. 기존 금액은 바꾸지 않았어요.",
                    "infeasible": "보호 조건 때문에 반영할 수 없어요. 금액이나 재원을 바꿔 주세요."}
        return _ask(store, cid, state, "request", messages.get(result.get("status"), "변경안을 검수하지 못했어요. 요청을 확인해 주세요."),
                    explanation=result_explanation(context, result))
    targets = {code: None for code in clear if code in context.get("category_targets", {})}
    if scope == "ongoing":
        targets.update({code: amount for code, amount in result.get("persistence_candidates", {}).items()
                        if code != "SPARE" and code not in clear})
        targets.update({item["category"]: result["allocations"][item["category"]] for item in requests
                        if item["category"] in result["allocations"] and item["category"] not in clear})
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
    state.update(proposal=deepcopy(card), work_ref=card["task_ref"])
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
