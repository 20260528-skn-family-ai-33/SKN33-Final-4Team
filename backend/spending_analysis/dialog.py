"""소비 분석 에이전트 — 대화 계층 (원문 → 자체 해석 → 소비 결정 · 되묻기 · 직전 조건 이어받기 · 설명 근거 · 분석 기록)

DB · 계산 없음 (계산은 analyze, 실행은 agent.py). chat 의 라우터 모듈을 import 하지 않는다.
단계 A 에서 router_core 의 소비 규칙을 그대로 옮겼고, 단계 B 에서 해석도 소비 전용(reading.py)으로 직접 한다.

소비 분석 유형 (1~9):
1 이번 달 총소비 · 2 이번 달 카테고리 소비 · 3 카테고리 남은 예산 · 4 전체 생활비 남은 예산 · 5 이번 달 전반 분석
6 지난달과 전반 비교 · 7 지난달과 카테고리 비교 · 8 최근 N개월 전반 추이 · 9 최근 N개월 카테고리 추이

이 모듈만 쓰는 상태 (공유 저장소의 소비 몫):
- store.get_agent_state(cid, "spending") = {"pending": 소비 되묻기, "aliases": {원문 말: 코드}, "repick": 다시 고를 말}
  되묻기는 다른 기능으로 바뀌어도 지우지 않고 남는다. 별칭은 이 대화에서 사용자가 카테고리 질문에 직접 고른 대응만
- store.get_analysis / save_analysis = 직전 조회 {request, items[ok, evidence, limits, period, as_of, facts?]}
- store.save_shared(cid, "spending", summary(...)) = 라우터 · 다른 에이전트에 공개하는 소비 요약 (이 모듈만 쓴다)
"""
from dataclasses import asdict, dataclass, replace

from . import reading
from .reading import CATEGORY_NAMES, ReadFailed, SpendingReading, find_category


@dataclass(frozen=True)
class Decision:
    """소비 입력 하나의 결정 (소비 에이전트 내부 형식)"""
    # ready(분석 가능) · ask(되묻기) · unsupported(자기 분야 미지원) · failed(해석 실패) · cancelled(취소 · 거절) · not_mine(다른 분야)
    # · noted(조회 없이 이 대화의 조회 기준만 정함 — 별칭)
    status: str
    intent: str | None = None
    items: tuple = ()        # [{type, category, months_requested, months_applied}] 말한 순서대로
    notices: tuple = ()      # months_capped · no_budget_change_record · budget_change_not_saved
    asking: str | None = None     # months · category · scope · budget_current · explain_target · detail
    ask_about: tuple = ()
    options: tuple = ()
    reason: str | None = None
    skipped: tuple = ()      # 남은 예산 조회에서 뺀 카테고리 [{category, reason}]
    aliases: tuple = ()      # 이번에 쓴 대화 내 별칭 [{word, code, new}] (new = 이번에 기억함)
    budget_basis: dict | None = None  # "바뀐 예산으로": {refetch_current_budget: True, last_change: 라우터가 넘긴 결과 참조}
    explain: dict | None = None   # 설명 요청: {question, items: 참조할 확정 근거}
    request: object = None   # 이어받기 · 후속 답을 합친 뒤 실제로 규칙에 쓴 요청
    interpretation: object = None  # 이번 문장의 소비 해석 (원래 말한 개월 수 등)
    agent: str | None = None

AGENT = "spending"
MONTHS_DEFAULT = MONTHS_MAX = 3
# 예산 줄이 없는 카테고리 (budgets.md R-08: 예산 = 10개 + 기타). 소비 조회는 되고 남은 예산 조회는 미지원 (10/29 확정)
NO_BUDGET = ("UNCLASSIFIED",)


# ---------- 상태 ----------

def _state(store, conversation_id):
    return store.get_agent_state(conversation_id, AGENT) or {}


def _save(store, conversation_id, **fields):
    """소비 칸의 일부만 바꾼다 (되묻기를 지워도 별칭은 남는다)"""
    state = {k: v for k, v in {**_state(store, conversation_id), **fields}.items() if v}
    store.save_agent_state(conversation_id, AGENT, state or None)


def pending(store, conversation_id):
    return _state(store, conversation_id).get("pending")


def _save_pending(store, conversation_id, value):
    _save(store, conversation_id, pending=value)


def _word(text):
    return " ".join(text.split())


def _unknown(c):
    return isinstance(c, dict) and "unknown" in c


