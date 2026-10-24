# 2026-10-24: 급여 주기 상태 active · closed (home.md)

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0003_seed_categories"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="budgetcycle",
            name="status",
            field=models.CharField(
                choices=[("active", "진행 중"), ("closed", "끝남")],
                default="active",
                max_length=20,
                verbose_name="진행 상태",
            ),
        ),
        migrations.AddConstraint(
            model_name="budgetcycle",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["active", "closed"])),
                name="bc_status_valid",
            ),
        ),
    ]
