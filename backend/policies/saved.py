"""저장한 정책 (북마크). 저장 API(api.py)와 채팅(엔진이 핸들러 결과의 save_policies 를 보고 저장)이 같이 쓴다.

- 저장은 활성 정책만. 이미 저장돼 있으면 그대로 둔다 (두 번 눌러도 한 줄 — saved_policy_once)
- 목록은 지금 정책 값을 읽고, 마감 · 수집 종료(비활성)도 지우지 않고 상태로 보여 준다
"""
from django.db import IntegrityError, transaction

from . import search
from .models import Policy, SavedPolicy


def save(user, policy_no):
    """→ (SavedPolicy, 새로 저장했나). 없는 · 비활성 정책이면 (None, False)"""
    policy = Policy.objects.filter(pk=policy_no, is_active=True).first()
    if policy is None:
        return None, False
    found = SavedPolicy.objects.filter(user=user, policy=policy).first()
    if found:
        return found, False
    try:
        with transaction.atomic():  # 동시에 두 번 와도 UNIQUE 가 하나만 남긴다
            return SavedPolicy.objects.create(user=user, policy=policy), True
    except IntegrityError:
        return SavedPolicy.objects.get(user=user, policy=policy), False


def save_all(user, numbers):
    """채팅에서 고른 정책들 → 새로 저장한 수. 엔진이 대화 저장과 같은 트랜잭션에서 부른다 (7단계)"""
    return sum(save(user, number)[1] for number in dict.fromkeys(numbers))


def unsave(user, policy_no):
    """저장 취소. 저장돼 있지 않아도 그대로 성공 (지운 수를 돌려준다)"""
    return SavedPolicy.objects.filter(user=user, policy_id=policy_no).delete()[0]


def apply_state(policy, today):
    """open · always · upcoming · unknown · closed. 검색과 달리 저장 목록은 마감 정책도 보여 주므로 closed 가 있다"""
    if policy.apply_type == "closed" or (policy.apply_type == "specific" and policy.last_apply_end
                                         and policy.last_apply_end < today):
        return "closed"
    return search._apply_state(policy, today)


def item(saved, today):
    """API 응답 한 칸 (snake_case — 렌더러가 camelCase 로 바꾼다)"""
    policy = saved.policy
    return {
        "saved_policy_id": saved.pk,
        "policy_no": policy.pk,
        "name": policy.name,
        "apply_state": apply_state(policy, today),
        "apply_end": policy.last_apply_end,
        "link": policy.apply_url or (policy.ref_urls[0] if policy.ref_urls else None),
        "active": policy.is_active,
        "saved_at": saved.created_at,
    }


def items(user, today):
    rows = SavedPolicy.objects.filter(user=user).select_related("policy").order_by("-created_at", "pk")
    return [item(row, today) for row in rows]
