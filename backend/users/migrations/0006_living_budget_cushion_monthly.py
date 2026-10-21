# 2026-10-21 지킬 돈 A안
# - financial_profile.living_budget(월 생활비) 추가: 지킬 돈 설정에서 사용자가 정한 생활비
# - financial_profile.cushion_floor(쿠션 보호 하한액) 삭제 → cushion_monthly(월 쿠션 적립금) 추가
#   (의미가 달라 이름만 바꾸지 않고 지우고 새로 만든다. 아직 실제 사용자 데이터 없음)
# 쿠션을 예산 조정 때 어디까지 쓸 수 있는지(사용 한도 규칙)는 2차 전에 따로 정한다

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_category_minimum"),
    ]

    operations = [
        migrations.AddField(
            model_name="financialprofile",
            name="living_budget",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="월 생활비"),
        ),
        migrations.RemoveField(
            model_name="financialprofile",
            name="cushion_floor",
        ),
        migrations.AddField(
            model_name="financialprofile",
            name="cushion_monthly",
            field=models.BigIntegerField(blank=True, null=True, verbose_name="월 쿠션 적립금"),
        ),
        migrations.AddConstraint(
            model_name="financialprofile",
            constraint=models.CheckConstraint(
                condition=Q(living_budget__isnull=True) | Q(living_budget__gte=0), name="fp_living_budget_gte_0",
            ),
        ),
        migrations.AddConstraint(
            model_name="financialprofile",
            constraint=models.CheckConstraint(
                condition=Q(cushion_monthly__isnull=True) | Q(cushion_monthly__gte=0), name="fp_cushion_monthly_gte_0",
            ),
        ),
    ]
