"""
챗봇 대화 · 비동기 작업 (ERD v2)

테이블: conversation, chat_message, async_task

- 작성 규칙(UUID PK, db_table, settings.AUTH_USER_MODEL)은 docs/DB_MODELS.md
- on_delete는 FK_삭제규칙.md 표 그대로 (이유도 거기에)
- 메시지가 가리키는 작업이 같은 대화의 작업인지는 서비스 코드에서 검사한다.
- "# 코드 미정" 표시 칸은 값 목록이 아직 안 정해진 칸이다 (docs/DB_MODELS.md 5번)
"""
import uuid

from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL


def uuid_pk():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class Conversation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "진행 중"
        ARCHIVED = "archived", "보관"

    conversation_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="conversations")
    status = models.CharField("상태", max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "conversation"
        verbose_name = verbose_name_plural = "대화"


class AsyncTask(models.Model):
    """예산 생성·조정 같은 오래 걸리는 작업. 같은 request_key로 다시 요청하면 새로 만들지 않는다"""

    task_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="async_tasks")
    conversation = models.ForeignKey(
        Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks",
    )
    request_key = models.CharField("중복 요청 키", max_length=128)
    task_type = models.CharField("작업 종류", max_length=40)  # 코드 미정
    status = models.CharField("상태(대기/실행/완료/실패/취소)", max_length=20)  # 코드 미정
    input_hash = models.CharField("입력 해시", max_length=128)
    llm_call_count = models.IntegerField("LLM 호출 수", default=0)
    regeneration_count = models.IntegerField("재생성 횟수", default=0)
    result = models.JSONField("결과 요약", null=True, blank=True)
    error_code = models.CharField("오류 코드", max_length=50, null=True, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)
    started_at = models.DateTimeField("실행 시작 시각", null=True, blank=True)
    completed_at = models.DateTimeField("완료 시각", null=True, blank=True)

    class Meta:
        db_table = "async_task"
        verbose_name = verbose_name_plural = "비동기 작업"
        constraints = [
            models.UniqueConstraint(fields=["user", "request_key"], name="async_task_user_id_request_key_key"),
        ]


class ChatMessage(models.Model):
    message_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="chat_messages")
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    task = models.ForeignKey(
        AsyncTask, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages",
    )
    role = models.CharField("발화자", max_length=20)  # 코드 미정
    mode = models.CharField("모드", max_length=50, null=True, blank=True)
    content_type = models.CharField("텍스트/결과 카드", max_length=20)  # 코드 미정
    content = models.JSONField("메시지 내용")
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "chat_message"
        verbose_name = verbose_name_plural = "채팅 메시지"
        ordering = ["created_at"]
