"""새 챗봇 엔진 — 라우터(run_turn) + 소비 분석·예산 조정 에이전트. 정책은 준비 중. chat.api.messages 가 settings.CHAT_ENGINE
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
from copy import deepcopy
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
OVER_HINT_KEYS = {"reason", "categories", "over_amount"}
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
    content.update({k: bot[k] for k in ("chart", "examples", "view", "budget_adjustment") if bot.get(k)})
    if getattr(settings, "CHAT_DEV_TRACE", False) and (bot.get("trace") or bot.get("entry")):
        # 개발용 분석 과정 — 더미 설정에서만 (chat.md 계약 칸 아님). entry = B 전 단계, 그 밖 = B 흐름
        content["dev_trace"] = {**(bot.get("trace") or {}), "entry": bot.get("entry")}
    return content


def _route(turn):
    """기존 응답의 route 칸: 실제로 답한 에이전트 · 범위 밖 · 불명확. fallback = 라우터 해석 실패"""
    d, r = turn["decision"], turn["result"] or {}
    if r.get("status") == "failed" and r.get("error") in ("call", "call_failed", "call_limit"):  # 에이전트 해석 LLM 실패 · 상한 — 라우터 실패와 같이 (MEMO 25-12)
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


def answer(user, store, text=None, button=None, *, budget_adapter=None, budget_hints=None, suggested=False):
    """한 입력 → (render 결과, route). DB 를 잠그지 않는다 (가짜 · 실제 LLM 호출과 분석은 여기서)
    budget_hints = 예산 화면이 넘긴 초과 항목 시작 정보 (_entry_hints). 예산 조정 버튼에만 쓴다
    suggested = 이번 버튼이 직전 답변의 선택지를 누른 것 — 직전이 소비 분석 초과 답변이면 라우터가 그 카테고리를 hints 로 만든다"""
    llm = reading()
    from budgets.adjustment_service import AdjustmentAdapter
    from .budget_adjustment import handler as budget_handler
    today, as_of = today_and_as_of()
    budget_adapter = budget_adapter or AdjustmentAdapter(user, today=today)
    agents = {"spending": partial(spending_agent.handle, store=store, complete=llm, user=user,
                                  # B 가설 확인(MEMO 26) — CHAT_EXPLAIN_PLAN 이 켜졌을 때 가설을 말한 설명 질문에만, 해석과 같은 LLM(장부 · 상한 공유)
                                  explain_plan=llm if getattr(settings, "CHAT_EXPLAIN_PLAN", False) else None,
                                  # 첫 조회 뒤 추가 판단 1회(MEMO 31) — CHAT_AGENT 가 켜졌을 때만. 가짜 해석도 정해 둔 판단으로 돈다
                                  agent_complete=llm if getattr(settings, "CHAT_AGENT", False) else None),
              "budget": budget_handler(user, store, budget_adapter, llm, KEY), "policy": not_ready("policy")}
    turn = run_turn(KEY, store, llm, text=text, button=button, agents=agents, server=budget_adapter, today=today, as_of=as_of,
                    hints=budget_hints, suggested=suggested)
    result = turn["result"] or {}
    if result.get("error") == "budget_adjust_consent_required":
        from budgets.ai_consent import ConsentRequired
        raise ConsentRequired()
    # 실패 시에도 소모한 호출 수와 한 번 조회한 ML 참조는 되돌리지 않는다.
    budget_state = store.get_agent_state(KEY, "budget") or {}
    if budget_adapter.llm_call_count and (budget_state or result.get("status") == "failed"):
        budget_state["llm_call_count"] = budget_adapter.llm_call_count
        if budget_adapter.reference_context is not None:
            budget_state["reference_context"] = deepcopy(budget_adapter.reference_context)
        store.save_agent_state(KEY, "budget", budget_state)
    bot = router_messages.render(turn) or {"lines": [], "choices": [], "notices": [], "review": []}
    bot.pop("review", None)  # 미정 정책 코드는 화면에 보내지 않는다
    bot["choices"] = [{"value": _value(c["value"]), "label": c["label"]} for c in bot["choices"]]
    # 담당 표시: 진행 중인 담당, 없으면 이번에 답한 에이전트 (문장으로 추측하지 않는다)
    answered = turn["delivered"][-1] if turn["delivered"] and result.get("status") not in (None, "failed", "not_mine") else None
    now = current_agent(store, KEY)
    bot.update(agent=now or answered, inputs=[],
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


def _entry_hints(raw):
    """예산 화면이 넘긴 초과 정보 (11/11): {"reason": "over", "categories": [카테고리 코드 1개], "over_amount": 양의 정수}.
    카테고리는 생활비 배분 대상만 (예산 밖 주거·통신 · 여행·숙박과 기타는 초과 항목 조정을 시작하지 않는다 — 예산 조정 에이전트와 같은 기준).
    over_amount 는 참고값이다 — 증액 금액으로 확정하지 않고, 예산 조정 에이전트가 서버에서 최신 초과액을 다시 확인한다.
    모양이 조금이라도 다르면 400 (화면이 금액 · 슬롯을 임의로 주입하지 못하게 칸을 정확히 맞춘다)"""
    from budgets.recommend import ALLOCATION_CODES
    ok = (isinstance(raw, dict) and set(raw) == OVER_HINT_KEYS and raw["reason"] == "over"
          and isinstance(raw["categories"], list) and len(raw["categories"]) == 1
          and isinstance(raw["categories"][0], str) and raw["categories"][0] in ALLOCATION_CODES
          and type(raw["over_amount"]) is int and raw["over_amount"] > 0)
    if not ok:
        raise ValidationError({"budget_context": ["초과한 카테고리 정보가 올바르지 않아요."]})
    return {"reason": "over", "categories": [raw["categories"][0]], "over_amount": raw["over_amount"]}


def _hinted_label(hints):
    """초과 카테고리로 시작한 대화의 사용자 말풍선: "식비 예산 조정" (카테고리 이름을 못 찾으면 기본 "예산 조정")"""
    from budgets.models import Category
    name = Category.objects.filter(pk=hints["categories"][0]).values_list("display_name", flat=True).first()
    return f"{name} {FEATURES['budget']}" if name else FEATURES["budget"]


def _check_body(request):
    """→ (conversation_id, text, feature, choice). 기존 계약(문장 · 기능 선택지 spending · budget)은 기존 검사 그대로,
    더미 실행에서 넓힌 것: 기능 선택지 policy, 답변 선택지 {value, turn} (이어서 말하는 대화에서만)"""
    data = body_dict(request)
    choice = data.get("choice")
    if not (isinstance(choice, dict) or choice == "policy"):
        conversation_id, text, feature = chat_api._check_message_body(request, extra_fields=("budget_context",))
        return conversation_id, text, feature, None
    reject_unknown_fields(data, {"conversation_id", "choice", "budget_context"})
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


def prepare(request):
    """입력 검사·조회·LLM은 응답 기록 트랜잭션 밖에서 준비한다."""
    conversation_id, text, feature, choice = _check_body(request)
    data = body_dict(request)
    entry = data.get("budget_context")
    budget_hints = None
    if "budget_context" in data:
        recovery = isinstance(entry, dict) and entry.get('mode') == 'DESIGN_RECOVERY'
        id_field = 'source_task_id' if recovery else 'base_budget_id'
        has_hints = isinstance(entry, dict) and "hints" in entry and not recovery
        if has_hints:
            # hints 는 기준 예산(entry_context)과 따로 둔다: 저장하지 않고 이번 시작 요청에만 쓴다.
            # 같이 두면 진행 중 조정의 기준과 달라 보여 아래 409(not_ready)가 잘못 걸린다
            raw_hints = entry["hints"]
            entry = {k: v for k, v in entry.items() if k != "hints"}
        if (not isinstance(entry, dict) or set(entry) != {id_field, "mode"}
                or entry.get("mode") not in {"INITIAL_ADJUSTMENT", "NORMAL_ADJUSTMENT", 'DESIGN_RECOVERY'} or feature != "budget"):
            raise ValidationError({"budget_context": ["예산 조정 시작 시 기준 예산과 모드를 보내 주세요."]})
        try:
            entry = {**entry, id_field: str(uuid.UUID(str(entry[id_field])))}
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"budget_context": ["기준 예산 번호가 올바르지 않아요."]})
        from django.shortcuts import get_object_or_404
        from budgets.models import Budget
        if not recovery:
            get_object_or_404(Budget, pk=entry["base_budget_id"], user=request.user)
        if has_hints:  # 기준 예산 검사 뒤에 본다 (남의 예산이면 404 가 먼저). 칸이 있으면 null 도 거절한다
            if entry["mode"] != "NORMAL_ADJUSTMENT":  # 초과 항목 조정은 승인된 예산에서만 시작한다 (예산 조정 에이전트와 같은 기준)
                raise ValidationError({"budget_context": ["초과 항목 조정은 승인된 예산에서만 시작할 수 있어요."]})
            budget_hints = _entry_hints(raw_hints)
    talk = None
    if conversation_id is not None:
        talk = chat_api._active_conversations(request.user).filter(pk=conversation_id).first()
        if talk is None:
            raise ValidationError({"conversation_id": [chat_api.CONVERSATION_ERROR]})
    today, _ = today_and_as_of()
    recovery_adapter = None
    if entry and entry['mode'] == 'DESIGN_RECOVERY':
        from budgets.design_recovery import DesignRecoveryAdapter
        recovery_adapter = DesignRecoveryAdapter(request.user, entry['source_task_id'], today)
        bound = recovery_adapter.task.conversation_id
        if bound is not None:
            if talk is not None and talk.pk != bound:
                raise ValidationError({'budget_context': ['이미 연결된 조정 대화를 열어 주세요.']})
            talk = chat_api._active_conversations(request.user).filter(pk=bound).first()
            if talk is None:
                from budgets.design_recovery import RecoveryExpired
                raise RecoveryExpired()
    said = text if text is not None else FEATURES.get(feature)
    if budget_hints:
        said = _hinted_label(budget_hints)
    if choice is not None:
        said = _picked(talk, choice)
        if said is None:
            return Response(STALE, status=409)
        feature = choice["value"] if choice["value"] in FEATURES else None  # 의도 선택 · 범위 밖 답변의 기능 선택지
        text = None if feature else said
    store = ConversationStore.load(talk and talk.slots)
    state = store.get_agent_state(KEY, "budget") or {}
    if entry:
        if state.get("entry_context") != entry and any(state.get(k) for k in ("requests", "proposal", "pending")):
            return Response({"error": {"code": "not_ready", "message": "진행 중인 조정을 취소한 뒤 새 예산을 선택해 주세요."}}, status=409)
        state["entry_context"] = entry
        store.save_agent_state(KEY, "budget", state)
    from budgets.adjustment_service import AdjustmentAdapter
    if state.get('entry_context', {}).get('mode') == 'DESIGN_RECOVERY' and feature in (None, 'budget'):
        from budgets.design_recovery import DesignRecoveryAdapter
        adapter = recovery_adapter or DesignRecoveryAdapter(request.user, state['entry_context']['source_task_id'], today)
        bot, route = adapter.reply(store, KEY, text)
        return talk, store, adapter, bot, route, said
    if state.get('entry_context', {}).get('mode') == 'DESIGN_RECOVERY':
        store.save_agent_state(KEY, 'budget', {})
        store.set_active(KEY, None)
        state = {}
    adapter = AdjustmentAdapter(request.user, conversation=talk, today=today, **(state.get("entry_context") or {}))
    bot, route = answer(request.user, store, text=text, button=feature, budget_adapter=adapter, budget_hints=budget_hints,
                        suggested=choice is not None)  # 답변 선택지를 눌러 온 버튼 (소비 분석 초과 답변의 [예산 조정하기] 등)
    return talk, store, adapter, bot, route, said


def respond(request):
    """준비한 답을 회원 잠금·대화 rev 검수 후 저장한다. 헤더 없는 요청도 같은 준비 경로를 쓴다."""
    prepared = getattr(request, "_prepared_chat_turn", None)
    if prepared is None:
        prepared = prepare(request)
    if isinstance(prepared, Response):
        return prepared
    talk, store, adapter, bot, route, said = prepared
    with transaction.atomic():
        from budgets.api import lock_user
        lock_user(request.user)
        if talk is None:
            talk = Conversation.objects.create(user=request.user)
        else:
            talk = chat_api._active_conversations(request.user).select_for_update().filter(pk=talk.pk).first()
            if talk is None:
                raise ValidationError({"conversation_id": [chat_api.CONVERSATION_ERROR]})
            if (talk.slots or {}).get("rev", 0) != store.rev:  # 같은 대화의 다른 메시지가 먼저 저장됨
                return Response(BUSY, status=409)
        adapter.flush(talk)
        mine, reply = chat_api.save_turn(talk, said, _content(bot, store.rev + 1),
                                         content_type=ChatMessage.ContentType.TEXT)
        talk.slots, talk.active_agent = store.dump(), store.get_active(KEY)
        talk.save(update_fields=["slots", "active_agent"])
    return Response({"conversation_id": talk.conversation_id, "route": route, "agent": bot["agent"],
                     "messages": [chat_api.message_item(mine), chat_api.message_item(reply)], "task": None,
                     **chat_api.engine_info()})
