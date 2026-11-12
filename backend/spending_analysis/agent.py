"""소비 분석 에이전트 진입점: 공통 입력(원문 + 맥락) → 자체 해석 · 결정 · 분석 실행 · 답변 · 맥락 기록 → 공통 결과

라우터의 run_turn 이 agents["spending"] 으로 부른다: functools.partial(handle, store=..., complete=..., user=...)
"""
from dataclasses import replace
from datetime import date, datetime

from django.db import DatabaseError

from . import adapter, agent_loop, agent_tools, claims, dialog, explain, investigation, tools
from .answer import compose, over_target

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


def _today(request):
    return request.get("today") and date.fromisoformat(request["today"])


def _merchant(user, request):
    """가게 이름 검색 가능 여부 (dialog 는 DB 를 모른다). 조회 실패면 검색하지 않고 기존 '카테고리 전체로 볼까요?'로"""
    def known(word, names=()):
        if not _today(request):  # 기준 날짜가 없는 대역(평가 · 테스트)은 가게 이름 검색 없이
            return False
        try:
            return tools.has_merchant(user, word, _today(request), names)
        except DatabaseError:
            return False
    return known


def _run(user, request):
    today = _today(request)
    as_of = _as_of(request)
    return lambda decision: adapter.run(user, decision, today=today, as_of=as_of)


