"""소비 유형 v2 (11/16 확정, local_workspace/reports/소비유형_판정방식_v2_20261116.md).

두 축으로 본다.
- 소비 취향 축 → 대표 유형 1개: 완료된 달의 본인 기록(반복 활동 · 생활비 중 비중)으로만 정한다.
  카테고리끼리는 실측 자료(조사 가중치 반영) 비중 분포의 백분위로 비교한다 — 식비가 늘 1등이 되지 않게.
- 관리 축 → 태그 최대 2개: 서비스 권장 저축률 · 승인 예산 이력 · 예산 속도.
모델 C는 대표 유형의 보조 근거 문장 한 줄에만 쓰고 판정을 바꾸지 않는다. 예산 배분 ML은 쓰지 않는다.
이름 · 한 줄 소개는 유형마다 고정이고, 근거(lines)는 반드시 사용자 데이터에서 나온 문장이다.
mascot = 화면의 마스코트 표정 코드 (frontend/src/components/Mascot.jsx 와 같은 이름).
"""
from datetime import date, timedelta
from statistics import median

from budgets.protect import RATES, round_amount

from .answer import NAMES, won
from .snapshot import josa

# 대표 유형 (취향 축). 여행 · 교통 · 의료 · 생활용품 · 교육은 성향 이름을 붙이지 않는다 (v2 2절)
TASTE_TYPES = {
    "FOOD": ("맛있는 게 제일 좋아형", "외식·카페 등 먹는 즐거움에 소비가 모여요.", "food"),
    "FASHION_SHOPPING": ("사고 싶은 게 많아형", "쇼핑과 취향을 위한 소비가 두드러져요.", "shopping"),
    "CULTURE_LEISURE": ("즐거움은 못 참아형", "문화·여가에 돈을 쓰며 일상의 즐거움을 챙겨요.", "fun"),
    "BEAUTY": ("나를 가꾸는 게 좋아형", "나를 위한 관리에 투자하는 편이에요.", "beauty"),
}
# 취향이 뚜렷하지 않을 때만 대표 유형이 되는 관리 유형
SAVER = ("차곡차곡 모으는형", "소비를 조절하면서 저축 계획도 잘 지켜요.", "saver")
PLANNER = ("계획 척척형", "정한 예산 안에서 꾸준히 소비를 관리해요.", "planner")
BASIC = ("아직 탐색 중이에요", "소비 취향을 정할 만큼 뚜렷한 특징은 아직 보이지 않아요.", "basic")
TAGS = {"saver": "차곡차곡 모아요", "planner": "계획대로 써요", "stretch": "이번만 조금 더"}

# ---- 문턱값 (전부 임시값, v2 7절. 합성 데이터로 유형 부여 비율 · 뒤집힘을 보고 조정한다) ----
MIN_MONTHS = 2            # 취향 축 최소 완료 달
MAX_MONTHS = 3            # 최근 완료 달 몇 개까지 볼지
SHARE_PCTL = 0.75         # 비중 백분위가 이 이상인 달이 과반이면 '많이 쓰는 편'
ONE_OFF_SHARE = 0.5       # 결제 1건이 그 달 그 항목의 이 비율 이상이고
ONE_OFF_SPIKE = 2.0       # 그 달 금액이 다른 달 중앙값의 이 배 이상이면 일회성으로 보고 그 달을 뺀다
MIN_BASE_SHARE = 0.01     # 월평균 금액이 배분 기준 금액의 이 비율 이상
MODEL_RATIO = 1.3         # 모델 C 보조 문장: 실제 ≥ 모델 예상 × 1.3 이고 차이 ≥ max(배분 기준 1%, 카테고리 MAE)
UNCLASSIFIED_MAX = 0.3    # 완료 달 미분류 비중이 이 이상이면 취향 판정 보류
AGE_RANGE = (20, 34)      # 실측 분포 · 모델 C 대상: 20~34세 1인 가구
# 취향 근거가 되는 반복 활동 → 카테고리 (장보기는 필요 지출이라 제외)
HABIT_KINDS = {"dining": "FOOD", "cafe": "FOOD", "delivery": "FOOD"}

