"""run_turn 결과 → 사용자에게 보일 안내 문구 (LLM 없음 · 금액 계산 없음)

- 분석 결과 본문은 소비 에이전트가 answer.compose 로 만든 reply 를 그대로 쓴다. 예산 · 정책 에이전트의 reply 도 그대로 둔다
  (세부 대화 문구는 각 에이전트 몫 — 여기서 만들지 않는다).
- 여기서는 시작 · 되묻기 · 미지원 · 전환 · 보류 · 취소 · 실패 문구만 [사유] + [질문 줄기] + [선택지] 로 조합한다 (MEMO 4번 원칙).
  카테고리 이름 · 선택지는 자리에 넣고, 카테고리 · 문장마다 따로 만들지 않는다.
- 확정 문구 출처: 범위 밖 · 불명확 · 라우터 실패 = chat.md 의도 표, 현재 예산 기간 질문 = MEMO 3번, 시작 인사 = 채팅 화면(AssistantPage),
  기능 선택지 이름 = chat/router.py CHOICES. 나머지는 [임시] 문구 (MEMO 18번).
- 미정 정책에 걸린 코드는 문구를 만들지 않고 undecided 로 돌려준다 (MEMO 18번 목록).
"""
from chat.router import CHOICES
from spending_analysis.answer import NAMES, josa
from spending_analysis.dialog import REPICK

from . import CANCEL_WORDS

# 의도 선택지: 기존 API 선택값(spending · budget)과 이름. 정책은 API 합의 전이라 [임시] 값 · 이름
CHOICE = {"spending": CHOICES[0], "budget_adjust": CHOICES[1], "policy": {"value": "policy", "label": "청년 정책"}}

OPENING = {"lines": ["안녕하세요! 무엇을 도와드릴까요?"], "hint": "아래에서 고르거나 하고 싶은 말을 바로 적어 주세요."}

ASK = {
    "intent": "무엇부터 도와드릴까요?",
    "cancel_target": "무엇을 취소할까요?",
    "budget_task_choice": "진행 중인 예산 조정이 있어요. 어떻게 할까요?",
    "detail": "조금 더 자세히 적어 주세요.",
    "months": "몇 개월을 볼까요? 최근 3개월까지 볼 수 있어요.",
    "category": "'{words}'{words_obj} 어느 카테고리로 조회할까요?",
    "period_choice": "어느 기간의 {name}{name_obj} 볼까요?",
    "category_choice": "어느 범위를 볼까요?",
    "include_choice": "어느 기준으로 {name}{name_obj} 볼까요?",
    "fallback_scope": "대신 {scope} 전체를 확인할까요?",
    "fallback_category": "대신 어느 카테고리 전체를 볼까요?",
    "scope": "어떤 기간이나 카테고리를 볼지 알려 주세요.",
    "explain_target": "어느 카테고리를 설명해 드릴까요?",
    "budget_current": "현재 예산 기간의 {name} 남은 예산을 확인할까요?",
}
# 질문 앞에 붙는 사유 (budget_current 의 사유는 사용자가 말한 기간 방식)
REASON = {
    "destination_unclear": "어느 기능에서 도와드릴지 확실하지 않아요.",
    "no_previous_analysis": "이어서 볼 이전 분석이 없어요.",
    "reference_unclear": "어떤 분석을 가리키는지 확실하지 않아요.",
    "conflicting_conditions": "말씀하신 조건이 서로 맞지 않아요.",
    "include_unclear": "진행 중인 이번 달까지 넣을지에 따라 비교하는 기간이 달라져요.",
    "detail_not_supported": "{words}에 쓴 돈만 따로 구분해서 보여드리기는 아직 어려워요.",
    "analysis_unclear": "바뀐 예산으로 볼 분석 범위가 정해지지 않았어요.",
    "recent": "남은 예산은 현재 예산 기간으로만 볼 수 있어요.",
    "prev_month": "남은 예산은 현재 예산 기간으로만 볼 수 있어요.",
    "last_month": "남은 예산은 현재 예산 기간으로만 볼 수 있어요.",
}
OPTION = {"question": "방금 질문", "budget_task": "예산 조정", "resume": "이어서 하기", "restart": "취소하고 새로 시작",
          True: "네", False: "아니요"}
