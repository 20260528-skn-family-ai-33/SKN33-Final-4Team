"""추천 예산 입력 · 임시 카테고리 배분. 에이전트 연결 시 recommend_category_budgets만 교체한다."""
import hashlib
import json
from fractions import Fraction

from django.utils import timezone

from users.models import CategoryMinimum, CategoryPriority, FinancialProfile

from .models import BudgetCategorySetting
from .protect import fixed_base, fixed_breakdown, fixed_sum, savings_min


# TODO ML 담당 fallback_ratios 로 교체
# 화면 시안(이전 RecommendPage.jsx 예시 금액)의 기타 제외 10개 금액 / 합계 1,060,000원.
FALLBACK_RATIOS = {
    code: Fraction(amount, 1_060_000) for code, amount in (
        ("FOOD", 330_000), ("HOUSING_TELECOM", 110_000), ("TRANSPORT_CAR", 90_000),
        ("HOUSEHOLD", 60_000), ("FASHION_SHOPPING", 80_000), ("MEDICAL_HEALTH", 50_000),
        ("BEAUTY", 60_000), ("EDUCATION", 50_000), ("CULTURE_LEISURE", 170_000),
        ("TRAVEL_LODGING", 60_000),
    )
}
CATEGORY_CODES = tuple(FALLBACK_RATIOS)
SUMMARY = "비슷한 또래 1인 가구의 평균 소비 비율로 나눴어요. 최소 금액과 자물쇠 건 카테고리는 그대로 지켰어요."
ENGINE_VERSION = "temp-fallback-1"
POLICY_VERSION = "2026-10-27"
INPUT_VERSION = 1
# 카드 선차감(card_reserved)은 카드로 쓸 때마다 바뀌므로 해시에 넣지 않는다. 초안을 만들 때 값을 input_snapshot에 저장해 두고
# 승인 때는 그 값을 쓴다. 대신 어떤 카드가 대상인지(card_ids)는 넣어서 카드를 고르거나 빼면 초안이 만료된다.
# profile_inputs: 추천 엔진(ML) 입력인 내 정보(생년월일 · 성별 · 자차 · 소비 우선순위). 바뀌면 추천 결과가 달라지므로 초안 만료 (리뷰 10/28)
HASH_FIELDS = ("cycle", "take_home_income", "fixed_base", "card_ids", "savings_min", "saved", "minimums", "profile_inputs")


# ---------------- 순수 계산 · 엔진 결과 검사 (DB를 읽지 않음) ----------------

class EngineCheckFailed(ValueError):
    """입력 조건이나 엔진 결과가 합계 · 최소 금액 · 자물쇠를 함께 지키지 못함."""


def infeasible(conflicts):
    return {
        "status": "infeasible", "allocations": {}, "reasons": [],
        "summary": "카테고리 최소 금액과 자물쇠 금액을 확인해 주세요.",
        "conflicts": conflicts,
    }


def check_inputs(inp):
    target, minimums, locks = inp["target"], inp["minimums"], inp["locks"]
    if type(target) is not int:
        raise EngineCheckFailed("배분 금액은 정수여야 해요.")
    for values in (minimums, locks):
        if not isinstance(values, dict) or set(values) - set(CATEGORY_CODES):
            raise EngineCheckFailed("카테고리 코드가 맞지 않아요.")
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise EngineCheckFailed("최소 금액 · 잠긴 금액은 0 이상의 정수여야 해요.")


def allocation_conflicts(inp):
    minimums, locks = inp["minimums"], inp["locks"]
    below = [{"type": "lock_below_minimum", "category": code, "locked_amount": locks[code], "min_amount": minimums[code]}
             for code in CATEGORY_CODES if code in locks and locks[code] < minimums.get(code, 0)]
    if below:
        return below
    if len(locks) == len(CATEGORY_CODES) and sum(locks.values()) != inp["target"]:
        return [{"type": "locks_mismatch_living", "difference": sum(locks.values()) - inp["target"]}]
    shortfall = required_total(inp) - inp["target"]
    return [{"type": "minimums_over_living", "shortfall": shortfall}] if shortfall > 0 else []


def required_total(inp):
    return sum(inp["locks"].get(code, inp["minimums"].get(code, 0)) for code in CATEGORY_CODES)