# 실측 비중 분포 — 모델 C 평가용 실측 2,635건 (2025년 1,708 + 2026년 상반기 927, 20~34세 1인 가구 임금근로자),
# 조사 가중치(survey_weight) 반영. 분모는 우리 생활비와 같게 주거·통신 · 여행·숙박을 뺀 9개 항목 합계.
# 값은 0%, 5%, ..., 100% 백분위의 비중. 계산: local_workspace/models/living_expense_prediction/data/processed/household_budget.csv (evaluation 행)
SHARE_DIST_VERSION = "share_dist_v13c_eval2635_w"
SHARE_DIST = {
    "FOOD": (0.0572, 0.1925, 0.2448, 0.2802, 0.3075, 0.3295, 0.3498, 0.3709, 0.3889, 0.4034, 0.4225,
             0.4395, 0.4606, 0.4839, 0.5024, 0.5295, 0.5534, 0.5878, 0.6343, 0.6864, 0.9754),
    "FASHION_SHOPPING": (0.0, 0.0, 0.0026, 0.0089, 0.0162, 0.0227, 0.0286, 0.0349, 0.0433, 0.0518, 0.0593,
                         0.069, 0.0807, 0.0945, 0.107, 0.124, 0.1428, 0.1647, 0.1935, 0.2537, 0.7698),
    "CULTURE_LEISURE": (0.0, 0.0051, 0.0105, 0.0154, 0.019, 0.0229, 0.0268, 0.0301, 0.0346, 0.0392, 0.0442,
                        0.0513, 0.0577, 0.0646, 0.0729, 0.0858, 0.0999, 0.1155, 0.1453, 0.1975, 0.5143),
    "BEAUTY": (0.0, 0.0037, 0.0082, 0.0117, 0.0157, 0.0194, 0.0235, 0.0275, 0.0314, 0.0355, 0.0408,
               0.0458, 0.0513, 0.0578, 0.065, 0.0721, 0.0827, 0.0982, 0.1183, 0.1549, 0.4602),
}


def percentile(code, share):
    """실측 분포에서 이 비중이 몇 백분위인지 (0~1, 구간 안은 선형 보간)"""
    grid = SHARE_DIST[code]
    if share <= grid[0]:
        return 0.0
    for i in range(1, len(grid)):
        if share <= grid[i]:
            lo, hi = grid[i - 1], grid[i]
            within = (share - lo) / (hi - lo) if hi > lo else 1.0
            # 같은 값이 여러 칸이면(0% 비중이 많은 항목) 그 값의 가장 높은 백분위를 쓰지 않는다
            return ((i - 1) + within) / (len(grid) - 1)
    return 1.0


def _profile(kind, lines, action=None, **extra):
    title, desc, mascot = kind
    return {"title": title, "desc": desc, "mascot": mascot, "lines": lines, "action": action, **extra}


def _period(history):
    start = date.fromisoformat(history["period"]["from"])
    end = date.fromisoformat(history["period"]["to"]) - timedelta(days=1)
    return f"{start.month}월 {start.day}일~{end.month}월 {end.day}일"


def _pct(share):
    return f"{round(share * 100)}%"


def _ieyo(word):
    return word + ("이에요" if josa(word, "은는").endswith("은") else "예요")


def _population_ok(context):
    age = context.get("age")
    return context.get("housing") == "alone" and age is not None and AGE_RANGE[0] <= age <= AGE_RANGE[1]


def _usable(months, code):
    """일회성 큰 결제가 있던 달을 뺀다: 한 건이 그 달 그 항목의 절반 이상이고, 그 달 금액이 다른 달 중앙값의 2배 이상"""
    keep = []
    for i, m in enumerate(months):
        amount = m["categories"].get(code, 0)
        others = [x["categories"].get(code, 0) for j, x in enumerate(months) if j != i]
        spike = amount > 0 and others and amount >= ONE_OFF_SPIKE * max(median(others), 1)
        if spike and m.get("largest", {}).get(code, 0) >= amount * ONE_OFF_SHARE:
            continue
        keep.append(m)
    return keep


