"""cases.json(분리 전 통합 형식) → cases_b.json(역할별 형식) 변환 + 단계 B 새 사례. 네트워크 · LLM 호출 없음

    cd <저장소> && .venv/bin/python scripts/router_eval/make_cases_b.py      # cases_b.json 을 다시 만든다 (결과가 같아야 함)

- 기존 사례는 회귀용(regression_*)으로 옮긴다. cases.json 과 저장 결과는 그대로 둔다
- 각 단위를 새 구조(정답 추출값을 역할별로 바꿔 넣음)로 한 번 돌려, 어느 역할이 불리는지 · 그때의 입력(되묻기 · chosen)을 보고
  역할별 기대값을 만든다. 라우터 기대값에서 예산 보조값은 예산 조정 요청일 때만 남긴다 (그 밖에는 비어 있어야 함)
- 턴 시작 때 예산 작업이 진행 중이면 원문이 예산 에이전트로 간다 → 분리 전 기대 결정이 소비 쪽이면 예산 대역 결과를
  not_mine 으로 적는다 (doubles — 대역으로 검증한 범위). 그 밖에는 대역 없이 전달까지만
- 변환한 결과가 분리 전 결정 · 상태 기대값과 다르면 review 에 이유를 남긴다 (사람 확인 필요, 임의로 고치지 않음)
"""
import copy
import hashlib
import json
from functools import partial
from pathlib import Path

import run
import stage_b
from chat.interpretation.legacy import to_router, to_spending, view
from chat.router_core import run_turn as router_run_turn
from chat.router_core.rules import ROUTER_QUESTIONS
from spending_analysis import agent as spending_agent

HERE = Path(__file__).resolve().parent
SPLITS = {"dev": "regression_dev", "new": "regression_new", "holdout": "reference"}
HINTS = ("categories", "direction", "amount", "amount_mode")
ROUTER_KEYS = set(stage_b.ROLES["router"][1].__dataclass_fields__)
SPENDING_ASKS = ("months", "category", "scope", "budget_current", "explain_target", "detail")


def role_expect(expect, role, payload):
    """통합 기대값 → 한 역할의 기대값 (must · allow · observe). payload = 그 역할이 받은 입력 (되묻기 · chosen)
    [B-2026-10-29.2] followup 은 그 역할이 질문 중일 때(payload.pending)만 채점한다 — 역할별 계약: 라우터 followup 은
    라우터 질문 중, 소비 followup 은 소비 되묻기 중에만 쓰이고 그 밖에서는 코드가 읽지 않는다.
    분리 전 통합 형식은 예산 작업 맥락만 있어도 followup 을 기대했다 → 그 기대는 질문이 없으면 관찰만 (어느 값이든 채점 안 함)"""
    must, allow, observe = expect.get("must", {}), expect.get("allow", {}), expect.get("observe", [])
    if not payload.get("pending") and ("followup" in must or "followup" in allow):
        must, allow = {k: v for k, v in must.items() if k != "followup"}, {k: v for k, v in allow.items() if k != "followup"}
        observe = [*observe, "followup"]
    if role == "router":
        budget = "budget_adjust" in must.get("intents", [])
        keep = lambda k: k in ROUTER_KEYS and (budget or k not in HINTS)  # noqa: E731
        out = {"must": {k: v for k, v in to_router(must).items() if keep(k)},
               "allow": {k: v for k, v in to_router(allow).items() if keep(k)}}
        if [k for k in observe if keep(k)]:
            out["observe"] = [k for k in observe if keep(k)]
        return out
    spending = to_spending(must if "intents" in must or must.get("followup") else {**must, "intents": ["spending"]},
                           payload)
    if spending.get("mine") is False:
        return {"must": {"mine": False}, "allow": {}}
    spending.setdefault("mine", True)
    allowed = {k: v for k, v in allow.items() if k in stage_b.ROLES["spending"][1].__dataclass_fields__}
    if spending.get("explain"):  # [B-2026-10-30.1] 설명 종류 칸은 분리 전 사례에 없다 → 새 사례(b14~b17)에서만 채점
        allowed["explain_kind"] = [None, "change", "composition"]
    out = {"must": spending, "allow": allowed}
    if "followup" in observe:
        out["observe"] = ["followup"]
    return out


def budget_double(turn, store):
    """턴 시작 때 예산 작업 진행 중 + 분리 전 기대 결정이 소비 쪽이면 예산 대역은 not_mine"""
    task = store.get_task("eval")
    if not (task and not task["suspended"]) or turn["text"].strip(" .!~") in ("취소", "됐어", "그만"):
        return {}
    route = turn.get("expect_route", {})
    spending = route.get("items") or route.get("intent") == "spending" or route.get("asking") in SPENDING_ASKS \
        or route.get("explain")
    return {"budget": "not_mine"} if spending else {}


