"""5단계 자격 거르기: 정책의 조건 칸과 사용자 조건을 코드로 비교한다. LLM 은 쓰지 않는다 (판단은 LLM, 사실은 코드).

조건 하나마다 위에서부터 처음 맞는 줄을 따른다.
1. 정책이 제한없음 → 충족
2. 정책 칸이 비었거나 사용자 값을 모름 → 확인 필요
3. 사용자 코드가 정책 코드에 있음 → 충족
4. 정책 코드에 '기타'가 있음 → 확인 필요
5. 그 밖 → 불충족 (추천에서 뺀다)
정책 하나의 결과는 가장 나쁜 조건을 따른다 (불충족 > 확인 필요 > 충족).

- 지역은 검색(search.candidates)이 이미 match 목록으로 걸렀다. 여기서 다시 보지 않는다.
- 소득은 탈락 근거로 쓰지 않는다 (금액 단위 · 본인/가구 구분을 알 수 없다). 무관이 아니면 확인 필요.
- 학력 · 전공 · 혼인은 묻지 않는다 → 제한없음이 아니면 확인 필요.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import codes
from .normalize import CHILD_AGE  # 나이 상한이 이보다 낮으면 신청자 나이가 아니라 자녀 나이다

MET, CHECK, UNMET = "met", "check", "unmet"
LABEL = {MET: "충족", CHECK: "확인 필요", UNMET: "불충족"}
_ORDER = {MET: 0, CHECK: 1, UNMET: 2}
FEMALE = "0014002"  # 특화 대상 '여성' — 성별(온보딩 값)로 판단한다
SOON_DAYS = 90  # 나이 상한에 이만큼 안에 닿으면 표시한다 ("다음 달이면 만 35세")


@dataclass(frozen=True)
class Profile:
    """사용자 조건. 정하는 건 부르는 쪽(6단계)이다: 온보딩 값 · 버튼 · "취업 준비 중" 같은 말"""
    birth_date: date | None = None
    gender: str | None = None  # 온보딩 값 그대로: "male" · "female"
    job: str | None = None  # 취업 상태 codes.JOB 코드 (월급 정보가 있으면 재직자)
    specials: frozenset | None = None  # 특화 대상 codes.SPECIAL 코드. None = 모름(건너뛰기), 빈 집합 = 해당 없음


@dataclass
class Condition:
    item: str  # 나이 · 취업 상태 · 특화 대상 · 학력 · 전공 · 혼인 · 소득
    status: str
    detail: str  # 정책이 요구하는 값 (답변 · 확인 명령에 그대로 보여 준다)


@dataclass
class Verdict:
    status: str
    conditions: list[Condition] = field(default_factory=list)
    soon_over: bool = False  # 나이 상한에 곧 닿는다

    @property
    def to_check(self):
        return [c for c in self.conditions if c.status == CHECK]

    @property
    def unmet(self):
        return [c for c in self.conditions if c.status == UNMET]


def age_on(birth_date, today):
    """만 나이"""
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def _next_birthday(birth_date, today):
    for year in (today.year, today.year + 1):
        try:
            day = birth_date.replace(year=year)
        except ValueError:  # 2월 29일생 → 평년엔 3월 1일
            day = date(year, 3, 1)
        if day > today:
            return day


def _age(policy, profile, today):
    low, high = policy.min_age, policy.max_age
    if low is None and high is None:
        return Condition("나이", CHECK, "나이 조건 정보 없음"), False
    detail = f"만 {low if low is not None else ''}~{high if high is not None else ''}세"
    if high is not None and high < CHILD_AGE:  # 부모급여(만 ~1세) 등 11건: 신청자가 아니라 자녀 나이가 적혀 있다
        return Condition("나이", CHECK, f"자녀 나이 조건으로 보임 ({detail})"), False
    if profile.birth_date is None:
        return Condition("나이", CHECK, detail), False
    age = age_on(profile.birth_date, today)
    if (low is not None and age < low) or (high is not None and age > high):
        return Condition("나이", UNMET, detail), False
    soon = high is not None and age == high and _next_birthday(profile.birth_date, today) - today <= timedelta(SOON_DAYS)
    return Condition("나이", MET, detail), soon


def _names(have, table):
    return " · ".join(table.get(code, code) for code in have)


def _coded(item, have, user, table, unlimited, other):
    """코드 목록 조건 하나 (규칙 1~5). user = 사용자 코드 집합, None 이면 모름"""
    if unlimited in have:
        return Condition(item, MET, "제한없음")
    if not have or user is None:
        return Condition(item, CHECK, _names(have, table) or "조건 정보 없음")
    if set(user) & set(have):
        return Condition(item, MET, _names(have, table))
    return Condition(item, CHECK if other in have else UNMET, _names(have, table))


def _special(policy, profile):
    have = policy.special_codes
    user = set(profile.specials or ())
    if profile.gender == "female":
        user.add(FEMALE)
    # 특화 대상을 건너뛰었어도 정책이 '여성'만 요구하면 성별로 알 수 있다
    known = profile.specials is not None or (bool(have) and set(have) <= {FEMALE} and profile.gender in ("male", "female"))
    if not known and user & set(have):
        known = True  # 여성인데 정책에 여성이 있으면 충족 (다른 특화 대상은 몰라도 된다)
    return _coded("특화 대상", have, user if known else None, codes.SPECIAL, "0014010", "0014009")


def _income(policy):
    if policy.income_code == "0043001":
        return Condition("소득", MET, "무관")
    note = policy.income_note or codes.INCOME.get(policy.income_code, "조건 정보 없음")
    return Condition("소득", CHECK, note[:60])


def judge(policy, profile, today):
    """정책 하나 → Verdict"""
    age, soon = _age(policy, profile, today)
    marriage = [policy.marriage_code] if policy.marriage_code else []
    conditions = [
        age,
        _coded("취업 상태", policy.job_codes, {profile.job} if profile.job else None, codes.JOB, "0013010", "0013009"),
        _special(policy, profile),
        _coded("학력", policy.school_codes, None, codes.SCHOOL, "0049010", "0049009"),
        _coded("전공", policy.major_codes, None, codes.MAJOR, "0011009", "0011008"),
        _coded("혼인", marriage, None, codes.MARRIAGE, "0055003", None),
        _income(policy),
    ]
    status = max((c.status for c in conditions), key=_ORDER.get)
    return Verdict(status, conditions, soon_over=soon)


def keep(hits, profile, today):
    """검색 결과에서 불충족을 뺀다 → [(hit, verdict)] (순서 그대로), 뺀 [(hit, verdict)]"""
    kept, dropped = [], []
    for hit in hits:
        verdict = judge(hit.policy, profile, today)
        (dropped if verdict.status == UNMET else kept).append((hit, verdict))
    return kept, dropped
