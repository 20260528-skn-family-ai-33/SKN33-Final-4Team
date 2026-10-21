# 2026-10-21 C-04: 고정지출 상태(suggested·confirmed·stopped·ignored) · 출처(manual·detected) 코드 확정

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0003_recurring_expense_amount_day_checks"),
    ]

    operations = [
        migrations.AlterField(
            model_name="recurringexpense",
            name="status",
            field=models.CharField(
                choices=[("suggested", "후보"), ("confirmed", "확정"), ("stopped", "중지"), ("ignored", "무시")],
                max_length=20,
                verbose_name="상태",
            ),
        ),
        migrations.AlterField(
            model_name="recurringexpense",
            name="source_type",
            field=models.CharField(
                choices=[("manual", "직접 등록"), ("detected", "반복 탐지")],
                max_length=20,
                verbose_name="등록 출처",
            ),
        ),
        migrations.AddConstraint(
            model_name="recurringexpense",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["suggested", "confirmed", "stopped", "ignored"])),
                name="re_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="recurringexpense",
            constraint=models.CheckConstraint(
                condition=models.Q(("source_type__in", ["manual", "detected"])),
                name="re_source_type_valid",
            ),
        ),
    ]