def _detail(c):
    """카테고리 안의 세부 지출 (커피 등) — accepted 는 사용자가 대체 범위(카테고리 전체)에 동의함"""
    return isinstance(c, dict) and "detail" in c


def label(c):
    """카테고리 자리의 화면 이름: 코드 → 이름, 모르는 말 · 세부 지출 → 원문"""
    return c["unknown"] if _unknown(c) else c["detail"] if _detail(c) else CATEGORY_NAMES[c]


# 별칭을 쓴 답변에 붙는 선택지 이름 — 사용자가 이 이름을 그대로 보내면 그 말의 카테고리를 다시 묻는다
REPICK = "'{word}' 카테고리 다시 고르기"


def _with_aliases(request, aliases):
    """모르는 말 중 이 대화에서 사용자가 고른 별칭이 있는 것만 코드로 바꾼다 (같은 코드는 처음 자리만) → (요청, 쓴 별칭)"""
    used, out = [], []
    for c in request.categories:
        if _unknown(c) and aliases.get(_word(c["unknown"])):
            used.append({"word": _word(c["unknown"]), "code": aliases[_word(c["unknown"])], "new": False})
            c = used[-1]["code"]
        if isinstance(c, dict) or c not in out:
            out.append(c)
    return (replace(request, categories=tuple(out)) if used else request), tuple(used)


def cancel_question(store, conversation_id):
    """라우터가 '질문 취소'로 정했을 때(mode=cancel) 소비 되묻기만 지운다 (직전 분석은 그대로)"""
    _save_pending(store, conversation_id, None)


def summary(store, conversation_id):
    """라우터 · 다른 에이전트에 공개하는 소비 요약: 되묻기 중인지, 직전 조회 조건, 항목별 근거 상태.
    확정 수치(facts)는 evidence=confirmed 항목만 넣는다 (부분 자료는 조건 · 제한 코드만). 거래 원문은 넣지 않는다"""
    last = store.get_analysis(conversation_id)
    keep = ("type", "category", "instead_of", "include_current", "ok", "evidence", "limits", "period", "compare_period",
            "as_of")  # instead_of · include_current = 실제 조회 범위가 말한 것과 다를 때 · 이번 달 포함
    items = [{**{k: i[k] for k in keep if k in i}, **({"facts": i["facts"]} if i.get("evidence") == "confirmed" and "facts" in i
                                                     else {})} for i in (last or {}).get("items", [])]
    return {"has_question": pending(store, conversation_id) is not None,
            "previous": last and last["request"], "items": items}


def publish(store, conversation_id):
    store.save_shared(conversation_id, AGENT, summary(store, conversation_id))


# ---------- 진입 ----------

def start_button(store, conversation_id):
    """[소비 분석] 버튼: 조건을 정하지 않고 무엇이 궁금한지 묻는다. 상태 변경 없음 (보류된 되묻기도 그대로)"""
    return Decision("ask", AGENT, asking="detail", reason="start", agent=AGENT)


def _reask(asked):
    return Decision("ask", asked["intent"], asking=asked["asking"], ask_about=tuple(asked["ask_about"]),
                    options=tuple(asked["options"]), agent=AGENT)


