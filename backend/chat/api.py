"""챗봇 껍데기 API: 고정지출처럼 예시 응답만 반환한다 (실제 저장/작업 없음)."""

from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import examples


@api_view(["GET"])
def conversation_detail(request, conversation_id):
    # UUID 형식은 URL에서 검사. 존재 여부·소유권 검사는 실제 구현 때 추가한다.
    return Response({**examples.CONVERSATION_EXAMPLE, "conversationId": str(conversation_id)})


@api_view(["POST"])
def messages(request):
    return Response(examples.MESSAGE_SENT_EXAMPLE)


@api_view(["GET"])
def task_detail(request, task_id):
    return Response({**examples.TASK_EXAMPLE, "taskId": str(task_id)})
