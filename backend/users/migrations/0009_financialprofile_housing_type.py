# 2026-10-27 온보딩 변경: 예산 산정 방향(생활 안정형 · 저축 집중형) 대신 자취 여부로 지킬 돈 비율을 정한다
# - financial_profile.housing_type 추가 (alone 자취 · family 본가 거주, 비어 있으면 미응답)
# - budget_direction 은 아직 지우지 않는다 (내 정보 API · budgets/mock.py 정리 때 같이)

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0008_consent_product_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="financialprofile",
            name="housing_type",
            field=models.CharField(
                blank=True, choices=[("alone", "자취"), ("family", "본가 거주")], max_length=10, null=True,
                verbose_name="거주 형태",
            ),
        ),
        migrations.AddConstraint(
            model_name="financialprofile",
            constraint=models.CheckConstraint(
                condition=Q(housing_type__isnull=True) | Q(housing_type__in=["alone", "family"]),
                name="fp_housing_type_valid",
            ),
        ),
    ]
