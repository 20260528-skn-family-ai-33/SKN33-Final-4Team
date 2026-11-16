"""Replay refinements and diagnosis at preview and final approval."""
from copy import deepcopy
from .calculation import proportional_group_reduction
from .funding import simulate_funding, validate_funding
from .financial_planner import diagnose, reserve_after, recommendation_context
from .diagnostics import with_overrides

def simulate(context, request, *, allocator=proportional_group_reduction):
    base, calculation = deepcopy(request), deepcopy(context)
    analysis = base.pop("analysis", None)
    refinement = base.pop("refinement", None)
    if analysis is not None:
        if (not isinstance(analysis, dict) or set(analysis) != {"request", "overrides"}
                or not isinstance(analysis['request'], dict) or not isinstance(analysis["overrides"], dict)):
            raise ValueError("invalid_analysis_proof")
        calculation = recommendation_context(calculation, analysis['request'])
    if refinement is not None:
        if not {"primary", "donors"} <= set(refinement) or set(refinement) - {"primary", "donors", "bucket_caps"} or not isinstance(refinement["donors"], dict):
            raise ValueError("invalid_refinement")
        if (not isinstance(refinement["primary"], list) or not refinement["primary"]
                or any(c not in {*context['allocations'], *context['buckets']} for c in refinement["primary"]) or set(refinement["primary"]) & set(refinement["donors"])):
            raise ValueError("invalid_primary")
        for code, condition in refinement["donors"].items():
            if not isinstance(condition, dict) or set(condition) != {"target", "explicit"} or type(condition["explicit"]) is not bool:
                raise ValueError("invalid_donor_condition")
            target = condition["target"]
            before_amount = calculation["allocations"].get(code, calculation["buckets"].get(code))
            if before_amount is None or type(target) is not int or not 0 <= target <= before_amount:
                raise ValueError("invalid_donor_target")
            if code in calculation["allocations"]:
                calculation.setdefault("minimums", {})[code] = max(calculation.get("minimums", {}).get(code, 0), target)
            else:
                calculation.setdefault("bucket_available", {})[code] = min(calculation.get("bucket_available", {}).get(code, 0), before_amount - target)
        for source, cap in refinement.get("bucket_caps", {}).items():
            if source not in {"SPARE", "SAVINGS", "CUSHION_MONTHLY"} or type(cap) is not int or cap < 0:
                raise ValueError("invalid_bucket_cap")
            calculation.setdefault("bucket_available", {})[source] = min(calculation.get("bucket_available", {}).get(source, 0), cap)
    result = simulate_funding(calculation, base, allocate_group_reduction=allocator)
    validate_funding(calculation if analysis is not None else context, base, result)
    if refinement is not None:
        result["requests"]["refinement"] = deepcopy(refinement)
    if analysis is not None:
        if (not isinstance(analysis, dict) or set(analysis) != {"request", "overrides"}
                or not isinstance(analysis["overrides"], dict)):
            raise ValueError("invalid_analysis_proof")
        diagnosis = diagnose(with_overrides(context, analysis["overrides"]), analysis["request"])
        if diagnosis["status"] != "ready":
            raise ValueError("diagnosis_no_longer_verified")
        available = context.get('bucket_available', {}).get('SPARE', 0)
        after = available - (context["buckets"]["SPARE"] - result["buckets"]["SPARE"])
        gap = max(reserve_after(context, diagnosis, result["allocations"]) - after, 0)
        before_gap = max(diagnosis["reserve_before"] - available, 0)
        if gap > before_gap and not diagnosis.get("allow_reserve_risk"):
            raise ValueError("reserve_conflict")
        result["requests"]["analysis"] = deepcopy(analysis)
    return result

