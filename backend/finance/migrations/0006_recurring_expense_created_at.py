# 2026-10-21: 고정지출 발견·등록 시각 — 후보를 '최근에 찾은 순'으로 정렬하려고 (fixed.md 4. 목록 보기)

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0005_remove_recurring_expense_category_detail"),
    ]

    operations = [
        migrations.AddField(
            model_name="recurringexpense",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True, default=django.utils.timezone.now, verbose_name="발견·등록 시각"
            ),
            preserve_default=False,
        ),
    ]
