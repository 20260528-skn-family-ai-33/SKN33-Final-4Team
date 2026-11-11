"""Compare independent funding alternatives; none is approved or written here."""
from copy import deepcopy
from fractions import Fraction

from .funding import BUCKETS, LIVING, _data, _value, available_adjustment, simulate_funding


NAMES = {"LIVING": "생활비", "SAVINGS": "저축", "CUSHION_MONTHLY": "월 쿠션", "SPARE": "여윳돈"}
REASONS = {
    "needs_policy": "자동 배분 정책 연결이 필요해요.",
    "needs_data": "재원의 가용 금액을 확인해야 해요.",
    "needs_confirmation": "기존 예산과 합계가 금액 단위에 맞는지 먼저 확인해야 해요.",
    "infeasible": "선택한 범위에서 보호 조건을 지키며 옮길 수 있는 금액이 없어요.",
    "needs_destination": "받을 항목을 먼저 선택해야 정확한 변경안을 만들 수 있어요.",
    "needs_source": "사용할 재원을 먼저 선택해야 해요.",
    "unchanged": "현재 예산과 같아서 바뀌는 금액이 없어요.",
}

RECOMMENDATION_POLICY = [
    "요청 금액을 전액 반영한 검수 완료안끼리 비교",
    "사용자가 정한 높은 순위부터 카테고리 감액을 적게 하는 안",
    "기존 유지 목표와 기본 배분 목표의 미달을 덜 늘리는 안",
    "가용 재원이나 다른 카테고리의 남은 예산을 새로 0원으로 만들지 않는 안",
    "저축·월 쿠션 계획과 다른 생활비 항목의 변경 비율이 작은 안",
]


