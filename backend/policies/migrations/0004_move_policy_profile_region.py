# 2026-11-11 사는 지역을 회원 정보(financial_profile.region_code)로 모은다 (PR 97, 멘토 요청 ②).
# 채팅 [다음에도 …로 찾기]로 저장한 policy_profile 지역을 비어 있는 회원 정보로 옮기고 표를 지운다.

from django.db import migrations


def move_regions(apps, schema_editor):
    PolicyProfile = apps.get_model("policies", "PolicyProfile")
    FinancialProfile = apps.get_model("users", "FinancialProfile")
    for row in PolicyProfile.objects.all():
        profile, _ = FinancialProfile.objects.get_or_create(user_id=row.user_id)
        if not profile.region_code:  # 이미 회원 정보에 지역이 있으면 그 값을 둔다
            profile.region_code = row.region_code
            profile.save(update_fields=["region_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("policies", "0003_policy_profile"),
        ("users", "0013_financialprofile_region_code"),
    ]

    operations = [
        migrations.RunPython(move_regions, migrations.RunPython.noop),
        migrations.DeleteModel(name="PolicyProfile"),
    ]
