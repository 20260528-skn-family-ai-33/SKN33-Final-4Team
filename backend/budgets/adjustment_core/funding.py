"""Pure category and upper-allocation simulation; no bank or database mutations."""
from copy import deepcopy

from .calculation import AdjustmentError, NON_BUDGET_CODES, _cuts, _eligible, _floor, _hard_floor, _money, _money_map, _prepare


BUCKETS = ("SAVINGS", "CUSHION_MONTHLY", "SPARE")
LIVING = "LIVING"
BLOCKED = {"needs_amount", "needs_source", "needs_destination", "needs_confirmation",
           "needs_policy", "needs_data", "infeasible"}


def increase_input_limit(context, category):
    """Absolute budget ceiling, not a recommendation or permission to spend it all."""
    living = 0 if category == LIVING else sum(amount for code, amount in context['allocations'].items()
                                             if code != category and code not in NON_BUDGET_CODES)
    return living + sum(amount for code, amount in context['buckets'].items() if code != category)


def oversized_increase(context, item):
    if item.get('amount') is None or item.get('amount_mode') in (None, 'available'):
        return None
    try:
        data = _data(context, {'items': [item]})
    except AdjustmentError:
        return None
    category = item['category']
    increase = data['targets'][category] - _value(data, data['before'], category)
    limit = increase_input_limit(context, category)
    return (increase, limit) if increase > limit else None


def large_increase_warning(context, items):
    income = context.get('monthly_income')
    if type(income) is not int or income <= 0 or not items or any(item.get('amount') is None for item in items):
        return None
    try:
        data = _data(context, {'items': items})
    except AdjustmentError:
        return None
    increases = {code: max(target - _value(data, data['before'], code), 0) for code, target in data['targets'].items()}
    living = max(increases.get(LIVING, 0), sum(increases.get(code, 0) for code in data['allocations']))
    amount = living + sum(increases.get(code, 0) for code in BUCKETS)
    if amount * 100 >= income * 30:
        return '요청 금액이 월 실수령액의 30% 이상이에요. 준비한 돈과 추가로 필요한 돈을 나눠 확인해 주세요.'
    return None


class _Blocked(Exception):
    def __init__(self, status, code, **details):
        self.status, self.conflict = status, {"code": code, **details}


def _codes(value, allowed, name):
    if not isinstance(value, (list, tuple)) or any(not isinstance(c, str) or c not in allowed for c in value):
        raise AdjustmentError("invalid_" + name)
    if len(set(value)) != len(value):
        raise AdjustmentError("duplicate_" + name)
    return list(value)


def _units(context):
    automatic = _money(context.get("automatic_unit", 1), "invalid_automatic_unit")
    if automatic not in (1, 10000):
        raise AdjustmentError("invalid_automatic_unit")
    fallback = _money(context.get("fallback_unit", automatic), "invalid_fallback_unit")
    if (automatic, fallback) not in {(1, 1), (10000, 10000), (10000, 5000)}:
        raise AdjustmentError("invalid_fallback_unit")
    return automatic, fallback


