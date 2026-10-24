"""
혜리 담당 API 껍데기 (2026-10-24) — 통합 챗봇 32~34
- 주소 · 방식은 명세(docs/api/chat.md)와 같고, 응답은 명세의 예시 JSON(chat/shell_examples.json)을 그대로 돌려준다.
- DB · LLM 라우터 · 집계 · 로그인 확인 · 입력 검사 없음. 진짜 기능은 나중에 함수 속만 바꾼다.
"""
import copy
import json
from pathlib import Path

from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.idempotency import idempotent

EXAMPLES = json.loads(Path(__file__).with_name("shell_examples.json").read_text(encoding="utf-8"))


def example(name):
    """명세 예시 응답 하나를 복사해서 준다 (원본이 바뀌지 않게)"""
    return copy.deepcopy(EXAMPLES[name])


@api_view(["GET"])
def conversation(request, conversation_id):
    """32. GET /api/chat/conversations/<id>/ — 이전 대화 보기"""
    return Response(example("conversation"))


@api_view(["POST"])
@idempotent()
def messages(request):
    """33. POST /api/chat/messages/ — 챗봇에 메시지 보내기 (소비 분석 카드 예시)"""
    return Response(example("message"))


@api_view(["GET"])
def task(request, task_id):
    """34. GET /api/tasks/<id>/ — 오래 걸리는 작업 진행 확인 (running 예시)"""
    return Response(example("task"))
