"""메인 라우터 (단계 B: 역할별 해석 — chat/router.py 의 route_text 에는 아직 연결하지 않음)

담당 에이전트 우선
- 라우터가 직접 물은 질문(의도 선택 · 목적지 확인 · 취소 대상 · 예산 작업 이어서/새로)이 있으면 라우터가 먼저 처리한다
- 담당이 있으면 입력(원문)을 먼저 담당에게 보낸다. 라우터는 이때 LLM 을 부르지 않는다. 담당이 없으면 라우터가 분류한다
- 담당이 not_mine 이면 라우터가 원문 + 최소 맥락으로 다시 분류한다(라우터 LLM 1회). 라우터가 직접 분류해 보낸 에이전트가
  거절했거나, 같은 입력이 이미 2번 전달됐거나, 거절한 에이전트를 다시 가리키면 목적지 확인 질문으로 바꾼다
  (이 제한은 한 입력 안의 재전달만 막는다 — 서버 저장 · 외부 작업 전체의 중복 방지가 아니다)
- 기능 버튼은 목적지가 정해져 있어 라우터 분류 LLM 없이 전환 규칙만 적용한다
- 전환 때 다른 에이전트의 미완료 질문 · 작업은 보류만 한다 (삭제 · 완료 처리 없음)

공개 함수
- route(text, conversation_id, store, complete, server=None) → Decision    문장 입력
- route_button(agent, conversation_id, store, server=None) → Decision      기능 버튼 (spending · budget · policy)
- reroute(text, conversation_id, store, complete, server=None, *, declined) → Decision   not_mine 뒤 재분류
- run_turn(conversation_id, store, complete, *, text | button, agents, ...) → 턴 결과    독립 실행 진입점
- record_budget_task · pop_queued_start                                     예산 작업 서버 확인 (기존 규칙 그대로)
- current_question(store, conversation_id)                                 지금 사용자가 답할 질문 (진단 · 테스트용)

상태 (공유 저장소. 칸마다 쓰는 쪽은 하나)
- 라우터: get/set/clear = 라우터 질문, get_active/set_active = 담당, get_task/save_task = 예산 작업
  {task_ref, phase, suspended, next_start?, start_ready?}, get_budget_result/save_budget_result = 끝난 예산 작업 결과(서버 확인값),
  get_work/save_work(cid, "policy") = 정책 작업 {work_ref, suspended}
- 소비 분석 에이전트: get_agent_state(cid, "spending") · get_analysis/save_analysis (내부),
  get_shared(cid, "spending") = 소비가 공개하는 요약 {has_question, previous, items} — 라우터는 이것만 읽는다
- server: 읽기 전용 서버 어댑터. task_status(task_ref) → {"task_ref", "state", "saved_ref"?, "checked_at"}. 지금은 테스트 대역뿐
"""
import copy
import uuid
from dataclasses import asdict, replace

from . import reading, rules
from .reading import ReadFailed, RouterReading
from .rules import Decision

CANCEL_WORDS = {"취소", "됐어", "그만"}
ACTIVE = ("proposing", "awaiting_approval", "saving")
ENDED = ("saved", "failed", "canceled", "expired", "rejected")
TASK_QUESTIONS = ("cancel_target", "budget_task_choice")
NEW_TASK = {"task_ref": None, "phase": "started", "suspended": False}
MAX_DELIVERIES = 2  # 같은 입력을 에이전트에 전달하는 최대 횟수 (현재 담당 1 + 새 담당 1)
RESULT_STATUSES = ("replied", "awaiting_input", "unsupported", "ended", "failed", "not_mine")