def step(store, conversation_id, text, complete, *, mode="continue", chosen=False, budget_change=None):
    """소비 입력(원문) 하나 → Decision. 해석은 소비 전용 프롬프트로 직접 한다 (complete 주입)
    - mode=continue: 소비가 담당일 때 입력을 먼저 받는다. 되묻기 답 · 새 소비 요청 · 다른 분야(not_mine) 를 가린다
    - mode=start: 라우터가 소비로 보낸 새 요청. 보류된 되묻기 없이 해석한다
      [임시 · 미정] 새 소비 요청이 보류된 되묻기를 대신한다 (기존 '새 요청이면 질문을 바꾼다' 규칙을 그대로 씀)
    - chosen: 사용자가 라우터 질문에서 소비 분석을 골랐다 (복합 문장 중 소비 부분만 해석)
    - not_mine · 해석 실패(failed)면 아무 상태도 바꾸지 않는다"""
    last, state = store.get_analysis(conversation_id), _state(store, conversation_id)
    asked = pending(store, conversation_id) if mode == "continue" else None
    repick = state.get("repick")
    if not asked and repick and _word(text) == REPICK.format(word=repick["word"]):
        return _ask_repick(store, conversation_id, repick)
    picked = asked and asked["asking"] in ("category", "explain_target", "fallback_category") and find_category(text)
    option = asked and asked["asking"] in LABELED and next(
        (o for o in asked["options"] if o["label"] == _word(text)), None)
    try:
        # 목록에서 고른 코드 · 이름 · 선택지 이름이면 LLM 없이 답으로 본다 (조회 조건만 정함, 거래 카테고리는 안 바꿈)
        if picked:
            interp = SpendingReading(mine=True, followup="answer", categories=(picked,))
        elif option:
            interp = reading.parse({"mine": True, "followup": "answer", **option["set"]})
        else:
            interp = reading.read(text, asked, complete, last and last["request"], chosen)
    except ReadFailed as error:
        return Decision("failed", reason=str(error), agent=AGENT)
    if not interp.mine:
        # 다른 분야 · 복합 의도 · 범위 밖 · 불분명 → 라우터가 다시 분류 (목적지는 정하지 않는다)
        return Decision("not_mine", reason="other_domain", interpretation=interp, agent=AGENT)
    request, source, fresh = interp, text, ()
    if interp.alias:
        # "데이트비는 식비로 볼게": 이 대화의 조회 기준을 정하는(바꾸는) 말 — 사용자가 명시했으므로 바로 기억한다
        word = _word(interp.alias["word"])
        fresh = ({"word": word, "code": interp.alias["category"], "new": True},)
        _save(store, conversation_id, aliases={**state.get("aliases", {}), word: interp.alias["category"]}, repick=None)
        state = _state(store, conversation_id)
        if not _asks_anything(interp):  # 조회 요청 없이 기준만 정함 → 보류된 질문은 그대로 두고 적용 범위만 알린다
            return Decision("noted", reason="alias_set", aliases=fresh, interpretation=interp, agent=AGENT)
    if asked:
        if interp.followup == "cancel":
            _save_pending(store, conversation_id, None)
            return Decision("cancelled", reason="cancel", agent=AGENT)
        if interp.followup == "answer" and asked["asking"] in ("budget_current", "fallback_scope") and interp.confirm is False:
            _save_pending(store, conversation_id, None)
            return Decision("cancelled", reason="declined", agent=AGENT)
        if interp.followup == "answer":
            request = merge(reading.parse(asked["request"]), interp, asked["asking"], tuple(asked["options"]))
            if request is None:
                return _reask(asked)
            source = asked["text"]
            if picked and asked["asking"] == "category":
                # 사용자가 카테고리 질문에 직접 고른 대응만 이 대화의 별칭으로 기억한다 (다시 고르면 바뀐다)
                word = _word(next(c["unknown"] for c in reading.parse(asked["request"]).categories if _unknown(c)))
                fresh += ({"word": word, "code": picked, "new": True},)
                _save(store, conversation_id, aliases={**state.get("aliases", {}), word: picked})
                state = _state(store, conversation_id)
        elif interp.followup != "new":
            # unclear · 빈 값: 임의로 고르지 않고 같은 질문을 다시 한다
            return _reask(asked)
    asked_request = request
    request, used = _with_aliases(request, state.get("aliases", {}))
    used = fresh + used
    decision = decide(request, source, last)
    request = decision.request or request  # 직전 분석을 이어받았으면 그 요청
    if request.after_budget_change and decision.status != "failed":
        # 최신 적용 예산은 소비 분석 쪽이 다시 조회한다. 과거 결과의 예산 참조를 현재 예산으로 고정하지 않는다
        # budget_change = 라우터가 넘긴 예산 작업 결과 참조(서버 확인값) — 읽기만
        notice = ("no_budget_change_record",) if budget_change is None else \
            ("budget_change_not_saved",) if budget_change["outcome"] != "saved" else ()
        decision = replace(decision, notices=decision.notices + notice,
                           budget_basis={"refetch_current_budget": True, "last_change": budget_change})
    reused = [u for u in used if not u["new"]]
    # 기억한 대응을 쓴 요청은 원래 말이 남은 요청을 보관한다 → '다시 고르기' 를 누르면 그 말만 다시 묻는다
    _save(store, conversation_id, repick=reused and {
        "word": reused[0]["word"], "request": asdict(replace(asked_request, followup=None, confirm=None)), "text": source})
    if decision.status == "ask" and decision.asking != "detail":
        _save_pending(store, conversation_id, {
            "request": asdict(replace(request, followup=None, confirm=None)),
            "text": source, "intent": decision.intent, "asking": decision.asking,
            "ask_about": list(decision.ask_about), "options": list(decision.options),
        })
    else:
        _save_pending(store, conversation_id, None)
    return replace(decision, interpretation=interp, request=request, agent=AGENT, aliases=used)


