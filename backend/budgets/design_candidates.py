"""Budget-design functions, deliberately not connected to the recommend API yet."""
from copy import deepcopy
from fractions import Fraction
from math import isfinite

from .recommend import (
    CATEGORY_CODES, EngineCheckFailed, allocation_conflicts, check_inputs,
    check_result, infeasible,
)

DISCRETIONARY = frozenset(("FASHION_SHOPPING", "BEAUTY", "CULTURE_LEISURE"))
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


def _allocate(inp, weights, active, *, audit=None):
    # R-05 with supplied ML weights; shared recommend/check_result stay unchanged.
    allocations = {c: 0 for c in CATEGORY_CODES if c not in active}
    allocations.update(inp["locks"])
    free = [c for c in CATEGORY_CODES if c not in allocations]
    fixed_since = {c: 'lock' if c in active else 'inactive' for c in allocations}
    while free:
        remaining = inp["target"] - sum(allocations.values())
        if remaining == sum(inp["minimums"].get(c, 0) for c in free):
            allocations.update({c: inp["minimums"].get(c, 0) for c in free})
            if audit is not None:
                audit.append((dict(allocations), {**fixed_since, **{c: 'minimum' for c in free}}))
            break
        weight = sum(weights[c] for c in free)
        if not weight:
            raise ValueError("zero_free_weights_requires_policy")
        shares = {c: remaining * weights[c] / weight for c in free}
        if audit is not None:
            audit.append(({**allocations, **shares}, fixed_since))
        rounding_fixed = False
        fixed = [c for c in free if shares[c] < inp["minimums"].get(c, 0)]
        if not fixed:
            receiver = max(free, key=weights.get)
            rounded = {c: int(shares[c] // 10_000) * 10_000 for c in free}
            fixed = [c for c in free if c != receiver and rounded[c] < inp["minimums"].get(c, 0)]
            rounding_fixed = bool(fixed)
            if not fixed:
                allocations.update(rounded)
                allocations[receiver] += inp["target"] - sum(allocations.values())
                if audit is not None:
                    audit.append((dict(allocations), {c: 'rounding' for c in free}))
                break
        fixed_since = {c: 'minimum_rounding' if rounding_fixed else 'minimum' for c in fixed}
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
            or any(v is not None and (type(v) not in (int, float) or not isfinite(v) or v < 0) for v in rates.values())):
        raise ValueError("invalid_increase_rates")
    for name in ("increase_limits", "decrease_limits", "ceilings"):
        required = CATEGORY_CODES if name == "decrease_limits" else receivers
        _money_map(policy[name], CATEGORY_CODES, name, required=required)


def _adjust(inp, base, order, active, policy, ml_amounts, *, enabled_receivers=None):
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
    for index, receiver in enumerate(receivers):
        if enabled_receivers is not None and receiver not in enabled_receivers:
            continue
        action = enabled_receivers.get(receiver, 'increase_large') if isinstance(enabled_receivers, dict) else 'increase_large'
        balanced = action.endswith('_available')
        ordinary = []
        for group in (unranked, low):
            for discretionary in (True, False):
                eligible = [c for c in group if (c in DISCRETIONARY) == discretionary]
                if balanced and group is unranked:
                    eligible.sort(key=lambda c: max(0, min(base[c], amounts[c]) - inp['minimums'].get(c, 0)), reverse=True)
                ordinary.extend(eligible)
        rate = policy["increase_rates"][receiver]
        if rate is None:
            portions = {'increase_small': 1, 'increase_medium': 2, 'increase_large': 3}
            raw = Fraction(left * portions[action.removesuffix('_available')], 3 * (len(receivers) - index))
        else:
            raw = ml_amounts[receiver] * Fraction(str(rate)) / 100
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


def _with_system_floors(inp, floors):
    calculation = deepcopy(inp)
    for code, amount in floors.items():
        if code not in calculation['locks']:
            calculation['minimums'][code] = max(calculation['minimums'].get(code, 0), amount)
    return calculation


def _check_system_floors(inp, allocations, floors):
    if any(allocations[c] < amount for c, amount in floors.items() if c not in inp['locks']):
        raise EngineCheckFailed('system_floor_violated')


def build_candidates(inp, ratios, *, policy=None, warning_thresholds=None, reality_profile=None):
    """ETC reserved upstream. Explicit limits enable adjustment; no DB/LLM calls.

    Travel is excluded by monthly-budget policy and cannot be ranked.
    Housing and travel minimums/locks are not accepted.
    Keep zero entries for the existing ten-code validator; UI needs review.
    """
    inp = deepcopy(inp)
    check_inputs(inp)
    if inp["target"] < 0:
        raise EngineCheckFailed("negative_target")
    order = _priority_order(inp.get("priorities"))
    if inp['minimums'].get('TRAVEL_LODGING', 0) > 0:
        raise ValueError('travel_minimum_not_allowed')
    if 'TRAVEL_LODGING' in inp['locks']:
        raise ValueError('travel_lock_not_allowed')
    if inp['minimums'].get('HOUSING_TELECOM', 0) > 0:
        raise ValueError('housing_minimum_not_allowed')
    if 'HOUSING_TELECOM' in inp['locks']:
        raise ValueError('housing_lock_not_allowed')
    policy = deepcopy(policy)
    warning_thresholds = _money_map(
        {} if warning_thresholds is None else warning_thresholds,
        DISCRETIONARY, "warning_thresholds")
    warnings = [{"code": "high_minimum", "category": c,
                 "amount": inp["minimums"][c], "reference": threshold}
                for c, threshold in warning_thresholds.items()
                if inp["minimums"].get(c, 0) > threshold]
    active = [c for c in CATEGORY_CODES if c not in ('TRAVEL_LODGING', 'HOUSING_TELECOM')]
    # Inactive categories are fixed at zero for both feasibility and validation.
    inp["locks"].update({c: 0 for c in CATEGORY_CODES if c not in active})
    conflicts = allocation_conflicts(inp)
    if conflicts:
        out = infeasible(conflicts)
        if any(c["type"] == "locks_mismatch_living" for c in conflicts):
            out["summary"] = "모든 항목에 잠금이 걸려 생활비와 배분액을 맞출 수 없어요."
        elif conflicts[0]['type'] == 'minimums_over_living':
            condition = '최소금액과 잠금액' if any(inp['locks'].values()) else '최소금액'
            out['summary'] = f"{condition}을 모두 지키려면 생활비가 {conflicts[0]['shortfall']:,}원 부족해요."
        check_result(inp, out)
        return {"status": "infeasible", "result": out, "candidates": [],
                "warnings": warnings + [{"code": "constraints_conflict"}],
                "next_action": "review_settings_or_budget_chat"}
    original_inp = deepcopy(inp)
    floors, ignored_floors = {}, []
    if reality_profile is not None:
        from .design_reality import system_floors
        floors = system_floors(reality_profile)
        ignored_floors = [c for c in floors if c in inp['locks']]
        inp = _with_system_floors(inp, floors)
        shortfall = sum(inp['locks'].get(c, inp['minimums'].get(c, 0)) for c in CATEGORY_CODES) - inp['target']
        if shortfall > 0:
            return {'status': 'needs_upper_allocation_review', 'result': None,
                    'reason': 'system_floor_unaffordable', 'shortfall': shortfall,
                    'categories': [c for c in floors if c not in inp['locks']],
                    'system_floors': floors, 'floor_ignored_locked': ignored_floors,
                    'candidates': [], 'warnings': warnings}
    ml_amounts = dict.fromkeys(CATEGORY_CODES, Fraction(0))
    audit = []
    if inp["target"] == 0:
        amounts = dict.fromkeys(CATEGORY_CODES, 0)
    elif len(inp["locks"]) == len(CATEGORY_CODES):
        amounts = dict(inp["locks"])
    else:
        weights = _weights(ratios)
        ml_amounts = {c: inp["target"] * weights[c] / sum(weights.values()) for c in CATEGORY_CODES}
        amounts = _allocate(inp, weights, active, audit=audit)
    from .design_explain import allocation_ledger, explain_allocations, transfer_ledger
    reference_amounts = ml_amounts
    if not audit:
        # ML is deliberately skipped for zero budgets / fully locked allocations.
        if ratios is not None:
            try:
                reference_weights = _weights(ratios)
                reference_amounts = {c: inp['target'] * reference_weights[c] / sum(reference_weights.values()) for c in CATEGORY_CODES}
            except ValueError:
                pass
        audit = [(amounts, {c: 'lock' if c in active else 'inactive' for c in CATEGORY_CODES})]
    ledger = allocation_ledger(reference_amounts, audit, original_inp, floors,
                               reference_unavailable=not any(reference_amounts.values()) and inp['target'] > 0)
    base = {"status": "proposed", "allocations": amounts, "reasons": [],
            "summary": "입력 비율에 최소 금액과 자물쇠를 반영한 기본 후보입니다.", "conflicts": []}
    check_result(original_inp, base)
    floor_applied = []
    if floors and inp['target'] and len(inp['locks']) != len(CATEGORY_CODES):
        original_amounts = _allocate(original_inp, weights, active)
        floor_applied = [c for c in floors if c not in inp['locks']
                         and original_amounts[c] < floors[c] and amounts[c] > original_amounts[c]]
        from .design_reality import REALITY_FLOORS
        base['reasons'].extend(f"{CATEGORY_NAMES[c]} 예산을 {REALITY_FLOORS[c]['label']}({floors[c]:,}원)에 맞췄어요"
                               for c in floor_applied)
    candidates = [{"id": "base", "reason_code": "baseline", "result": base}]
    stops, transfers, targets = {}, [], {}
    if policy is not None:
        adjusted = deepcopy(base)
        adjusted["allocations"], transfers, stops, targets = _adjust(inp, amounts, order, active, policy, ml_amounts)
        warnings.extend({"code": "minimum_above_ceiling", "category": c,
                         "amount": minimum, "reference": policy["ceilings"][c]}
                        for c, minimum in original_inp["minimums"].items()
                        if c in policy["ceilings"] and minimum > policy["ceilings"][c])
        if adjusted["allocations"] != amounts:
            ranks = {c: rank for rank, c in enumerate(order, 1)}
            adjusted["reasons"] = base["reasons"] + [
                f"{CATEGORY_NAMES[t['from']]}에서 {t['amount']:,}원을 "
                f"{CATEGORY_NAMES[t['to']]} 예산으로 옮겼어요"
                f"({CATEGORY_NAMES[t['to']]} {ranks[t['to']]}순위)"
                for t in transfers
            ]
            adjusted["summary"] = "최소 금액과 잠금을 지키며 미입력·후순위부터 재원을 마련해 상위 순위를 조정한 후보입니다."
            check_result(original_inp, adjusted)
            candidates.append({"id": "priority", "reason_code": "priority_transfer", "result": adjusted})
    for candidate in candidates:
        candidate_ledger = transfer_ledger(ledger, transfers if candidate['id'] == 'priority' else [], original_inp['priorities'])
        candidate['explanations'] = explain_allocations(reference_amounts, candidate['result']['allocations'], candidate_ledger,
            reference_type='ml_share', priorities=original_inp['priorities'], names=CATEGORY_NAMES,
            stop_reasons=stops if candidate['id'] == 'priority' or len(candidates) == 1 else None)
        _check_system_floors(original_inp, candidate['result']['allocations'], floors)
    extra = {'system_floors': floors, 'floor_applied': floor_applied,
             'floor_ignored_locked': ignored_floors} if reality_profile is not None else {}
    return {**extra, "status": "proposed", "policy": policy, "candidates": candidates,
            "active_categories": active, "warnings": warnings,
            "transfers": transfers, "stop_reasons": stops, "increase_targets": targets}


def apply_priority_decisions(inp, ratios, *, policy, decisions, reality_profile=None):
    """Replay a prefix of LLM decisions using the original server limits/ledger."""
    engine = build_candidates(inp, ratios, policy=policy, reality_profile=reality_profile)
    if engine.get('reason') == 'system_floor_unaffordable':
        return engine
    if engine['status'] != 'proposed' or policy is None:
        raise ValueError('adjustment_requires_feasible_policy')
    order = _priority_order(inp['priorities'])
    receivers = [c for c in order[:3] if c in engine['active_categories'] and c not in inp['locks']]
    if not isinstance(decisions, list) or len(decisions) > len(receivers):
        raise ValueError('invalid_adjustment_decisions')
    enabled = {}
    for code, decision in zip(receivers, decisions):
        if (not isinstance(decision, dict) or set(decision) != {'category', 'action'}
                or decision['category'] != code or decision['action'] not in
                (('increase_small', 'increase_medium', 'increase_large',
                  'increase_small_available', 'increase_medium_available', 'increase_large_available', 'keep')
                 if policy['increase_rates'][code] is None else ('increase', 'keep'))):
            raise ValueError('invalid_adjustment_decision')
        if decision['action'] != 'keep':
            enabled[code] = decision['action']
    base = engine['candidates'][0]['result']
    weights = _weights(ratios)
    ml_amounts = {c: inp['target'] * weights[c] / sum(weights.values()) for c in CATEGORY_CODES}
    floors = engine.get('system_floors', {})
    calculation = _with_system_floors(inp, floors)
    amounts, transfers, stops, targets = _adjust(
        calculation, base['allocations'], order, engine['active_categories'], policy, ml_amounts,
        enabled_receivers=enabled)
    checked = {**base, 'allocations': amounts}
    check_result(inp, checked)
    _check_system_floors(inp, amounts, floors)
    extra = {key: engine[key] for key in ('system_floors', 'floor_applied', 'floor_ignored_locked', 'warnings')} if reality_profile is not None else {}
    from .design_explain import explain_allocations, transfer_ledger
    base_explanations = engine['candidates'][0]['explanations']
    references = {e['category']: e['reference_amount'] for e in base_explanations}
    ledger = transfer_ledger({e['category']: e['causes'] for e in base_explanations}, transfers, inp['priorities'])
    explanations = explain_allocations(references, amounts, ledger, reference_type='ml_share',
                                       priorities=inp['priorities'], names=CATEGORY_NAMES, stop_reasons=stops)
    return {**extra, 'explanations': explanations, 'allocations': amounts, 'transfers': transfers, 'stop_reasons': stops,
            'increase_targets': targets, 'remaining_transfer_limit':
                policy['total_limit'] - sum(t['amount'] for t in transfers),
            'pending_categories': receivers[len(decisions):]}


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
    return {"candidate_id": chosen, "result": result, "fallback": fallback,
            "explanations": deepcopy(by_id[chosen].get("explanations", []))}