class MemoryStateStore:
    """테스트용 대화 상태 저장소 (프로세스 메모리, 만료 없음). 실제 영속 저장 위치 · 만료는 통합 때 정한다
    칸 = (종류, 대화 ID[, 에이전트]). export/restore 는 한 대화의 칸 전체를 복사 · 되돌린다 (실패 시 상태 보존)"""

    def __init__(self):
        self.data = {}

    def _put(self, key, value):
        if value is None:
            self.data.pop(key, None)
        else:
            self.data[key] = value

    def get(self, cid):
        return self.data.get(("question", cid))

    def set(self, cid, question):
        self._put(("question", cid), question)

    def clear(self, cid):
        self._put(("question", cid), None)

    def get_active(self, cid):
        return self.data.get(("active", cid))

    def set_active(self, cid, agent):
        self._put(("active", cid), agent)

    def get_agent_state(self, cid, agent):
        return self.data.get(("agent", cid, agent))

    def save_agent_state(self, cid, agent, state):
        self._put(("agent", cid, agent), state)

    def get_shared(self, cid, agent):
        return self.data.get(("shared", cid, agent))

    def save_shared(self, cid, agent, summary):
        self._put(("shared", cid, agent), summary)

    def get_work(self, cid, agent):
        return self.data.get(("work", cid, agent))

    def save_work(self, cid, agent, work):
        self._put(("work", cid, agent), work)

    def get_task(self, cid):
        return self.get_work(cid, "budget")

    def save_task(self, cid, task):
        """task=None 이면 해제"""
        self.save_work(cid, "budget", task)

    def get_analysis(self, cid):
        return self.data.get(("analysis", cid))

    def save_analysis(self, cid, analysis):
        self._put(("analysis", cid), analysis)

    def get_budget_result(self, cid):
        return self.data.get(("budget_result", cid))

    def save_budget_result(self, cid, result):
        self._put(("budget_result", cid), result)

    def export(self, cid):
        return {k: copy.deepcopy(v) for k, v in self.data.items() if k[1] == cid}

    def restore(self, cid, saved):
        for key in [k for k in self.data if k[1] == cid]:
            del self.data[key]
        self.data.update(copy.deepcopy(saved))


# ---------- 상태 도우미 ----------

def _active(store, cid):
    """지금 담당: 진행 중(보류 아님) 예산 작업 · 정책 작업이 있으면 그 에이전트, 아니면 저장된 소비 담당"""
    task, work = store.get_task(cid), store.get_work(cid, "policy")
    if task and not task["suspended"]:
        return "budget"  # 다른 기능으로 바뀔 때 항상 보류하므로 보류 아닌 작업 = 담당
    if work and not work["suspended"]:
        return "policy"
    return "spending" if store.get_active(cid) == "spending" else None


def _spending_summary(store, cid):
    return store.get_shared(cid, "spending") or {}


def current_question(store, cid):
    """사용자가 지금 답할 질문: 라우터 질문, 없으면 담당이 소비일 때의 소비 되묻기 (진단 · 테스트용 — 소비 내부 칸을 읽는다)"""
    if store.get(cid):
        return store.get(cid)
    if _active(store, cid) == "spending":
        return (store.get_agent_state(cid, "spending") or {}).get("pending")
    return None


def _switch(store, cid, to):
    """담당을 to 로 바꾼다. 진행 중이던 다른 에이전트 작업은 보류만 (승인 · 저장 · 삭제 안 함). 소비 되묻기는 소비 칸에 그대로"""
    notices = ()
    task = store.get_task(cid)
    if to != "budget" and task and not task["suspended"]:
        store.save_task(cid, {**task, "suspended": True})
        notices += ("budget_task_saving" if task["phase"] == "saving" else "budget_task_suspended",)
    work = store.get_work(cid, "policy")
    if to != "policy" and work and not work["suspended"]:
        store.save_work(cid, "policy", {**work, "suspended": True})
        notices += ("policy_work_suspended",)
    store.set_active(cid, to)
    return notices


def _reask(question):
    return Decision("ask", question["intent"], asking=question["asking"], ask_about=tuple(question["ask_about"]),
                    options=tuple(question["options"]))


def _ask_router(store, cid, decision, request, text):
    """라우터 질문 저장 (답은 다음 입력에서 라우터가 먼저 처리)"""
    store.set(cid, {
        "request": request and asdict(replace(request, followup=None, choice=None)),
        "text": text, "intent": decision.intent, "asking": decision.asking,
        "ask_about": list(decision.ask_about), "options": list(decision.options),
    })
    return decision


