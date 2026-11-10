"""6단계 도구: 고정 흐름(agent.py)과 8단계 에이전트가 같이 쓴다. 입력 · 출력이 분명한 함수이고, 사실(정책명 · 기간 · 링크 · 자격)은
모두 DB 값과 코드 계산이다. LLM 은 여기 없다.

- profile_for(user, text): 사용자 조건 (온보딩 값 · 월급 정보 · 문장 속 "취업 준비")
- find(needs, region, profile, today): 검색 → 자격 불충족 빼기 → 재등록 겹침 빼기 → 추천 후보 순서
- facts(policy, verdict, today): 답변 한 칸에 들어갈 사실 (DB 값 그대로)
"""
import re
from dataclasses import dataclass

from . import eligibility, search
from .eligibility import Profile
from .models import Policy, PolicySync
from .normalize import _clean

EMPLOYED, JOBLESS = "0013001", "0013003"
# "취업 준비 중"처럼 말하면 그 말을 따른다 (메모: 조건 입력). 월급 정보보다 앞선다
JOBLESS_WORDS = re.compile(r"취준|취업\s*준비|구직\s*중|무직|백수|일자리를?\s*찾|실직|퇴사")
STATE = {"open": "신청 중", "always": "상시", "upcoming": "신청 예정", "unknown": "기간 확인 필요"}
SUPPORT_CHARS = 90  # 지원 내용은 DB 문장 앞부분만 (자르기만 하고 바꿔 쓰지 않는다)


def profile_for(user, text=None):
    """회원 → Profile. 회원이 없으면(로컬 확인 · 평가) 빈 Profile. 생년월일이 없으면 birth_date=None — 부르는 쪽이 안내한다"""
    info = getattr(user, "financial_profile", None) if user is not None else None
    job = JOBLESS if text and JOBLESS_WORDS.search(text) else \
        EMPLOYED if info is not None and info.monthly_take_home_income else None
    if info is None:
        return Profile(job=job)
    return Profile(birth_date=info.birth_date, gender=info.gender, job=job)


@dataclass
class Found:
    policy: Policy
    verdict: eligibility.Verdict
    keywords: list[str]  # 이 정책에서 찾은 키워드 (추천 이유 문장에 쓴다)


def find(needs, region, profile, today, *, embed=None, limit=search.LIMIT):
    """→ (추천 순서의 Found 목록, 자격 불충족으로 뺀 수). 검색 순서를 그대로 두고 불충족만 뺀다.
    같은 이름(2024 · 2026 재등록 등)은 앞의 것 하나만 남긴다 — 추천 3칸이 같은 정책으로 차지 않게"""
    hits = search.search_needs(needs, region, today, limit=limit, embed=embed)
    kept, dropped = eligibility.keep(hits, profile, today)
    found, names = [], set()
    for hit, verdict in kept:
        if hit.policy.name in names:
            continue
        names.add(hit.policy.name)
        found.append(Found(hit.policy, verdict, hit.keywords))
    return found, len(dropped)


def again(numbers, profile, today):
    """상태에 남긴 정책번호 → Found 목록 (더 보기). 그사이 비활성이 됐거나 사라진 번호는 건너뛴다"""
    policies = Policy.objects.in_bulk(numbers)
    return [Found(policies[n], eligibility.judge(policies[n], profile, today), [])
            for n in numbers if n in policies and policies[n].is_active]


def _period(policy, today):
    state = search._apply_state(policy, today)
    if state in ("open", "upcoming") and policy.periods:
        day = today.isoformat()
        now = next((p for p in policy.periods if p["end"] >= day), policy.periods[-1])
        return f"{STATE[state]} ({now['start']} ~ {now['end']})"
    return STATE[state]


def facts(found, today):
    """답변 한 칸의 사실 (모두 DB 값 · 코드 계산). 링크가 없으면 None — 지어내지 않는다"""
    policy, verdict = found.policy, found.verdict
    support = _clean(policy.raw, "plcySprtCn") or _clean(policy.raw, "plcyExplnCn")
    checks = [f"{c.item}({c.detail})" if c.item in ("나이", "소득") else c.item for c in verdict.to_check]
    return {
        "policy_no": policy.pk,
        "name": policy.name,
        "period": _period(policy, today),
        "support": support[:SUPPORT_CHARS] + ("…" if len(support) > SUPPORT_CHARS else ""),
        "check": checks,
        "soon_over": verdict.soon_over,
        "link": policy.apply_url or (policy.ref_urls[0] if policy.ref_urls else None),
        "keywords": found.keywords,
    }


def policy_name(policy_no):
    """정책번호 → 정책명 (DB 값). 없으면 번호 그대로"""
    return Policy.objects.filter(pk=policy_no).values_list("name", flat=True).first() or policy_no


def data_date():
    """정책 데이터 기준일 (마지막 성공 수집). 없으면 None"""
    last = PolicySync.objects.filter(status=PolicySync.Status.OK).order_by("-finished_at").first()
    return last and last.data_date
