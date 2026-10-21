# 2026-10-21 C-10: 같은 저장 요청 두 번 막기 — 요청 번호(Idempotency-Key) 기록 표

import uuid

import rest_framework.utils.encoders
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_living_budget_cushion_monthly"),
    ]

    operations = [
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("record_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("key", models.CharField(max_length=64, verbose_name="요청 번호")),
                ("method", models.CharField(max_length=10, verbose_name="요청 방식")),
                ("path", models.CharField(max_length=200, verbose_name="요청 주소")),
                ("request_hash", models.CharField(max_length=64, verbose_name="요청 내용 해시")),
                ("response_status", models.SmallIntegerField(verbose_name="처음 응답 상태 코드")),
                ("response_body", models.JSONField(blank=True, encoder=rest_framework.utils.encoders.JSONEncoder, null=True, verbose_name="처음 응답 내용")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="처리 시각")),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="idempotency_records",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "요청 번호 기록",
                "verbose_name_plural": "요청 번호 기록",
                "db_table": "idempotency_record",
                "constraints": [
                    models.UniqueConstraint(fields=("user", "key"), name="idempotency_record_user_id_key_key")
                ],
            },
        ),
    ]