def _assess_and_recommend(context, comparison, primary):
    """Explain verified changes and compare declared priorities in a public order."""
    names = {**context.get("category_names", {}), **NAMES}
    before = {**context["allocations"], **context["buckets"], LIVING: sum(context["allocations"].values())}
    ranks = context.get("priorities", {})
    rank_order = sorted(set(ranks.values()))
    goals, invalid_goals = {}, False
    for field in ("category_targets", "bucket_targets"):
        saved = context.get(field, {})
        if not isinstance(saved, dict):
            invalid_goals = True
            continue
        for code, amount in saved.items():
            if code in before and code != "SPARE":
                if type(amount) is int and amount >= 0:
                    goals[code] = amount
                else:
                    invalid_goals = True
    scores = {}
    for plan in comparison["plans"]:
        plan["recommended"] = False
        assessment = {"label": "확인 필요", "reasons": [], "cautions": [], "facts": []}
        plan["assessment"] = assessment
        result = plan.get("result")
        if not result or plan["status"] not in ("ready", "partial"):
            assessment["reasons"].append(REASONS.get(plan["status"], "추가 정보를 확인해야 이 안을 평가할 수 있어요."))
            continue
        after = {**result["allocations"], **result["buckets"], LIVING: sum(result["allocations"].values())}
        collateral = sorted((change for change in result["changes"] if change["diff"] < 0 and change["category"] != primary),
                            key=lambda change: ranks.get(change["category"], float("inf")))
        rank_damage = tuple(sum(-change["diff"] for change in collateral if ranks.get(change["category"]) == rank)
                            for rank in rank_order)
        exhausted = 0
        for change in collateral:
            code, amount = change["category"], -change["diff"]
            if code in context["allocations"]:
                rank = ranks.get(code)
                rank_text = f"{rank}순위로 지정한 " if rank is not None else ""
                remaining = change["after"] - context.get("spent", {}).get(code, 0)
                fact = {"kind": "category_reduction", "category": code, "rank": rank,
                        "before": change["before"], "after": change["after"], "decrease": amount,
                        "spent": context.get("spent", {}).get(code, 0), "remaining": remaining}
                assessment["facts"].append(fact)
                assessment["cautions"].append(f"{rank_text}{names.get(code, code)} 예산이 "
                    f"{change['before']:,}원에서 {change['after']:,}원으로 {amount:,}원 줄어요. "
                    f"기지출을 제외한 남은 예산은 {remaining:,}원이에요.")
                if remaining == 0:
                    exhausted += 1
                floor = max(context.get("minimums", {}).get(code, 0),
                            context.get("system_floors", {}).get(code, 0), fact["spent"])
                if code not in context.get("ml_not_applicable", ("ETC",)) and code in context.get("ml_reference", {}):
                    floor = max(floor, min(change["before"], context["ml_reference"][code]))
                if change["after"] == floor:
                    assessment["cautions"].append(f"{names.get(code, code)} 항목은 자동 감액 하한 {floor:,}원까지 내려가 추가 자동 감액 여지가 없어요.")
            else:
                available_before = context.get("bucket_available", {}).get(code, 0)
                available_after = max(available_before - amount, 0)
                percentage = float(Fraction(amount * 100, change["before"]))
                assessment["facts"].append({"kind": "bucket_reduction", "category": code,
                    "before": change["before"], "after": change["after"], "decrease": amount,
                    "available_before": available_before, "available_after": available_after})
                assessment["cautions"].append(f"{names[code]} 배정액이 {change['before']:,}원에서 {change['after']:,}원으로 "
                    f"{amount:,}원(기존 금액의 약 {percentage:.1f}%) 줄고, "
                    f"이번 주기에 다시 옮길 수 있는 가용액은 {available_after:,}원 남아요.")
                if available_before > 0 and available_after == 0:
                    exhausted += 1
        gaps = {}
        for code, goal in goals.items():
            if code == primary or code not in after or code == "SPARE":
                continue
            old_gap, new_gap = max(goal - before[code], 0), max(goal - after[code], 0)
            increase = max(new_gap - old_gap, 0)
            if increase:
                gaps[code] = increase
                assessment["facts"].append({"kind": "goal_shortfall_increase", "category": code,
                    "target": goal, "before_shortfall": old_gap, "after_shortfall": new_gap, "increase": increase})
                assessment["cautions"].append(f"{names.get(code, code)}의 저장된 목표 {goal:,}원과 비교하면 "
                    f"미달 배정액이 {old_gap:,}원에서 {new_gap:,}원으로 {increase:,}원 늘어요. 목표는 잠금이나 필수 하한과 달라요.")
        category_gaps = sum(amount for code, amount in gaps.items() if code in context["allocations"])
        goal_damage = max(category_gaps, gaps.get(LIVING, 0)) + sum(gaps.get(code, 0) for code in ("SAVINGS", "CUSHION_MONTHLY"))
        upper_change = sum((Fraction(-change["diff"], change["before"]) for change in collateral
                            if change["category"] in ("SAVINGS", "CUSHION_MONTHLY")), Fraction())
        category_change = sum((Fraction(-change["diff"], change["before"]) for change in collateral
                               if change["category"] in context["allocations"]), Fraction())
        if plan["status"] == "ready" and plan["amount"] == comparison["basis_amount"] and plan["amount"] > 0:
            scores[plan["id"]] = (rank_damage, goal_damage, exhausted, upper_change, category_change)
            assessment["label"] = "대안"
            assessment["reasons"].append(f"이 방법으로 {plan['amount']:,}원을 옮길 수 있어요.")
        else:
            assessment["reasons"].append(f"요청 {comparison['basis_amount']:,}원 중 {plan['amount']:,}원만 반영 가능해 "
                                          f"{comparison['basis_amount'] - plan['amount']:,}원이 부족해요.")
    recommendation = {"plan_id": None, "label": None, "reasons": [], "cautions": [],
                      "policy": list(RECOMMENDATION_POLICY)}
    comparison["recommendation"] = recommendation
    if invalid_goals:
        recommendation["reasons"].append("저장된 목표 자료를 확인해야 사용자 목표에 맞는 추천을 정할 수 있어요. 변경안의 계산 결과와 구분해 확인해 주세요.")
        for plan in comparison["plans"]:
            plan["assessment"]["label"] = "확인 필요"
        return
    if not scores:
        recommendation["reasons"].append("현재 금액을 전액 반영한 검수 완료안이 없어 한 안을 추천하지 않았어요. 금액이나 재원을 먼저 조정해 주세요.")
        return
    best = min(scores.values())
    winners = [plan for plan in comparison["plans"] if scores.get(plan["id"]) == best]
    if len(winners) != 1:
        recommendation["reasons"].append("확인된 우선순위·목표·남은 가용액 기준으로 우열이 같은 안들이 있어요. 어떤 계획을 더 유지하고 싶은지 선택해 주세요.")
        return
    winner = winners[0]
    reasons = list(winner["assessment"]["reasons"])
    if len(scores) == 1:
        reasons.append("보여 드린 방법 중 원하는 금액을 모두 옮길 수 있는 방법은 이 안뿐이에요.")
    if rank_order and not any(best[0]):
        reasons.append("중요하게 고른 다른 항목의 예산은 줄이지 않아도 돼요.")
    if goals and not best[1]:
        reasons.append("다른 항목에 남겨 두기로 한 돈도 더 부족해지지 않아요.")
    unchanged = [f"{names[code]} {before[code]:,}원" for code in ("SAVINGS", "CUSHION_MONTHLY")
                 if code != primary and winner["result"]["buckets"][code] == before[code]]
    if unchanged:
        reasons.append(" / ".join(unchanged) + " 배정을 유지해요.")
    if context.get("bucket_status", {}).get("SPARE") == "ready":
        remaining = context.get("bucket_available", {}).get("SPARE", 0) + winner["result"]["buckets"]["SPARE"] - before["SPARE"]
        reasons.append(f"조정 후 이번 주기에 사용할 수 있는 여윳돈은 {remaining:,}원이에요.")
    winner["recommended"] = True
    winner["assessment"].update(label="추천", reasons=reasons)
    recommendation.update(plan_id=winner["id"], label=winner["label"], reasons=list(reasons),
                          cautions=list(winner["assessment"]["cautions"]))
    for plan in comparison["plans"]:
        if plan is winner:
            continue
        score = scores.get(plan["id"])
        if score is None:
            if plan["status"] == "partial":
                plan["assessment"]["label"] = "비추천"
            continue
        if score[:3] > best[:3]:
            plan["assessment"]["label"] = "비추천"
        if score[0] > best[0]:
            plan["assessment"]["reasons"].append("추천안보다 사용자가 높게 정한 순위의 카테고리를 더 줄여 우선순위 보존 기준에서 추천 순서가 낮아요.")
        elif score[1] > best[1]:
            plan["assessment"]["reasons"].append("추천안보다 기존 목표의 미달 배정액을 더 늘려요.")
        elif score[2] > best[2]:
            plan["assessment"]["reasons"].append("추천안과 달리 일부 재원의 가용액이나 다른 항목의 남은 예산이 0원이 돼요.")
        elif score[3] > best[3]:
            plan["assessment"]["reasons"].append("각 재원의 기존 배정액 대비 감액 비율을 비교하면 추천안보다 저축·월 쿠션 계획의 변화가 커요.")
        else:
            plan["assessment"]["reasons"].append("각 항목의 기존 배정액 대비 감액 비율을 비교하면 추천안보다 다른 생활비 계획의 변화가 커요.")