def _data(context, envelope):
    data = _prepare(context, [])
    data["automatic_unit"], data["fallback_unit"] = _units(context)
    data["buckets"] = _money_map(context.get("buckets"), "invalid_buckets", BUCKETS)
    if set(data["buckets"]) != set(BUCKETS):
        raise AdjustmentError("missing_buckets")
    data["funding_amount"] = _money(context.get("funding_amount"), "invalid_funding_amount")
    data["fixed_amount"] = _money(context.get("fixed_amount"), "invalid_fixed_amount")
    if data["funding_amount"] != data["fixed_amount"] + sum(data["allocations"].values()) + sum(data["buckets"].values()):
        raise AdjustmentError("funding_total_mismatch")
    for key in ("bucket_floors", "bucket_available"):
        data[key] = _money_map(context.get(key, {}), "invalid_" + key, BUCKETS)
    status = context.get("bucket_status", {})
    if (not isinstance(status, dict) or set(status) - set(BUCKETS)
            or any(v not in ("ready", "unknown", "disabled") for v in status.values())):
        raise AdjustmentError("invalid_bucket_status")
    data["bucket_status"] = {c: status.get(c, "unknown") for c in BUCKETS}
    if not isinstance(envelope, dict) or not isinstance(envelope.get("items"), list):
        raise AdjustmentError("invalid_funding_request")
    allowed = set(data["allocations"]) | set(BUCKETS) | {LIVING}
    normalized = {key: _codes(envelope.get(key, []), allowed, key)
                  for key in ("sources", "destinations", "excluded")}
    if NON_BUDGET_CODES.intersection(normalized['sources'] + normalized['destinations']):
        raise AdjustmentError('unsupported_category')
    if envelope.get('spare_minimum') is not None:
        minimum = _money(envelope['spare_minimum'], 'invalid_spare_minimum')
        normalized['spare_minimum'] = minimum
        data['bucket_available']['SPARE'] = min(data['bucket_available'].get('SPARE', 0),
                                               max(data['buckets']['SPARE'] - minimum, 0))
    before = {**data["allocations"], **data["buckets"]}
    data["before"] = before
    data["targets"], data["items"] = {}, []
    for item in envelope["items"]:
        if not isinstance(item, dict):
            raise AdjustmentError("invalid_funding_item")
        c, mode, direction = item.get("category"), item.get("amount_mode"), item.get("direction")
        if not isinstance(c, str) or c not in allowed or c in NON_BUDGET_CODES:
            raise AdjustmentError("unsupported_category")
        if c in data["targets"]:
            raise AdjustmentError("duplicate_request")
        automatic = item.get("automatic", False)
        if type(automatic) is not bool:
            raise AdjustmentError("invalid_automatic")
        amount = _money(item.get("amount"), "invalid_amount")
        if mode not in ("by", "to", "remaining", "percent_by", "percent_to") or direction not in (None, "up", "down"):
            raise AdjustmentError("invalid_request_mode")
        current = _value(data, before, c)
        if mode in ("by", "percent_by"):
            if direction is None:
                raise AdjustmentError("direction_required")
            delta = amount if mode == "by" else current * amount // 100
            target = current + (delta if direction == "up" else -delta)
        elif mode == "percent_to":
            target = current * amount // 100
        elif mode == "remaining":
            used = (_living_spent(data) if c == LIVING else data["spent"].get(c, 0)
                    if c in data["allocations"] else current - min(current, data["bucket_available"].get(c, 0)))
            target = used + amount
        else:
            target = amount
        if target < 0:
            raise AdjustmentError("negative_target")
        if (direction == "up" and target < current) or (direction == "down" and target > current):
            raise AdjustmentError("direction_conflict")
        data["targets"][c] = target
        data["items"].append({"category": c, "amount_mode": mode, "amount": amount,
                              "automatic": automatic, **({"direction": direction} if direction else {})})
    if LIVING in data["targets"] and set(data["targets"]) & set(data["allocations"]):
        raise AdjustmentError("overlapping_living_requests")
    normalized["items"] = data["items"]
    data["request"] = normalized
    data["excluded"] = set(normalized["excluded"]) | NON_BUDGET_CODES
    if LIVING in data["excluded"]:
        # Excluding living as a source still permits the explicitly requested increase.
        data["excluded"].update(c for c in data["allocations"] if data['targets'].get(c, before[c]) <= before[c])
    return data


def _value(data, amounts, category):
    return sum(amounts[c] for c in data["allocations"]) if category == LIVING else amounts[category]


def _living_spent(data):
    return sum(data["spent"].get(c, 0) for c in data["allocations"]) + data["spent"].get("UNCLASSIFIED", 0)