def _ask_destination(store, cid, request, text, declined):
    """목적지 확인 질문. 담당 · 작업 · 되묻기는 바꾸지 않고 라우터 질문만 남긴다"""
    options = tuple(rules.INTENT_OF[a] for a in rules.AGENTS)
    decision = Decision("ask", asking="intent", options=options, reason="destination_unclear", declined=tuple(declined))
    # 저장한 요청은 답을 받을 때 다시 형식 검사를 거친다 → 의도가 비었으면 unclear 로 둔다
    return _ask_router(store, cid, decision, request if request and request.intents else RouterReading(("unclear",)), text)


def _read(text, pending, store, cid, complete, declined=()):
    """라우터 해석. 맥락은 분류에 필요한 최소한만 (소비 요약의 직전 조건 · 담당 · 예산 작업 단계)"""
    task = store.get_task(cid)
    context = {"active": _active(store, cid),
               "budget_task": task and {"phase": task["phase"], "suspended": task["suspended"]},
               "previous_spending": _spending_summary(store, cid).get("previous"),
               "declined_by": declined[-1] if declined else None}
    return reading.read(text, pending, complete, context)


# ---------- 에이전트 입력 ----------

def _input(agent, mode, text, cid, *, entry="text", work_ref=None, declined=(), **context):
    """공통 입력 (input_id · today · as_of 는 run_turn 이 붙인다)"""
    return {"agent": agent, "mode": mode, "entry": entry, "text": text, "work_ref": work_ref,
            "context": {"conversation_id": cid, "switched_from": None, "declined_by": declined[-1] if declined else None,
                        **context}}


def _spending(mode, text, cid, store, *, entry="text", declined=(), selected_from=None, notices=(), classified=False):
    """소비 분석 에이전트 전달. 예산 작업 결과는 '바뀐 예산으로' 안내에 필요한 결과 · 시각만 (서버 확인값)"""
    result = store.get_budget_result(cid)
    change = result and {"outcome": result["outcome"], "checked_at": result["checked_at"]}
    handoff = _input("spending", mode, text, cid, entry=entry, declined=declined, selected_from=selected_from,
                     budget_change=change)
    return Decision("ready", "spending", agent="spending", handoff=handoff, notices=notices,
                    declined=tuple(declined), classified=classified)


def _policy(store, cid, mode, text, entry="text", declined=(), notices=(), classified=False):
    """청년 정책 추천 에이전트 입력 (연결 계약만 — 내부 동작 · 판별은 담당자 몫)"""
    work = store.get_work(cid, "policy")
    return Decision("ready", "policy", agent="policy", notices=notices, declined=tuple(declined), classified=classified,
                    handoff=_input("policy", mode, text, cid, entry=entry, work_ref=work and work["work_ref"],
                                   declined=declined))


# ---------- 예산 조정 전달 (기존 규칙 그대로) ----------

def _check(server, task_ref):
    """읽기 전용 서버 확인. 실패 · 모양 불일치면 None (성공 · 취소 · 만료를 추정하지 않는다)
    saved 는 이 작업과 저장 결과를 잇는 saved_ref 가 있어야만 믿는다 — 현재 승인 예산이 있다는 것만으로는 안 됨"""
    try:
        status = server.task_status(task_ref)
    except Exception:  # 어댑터 미연결 · 서버 오류 종류를 모른다
        return None
    if not isinstance(status, dict) or status.get("task_ref") != task_ref or status.get("state") not in ACTIVE + ENDED:
        return None
    if status["state"] == "saved" and not status.get("saved_ref"):
        return None
    return status


def _finish(store, cid, status, task):
    """서버가 끝났다고 확인한 작업 → 진행 중 작업 해제, 결과 참조는 남긴다 (결과 이름은 서버 값 그대로)
    "새로 시작" 을 기다리던 요청: 취소로 끝났을 때만 시작 준비, 다른 결과면 시작하지 않고 결과에 남긴다"""
    queued = task.get("next_start")
    result = {"outcome": status["state"], "task_ref": status["task_ref"],
              "saved_ref": status.get("saved_ref") if status["state"] == "saved" else None,
              "checked_at": status.get("checked_at")}
    if queued and status["state"] != "canceled":
        result["unstarted_request"] = queued
    store.save_budget_result(cid, result)
    ready = queued and status["state"] == "canceled"
    store.save_task(cid, {**NEW_TASK, "start_ready": queued} if ready else None)


