"""추천 예산 입력 · 임시 카테고리 배분. 에이전트 연결 시 recommend_category_budgets만 교체한다."""
import hashlib
import json
from fractions import Fraction

from django.utils import timezone

from users.models import CategoryMinimum, CategoryPriority, FinancialProfile

from .models import BudgetCategorySetting
from .protect import fixed_base, fixed_breakdown, fixed_sum, savings_min
from .scope import OUT_OF_BUDGET


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
CATEGORY_CODES = tuple(FALLBACK_RATIOS)  # 기타 제외 소비 카테고리 전체 (design_candidates도 씀 — 의미 유지)
# 생활비 배분 대상 = 전체 − 예산 밖(scope.OUT_OF_BUDGET). 비율은 이 안에서 다시 나눈다
ALLOCATION_CODES = tuple(code for code in CATEGORY_CODES if code not in OUT_OF_BUDGET)
SUMMARY = "임시 비율로 나눈 테스트 추천이에요. 최소 금액과 자물쇠는 그대로 지켰어요."  # 실제 분석처럼 보이지 않게 (11/3)
ENGINE_VERSION = "temp-fallback-1"
POLICY_VERSION = "2026-11-09"  # 청구서 미발행 시 미반영 안내와 함께 예산 시작 허용. 이전 규칙 초안은 만료
INPUT_VERSION = 1
# 카드 선차감(card_reserved)은 카드로 쓸 때마다 바뀌므로 해시에 넣지 않는다. 초안을 만들 때 값을 input_snapshot에 저장해 두고
# 승인 때는 그 값을 쓴다. 대신 어떤 카드가 대상인지(card_ids)는 넣어서 카드를 고르거나 빼면 초안이 만료된다.
# profile_inputs: 추천 엔진(ML) 입력인 내 정보(생년월일 · 성별 · 자차 · 소비 우선순위). 바뀌면 추천 결과가 달라지므로 초안 만료 (리뷰 10/28)
HASH_FIELDS = ("cycle", "take_home_income", "fixed_base", "card_ids", "savings_min", "saved", "minimums", "profile_inputs", "category_targets")


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
        # 예산 밖 카테고리는 0원(= 배분 안 함)일 때만 허용 (design_candidates가 0원 자물쇠로 넘김)
        if any(values.get(code, 0) != 0 for code in OUT_OF_BUDGET):
            raise EngineCheckFailed("예산 밖 카테고리에는 최소 금액 · 자물쇠를 걸 수 없어요.")


def allocation_conflicts(inp):
    minimums, locks = inp["minimums"], inp["locks"]
    below = [{"type": "lock_below_minimum", "category": code, "locked_amount": locks[code], "min_amount": minimums[code]}
             for code in ALLOCATION_CODES if code in locks and locks[code] < minimums.get(code, 0)]
    if below:
        return below
    if all(code in locks for code in ALLOCATION_CODES) and sum(locks.values()) != inp["target"]:
        return [{"type": "locks_mismatch_living", "difference": sum(locks.values()) - inp["target"]}]
    shortfall = required_total(inp) - inp["target"]
    return [{"type": "minimums_over_living", "shortfall": shortfall}] if shortfall > 0 else []


def required_total(inp):
    return sum(inp["locks"].get(code, inp["minimums"].get(code, 0)) for code in ALLOCATION_CODES)