UNSUPPORTED = {
    "out_of_scope": "머니쿠션은 소비 분석과 예산 조정을 도와드려요.",
    "last_month_only": "지난달 소비만 따로 보는 건 아직 지원하지 않아요.",
    "category_compare": "카테고리끼리 비교하는 건 아직 지원하지 않아요.",
    "no_budget_category": "확인 필요 거래는 예산 카테고리가 없어 남은 예산을 알려드릴 수 없어요.",
    "reference_not_available": "불러오지 못한 결과라 설명해 드릴 수 없어요.",
    "evidence_incomplete": "자료가 다 모이지 않아 이유를 설명해 드릴 수 없어요.",
}
SUPPORTED_VIEWS = "지금은 이번 달 소비, 지난달 같은 기간 대비, 최근 3개월 추이를 볼 수 있어요."
# 취소 세 가지를 구분한다: 질문 취소(라우터 · 소비가 직접) / 에이전트에 취소 요청 전달 / 서버가 확인한 작업 취소(OUTCOME)
CANCELLED = {"cancel": "방금 질문은 취소했어요.", "declined": "알겠어요. 확인하지 않을게요."}
FAILED = {
    "router": "지금은 이해하지 못했어요, 아래에서 골라 주세요.",
    "task_check_failed": "예산 조정 상태를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.",
    "agent": "지금은 처리하지 못했어요. 잠시 후 다시 말씀해 주세요.",
}
# 전환 · 보류 · 예산 변경 안내 (답변에 한 번만)
NOTICE = {
    "budget_task_suspended": "진행 중이던 예산 조정은 잠시 멈춰 뒀어요. 다시 이어갈 때 상태를 확인할게요.",
    "budget_task_saving": "예산 조정은 저장이 진행 중이에요. 결과는 확인한 뒤 알려드릴게요.",
    "policy_work_suspended": "진행 중이던 청년 정책 추천은 잠시 멈춰 뒀어요.",
    "budget_change_not_saved": "예산 변경이 저장되지 않았어요.",
    "no_budget_change_record": "이 대화에서 바뀐 예산 기록을 찾지 못했어요.",
    # 에이전트에 취소 요청만 보냈을 때 — 취소됐다고 말하지 않는다 (결과는 서버 확인 뒤 OUTCOME)
    "budget_cancel_forwarded": "예산 조정 쪽에 취소 요청을 전달했어요. 실제로 취소됐는지는 확인한 뒤 알려드릴게요.",
    "budget_cancel_for_restart": "새로 시작하기 전에 기존 예산 조정 취소부터 요청했어요. 취소가 확인되면 새 조정을 시작할게요.",
}
# 서버 어댑터가 확인한 예산 작업 결과 (record_budget_task 결과). 에이전트 말 · LLM 출력으로는 만들지 않는다
OUTCOME = {
    "canceled": "예산 조정 작업이 취소된 것을 확인했어요.",
    "saved": "예산 변경이 저장된 것을 확인했어요.",
    "failed": "예산 조정 저장이 실패한 것으로 확인됐어요.",
    "expired": "예산 조정 제안이 만료된 것으로 확인됐어요.",
    "rejected": "예산 조정 제안이 승인되지 않은 것으로 확인됐어요.",
}
# 미정 정책에 걸린 코드 → 문구 없이 그대로 돌려준다
UNDECIDED = {"policy_resume_undecided": "보류된 청년 정책 추천이 있을 때 다시 정책을 고르면 이어서 할지 새로 할지",
             "policy_cancel_undecided": "청년 정책 추천 중 '취소'가 질문만 취소인지 추천 대화 종료인지"}