def _handoff(decision, mode, cid, store, task, *, entry="text", declined=(), classified=False, **context):
    """예산 조정 에이전트 입력 완성 (공통 계약 칸 + 기존 예산 칸). mode = start · continue · resume · cancel
    소비 맥락은 소비 에이전트가 공개한 요약(shared.spending)만 넘긴다"""
    context = {"selected_from": None, "shared": {"spending": store.get_shared(cid, "spending")},
               "active_task": task and {"task_ref": task["task_ref"], "phase": task["phase"]}, **context}
    base = _input("budget", mode, decision.handoff["text"], cid, entry=entry, work_ref=task and task["task_ref"],
                  declined=declined, **context)
    return replace(decision, agent="budget", classified=classified,
                   handoff={**decision.handoff, **{k: v for k, v in base.items() if k != "text"}})


def _forward(text, cid, store, task, entry="text"):
    """진행 중 조정 작업에 대한 입력 → 원문 그대로 예산 조정 에이전트에. 같은 분야 판단 · 승인 · 취소는 에이전트가 정한다"""
    decision = Decision("ready", "budget_adjust", handoff={"task": "budget_adjust", "text": text, "hints": {}})
    return _handoff(decision, "continue", cid, store, task, entry=entry)


def _resume(decision, cid, store, server, task):
    """보류한 작업 재개: 읽기 전용 확인 → 아직 진행 중이면 보류 해제, 끝났으면 결과 기록. 둘 다 에이전트가 안내"""
    status = None
    if task["task_ref"] is not None:
        status = _check(server, task["task_ref"])
        if status is None:
            return Decision("failed", "budget_adjust", reason="task_check_failed")
        if status["state"] in ENDED:
            _finish(store, cid, status, task)
        else:
            task = {**task, "phase": status["state"]}
    if status is None or status["state"] in ACTIVE:
        store.save_task(cid, {**task, "suspended": False})
    return _handoff(decision, "resume", cid, store, task, classified=True, task_state=status and status["state"])


def _budget(decision, request, cid, store, server, *, chosen=None, declined=(), entry="text"):
    """라우터가 예산 조정으로 정한 요청. 대화당 조정 작업 하나 (기존 규칙)"""
    task = store.get_task(cid)
    if task is None:
        notices = _switch(store, cid, "budget")
        store.save_task(cid, dict(NEW_TASK))
        decision = _handoff(decision, "start", cid, store, None, entry=entry, declined=declined, classified=True,
                            selected_from=chosen)
    elif request.resume:
        decision = _resume(decision, cid, store, server, task)
        if decision.status == "failed":  # 서버 확인 실패 → 대기 질문 등 어떤 상태도 바꾸지 않는다
            return replace(decision, declined=tuple(declined))
        notices = _switch(store, cid, "budget")
    else:
        # 기존 작업을 덮어쓰거나 새로 만들지 않고 어떻게 할지 묻는다 (담당 전환은 답을 받은 뒤)
        ask = Decision("ask", "budget_adjust", asking="budget_task_choice", options=("resume", "restart"),
                       declined=tuple(declined))
        return _ask_router(store, cid, ask, request, decision.handoff["text"])
    store.clear(cid)
    return replace(decision, notices=decision.notices + notices, declined=tuple(declined), reading=request)


# ---------- 분류 · 전달 ----------

