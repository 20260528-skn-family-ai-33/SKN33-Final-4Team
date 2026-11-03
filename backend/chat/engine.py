"""새 챗봇 엔진 (MEMO 22 · 25절) — 라우터(run_turn) + 소비 분석 에이전트 + 예산 · 정책 '준비 중'. chat.api.messages 가 settings.CHAT_ENGINE
= "router" 일 때만 쓴다 (기본 "legacy" = 기존 router.py · replies.py 그대로). 더미 실행(spending_fixture.service_settings)에서 켠다.

기존 chat/api.py 와 같은 계약 · 같은 도구: 로그인 · @idempotent(뷰) · 본문 검사(_check_message_body) · 본인의 진행 중 대화만(_active_conversations) ·
메시지 한 줄 모양(message_item). 대화 조회(GET)는 기존 뷰 그대로(확장 content 도 그대로 돌려준다).
- 대화 상태(라우터 · 소비 에이전트 칸 전체)를 Conversation.slots 에 {"schema", "rev", "state"} 로 저장 · 복원 (마이그레이션 없음)
- LLM · 분석은 DB 잠금 밖에서 하고, 저장할 때만 대화 행을 잠근다. 그사이 상태(rev)가 바뀌었으면 저장하지 않고 409 conversation_busy
- 기능 버튼(choice "spending" · "budget" · "policy") = 라우터 해석 없이 담당 에이전트로 / 답변 선택지 = choice {value, turn},
  마지막 봇 메시지의 선택지가 아니면 409 stale_choice. 기능 선택지면 버튼으로, 아니면 그 이름을 문장으로
- 봇 content(text) = 기존 {text, choices} + lead · lines · sections · notices · view · chart? · examples? · turn · agent · inputs · result
- 기준 날짜 = settings.CHAT_TODAY(고정) 또는 오늘, 해석 = settings.CHAT_LLM(fake · live) · CHAT_LLM_MAX_CALLS · CHAT_LLM_LEDGER (chat/llm)
"""
import json
from dataclasses import asdict
import uuid
from datetime import date, datetime, time
from functools import partial
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from chat.router_core import CANCEL_WORDS, MemoryStateStore, current_agent, messages as router_messages, run_turn
from finance.inputs import body_dict, reject_unknown_fields
from spending_analysis import agent as spending_agent

from . import api as chat_api
from .llm.fake import FakeReading
from .models import ChatMessage, Conversation

KST = ZoneInfo("Asia/Seoul")
SCHEMA = "router_core/1"
KEY = "conversation"  # 저장소 하나 = 대화 하나라 run_turn 의 대화 번호 자리는 고정값
BUSY = {"error": {"code": "conversation_busy", "message": "앞 메시지를 처리하는 중이에요. 잠시 후 다시 보내 주세요."}}
STALE = {"error": {"code": "stale_choice", "message": "이전 질문의 선택지예요. 가장 최근 질문에서 골라 주세요."}}
# 기능 버튼 (큰 지출 상담 → 청년 정책으로 대체, 22-12 결정). spending · budget 은 기존 chat API 선택지와 같은 값 · 이름
FEATURES = {"spending": "소비 분석", "budget": "예산 조정", "policy": "청년 정책"}
NOT_READY = {
    # 예산 설계(새로 짜기)는 추천 화면에서 한다 — 채팅은 금액을 바꾸지 않는다 (11/3)
    "budget": "예산 조정 챗봇은 준비 중이에요. 예산을 새로 짜려면 예산 화면에서 추천을 다시 받을 수 있어요.",
    "policy": "청년 정책 추천은 준비 중이에요.",
}
BUDGET_ASK = "어느 카테고리를 얼마나 조정할까요? 아래에서 골라 보내 주세요."
BUDGET_CANCELLED = "예산 조정을 그만뒀어요."