# 소비 답변이 제안하는 다음 행동 → 안내 + 기능 선택지 (선택만으로 승인 · 저장하지 않는다. 기존 버튼 전환 경로 그대로)
SUGGEST = {"budget_adjust": ("필요하면 예산 조정을 요청할 수 있어요.", {"value": "budget", "label": "예산 조정하기"})}
# 이 대화에서 사용자가 고른 카테고리 별칭 (처음 기억할 때 · 기억한 대응을 쓸 때)
ALIAS = {True: "이번 대화에서는 '{word}'{obj} {name} 전체로 조회할게요.",
         False: "'{word}'{topic} 이번 대화에서 정한 대로 {name} 전체로 조회했어요."}
# answer.compose 가 결과와 함께 처리하는 안내 (여기서 다시 쓰지 않는다)
COMPOSED = {"months_capped"}


def _alias_lines(aliases):
    """별칭 안내 + 기억한 대응을 쓴 말마다 '다시 고르기' 선택지"""
    lines, choices = [], []
    for a in aliases:
        name = NAMES[a["code"]]
        lines.append(ALIAS[a["new"]].format(word=a["word"], obj=josa(a["word"], ("을", "를")), name=name,
                                            topic=josa(a["word"], ("은", "는"))))
        if not a["new"]:
            choices.append({"value": "repick", "label": REPICK.format(word=a["word"])})
    return lines, choices


def _names(codes):
    """카테고리 코드 → 화면 이름. 모르는 말 · 세부 지출({"unknown"} · {"detail"} 또는 원문)은 그대로"""
    return ", ".join((c.get("unknown") or c.get("detail")) if isinstance(c, dict) else NAMES.get(c, c) for c in codes)


def _choices(asking, options):
    if asking == "intent":
        return [dict(CHOICE[o]) for o in options]
    if asking in ("category", "explain_target", "fallback_category"):
        return [{"value": o["code"], "label": o["name"]} for o in options]
    if asking in ("period_choice", "category_choice", "include_choice", "fallback_scope"):
        return [{"value": o["value"], "label": o["label"]} for o in options]
    return [{"value": o, "label": OPTION[o]} for o in options]


def _ask(asking, reason, ask_about, options, router):
    words = _names(ask_about)
    name = words or ("소비" if asking in ("period_choice", "include_choice") else "생활비")
    scope = NAMES[options[0]["code"]] if asking == "fallback_scope" else None
    stem = ASK[asking].format(words=words, words_obj=josa(words, ("을", "를")), name=name, name_obj=josa(name, ("을", "를")),
                              scope=scope)
    lines = [REASON[reason].format(words=words), stem] if reason in REASON else [stem]
    choices = _choices(asking, options)
    if asking == "detail" and router:
        choices = [dict(c) for c in CHOICES]  # chat.md: 불명확 의도는 기능 선택지와 함께
    return {"lines": lines, "choices": choices}


def _decision(status, asking, reason, ask_about, options, router):
    """라우터 · 소비 결정의 칸 → 문구 (본문 없는 결정만)"""
    if reason in UNDECIDED:
        return {"lines": [], "choices": [], "review": [reason]}
    if status == "ask":
        return _ask(asking, reason, ask_about, options, router)
    if status == "unsupported":
        lines = [UNSUPPORTED[reason]]
        if reason in ("last_month_only", "category_compare"):
            lines.append(SUPPORTED_VIEWS)
        return {"lines": lines, "choices": [dict(c) for c in CHOICES] if reason == "out_of_scope" else []}
    if status == "cancelled":
        return {"lines": [CANCELLED[reason]], "choices": []}
    if status == "failed":
        if reason == "task_check_failed":
            return {"lines": [FAILED[reason]], "choices": []}
        return {"lines": [FAILED["router"]], "choices": [dict(c) for c in CHOICES]}  # 라우터 해석 실패 (chat.md)
    raise ValueError(f"문구 없는 결정: {status}/{reason}")