def handle(request, *, store, complete, user=None, execute=None, explain_plan=None, agent_complete=None):
    """request = 공통 입력 {agent: "spending", mode, entry, text, input_id, conversation_id, today, as_of, context}
    context = {selected_from?, budget_change?: {outcome, checked_at}, switched_from?, declined_by?, budget_targets?: 넘겨 둔 조정이 늘리려는 카테고리,
               suggested_category?: 예산 조정이 넘기며 알려 준 카테고리, budget_return?: 넘겨 둔 예산 조정이 있음}
    → {"status": replied | awaiting_input | unsupported | failed | not_mine, "reply", "detail", "reason", "error", ...}
      초과 답변(reply.suggest 에 "budget_adjust")이면 "over": {"category", "over_amount"} 를 더한다 — 라우터가 [예산 조정하기]에 싣는다
    - not_mine · 해석 실패는 상태를 바꾸지 않는다. 분석 한 번이 끝나도 ended 가 아니다(직전 분석은 남는다)
    - 모든 항목 조회 실패면 failed (라우터가 이번 입력 전 상태로 되돌린다). 일부 실패는 replied + 항목별 안내
    - execute(decision) → run_items 결과 (기본: 실제 분석). None 을 돌려주면 실행을 건너뛴다(평가 · 테스트 대역)
    - 설명 요청은 서버가 거래 근거를 계산해 짧은 서버 설명으로 답한다. user 가 없으면(라우터 · 평가 대역) 설명 근거를 만들지 않는다
    - explain_plan(B 계획 · 답 LLM, MEMO 26)은 사용자가 원인 가설(hypothesis)을 말한 설명에만 쓴다. 실패하면 서버 설명 그대로
    - agent_complete(추가 판단 LLM, MEMO 31)를 주면 첫 조회(기존 경로) 뒤 필요한 경우에만 판단 1회 → 추가 도구 최대 1회.
      '비교 + 나누기'(compare_query)는 비교만 기존 경로로 먼저 하고 나누기 기준을 남은 요구로 넘긴다"""
    cid, text, mode = request["conversation_id"], request["text"], request["mode"]
    context = request.get("context") or {}
    asked = None
    if mode == "cancel":
        dialog.cancel_question(store, cid)
        decision = dialog.Decision("cancelled", reason="cancel", agent=dialog.AGENT)
    elif text is None:
        decision = dialog.start_button(store, cid)
    else:
        asked = mode != "start" and dialog.pending(store, cid)  # 되묻기의 답이면 판단에는 원래 질문을 넘긴다 (step 이 대기를 지우기 전에)
        decision = dialog.step(store, cid, text, complete, mode="start" if mode == "start" else "continue",
                               chosen=bool(context.get("selected_from")), budget_change=context.get("budget_change"),
                               merchant=user and _merchant(user, request), today=_today(request),
                               suggested_category=context.get("suggested_category"))
    if decision.status == "not_mine":
        return _result("not_mine", reason=decision.reason, decision=decision)
    if decision.status == "failed":
        return _result("failed", error=decision.reason, decision=decision)
    if decision.status == 'ready' and (decision.explain or {}).get('kind') == 'allocation' and user is not None:
        from budgets.budget_explanation import explain_current_budget
        try:
            lines = explain_current_budget(user, [item['category'] for item in decision.items if item.get('category')], _today(request))
        except DatabaseError:
            return _result('failed', error='analysis_failed', decision=decision)
        dialog.publish(store, cid)
        return _result('replied', {'lines': lines, 'notices': []}, decision)
    judging = agent_complete is not None and user is not None and mode != "cancel" and text is not None
    need = note = None
    question = text
    if judging and asked and decision.interpretation and decision.interpretation.followup == "answer":
        question = asked["text"]
    if text is not None and user is not None and mode != "cancel" and (judging or explain_plan is not None):
        split = _split(decision, question, user, request, judging=judging, b_path=explain_plan is not None)
        if split is not None:
            decision, need, note = split
            dialog.cancel_question(store, cid)  # 예산 + 비교를 예산 되묻기로 저장했으면 지운다 (비교부터 하므로)
    ran = []
    run = execute or _run(user, request)

    def keep(d):  # 첫 조회 결과를 판단 단계가 다시 실행하지 않고 쓰게 남긴다
        ran.append(run(d))
        return ran[-1]

    result = _outcome(decision, store, cid, keep,
                      user and (lambda items, question=None, hypothesis=None: explain.answer(
                          user, items, today=_today(request), question=question, hypothesis=hypothesis,
                          plan=explain_plan if hypothesis is not None else None)))  # B 는 가설 질문에만
    if judging and result["status"] == "replied" and result.get("reply") and decision.items and ran and ran[-1] \
            and (need is not None or _multi_step(decision)):
        result = _judged(result, decision, ran[-1], question, need, user, request, agent_complete)
    if note and result.get("reply"):  # 가설 경로로 나누며 뺀 나누기 — 확인하지 못했다고 밝힌다
        result["reply"] = {**result["reply"], "notices": [*result["reply"].get("notices", []), note]}
    if context.get("budget_return") and result["status"] == "replied" and result.get("reply"):
        source = _take_from(decision)
        # 넘겨 둔 예산 조정이 있고 카테고리 하나의 남은 예산을 답했다 → [○○에서 가져오기] (문구 · 버튼은 라우터 안내가 붙인다).
        # 그 조정이 늘리려는 항목 자체(budget_targets)는 재원이 될 수 없으니 제안하지 않는다 — 누르면 예산 조정이 거절한다 (PR #111 리뷰)
        if source and source not in (context.get("budget_targets") or ()):
            result["reply"] = {**result["reply"], "suggest": [*result["reply"].get("suggest", []), {"take_from": source}]}
    if context.get("budget_return") and (result.get("reply") or {}).get("alt"):
        # 넘겨 둔 예산 조정이 있는 대화 — 예산 조정과 같은 기준인 예산 주기를 먼저 보여 준다 (화면 전환은 그대로 가능)
        result["reply"] = {**result["reply"], "alt": {**result["reply"]["alt"], "first": True}}
    if result["status"] == "replied" and "budget_adjust" in (result.get("reply") or {}).get("suggest", []):
        # 초과 답변에 [예산 조정하기]가 붙었다 → 어느 카테고리가 얼마 넘었는지 라우터에 알린다 (화면 응답에는 넣지 않는다, 11/11)
        target = over_target([r for results in ran if results for r in results])
        if target:
            result["over"] = target
    dialog.publish(store, cid)
    return result