def _asks_anything(interp):
    """별칭 말 외에 조회 조건 · 설명 · 이어받기를 말했는지"""
    return bool(interp.all_categories or interp.explain or interp.include_current or interp.refers_to == "previous"
                or interp.followup == "answer" or any(getattr(interp, k) not in (None, ()) for k in CONDITIONS))


def _ask_repick(store, conversation_id, repick):
    """'다시 고르기': 기억한 대응을 쓰지 않고 그 말의 카테고리를 다시 묻는다. 답하면 별칭이 바뀐다"""
    request = reading.parse(repick["request"])
    others = {k: v for k, v in _state(store, conversation_id).get("aliases", {}).items() if k != repick["word"]}
    request, _ = _with_aliases(request, others)
    decision = Decision("ask", "spending", asking="category", ask_about=(repick["word"],),
                        options=category_options(request.target == "budget_left"), agent=AGENT)
    _save(store, conversation_id, repick=None, pending={
        "request": asdict(request), "text": repick["text"], "intent": "spending", "asking": "category",
        "ask_about": [repick["word"]], "options": list(decision.options)})
    return replace(decision, request=request)


# ---------- 규칙 (router_core/rules.py 에서 그대로 옮김) ----------

def _ready(interp, by_category, overall, **extra):
    """대체 범위에 동의한 세부 지출은 그 카테고리 전체로 조회하고, 원래 말(instead_of)을 항목에 남긴다 (답변 · 설명이 범위를 밝힌다)"""
    if not interp.categories:
        items = ({"type": overall, "category": None, **extra},)
    else:
        items = tuple({"type": by_category, "category": c["category"], "instead_of": c["detail"], **extra} if _detail(c)
                      else {"type": by_category, "category": c, **extra} for c in interp.categories)
    return Decision("ready", "spending", items)


def _mode(interp):
    """비교 방식: current(이번 기간) · prev_month(지난달 대비) · recent(최근 N개월) · last_month(지난달 단독) · category_compare"""
    if interp.compare == "category":
        return "category_compare"
    if interp.compare == "prev_month":
        return "prev_month"
    # 개월 수 · 이번 달 포함만 말해도 최근 N개월로 본다
    if interp.period == "recent" or interp.months is not None or interp.include_current:
        return "recent"
    if interp.period == "last_month":
        return "last_month"
    return "current"


def _budget_left(interp, mode):
    # 분석 대상(남은 예산)을 유지한다. 지원하는 비교 방식은 current 뿐 (10/29 확정)
    if mode == "current":
        return _ready(interp, 3, 4)
    if mode == "category_compare":
        # 카테고리끼리 비교는 대상과 상관없이 미지원. 현재 기간 질문으로 보내지 않는다
        return Decision("unsupported", "spending", reason="category_compare")
    # 지난달 대비 · 최근 N개월 · 지난달 단독 → 미지원 안내(reason) + 현재 예산 기간 잔액으로 볼지 되묻기
    return Decision("ask", "spending", asking="budget_current", ask_about=interp.categories,
                    options=(True, False), reason=mode)


def _spend(interp, mode):
    """쓴 돈 · 전반 분석"""
    if mode == "category_compare":
        # 1차 미지원 (10/29 확정). 단순 복수 카테고리 조회(compare 없음)와 다르다
        return Decision("unsupported", "spending", reason="category_compare")
    if mode == "last_month":
        # 지난달 단독 조회는 미지원. 비교나 최근 1개월로 바꾸지 않는다
        return Decision("unsupported", "spending", reason="last_month_only")
    if mode == "prev_month":
        return _ready(interp, 7, 6)
    if mode == "recent":
        months = interp.months
        if months is not None and months < 1:
            return Decision("ask", "spending", asking="months")
        applied = MONTHS_DEFAULT if months is None else min(months, MONTHS_MAX)
        notices = ("months_capped",) if months is not None and months > MONTHS_MAX else ()
        # 이번 달 포함은 따로 요청했을 때만 (기존 '최근 N개월' = 완료된 달 기준 그대로)
        extra = {"include_current": True} if interp.include_current else {}
        return replace(_ready(interp, 9, 8, months_requested=months, months_applied=applied, **extra), notices=notices)
    if interp.target == "spend":
        return _ready(interp, 2, 1)
    # "식비 알려줘" 처럼 전반 분석 + 카테고리면 2번 (10/29 확정)
    return _ready(interp, 2, 5)


