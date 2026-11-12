"""6단계 도구: 고정 흐름(agent.py)과 8단계 에이전트가 같이 쓴다. 입력 · 출력이 분명한 함수이고, 사실(정책명 · 기간 · 링크 · 자격)은
모두 DB 값과 코드 계산이다. LLM 은 여기 없다.

- profile_for(user, text, job): 사용자 조건 (온보딩 값 · 월급 정보 · 문장 속 "취업 준비" · "회사 다녀", 대화에서 마지막으로 말한 취업 상태)
- find(needs, region, profile, today): 검색 → 자격 불충족 빼기 → 재등록 겹침 빼기 → 추천 후보 순서
- facts(policy, verdict, today): 답변 한 칸에 들어갈 사실 (DB 값 그대로)
"""
import re
from dataclasses import dataclass

from . import eligibility, saved, search
from .eligibility import Profile
from .models import Policy, PolicySync
from .normalize import _clean

EMPLOYED, JOBLESS = "0013001", "0013003"
# "취업 준비 중" · "회사 다녀"처럼 말하면 그 말을 따른다 (메모: 조건 입력). 월급 정보보다 앞서고, 대화 중에 고쳐 말하면 바뀐다
JOBLESS_WORDS = re.compile(r"취준|취업\s*준비|구직\s*중|무직|백수|일자리를?\s*찾|실직|퇴사")
EMPLOYED_WORDS = re.compile(r"재직|(?:회사|직장)(?:에|를|을)?\s*다니|월급(?:을|이)?\s*받|취직했|취업했|직장인|근무\s*중|출근")
SAID_JOB = {"jobless": JOBLESS, "employed": EMPLOYED}
STATE = {"open": "신청 중", "always": "상시", "upcoming": "신청 예정", "unknown": "기간 확인 필요", "closed": "마감"}
SUPPORT_CHARS = 80  # 지원 내용은 DB 문장 앞부분만 (자르기만 하고 바꿔 쓰지 않는다)
# 원문 글머리 기호 (ㅁ · □ · ㅇ · ○ · ※ · ① · ➊ · - …). 낱자 자모(ㅁ · ㅇ)는 낱말 안에 오지 않아 지워도 뜻이 바뀌지 않는다
BULLETS = re.compile(r"(?:(?<=\s)|^)(?:[ㅁㅇ□■○●◦▪▫◆◇※•►▶❍]|[①-⑳]|[➊-➓]|-(?=\s|[가-힣]))\s*")


def said_job(text):
    """문장에서 말한 취업 상태 → "jobless" · "employed" · None.
    둘 다 있으면 뒤에 나온 말을 따른다 ("무직이었는데 이제 회사 다녀" → employed, "회사 다니다 퇴사했어" → jobless)"""
    last = {}
    for kind, words in (("jobless", JOBLESS_WORDS), ("employed", EMPLOYED_WORDS)):
        spots = [match.start() for match in words.finditer(text or "")]
        if spots:
            last[kind] = spots[-1]
    return max(last, key=last.get) if last else None


def profile_for(user, text=None, job=None):
    """회원 → Profile. 회원이 없으면(로컬 확인 · 평가) 빈 Profile. 생년월일이 없으면 birth_date=None — 부르는 쪽이 안내한다
    취업 상태 우선순위: 이 문장에서 말한 것 > 이 대화에서 마지막으로 말한 것(job, "jobless" · "employed") > 월급 정보.
    말한 재직은 월급 정보가 없어도 재직자다"""
    info = getattr(user, "financial_profile", None) if user is not None else None
    said = said_job(text) or job
    job = SAID_JOB[said] if said in SAID_JOB else \
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
    """신청 상태 문구. 상세 · 더 보기는 지난 추천을 다시 읽으므로 그사이 마감된 정책도 온다 (saved.apply_state 가 closed 를 가린다)"""
    state = saved.apply_state(policy, today)
    if state == "closed":
        return STATE["closed"] + (f" ({policy.last_apply_end})" if policy.last_apply_end else "")
    if state in ("open", "upcoming") and policy.periods:
        day = today.isoformat()
        now = next((p for p in policy.periods if p["end"] >= day), policy.periods[-1])
        return f"{STATE[state]} ({now['start']} ~ {now['end']})"
    return STATE[state]


