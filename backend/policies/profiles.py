"""청년 정책 추천 조건 저장 (회원별, policy_profile). 사용자가 [다음에도 …로 찾기]를 눌렀을 때만 저장한다.

- 읽기는 핸들러가 한다 (다음 대화에서 지역을 다시 묻지 않기). 쓰기는 엔진이 대화 저장과 같은 트랜잭션에서 한다 (save_profile)
- 회원이 아니거나(로컬 확인 · 테스트 대역) 저장한 지역이 지역 표에서 사라졌으면 없는 것으로 본다
"""
from .models import PolicyProfile
from .regions import REGIONS


def is_member(user):
    return getattr(user, "pk", None) is not None and getattr(user, "is_authenticated", False)


def remembered_region(user):
    """저장한 사는 지역 코드. 없으면 None"""
    if not is_member(user):
        return None
    code = PolicyProfile.objects.filter(user=user).values_list("region_code", flat=True).first()
    return code if code in REGIONS and REGIONS[code]["final"] else None


def remember(user, region_code):
    """사는 지역 저장 (있으면 바꾼다). 시군구까지 고른 지역만"""
    if not (REGIONS.get(region_code) or {}).get("final"):
        raise ValueError(f"시군구까지 고른 지역 코드가 아니에요: {region_code}")
    PolicyProfile.objects.update_or_create(user=user, defaults={"region_code": region_code})
