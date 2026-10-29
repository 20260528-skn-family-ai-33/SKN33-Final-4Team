"""소비 분석 에이전트 진입점: 공통 입력(원문 + 맥락) → 자체 해석 · 결정 · 분석 실행 · 답변 · 맥락 기록 → 공통 결과

라우터의 run_turn 이 agents["spending"] 으로 부른다: functools.partial(handle, store=..., complete=..., user=...)
"""
from datetime import date, datetime

from django.db import DatabaseError

from . import adapter, dialog, explain
from .answer import compose

START = {"lines": ["어떤 소비가 궁금하세요?"],
         "examples": ["이번 달 얼마 썼어?", "지난달보다 식비 더 썼어?", "식비 예산 얼마 남았어?", "최근 3개월 소비 어때?"]}


def _result(status, reply=None, decision=None, **extra):
    detail = decision and {
        "items": list(decision.items), "asking": decision.asking, "ask_about": list(decision.ask_about),
        "options": list(decision.options), "reason": decision.reason, "notices": list(decision.notices),
        "skipped": list(decision.skipped), "explain": decision.explain,
        "aliases": list(decision.aliases),
    }
    # decision = 같은 프로세스 진단 · 테스트용 원본 결정 (계약 칸 아님, JSON 으로 내보내지 않는다)
    return {"status": status, "reply": reply, "work_ref": None, "ended_reason": None, "error": None, "reason": None,
            "detail": detail, "decision": decision, **extra}


def _run(user, request):
    today = request.get("today") and date.fromisoformat(request["today"])
    as_of = request.get("as_of") and datetime.fromisoformat(request["as_of"])
    return lambda decision: adapter.run(user, decision, today=today, as_of=as_of)


def handle(request, *, store, complete, user=None, execute=None, explain_complete=None):
    """request = 공통 입력 {agent: "spending", mode, entry, text, input_id, conversation_id, today, as_of, context}
    context = {selected_from?, budget_change?: {outcome, checked_at}, switched_from?, declined_by?}
    → {"status": replied | awaiting_input | unsupported | failed | not_mine, "reply", "detail", "reason", "error", ...}
    - not_mine · 해석 실패는 상태를 바꾸지 않는다. 분석 한 번이 끝나도 ended 가 아니다(직전 분석은 남는다)
    - 모든 항목 조회 실패면 failed (라우터가 이번 입력 전 상태로 되돌린다). 일부 실패는 replied + 항목별 안내
    - execute(decision) → run_items 결과 (기본: 실제 분석). None 을 돌려주면 실행을 건너뛴다(평가 · 테스트 대역)
    - 설명 요청은 서버가 거래 근거를 계산해 템플릿 문장으로 답한다. explain_complete(설명 LLM)를 주면 검사를 통과한 문장만 쓴다
      user 가 없으면(라우터 · 평가 대역) 설명 근거를 만들지 않는다"""
    cid, text, mode = request["conversation_id"], request["text"], request["mode"]
    context = request.get("context") or {}
    if mode == "cancel":
        dialog.cancel_question(store, cid)
        decision = dialog.Decision("cancelled", reason="cancel", agent=dialog.AGENT)
    elif text is None:
        decision = dialog.start_button(store, cid)
    else:
        decision = dialog.step(store, cid, text, complete, mode="start" if mode == "start" else "continue",
                               chosen=bool(context.get("selected_from")), budget_change=context.get("budget_change"))
    if decision.status == "not_mine":
        return _result("not_mine", reason=decision.reason, decision=decision)
    if decision.status == "failed":
        return _result("failed", error=decision.reason, decision=decision)
    result = _outcome(decision, store, cid, execute or _run(user, request),
                      user and (lambda items: explain.answer(user, items, explain_complete)))
    dialog.publish(store, cid)
    return result


def _outcome(decision, store, cid, execute, explainer=None):
    if decision.status == "ready" and decision.items:
        results = execute(decision)
        if results is None:
            return _result("replied", decision=decision)  # 실행 생략 (평가 · 테스트 대역)
        reply = compose(results, decision.notices)
        if not any(r["ok"] for r in results):
            return _result("failed", reply, decision, error="analysis_failed")
        adapter.record(store, cid, decision, results)
        return _result("replied", reply, decision)
    if decision.status == "ready" and decision.explain:
        if explainer is None:
            return _result("replied", decision=decision)  # 근거 조회 대역 없음 — 참조할 확정 항목만(detail.explain)
        try:
            reply = explainer(decision.explain["items"])
        except DatabaseError:
            return _result("failed", decision=decision, error="analysis_failed")
        return _result("replied", reply, decision)
    if decision.status == "ask":
        return _result("awaiting_input", START if decision.reason == "start" else None, decision)
    if decision.status == "unsupported":
        return _result("unsupported", decision=decision, reason=decision.reason)
    return _result("replied", decision=decision, reason=decision.reason)  # cancelled · declined