def _profiles(category, direction):
    """Three ways of funding the same request, or three destinations for its release."""
    if category == LIVING:
        edges = [("living", "저축 배분 조정", ["SAVINGS"]),
                 ("spare", "여윳돈 활용" if direction == "up" else "여윳돈으로 남기기", ["SPARE"]),
                 ("extended", "월 쿠션 배분 조정", ["CUSHION_MONTHLY"])]
    elif category in ("SAVINGS", "CUSHION_MONTHLY"):
        other = "CUSHION_MONTHLY" if category == "SAVINGS" else "SAVINGS"
        edges = [("living", "생활비 배분 조정", [LIVING]),
                 ("spare", "여윳돈 활용" if direction == "up" else "여윳돈으로 남기기", ["SPARE"]),
                 ("extended", NAMES[other] + " 배분 조정", [other])]
    elif category == "SPARE":
        edges = [("living", "생활비 배분 조정", [LIVING]),
                 ("spare", "저축 배분 조정", ["SAVINGS"]),
                 ("extended", "월 쿠션 배분 조정", ["CUSHION_MONTHLY"])]
    else:
        edges = [("living", "생활비 내 재배분" if direction == "up" else "다른 생활비에 배분", [LIVING]),
                 ("spare", "여윳돈 활용" if direction == "up" else "여윳돈으로 남기기", ["SPARE"]),
                 ("extended", "저축·월 쿠션 배분 조정", ["SAVINGS", "CUSHION_MONTHLY"])]
    return edges