def _classify(request, text, cid, store, server, *, declined=(), chosen=None):
    """라우터 분류 → 목적지 에이전트로 시작. 거절한 에이전트 재지정 · 전달 한도 초과면 목적지 확인 질문"""
    targets = rules.destinations(request)
    if len(targets) > 1:
        ask = Decision("ask", asking="intent", options=tuple(rules.INTENT_OF[a] for a in targets),
                       declined=tuple(declined), reading=request)
        return _ask_router(store, cid, ask, request, text)
    agent = targets[0] if targets else None
    if (agent and agent in declined) or len(declined) >= MAX_DELIVERIES:
        return replace(_ask_destination(store, cid, request, text, declined), reading=request)
    if agent is None:
        store.clear(cid)  # 라우터 질문을 새 요청이 대신했다 (기존과 같음). 담당 · 작업 · 되묻기는 그대로
        decision = Decision("unsupported", "unsupported", reason="out_of_scope") if "unsupported" in request.intents \
            else Decision("ask", "unclear", asking="detail")  # 대기 상태를 만들지 않는다: 다음 문장은 새 요청
        return replace(decision, declined=tuple(declined), reading=request)
    if agent == "budget":
        return _budget(rules.budget_request(request, text), request, cid, store, server, chosen=chosen, declined=declined)
    if agent == "policy" and store.get_work(cid, "policy"):
        # [미정] 보류된 정책 작업이 있을 때 새 정책 요청 — 이어서 · 새로 정하지 않고 상태를 그대로 둔다 (버튼과 같음)
        return Decision("unsupported", "policy", reason="policy_resume_undecided", declined=tuple(declined), reading=request)
    notices = _switch(store, cid, agent)
    store.clear(cid)
    if agent == "policy":
        store.save_work(cid, "policy", {"work_ref": None, "suspended": False})
        return replace(_policy(store, cid, "start", text, declined=declined, notices=notices, classified=True),
                       reading=request)
    return replace(_spending("start", text, cid, store, declined=declined, selected_from=chosen, notices=notices,
                             classified=True), reading=request)


def _cancel_word(text, cid, store, asked, active, task):
    """코드로 처리하는 취소 단어. 대상이 둘(질문 + 조정 작업)이면 묻는다. 처리할 게 없으면 None"""
    if asked and asked["asking"] == "cancel_target":
        return _reask(asked)
    owner = "router" if asked else \
        ("spending" if active == "spending" and _spending_summary(store, cid).get("has_question") else None)
    if owner and task:
        store.set(cid, {"request": None, "text": text, "intent": None, "asking": "cancel_target",
                        "ask_about": [], "options": ["question", "budget_task"], "previous": asked, "owner": owner})
        return Decision("ask", asking="cancel_target", options=("question", "budget_task"))
    if owner == "router":
        store.clear(cid)
        return Decision("cancelled", reason="cancel")
    if owner == "spending":
        return replace(_spending("cancel", None, cid, store), status="cancelled", reason="cancel")
    if task:
        return _forward(text, cid, store, task)  # 라우터가 취소됐다고 기록하지 않는다
    if active == "policy":
        return _policy(store, cid, "continue", text)  # 취소의 뜻은 정책 에이전트가 정한다
    return None


def _task_answer(text, request, cid, store, server, asked, task):
    """cancel_target · budget_task_choice 질문의 답"""
    # 이 질문의 선택지를 골랐으면 답이다. "질문만 취소" · "취소하고 새로"처럼 취소 말투(cancel)여도 고른 대상이 있으면 그 대상을 따른다
    picked = request.followup in ("answer", "cancel") and request.choice in asked["options"]
    choice = request.choice if picked else None
    if asked["asking"] == "cancel_target" and choice == "question":
        store.clear(cid)
        if asked.get("owner") == "spending":
            return replace(_spending("cancel", None, cid, store), status="cancelled", reason="cancel")
        return Decision("cancelled", reason="cancel")
    if asked["asking"] == "cancel_target" and choice == "budget_task":
        store.set(cid, asked["previous"])  # 라우터 질문이었으면 그대로 둔다 (소비 되묻기는 소비 칸에 남아 있다)
        return _forward(asked["text"], cid, store, task)
    if asked["asking"] == "budget_task_choice" and choice == "resume":
        decision = _resume(rules.budget_request(reading.parse(asked["request"]), asked["text"]), cid, store, server, task)
        if decision.status != "failed":  # 서버 확인이 실패하면 질문까지 그대로 둔다
            store.clear(cid)
            decision = replace(decision, notices=decision.notices + _switch(store, cid, "budget"))
        return decision
    if asked["asking"] == "budget_task_choice" and choice == "restart":
        # 기존 작업 취소부터 에이전트에 전달. 새 요청은 작업에 보관했다가 취소 완료가 확인되면 pop_queued_start 가 시작
        store.save_task(cid, {**task, "suspended": False,
                              "next_start": {"request": asked["request"], "text": asked["text"]}})
        store.clear(cid)
        notices = _switch(store, cid, "budget")
        cancel = Decision("ready", "budget_adjust", handoff={"task": "budget_adjust", "text": text, "hints": {}})
        return replace(_handoff(cancel, "cancel", cid, store, task, classified=True, cancel_for="restart"), notices=notices)
    return _reask(asked)


