# 2026-10-24: 금융 조회 동의 범위 bank · card · loan (결정 L)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0008_decided_codes_card_bill_loan"),
        ("users", "0007_idempotency_record"),
    ]

    operations = [
        migrations.AlterField(
            model_name="consent",
            name="product_scope",
            field=models.CharField(
                blank=True,
                choices=[("bank", "은행"), ("card", "카드"), ("loan", "대출")],
                max_length=50,
                null=True,
                verbose_name="조회 허용 상품 범위",
            ),
        ),
        migrations.AddConstraint(
            model_name="consent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("product_scope__isnull", True),
                    ("product_scope__in", ["bank", "card", "loan"]),
                    _connector="OR",
                ),
                name="consent_product_scope_valid",
            ),
        ),
    ]
