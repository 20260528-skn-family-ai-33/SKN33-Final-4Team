"""Pure adjustment calculation. Automatic cuts require an explicit allocation policy."""
from copy import deepcopy
from fractions import Fraction


CATEGORY_CODES = (
    "FOOD", "TRANSPORT_CAR", "HOUSEHOLD", "FASHION_SHOPPING", "MEDICAL_HEALTH",
    "BEAUTY", "EDUCATION", "CULTURE_LEISURE", "ETC", "TRAVEL_LODGING",
)
ALL_CODES = set(CATEGORY_CODES) | {"HOUSING_TELECOM", "TRAVEL_LODGING", "UNCLASSIFIED"}
NON_BUDGET_CODES = {"ETC"}
BLOCKED = {"needs_policy", "needs_data", "needs_destination", "infeasible"}


class AdjustmentError(ValueError):
    """Invalid input or an unverified calculation result."""


def _money(value, code):
    if type(value) is not int or value < 0:
        raise AdjustmentError(code)
    return value


def _money_map(value, code, allowed=ALL_CODES):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise AdjustmentError(code)
    return {c: _money(value[c], code) for c in sorted(value)}


def _prepare(context, requests):
    if not isinstance(context, dict):
        raise AdjustmentError("invalid_context")
    amounts = _money_map(context.get("allocations"), "invalid_allocations", CATEGORY_CODES)
    if not amounts:
        raise AdjustmentError("empty_allocations")
    data = {"allocations": amounts}
    for key in ("spent", "minimums", "system_floors", "locks"):
        data[key] = _money_map(context.get(key, {}), "invalid_" + key)
    if set(data["locks"]) - set(amounts):
        raise AdjustmentError("lock_without_allocation")
    priorities = context.get("priorities", {})
    if (not isinstance(priorities, dict) or set(priorities) - set(CATEGORY_CODES)
            or any(type(v) is not int or v <= 0 for v in priorities.values())
            or len(set(priorities.values())) != len(priorities)):
        raise AdjustmentError("invalid_priorities")
    data["priorities"] = dict(priorities)
    reference = context.get("ml_reference")
    data["ml_reference"] = {} if reference is None else _money_map(reference, "invalid_ml_reference")
    not_applicable = context.get("ml_not_applicable", ("ETC",))
    if (not isinstance(not_applicable, (list, tuple, set, frozenset))
            or any(not isinstance(c, str) or c not in ALL_CODES for c in not_applicable)):
        raise AdjustmentError("invalid_ml_not_applicable")
    data["ml_not_applicable"] = set(not_applicable)
    if not isinstance(requests, list):
        raise AdjustmentError("invalid_requests")
    normalized, targets = [], {}
    for request in requests:
        if not isinstance(request, dict):
            raise AdjustmentError("invalid_request")
        c, mode, direction = request.get("category"), request.get("amount_mode"), request.get("direction")
        if not isinstance(c, str) or c not in amounts or c in NON_BUDGET_CODES:
            raise AdjustmentError("unsupported_category")
        if c in targets:
            raise AdjustmentError("duplicate_request")
        amount = _money(request.get("amount"), "invalid_amount")
        if mode not in ("by", "to", "remaining") or direction not in (None, "up", "down"):
            raise AdjustmentError("invalid_request_mode")
        if mode == "by":
            if direction is None:
                raise AdjustmentError("direction_required")
            target = amounts[c] + (amount if direction == "up" else -amount)
        elif mode == "remaining":
            target = data["spent"].get(c, 0) + amount
        else:
            target = amount
        if target < 0:
            raise AdjustmentError("negative_target")
        if ((direction == "up" and target < amounts[c])
                or (direction == "down" and target > amounts[c])):
            raise AdjustmentError("direction_conflict")
        targets[c] = target
        normalized.append({"category": c, "amount_mode": mode, "amount": amount, **(
            {"direction": direction} if direction else {})})
    data.update(targets=targets, requests=normalized)
    return data


