"""Budget-design functions, deliberately not connected to the recommend API yet."""
from copy import deepcopy
from fractions import Fraction
from math import isfinite

from .recommend import (
    CATEGORY_CODES, EngineCheckFailed, allocation_conflicts, check_inputs,
    check_result, infeasible,
)

DISCRETIONARY = frozenset(("FASHION_SHOPPING", "BEAUTY", "CULTURE_LEISURE", "TRAVEL_LODGING"))
CONDITIONAL_CATEGORIES = frozenset(("HOUSING_TELECOM", "TRAVEL_LODGING"))
# Static labels keep candidate explanations independent of DB reads.
CATEGORY_NAMES = {
    "FOOD": "식비", "HOUSING_TELECOM": "주거·통신", "TRANSPORT_CAR": "교통·차량",
    "HOUSEHOLD": "생활용품", "FASHION_SHOPPING": "패션·쇼핑", "MEDICAL_HEALTH": "의료·건강",
    "BEAUTY": "뷰티·미용", "EDUCATION": "교육", "CULTURE_LEISURE": "문화·여가",
    "TRAVEL_LODGING": "여행·숙박",
}


def ml_profile(inp):
    """Team input mapping: monthly KRW; do not subtract fixed costs again."""
    raw_sex = inp.get("sex")
    sex = {"male": 0, "female": 1}.get(raw_sex) if isinstance(raw_sex, str) else None
    car = inp.get("has_vehicle")
    age = inp.get("age")
    if type(age) is not int or not 20 <= age <= 34 or sex is None or type(car) is not bool:
        raise ValueError("unsupported_ml_profile")
    for key in ("take_home_income", "housing_fixed", "target"):
        value = inp.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("invalid_ml_amount")
    if inp["take_home_income"] == 0 or inp["target"] == 0:
        raise ValueError("ml_requires_positive_income_and_target")
    return {"age": age, "sex": sex, "has_vehicle": int(car),
            "disposable_income": inp["take_home_income"],
            "fixed_expense_amount": inp["housing_fixed"],
            "living_budget_amount": inp["target"]}


def _weights(ratios):
    if not isinstance(ratios, dict) or set(ratios) != set(CATEGORY_CODES):
        raise ValueError("invalid_ratio_categories")
    for value in ratios.values():
        if type(value) not in (int, float) or not isfinite(value) or value < 0:
            raise ValueError("invalid_ratio_value")
    weights = {code: Fraction(str(ratios[code])) for code in CATEGORY_CODES}
    if not sum(weights.values()):
        raise ValueError("zero_ratio_sum")
    return weights


def _allocate(inp, weights, active):
    # R-05 with supplied ML weights; shared recommend/check_result stay unchanged.
    allocations = {c: 0 for c in CATEGORY_CODES if c not in active}
    allocations.update(inp["locks"])
    free = [c for c in CATEGORY_CODES if c not in allocations]
    while free:
        remaining = inp["target"] - sum(allocations.values())
        if remaining == sum(inp["minimums"].get(c, 0) for c in free):
            allocations.update({c: inp["minimums"].get(c, 0) for c in free})
            break
        weight = sum(weights[c] for c in free)
        if not weight:
            raise ValueError("zero_free_weights_requires_policy")
        shares = {c: remaining * weights[c] / weight for c in free}
        fixed = [c for c in free if shares[c] < inp["minimums"].get(c, 0)]
        if not fixed:
            receiver = max(free, key=weights.get)
            rounded = {c: int(shares[c] // 10_000) * 10_000 for c in free}
            fixed = [c for c in free if c != receiver and rounded[c] < inp["minimums"].get(c, 0)]
            if not fixed:
                allocations.update(rounded)
                allocations[receiver] += inp["target"] - sum(allocations.values())
                break
        for code in fixed:
            allocations[code] = inp["minimums"][code]
            free.remove(code)
    return {c: allocations[c] for c in CATEGORY_CODES}


def _priority_order(rows):
    if not isinstance(rows, list) or not 3 <= len(rows) <= 10:
        raise ValueError("priorities_require_3_to_10")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"category", "rank"}
                or type(row["rank"]) is not int or not isinstance(row["category"], str)
                or row["category"] not in CATEGORY_CODES):
            raise ValueError("invalid_priority")
        if row["category"] == "TRAVEL_LODGING":
            raise ValueError("travel_not_rankable")
    ordered = sorted(rows, key=lambda row: row["rank"])
    if ([r["rank"] for r in ordered] != list(range(1, len(rows) + 1))
            or len({r["category"] for r in rows}) != len(rows)):
        raise ValueError("invalid_priority_order")
    return [row["category"] for row in ordered]