def convert(turn, raw_turn, store, server, said):
    """한 단위를 새 구조로 돌려 역할별 기대값을 만든다 → (새 단위, 결정)
    said = 문장 → 분리 전 통합 기대값. 사용자가 라우터 질문에서 소비를 고르면(chosen) 소비는 원래 문장을 해석하므로
    그 원래 문장의 기대값으로 소비 기대값을 만든다 (답 문장의 기대값이 아님)"""
    doubles = budget_double(turn, store)
    source = run.gold(turn["expect_llm"]) if turn.get("expect_llm") else None
    translate, calls = run.role_complete(source), []

    def complete(system, user):
        calls.append((stage_b.ROLE_OF[system], json.loads(user)))
        return translate(system, user)

    agents = {"spending": partial(spending_agent.handle, store=store, complete=complete, execute=lambda decision: None),
              "budget": stage_b.double(doubles.get("budget"))}
    decision = view(router_run_turn("eval", store, complete, text=turn["text"], agents=agents, server=server))
    unit = {k: v for k, v in raw_turn.items() if k != "expect_llm"}
    review = []
    for role, payload in calls:
        source = turn["expect_llm"]
        if role == "spending" and payload.get("chosen"):
            source = said.get(payload["text"])
            if source is None:
                review.append("chosen_source_missing")
                continue
        unit[stage_b.EXPECT[role]] = role_expect(source, role, payload)
    if doubles:
        unit["doubles"] = doubles
    if not run.score_paths(decision, turn.get("expect_route", {}))[0]:
        review.append("decision")
    if not run.score_paths(stage_b.snapshot(store, "eval"), turn.get("expect_state", {}))[0]:
        review.append("state")
    if review:
        unit["review"] = review
    return unit, decision


def fixtures(raw):
    """fixture 의 보관 요청을 질문한 쪽 형식으로 (응답 변환과 같은 규칙)"""
    out = copy.deepcopy(raw)
    for value in out.values():
        if isinstance(value, dict) and "asking" in value and value.get("request"):
            value["request"] = to_router(value["request"]) if value["asking"] in ROUTER_QUESTIONS \
                else to_spending(value["request"], {"chosen": True})
    return out


ASK_MONTHS = "@ask_months"
# 새 사례의 직전 분석 (22차 이후 형식 — evidence 포함)
_NOW, _PREV, _AS_OF = {"from": "2026-10-01", "to": "2026-10-29"}, {"from": "2026-09-01", "to": "2026-09-29"}, "2026-10-29T10:00:00+09:00"


def _last(target, compare, categories, item):
    return {"request": {"target": target, "period": None, "compare": compare, "months": None, "categories": categories},
            "items": [{**item, "ok": True, "evidence": "confirmed", "as_of": _AS_OF}]}


FOOD_BUDGET = _last("budget_left", None, ["FOOD"], {"type": 3, "category": "FOOD", "period": {"from": "2026-10-25", "to": "2026-10-29"}})
TRANSPORT_PREV = _last("spend", "prev_month", ["TRANSPORT_CAR"],
                       {"type": 7, "category": "TRANSPORT_CAR", "period": _NOW, "compare_period": _PREV})
FOOD_PREV = _last("spend", "prev_month", ["FOOD"], {"type": 7, "category": "FOOD", "period": _NOW, "compare_period": _PREV})
FOOD_NOW = _last("spend", None, ["FOOD"], {"type": 2, "category": "FOOD", "period": _NOW})
OVERVIEW_NOW = _last("overview", None, [], {"type": 5, "category": None, "period": _NOW})
HINT_OBSERVE = ["categories", "direction", "amount", "amount_mode"]
def _q(cid, text, must, items=None, route=None, allow=None):
    """범용 조회 사례: 소비 해석 must (말한 조건) + 흔한 허용값(target · period · refers_to) + 결정 기대값"""
    allowed = {"target": [None, "spend", "overview"], "period": [None, "this_month"], "refers_to": [None, "none"], **(allow or {})}
    allowed = {k: v for k, v in allowed.items() if k not in must}
    return {"id": cid, "text": text, "expect_router": {"must": {"intents": ["spending"]}, "allow": {"categories": [[], *[
                [c] for c in must.get("categories", [])]]}},
            "expect_spending": {"must": {"mine": True, **must}, "allow": allowed},
            "expect_route": route or ({"status": "ready", "items": items} if items else {"status": "ready"})}