def opening():
    """대화 시작 인사 + 기능 선택지 (채팅 화면 문구 그대로)"""
    return {**OPENING, "choices": [dict(c) for c in CHOICES], "notices": []}


def _cancel_request(d):
    """에이전트에 보낸 취소 요청 → (안내 코드, 검토 코드). 라우터는 취소됐다고 기록하지 않는다"""
    h = d.handoff or {}
    if h.get("mode") != "cancel" and (h.get("text") or "").strip(" .!~") not in CANCEL_WORDS:
        return None, None
    if d.agent == "budget":
        return ("budget_cancel_for_restart" if h.get("mode") == "cancel" else "budget_cancel_forwarded"), None
    if d.agent == "policy":
        return None, "policy_cancel_undecided"
    return None, None


def render(turn, budget_outcome=None):
    """run_turn 결과 → {"lines", "choices", "notices", "review", "examples"?, "card"?}
    budget_outcome = 이번 입력에서 새로 기록된 서버 확인 결과(saved · canceled …) — 부르는 쪽이 저장소에서 읽어 넘긴다.
    review = 미정 정책이라 문구 · 동작을 정하지 않은 경로 코드(UNDECIDED). None = 연결 없는 대역에 전달만 함"""
    d, r = turn["decision"], turn["result"]
    notices = [NOTICE[n] for n in d.notices if n not in COMPOSED]
    cancel, review = _cancel_request(d)
    if cancel:
        notices.append(NOTICE[cancel])
    if budget_outcome in OUTCOME:
        notices.append(OUTCOME[budget_outcome])
    if r is None and d.handoff:
        if not notices and not review:
            return None
        out = {"lines": [], "choices": []}  # 연결 없는 대역에 전달만 함: 본문 없이 안내만
    elif r is None:
        out = _decision(d.status, d.asking, d.reason, d.ask_about, d.options, router=True)
    elif r.get("reply"):
        out = {k: v for k, v in r["reply"].items() if k not in ("notices", "suggest")}
        out.setdefault("choices", [])
        notices += list(r["reply"].get("notices") or [])
        for key in r["reply"].get("suggest", []):
            notices.append(SUGGEST[key][0])
            out["choices"] = [*out["choices"], dict(SUGGEST[key][1])]
    elif r["status"] == "failed":
        out = {"lines": [FAILED["agent"]], "choices": []}
    elif r.get("detail") is not None:  # 소비 에이전트: 본문 없는 결정(질문 · 미지원 · 취소). 공통 결과 칸만 읽는다
        detail = r["detail"]
        status = {"awaiting_input": "ask", "unsupported": "unsupported"}.get(r["status"])
        if r["status"] == "replied" and detail["reason"] in CANCELLED:
            status = "cancelled"
        out = _decision(status, detail["asking"], detail["reason"], detail["ask_about"], detail["options"], router=False) \
            if status else {"lines": [], "choices": []}  # 설명 요청 등 본문 생성기가 아직 없는 결과
        notices += [NOTICE[n] for n in detail["notices"] if n not in COMPOSED]
        if status != "unsupported" and any(x["reason"] == "no_budget_category" for x in detail["skipped"]):
            notices.append(UNSUPPORTED["no_budget_category"])
    else:
        out = {"lines": [], "choices": []}  # 예산 · 정책 에이전트가 reply 없이 답함 — 문구는 에이전트 몫
    if r and (r.get("detail") or {}).get("aliases"):
        lines, extra = _alias_lines(r["detail"]["aliases"])
        if r["detail"]["reason"] == "alias_set":  # 조회 없이 기준만 정한 답(별칭)이면 그 안내가 본문
            out = {**out, "lines": lines}
        else:
            notices = lines + notices
        out = {**out, "choices": [*out.get("choices", []), *extra]}
    return {"choices": [], **out, "notices": list(dict.fromkeys(notices)),
            "review": [*out.get("review", []), *([review] if review else [])]}