def unknown_words(interp):
    return tuple(c["unknown"] for c in interp.categories if _unknown(c))


def category_options(budget):
    # 소비 조회는 12개 전부, 남은 예산 조회는 예산 줄이 없는 UNCLASSIFIED 를 뺀다 (10/29 확정)
    return tuple({"code": code, "name": name} for code, name in CATEGORY_NAMES.items()
                 if not (budget and code in NO_BUDGET))


CONDITIONS = ("target", "period", "compare", "months", "include_current", "categories")
PERIOD_GROUP = ("period", "compare", "months", "include_current")


def conditions(interp):
    """이어받을 수 있는 조회 조건만 (JSON 으로 저장 가능한 모양)"""
    out = {"target": interp.target, "period": interp.period, "compare": interp.compare,
           "months": interp.months, "categories": list(interp.categories)}
    return {**out, "include_current": True} if interp.include_current else out  # 이번 달 포함일 때만 (기존 기록 모양 그대로)


def overlay(top, base):
    """top 이 명시한 조건 우선, 빈 칸만 base(조건 dict)에서 가져온다. 기본값은 이 뒤에 규칙이 넣는다
    - 기간 · 비교 · 개월은 한 묶음: top 이 하나라도 말했으면 base 의 묶음은 버린다.
      개월 수만 말하면 _mode 가 최근 N개월로 보므로 최근 추이 의미는 그대로 남고("3개월은?"),
      지난달 비교 등 다른 방식이었으면 최근 N개월로 바뀐다("최근 2개월로 봐줘"). "이번 달은?" → 최근 추이 · 개월 제거
    - 카테고리: 말하지 않았으면 이어받고, 전체를 명시(all_categories)했으면 비운다
    - 남은 예산과 맞지 않는 기간 묶음은 그대로 넘겨 _budget_left 가 현재 예산 기간 확인 질문을 하게 둔다"""
    fields = {}
    if top.target is None:
        fields["target"] = base["target"]
    if all(getattr(top, k) is None for k in PERIOD_GROUP):
        fields.update({k: base.get(k) for k in PERIOD_GROUP})
    if not top.categories and not top.all_categories:
        fields["categories"] = tuple(base["categories"])
    return replace(top, refers_to=None, **fields)


CHOICE_ASKS = ("period_choice", "category_choice", "include_choice")
LABELED = CHOICE_ASKS + ("fallback_scope",)  # 선택지 이름을 그대로 보내면 LLM 없이 답으로 본다
_NONE = {"period": None, "compare": None, "months": None, "include_current": None}
VIEWS = {  # 기간 묶음 하나를 고르면 그 묶음 칸만 바꾼다 (최근 N개월의 개월 수 · 이번 달 포함은 말한 값 그대로)
    "this_month": ("이번 달", {**_NONE, "period": "this_month"}),
    "last_month": ("지난달", {**_NONE, "period": "last_month"}),
    "prev_month": ("지난달과 비교", {**_NONE, "compare": "prev_month"}),
    "recent": ("최근 {n}개월", {**_NONE, "period": "recent"}),
}


def _views(interp):
    """명시한 기간 · 비교 조건이 가리키는 보기 방식들
    - 이번 달 + 지난달 비교 = 한 보기 (이번 달을 지난달과 비교)
    - 이번 달 포함(include_current) 최근 N개월의 '이번 달'은 포함 표현이라 이번 달 단독 보기가 아니다"""
    recent = interp.period == "recent" or interp.months is not None or bool(interp.include_current)
    found = [v for v, on in (("this_month", interp.period == "this_month"), ("last_month", interp.period == "last_month"),
                             ("prev_month", interp.compare == "prev_month"), ("recent", recent)) if on]
    return [v for v in found if not (v == "this_month" and ("prev_month" in found or interp.include_current))]


