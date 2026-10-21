# 2026-10-21 C-03: 고정지출 금액 1,000원 ~ 1억 원, 납부일 1~31(비워도 됨)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0002_suggestion_candidates_merchant_idx"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="recurringexpense",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gte", 1000), ("amount__lte", 100000000)),
                name="re_amount_1000_100m",
            ),
        ),
        migrations.AddConstraint(
            model_name="recurringexpense",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("payment_day__isnull", True),
                    models.Q(("payment_day__gte", 1), ("payment_day__lte", 31)),
                    _connector="OR",
                ),
                name="re_payment_day_1_31",
            ),
        ),
    ]