def tidy(text, limit=SUPPORT_CHARS):
    """원문 앞부분을 읽기 쉽게: 글머리 기호는 ' · ' 로 (맨 앞은 지움), 따옴표 · 겹친 빈칸을 정리하고
    limit 글자에서 낱말 단위로 자른다 (내용은 바꾸지 않는다)"""
    text = BULLETS.sub(" · ", (text or "").strip().strip('"'))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:· )+|(?: ·)+$", "", re.sub(r"(?:· )+", "· ", text)).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return (cut[:cut.rfind(" ")] if " " in cut[limit // 2:] else cut).rstrip(" ,·/") + "…"


def facts(found, today):
    """답변 한 칸의 사실 (모두 DB 값 · 코드 계산). 링크가 없으면 None — 지어내지 않는다"""
    policy, verdict = found.policy, found.verdict
    support = _clean(policy.raw, "plcySprtCn") or _clean(policy.raw, "plcyExplnCn")
    checks = [f"{c.item}({c.detail})" if c.item in ("나이", "소득") else c.item for c in verdict.to_check]
    return {
        "policy_no": policy.pk,
        "name": policy.name,
        "period": _period(policy, today),
        "support": tidy(support),
        "check": checks,
        "soon_over": verdict.soon_over,
        "link": policy.apply_url or (policy.ref_urls[0] if policy.ref_urls else None),
        "keywords": found.keywords,
    }


DETAIL_CHARS = 300  # 상세는 추천 카드(80자)보다 길게, 그래도 원문 앞부분만
DETAIL_LABEL = {"support": "지원 내용", "period": "신청", "eligibility": "자격", "apply": "신청 방법",
                "documents": "제출 서류", "other": "그 밖"}
DETAIL_KEYS = {"support": ("plcySprtCn", "plcyExplnCn"), "apply": ("plcyAplyMthdCn",), "documents": ("sbmsnDcmntCn",),
               "other": ("etcMttrCn",)}


def detail(policy_no, topics, profile, today):
    """정책 하나의 원문 항목 (8단계 정책 상세). → {name, link, agency, rows: [(이름, 값 또는 None)]} 또는 None(없는 정책).
    원문이 비어 있는 항목은 None — 지어내지 않고 부르는 쪽이 '원문에 없음'으로 안내한다"""
    found = again([policy_no], profile, today)
    if not found:
        return None
    policy, verdict = found[0].policy, found[0].verdict
    rows = []
    for topic in DETAIL_LABEL:  # 항목 순서는 고정 (LLM 이 고른 순서와 상관없이)
        if topic not in topics:
            continue
        if topic == "period":
            value = _period(policy, today)
        elif topic == "eligibility":
            parts = [f"{c.item}({c.detail})" for c in verdict.conditions]  # 맞는 조건도 보인다 ("일해도 돼?" → 취업 상태)
            extra = _clean(policy.raw, "addAplyQlfcCndCn")
            value = tidy(" · ".join(parts + ([extra] if extra else [])), DETAIL_CHARS) or None
        else:
            value = tidy(next((v for v in (_clean(policy.raw, k) for k in DETAIL_KEYS[topic]) if v), ""), DETAIL_CHARS) or None
        rows.append((DETAIL_LABEL[topic], value))
    agency = _clean(policy.raw, "operInstCdNm") or _clean(policy.raw, "sprvsnInstCdNm") or None
    return {"name": policy.name, "link": policy.apply_url or (policy.ref_urls[0] if policy.ref_urls else None),
            "agency": agency, "rows": rows}


def policy_name(policy_no):
    """정책번호 → 정책명 (DB 값). 없으면 번호 그대로"""
    return Policy.objects.filter(pk=policy_no).values_list("name", flat=True).first() or policy_no


def data_date():
    """정책 데이터 기준일 (마지막 성공 수집). 없으면 None"""
    last = PolicySync.objects.filter(status=PolicySync.Status.OK).order_by("-finished_at").first()
    return last and last.data_date