def _hard_floor(data, category):
    keys = ("minimums", "spent") if category in data["locks"] else ("minimums", "system_floors", "spent")
    return max(data[key].get(category, 0) for key in keys)


def _floor(data, category):
    floor = _hard_floor(data, category)
    if category in data["ml_not_applicable"]:
        return floor
    if category not in data["ml_reference"]:
        raise AdjustmentError("ml_reference_unavailable")
    return max(floor, min(data["allocations"][category], data["ml_reference"][category]))


def _eligible(data, donor, recipient, *, allow_ranked=False):
    if donor in NON_BUDGET_CODES or recipient in NON_BUDGET_CODES:
        return False
    donor_rank = data["priorities"].get(donor)
    receiver_rank = data["priorities"].get(recipient)
    return donor_rank is None or allow_ranked and receiver_rank is not None and donor_rank > receiver_rank


def _groups(data, recipient):
    candidates = [c for c in data["allocations"] if c not in data["targets"]
                  and c not in data["locks"] and _eligible(data, c, recipient)]
    unranked = [c for c in candidates if c not in data["priorities"]]
    groups = [unranked] if unranked else []
    ranks = sorted({data["priorities"][c] for c in candidates if c in data["priorities"]}, reverse=True)
    groups.extend([c for c in candidates if data["priorities"].get(c) == rank] for rank in ranks)
    return groups


def _result(data, status, amounts=None, transfers=(), conflicts=()):
    before = data["allocations"]
    amounts = dict(before if amounts is None else amounts)
    shortfalls = [{"category": c, "requested_amount": target - before[c],
                   "applied_amount": max(amounts[c] - before[c], 0),
                   "missing_amount": target - amounts[c]}
                  for c, target in data["targets"].items() if target > before[c] and amounts[c] < target]
    return {"status": status, "allocations": amounts, "requests": deepcopy(data["requests"]),
            "requested_targets": dict(data["targets"]), "transfers": deepcopy(list(transfers)),
            "changes": [{"category": c, "before": before[c], "after": v, "diff": v - before[c],
                         "direct": c in data["targets"]} for c, v in amounts.items() if v != before[c]],
            "shortfalls": shortfalls, "conflicts": deepcopy(list(conflicts))}


def _conflicts(data):
    conflicts = [{"code": "lock_mismatch", "category": c} for c, v in data["locks"].items()
                 if v != data["allocations"][c]]
    conflicts.extend({"code": "lock_below_minimum", "category": c} for c, v in data["locks"].items()
                     if v < data["minimums"].get(c, 0))
    for c, target in data["targets"].items():
        if target == data["allocations"][c]:
            continue
        if c in data["locks"]:
            conflicts.append({"code": "locked_category", "category": c})
        if target < _hard_floor(data, c):
            conflicts.append({"code": "below_protected_amount", "category": c,
                              "minimum_amount": _hard_floor(data, c)})
    return conflicts


def _cuts(policy, group, required, capacities):
    try:
        response = policy(tuple(group), required, dict(capacities))
    except Exception:
        raise AdjustmentError("allocation_policy_failed") from None
    if not isinstance(response, dict) or set(response) != {"reductions", "remaining"}:
        raise AdjustmentError("invalid_policy_result")
    cuts = _money_map(response["reductions"], "invalid_policy_reductions", group)
    remaining = _money(response["remaining"], "invalid_policy_remaining")
    if (any(v > capacities[c] for c, v in cuts.items())
            or sum(cuts.values()) + remaining != required):
        raise AdjustmentError("invalid_policy_total")
    return cuts