def _taste(summary, context):
    """→ (특성 기록 목록, 성립한 특성 [강도 큰 순], 보류 이유|None)"""
    st = summary.get("story") or {}
    months = (st.get("taste_months") or [])[-MAX_MONTHS:]
    period = {"months": [m["label"] for m in months]}
    if len(months) < MIN_MONTHS:
        return [{"code": c, "axis": "taste", "basis": "own_history", "status": "unavailable", "reason": "short_history", **period}
                for c in TASTE_TYPES], [], "short_history"
    spent = sum(m["total"] for m in months)
    unclassified = sum(m.get("unclassified", 0) for m in months)
    if unclassified and unclassified / (spent + unclassified) >= UNCLASSIFIED_MAX:
        return [{"code": c, "axis": "taste", "basis": "own_history", "status": "unavailable", "reason": "too_unclassified", **period}
                for c in TASTE_TYPES], [], "too_unclassified"
    population = _population_ok(context)
    base = context.get("base")
    traits, met = [], []
    for code in TASTE_TYPES:
        usable = _usable(months, code)
        record = {"code": code, "axis": "taste", "basis": "own_history",
                  "basis_version": SHARE_DIST_VERSION if population else None, "months": [m["label"] for m in usable]}
        if len(usable) < MIN_MONTHS:
            traits.append({**record, "status": "not_met", "reason": "one_off"})
            continue
        shares = [m["categories"].get(code, 0) / m["total"] if m["total"] > 0 else 0 for m in usable]
        average = sum(m["categories"].get(code, 0) for m in usable) / len(usable)
        share = sum(shares) / len(shares)
        # 달마다 ① 반복 활동 습관 또는 ② 비중 상위 25% — 같은 최근 달(최대 3개월) 안에서 과반이어야 한다
        habit_months = [any(HABIT_KINDS.get(kind) == code for kind in m.get("habit_kinds", ())) for m in usable]
        high_months = [population and percentile(code, s) >= SHARE_PCTL for s in shares]
        habit = sum(habit_months) * 2 > len(usable)
        high = sum(high_months) * 2 > len(usable)
        repeated = sum(h or g for h, g in zip(habit_months, high_months)) * 2 > len(usable)
        floor_ok = base is None or (base > 0 and average >= base * MIN_BASE_SHARE)
        strength = percentile(code, share) if population else share
        status = "met" if repeated and floor_ok and average > 0 else "not_met"
        row = {**record, "status": status, "strength": round(strength, 3), "share": round(share, 4),
               "average": round(average), "habit": habit, "high_share": bool(high)}
        if base is not None and base <= 0:
            row.update(status="not_met", reason="zero_base")
        traits.append(row)
        if row["status"] == "met":
            met.append(row)
    met.sort(key=lambda r: (-r["strength"], not r["habit"], -r["average"]))
    return traits, met, None


def _taste_lines(summary, trait):
    """근거 문장: 본인 기록만 (백분위 · 남과의 비교는 쓰지 않는다)"""
    code, name = trait["code"], NAMES[trait["code"]]
    lines = []
    # 습관 문장은 최근 달에서도 반복이 과반일 때만 쓴다 (story 습관은 최대 6개월을 본다)
    habit = trait["habit"] and next((f for f in (summary.get("story") or {}).get("findings", [])
                                     if f.get("id") == "HABIT" and f.get("category") == code), None)
    if habit:
        lines.append(habit["conclusion"])
    n = len(trait["months"])
    share_line = f"최근 완료된 {n}개월 생활비의 평균 {_pct(trait['share'])}가 {_ieyo(name)}."
    if not habit or trait["high_share"]:
        lines.append(share_line)
    return lines[:2]


