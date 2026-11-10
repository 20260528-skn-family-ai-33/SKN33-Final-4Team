from django.db import migrations, models


def require_legacy_recheck(apps, schema_editor):
    """확인 당시 값이 없는 기록은 추정 복원하지 않는다. 영향 회차의 캐시도 비운다."""
    alias = schema_editor.connection.alias
    Decision = apps.get_model("budgets", "SalaryDecision")
    Cycle = apps.get_model("budgets", "BudgetCycle")
    legacy = Decision.objects.using(alias).filter(state="confirmed")
    cycles = list(legacy.values_list("user_id", "pay_cycle_on").distinct())
    legacy.update(state="recheck", is_pattern=False)
    for user_id, starts_on in cycles:
        Cycle.objects.using(alias).filter(user_id=user_id, starts_on=starts_on).update(confirmed_income=None)


class Migration(migrations.Migration):
    dependencies = [("budgets", "0007_salarydecision_recheck")]

    operations = [
        migrations.AddField(
            model_name="salarydecision", name="snapshot_amount",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="확정 당시 금액"),
        ),
        migrations.AddField(
            model_name="salarydecision", name="snapshot_occurred_on",
            field=models.DateField(blank=True, null=True, verbose_name="확정 당시 거래일"),
        ),
        migrations.AddField(
            model_name="salarydecision", name="snapshot_description",
            field=models.CharField(blank=True, null=True, max_length=500, verbose_name="확정 당시 적요"),
        ),
        migrations.AddField(
            model_name="salarydecision", name="snapshot_account_id",
            field=models.UUIDField(blank=True, null=True, verbose_name="확정 당시 입금계좌 ID"),
        ),
        migrations.RunPython(require_legacy_recheck, migrations.RunPython.noop),
    ]