def recommend_category_budgets(inp):
    """R-05: 자물쇠 → 최소 금액 고정 반복 → 비율 몫만 내림 → 가장 큰 비율에 자투리."""
    check_inputs(inp)
    target, minimums = inp["target"], inp["minimums"]
    conflicts = allocation_conflicts(inp)
    if conflicts:
        return infeasible(conflicts)
    allocations = {code: amount for code, amount in inp["locks"].items() if code in ALLOCATION_CODES}
    free = [code for code in ALLOCATION_CODES if code not in allocations]
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
    """성공 결과에 unallocated가 있으면 카테고리 합계 + 남는 여윳돈 = target.

    키가 없는 기존 엔진은 합계 = target을 유지한다. 여윳돈은 bool이 아닌
    0 이상 정수이며 infeasible에는 허용하지 않는다. 나머지 보호 검수는 동일하다.
    """
    check_inputs(inp)
    if not isinstance(out, dict) or not isinstance(out.get("summary"), str) or not isinstance(out.get("reasons"), list):
        raise EngineCheckFailed("엔진 응답 모양이 맞지 않아요.")
    if out.get("status") == "infeasible":
        if "unallocated" in out:
            raise EngineCheckFailed("조건 충돌 결과에는 남는 여윳돈을 넣을 수 없어요.")
        expected = allocation_conflicts(inp)
        if not expected or out.get("conflicts") != expected or out.get("allocations") != {}:
            raise EngineCheckFailed("부족액이 입력 조건과 맞지 않아요.")
        return
    values = out.get("allocations")
    # 예산 밖 카테고리는 없거나 0원이면 통과 (ML이 전체 코드를 0원 포함해 내보내도 됨). 저장은 배분 대상만
    if (out.get("status") != "proposed" or not isinstance(values, dict)
            or not set(ALLOCATION_CODES) <= set(values) <= set(CATEGORY_CODES)):
        raise EngineCheckFailed("배분 대상 카테고리 금액이 필요해요.")
    if any(values.get(code, 0) != 0 for code in OUT_OF_BUDGET):
        raise EngineCheckFailed("예산 밖 카테고리에 금액을 배분했어요.")
    unallocated = out.get("unallocated", 0)
    if type(unallocated) is not int or unallocated < 0:
        raise EngineCheckFailed("남는 여윳돈은 0 이상의 정수여야 해요.")
    if (any(type(value) is not int or value < 0 for value in values.values())
            or sum(values.values()) + unallocated != inp["target"]):
        raise EngineCheckFailed("배분 합계 · 금액이 맞지 않아요.")
    if any(values.get(code, 0) < amount for code, amount in inp["minimums"].items()):
        raise EngineCheckFailed("최소 금액보다 작게 배분했어요.")
    if any(values.get(code, 0) != amount for code, amount in inp["locks"].items()):
        raise EngineCheckFailed("잠긴 금액을 바꿨어요.")
    if out.get("conflicts") != []:
        raise EngineCheckFailed("성공 결과에 충돌이 있어요.")


def hash_snapshot(snapshot):
    """승인 단계도 같은 필드 · 정렬 · 직렬화를 사용한다."""
    payload = {key: snapshot.get(key, {}) if key == 'category_targets' else snapshot[key] for key in HASH_FIELDS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


# ---------------- DB 조회 (호출 쪽에서 회원 잠금, 저장하지 않음) ----------------

def budget_settings(user, cycle):
    """→ (최소 금액, 자물쇠). 예전에 걸어 둔 예산 밖 카테고리 설정은 새 계산에서 뺀다 (DB 값은 지우지 않음)"""
    minimums = dict(CategoryMinimum.objects.filter(user=user).exclude(category_id__in=OUT_OF_BUDGET)
                    .values_list("category_id", "min_amount"))
    locks = dict(BudgetCategorySetting.objects.filter(user=user, cycle=cycle, locked=True)
                 .exclude(category_id__in=OUT_OF_BUDGET).values_list("category_id", "locked_amount"))
    return minimums, locks


def budget_input_snapshot(user, cycle, *, profile=None, today=None, card=True):
    profile = profile or FinancialProfile.objects.get(user=user)
    today = today or timezone.localdate()
    minimums, locks = budget_settings(user, cycle)
    fixed = fixed_breakdown(user, cycle, card=card)
    saved = {"living_budget": profile.living_budget, "saving_floor": profile.savings_floor, "cushion_monthly": profile.cushion_monthly}
    birth = profile.birth_date
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day)) if birth else None
    from .design_targets import targeted_etc
    category_targets = getattr(profile, 'budget_category_targets', {})
    etc, active_targets, target_conflicts = targeted_etc(category_targets, minimums, locks, profile.living_budget)
    # 예산 밖 카테고리는 순위에서 빼고 1부터 다시 매긴다 (예전에 고른 순위는 DB에 그대로)
    ranked = CategoryPriority.objects.filter(user=user).exclude(category_id__in=OUT_OF_BUDGET).order_by("rank")
    priorities = [{"category": code, "rank": rank} for rank, code in enumerate(ranked.values_list("category_id", flat=True), 1)]
    return {
        "cycle": {"id": str(cycle.pk), "starts_on": cycle.starts_on.isoformat(), "ends_before": cycle.ends_before.isoformat()},
        "take_home_income": profile.monthly_take_home_income,
        "fixed_total": fixed_sum(fixed),
        "fixed_base": fixed_base(fixed),
        "card_ids": fixed["card_ids"],
        "fixed": fixed,
        "savings_min": savings_min(user),
        "saved": saved, "minimums": minimums, "locks": locks, "etc": etc, 'category_targets': category_targets,
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
            'category_targets': active_targets, 'category_target_conflicts': target_conflicts,
        },
    }


def locks_block_recommendation(user, cycle, living_budget):
    """지금 생활비 · 최소 금액 · 자물쇠로 추천하면 자물쇠 때문에 막히는지 (run_recommend · allocation_conflicts와 같은 조건).

    자물쇠를 빼고 계산하면 막히지 않을 때만 True — 최소 금액만으로 모자란 경우는 자물쇠를 풀어도 소용없으므로 False.
    """
    if living_budget is None:
        return False
    minimums, locks = budget_settings(user, cycle)
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