def _conflict(interp):
    """명시한 조건끼리 모순이면 (질문, 선택지). 어느 쪽도 임의로 고르지 않고 말한 조건을 선택지로 돌려준다
    기간 · 비교 보기가 둘 이상이면 기간부터, 전체 + 특정 카테고리면 범위를 묻는다 (고른 뒤 남은 모순은 다음에 묻는다)"""
    views = _views(interp)
    n = MONTHS_DEFAULT if interp.months is None else interp.months
    if set(views) == {"prev_month", "recent"} and not interp.include_current:
        # "지난달 대비 최근 3개월로 비교" — 달끼리 비교하려는 뜻은 같고 이번 달을 넣을지만 모호하다 → 포함 여부만 묻는다
        keep = {**_NONE, "period": "recent", "months": interp.months}
        return "include_choice", ({"value": "completed", "label": f"완료된 {n}개월", "set": {**keep, "include_current": False}},
                                  {"value": "include", "label": f"이번 달 포함 {n}개월",
                                   "set": {**keep, "include_current": True}})
    if len(views) > 1:
        return "period_choice", tuple(
            {"value": v, "label": VIEWS[v][0].format(n=n),
             "set": {**VIEWS[v][1], **({"months": interp.months} if v == "recent" else {})}} for v in views)
    if interp.all_categories and interp.categories:
        names = ", ".join(label(c) for c in interp.categories)
        return "category_choice", ({"value": "all", "label": "전체", "set": {"all_categories": True, "categories": []}},
                                   {"value": "categories", "label": names,
                                    "set": {"all_categories": None, "categories": list(interp.categories)}})
    return None


def _picked_view(answer, asking, options):
    """후속 답(해석) → 고른 선택지. 선택지에 없는 보기면 None"""
    if asking == "period_choice":
        views = _views(answer)
        value = views[0] if len(views) == 1 else None
    elif asking == "include_choice":
        value = {True: "include", False: "completed"}.get(answer.include_current)
    else:
        value = "all" if answer.all_categories else "categories" if answer.categories else None
    option = next((o for o in options if o["value"] == value), None)
    if option and value in ("recent", "completed", "include") and answer.months is not None:
        option = {**option, "set": {**option["set"], "months": answer.months}}  # 답에서 개월 수를 다시 말했으면 그 값
    return option


def _explain(interp, text, last):
    """설명 요청 → 참조할 직전 분석 항목만 정한다. 설명 생성 · 재조회는 소비 분석 계층 몫
    근거는 직전 조회의 확정(evidence=confirmed) 항목뿐. 부분 자료 · 비교 불가면 evidence_incomplete 로 멈춘다
    (더 이전의 다른 분석으로 바꿔 설명하지 않는다)"""
    if last is None:
        return Decision("ask", "spending", asking="detail", reason="no_previous_analysis")
    finished = [i for i in last["items"] if i["ok"]]  # 실패한 항목은 참조하지 않는다
    done = [i for i in finished if i.get("evidence") == "confirmed"]
    if interp.categories:
        picked = [i for i in done if i["category"] in interp.categories]
        if len(picked) != len(interp.categories):
            limited = any(i["category"] in interp.categories for i in finished)
            return Decision("unsupported", "spending", reason="evidence_incomplete" if limited else "reference_not_available")
    elif not done:
        return Decision("unsupported", "spending", reason="evidence_incomplete" if finished else "reference_not_available")
    elif len(done) == 1:
        picked = done
    else:
        # 여러 결과 중 무엇인지 모르면 고르지 않고 묻는다
        return Decision("ask", "spending", asking="explain_target", ask_about=tuple(i["category"] for i in done),
                        options=tuple({"code": i["category"], "name": CATEGORY_NAMES[i["category"]]} for i in done))
    return Decision("ready", "spending", explain={"question": text, "items": picked})