def compare_adjustments(context, envelope, *, allocate_group_reduction=None, respect_selected=False):
    """Compare the same amount in three separate copies of one budget context.

    Without a requested amount, the common basis is the smallest positive verified
    maximum. It is a comparison amount, never a forecast of the user's needs.
    """
    response = {"status": "unsupported", "basis_amount": 0, "amount_was_given": False,
                "plans": [], "message": "먼저 비교할 항목 하나와 늘릴지 줄일지를 알려 주세요."}
    if not isinstance(envelope, dict) or len(envelope.get("items", [])) != 1:
        return response
    item = deepcopy(envelope["items"][0])
    category = item.get("category")
    known = item.get("amount") is not None and item.get("amount_mode") != "available"
    if known:
        data = _data(context, envelope)
        current, target = _value(data, data["before"], category), data["targets"][category]
        direction = "up" if target > current else "down" if target < current else None
        amount = abs(target - current)
    else:
        direction, amount = item.get("direction"), 0
        item.update(amount=0, amount_mode="by")
        checked = {**deepcopy(envelope), "items": [item]}
        data = _data(context, checked)
    if direction not in ("up", "down"):
        return response
    response["amount_was_given"] = known
    excluded = list(envelope.get("excluded", []))
    plans = []
    profiles = _profiles(category, direction)
    selected = envelope.get('sources' if direction == 'up' else 'destinations', [])
    if respect_selected and selected and any(code in context['allocations'] for code in selected):
        names = {**context.get('category_names', {}), **NAMES}
        profiles = [('living', ' + '.join(names.get(code, code) for code in selected), list(selected))]
    for plan_id, label, edges in profiles:
        candidate = {"items": [{"category": category, "amount_mode": "by", "amount": amount,
                                "direction": direction, "automatic": item.get("automatic", False) if known else True}],
                     "sources": edges if direction == "up" else [],
                     "destinations": edges if direction == "down" else [], "excluded": list(excluded)}
        if respect_selected:
            field = 'sources' if direction == 'up' else 'destinations'
            if envelope.get(field):
                candidate[field] = [code for code in candidate[field] if code in envelope[field]]
                if not candidate[field]:
                    continue
        if category == LIVING and direction == "up":
            candidate["destinations"] = list(envelope.get("destinations", []))
        # Unknown or disabled sources are never counted as spendable. Show their
        # limitation even when a different source in the combined option is ready.
        unavailable = []
        if direction == "up":
            usable = []
            for code in candidate["sources"]:
                if code in BUCKETS and context.get("bucket_status", {}).get(code) != "ready":
                    reason = "지원되지 않아" if context.get("bucket_status", {}).get(code) == "disabled" else "가용 상태 확인이 필요해"
                    unavailable.append(NAMES[code] + "은 " + reason + " 비교 재원에서 제외했어요.")
                else:
                    usable.append(code)
            candidate["sources"] = usable
        pending_destination = direction == "down" and len(candidate['destinations']) != 1
        scope = "living" if LIVING in edges else "spare" if edges == ["SPARE"] else "extended"
        plan = {"id": plan_id, "scope": scope, "label": label, "status": "needs_destination",
                "maximum": None, "amount": 0, "result": None, "envelope": None,
                "lines": unavailable, "recommended": False, "candidate": candidate}
        if pending_destination:
            candidate["destinations"] = [LIVING] if LIVING in edges else []
            plan["lines"].append(REASONS["needs_destination"])
        elif direction == "up" and not candidate["sources"]:
            needs_data = any(context.get("bucket_status", {}).get(code) != "disabled" for code in edges)
            plan.update(status="needs_data" if needs_data else "infeasible")
            plan["lines"].append(REASONS[plan["status"]])
        elif known:
            result = simulate_funding(context, candidate, allocate_group_reduction=allocate_group_reduction)
            plan.update(status=result["status"], result=result)
        else:
            quote = available_adjustment(context, category, direction,
                sources=candidate["sources"], destinations=candidate["destinations"], excluded=excluded,
                allocate_group_reduction=allocate_group_reduction)
            plan.update(status=quote["status"], maximum=quote["maximum"])
            if quote["status"] != "ready":
                plan["lines"].append(REASONS.get(quote["status"], "이 안은 추가 확인이 필요해요."))
        plans.append(plan)
    if not known:
        maxima = [plan["maximum"] for plan in plans if plan["status"] == "ready" and plan["maximum"]]
        amount = min(maxima) if maxima else 0
    response.update(basis_amount=amount, plans=plans)
    for plan in plans:
        candidate = plan.pop("candidate")
        candidate["items"][0]["amount"] = amount
        if plan["status"] == "needs_destination":
            if amount:
                plan["envelope"] = candidate
            continue
        if plan["status"] not in ("ready", "proposed", "partial") or not amount:
            continue
        result = simulate_funding(context, candidate, allocate_group_reduction=allocate_group_reduction)
        plan.update(result=result, status="ready" if result["status"] == "proposed" else result["status"])
        if result["status"] not in ("proposed", "partial"):
            plan["lines"].append(REASONS.get(result["status"], "이 금액으로는 추가 검수가 필요해요."))
            continue
        actual = sum(result["allocations"].values()) if category == LIVING else (
            result["allocations"].get(category, result["buckets"].get(category)))
        plan["amount"] = abs(actual - _value(data, data["before"], category))
        plan["envelope"] = deepcopy(result["requests"])
        names = {**context.get("category_names", {}), **NAMES}
        for change in result["changes"]:
            plan["lines"].append(f"{names.get(change['category'], change['category'])}: "
                                 f"{change['before']:,} → {change['after']:,}원")
        changed = {change["category"] for change in result["changes"]}
        if direction == "up" and plan["id"] == "spare" and category != "SPARE":
            plan["lines"].append("다른 배분을 유지하는 대신, 앞으로 쓸 여윳돈이 줄어요.")
        elif changed & {"SAVINGS", "CUSHION_MONTHLY"}:
            plan["lines"].append("저축·월 쿠션의 배정 계획이 달라져요. 이미 납입한 돈은 회수하지 않아요.")
        elif direction == "up":
            plan["lines"].append("저축·월 쿠션·여윳돈을 유지하는 대신 다른 생활비 항목의 여유가 줄어요.")
    selectable = any(plan['status'] in ('ready', 'partial') and plan.get('envelope')
                     and plan['amount'] > 0 and (plan.get('result') or {}).get('changes') for plan in plans)
    response["status"] = "ready" if selectable else "unavailable"
    response["message"] = (f"같은 {amount:,}원을 기준으로 비교했어요." if known else
                           f"금액 미지정으로 비교 가능한 안의 공통 가능액 {amount:,}원을 기준으로 삼았어요. 필요한 금액의 권고는 아니에요.")
    if not selectable:
        name = (context.get('category_names') or {}).get(category, category)
        current = context['allocations'].get(category)
        if context.get('spending_status', 'complete') != 'complete':
            response['message'] = '지금까지 쓴 돈을 모두 확인하지 못해 계산하지 못했어요. 계좌·카드 내역을 다시 불러온 뒤 확인해 주세요.'
        elif category in context.get('locks', {}):
            response['message'] = f'{name} 예산을 {context["locks"][category]:,}원으로 고정해 두셨어요. 이 금액을 바꾸려면 먼저 고정을 풀어도 되는지 알려 주세요.'
        elif direction == 'down' and not known and current is not None:
            keep = max(context.get('minimums', {}).get(category, 0), context.get('system_floors', {}).get(category, 0),
                       context.get('spent', {}).get(category, 0), min(current, context.get('ml_reference', {}).get(category, 0)))
            if current <= keep:
                response['message'] = (f'자동으로 줄일 때는 {name}에 {keep:,}원을 남기도록 계산해요. 지금 예산은 {current:,}원이어서 '
                                       '자동으로 줄일 수 있는 금액이 없어요. 그보다 적게 잡고 싶다면 원하는 금액을 알려 주세요.')
            else:
                response['message'] = f'{name} 예산을 줄일 금액을 자동으로 정하지 못했어요. 줄인 뒤 얼마를 남기고 싶은지 알려 주세요.'
        else:
            response['message'] = '지금 선택한 범위에서는 옮길 수 있는 돈을 찾지 못했어요. 다른 곳에서 가져오거나 바꾸려는 금액을 다시 정해 주세요.'
    _assess_and_recommend(context, response, category)
    return response