NEW_CASES = [
    {"id": "b01", "text": "청년 월세 지원 같은 거 나도 받을 수 있어?",
     "expect_router": {"must": {"intents": ["policy"]}},
     "doubles": {"policy": {"status": "awaiting_input", "work_ref": "P1"}},
     "expect_route": {"intent": "policy", "handoff.mode": "start"}, "expect_state": {"policy_work.work_ref": "P1"}},
    {"id": "b02", "text": "다음 달 교통비 예산 좀 늘려줄래?",
     "expect_router": {"must": {"intents": ["budget_adjust"]}, "observe": HINT_OBSERVE},
     "expect_route": {"intent": "budget_adjust", "handoff.mode": "start"}, "expect_state": {"budget_task.phase": "started"}},
    {"id": "b03", "text": "주말에 뭐 먹을지 추천해줘", "expect_router": {"must": {"intents": ["unsupported"]}},
     "expect_route": {"status": "unsupported", "reason": "out_of_scope"}},
    {"id": "b04", "text": "식비 예산 줄이고 청년 정책도 알려줘",
     "expect_router": {"must": {"intents": ["budget_adjust", "policy"]}, "observe": HINT_OBSERVE},
     "expect_route": {"status": "ask", "asking": "intent", "options": ["budget_adjust", "policy"]},
     "expect_state": {"pending.asking": "intent"}},
    {"id": "b05", "text": "이번 달 쇼핑에 얼마 썼어?", "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "target": "spend", "categories": ["FASHION_SHOPPING"]},
                         "allow": {"period": ["this_month", None], "refers_to": [None, "none"]}},
     "expect_route": {"items": [{"type": 2, "category": "FASHION_SHOPPING"}]}},
    # 소비 되묻기(개월 수) 중 금액이 들어간 입력 세 가지: 불분명한 답 / 다른 분야 요청 / 금액이 든 새 소비 요청
    {"id": "b06", "text": "5만 원만큼", "state": {"pending": ASK_MONTHS},
     "expect_spending": {"must": {"mine": True, "followup": "unclear"}},
     "expect_route": {"status": "ask", "asking": "months"}, "expect_state": {"pending.asking": "months"}},
    {"id": "b07", "text": "예산을 5만 원 줄여줘", "state": {"pending": ASK_MONTHS},
     "expect_spending": {"must": {"mine": False}},
     "expect_router": {"must": {"intents": ["budget_adjust"]}, "observe": HINT_OBSERVE},
     "expect_route": {"intent": "budget_adjust", "handoff.mode": "start"},
     "expect_state": {"pending": None, "spending_pending.asking": "months", "budget_task.phase": "started"}},
    {"id": "b08", "text": "지난달보다 5만 원 더 썼어?", "state": {"pending": ASK_MONTHS},
     "expect_spending": {"must": {"mine": True, "followup": "new", "compare": "prev_month", "target": "spend"},
                         "allow": {"refers_to": [None, "none"]}},
     "expect_route": {"items": [{"type": 6, "category": None}]}, "expect_state": {"pending": None}},
    # [B-2026-10-30.1] 사용자 테스트 후속: 카드 결제만 · 가게 이름 · 카테고리별 · 하루 금액 · 설명 종류
    {"id": "b09", "text": "이번 달 카드 얼마 긁었지?", "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "payment": "card"},
                         "allow": {"target": ["spend", None, "overview"], "period": ["this_month", None], "refers_to": [None, "none"]}},
     "expect_route": {"items": [{"type": 1, "category": None, "payment": "card"}]}},
    {"id": "b10", "text": "스타벅스에서 얼마나 썼어?", "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "target": "spend", "categories": [{"detail": "스타벅스", "category": "FOOD"}]},
                         "allow": {"period": ["this_month", None], "refers_to": [None, "none"]}},
     # 평가에는 회원 거래가 없어 가게 이름 검색을 하지 않는다 → 식비 전체로 볼지 묻는다 (names 표기는 관찰만)
     "expect_route": {"status": "ask", "asking": "fallback_scope"}},
    {"id": "b11", "text": "카테고리별로 예산 얼마나 남았어?", "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "target": "budget_left", "compare": "category"},
                         "allow": {"refers_to": [None, "none"], "all_categories": [None, True]}},
     "expect_route": {"items": [{"type": 4, "category": None, "breakdown": True}]}},
    {"id": "b12", "text": "그럼 카테고리별로는?", "state": {"last_analysis": FOOD_BUDGET},
     "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "compare": "category", "refers_to": "previous"},
                         "allow": {"all_categories": [None, True]}},
     "expect_route": {"items": [{"type": 4, "category": None, "breakdown": True}]}},
    {"id": "b13", "text": "남은 기간 동안 식비 하루에 얼마 쓸 수 있어?", "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "target": "budget_left", "categories": ["FOOD"]},
                         "allow": {"refers_to": [None, "none"], "period": [None, "this_month"]}},
     "expect_route": {"items": [{"type": 3, "category": "FOOD"}]}},
    {"id": "b14", "text": "식비는 왜 늘었을까?", "state": {"last_analysis": TRANSPORT_PREV},
     "expect_router": {"must": {"intents": ["spending"]}, "allow": {"categories": [[], ["FOOD"]]}},
     "expect_spending": {"must": {"mine": True, "explain": True, "categories": ["FOOD"], "explain_kind": "change"},
                         "allow": {"refers_to": ["previous", None, "none"]}},
     "expect_route": {"status": "ready", "explain.items.category": ["FOOD"], "explain.items.fresh": [True]}},
    {"id": "b15", "text": "왜 이렇게 많이 썼어?", "state": {"last_analysis": FOOD_PREV},
     "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "explain": True, "explain_kind": "composition"},
                         "allow": {"refers_to": ["previous", None]}},
     "expect_route": {"status": "ready", "explain.items.type": [2], "explain.items.category": ["FOOD"]}},
    {"id": "b16", "text": "그럼 왜 늘었는데?", "state": {"last_analysis": FOOD_NOW},
     "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "explain": True, "explain_kind": "change"},
                         "allow": {"refers_to": ["previous", None]}},
     "expect_route": {"status": "ready", "explain.items.type": [7], "explain.items.fresh": [True]}},
    {"id": "b17", "text": "제일 많이 쓴 데는 왜 이렇게 많이 나왔어?", "state": {"last_analysis": OVERVIEW_NOW},
     "expect_router": {"must": {"intents": ["spending"]}},
     "expect_spending": {"must": {"mine": True, "explain": True, "explain_kind": "composition"},
                         "allow": {"refers_to": ["previous", None]}},
     "expect_route": {"status": "ready", "explain.items.type": [5]}},
    # [B-2026-10-30.3] 범용 조회 A 경로 (MEMO 23절 3단계) — 문장은 지시문 예시 · 가짜 해석 문장과 다르게
    _q("c01", "주말에 외식비 얼마나 나갔어?", {"weekday": ["weekend"], "categories": ["FOOD"]}, items=[{"type": 2, "category": "FOOD", "weekdays": [5, 6]}]),
    _q("c02", "평일 점심값으로 얼마 썼지?", {"weekday": ["weekday"], "time_slot": ["lunch"], "categories": ["FOOD"]}, items=[{"type": 2, "category": "FOOD", "weekdays": [0, 1, 2, 3, 4], "time_slots": ["lunch"]}]),
    _q("c03", "새벽에 쓴 돈 합계 알려줘", {"time_slot": ["dawn"]}, items=[{"type": 1, "category": None, "time_slots": ["dawn"]}]),
    _q("c04", "퇴근하고 나서 쓴 돈 얼마야?", {"time_slot": ["after_work"]}, items=[{"type": 1, "category": None, "time_slots": ["evening", "night"]}], allow={"time_slot": [["after_work"], ["evening", "night"]]}),
    _q("c05", "늦은 밤에 시켜 먹은 거 합치면 얼마야?", {}, allow={"time_slot": [["late_night"], ["night"], ["night", "dawn"]], "categories": [["FOOD"], []]}),
    _q("c06", "3만 원 이상 결제한 거 몇 건이야?", {"amount_min": 30000, "measure": "count"}, items=[{"type": 10, "category": None, "amount_min": 30000, "query": {"period": "this_month", "measure": "count"}}]),
    _q("c07", "5천 원 이하 소액 결제는 다 합쳐서 얼마야?", {"amount_max": 5000}, items=[{"type": 1, "category": None, "amount_max": 5000}], allow={"measure": [None, "sum"]}),
    _q("c08", "이번 달 결제 횟수 알려줘", {"measure": "count"}, items=[{"type": 10, "category": None, "query": {"period": "this_month", "measure": "count"}}]),
    _q("c09", "교통비는 건당 평균 얼마 정도야?", {"measure": "avg", "categories": ["TRANSPORT_CAR"]}, items=[{"type": 10, "category": "TRANSPORT_CAR", "query": {"period": "this_month", "measure": "avg"}}]),
    _q("c10", "하루에 평균 얼마씩 쓰고 있어?", {"measure": "daily_avg"}, items=[{"type": 10, "category": None, "query": {"period": "this_month", "measure": "daily_avg"}}]),
    _q("c11", "이번 달 가장 비싸게 결제한 건 뭐야?", {"measure": "max"}, items=[{"type": 10, "category": None, "query": {"period": "this_month", "measure": "max"}}]),
    _q("c12", "제일 많이 간 가게 다섯 곳", {"measure": "count", "group_by": "merchant", "top_n": 5}, items=[{"type": 10, "category": None, "query": {"period": "this_month", "measure": "count", "group_by": "merchant", "top_n": 5}}]),
    _q("c13", "요일마다 얼마씩 썼는지 보여줘", {"group_by": "weekday"}, items=[{"type": 10, "category": None, "query": {"period": "this_month", "measure": "sum", "group_by": "weekday"}}], allow={"measure": [None, "sum"]}),
    _q("c14", "시간대별 지출 나눠서 보여줘", {"group_by": "time_slot"}, items=[{"type": 10, "category": None, "query": {"period": "this_month", "measure": "sum", "group_by": "time_slot"}}], allow={"measure": [None, "sum"]}),
    _q("c15", "최근 3개월 식비를 달마다 보여줘", {"months": 3, "categories": ["FOOD"]}, allow={"period": [None, "recent"], "group_by": [None, "month"]}),
    _q("c16", "지난달 한 달 동안 교통비 얼마 들었어?", {"period": "last_month", "categories": ["TRANSPORT_CAR"]}, items=[{"type": 10, "category": "TRANSPORT_CAR", "query": {"period": "last_month", "measure": "sum"}}], allow={"measure": [None, "sum"]}),
    _q("c17", "계좌로 빠져나간 돈만 얼마야?", {"payment": "bank"}, items=[{"type": 1, "category": None, "payment": "bank"}]),
    _q("c18", "예산을 요일별로 쪼개서 보여줘", {"target": "budget_left", "group_by": "weekday"}, route={"status": "unsupported", "reason": "budget_query"}),
    _q("c19", "지난달보다 가게별로 몇 번 더 갔어?", {"compare": "prev_month", "group_by": "merchant", "measure": "count"}, route={"status": "unsupported", "reason": "compare_query"}),
    # [B-2026-10-30.4] 지정 기간 · 볼 수 없는 기간 · 기간끼리 비교 · 고정지출 포함 (사용자 테스트 보완 10/30)
    _q("p01", "이번 주에 쓴 돈 얼마야?", {"period": "this_week"}, items=[{"type": 10, "category": None, "query": {"period": "this_week", "measure": "sum"}}]),
    _q("p02", "지난주 교통비 얼마 나왔어?", {"period": "last_week", "categories": ["TRANSPORT_CAR"]}, items=[{"type": 10, "category": "TRANSPORT_CAR", "query": {"period": "last_week", "measure": "sum"}}]),
    _q("p03", "8월에 식비 얼마 썼지?", {"period": "month", "month": 8, "categories": ["FOOD"]}, items=[{"type": 10, "category": "FOOD", "month": 8, "query": {"period": "month", "measure": "sum"}}]),
    _q("p04", "10월 5일부터 12일까지 쓴 돈 알려줘", {"period": "range", "date_from": "10-05", "date_to": "10-12"}, items=[{"type": 10, "category": None, "date_from": "10-05", "date_to": "10-12", "query": {"period": "range", "measure": "sum"}}]),
    _q("p05", "작년 이맘때 얼마 썼어?", {"period_said": "작년 이맘때"}, route={"status": "ask", "asking": "period_choice", "reason": "period_unavailable"}),
    _q("p06", "지난주랑 비교하면 이번 주에 더 썼어?", {"compare": "other_period"}, route={"status": "unsupported", "reason": "period_compare"}, allow={"period": [None, "this_week"]}),
    _q("p07", "고정지출까지 넣어서 요일별로 보여줘", {"include_fixed": True, "group_by": "weekday"}, items=[{"type": 10, "category": None, "include_fixed": True, "query": {"period": "this_month", "measure": "sum", "group_by": "weekday"}}], allow={"measure": [None, "sum"]}),
    # [B-2026-11-02.1] 기간 + 카테고리별(예전엔 기간을 버림) · 다른 사람의 소비 (사용자 테스트 보완 11/2)
    _q("g01", "10월 5일부터 10일까지 카테고리별로 얼마 썼어?", {"period": "range", "date_from": "10-05", "date_to": "10-10"},
       items=[{"type": 10, "category": None, "date_from": "10-05", "date_to": "10-10",
               "query": {"period": "range", "measure": "sum", "group_by": "category"}}],
       allow={"compare": ["category", None], "group_by": [None, "category"], "measure": [None, "sum"]}),
    _q("g02", "남자친구가 이번 달 쇼핑에 얼마 썼어?", {"other_person": True, "categories": ["FASHION_SHOPPING"]},
       route={"status": "ask", "asking": "self_instead", "reason": "other_person"}),
    _q("g03", "우리 엄마 이번 달 카드값 얼마 나왔어?", {"other_person": True},
       route={"status": "ask", "asking": "self_instead", "reason": "other_person"}, allow={"payment": [None, "card"]}),
    _q("g04", "내가 이번 달 쇼핑에 얼마 썼어?", {"categories": ["FASHION_SHOPPING"]}, items=[{"type": 2, "category": "FASHION_SHOPPING"}]),
    # [B-2026-11-02.3] 월 · 연도를 생략한 날짜 — 해석은 말한 값만('DD'), 채우기는 서버 (사용자 테스트 보완 2차)
    _q("r01", "20일부터 27일까지 쓴 돈 얼마야?", {"period": "range", "date_from": "20", "date_to": "27"},
       items=[{"type": 10, "category": None, "date_from": "20", "date_to": "27", "query": {"period": "range", "measure": "sum"}}]),
    _q("r02", "3일부터 지금까지 식비 얼마 썼어?", {"period": "range", "date_from": "03", "categories": ["FOOD"]},
       items=[{"type": 10, "category": "FOOD", "date_from": "03", "query": {"period": "range", "measure": "sum"}}]),
    _q("r03", "10월 25일부터 11월 3일까지 얼마 썼어?", {"period": "range", "date_from": "10-25", "date_to": "11-03"},
       items=[{"type": 10, "category": None, "date_from": "10-25", "date_to": "11-03", "query": {"period": "range", "measure": "sum"}}]),
]
NEW_SCENARIOS = [
    {"id": "ps1", "note": "고정지출 기본 제외 → '고정지출도 넣어서' 후속 (직전 조건 + 포함)", "turns": [
        {"text": "시간대별로 얼마 썼는지 알려줘", "expect_router": {"must": {"intents": ["spending"]}},
         "expect_spending": {"must": {"mine": True, "group_by": "time_slot"},
                             "allow": {"target": [None, "spend", "overview"], "period": [None, "this_month"], "refers_to": [None, "none"],
                                       "measure": [None, "sum"]}},
         "expect_route": {"items": [{"type": 10, "category": None, "query": {"period": "this_month", "measure": "sum", "group_by": "time_slot"}}]},
         "after": {"analysis": True}},
        {"text": "고정지출도 넣어서 다시 보여줘",
         "expect_spending": {"must": {"mine": True, "refers_to": "previous", "include_fixed": True}, "observe": ["followup"]},
         "expect_route": {"items": [{"type": 10, "category": None, "include_fixed": True,
                                     "query": {"period": "this_month", "measure": "sum", "group_by": "time_slot"}}]}}]},
    {"id": "cs1", "note": "범용 조회 조건 이어받기: 주말 쇼핑 → 그럼 식비는? (주말 그대로)", "turns": [
        {"text": "주말에 쇼핑 얼마나 했어?", "expect_router": {"must": {"intents": ["spending"]}, "allow": {"categories": [[], ["FASHION_SHOPPING"]]}},
         "expect_spending": {"must": {"mine": True, "weekday": ["weekend"], "categories": ["FASHION_SHOPPING"]},
                             "allow": {"target": [None, "spend", "overview"], "period": [None, "this_month"], "refers_to": [None, "none"]}},
         "expect_route": {"items": [{"type": 2, "category": "FASHION_SHOPPING", "weekdays": [5, 6]}]},
         "after": {"analysis": True}},
        {"text": "그럼 식비는?",  # 소비가 담당 중 → 라우터 해석 없이 소비가 먼저 받는다 (되묻기 아님 → followup 은 관찰만)
         "expect_spending": {"must": {"mine": True, "refers_to": "previous", "categories": ["FOOD"]},
                             "observe": ["followup"]},
         "expect_route": {"items": [{"type": 2, "category": "FOOD", "weekdays": [5, 6]}]}}]},
    {"id": "bs1", "note": "되묻기 중 다른 분야 → 예산 → (예산 진행 중) 소비로 돌아오기", "turns": [
        {"text": "최근 0개월 식비 알려줘", "expect_router": {"must": {"intents": ["spending"]}},
         "expect_spending": {"must": {"mine": True, "months": 0, "categories": ["FOOD"]},
                             "allow": {"period": ["recent", None], "target": [None, "overview"], "refers_to": [None, "none"]}},
         "expect_route": {"status": "ask", "asking": "months"}},
        {"text": "예산을 5만 원 줄여줘", "expect_spending": {"must": {"mine": False}},
         "expect_router": {"must": {"intents": ["budget_adjust"]}, "observe": HINT_OBSERVE},
         "expect_route": {"intent": "budget_adjust", "handoff.mode": "start"},
         "expect_state": {"spending_pending.asking": "months"}},
        {"text": "식비 얼마 썼어?", "doubles": {"budget": "not_mine"},
         "expect_router": {"must": {"intents": ["spending"]}},
         "expect_spending": {"must": {"mine": True, "target": "spend", "categories": ["FOOD"]},
                             "allow": {"refers_to": [None, "none"]}},
         "expect_route": {"items": [{"type": 2, "category": "FOOD"}], "notices": ["budget_task_suspended"]},
         "expect_state": {"budget_task.suspended": True, "spending_pending": None},
         "policy_pending": "보류된 소비 되묻기로 돌아와 새 소비 요청을 할 때 질문을 대신할지 (MEMO 13 미정)"}]},
    {"id": "bs2", "note": "청년 정책 진행 중 소비로 전환 → 정책 재진입(미정)", "turns": [
        {"text": "청년 정책 추천받고 싶어", "expect_router": {"must": {"intents": ["policy"]}},
         "doubles": {"policy": {"status": "awaiting_input", "work_ref": "P1"}},
         "expect_route": {"intent": "policy", "handoff.mode": "start"}},
        {"text": "이번 달 소비부터 볼래", "doubles": {"policy": "not_mine"},
         "expect_router": {"must": {"intents": ["spending"]}},
         "expect_spending": {"must": {"mine": True},
                             "allow": {"target": [None, "overview"], "period": ["this_month", None], "refers_to": [None, "none"]}},
         "expect_route": {"items": [{"type": 5, "category": None}], "notices": ["policy_work_suspended"]},
         "expect_state": {"policy_work.suspended": True}},
        {"text": "아까 정책 추천 계속하자", "expect_spending": {"must": {"mine": False}},
         "expect_router": {"must": {"intents": ["policy"]}},
         "expect_route": {"status": "unsupported", "reason": "policy_resume_undecided"},
         "expect_state": {"policy_work.work_ref": "P1"},
         "policy_pending": "보류된 정책 작업이 있을 때 이어서 · 새로 (MEMO 13 미정)"}]},
]


