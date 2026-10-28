# 2026-10-28 예산 명세 리뷰 4번: 자물쇠를 걸 때의 금액을 같이 저장한다
# - budget_category_setting.locked_amount 추가 (원 단위, 풀려 있으면 NULL)
# 다시 추천받을 때 어느 예산의 금액을 유지할지 헷갈리지 않게 (docs/api/budgets.md 금액 고정 · 해제)

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0004_budget_cycle_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="budgetcategorysetting",
            name="locked_amount",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="잠긴 금액"),
        ),
        migrations.AddConstraint(
            model_name="budgetcategorysetting",
            constraint=models.CheckConstraint(
                condition=Q(locked_amount__isnull=True) | Q(locked_amount__gte=0), name="bcs_locked_amount_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="budgetcategorysetting",
            constraint=models.CheckConstraint(
                condition=Q(locked=True) | Q(locked_amount__isnull=True), name="bcs_unlocked_no_amount",
            ),
        ),
    ]
