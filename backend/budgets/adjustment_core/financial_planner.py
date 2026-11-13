"""30-day financial diagnosis and verified proposals; no ORM or writes."""
from copy import deepcopy
from fractions import Fraction
import json
import math

from budgets.adjustment_core.calculation import proportional_group_reduction
from budgets.adjustment_core.funding import available_adjustment, simulate_funding, validate_funding, _data
from budgets.adjustment_core.explanations import NAMES
from budgets.adjustment_core.diagnostics import resolve_history, POLICY

SOURCES = ("LIVING", "SPARE", "SAVINGS", "CUSHION_MONTHLY")
PROMPT = """머니쿠션 재정 상태 기반 조정 계획기. 제공된 집계·서버 계산만 근거로 JSON을 반환한다.
draft 단계: 1~3개 계획을 만들고 recommendation_index=null, reason_fact_ids=[]로 반환한다.
funding은 허용된 source와 이동 amount(원 정수) 목록이다. 합계는 planning_amount와 같아야 한다.
한 재원을 여러 번 쓰지 않는다. 단일 재원만으로 부족하면 허용 재원을 함께 쓰는 복합안을 반드시 검토한다.
최근 지출·남은 기간·예정 지출·목표·가용액을 보고 불필요한 카테고리 감액과 저축/월 쿠션 변경을 피한다.
여윳돈도 예정 지출과 추정 보유액이 있어 전부 가용하다고 단정하지 않는다.
저축은 금액이나 비율만 보고 과다하다고 판단하지 않는다. 실행·납입 예정·하한이 보호된 이동 가능액만 검토한다.
사용자 제외·잠금·우선순위·하한을 무시하지 않는다. 현재 금액이 확인되지 않은 재원은 쓰지 않는다.
과거 소비 기록이 부족해도 basis=budget이면 확인된 예산과 남길 금액으로 복합 예시를 만든다.
이 경우 소비 패턴이나 필요한 지출을 알아낸 것처럼 설명하지 않는다. 모르는 값은 추측해 가용액에 더하지 않는다.
capacities는 각 재원에 남길 돈까지 보호한 상한이다. balanced_funding을 출발점으로 여러 재원을 조금씩 함께 사용한다.
한 재원만으로 모자라면 다른 허용 재원으로 보충한다. 저축·월 쿠션·여윳돈을 하나씩 소진하는 안은 피한다.
review 단계: 서버가 검수한 ready 계획 중 추천할 번호(0부터)를 recommendation_index로 선택한다.
review에서는 plans=[]이고, 근거는 제공된 fact id만 reason_fact_ids에 넣는다. 해당 계획의 fact를 반드시 포함한다.
추천 순서는 요청 충족, 조정 후 부족 위험, 사용자 우선순위·저축·쿠션 목표 영향, 변경 규모다.
동등한 안이나 판단 근거 부족이면 추천을 보류한다. 안은 의미 있게 다른 것만 최대 세 개다.
여윳돈을 충분히 남기면서 저축·순위 항목을 유지하는 안을 우선 검토하되 자료에 근거해 판단한다.
검수된 전액 반영안이 없으면 recommendation_index=null로 추천을 보류한다. 모자란 돈을 만들어내지 않는다.
수정 요청을 받으면 오류를 반영해 draft를 다시 반환한다. 예산 확정·저장 권한은 없다.
출력은 plans, recommendation_index, reason_fact_ids만 포함한다. 자유 문장으로 금융 판단을 덧붙이지 않는다."""

SCHEMA = {"type": "object", "properties": {
    "plans": {"type": "array", "items": {"type": "object", "properties": {
        "funding": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string", "enum": list(SOURCES)}, "amount": {"type": "integer"}},
            "required": ["source", "amount"], "additionalProperties": False}}},
        "required": ["funding"], "additionalProperties": False}},
    "recommendation_index": {"type": ["integer", "null"]},
    "reason_fact_ids": {"type": "array", "items": {"type": "string"}},
}, "required": ["plans", "recommendation_index", "reason_fact_ids"], "additionalProperties": False}