def recommend_category_budgets(inp):
    """R-05: 자물쇠 → 최소 금액 고정 반복 → 비율 몫만 내림 → 가장 큰 비율에 자투리."""
    check_inputs(inp)
    target, minimums = inp["target"], inp["minimums"]
    conflicts = allocation_conflicts(inp)
    if conflicts:
        return infeasible(conflicts)
    allocations = dict(inp["locks"])
    free = [code for code in CATEGORY_CODES if code not in allocations]
    while free:
        remaining = target - sum(allocations.values())
        weight = sum(FALLBACK_RATIOS[code] for code in free)
        shares = {code: remaining * FALLBACK_RATIOS[code] / weight for code in free}
        at_minimum = [code for code in free if shares[code] < minimums.get(code, 0)]
        if not at_minimum:
            # 자투리를 받는 곳은 최종 금액이 원래 비율 몫 이상이므로 최소 금액도 지킨다.
            receiver = max(free, key=FALLBACK_RATIOS.get)
            rounded = {code: int(shares[code] // 10_000) * 10_000 for code in free}
            # 비율 몫은 최소 이상이어도 내림하면 15.5만 → 15만이 될 수 있다.
            # 그런 곳도 최소로 고정한 뒤 다시 나눠, 고정 금액을 내림하지 않는다.
            at_minimum = [code for code in free if code != receiver and rounded[code] < minimums.get(code, 0)]
            if not at_minimum:
                allocations.update(rounded)
                allocations[receiver] += target - sum(allocations.values())
                break
        for code in at_minimum:
            allocations[code] = minimums[code]
            free.remove(code)
    if sum(allocations.values()) != target:
        raise EngineCheckFailed("모든 카테고리가 잠겨 있어 남은 금액을 배분할 수 없어요.")
    return {"status": "proposed", "allocations": allocations, "reasons": [], "summary": SUMMARY, "conflicts": []}


def check_result(inp, out):
    """에이전트를 바꿔도 유지할 서버 검증. 잘못된 결과는 예산에 저장하지 않는다."""
    check_inputs(inp)
    if not isinstance(out, dict) or not isinstance(out.get("summary"), str) or not isinstance(out.get("reasons"), list):
        raise EngineCheckFailed("엔진 응답 모양이 맞지 않아요.")
    if out.get("status") == "infeasible":
        expected = allocation_conflicts(inp)
        if not expected or out.get("conflicts") != expected or out.get("allocations") != {}:
            raise EngineCheckFailed("부족액이 입력 조건과 맞지 않아요.")
        return
    values = out.get("allocations")
    if out.get("status") != "proposed" or not isinstance(values, dict) or set(values) != set(CATEGORY_CODES):
        raise EngineCheckFailed("10개 카테고리 배분이 필요해요.")
    if any(type(value) is not int or value < 0 for value in values.values()) or sum(values.values()) != inp["target"]:
        raise EngineCheckFailed("배분 합계 · 금액이 맞지 않아요.")
    if any(values[code] < amount for code, amount in inp["minimums"].items()):
        raise EngineCheckFailed("최소 금액보다 작게 배분했어요.")
    if any(values[code] != amount for code, amount in inp["locks"].items()):
        raise EngineCheckFailed("잠긴 금액을 바꿨어요.")
    if out.get("conflicts") != []:
        raise EngineCheckFailed("성공 결과에 충돌이 있어요.")


def hash_snapshot(snapshot):
    """승인 단계도 같은 필드 · 정렬 · 직렬화를 사용한다."""
    payload = {key: snapshot[key] for key in HASH_FIELDS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


# ---------------- DB 조회 (호출 쪽에서 회원 잠금, 저장하지 않음) ----------------

def budget_input_snapshot(user, cycle, *, profile=None, today=None, card=True):
    profile = profile or FinancialProfile.objects.get(user=user)
    today = today or timezone.localdate()
    minimums = dict(CategoryMinimum.objects.filter(user=user).values_list("category_id", "min_amount"))
    locks = dict(BudgetCategorySetting.objects.filter(user=user, cycle=cycle, locked=True).values_list("category_id", "locked_amount"))
    fixed = fixed_breakdown(user, cycle, card=card)
    saved = {"living_budget": profile.living_budget, "saving_floor": profile.savings_floor, "cushion_monthly": profile.cushion_monthly}
    birth = profile.birth_date
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day)) if birth else None
    etc = locks.get("ETC", minimums.get("ETC", 0))
    priorities = [{"category": code, "rank": rank} for code, rank in
                  CategoryPriority.objects.filter(user=user).order_by("rank").values_list("category_id", "rank")]
    return {
        "cycle": {"id": str(cycle.pk), "starts_on": cycle.starts_on.isoformat(), "ends_before": cycle.ends_before.isoformat()},
        "take_home_income": profile.monthly_take_home_income,
        "fixed_total": fixed_sum(fixed),
        "fixed_base": fixed_base(fixed),
        "card_ids": fixed["card_ids"],
        "fixed": fixed,
        "savings_min": savings_min(user),
        "saved": saved, "minimums": minimums, "locks": locks, "etc": etc,
        "profile_inputs": {"birth_date": birth.isoformat() if birth else None, "sex": profile.gender,
                           "has_vehicle": profile.has_car, "priorities": priorities},
        "engine_input": {
            "age": age, "sex": profile.gender, "has_vehicle": profile.has_car,
            "take_home_income": profile.monthly_take_home_income,
            "housing_fixed": fixed["categories"].get("HOUSING_TELECOM", 0),
            "target": profile.living_budget - etc if profile.living_budget is not None and etc is not None else None,
            "priorities": priorities,
            "minimums": {code: amount for code, amount in minimums.items() if code != "ETC"},
            "locks": {code: amount for code, amount in locks.items() if code != "ETC"},
            "input_version": INPUT_VERSION,
        },
    }


def locks_block_recommendation(user, cycle, living_budget):
    """지금 생활비 · 최소 금액 · 자물쇠로 추천하면 자물쇠 때문에 막히는지 (run_recommend · allocation_conflicts와 같은 조건).

    자물쇠를 빼고 계산하면 막히지 않을 때만 True — 최소 금액만으로 모자란 경우는 자물쇠를 풀어도 소용없으므로 False.
    """
    if living_budget is None:
        return False
    minimums = dict(CategoryMinimum.objects.filter(user=user).values_list("category_id", "min_amount"))
    locks = dict(BudgetCategorySetting.objects.filter(user=user, cycle=cycle, locked=True).values_list("category_id", "locked_amount"))
    if not locks:
        return False

    def blocked(locks):
        etc = locks.get("ETC", minimums.get("ETC", 0))
        if etc < minimums.get("ETC", 0) or etc > living_budget:
            return True
        return bool(allocation_conflicts({
            "target": living_budget - etc,
            "minimums": {code: amount for code, amount in minimums.items() if code != "ETC"},
            "locks": {code: amount for code, amount in locks.items() if code != "ETC"},
        }))

    return blocked(locks) and not blocked({})


def budget_input_hash(user, cycle):
    """승인 때 비교용. 카드 선차감은 해시에 없으므로 계산하지 않는다"""
    return hash_snapshot(budget_input_snapshot(user, cycle, card=False))
