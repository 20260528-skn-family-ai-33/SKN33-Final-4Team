"""
통합 챗봇 API (docs/api/chat.md)
- 32. GET /api/chat/conversations/<id>/ — 이전 대화 보기 (DB 구현, 10/26)
- 33 · 34 는 아직 껍데기: 응답 = 명세 예시 JSON(chat/shell_examples.json). LLM 라우터 · 집계가 정해지면 함수 속을 바꾼다
"""
import copy
import json
from pathlib import Path

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.idempotency import idempotent

from .models import Conversation

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


@api_view(["POST"])
@idempotent()
def messages(request):
    """33. POST /api/chat/messages/ — 챗봇에 메시지 보내기 (소비 분석 카드 예시)"""
    return Response(example("message"))


@api_view(["GET"])
def task(request, task_id):
    """34. GET /api/tasks/<id>/ — 오래 걸리는 작업 진행 확인 (running 예시)"""
    return Response(example("task"))