def _money_map(values, allowed, name, *, required=()):
    if (not isinstance(values, dict) or set(values) - set(allowed)
            or set(required) - set(values)
            or any(type(v) is not int or v < 0 for v in values.values())):
        raise ValueError("invalid_" + name)
    return values


def _policy(policy, receivers):
    # No guessed numeric defaults: these are explicit scenario inputs until review.
    keys = {"total_limit", "increase_rates", "increase_limits", "decrease_limits", "ceilings"}
    if not isinstance(policy, dict) or set(policy) != keys:
        raise ValueError("explicit_adjustment_policy_required")
    if type(policy["total_limit"]) is not int or policy["total_limit"] < 0:
        raise ValueError("invalid_total_limit")
    rates = policy["increase_rates"]
    if (not isinstance(rates, dict) or set(rates) - set(CATEGORY_CODES)
            or set(receivers) - set(rates)
            or any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in rates.values())):
        raise ValueError("invalid_increase_rates")
    for name in ("increase_limits", "decrease_limits", "ceilings"):
        required = CATEGORY_CODES if name == "decrease_limits" else receivers
        _money_map(policy[name], CATEGORY_CODES, name, required=required)


def _adjust(inp, base, order, active, policy, ml_amounts):
    receivers = [c for c in order[:3] if c in active and c not in inp["locks"]]
    _policy(policy, receivers)
    amounts = base.copy()
    left = policy["total_limit"]
    donated = dict.fromkeys(CATEGORY_CODES, 0)
    transfers, stops, targets = [], {}, {}
    ranks = {c: rank for rank, c in enumerate(order, 1)}
    unranked = [c for c in CATEGORY_CODES if c not in ranks]
    low = list(reversed(order[3:]))
    # Within each tier, discretionary spending first; ties keep stable code/rank order.
    ordinary = [c for group in (unranked, low) for discretionary in (True, False)
                for c in group if (c in DISCRETIONARY) == discretionary]
    for receiver in receivers:
        raw = ml_amounts[receiver] * Fraction(str(policy["increase_rates"][receiver])) / 100
        # Round half up, not Python's ties-to-even; only the increase is rounded.
        targets[receiver] = int(raw / 1_000 + Fraction(1, 2)) * 1_000
        ceiling = max(policy["ceilings"][receiver], inp["minimums"].get(receiver, 0))
        need = min(targets[receiver], policy["increase_limits"][receiver],
                   max(0, ceiling - amounts[receiver])) // 1_000 * 1_000
        if need == 0:
            stops[receiver] = "zero_rate_target" if targets[receiver] == 0 else "category_limit"
            continue
        extra = [c for c in reversed(order[:3])
                 if c in DISCRETIONARY and ranks[c] > ranks[receiver]]
        for donor in ordinary + extra:
            if left < 1_000 or need == 0:
                break
            if donor not in active or donor in inp["locks"]:
                continue
            # Never recycle an earlier increase as funding for a later receiver.
            available = min(
                min(base[donor], amounts[donor]) - inp["minimums"].get(donor, 0),
                policy["decrease_limits"][donor] - donated[donor],
            )
            # Caps and hard floors take precedence over rounding up a transfer.
            taken = min(left, need, max(0, available)) // 1_000 * 1_000
            if not taken:
                continue
            amounts[donor] -= taken
            amounts[receiver] += taken
            donated[donor] += taken
            left -= taken
            need -= taken
            transfers.append({"from": donor, "to": receiver, "amount": taken})
        stops[receiver] = "target_or_category_limit" if need == 0 else "total_limit" if left < 1_000 else "no_funding"
    return amounts, transfers, stops, targets