def _model_line(trait, context):
    """모델 C 보조 문장 (있을 때만, 판정에는 쓰지 않는다)"""
    model = context.get("model") or {}
    code = trait["code"]
    if "amounts" not in model:
        return {"code": code, "axis": "taste", "basis": "model_c", "status": "unavailable",
                "reason": model.get("reason", "not_installed")}, None
    record = {"code": code, "axis": "taste", "basis": "model_c", "basis_version": model.get("version"),
              "months": [model.get("label")]}
    if not _population_ok(context):
        return {**record, "status": "unavailable", "reason": "out_of_range"}, None
    expected, actual = model["amounts"].get(code, 0), model.get("actual", {}).get(code, 0)
    base = context.get("base") or 0
    gap = actual - expected
    if expected > 0 and actual >= expected * MODEL_RATIO and gap >= max(base * MIN_BASE_SHARE, model["mae"].get(code, 0)):
        return {**record, "status": "met"}, (
            f"{model['label']} {josa(NAMES[code], '은는')} 비슷한 조건을 반영한 모델 예상(약 {won(expected)})보다 높은 편이에요.")
    return {**record, "status": "not_met"}, None


def _manage(summary, history, context):
    """관리 축 태그 → (태그 목록 [(code, 근거 문장들, 조언|None)], 특성 기록)"""
    story = summary.get("story") or {}
    findings = [*(summary.get("snapshot") or []), *(story.get("findings") or [])]
    ready = history.get("state") == "ready"
    tags, traits = [], []

    housing, base, floor = context.get("housing"), context.get("base"), context.get("savings_floor")
    if housing in RATES and base and base > 0 and floor is not None:
        recommended = round_amount(base * RATES[housing]["saving"], 100)  # 지킬 돈 화면 '추천 저축'과 같은 계산
        ok = floor >= recommended and context.get("saving_seen")
        traits.append({"code": "saver", "axis": "manage", "basis": "saving_policy", "status": "met" if ok else "not_met"})
        if ok:
            tags.append(("saver", [f"정해 둔 월 저축 {won(floor)}이 서비스 권장 저축액({won(recommended)}) 이상이에요.",
                                   "이번 급여 주기 정기 저축 이체도 확인됐어요."], None))
    else:
        traits.append({"code": "saver", "axis": "manage", "basis": "saving_policy", "status": "unavailable",
                       "reason": "zero_base" if base is not None and base <= 0 else "no_profile"})

    categories = history.get("categories", []) if ready else []
    repeated = [c for c in categories if c.get("topups", c.get("increases", 0)) >= 2]
    if repeated:
        row = max(repeated, key=lambda c: (c.get("topups", c["increases"]), c.get("increase_amount", 0)))
        name = NAMES[row["code"]]
        count = row.get("topups", row["increases"])
        tags.append(("stretch", [f"이번 급여 주기({_period(history)})에 {name} 예산을 {count}번 늘렸어요.",
                                 "같은 항목의 예산을 여러 번 늘리며 계획을 조절하는 모습이에요."],
                     f"다음 주에는 추가 증액 전에 남은 {name} 예산을 확인하고, 그 안에서 쓸 계획을 먼저 정해 보세요."))
    traits.append({"code": "stretch", "axis": "manage", "basis": "approved_budget_history",
                   "status": "met" if repeated else "not_met" if ready else "unavailable"})

    # 생활비를 예산 속도에 맞게 쓰는지 (budget_ok 에는 '조금 빠름'도 포함돼 문구로 한 번 더 거른다)
    pace = next((f for f in findings if f.get("kind") == "budget_ok" and "예산 속도에 맞게" in f.get("conclusion", "")), None)
    complete = summary.get("basis", {}).get("coverage") == "complete"
    plan = ready and history["change_count"] == 0 and pace and complete and not repeated
    traits.append({"code": "planner", "axis": "manage", "basis": "approved_budget_history",
                   "status": "met" if plan else "not_met" if ready else "unavailable"})
    if plan:
        tags.append(("planner", [pace["conclusion"], "이번 급여 주기에는 승인한 예산을 바꾸지 않고 쓰고 있어요."], None))
    return tags, traits


def _action(summary, code):
    finding = next((f for f in summary.get("snapshot", []) if f.get("category") == code and f.get("actions")), None)
    return finding["actions"][0] if finding else None


