"""카테고리 초기 데이터 12개 (10-22)

docs/API_RULES.md '카테고리 코드' 표와 같다. 코드를 바꾸려면 그 표를 먼저 고치고 → 새 데이터 마이그레이션을 추가한다.
- 1~10: 소비 우선순위 O · 예산 칸 O · 고르기 O · ML 정답 O
- ETC(기타): 예산 칸 O · 고르기 O, 우선순위 · ML 정답 X
- UNCLASSIFIED(확인 필요): 분류 전 상태값. 예산 칸 · 고르기 · ML 정답 X
  (거래는 처음에 전부 UNCLASSIFIED 로 들어오므로 이 줄이 없으면 거래 저장이 안 된다)

이미 같은 코드가 있으면(손으로 넣어 둔 경우) 이름 · 순서 · 표시만 맞춘다. 여러 번 돌려도 결과가 같다.
주의: `python manage.py flush`는 이 데이터까지 지운다. flush 뒤에는
      `python manage.py migrate budgets 0002 && python manage.py migrate` 로 다시 넣는다.
"""
from django.db import migrations

CATEGORIES = [
    # (코드, 화면 이름, ML 정답, 생활비 예산 대상)
    ("FOOD", "식비", True, True),
    ("HOUSING_TELECOM", "주거·통신", True, True),
    ("TRANSPORT_CAR", "교통·차량", True, True),
    ("HOUSEHOLD", "생활용품", True, True),
    ("FASHION_SHOPPING", "패션·쇼핑", True, True),
    ("MEDICAL_HEALTH", "의료·건강", True, True),
    ("BEAUTY", "뷰티·미용", True, True),
    ("EDUCATION", "교육", True, True),
    ("CULTURE_LEISURE", "문화·여가", True, True),
    ("TRAVEL_LODGING", "여행·숙박", True, True),
    ("ETC", "기타", False, True),
    ("UNCLASSIFIED", "확인 필요", False, False),
]


def seed(apps, schema_editor):
    Category = apps.get_model("budgets", "Category")
    for order, (code, name, ml, living) in enumerate(CATEGORIES, start=1):
        Category.objects.update_or_create(
            category_code=code,
            defaults=dict(display_name=name, is_ml_label=ml, is_living_budget_category=living,
                          display_order=order, is_active=True),
        )


class Migration(migrations.Migration):

    dependencies = [("budgets", "0002_initial")]

    # 되돌릴 때는 지우지 않는다 (거래 · 고정지출이 가리키고 있으면 RESTRICT 로 실패하므로)
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