def _ready(data, category):
    if category in BUCKETS:
        state = data["bucket_status"][category]
        if state == "unknown":
            raise _Blocked("needs_data", "bucket_unverified", category=category)
        if state == "disabled":
            raise _Blocked("infeasible", "bucket_disabled", category=category)


def _unit_amount(data, amount):
    return amount // data["fallback_unit"] * data["fallback_unit"]


def _unaligned(data, amounts):
    return [code for code, amount in amounts.items() if code != "SPARE" and code not in data["locks"]
            and amount % data["fallback_unit"]]


def _check_alignment(data):
    categories = _unaligned(data, data["before"])
    if categories:
        raise _Blocked("needs_confirmation", "unit_alignment_required", categories=categories)


def _check_spending(context):
    if context.get("spending_status", "complete") != "complete":
        raise _Blocked("needs_data", "spending_unverified")


def _capacity(data, amounts, donor, recipient, *, explicit=False, primary=False):
    if donor in data["excluded"] or donor == recipient:
        return 0
    _ready(data, donor)
    if donor in BUCKETS:
        spent_since = max(data["before"][donor] - amounts[donor], 0)
        capacity = max(min(amounts[donor] - data["bucket_floors"].get(donor, 0),
                           data["bucket_available"].get(donor, 0) - spent_since), 0)
        return _unit_amount(data, capacity)
    if donor in data["locks"]:
        return 0
    if explicit:
        return _unit_amount(data, max(amounts[donor] - _hard_floor(data, donor), 0))
    # A named primary decrease selects the donor, but does not waive its ML floor.
    if not primary and ((recipient in data["allocations"] and not _eligible(data, donor, recipient, allow_ranked=donor in data['request']['sources']))
                        or (recipient not in data["allocations"] and donor in data["priorities"])):
        return 0
    if amounts[donor] <= _hard_floor(data, donor):
        return 0
    try:
        floor = _floor(data, donor)
    except AdjustmentError:
        raise _Blocked("needs_data", "ml_reference_unavailable", category=donor) from None
    return _unit_amount(data, max(amounts[donor] - floor, 0))


def _selected_categories(data, sources):
    categories = []
    for c in sources:
        expanded = data["allocations"] if c == LIVING else [c] if c in data["allocations"] else []
        categories.extend(code for code in expanded if code not in categories)
    return categories


def _groups(data, sources, recipient, forbidden):
    candidates = []
    for c in sources:
        expanded = data["allocations"] if c == LIVING else [c]
        candidates.extend(code for code in expanded if code not in candidates)
    candidates = [c for c in candidates if c not in forbidden and c != recipient and c not in data["excluded"]]
    unranked = [c for c in candidates if c in BUCKETS or c not in data["priorities"]]
    groups = [unranked] if unranked else []
    ranks = sorted({data["priorities"][c] for c in candidates if c in data["priorities"]}, reverse=True)
    groups.extend([c for c in candidates if data["priorities"].get(c) == rank] for rank in ranks)
    return groups


def priority_destination(context, *, excluded=(), requested=()):
    candidates = [code for code in context['priorities'] if code in context['allocations']
                  and code not in requested and code not in context.get('locks', {}) and code not in excluded
                  and code not in NON_BUDGET_CODES]
    return min(candidates, key=lambda code: context['priorities'][code]) if candidates else None


def _single_destination(data, *, living=False):
    if not living and data['request']['destinations'] == [LIVING]:
        recipient = priority_destination(data, excluded=data['excluded'], requested=data['targets'])
        if recipient:
            return recipient
    choices = [c for c in data["request"]["destinations"] if c != LIVING and c not in data["excluded"]]
    if living:
        choices = [c for c in choices if c in data["allocations"]]
    if len(choices) != 1:
        raise _Blocked("needs_destination", "destination_required")
    return choices[0]


