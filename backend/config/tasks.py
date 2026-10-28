"""
오래 걸리는 작업 공용 도구 + GET /api/tasks/<id>/ (docs/api/chat.md '오래 걸리는 작업 진행 확인')

- 작업 하나 = chat.AsyncTask 한 줄. 다시 불러오기(finance_sync)가 처음 쓰고, 챗봇 예산 조정 · 추천 예산도 같이 쓴다.
- 1차는 작업을 요청 안에서 끝까지 처리하고 응답만 작업 모양(taskId · status)으로 준다 (10/27 나은 결정).
  나중에 느려지면 run() 부분만 뒤에서 돌리게 바꾸면 되고, API · 화면은 그대로다.
- 같은 회원 · 같은 종류의 작업이 이미 진행 중이면 새로 시작하지 않고 그 작업을 돌려준다 (start_or_reuse).
"""
import hashlib
import json
import logging
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat.models import AsyncTask

logger = logging.getLogger(__name__)

ACTIVE = (AsyncTask.Status.QUEUED, AsyncTask.Status.RUNNING)
STALE_AFTER = timedelta(minutes=10)  # 이보다 오래 running 이면 서버가 중간에 멈춘 것으로 보고 실패 처리

# error_code → 화면 문구 (작업 종류마다 늘려 간다)
ERROR_MESSAGES = {
    "server_error": "잠시 후 다시 시도해 주세요.",
    "stale": "작업이 중간에 멈췄어요. 다시 시도해 주세요.",
}


def input_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def task_item(task):
    """chat.md '오래 걸리는 작업 진행 확인' 응답 모양"""
    error = None
    if task.status == AsyncTask.Status.FAILED:
        code = task.error_code or "server_error"
        error = {"code": code, "message": ERROR_MESSAGES.get(code, ERROR_MESSAGES["server_error"])}
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        "conversation_id": task.conversation_id,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "result": task.result if task.status == AsyncTask.Status.SUCCEEDED else None,
        "error": error,
    }


def start_or_reuse(user, task_type, hash_value, reuse_within=None, conversation=None, key_prefix=None):
    """(작업, 새로 만들었는지)
    - 같은 회원 · 같은 종류가 진행 중이면 그 작업 (새로 안 만듦)
    - reuse_within 이 있으면, 같은 입력으로 그 시간 안에 끝난 작업도 그대로 돌려줌
    - 아니면 running 으로 새로 만든다. 판단과 생성은 회원 행을 잠그고 한 번에 한다 (잠금 순서: 회원 먼저)"""
    now = timezone.now()
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        mine = AsyncTask.objects.filter(user=user, task_type=task_type)
        active = mine.filter(status__in=ACTIVE).order_by("-created_at").first()
        if active is not None:
            if (active.started_at or active.created_at) > now - STALE_AFTER:
                return active, False
            active.status, active.error_code, active.completed_at = AsyncTask.Status.FAILED, "stale", now
            active.save(update_fields=["status", "error_code", "completed_at"])
        if reuse_within is not None:
            recent = mine.filter(
                status=AsyncTask.Status.SUCCEEDED, input_hash=hash_value, completed_at__gte=now - reuse_within,
            ).order_by("-completed_at").first()
            if recent is not None:
                return recent, False
        task = AsyncTask.objects.create(
            user=user, conversation=conversation, task_type=task_type, input_hash=hash_value,
            request_key=f"{key_prefix or task_type}:{uuid.uuid4()}",
            status=AsyncTask.Status.RUNNING, started_at=now,
        )
    return task, True


def run(task, work):
    """작업 실행 (1차: 지금 이 요청 안에서). work() 의 반환값이 result. 예외면 failed(server_error)"""
    try:
        result = work()
    except Exception:
        logger.exception("작업 실패 task=%s type=%s", task.task_id, task.task_type)
        task.status, task.error_code = AsyncTask.Status.FAILED, "server_error"
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "error_code", "completed_at"])
        return task
    task.status, task.result, task.completed_at = AsyncTask.Status.SUCCEEDED, result, timezone.now()
    task.save(update_fields=["status", "result", "completed_at"])
    return task


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_detail(request, task_id):
    """GET /api/tasks/<id>/ — 작업 진행 확인. 없는 작업 · 남의 작업은 404"""
    task = AsyncTask.objects.filter(user=request.user, pk=task_id).first()
    if task is None:
        raise Http404
    return Response(task_item(task))