def proportional_group_reduction(group, required, capacities):
    """Round capacity-proportional shares to 10,000 won, then fill 5,000-won gaps."""
    if (not isinstance(group, (list, tuple)) or any(not isinstance(c, str) or not c for c in group)
            or len(set(group)) != len(group)):
        raise AdjustmentError("invalid_policy_group")
    required = _money(required, "invalid_policy_required")
    capacities = _money_map(capacities, "invalid_policy_capacities", group)
    if set(capacities) != set(group):
        raise AdjustmentError("invalid_policy_capacities")
    cuts = dict.fromkeys(group, 0)
    charge = min(required // 5000, sum(v // 5000 for v in capacities.values())) * 5000
    if not charge:
        return {"reductions": cuts, "remaining": required}
    total = sum(capacities.values())
    exact = {c: Fraction(charge * capacities[c], total) for c in group}
    cuts = {c: int(exact[c] // 10000) * 10000 for c in group}
    remaining = charge - sum(cuts.values())
    # Rounding each exact share down leaves fewer than two half-units per category.
    while remaining:
        eligible = [c for c in group if cuts[c] + 5000 <= capacities[c]]
        chosen = min(eligible, key=lambda c: (-(exact[c] - cuts[c]), c))
        cuts[chosen] += 5000
        remaining -= 5000
    return {"reductions": cuts, "remaining": required - charge}


def simulate_adjustment(context, requests, *, allocate_group_reduction=None):
    """Return a checked, total-preserving candidate; never mutate inputs or select an A/B policy.

    Requests use nonnegative won amounts, by/to/remaining, and up/down for by.
    Explicit decreases fund explicit increases without an automatic-cut policy.
    """
    data = _prepare(context, requests)
    conflicts = _conflicts(data)
    if conflicts:
        return _result(data, "infeasible", conflicts=conflicts)
    before, targets = data["allocations"], data["targets"]
    donors = {c: before[c] - target for c, target in targets.items() if target < before[c]}
    recipients = [c for c, target in targets.items() if target > before[c]]
    demand = sum(targets[c] - before[c] for c in recipients)
    if sum(donors.values()) > demand:
        return _result(data, "needs_destination", conflicts=[{"code": "destination_required",
                       "amount": sum(donors.values()) - demand}])
    if not recipients:
        return _result(data, "unchanged")
    order = {r["category"]: i for i, r in enumerate(data["requests"])}
    recipients.sort(key=lambda c: (data["priorities"].get(c, float("inf")), order[c]))
    amounts, transfers = dict(before), []

    def move(donor, recipient, amount, direct):
        if amount:
            amounts[donor] -= amount
            amounts[recipient] += amount
            transfers.append({"from": donor, "to": recipient, "amount": amount, "direct": direct})

    for recipient in recipients:
        required = targets[recipient] - amounts[recipient]
        for donor, available in donors.items():
            amount = min(required, available)
            move(donor, recipient, amount, True)
            donors[donor] -= amount
            required -= amount
        if required == 0:
            continue
        for group in _groups(data, recipient):
            capacities, missing = {}, []
            for donor in group:
                if amounts[donor] <= _hard_floor(data, donor):
                    continue
                if donor not in data["ml_not_applicable"] and donor not in data["ml_reference"]:
                    missing.append(donor)
                    continue
                capacity = max(amounts[donor] - _floor(data, donor), 0)
                if capacity:
                    capacities[donor] = capacity
            if missing:
                return _result(data, "needs_data", conflicts=[{"code": "ml_reference_unavailable",
                               "categories": missing}])
            if not capacities:
                continue
            if allocate_group_reduction is None:
                return _result(data, "needs_policy", conflicts=[{"code": "allocation_policy_required"}])
            cuts = _cuts(allocate_group_reduction, list(capacities), required, capacities)
            for donor, amount in cuts.items():
                move(donor, recipient, amount, False)
                required -= amount
            if required == 0:
                break
    status = "proposed" if all(amounts[c] == targets[c] for c in recipients) else "partial"
    unmet = [c for c, value in amounts.items() if value != before[c] and value < _hard_floor(data, c)]
    if unmet:
        return _result(data, "infeasible", conflicts=[{"code": "protected_amount_not_reached",
                       "categories": unmet}])
    if amounts == before:
        return _result(data, "infeasible", conflicts=[{"code": "insufficient_funds"}])
    result = _result(data, status, amounts, transfers)
    return validate_result(context, requests, result)


def validate_result(context, requests, result):
    """Recheck a proposal against fresh context before persisting or approving it."""
    data = _prepare(context, requests)
    if not isinstance(result, dict):
        raise AdjustmentError("invalid_result")
    amounts = _money_map(result.get("allocations"), "invalid_result_allocations", data["allocations"])
    if set(amounts) != set(data["allocations"]) or sum(amounts.values()) != sum(data["allocations"].values()):
        raise AdjustmentError("result_total_mismatch")
    if result.get("requests") != data["requests"] or result.get("requested_targets") != data["targets"]:
        raise AdjustmentError("result_request_mismatch")
    status = result.get("status")
    transfers = result.get("transfers")
    if not isinstance(transfers, list):
        raise AdjustmentError("invalid_transfers")
    if status in BLOCKED:
        if amounts != data["allocations"] or transfers or result.get("changes"):
            raise AdjustmentError("blocked_result_changed_budget")
        return result
    if status not in ("proposed", "partial", "unchanged") or _conflicts(data):
        raise AdjustmentError("invalid_result_status")
    expected = dict(data["allocations"])
    for transfer in transfers:
        if not isinstance(transfer, dict) or set(transfer) != {"from", "to", "amount", "direct"}:
            raise AdjustmentError("invalid_transfer")
        donor, recipient = transfer["from"], transfer["to"]
        amount = _money(transfer["amount"], "invalid_transfer_amount")
        direct = transfer["direct"]
        if (donor not in expected or recipient not in data["targets"] or donor == recipient
                or type(direct) is not bool or amount == 0
                or data["targets"][recipient] <= data["allocations"][recipient]
                or donor in data["locks"]):
            raise AdjustmentError("invalid_transfer")
        if direct:
            if donor not in data["targets"] or data["targets"][donor] >= data["allocations"][donor]:
                raise AdjustmentError("unconfirmed_direct_donor")
        elif donor in data["targets"] or not _eligible(data, donor, recipient):
            raise AdjustmentError("ineligible_automatic_donor")
        expected[donor] -= amount
        expected[recipient] += amount
    if expected != amounts:
        raise AdjustmentError("transfer_ledger_mismatch")
    for c, amount in amounts.items():
        before, target = data["allocations"][c], data["targets"].get(c)
        if amount != before and amount < _hard_floor(data, c):
            raise AdjustmentError("protected_amount_violated")
        if c in data["locks"] and amount != data["locks"][c]:
            raise AdjustmentError("lock_violated")
        if c in data["targets"]:
            if target < before and amount != target:
                raise AdjustmentError("direct_decrease_mismatch")
            if target >= before and not before <= amount <= target:
                raise AdjustmentError("requested_increase_mismatch")
        elif amount > before:
            raise AdjustmentError("unrequested_increase")
        if amount < before:
            floor = _hard_floor(data, c) if c in data["targets"] else _floor(data, c)
            if amount < floor:
                raise AdjustmentError("protected_amount_violated")
    checked = _result(data, status, amounts, transfers)
    if result.get("changes") != checked["changes"] or result.get("shortfalls") != checked["shortfalls"]:
        raise AdjustmentError("result_details_mismatch")
    has_shortfall = bool(checked["shortfalls"])
    if ((status == "proposed" and has_shortfall) or (status == "partial" and not has_shortfall)
            or (status == "unchanged" and (amounts != data["allocations"] or has_shortfall))):
        raise AdjustmentError("result_outcome_mismatch")
    if result.get("conflicts") != []:
        raise AdjustmentError("successful_result_has_conflicts")
    return result