def _router_answer(text, cid, store, server, asked, task, complete):
    """라우터 질문이 있을 때: 질문의 답을 담당 에이전트보다 먼저 처리"""
    request = _read(text, asked, store, cid, complete)
    if asked["asking"] in TASK_QUESTIONS and request.followup != "new":
        return replace(_task_answer(text, request, cid, store, server, asked, task), reading=request)
    if request.followup == "cancel":
        store.clear(cid)
        return Decision("cancelled", reason="cancel", reading=request)
    if request.followup == "answer":
        chosen = rules.merge_intent(reading.parse(asked["request"]), request, tuple(asked["options"]))
        if chosen is None:
            return _reask(asked)
        return _classify(chosen, asked["text"], cid, store, server, chosen=list(asked["options"]))
    if request.followup != "new":
        return _reask(asked)  # unclear · 빈 값: 임의로 고르지 않고 같은 질문을 다시 한다
    return _classify(request, text, cid, store, server)


def route(text, conversation_id, store, complete, server=None):
    """문장 입력 → Decision. 해석 실패 · 서버 확인 실패면 상태를 건드리지 않는다"""
    cid = conversation_id
    asked, task, active = store.get(cid), store.get_task(cid), _active(store, cid)
    if text.strip(" .!~") in CANCEL_WORDS:
        cancelled = _cancel_word(text, cid, store, asked, active, task)
        if cancelled is not None:
            return cancelled
    try:
        if asked:
            return _router_answer(text, cid, store, server, asked, task, complete)
        if active == "spending":
            return _spending("continue", text, cid, store)
        if active == "budget":
            return _forward(text, cid, store, task)  # 원문 그대로. 같은 분야인지는 예산 조정 에이전트가 정한다
        if active == "policy":
            return _policy(store, cid, "continue", text)
        return _classify(_read(text, None, store, cid, complete), text, cid, store, server)
    except ReadFailed as error:
        return Decision("failed", reason=str(error))  # 실패 때는 대화 상태를 건드리지 않는다


def reroute(text, conversation_id, store, complete, server=None, *, declined):
    """담당 에이전트가 not_mine 을 돌려준 뒤 라우터가 원문 + 최소 맥락으로 다시 분류한다(라우터 LLM 1회). 실패면 상태 그대로"""
    try:
        request = _read(text, None, store, conversation_id, complete, tuple(declined))
        return _classify(request, text, conversation_id, store, server, declined=tuple(declined))
    except ReadFailed as error:
        return Decision("failed", reason=str(error), declined=tuple(declined))