def assess(summary, history, context=None):
    """→ {'profile': 카드|None, 'traits': [...], 'reason': 취향 보류 이유|None}. 입력은 바꾸지 않는다"""
    context = context or {}
    if context.get("scoped"):
        return {"profile": None, "traits": [], "reason": "scoped"}
    taste_traits, met, reason = _taste(summary, context)
    tags, manage_traits = _manage(summary, history, context)
    traits = [*taste_traits, *manage_traits]
    if met:
        top = met[0]
        model_trait, model_line = _model_line(top, context)
        traits.append(model_trait)
        lines = [*_taste_lines(summary, top), *([model_line] if model_line else [])]  # 대표 유형 근거 최대 3줄
        profile = _profile(TASTE_TYPES[top["code"]], lines, _action(summary, top["code"]))
        chosen = tags
    else:
        lead = next((t for t in tags if t[0] in ("saver", "planner")), None)
        if lead:
            profile = _profile(SAVER if lead[0] == "saver" else PLANNER, list(lead[1]), lead[2])
            chosen = [t for t in tags if t is not lead]
        elif tags:  # 예산 관리 태그만 있음 — 대표 유형은 기본, 구체적인 이유를 함께
            why = {"short_history": "완료된 달이 2개월 이상 쌓이면 소비 취향 유형을 정해요.",
                   "too_unclassified": "분류되지 않은 결제가 많아 소비 취향 유형은 아직 정하지 않았어요."}.get(
                reason, "최근 완료된 달에는 한 항목에 두드러지게 몰린 소비가 보이지 않았어요.")
            profile = _profile(BASIC, [why])
            chosen = tags
        else:
            return {"profile": None, "traits": traits, "reason": reason}
    profile["tags"] = [{"code": code, "label": TAGS[code]} for code, _, _ in chosen[:2]]
    # 태그 근거는 대표 유형 근거(최대 3줄: 본인 기록 2 + 모델 C 1) 뒤에 한 줄씩, 조언은 대표 유형에 없을 때만
    for code, lines, action in chosen[:2]:
        profile["lines"].append(lines[0])
        if action and not profile.get("action"):
            profile["action"] = action
    profile["lines"] = list(dict.fromkeys(profile["lines"]))[:5]
    profile["traits"] = traits
    return {"profile": profile, "traits": traits, "reason": reason}


def describe(summary, history, context=None):
    """기존 분석 내용은 그대로 두고, 앞에 붙일 유형 · 근거 · 태그 · 행동 하나만 반환한다. 근거가 없으면 None"""
    return assess(summary, history, context)["profile"]


def _amount(sign, value):
    return f"({sign}{won(value)})" if value else ""


def habit_lines(history, profile):
    """'예산을 관리하는 습관' 칸. 횟수만 늘어놓지 않고 바뀐 것만 · 금액과 함께, 마지막에 짧은 조언 하나"""
    if history.get("state") != "ready":
        return []
    label = _period(history)
    if not history["change_count"]:
        return [f"이번 급여 주기({label})에는 승인한 예산을 한 번도 바꾸지 않았어요."]
    lines = [f"이번 급여 주기({label})에 예산을 {history['change_count']}번 바꿨어요."]
    for row in history.get("categories", []):
        parts = [f"{row['increases']}번 늘림{_amount('+', row.get('increase_amount', 0))}" if row.get("increases") else None,
                 f"{row['decreases']}번 줄임{_amount('-', row.get('decrease_amount', 0))}" if row.get("decreases") else None]
        lines.append(f"{row['name']}: {', '.join(p for p in parts if p)}")
    savings = history.get("savings") or {}
    if savings.get("decreases"):
        lines.append(f"저축 계획을 {savings['decreases']}번 줄였어요{_amount('-', savings.get('decrease_amount', 0))}. 실제 저축액이 아닌 예산 기준이에요.")
    elif savings.get("increases"):
        lines.append(f"저축 계획을 {savings['increases']}번 늘렸어요{_amount('+', savings.get('increase_amount', 0))}. 실제 저축액이 아닌 예산 기준이에요.")
    raised = any(row.get("increases") for row in history.get("categories", []))
    if raised and not (profile and profile.get("action")):
        lines.append("늘린 예산은 이번 주기 동안 지켜 보면 다음 달 계획이 더 정확해져요.")
    return lines