def _plan(data):
    before = data["before"]
    for code, amount in data["locks"].items():
        if amount != before[code]:
            raise _Blocked("infeasible", "lock_mismatch", category=code)
        if amount < data["minimums"].get(code, 0):
            raise _Blocked("infeasible", "lock_below_minimum", category=code)
    increases, decreases = {}, []
    for item in data["items"]:
        code = item["category"]
        current, target = _value(data, before, code), data["targets"][code]
        if code in data["excluded"]:
            raise _Blocked("infeasible", "excluded_requested_category", category=code)
        if code in data["locks"] and target != current:
            raise _Blocked("infeasible", "locked_category", category=code)
        if target == current:
            continue
        _ready(data, code)
        if target > current:
            recipient = _single_destination(data, living=True) if code == LIVING else code
            if recipient in data["excluded"]:
                raise _Blocked("infeasible", "excluded_destination", category=recipient)
            _ready(data, recipient)
            increases[recipient] = increases.get(recipient, 0) + target - current
        else:
            decreases.append({"category": code, "remaining": current - target,
                              "explicit": not item["automatic"] and code != LIVING})
    demand, supply = sum(increases.values()), sum(d["remaining"] for d in decreases)
    if supply > demand:
        recipient = _single_destination(data)
        if recipient == LIVING or any(d["category"] == recipient for d in decreases):
            raise _Blocked("needs_destination", "destination_required")
        if any(d["category"] == LIVING for d in decreases) and recipient in data["allocations"]:
            raise _Blocked("needs_destination", "living_decrease_requires_upper_destination")
        _ready(data, recipient)
        increases[recipient] = increases.get(recipient, 0) + supply - demand
    if increases and not decreases and not data["request"]["sources"]:
        raise _Blocked("needs_source", "source_required")
    if LIVING in data["targets"] and data["targets"][LIVING] > _value(data, before, LIVING):
        if any(c == LIVING or c in data["allocations"] for c in data["request"]["sources"]):
            raise _Blocked("needs_source", "living_increase_requires_upper_source")
    return increases, decreases


def _response(data, status, amounts=None, transfers=(), conflicts=()):
    before, amounts = data["before"], dict(data["before"] if amounts is None else amounts)
    shortfalls = []
    for c, target in data["targets"].items():
        current, actual = _value(data, before, c), _value(data, amounts, c)
        requested, applied = abs(target - current), abs(actual - current)
        if applied < requested:
            shortfalls.append({"category": c, "direction": "up" if target > current else "down",
                               "requested_amount": requested, "applied_amount": applied,
                               "missing_amount": requested - applied})
    return {"kind": "funding", "status": status, "requests": deepcopy(data["request"]),
            "allocations": {c: amounts[c] for c in data["allocations"]},
            "buckets": {c: amounts[c] for c in BUCKETS},
            "requested_targets": dict(data["targets"]),
            "persistence_candidates": {c: _value(data, amounts, c) for c in data["targets"] if c != "SPARE"},
            "changes": [{"category": c, "before": before[c], "after": value, "diff": value - before[c],
                         "direct": c in data["targets"]} for c, value in amounts.items() if value != before[c]],
            "transfers": deepcopy(list(transfers)), "shortfalls": shortfalls,
            "conflicts": deepcopy(list(conflicts))}


def _apply_group(data, amounts, recipient, required, sources, forbidden, policy, move):
    for group in _groups(data, sources, recipient, forbidden):
        wanted = _unit_amount(data, required)
        if not wanted:
            break
        capacities = {c: _capacity(data, amounts, c, recipient) for c in group}
        capacities = {c: value for c, value in capacities.items() if value}
        if not capacities:
            continue
        if len(capacities) == 1 and next(iter(capacities)) in BUCKETS:
            donor = next(iter(capacities))
            cuts = {donor: min(wanted, capacities[donor])}
        else:
            if policy is None:
                raise _Blocked("needs_policy", "allocation_policy_required")
            cuts = _cuts(policy, list(capacities), wanted, capacities)
        for donor, amount in cuts.items():
            if amount % data["fallback_unit"]:
                raise AdjustmentError("invalid_automatic_transfer_unit")
            move(donor, recipient, amount, False)
            required -= amount
        if required == 0:
            break
    return required