class ConversationStore(MemoryStateStore):
    """Conversation.slots ↔ 라우터 상태 저장소. 칸 (종류, 대화[, 에이전트]) → "종류" · "종류:에이전트" 문자열 키"""

    @classmethod
    def load(cls, slots):
        store, slots = cls(), slots or {}
        if slots and slots.get("schema") != SCHEMA:
            raise ValueError(f"모르는 대화 상태 형식: {slots.get('schema')!r}")
        for name, value in (slots.get("state") or {}).items():
            kind, _, agent = name.partition(":")
            store.data[(kind, KEY, agent) if agent else (kind, KEY)] = value
        store.rev = slots.get("rev", 0)
        return store

    def dump(self):
        state = {k[0] + (f":{k[2]}" if len(k) > 2 else ""): v for k, v in self.data.items()}
        return {"schema": SCHEMA, "rev": self.rev + 1, "state": state}


def not_ready(agent):
    """예산 · 정책 '준비 중' 에이전트 (작업 번호 없음 → 라우터가 서버 작업을 남기지 않는다)
    - 예산: 문장 없이 시작(기능 버튼 · 재개)하면 고르기 폼을 기다린다(awaiting_input → 담당 유지, 다음 문장이 바로 온다).
      문장이 오면(폼 문장 · 직접 입력) 금액을 해석하지 않고 '준비 중'으로 끝낸다. 취소면 그만뒀다고 끝낸다
    - 정책: 바로 '준비 중'으로 끝낸다"""
    def handle(request):
        text = request["text"]
        if request["mode"] == "cancel" or (text or "").strip(" .!~") in CANCEL_WORDS:
            return _ended(BUDGET_CANCELLED if agent == "budget" else NOT_READY[agent])
        if agent == "budget" and text is None:
            return {"status": "awaiting_input", "work_ref": None, "reply": {"lines": [BUDGET_ASK], "notices": []}}
        return _ended(NOT_READY[agent])
    return handle


def _ended(line):
    return {"status": "ended", "work_ref": None, "reply": {"lines": [line], "notices": []}}


def _value(value):
    """선택지 값은 문자열로 주고받는다 (참 · 거짓 → "true" · "false")"""
    return value if isinstance(value, str) else json.dumps(value)


def _content(bot, turn):
    """render 결과 → 봇 메시지 content (기존 text · choices 칸도 채운다 — 옛 화면은 text 만 그린다)"""
    lead, lines = bot.get("lead", []), bot.get("lines", [])
    content = {"text": "\n".join(lead + lines), "choices": bot.get("choices", []), "lead": lead, "lines": lines,
               "sections": bot.get("sections", []), "notices": bot.get("notices", []), "turn": turn,
               "agent": bot["agent"], "inputs": bot["inputs"], "result": bot["result"]}
    content.update({k: bot[k] for k in ("chart", "examples", "view") if bot.get(k)})
    if getattr(settings, "CHAT_DEV_TRACE", False) and (bot.get("trace") or bot.get("entry")):
        # 개발용 분석 과정 — 더미 설정에서만 (chat.md 계약 칸 아님). entry = B 전 단계, 그 밖 = B 흐름
        content["dev_trace"] = {**(bot.get("trace") or {}), "entry": bot.get("entry")}
    return content


def _route(turn):
    """기존 응답의 route 칸: 실제로 답한 에이전트 · 범위 밖 · 불명확. fallback = 라우터 해석 실패"""
    d, r = turn["decision"], turn["result"] or {}
    if r.get("status") == "failed" and r.get("error") in ("call", "call_limit"):  # 에이전트 해석 LLM 실패 · 상한 — 라우터 실패와 같이 (MEMO 25-12)
        return {"intent": "unknown", "slots": {}, "fallback": True}
    if turn["delivered"]:
        return {"intent": turn["delivered"][-1], "slots": {}, "fallback": False}
    if d.status == "unsupported" and d.reason == "out_of_scope":
        return {"intent": "out", "slots": {}, "fallback": False}
    return {"intent": "unknown", "slots": {}, "fallback": d.status == "failed"}


_live = None