def decide(interp, text, last=None):
    """소비 요청(mine=true 인 해석) → Decision. text = 원래 문장, last = 직전 조회 (없으면 None)
    복합 의도 · 예산 조정 · 범위 밖 판단은 라우터 몫"""
    if interp.after_budget_change and interp.refers_to != "previous" \
            and interp.target is None and not interp.categories and not interp.all_categories:
        # "바뀐 예산"이라는 말만으로 3 · 4번에 고정하지 않는다 → 볼 분석 범위만 묻는다
        return Decision("ask", "spending", asking="scope", reason="analysis_unclear")
    if interp.explain:
        return _explain(interp, text, last)
    if interp.refers_to in ("previous", "unclear"):
        if interp.refers_to == "unclear" or last is None:
            # 무엇을 이어받을지 모르거나 이어받을 분석이 없다 → 기본값으로 채우지 않고 조건을 묻는다
            reason = "reference_unclear" if interp.refers_to == "unclear" else "no_previous_analysis"
            return Decision("ask", "spending", asking="scope", ask_about=interp.categories, reason=reason)
        merged = overlay(interp, last["request"])
        # 이어받은 요청을 결과에 남긴다 → 대기 상태 · 성공 기록이 이어받은 조건으로 이어진다
        return replace(decide(merged, text, last), request=merged)
    conflict = _conflict(interp)
    if conflict:
        # 이어받은 뒤 검사 (대기 상태에 이어받은 조건이 남게). 추측해서 하나를 고르지 않고 말한 조건 중 무엇인지 묻는다
        return Decision("ask", "spending", asking=conflict[0], ask_about=interp.categories, options=conflict[1],
                        reason="include_unclear" if conflict[0] == "include_choice" else "conflicting_conditions")
    budget = interp.target == "budget_left"
    if unknown_words(interp):
        # 확인된 카테고리 · 순서 · 기간은 대기 상태에 남기고 모르는 말만 묻는다. 선택지 = 서비스 카테고리 목록
        return Decision("ask", "spending", asking="category", ask_about=unknown_words(interp),
                        options=category_options(budget))
    details = [c for c in interp.categories if _detail(c) and not c.get("accepted")]
    if details:
        # 세부 지출(커피 등)만 따로 볼 수는 없다 → 같은 뜻으로 바꾸지 않고, 대신 볼 카테고리 전체를 제안 · 동의를 받는다
        word, code = details[0]["detail"], details[0]["category"]
        if code is None:  # 대체 범위가 불분명 → 임의로 정하지 않고 고르게 한다
            return Decision("ask", "spending", asking="fallback_category", ask_about=(word,),
                            options=category_options(budget), reason="detail_not_supported")
        return Decision("ask", "spending", asking="fallback_scope", ask_about=(word,), reason="detail_not_supported",
                        options=({"value": True, "label": f"{CATEGORY_NAMES[code]} 전체 보기", "set": {"confirm": True},
                                  "code": code},
                                 {"value": False, "label": "취소", "set": {"confirm": False}}))
    skipped = ()
    if budget:
        # 예산 줄이 없는 카테고리는 다른 코드로 바꾸거나 0 원으로 만들지 않고 빼서 이유와 함께 돌려준다
        skipped = tuple({"category": c, "reason": "no_budget_category"} for c in interp.categories if c in NO_BUDGET)
        if skipped:
            interp = replace(interp, categories=tuple(c for c in interp.categories if c not in NO_BUDGET))
            if not interp.categories:
                return Decision("unsupported", "spending", reason="no_budget_category", skipped=skipped)
    mode = _mode(interp)
    return replace(_budget_left(interp, mode) if budget else _spend(interp, mode), skipped=skipped)


def merge(request, answer, asking, options):
    """대기 중인 소비 요청 + 후속 답변 → 합친 Interpretation. 답이 질문에 맞지 않으면 None (거절은 step 이 먼저 처리)"""
    if asking == "months" and answer.months is not None:
        return replace(request, months=answer.months, period="recent")
    if asking == "category":
        return _fill_categories(request, answer.categories)
    if asking == "scope" and (answer.all_categories or any(getattr(answer, k) not in (None, ()) for k in CONDITIONS)):
        merged = overlay(answer, conditions(request))
        # 답에서 카테고리를 다시 말했으면 그 범위가 '전체' 여부를 정한다
        whole = answer.all_categories or (request.all_categories and not answer.categories)
        return replace(request, **{k: getattr(merged, k) for k in CONDITIONS}, all_categories=whole, refers_to=None)
    if asking == "explain_target" and len(answer.categories) == 1 \
            and answer.categories[0] in [o["code"] for o in options]:
        return replace(request, categories=answer.categories)
    if asking in CHOICE_ASKS:
        option = _picked_view(answer, asking, options)
        if option is None:
            return _restated(request, answer, asking)
        values = {**option["set"], "categories": tuple(option["set"].get("categories", request.categories))}
        return replace(request, **values)  # 고른 묶음만 바꾸고 카테고리 · 대상 등 나머지 조건은 그대로
    if asking == "fallback_scope" and answer.confirm is True:
        return _accept(request, options[0]["code"])
    if asking == "fallback_category" and len(answer.categories) == 1 and isinstance(answer.categories[0], str):
        return _accept(request, answer.categories[0])
    if asking == "budget_current" and answer.confirm is True:
        return replace(request, months=None, period=None, compare=None)
    return None