def simulate_funding(context, envelope, *, allocate_group_reduction=None):
    """Keep fixed costs and total funding constant while simulating named upper/category moves."""
    data = _data(context, envelope)
    try:
        _check_spending(context)
        _check_alignment(data)
        increases, decreases = _plan(data)
        if not increases:
            return _response(data, "unchanged")
        amounts, transfers = dict(data["before"]), []
        primary = set(data["targets"]) | set(increases)

        def move(donor, recipient, amount, direct):
            if amount:
                amounts[donor] -= amount
                amounts[recipient] += amount
                transfers.append({"from": donor, "to": recipient, "amount": amount, "direct": direct})

        for recipient, requested in increases.items():
            required = requested
            for donor in decreases:
                if not required or not donor["remaining"]:
                    continue
                c = donor["category"]
                if c == LIVING:
                    sources = data["request"]["sources"] or [LIVING]
                    sources = _selected_categories(data, sources)
                    available = max(_value(data, amounts, LIVING) - _living_spent(data), 0)
                    wanted = min(required, donor["remaining"], available)
                    if wanted:
                        left = _apply_group(data, amounts, recipient, wanted, sources, primary,
                                            allocate_group_reduction, move)
                        amount = wanted - left
                    else:
                        amount = 0
                else:
                    capacity = _capacity(data, amounts, c, recipient, explicit=donor["explicit"], primary=True)
                    amount = min(required, donor["remaining"], capacity)
                    amount = _unit_amount(data, amount)
                    if amount and c in data["allocations"] and not donor["explicit"]:
                        if allocate_group_reduction is None:
                            raise _Blocked("needs_policy", "allocation_policy_required")
                        amount = _cuts(allocate_group_reduction, [c], amount, {c: capacity}).get(c, 0)
                        if amount % data["fallback_unit"]:
                            raise AdjustmentError("invalid_automatic_transfer_unit")
                    move(c, recipient, amount, donor["explicit"])
                donor["remaining"] -= amount
                required -= amount
            if required and data["request"]["sources"]:
                _apply_group(data, amounts, recipient, required, data["request"]["sources"], primary,
                             allocate_group_reduction, move)
        for c, value in amounts.items():
            if value == data["before"][c]:
                continue
            _ready(data, c)
            floor = _hard_floor(data, c) if c in data["allocations"] else data["bucket_floors"].get(c, 0)
            if value < floor:
                raise _Blocked("infeasible", "protected_amount_not_reached", category=c)
        result = _response(data, "proposed", amounts, transfers)
        if not result["changes"]:
            return _response(data, "infeasible", conflicts=[{"code": "insufficient_funds"}])
        if result["shortfalls"]:
            result["status"] = "partial"
        return validate_funding(context, envelope, result)
    except _Blocked as error:
        return _response(data, error.status, conflicts=[error.conflict])


