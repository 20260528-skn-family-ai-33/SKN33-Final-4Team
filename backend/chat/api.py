"""
통합 챗봇 API (docs/api/chat.md)
- 32. GET  /api/chat/conversations/<id>/ — 이전 대화 보기 (10/26)
- 33. POST /api/chat/messages/ — 메시지 보내기 (10/27: 저장 · 선택지 처리. 소비 분석 집계 · LLM 라우터는 다음 단계)
- 34 는 아직 껍데기: 응답 = 명세 예시 JSON(chat/shell_examples.json). 예산 조정 에이전트와 같이 만든다
"""
import copy
import json
from pathlib import Path
import uuid

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.idempotency import idempotent
from finance.inputs import body_dict, reject_unknown_fields

from . import replies, router
from .models import ChatMessage, Conversation

EXAMPLES = json.loads(Path(__file__).with_name("shell_examples.json").read_text(encoding="utf-8"))


def example(name):
    """명세 예시 응답 하나를 복사해서 준다 (원본이 바뀌지 않게)"""
    return copy.deepcopy(EXAMPLES[name])


def message_item(message):
    """메시지 한 줄 {messageId, role, contentType, content, taskId, createdAt}"""
    return {
        "message_id": message.message_id,
        "role": message.role,
        "content_type": message.content_type,
        "content": message.content,
        "task_id": message.task_id,
        "created_at": message.created_at,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def conversation(request, conversation_id):
    """32. GET /api/chat/conversations/<id>/ — 이전 대화 보기 (메시지는 오래된 것부터)
    없는 대화 · 남의 대화는 404 → 화면은 새 대화로 시작 (10/22 N⑤)
    서버만 쓰는 대화 상태(active_agent · slots)는 보내지 않는다"""
    talk = get_object_or_404(Conversation, pk=conversation_id, user=request.user)
    rows = talk.messages.order_by("created_at", "message_id")
    return Response({
        "conversation_id": talk.conversation_id,
        "status": talk.status,
        "created_at": talk.created_at,
        "messages": [message_item(message) for message in rows],
    })


TEXT_MAX = 500
TEXT_ERROR = "500자 이내로 입력해 주세요."
CONVERSATION_ERROR = "대화를 찾을 수 없어요."


def _check_message_body(request):
    """보내는 값 검사 → (conversation_id 또는 None, text 또는 None, choice 또는 None). 틀리면 400"""
    data = body_dict(request)
    reject_unknown_fields(data, {"conversation_id", "text", "choice"})
    if "conversation_id" not in data:
        raise ValidationError({"conversation_id": ["대화 번호를 보내 주세요. 새 대화면 null 이에요."]})
    text, choice = data.get("text"), data.get("choice")
    if (text is None) == (choice is None):
        raise ValidationError({"non_field_errors": ["text와 choice 중 하나만 보내 주세요."]})
    if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX):
        raise ValidationError({"text": [TEXT_ERROR]})
    if choice is not None and (not isinstance(choice, str) or choice not in router.CHOICE_LABELS):
        raise ValidationError({"choice": ["알 수 없는 선택지예요."]})
    raw_id = data["conversation_id"]
    conversation_id = None
    if raw_id is not None:
        try:
            conversation_id = uuid.UUID(str(raw_id))
        except ValueError:
            raise ValidationError({"conversation_id": [CONVERSATION_ERROR]})
    return conversation_id, text, choice


def _active_conversations(user):
    return Conversation.objects.filter(user=user, status=Conversation.Status.ACTIVE)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def messages(request):
    """33. POST /api/chat/messages/ {"conversationId": null | "<id>", "text": "…" | "choice": "spending"}
    - conversationId 가 null 이면 새 대화. 본문 속 ID 라서 없는/남의/끝난 대화는 404 가 아니라 400 (API_RULES 예외)
    - 선택지는 라우터를 거치지 않는다(fallback: false). 문장은 LLM 라우터 연결 전이라 unknown + fallback: true
    - 응답 messages = 이번에 새로 생긴 메시지 (사용자 1개 + 봇 1개, AA⑧)
    - 라우터 실패 때는 대화 중인 도우미(active_agent)와 모은 재료(slots)를 바꾸지 않는다"""
    conversation_id, text, choice = _check_message_body(request)
    talk = None
    if conversation_id is not None:
        talk = _active_conversations(request.user).filter(pk=conversation_id).first()
        if talk is None:
            raise ValidationError({"conversation_id": [CONVERSATION_ERROR]})

    # 라우팅은 DB 잠금 밖에서 한다 (LLM 호출이 붙으면 몇 초 걸릴 수 있음)
    if choice is not None:
        (intent, slots), fallback = router.route_choice(choice), False
    else:
        try:
            (intent, slots), fallback = router.route_text(text, talk), False
        except router.RouterUnavailable:
            intent, slots, fallback = "unknown", {}, True
    content_type, content = replies.reply(intent, slots, fallback)

    with transaction.atomic():
        if talk is None:
            talk = Conversation.objects.create(user=request.user)
        else:
            # 같은 대화에 동시에 온 메시지를 차례로 저장하고, 그사이 끝난 대화면 거절한다
            talk = _active_conversations(request.user).select_for_update().filter(pk=talk.pk).first()
            if talk is None:
                raise ValidationError({"conversation_id": [CONVERSATION_ERROR]})
        said = ChatMessage.objects.create(
            user=request.user, conversation=talk, role=ChatMessage.Role.USER,
            content_type=ChatMessage.ContentType.TEXT,
            # 선택지는 화면에 보인 이름으로 남긴다 (예: "소비 분석")
            content={"text": text if text is not None else router.CHOICE_LABELS[choice], "choices": []},
        )
        answer = ChatMessage.objects.create(
            user=request.user, conversation=talk, role=ChatMessage.Role.BOT,
            content_type=content_type, content=content,
        )
    return Response({
        "conversation_id": talk.conversation_id,
        "route": {"intent": intent, "slots": slots, "fallback": fallback},
        "messages": [message_item(said), message_item(answer)],
        "task": None,
    })


@api_view(["GET"])
def task(request, task_id):
    """34. GET /api/tasks/<id>/ — 오래 걸리는 작업 진행 확인 (running 예시)"""
    return Response(example("task"))