def _restated(request, answer, asking):
    """되묻는 중에 조건을 고쳐 말함("아니다 이번 달 2개월") → 묻던 묶음을 답의 조건으로 바꾼 요청. 다시 decide 하면
    해소됐으면 바로 처리, 아직 충돌이면 새 조건으로 질문 · 선택지를 다시 만든다. 그 묶음을 말하지 않았으면 None(같은 질문)
    바꾸지 않은 조건(카테고리 · 대상)은 그대로. 답에서 카테고리를 다시 말했으면 그것으로"""
    if asking == "category_choice":
        if not (answer.all_categories or answer.categories):
            return None
        return replace(request, all_categories=answer.all_categories, categories=answer.categories)
    if all(getattr(answer, k) is None for k in PERIOD_GROUP):
        return None
    if asking == "include_choice":  # 포함 여부를 묻는 중: 말한 칸만 바꾼다 ("2개월" → 개월만, 포함 여부는 다시 묻는다)
        said = {k: getattr(answer, k) for k in PERIOD_GROUP if getattr(answer, k) is not None}
        return replace(request, **said, categories=answer.categories or request.categories)
    # 기간을 고르는 중: 고쳐 말한 기간 묶음 전체로 바꾼다 ("이번 달만" → 개월 수도 버림)
    return replace(request, **{k: getattr(answer, k) for k in PERIOD_GROUP},
                   categories=answer.categories or request.categories)


def _accept(request, code):
    """대체 범위 동의: 첫 번째 미동의 세부 지출을 그 카테고리 전체로 (원래 말은 남긴다 · 별칭으로 저장하지 않는다)"""
    out, done = [], False
    for c in request.categories:
        if not done and _detail(c) and not c.get("accepted"):
            c, done = {"detail": c["detail"], "category": code, "accepted": True}, True
        out.append(c)
    return replace(request, categories=tuple(out))


def _fill_categories(request, answers):
    """답한 코드로 모르는 말의 자리를 앞에서부터 채운다 (순서 유지). 덜 답하면 남은 자리는 다시 묻는다"""
    holes = [i for i, c in enumerate(request.categories) if _unknown(c)]
    if not answers or len(answers) > len(holes) or not all(isinstance(a, str) for a in answers):
        return None
    filled = list(request.categories)
    for i, code in zip(holes, answers):
        filled[i] = code
    seen, merged = set(), []
    for c in filled:
        if isinstance(c, dict):
            merged.append(c)
        elif c not in seen:  # 같은 코드는 처음 자리만 남긴다
            seen.add(c)
            merged.append(c)
    return replace(request, categories=tuple(merged))


# ---------- 분석 기록 (router_core 에서 옮김) ----------

def record_analysis(store, conversation_id, decision, results):
    """분석 실행 뒤 부른다. results = run_items 결과,
    성공 항목의 result = {"evidence": "confirmed" | "limited", "limits"?: 자료 제한 코드, "period": 실제 조회 기간,
    "as_of": 조회 시점, "compare_period"?: 비교 기간, "facts"?: 확정 수치} — 모두 JSON 으로 저장 가능한 값
    - 결정(ready)만으로는 기록하지 않는다. 전부 실패면 직전 기록을 그대로 둔다
    - 일부만 성공하면 항목마다 ok 로 구분해 남긴다 (실패 항목은 조건만, 결과 없음)
    - ok 는 '조회가 끝났다'는 뜻일 뿐이다. 설명 근거는 evidence == "confirmed" 만 (없으면 "unknown" — 근거 아님)"""
    if not any(r["ok"] for r in results):
        return
    items = []
    for r in results:
        row = {**r["item"], "ok": r["ok"]}
        if r["ok"]:
            row.update({k: r["result"][k] for k in ("limits", "period", "as_of", "compare_period", "facts") if k in r["result"]})
            row["evidence"] = r["result"].get("evidence", "unknown")
        items.append(row)
    store.save_analysis(conversation_id, {"request": conditions(decision.request), "items": items})


def run_items(items, fetch):
    """소비 분석 항목마다 fetch(item) 호출. 한 항목이 실패해도 나머지 결과는 지킨다 → [{item, ok, result | error}]"""
    results = []
    for item in items:
        try:
            results.append({"item": item, "ok": True, "result": fetch(item)})
        except Exception as error:  # 조회 함수의 오류 종류를 가리지 않고 그 항목만 실패로 남긴다
            results.append({"item": item, "ok": False, "error": type(error).__name__})
    return results