def validate_funding(context, envelope, result):
    """Verify ledger, source consent, floors, available amounts and total conservation."""
    data = _data(context, envelope)
    if not isinstance(result, dict) or result.get("kind") != "funding" or result.get("requests") != data["request"]:
        raise AdjustmentError("invalid_funding_result")
    allocations = _money_map(result.get("allocations"), "invalid_result_allocations", data["allocations"])
    buckets = _money_map(result.get("buckets"), "invalid_result_buckets", BUCKETS)
    if set(allocations) != set(data["allocations"]) or set(buckets) != set(BUCKETS):
        raise AdjustmentError("funding_result_keys_mismatch")
    amounts = {**allocations, **buckets}
    if sum(amounts.values()) + data["fixed_amount"] != data["funding_amount"]:
        raise AdjustmentError("funding_total_mismatch")
    transfers = result.get("transfers")
    if not isinstance(transfers, list):
        raise AdjustmentError("invalid_transfers")
    if result.get("status") in BLOCKED:
        if amounts != data["before"] or transfers or result.get("changes"):
            raise AdjustmentError("blocked_result_changed_budget")
        return result
    if result.get("status") not in ("proposed", "partial", "unchanged"):
        raise AdjustmentError("invalid_result_status")
    if _unaligned(data, amounts):
        raise AdjustmentError("invalid_result_budget_unit")
    try:
        _check_spending(context)
        _check_alignment(data)
        increases, decreases = _plan(data)
        direct = {d["category"] for d in decreases if d["explicit"]}
        named_down = {d["category"] for d in decreases}
        selected = set(data["request"]["sources"])
        if LIVING in named_down:
            selected.update(data["request"]["sources"] or [LIVING])
        allowed_sources = set(_selected_categories(data, selected)) | (selected & set(BUCKETS)) | (named_down - {LIVING})
        expected = dict(data["before"])
        used = {c: 0 for c in expected}
        for transfer in transfers:
            if not isinstance(transfer, dict) or set(transfer) != {"from", "to", "amount", "direct"}:
                raise AdjustmentError("invalid_transfer")
            donor, recipient = transfer["from"], transfer["to"]
            amount = _money(transfer["amount"], "invalid_transfer_amount")
            if (donor not in allowed_sources or recipient not in increases or donor == recipient or not amount
                    or type(transfer["direct"]) is not bool or transfer["direct"] != (donor in direct)
                    or donor in data["excluded"] or recipient in data["excluded"] or donor in increases):
                raise AdjustmentError("invalid_funding_transfer")
            if amount % data["fallback_unit"]:
                raise AdjustmentError("invalid_automatic_transfer_unit")
            capacity = _capacity(data, expected, donor, recipient, explicit=donor in direct,
                                 primary=donor in named_down)
            if amount > capacity:
                raise AdjustmentError("funding_capacity_violated")
            expected[donor] -= amount
            expected[recipient] += amount
            used[donor] += amount
        if expected != amounts:
            raise AdjustmentError("transfer_ledger_mismatch")
        for donor in decreases:
            actual = _value(data, data["before"], donor["category"]) - _value(data, amounts, donor["category"])
            if actual < 0 or actual > donor["remaining"]:
                raise AdjustmentError("requested_decrease_mismatch")
        for c, needed in increases.items():
            if not 0 <= amounts[c] - data["before"][c] <= needed:
                raise AdjustmentError("requested_increase_mismatch")
        for c, amount in amounts.items():
            if c in data["locks"] and amount != data["locks"][c]:
                raise AdjustmentError("lock_violated")
            if amount != data["before"][c]:
                _ready(data, c)
                floor = _hard_floor(data, c) if c in data["allocations"] else data["bucket_floors"].get(c, 0)
                if amount < floor:
                    raise AdjustmentError("protected_amount_violated")
        if LIVING in named_down and _value(data, amounts, LIVING) < _living_spent(data):
            raise AdjustmentError("living_spent_violated")
    except _Blocked:
        raise AdjustmentError("unverified_funding_result") from None
    checked = _response(data, result["status"], amounts, transfers)
    for key in ("requested_targets", "persistence_candidates", "changes", "shortfalls", "conflicts"):
        if result.get(key) != checked[key]:
            raise AdjustmentError("funding_result_details_mismatch")
    if ((result["status"] == "proposed" and checked["shortfalls"])
            or (result["status"] == "partial" and not checked["shortfalls"])
            or (result["status"] == "unchanged" and (amounts != data["before"] or checked["shortfalls"]))):
        raise AdjustmentError("result_outcome_mismatch")
    return result