# 정책 검토 단위: 독립 · 후속 해석이 모두 가능하거나 기대값이 미정 정책에 달린 것 — 점수에는 넣고 따로 표시한다
POLICY_REVIEW = {
    "n10": "직전 분석 뒤 완결된 문장의 독립 · 후속 경계 (17차부터 미정) — 모델 오류로 단정하지 않음",
    "s8#2": "라우터 질문의 답에 의도(intents)를 채워도 되는지 (17차부터 미정) — 라우터 기대값은 분리 전 그대로",
}


def build():
    """cases_b.json 내용 (파일에 쓰지 않음)"""
    raw = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    data = run.load()
    said = {}
    for turn in data["cases"] + [t for f in data["scenarios"] for t in f["turns"]]:
        if turn.get("expect_llm"):
            said.setdefault(turn["text"], turn["expect_llm"])
    out_cases, out_flows = [], []
    for case, raw_case in zip(data["cases"], raw["cases"]):
        store = run.build_store(case.get("state", {}), "eval")
        unit, _ = convert(case, raw_case, store, run.FakeServer(case.get("server")), said)
        if case["id"] in POLICY_REVIEW:
            unit["policy_review"] = POLICY_REVIEW[case["id"]]
        if case["id"] in run.POLICY_CHANGES:  # 분리 전 기대값 대신 바뀐 결정 (이유를 남긴다)
            unit["expect_route"] = case["expect_route"]
            unit["policy_change"] = run.POLICY_CHANGES[case["id"]]["why"]
        out_cases.append({**unit, "split": SPLITS[case["split"]], "origin": case["id"]})
    for flow, raw_flow in zip(data["scenarios"], raw["scenarios"]):
        store, server, turns = run.MemoryStateStore(), run.FakeServer(flow.get("server")), []
        for turn, raw_turn in zip(flow["turns"], raw_flow["turns"]):
            server.states.update((turn.get("before") or {}).get("server", {}))
            unit, decision = convert(turn, raw_turn, store, server, said)
            key = f"{flow['id']}#{len(turns) + 1}"
            if key in POLICY_REVIEW:
                unit["policy_review"] = POLICY_REVIEW[key]
            turns.append(unit)
            run.after_turn(turn, decision, store, "eval", server)
        out_flows.append({**{k: v for k, v in raw_flow.items() if k != "turns"}, "split": SPLITS[flow["split"]],
                          "origin": flow["id"], "turns": turns})
    for case in NEW_CASES:
        out_cases.append({**case, "split": "new_b"})
    for flow in NEW_SCENARIOS:
        out_flows.append({**flow, "split": "new_b"})
    source = (HERE / "cases.json").read_bytes()
    result = {
        "version": "B-2026-11-02.3", "format": "role",
        "changes": [
            "B-2026-10-29.1: 첫 변환 (frozen/cases_b_B-2026-10-29.1.json 에 보존, 28차 실제 평가에 사용)",
            "B-2026-10-29.2: ① followup 기대는 그 역할이 질문 중일 때만 (역할별 계약) — 예산 진행 중 재분류 턴 d25 · n11 · "
            "s3#3 · s5#3 · s6#2 ② chosen 소비 기대값은 원래 문장의 기대값으로 — s8#2 ③ 정책 검토 표시 n10 · s8#2",
            "B-2026-10-30.1: 사용자 결정으로 바뀐 결정 기대값 d12 (카테고리 비교 → 카테고리별 금액, run.POLICY_CHANGES) · "
            "소비 해석 칸 payment · 세부 지출 names 추가 (이전 판은 frozen/cases_b_B-2026-10-29.2.json)",
            "B-2026-10-30.2: 바뀐 결정 기대값 d10 (지난달 단독 → 범용 조회 10번, MEMO 23-11) · 소비 해석 칸 weekday · time_slot · "
            "amount_min · amount_max · measure · group_by · top_n · payment bank (30.1 은 10/30 실제 평가 174회에 사용, 파일은 보존 안 함)",
            "B-2026-10-30.3: 범용 조회 A 경로 새 사례 c01~c19 + 연속 대화 cs1 (MEMO 23절 3단계)",
            "B-2026-10-30.4: 지정 기간 · 볼 수 없는 기간 · 기간 비교 · 고정지출 포함 p01~p07 + 연속 ps1. 소비 해석 칸 period(이번 주 · 지난주 ·"
            " 특정 달 · 날짜 범위) · month · date_from · date_to · period_said(채점 안 함) · include_fixed · compare other_period",
            "B-2026-11-02.1: 기간 + 카테고리별 g01 · 다른 사람의 소비 g02~g03 · 대조 g04. 소비 해석 칸 other_person",
            "B-2026-11-02.2: 바뀐 결정 기대값 n02 (주거·통신 예산 → 예산 범위 밖 안내, 11/2 팀 결정 임시 · MEMO 23-17)",
            "B-2026-11-02.3: 월 · 연도 생략 날짜 r01~r03 (date_from · date_to 에 일만 'DD' 허용)",
        ],
        "source": {"cases_file": "cases.json", "version": raw["version"], "sha256": hashlib.sha256(source).hexdigest()},
        "note": "역할별 기대값(expect_router · expect_spending) · 최종 결정(expect_route) · 상태(expect_state). "
                "기존 사례는 make_cases_b.py 로 변환한 회귀용, review 가 있는 단위는 사람 확인 필요",
        "splits": {"regression_dev": "분리 전 dev 28 + s1~s3 — 회귀용 (프롬프트 개선에 쓴 사례)",
                   "regression_new": "분리 전 new 12 + s4~s8 — 회귀용 (17차에 이미 실행)",
                   "reference": "분리 전 holdout 12 — 참고용 (오염 이력, 기본 실행 안 함)",
                   "new_b": "단계 B 새 사례 — 프롬프트 작성자와 같은 작성자, 실행 전 기대값 고정"},
        "independent": {"regression_dev": False, "regression_new": False, "reference": False, "new_b": False,
                        "why": "모든 사례를 프롬프트 작성자가 썼다 — 독립 평가가 아니다"},
        "fixtures": fixtures(raw["fixtures"]),
        "cases": out_cases, "scenarios": out_flows,
    }
    return result


def dump(result):
    return json.dumps(result, ensure_ascii=False, indent=1) + "\n"


def main():
    result = build()
    (HERE / "cases_b.json").write_text(dump(result), encoding="utf-8")
    flagged = [c["id"] for c in result["cases"] if c.get("review")] + \
        [f"{s['id']}#{i}" for s in result["scenarios"] for i, t in enumerate(s["turns"], 1) if t.get("review")]
    print(f"cases_b.json: 단일 {len(result['cases'])} · 연속 대화 {len(result['scenarios'])} · 사람 확인 필요 {flagged}")


if __name__ == "__main__":
    main()