LOOKUP_TYPES = {"budget_lookup": (3, 4)}


def _take_from(decision):
    """카테고리 하나의 남은 예산 조회(3번)만 한 답이면 그 카테고리 코드. 그 밖(전체 · 여러 개 · 소비 조회)은 None"""
    items = decision.items
    if len(items) == 1 and items[0]["type"] == 3 and isinstance(items[0].get("category"), str):
        return items[0]["category"]
    return None


def _multi_step(decision):
    """해석이 '조회 한 번으로 못 끝나는 새 질문'(multi_step)이라고 했는지. 이어서 묻는 질문이 새 조건 없이 true 를 달고 온 경우는 아니다
    (되묻기의 답은 원래 요청(decision.request)의 값을 본다 — 조건이 확정된 뒤 판단)"""
    interp, req = decision.interpretation, decision.request or decision.interpretation
    if interp is None or interp.refers_to == "unclear" or req.multi_step is not True:
        return False
    return not (interp.refers_to == "previous" and interp.target is None and interp.compare is None)


# 나누기 기준별로 지킬 수 있는 계산. 요일: 합계(sum) · 하루 평균(daily_avg, 말하지 않으면 이것 — 두 기간의 요일 수가 달라서) 둘 다
# claims.compare_group 이 계산한다. 다른 기준의 하루 평균은 지킬 수 없어 나누지 않는다(기존 미지원 안내)
SPLIT_MEASURES = {"weekday": (None, "sum", "daily_avg")}


def _split(decision, text, user, request, judging=True, b_path=False):
    """지금 경로가 한 번에 못 하는 두 단계 질문을 나눈다 → (Decision, need | None, 안내 | None) | None.
    기간 · 비교 기간 · 카테고리 · 필터는 그대로. 나눈 뒤에도 바로 실행할 수 없으면 나누지 않는다(기존 안내 · 되묻기 그대로)
    ① 비교 + 나누기(compare_query, judging): group_by · measure 를 빼고 비교, 나누기 기준 + 지킬 계산은 need. 가설 없는 explain 도 같이
       (실제 해석이 '어느 시간대에서 늘었어?'에 explain=true 를 붙였다)
    ② 가설 + 나누기(compare_query, b_path = B 켜짐): '배달 때문에 저녁에 늘었어?'가 hypothesis + group_by 로 읽히면 B 로 —
       group_by 만 빼고 가설 · 설명은 그대로 다시 정한다. 판단 단계와 섞지 않는다. 뺀 나누기를 가설 대상이 다루지 않으면 못 봤다고 안내
    ③ 예산 + 소비 비교(budget_left + 지난달 · 달 비교 + multi_step, judging): 같은 범위 소비 비교를 먼저,
       남은 예산은 질문의 '예산' 요구로 판단 단계에서. multi_step 없는 '지난달보다 예산 많이 남았어?'는 기존 되묻기 그대로"""
    req = decision.request or decision.interpretation
    if req is None:
        return None
    note = None
    if decision.status == "unsupported" and decision.reason == "compare_query" and req.hypothesis is not None:
        if not b_path or req.group_by not in claims.AXES:
            return None
        need, base = None, replace(req, group_by=None, measure=None)
        target = investigation.TARGETS.get(req.hypothesis["target"], {})
        if target.get("by") != req.group_by:  # 가설 대상('배달앱' = 가게)이 뺀 나누기(시간대)를 다루지 않는다
            note = agent_loop.unmet_line({"tool": "where_changed", "by": req.group_by})
    elif decision.status == "unsupported" and decision.reason == "compare_query" and judging:
        if req.compare not in ("prev_month", "month") or req.group_by not in claims.AXES or req.top_n is not None \
                or req.measure not in SPLIT_MEASURES.get(req.group_by, (None, "sum")):
            return None
        need = {"tool": "where_changed", "by": req.group_by,
                **({"measure": req.measure} if req.group_by == "weekday" and req.measure in ("sum", "daily_avg") else {})}
        base = replace(req, group_by=None, measure=None, explain=None, explain_kind=None)
    elif judging and decision.status == "ask" and decision.asking == "budget_current" and req.target == "budget_left" \
            and req.compare in ("prev_month", "month") and req.multi_step is True and req.hypothesis is None:
        need, base = None, replace(req, target="spend")
    else:
        return None
    split = dialog.decide(replace(base, refers_to=None), text, merchant=_merchant(user, request), today=_today(request))
    if req.hypothesis is not None:  # B: 설명 결정(항목 없음, explain 에 참조 항목)이어야 한다
        if split.status != "ready" or not split.explain:
            return None
    elif split.status != "ready" or not split.items or any(i["type"] not in (6, 7) for i in split.items):
        return None
    return replace(split, interpretation=decision.interpretation, request=base, agent=dialog.AGENT, aliases=decision.aliases), need, note