def available_bucket_source(context, source, *, excluded=(), spare_minimum=None):
    """Source-only limit while the user has not chosen a living destination yet."""
    if source not in BUCKETS:
        raise AdjustmentError('unsupported_category')
    request = {'items': [], 'excluded': list(excluded)}
    if spare_minimum is not None:
        request['spare_minimum'] = spare_minimum
    data = _data(context, request)
    try:
        _check_spending(context)
        _check_alignment(data)
        maximum = _capacity(data, data['before'], source, LIVING)
        if source == 'SPARE' and spare_minimum is None and maximum:
            return {'status': 'needs_confirmation', 'maximum': None,
                    'conflicts': [{'code': 'spare_minimum_required'}]}
        return {'status': 'ready' if maximum else 'infeasible', 'maximum': maximum,
                'conflicts': [] if maximum else [{'code': 'insufficient_funds'}]}
    except _Blocked as error:
        return {'status': error.status, 'maximum': None, 'conflicts': [error.conflict]}


def available_adjustment(context, category, direction, *, sources=(), destinations=(), excluded=(),
                         allocate_group_reduction=None, spare_minimum=None):
    """Quote a verified maximum and an automatic by-amount envelope for confirmation."""
    if direction not in ("up", "down"):
        raise AdjustmentError("invalid_direction")
    envelope = {"items": [{"category": category, "amount_mode": "by", "amount": 0,
                            "direction": direction, "automatic": True}],
                "sources": list(sources), "destinations": list(destinations), "excluded": list(excluded)}
    if spare_minimum is not None:
        envelope['spare_minimum'] = spare_minimum
    data = _data(context, envelope)
    capacities = {}
    try:
        _check_spending(context)
        _check_alignment(data)
        if direction == "up":
            recipient = _single_destination(data, living=True) if category == LIVING else category
            _ready(data, recipient)
            if not sources:
                raise _Blocked("infeasible", "source_required")
            for source in sources:
                candidates = data["allocations"] if source == LIVING else [source]
                capacities[source] = sum(_capacity(data, data["before"], c, recipient)
                                         for c in candidates if c != recipient)
            maximum = sum(capacities.values())
        else:
            recipient = _single_destination(data)
            _ready(data, recipient)
            if category == LIVING:
                candidates = _selected_categories(data, list(sources) or [LIVING])
                capacities[LIVING] = _unit_amount(data, min(sum(_capacity(data, data["before"], c, recipient) for c in candidates),
                                         max(_value(data, data["before"], LIVING) - _living_spent(data), 0)))
            else:
                capacities[category] = _capacity(data, data["before"], category, recipient, primary=True)
            maximum = capacities.get(category, 0)
        if not maximum:
            return {"status": "infeasible", "maximum": 0, "capacities": capacities,
                    "envelope": None, "conflicts": [{"code": "insufficient_funds"}]}
        envelope["items"][0]["amount"] = maximum
        result = simulate_funding(context, envelope, allocate_group_reduction=allocate_group_reduction)
        if result["status"] not in ("proposed", "partial"):
            status = result["status"] if result["status"] in ("needs_policy", "needs_data", "needs_confirmation") else "infeasible"
            return {"status": status, "maximum": 0, "capacities": capacities,
                    "envelope": None, "conflicts": result["conflicts"]}
        current = _value(data, data["before"], category)
        actual = sum(result["allocations"].values()) if category == LIVING else (
            result["allocations"].get(category, result["buckets"].get(category)))
        maximum = abs(actual - current)
        envelope["items"][0]["amount"] = maximum
        verified = simulate_funding(context, envelope, allocate_group_reduction=allocate_group_reduction)
        if not maximum or verified["status"] != "proposed":
            return {"status": "infeasible", "maximum": 0, "capacities": capacities,
                    "envelope": None, "conflicts": [{"code": "maximum_not_verifiable"}]}
        return {"status": "ready", "maximum": maximum, "capacities": capacities,
                "envelope": deepcopy(verified["requests"]), "conflicts": []}
    except _Blocked as error:
        status = error.status if error.status in ("needs_policy", "needs_data", "needs_confirmation") else "infeasible"
        return {"status": status, "maximum": 0, "capacities": capacities,
                "envelope": None, "conflicts": [error.conflict]}