def reading():
    """settings.CHAT_LLM = "live" 면 실제 LLM 하나를 서버 동안 같이 쓴다 (키 · 모델은 환경변수, 상한 = CHAT_LLM_MAX_CALLS(None = 없음),
    장부 = CHAT_LLM_LEDGER(None = 메모리 수만)). 그 밖(값 없음 포함)은 요청마다 가짜 해석 — 안전한 기본값"""
    global _live
    if getattr(settings, "CHAT_LLM", "fake") != "live":
        return FakeReading()
    if _live is None:
        from .llm.live import LiveReading  # fake · 첫 화면 조회에서는 실제 LLM 의존성을 불러오지 않는다
        _live = LiveReading(getattr(settings, "CHAT_LLM_MAX_CALLS", None), getattr(settings, "CHAT_LLM_LEDGER", None))
    return _live


def today_and_as_of():
    """기준 날짜 · 조회 시각: settings.CHAT_TODAY 가 있으면 그날 23:59:59 KST 로 고정(더미 = 2026-10-29), 없으면 지금(KST)"""
    fixed = getattr(settings, "CHAT_TODAY", None)
    if fixed:
        day = date.fromisoformat(fixed)
        return day, datetime.combine(day, time(23, 59, 59), KST)
    now = datetime.now(KST)
    return now.date(), now


def answer(user, store, text=None, button=None):
    """한 입력 → (render 결과, route). DB 를 잠그지 않는다 (가짜 · 실제 LLM 호출과 분석은 여기서)"""
    llm = reading()
    agents = {"spending": partial(spending_agent.handle, store=store, complete=llm, user=user,
                                  explain_complete=llm if llm.explains else None,
                                  # B 증감 설명(MEMO 26) — CHAT_EXPLAIN_PLAN 이 켜졌을 때만, 해석과 같은 LLM(장부 · 상한 공유)
                                  explain_plan=llm if getattr(settings, "CHAT_EXPLAIN_PLAN", False) else None),
              "budget": not_ready("budget"), "policy": not_ready("policy")}
    today, as_of = today_and_as_of()
    turn = run_turn(KEY, store, llm, text=text, button=button, agents=agents, today=today, as_of=as_of)
    result = turn["result"] or {}
    if "budget_adjust" in (result.get("reply") or {}).get("suggest", []):
        # [예산 조정하기] 제안은 1차에서 숨긴다 (예산은 '준비 중'이라 눌러도 진행되지 않음, 22-7). 다른 제안은 그대로
        kept = [k for k in result["reply"]["suggest"] if k != "budget_adjust"]
        result["reply"] = {**{k: v for k, v in result["reply"].items() if k != "suggest"}, **({"suggest": kept} if kept else {})}
    bot = router_messages.render(turn) or {"lines": [], "choices": [], "notices": [], "review": []}
    bot.pop("review", None)  # 미정 정책 코드는 화면에 보내지 않는다
    bot["choices"] = [{"value": _value(c["value"]), "label": c["label"]} for c in bot["choices"]]
    # 담당 표시: 진행 중인 담당, 없으면 이번에 답한 에이전트 (문장으로 추측하지 않는다)
    answered = turn["delivered"][-1] if turn["delivered"] and result.get("status") not in (None, "failed", "not_mine") else None
    now = current_agent(store, KEY)
    bot.update(agent=now or answered, inputs=["budget_form"] if now == "budget" else [],
               result=answered == "spending" and result.get("status") == "replied" and bool(result.get("reply")))
    if getattr(settings, "CHAT_DEV_TRACE", False):
        bot["entry"] = _entry(result.get("decision"))
    return bot, _route(turn)


def _entry(decision):
    """개발용 분석 과정의 B 전 단계 — 소비 해석 칸 값(채운 것만) · dialog 갈림길 · 설명 요청 판정 (26-20, 금액 없음)"""
    if decision is None:
        return None
    interp = decision.interpretation
    filled = {k: v for k, v in (asdict(interp) if interp else {}).items() if v not in (None, (), [], False)}
    return {"reading": filled, "decision": {"status": decision.status, "reason": decision.reason, "asking": decision.asking},
            "items": [{k: i.get(k) for k in ("type", "category") if i.get(k) is not None} for i in decision.items],
            "explain": bool(decision.explain)}


