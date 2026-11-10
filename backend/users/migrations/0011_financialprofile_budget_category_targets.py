from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0010_financialprofile_payday_change')]
    operations = [migrations.AddField(model_name='financialprofile', name='budget_category_targets',
        field=models.JSONField(blank=True, default=dict, verbose_name='유지할 카테고리 예산 목표'))]