def route_button(agent, conversation_id, store, server=None):
    """기능 버튼 → Decision. 목적지가 정해져 있어 라우터 분류 LLM 을 부르지 않는다. 선택만으로 조건 확정 · 예산 변경을 하지 않는다"""
    if agent not in rules.AGENTS:
        raise ValueError(f"알 수 없는 기능: {agent!r}")
    cid = conversation_id
    asked = store.get(cid)
    if asked:
        # 라우터 질문이 먼저: 의도 선택 질문의 선택지와 같은 버튼이면 답으로, 아니면 같은 질문을 다시 한다
        if asked["asking"] == "intent" and rules.INTENT_OF[agent] in asked["options"]:
            request = replace(reading.parse(asked["request"]), intents=(rules.INTENT_OF[agent],))
            return _classify(request, asked["text"], cid, store, server, chosen=list(asked["options"]))
        return _reask(asked)
    active = _active(store, cid)
    if agent == "spending":
        return _spending("start", None, cid, store, entry="button", notices=_switch(store, cid, "spending"))
    if agent == "budget":
        if active == "budget":
            return _forward(None, cid, store, store.get_task(cid), entry="button")
        request = RouterReading(("budget_adjust",))
        return _budget(rules.budget_request(request, None), request, cid, store, server, entry="button")
    if active == "policy":
        return _policy(store, cid, "continue", None, entry="button")
    if store.get_work(cid, "policy"):
        # [미정] 보류된 정책 작업이 있을 때 새로 시작할지 · 이어서 할지 — 정하지 않고 상태를 그대로 둔다
        return Decision("unsupported", "policy", reason="policy_resume_undecided")
    notices = _switch(store, cid, "policy")
    store.save_work(cid, "policy", {"work_ref": None, "suspended": False})
    return _policy(store, cid, "start", None, entry="button", notices=notices)


# ---------- 독립 실행 진입점 ----------

def _call(handler, request):
    """에이전트 호출. 예외 · 계약에 없는 결과는 failed (not_mine 으로 바꾸지 않는다). None = 연결 없는 대역(전달만 확인)"""
    try:
        result = handler(request)
    except Exception as error:  # 에이전트 내부 오류 종류를 모른다
        return {"status": "failed", "error": type(error).__name__}
    if result is None:
        return None
    if not isinstance(result, dict) or result.get("status") not in RESULT_STATUSES:
        return {"status": "failed", "error": "bad_result"}
    return result


def _record_policy(store, cid, result):
    """정책 결과 → 라우터의 정책 작업 기록 (재개 참조만 보관, 내용은 해석하지 않음)"""
    if result["status"] == "ended":
        store.save_work(cid, "policy", None)
        return
    work = store.get_work(cid, "policy") or {"work_ref": None, "suspended": False}
    store.save_work(cid, "policy", {**work, "work_ref": result.get("work_ref") or work["work_ref"]})


def _budget_result(store, cid, result, server):
    return record_budget_task(store, cid, {"task_ref": result.get("work_ref"), "ended": result["status"] == "ended"},
                              server)


def run_turn(conversation_id, store, complete, *, agents, text=None, button=None, server=None,
             today=None, as_of=None, input_id=None):
    """입력 1건(text 또는 button) → 라우터 결정 → 담당 에이전트 호출 → {"input_id", "decision", "result", "delivered"}
    - agents = {"spending" · "budget" · "policy": handler(공통 입력 dict) → 결과 | None(연결 없는 대역: 전달까지만)}
    - input_id: 진입점이 주거나 여기서 만든다 (chat API 계약에는 없는 내부 값)
    - 에이전트가 failed 면 이번 입력 전 상태로 되돌린다: 이전 작업이 사라지거나 새 작업이 시작된 것으로 남지 않는다
    - 예산 작업 성공 · 취소는 기존처럼 서버 확인(record_budget_task)으로만 기록한다"""
    if (text is None) == (button is None):
        raise ValueError("text 와 button 중 하나만")
    cid, input_id = conversation_id, input_id or uuid.uuid4().hex
    before = store.export(cid)
    decision = route_button(button, cid, store, server) if button else route(text, cid, store, complete, server)
    delivered, result = list(decision.declined), None
    while decision.handoff and decision.agent:
        agent = decision.agent
        delivered.append(agent)
        request = {**decision.handoff, "input_id": input_id, "conversation_id": cid,
                   "today": today and today.isoformat(), "as_of": as_of and as_of.isoformat()}
        result = _call(agents[agent], request) if agents.get(agent) else None
        if result is None:
            break  # 연결 없는 대역: 전달 결정까지만
        if result["status"] == "failed":
            store.restore(cid, before)
        elif result["status"] == "not_mine":
            if decision.classified or text is None or len(delivered) >= MAX_DELIVERIES:
                store.restore(cid, before)  # 전환이 정해지기 전 상태로 — 그 위에 목적지 확인 질문만
                decision, result = _ask_destination(store, cid, decision.reading, text, delivered), None
                break
            decision, result = reroute(text, cid, store, complete, server, declined=delivered), None
            continue
        elif agent == "budget":
            _budget_result(store, cid, result, server)
            start = pop_queued_start(store, cid)
            if start is not None:  # 취소 확인 뒤 보관한 새 요청을 한 번만 시작
                started = _call(agents["budget"], {**start.handoff, "input_id": input_id, "conversation_id": cid})
                if started and started["status"] not in ("failed", "not_mine"):
                    _budget_result(store, cid, started, server)
        elif agent == "policy":
            _record_policy(store, cid, result)
        break
    return {"input_id": input_id, "decision": decision, "result": result, "delivered": delivered}