QUESTIONS = {
    "period": "다음 급여일까지 며칠 남았나요? 기간을 확인해야 남은 지출을 추정할 수 있어요.",
    "planned": "다음 급여일까지 여윳돈으로 추가 지출할 예정이 있나요? 이미 예약했거나 고정비·카테고리에 잡힌 금액은 빼고 알려 주세요. 없으면 ‘없어요’라고 해 주세요.",
    "history": "지금까지 쓴 돈을 모두 확인하지 못해 옮길 수 있는 돈을 계산하지 못했어요. 계좌·카드 내역을 다시 불러온 뒤 확인해 주세요.",
    "amount": "최근 지출 추세만으로 추가 필요액을 정하기 어려워요. 이번에 더 필요한 금액을 알려 주세요.",
    "stale": "오늘 기준의 지출 정보를 불러오지 못했어요. 계좌·카드 내역을 다시 불러온 뒤 확인해 주세요.",
    "spare": "여윳돈의 실제 가용액을 확인하지 못했어요. 확인된 재원을 직접 고르거나 자료 확인 후 다시 추천받아 주세요.",
    "one_off": "이번 추정에 영향을 주는 큰 지출이 있어요. 일회성인지 확인해 주세요. 모르시면 포함할게요.",
}


def money(value):
    return type(value) is int and value >= 0


def ceil_unit(value, unit=5000):
    return math.ceil(Fraction(value) / unit) * unit


def recommendation_context(context, request):
    """Retain 50% of categories and 30% of upper budgets, plus existing protections."""
    result = deepcopy(context)
    for field, amounts in (("minimums", context["allocations"]), ("bucket_floors", context["buckets"])):
        for code, amount in amounts.items():
            retained = (amount * 3 + 9) // 10 if field == 'bucket_floors' else (amount + 1) // 2
            result.setdefault(field, {})[code] = max(result.get(field, {}).get(code, 0), retained)
    # An initial recommendation is not a user minimum; protected floors and ranks still apply.
    result['ml_not_applicable'] = sorted(set(context.get('ml_not_applicable', ())) | set(context['allocations']))
    if request.get('spare_minimum') is not None:
        result['bucket_floors']['SPARE'] = max(context.get('bucket_floors', {}).get('SPARE', 0), request['spare_minimum'])
    return result