def build_candidates(inp, ratios, *, policy=None, warning_thresholds=None):
    """ETC reserved upstream. Explicit limits enable adjustment; no DB/LLM calls.

    Travel is excluded by monthly-budget policy and cannot be ranked.
    Housing/travel currently activate for a positive minimum or any lock.
    Keep zero entries for the existing ten-code validator; UI needs review.
    """
    inp = deepcopy(inp)
    check_inputs(inp)
    if inp["target"] < 0:
        raise EngineCheckFailed("negative_target")
    order = _priority_order(inp.get("priorities"))
    policy = deepcopy(policy)
    warning_thresholds = _money_map(
        {} if warning_thresholds is None else warning_thresholds,
        DISCRETIONARY, "warning_thresholds")
    warnings = [{"code": "high_minimum", "category": c,
                 "amount": inp["minimums"][c], "reference": threshold}
                for c, threshold in warning_thresholds.items()
                if inp["minimums"].get(c, 0) > threshold]
    conflicts = allocation_conflicts(inp)
    if conflicts:
        out = infeasible(conflicts)
        check_result(inp, out)
        return {"status": "infeasible", "result": out, "candidates": [],
                "warnings": warnings + [{"code": "constraints_conflict"}],
                "next_action": "review_settings_or_budget_chat"}
    # TODO: Team must decide whether travel minimums/locks are rejected or mean
    # a trip-month budget. Preserve current protection until that decision.
    active = [c for c in CATEGORY_CODES if c not in CONDITIONAL_CATEGORIES
              or inp["minimums"].get(c, 0) > 0 or c in inp["locks"]]
    ml_amounts = dict.fromkeys(CATEGORY_CODES, Fraction(0))
    if inp["target"] == 0:
        amounts = dict.fromkeys(CATEGORY_CODES, 0)
    elif len(inp["locks"]) == len(CATEGORY_CODES):
        amounts = dict(inp["locks"])
    else:
        weights = _weights(ratios)
        ml_amounts = {c: inp["target"] * weights[c] / sum(weights.values()) for c in CATEGORY_CODES}
        amounts = _allocate(inp, weights, active)
    base = {"status": "proposed", "allocations": amounts, "reasons": [],
            "summary": "입력 비율에 최소 금액과 자물쇠를 반영한 기본 후보입니다.", "conflicts": []}
    check_result(inp, base)
    candidates = [{"id": "base", "reason_code": "baseline", "result": base}]
    stops, transfers, targets = {}, [], {}
    if policy is not None:
        adjusted = deepcopy(base)
        adjusted["allocations"], transfers, stops, targets = _adjust(inp, amounts, order, active, policy, ml_amounts)
        warnings.extend({"code": "minimum_above_ceiling", "category": c,
                         "amount": minimum, "reference": policy["ceilings"][c]}
                        for c, minimum in inp["minimums"].items()
                        if c in policy["ceilings"] and minimum > policy["ceilings"][c])
        if adjusted["allocations"] != amounts:
            ranks = {c: rank for rank, c in enumerate(order, 1)}
            adjusted["reasons"] = [
                f"{CATEGORY_NAMES[t['from']]}에서 {t['amount']:,}원을 "
                f"{CATEGORY_NAMES[t['to']]} 예산으로 옮겼어요"
                f"({CATEGORY_NAMES[t['to']]} {ranks[t['to']]}순위)"
                for t in transfers
            ]
            adjusted["summary"] = "최소 금액과 잠금을 지키며 미입력·후순위부터 재원을 마련해 상위 순위를 조정한 후보입니다."
            check_result(inp, adjusted)
            candidates.append({"id": "priority", "reason_code": "priority_transfer", "result": adjusted})
    return {"status": "proposed", "policy": policy, "candidates": candidates,
            "active_categories": active, "warnings": warnings,
            "transfers": transfers, "stop_reasons": stops, "increase_targets": targets}


def select_candidate(inp, candidates, choose=None):
    """Default to priority, else base. Optional choose gets ranks/directions only.

    The future LLM transport must enforce its own deadline and raise on failure.
    Free text and amounts are rejected; server templates produce the explanation.
    """
    inp, candidates = deepcopy(inp), deepcopy(candidates)
    if not candidates or len({c["id"] for c in candidates}) != len(candidates):
        raise ValueError("invalid_candidates")
    by_id = {c["id"]: c for c in candidates}
    if "base" not in by_id:
        raise ValueError("base_required")
    for candidate in candidates:
        if candidate["result"].get("status") != "proposed":
            raise ValueError("only_valid_proposals_can_be_selected")
        check_result(inp, candidate["result"])
    chosen, fallback = "base", None
    if "priority" in by_id:
        chosen = "priority"
    if choose is not None and len(candidates) > 1:
        baseline = by_id["base"]["result"]["allocations"]
        payload = {"priorities": deepcopy(inp["priorities"]), "candidates": [
            {"id": c["id"], "reason_code": c["reason_code"], "directions": {
                code: ("increase" if c["result"]["allocations"][code] > baseline[code]
                       else "decrease" if c["result"]["allocations"][code] < baseline[code] else "same")
                for code in CATEGORY_CODES}} for c in candidates]}
        try:
            answer = choose(payload)
            if (not isinstance(answer, dict) or set(answer) != {"candidate_id", "reason_code"}
                    or not isinstance(answer["candidate_id"], str)
                    or answer["candidate_id"] not in by_id
                    or answer["reason_code"] != by_id[answer["candidate_id"]]["reason_code"]):
                raise ValueError("invalid_selection")
            chosen = answer["candidate_id"]
        except Exception as exc:
            # External selector boundary: never log user input or raw model text.
            fallback = f"selector_failed:{type(exc).__name__}"
    result = deepcopy(by_id[chosen]["result"])
    check_result(inp, result)
    return {"candidate_id": chosen, "result": result, "fallback": fallback}
