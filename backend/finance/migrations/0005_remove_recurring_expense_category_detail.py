# 2026-10-21: 고정지출 세부 분류(category_detail) 칸 삭제 — 화면 입력이 없고 1차에 쓰는 곳이 없음

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0004_recurring_expense_status_source_codes"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="recurringexpense",
            name="category_detail",
        ),
    ]
