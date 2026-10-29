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
HINT_OBSERVE = ["categories", "direction", "amount", "amount_mode"]
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
]
NEW_SCENARIOS = [
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
        "version": "B-2026-10-29.2", "format": "role",
        "changes": [
            "B-2026-10-29.1: 첫 변환 (frozen/cases_b_B-2026-10-29.1.json 에 보존, 28차 실제 평가에 사용)",
            "B-2026-10-29.2: ① followup 기대는 그 역할이 질문 중일 때만 (역할별 계약) — 예산 진행 중 재분류 턴 d25 · n11 · "
            "s3#3 · s5#3 · s6#2 ② chosen 소비 기대값은 원래 문장의 기대값으로 — s8#2 ③ 정책 검토 표시 n10 · s8#2",
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