def _asks_budget(text):
    # shortcut: 예산을 물었는지는 질문의 '예산' 낱말로만 본다 — 해석 칸이 생기면 그걸로 바꾼다
    return "예산" in (text or "")


def _judged(result, decision, results, text, need, user, request, complete):
    """첫 조회 결과 → 판단 1회 · 추가 도구 최대 1회 (agent_loop.run). 비교 결과가 없으면(자료 부족 · 비교 불가) 판단하지 않고
    남은 요구만 '확인하지 못했어요'로 밝힌다. 기록은 첫 조회 것 그대로 (추가 조회는 기록하지 않는다)"""
    first = {"id": "R1", **agent_tools.observe("first", decision, results)}
    budget_seen = any(r["item"]["type"] in LOOKUP_TYPES["budget_lookup"] for r in results)
    needs = [need] if need else []
    if _asks_budget(text) and not budget_seen:
        needs.append({"tool": "budget_lookup"})
    compared = first["status"] == "ok" and agent_tools.comparisons(first)
    allowed = [t for t, ok in (("where_changed", compared and len(compared) == 1),
                               ("budget_lookup", _asks_budget(text) and not budget_seen)) if ok]
    if first["status"] != "ok" or not compared:
        allowed = []  # 비교할 수 없으면 추가 분석을 하지 않는다 (확인 가능한 결과 + 제한 안내)
        needs = needs or [{"tool": None}]  # multi_step 인데 무엇을 못 봤는지 특정 못 함 — '나머지는 확인하지 못했어요'
    interp = decision.interpretation
    out = agent_loop.run(text, first, request=decision.request or interp, needs=needs, allowed=allowed, user=user,
                         today=_today(request), as_of=_as_of(request), complete=complete,
                         follow_up=bool(interp and interp.refers_to == "previous"))
    reply = {**out["reply"], "trace": {**(result["reply"].get("trace") or {}), "agent": out["trace"]}}
    return {**result, "reply": reply}


def _as_of(request):
    return request.get("as_of") and datetime.fromisoformat(request["as_of"])


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
            items, records = adapter.prepare_explanation(decision, execute)
            reply = explainer(items, decision.explain["question"], decision.explain.get("hypothesis"))
        except (DatabaseError, adapter.AnalysisFailed):
            return _result("failed", decision=decision, error="analysis_failed")
        if records is not None:
            dialog.record_analysis(store, cid, decision, records)
        return _result("replied", reply, decision)
    if decision.status == "ask":
        return _result("awaiting_input", START if decision.reason == "start" else None, decision)
    if decision.status == "unsupported":
        return _result("unsupported", decision=decision, reason=decision.reason)
    return _result("replied", decision=decision, reason=decision.reason)  # cancelled · declined