# ---------- 예산 작업 기록 (기존 규칙 그대로) ----------

def record_budget_task(store, conversation_id, agent_result, server):
    """예산 조정 에이전트가 답한 뒤 부른다. agent_result = {"task_ref"?, "ended"?, "content"?, ...}
    - 성공 · 실패 · 취소는 서버 어댑터 확인값으로만 정한다. content · LLM 출력 · agent_result 의 다른 칸("server" 등)은 믿지 않는다
    - 확인 실패면 대화 상태를 바꾸지 않는다
    → "in_progress" · 끝난 상태(saved · failed · canceled · expired · rejected) · "unknown" · "previous_task_active" ·
      "ended_without_task" """
    task = store.get_task(conversation_id) or dict(NEW_TASK)
    ref = agent_result.get("task_ref")
    done = store.get_budget_result(conversation_id)
    if ref and ref != task["task_ref"] and done and done["task_ref"] == ref:
        # 이미 끝났다고 기록한 작업의 결과가 다시 와도 지금 작업 · 시작 준비를 건드리지 않는다 (start 중복 방지)
        return done["outcome"]
    if task["task_ref"] and ref and ref != task["task_ref"]:
        # 새 작업으로 바꾸려면 이전 작업이 끝났다는 서버 확인이 먼저 (대화당 하나)
        old = _check(server, task["task_ref"])
        if old is None:
            return "unknown"
        if old["state"] in ACTIVE:
            return "previous_task_active"
        _finish(store, conversation_id, old, {})  # 에이전트가 이미 새 작업을 만들었으니 보관한 새 요청으로 또 시작하지 않는다
        task = dict(NEW_TASK)
    ref = ref or task["task_ref"]
    if ref is None:
        # 서버 작업이 아직 없다 (에이전트가 묻는 중). 에이전트가 대화를 끝냈으면 변경 없이 종료
        if agent_result.get("ended"):
            # 서버 작업 취소가 아니라 조정 대화의 종료 → 결과 기록 없음. 새로 시작을 기다리던 요청이 있으면 시작 준비
            queued = task.get("next_start")
            store.save_task(conversation_id, {**NEW_TASK, "start_ready": queued} if queued else None)
            return "ended_without_task"
        store.save_task(conversation_id, task)
        return "in_progress"
    status = _check(server, ref)
    if status is None:
        return "unknown"
    if status["state"] in ENDED:
        _finish(store, conversation_id, status, task)
        return status["state"]
    store.save_task(conversation_id, {**task, "task_ref": ref, "phase": status["state"]})
    return "in_progress"


def pop_queued_start(store, conversation_id):
    """취소 완료가 확인된 "새로 시작" 요청 → mode=start 를 한 번만. 없거나 아직 확인 전이면 None"""
    task = store.get_task(conversation_id)
    if not task or not task.get("start_ready"):
        return None
    queued = task["start_ready"]
    store.save_task(conversation_id, {k: v for k, v in task.items() if k != "start_ready"})
    decision = rules.budget_request(reading.parse(queued["request"]), queued["text"])
    return _handoff(decision, "start", conversation_id, store, None)