def diagnose(context, request):
    protected = recommendation_context(context, request)
    diagnosis = _forecast_diagnosis(protected, request)
    items = request.get('items', [])
    if (diagnosis['status'] != 'ready' and len(items) == 1
            and items[0].get('amount') is not None and items[0].get('amount_mode') != 'available'
            and context.get('spending_status') == 'complete'
            and (diagnosis.get('field') in {'history', 'period', 'stale', 'planned', 'spare'}
                 or items[0].get('category') in context['buckets'])):
        target = items[0]['category']
        data = _data(protected, request)
        requested = data['targets'][target] - data['before'][target]
        if requested > 0 and requested % 5000 == 0 and target not in context.get('locks', {}):
            planned = context.get('diagnostic_inputs', {}).get('planned_spare', {})
            known_planned = (planned.get('status') in {'confirmed', 'user_confirmed'}
                            and planned.get('scope') == 'unbudgeted_only' and money(planned.get('amount')))
            diagnosis = {'status': 'ready', 'basis': 'budget', 'target': target,
                'requested_amount': requested, 'planning_amount': requested,
                'allowed': [s for s in (request.get('sources') or SOURCES) if s in SOURCES
                            and s != target and s not in request.get('excluded', [])],
                'excluded': list(request.get('excluded', [])), 'reserve_forecast': {},
                'planned_spare': planned['amount'] if known_planned else 0,
                'reserve_is_estimate': True, 'allow_reserve_risk': False,
                'facts': {'need': f"{NAMES.get(target, target)}에 {requested:,}원을 더하는 예시예요.",
                    'basis': '현재 예산으로 계산한 예시예요. 앞으로 쓸 돈을 예측한 결과는 아니에요.'}}
            if request.get('spare_minimum') is not None:
                diagnosis['spare_minimum'] = request['spare_minimum']
            diagnosis['reserve_before'] = reserve_after(protected, diagnosis, context['allocations'])
            diagnosis['capacities'] = {}
            for source in diagnosis['allowed']:
                quote = available_adjustment(protected, target, 'up', sources=[source], excluded=diagnosis['excluded'],
                    allocate_group_reduction=proportional_group_reduction, spare_minimum=request.get('spare_minimum'))
                maximum = quote['maximum'] if quote['status'] == 'ready' else 0
                if source == 'SPARE':
                    maximum = min(maximum, max(context.get('bucket_available', {}).get('SPARE', 0) - diagnosis['reserve_before'], 0))
                diagnosis['capacities'][source] = maximum
            diagnosis['facts']['reserve'] = f"여윳돈은 최소 {diagnosis['reserve_before']:,}원을 남기는 예시예요. 예정된 지출이 있다면 더 남겨야 해요."
    if diagnosis['status'] == 'ready':
        diagnosis.setdefault('basis', 'forecast')
        diagnosis['retained_amounts'] = {
            **{code: max(protected['minimums'][code], context.get('spent', {}).get(code, 0),
                         context.get('system_floors', {}).get(code, 0),
                         amount if code in context.get('priorities', {}) or code in context.get('locks', {}) else 0)
               for code, amount in context['allocations'].items()}, **protected['bucket_floors']}
        diagnosis['facts']['protection'] = '저축·월 머니쿠션·여윳돈은 기본 30% 이상을 남겨요. 직접 정한 최소금액과 중요한 생활비도 지켜요.'
        diagnosis['planning_amount'] = min(diagnosis['requested_amount'], sum(n // 5000 * 5000 for n in diagnosis['capacities'].values()))
    return diagnosis


def _forecast_diagnosis(context, request):
    items = request.get("items", [])
    if len(items) != 1 or items[0].get("category") not in context["allocations"]:
        return {"status": "unsupported", "message": "식비나 교통비처럼, 더 필요한 항목을 먼저 정하면 얼마를 늘릴지 추천할 수 있어요."}
    item, info = items[0], context.get("diagnostic_inputs", {})
    target = item["category"]
    if target in context.get("locks", {}):
        return {"status": "unsupported", "message": "잠긴 항목은 먼저 잠금 해제 여부를 확인해 주세요. 임의로 풀지 않아요."}
    if info.get("as_of") != context.get("today") or not info.get("as_of"):
        return {"status": "needs_data", "field": "stale"}
    days = info.get("remaining_days")
    if type(days) is not int or not 0 <= days <= 365:
        return {"status": "needs_data", "field": "period"}
    planned = info.get("planned_spare", {})
    history = info.get("history", {})
    try:
        samples, pending = resolve_history(history, info["as_of"], context["allocations"])
    except (ValueError, TypeError, KeyError):
        return {"status": "needs_data", "field": "history", "reason": history.get("reason", "history_unverified")}
    if context.get("spending_status") != "complete":
        return {"status": "needs_data", "field": "history"}
    if planned.get("status") not in {"confirmed", "user_confirmed"} or planned.get("scope") != "unbudgeted_only" or not money(planned.get("amount")):
        return {"status": "needs_data", "field": "planned"}
    averages = {c: Fraction(sum(s[c] for s in samples), len(samples)) for c in context["allocations"]}
    cycle_days = info.get("cycle_remaining_days", days)
    if type(cycle_days) is not int or not 0 <= cycle_days <= 365:
        return {"status": "needs_data", "field": "period"}
    forecast = {c: math.ceil(n * cycle_days) for c, n in averages.items()}
    reserve_forecast = {c: math.ceil(n * days) for c, n in averages.items()}
    expected_other = sum(v for c, v in reserve_forecast.items() if c != target)
    relevant = [p for p in pending if p["category"] == target or
                reserve_forecast[p["category"]] > max(context["allocations"][p["category"]] - context["spent"].get(p["category"], 0), 0)
                or "LIVING" in (request.get("sources") or SOURCES)]
    if relevant:
        return {"status": "needs_data", "field": "one_off", "candidates": relevant}
    remaining = max(context["allocations"][target] - context["spent"].get(target, 0), 0)
    if item.get("amount") is None or item.get("amount_mode") == "available":
        if item.get("direction") != "up":
            return {"status": "unsupported", "message": "늘릴 항목과 방향을 먼저 확인해 주세요."}
        requested = ceil_unit(max(forecast[target] - remaining, 0), 10000)
    else:
        data = _data(context, request)
        requested = data["targets"][target] - context["allocations"][target]
    if requested <= 0:
        return {"status": "needs_data", "field": "amount"}
    if requested % 5000:
        return {"status": "needs_data", "field": "amount", "reason": "budget_unit_confirmation"}
    if context.get("bucket_status", {}).get("SPARE") != "ready" or not money(context.get("bucket_available", {}).get("SPARE")):
        return {"status": "needs_data", "field": "spare"}
    allowed = [s for s in (request.get("sources") or SOURCES) if s in SOURCES and s not in request.get("excluded", [])]
    if "LIVING" in request.get("excluded", []):
        allowed = [s for s in allowed if s != "LIVING"]
    base = {"status": "ready", "target": target, "requested_amount": requested,
            "planning_amount": requested, "remaining_days": days, "cycle_remaining_days": cycle_days,
            "history_days": len(samples), "policy_version": POLICY,
            "forecast": forecast, "reserve_forecast": reserve_forecast, "expected_other": expected_other,
            "planned_spare": planned["amount"], "allowed": allowed, "excluded": list(request.get("excluded", [])),
            "reserve_is_estimate": True, "allow_reserve_risk": request.get("allow_reserve_risk") is True}
    if request.get('spare_minimum') is not None:
        base['spare_minimum'] = request['spare_minimum']
    reserve = reserve_after(context, base, context["allocations"])
    capacities = {}
    for source in allowed:
        quote = available_adjustment(context, target, "up", sources=[source], excluded=base["excluded"],
                                     allocate_group_reduction=proportional_group_reduction, spare_minimum=base.get('spare_minimum'))
        capacities[source] = quote["maximum"] if quote["status"] == "ready" else 0
        if source == "SPARE":
            if not base["allow_reserve_risk"]:
                capacities[source] = min(capacities[source], max(context["bucket_available"]["SPARE"] - reserve, 0))
    base.update(reserve_before=reserve, capacities=capacities)
    base["facts"] = {
        "period": f"다음 급여일까지 {days}일, 확인된 최근 관측일은 {len(samples)}일이에요.",
        "need": f"{NAMES.get(target, target)}의 남은 예산은 {remaining:,}원, 최근 추세로 추정한 남은 기간 지출은 {forecast[target]:,}원이에요.",
        "reserve": f"예정된 지출과 다른 항목에서 부족해질 돈을 대비해 여윳돈 {reserve:,}원을 남기길 권해요. 예상 금액이라 직접 바꿀 수 있어요.",
        "savings": f"저축 배정액 {context['buckets']['SAVINGS']:,}원 중 확인된 이동 가능액은 {context.get('bucket_available', {}).get('SAVINGS', 0):,}원이에요. 금액만으로 저축 과다를 판단하지 않아요.",
    }
    return base


def reserve_after(context, diagnosis, allocations):
    target = diagnosis["target"]
    # A surplus in one category cannot silently fund a deficit in another.
    gaps = sum(max(predicted - max(allocations[c] - context["spent"].get(c, 0), 0), 0)
               for c, predicted in diagnosis["reserve_forecast"].items() if c != target)
    return max(diagnosis["planned_spare"] + gaps,
               diagnosis.get('spare_minimum', (context['buckets']['SPARE'] * 3 + 9) // 10),
               context.get('bucket_floors', {}).get('SPARE', 0))


def simulate_plan(context, diagnosis, funding):
    context = recommendation_context(context, diagnosis)
    if not isinstance(funding, list) or not 1 <= len(funding) <= 4:
        raise ValueError("invalid_funding")
    limits = {}
    for row in funding:
        if not isinstance(row, dict) or set(row) != {"source", "amount"}:
            raise ValueError("invalid_funding")
        source, amount = row["source"], row["amount"]
        if (source not in diagnosis["allowed"] or source in limits or not money(amount) or amount <= 0
                or amount % 5000 or amount > diagnosis["capacities"].get(source, 0)):
            raise ValueError("funding_limit")
        limits[source] = amount
    if sum(limits.values()) != diagnosis["planning_amount"]:
        raise ValueError("funding_total")
    items = [{"category": diagnosis["target"], "amount_mode": "by", "direction": "up", "amount": diagnosis["requested_amount"]}]
    items.extend({"category": s, "amount_mode": "by", "direction": "down", "amount": n, "automatic": True}
                 for s, n in limits.items() if s != "LIVING")
    request = {"items": items, "sources": ["LIVING"] if limits.get("LIVING") else [],
               "destinations": [], "excluded": list(diagnosis["excluded"])}
    if diagnosis.get('spare_minimum') is not None:
        request['spare_minimum'] = diagnosis['spare_minimum']
    result = simulate_funding(context, request, allocate_group_reduction=proportional_group_reduction)
    validate_funding(context, request, result)
    if result["status"] not in {"proposed", "partial"}:
        raise ValueError("not_validated")
    actual = {s: 0 for s in SOURCES}
    for transfer in result["transfers"]:
        source = "LIVING" if transfer["from"] in context["allocations"] else transfer["from"]
        actual[source] += transfer["amount"]
    if any(n > limits.get(s, 0) for s, n in actual.items()):
        raise ValueError("split_exceeded")
    spare_used = context["buckets"]["SPARE"] - result["buckets"]["SPARE"]
    available = context.get("bucket_available", {}).get("SPARE")
    reserve = reserve_after(context, diagnosis, result["allocations"])
    if spare_used and (context.get("bucket_status", {}).get("SPARE") != "ready" or not money(available)):
        raise ValueError("spare_unverified")
    # Even savings-only funding must not claim an already insufficient reserve was solved.
    spare_after = max((available or 0) - spare_used, 0)
    if (max(reserve - spare_after, 0) > max(diagnosis["reserve_before"] - (available or 0), 0)
            and not diagnosis.get("allow_reserve_risk")):
        raise ValueError("spare_reserve_shortfall")
    facts = {"reserve_after": (f"조정 후 여윳돈 {spare_after:,}원을 남겨요. 이 예시에서 남길 기준은 {reserve:,}원이에요."
                              if available is not None else '여윳돈의 사용 가능 금액이 확인되지 않아 이번 계산에서 제외했어요.')}
    structured = []
    for change in result["changes"]:
        code = change["category"]
        rank = context.get("priorities", {}).get(code)
        prefix = f"{rank}순위 " if rank is not None else ""
        facts["change_" + code] = f"{prefix}{NAMES.get(code, code)} {change['before']:,}원 → {change['after']:,}원이에요."
        if change["diff"] < 0:
            kind = "category_reduction" if code in context["allocations"] else "bucket_reduction"
            detail = {"kind": kind, "category": code, "rank": rank, "before": change["before"],
                      "after": change["after"], "decrease": -change["diff"]}
            if kind == "category_reduction": detail["remaining"] = change["after"] - context["spent"].get(code, 0)
            else: detail["available_after"] = max(context.get("bucket_available", {}).get(code, 0) + change["diff"], 0)
            structured.append(detail)
        goal = context.get("bucket_targets", {}).get(code, context.get("category_targets", {}).get(code))
        if money(goal) and change["after"] < min(change["before"], goal):
            increase = max(goal - change["after"], 0) - max(goal - change["before"], 0)
            facts["goal_" + code] = f"{NAMES.get(code, code)}에 남겨 두기로 한 돈보다 부족한 금액이 {increase:,}원 더 커져요."
            structured.append({"kind": "goal_shortfall_increase", "category": code, "increase": increase})
    if spare_after < reserve:
        facts["reserve_gap"] = f"여윳돈 보유 추천액보다 {reserve - spare_after:,}원 부족한 상태는 남아요."
    preserved = [NAMES.get(c, c) for c in [*context.get("priorities", {}), "SAVINGS", "CUSHION_MONTHLY"]
                 if (result["allocations"].get(c, result["buckets"].get(c)) == context["allocations"].get(c, context["buckets"].get(c)))
                 and c != diagnosis["target"]]
    facts["preserved"] = (", ".join(preserved) + "의 배정을 유지해요.") if preserved else "다른 우선순위·저축 계획도 변경돼요."
    return {"result": result, "funding": [{"source": s, "amount": n} for s, n in actual.items() if n],
            "reserve_after": reserve, "spare_available_after": spare_after, "facts": facts, "structured_facts": structured}


def parse(value):
    data = json.loads(value) if isinstance(value, str) else deepcopy(value)
    if not isinstance(data, dict) or set(data) != {"plans", "recommendation_index", "reason_fact_ids"}:
        raise ValueError("planner_fields")
    if not isinstance(data["plans"], list) or len(data["plans"]) > 3:
        raise ValueError("planner_plans")
    index = data["recommendation_index"]
    if index is not None and (type(index) is not int or index < 0):
        raise ValueError("planner_index")
    if not isinstance(data["reason_fact_ids"], list) or len(data["reason_fact_ids"]) > 10 or any(not isinstance(s, str) for s in data["reason_fact_ids"]):
        raise ValueError("planner_evidence")
    return data


def recommend(context, request, complete):
    diagnosis = diagnose(context, request)
    if diagnosis["status"] != "ready":
        return {"diagnosis": diagnosis, "plans": [], "recommended_index": None}
    if request.get('sources') and diagnosis['planning_amount'] < diagnosis['requested_amount']:
        expanded = diagnose(context, {**request, 'sources': []})
        if expanded['status'] == 'ready' and expanded['planning_amount'] > diagnosis['planning_amount']:
            expanded['expanded_sources'] = [s for s in expanded['allowed'] if s not in request['sources']]
            expanded['facts']['additional_sources'] = '고른 곳만으로는 부족해 다른 예산도 조금씩 쓰는 예시를 함께 계산했어요. 선택하기 전에는 바뀌지 않아요.'
            diagnosis = expanded
    if diagnosis["planning_amount"] <= 0:
        return {"diagnosis": diagnosis, "plans": [], "recommended_index": None}
    split = proportional_group_reduction(list(diagnosis['capacities']), diagnosis['planning_amount'], diagnosis['capacities'])
    combined = [{'source': source, 'amount': amount} for source, amount in split['reductions'].items() if amount]
    payload = {"phase": "draft", "diagnosis": diagnosis, "budget": {
        key: context.get(key, {}) for key in ("allocations", "spent", "priorities", "category_targets", "bucket_targets", "buckets", "bucket_available", "bucket_status")}}
    payload['balanced_funding'] = combined
    payload['user_preferences'] = {key: request[key] for key in ('reason', 'sources', 'excluded', 'spare_minimum') if key in request}
    plans, evidence = [], dict(diagnosis["facts"])
    for _ in range(3):
        try:
            answer = parse(complete(PROMPT, json.dumps(payload, ensure_ascii=False)))
            if payload["phase"] == "draft":
                if not answer["plans"] or answer["recommendation_index"] is not None:
                    raise ValueError("draft_required")
                proposed = []
                for draft in answer["plans"]:
                    if not isinstance(draft, dict) or set(draft) != {"funding"}:
                        raise ValueError("planner_draft")
                    plan = simulate_plan(context, diagnosis, draft["funding"])
                    if plan["funding"] not in [p["funding"] for p in proposed]:
                        proposed.append(plan)
                if not split['remaining'] and len(combined) > 1:
                    try:
                        verified = simulate_plan(context, diagnosis, combined)
                    except ValueError:
                        # Living cuts can require a larger reserve than the individual source quotes.
                        verified = None
                    if verified is not None and verified['funding'] not in [p['funding'] for p in proposed]:
                        proposed = proposed[:2] + [verified]
                plans = proposed
                evidence = dict(diagnosis["facts"])
                review = []
                for i, plan in enumerate(plans):
                    keys = {f"plan_{i}.{key}": text for key, text in plan["facts"].items()}
                    evidence.update(keys)
                    review.append({"index": i, "status": plan["result"]["status"], "funding": plan["funding"],
                                   "shortfalls": plan["result"]["shortfalls"], "facts": keys})
                payload = {"phase": "review", "diagnosis": diagnosis, "verified_plans": review, "facts": evidence}
                continue
            index, ids = answer["recommendation_index"], answer["reason_fact_ids"]
            if answer["plans"] or any(key not in evidence for key in ids):
                raise ValueError("unsupported_evidence")
            if index is not None and (index >= len(plans) or plans[index]["result"]["status"] != "proposed"
                                      or not any(key.startswith(f"plan_{index}.") for key in ids)):
                raise ValueError("invalid_recommendation")
            return {"diagnosis": diagnosis, "plans": plans, "recommended_index": index,
                    "reasons": [evidence[key] for key in dict.fromkeys(ids)], "evidence_ids": ids}
        except (ValueError, TypeError, KeyError) as error:
            known = {"funding_limit", "funding_total", "spare_reserve_shortfall", "split_exceeded", "not_validated",
                     "unsupported_evidence", "invalid_recommendation"}
            code = str(error) if str(error) in known else "invalid_plan_or_evidence"
            payload["error"] = {"code": code, "hint": "허용 재원·가용액·합계·보유 추천액·사실 ID를 확인해 수정해 주세요."}
    return {"diagnosis": diagnosis, "plans": [], "recommended_index": None, "error": "planner_not_validated"}


def synthetic_diagnostics(allocations):
    from datetime import date, timedelta
    return {"as_of": "2026-11-10", "remaining_days": 10,
            "planned_spare": {"status": "confirmed", "scope": "unbudgeted_only", "amount": 330000},
            "history": {"status": "ready", "scope": "variable_living", "policy_version": POLICY,
                        "dates": [(date(2026, 11, 10) - timedelta(days=n)).isoformat() for n in range(30, 0, -1)],
                        "normalization_checks": {k: "verified" for k in ("duplicates", "cancellations", "refunds")},
                        "samples": [{c: 40000 if c == "FOOD" else 0 if c == "ETC" else 1000 for c in allocations} for _ in range(30)],
                        "candidates": [], "review_decisions": {}}}
