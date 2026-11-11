"""청년 정책을 찾을 사는 지역 = 회원 정보의 가입 지역 (financial_profile.region_code, 11-11 멘토 요청 ②).

- 온보딩 내 정보에서 받고, 채팅 [다음에도 …로 찾기]를 누르면 바꾼다
- 읽기는 핸들러가 한다. 채팅에서의 쓰기는 엔진이 대화 저장과 같은 트랜잭션에서 한다 (save_profile)
- 회원이 아니거나(로컬 확인 · 테스트 대역) 저장한 지역이 지역 표에서 사라졌으면 없는 것으로 본다
"""
from users.models import FinancialProfile

from .regions import REGIONS


def is_member(user):
    return getattr(user, "pk", None) is not None and getattr(user, "is_authenticated", False)


def is_final(code):
    return bool((REGIONS.get(code) or {}).get("final"))


def remembered_region(user):
    """회원 정보의 사는 지역 코드. 없으면 None"""
    if not is_member(user):
        return None
    code = FinancialProfile.objects.filter(user=user).values_list("region_code", flat=True).first()
    return code if is_final(code) else None


def remember(user, region_code):
    """회원 정보의 사는 지역을 바꾼다 (회원 정보가 없으면 만든다). 시군구까지 고른 지역만"""
    if not is_final(region_code):
        raise ValueError(f"시군구까지 고른 지역 코드가 아니에요: {region_code}")
    FinancialProfile.objects.update_or_create(user=user, defaults={"region_code": region_code})
