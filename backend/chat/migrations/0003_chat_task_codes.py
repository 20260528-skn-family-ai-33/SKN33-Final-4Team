# 2026-10-24: 챗봇 메시지 발화자 · 내용 종류, 작업 상태 코드 (결정 E)

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0002_conversation_active_agent_slots"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="asynctask",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "대기"),
                    ("running", "실행 중"),
                    ("succeeded", "완료"),
                    ("failed", "실패"),
                    ("canceled", "취소"),
                ],
                default="queued",
                max_length=20,
                verbose_name="상태(대기/실행/완료/실패/취소)",
            ),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="content_type",
            field=models.CharField(
                choices=[("text", "텍스트"), ("card", "결과 카드")],
                max_length=20,
                verbose_name="텍스트/결과 카드",
            ),
        ),
        migrations.AlterField(
            model_name="chatmessage",
            name="role",
            field=models.CharField(
                choices=[("user", "사용자"), ("bot", "챗봇")],
                max_length=20,
                verbose_name="발화자",
            ),
        ),
        migrations.AddConstraint(
            model_name="asynctask",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        ["queued", "running", "succeeded", "failed", "canceled"],
                    )
                ),
                name="at_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.CheckConstraint(
                condition=models.Q(("role__in", ["user", "bot"])), name="cm_role_valid"
            ),
        ),
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.CheckConstraint(
                condition=models.Q(("content_type__in", ["text", "card"])),
                name="cm_content_type_valid",
            ),
        ),
    ]