def _check_body(request):
    """→ (conversation_id, text, feature, choice). 기존 계약(문장 · 기능 선택지 spending · budget)은 기존 검사 그대로,
    더미 실행에서 넓힌 것: 기능 선택지 policy, 답변 선택지 {value, turn} (이어서 말하는 대화에서만)"""
    data = body_dict(request)
    choice = data.get("choice")
    if not (isinstance(choice, dict) or choice == "policy"):
        conversation_id, text, feature = chat_api._check_message_body(request)
        return conversation_id, text, feature, None
    reject_unknown_fields(data, {"conversation_id", "choice"})
    raw_id = data.get("conversation_id")
    if choice == "policy":
        return _conversation_id(raw_id, required=False), None, "policy", None
    ok = set(choice) == {"value", "turn"} and isinstance(choice["value"], str) and 0 < len(choice["value"]) <= 100 \
        and type(choice["turn"]) is int and choice["turn"] > 0
    if not ok:
        raise ValidationError({"choice": ["알 수 없는 선택지예요."]})
    return _conversation_id(raw_id, required=True), None, None, choice


def _conversation_id(raw, required):
    if raw is None:
        if required:
            raise ValidationError({"conversation_id": [chat_api.CONVERSATION_ERROR]})
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise ValidationError({"conversation_id": [chat_api.CONVERSATION_ERROR]})


def _picked(talk, choice):
    """답변 선택지 → 그 이름. 마지막 봇 메시지의 turn · 선택지가 아니면 None (지난 질문의 선택지)"""
    last = talk.messages.filter(role=ChatMessage.Role.BOT).order_by("-created_at", "-message_id").first()
    content = last and last.content or {}
    if content.get("turn") != choice["turn"]:
        return None
    return next((c["label"] for c in content.get("choices", []) if c["value"] == choice["value"]), None)


def respond(request):
    """chat.api.messages 가 CHAT_ENGINE = "router" 일 때 부른다 (인증 · @idempotent 는 그 뷰에 그대로).
    {"conversationId": null | "<id>", "text": "…" | "choice": "spending" | "budget" | "policy" | {"value", "turn"}}
    응답은 기존 봉투 + agent(지금 담당). 지난 선택지 409 stale_choice · 처리 중 상태가 바뀌면 409 conversation_busy (둘 다 저장 없음)"""
    conversation_id, text, feature, choice = _check_body(request)
    talk = None
    if conversation_id is not None:
        talk = chat_api._active_conversations(request.user).filter(pk=conversation_id).first()
        if talk is None:
            raise ValidationError({"conversation_id": [chat_api.CONVERSATION_ERROR]})
    said = text if text is not None else FEATURES.get(feature)
    if choice is not None:
        said = _picked(talk, choice)
        if said is None:
            return Response(STALE, status=409)
        feature = choice["value"] if choice["value"] in FEATURES else None  # 의도 선택 · 범위 밖 답변의 기능 선택지
        text = None if feature else said
    store = ConversationStore.load(talk and talk.slots)
    bot, route = answer(request.user, store, text=text, button=feature)

    with transaction.atomic():
        if talk is None:
            talk = Conversation.objects.create(user=request.user)
        else:
            talk = chat_api._active_conversations(request.user).select_for_update().filter(pk=talk.pk).first()
            if talk is None:
                raise ValidationError({"conversation_id": [chat_api.CONVERSATION_ERROR]})
            if (talk.slots or {}).get("rev", 0) != store.rev:  # 같은 대화의 다른 메시지가 먼저 저장됨
                return Response(BUSY, status=409)
        mine = ChatMessage.objects.create(
            user=request.user, conversation=talk, role=ChatMessage.Role.USER, content_type=ChatMessage.ContentType.TEXT,
            content={"text": said, "choices": []})  # 선택지는 화면에 보인 이름으로 남긴다 (기존과 같음)
        reply = ChatMessage.objects.create(
            user=request.user, conversation=talk, role=ChatMessage.Role.BOT, content_type=ChatMessage.ContentType.TEXT,
            content=_content(bot, store.rev + 1))
        talk.slots, talk.active_agent = store.dump(), store.get_active(KEY)
        talk.save(update_fields=["slots", "active_agent"])
    return Response({"conversation_id": talk.conversation_id, "route": route, "agent": bot["agent"],
                     "messages": [chat_api.message_item(mine), chat_api.message_item(reply)], "task": None,
                     **chat_api.engine_info()})
