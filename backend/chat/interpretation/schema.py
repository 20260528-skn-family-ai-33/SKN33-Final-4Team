"""① LLM 해석 결과의 형식 검사. 형식이 깨지면 InterpretFailed (값의 옳고 그름은 rules.py 가 본다)

- 칸이 없음(None) / 값이 잘못됨(예: months=0 → rules 가 되묻기) / 해석 실패(형식 오류 → InterpretFailed) 를 구분한다.
- categories 는 말한 순서대로 코드 문자열 또는 {"unknown": "원문 조각"} (알아보지 못한 말은 ETC 로 넣지 않고 그 자리에 둔다).
"""
from dataclasses import dataclass, field
from importlib import import_module

# 코드 · 화면 이름 원본 = DB 카테고리 초기 데이터 (API_RULES.md 표와 같음)
# shortcut: 초기 데이터 상수를 읽는다, 통합 때 Category(is_active=True, display_order 순) 조회로 바꾼다
CATEGORY_NAMES = {code: name for code, name, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES}

INTENTS = ("spending", "budget_adjust", "unsupported", "unclear")
TARGETS = ("spend", "budget_left", "overview")
PERIODS = ("this_month", "last_month", "recent")
COMPARES = ("prev_month", "category")
DIRECTIONS = ("up", "down")
AMOUNT_MODES = ("by", "to")  # by: 그만큼 늘리기/줄이기 (5만 원 줄여줘) · to: 그 금액으로 (5만 원으로 줄여줘)
FOLLOWUPS = ("answer", "new", "cancel", "unclear")
REFERS = ("previous", "none", "unclear")
# 대기 중 고르는 답: 의도 선택 · 취소 대상(라우터 질문 / 예산 조정 작업) · 기존 조정 작업 처리(이어서 / 취소하고 새로)
CHOICES = ("spending", "budget_adjust", "question", "budget_task", "resume", "restart")  # 직전 분석을 가리키는지: 가리킴 · 독립된 새 요청 · 모름


class InterpretFailed(Exception):
    """LLM 이 응답하지 않거나 형식이 깨졌을 때"""


@dataclass(frozen=True)
class Interpretation:
    intents: tuple                     # 복합 의도면 여러 개 (줄이지 않는다)
    target: str | None = None          # 무엇을: 쓴 돈 · 남은 예산 · 전반 분석
    period: str | None = None
    compare: str | None = None
    months: int | None = None          # 사용자가 말한 개월 수 그대로 (6이면 6)
    categories: tuple = ()             # 말한 순서대로, 코드 또는 {"unknown": 원문 조각}
    direction: str | None = None       # 예산 조정용
    amount: int | None = None          # 예산 조정용, 사용자가 말한 금액(원)만
    amount_mode: str | None = None     # amount 가 바뀌는 양(by)인지 목표 금액(to)인지
    followup: str | None = None        # 대기 중일 때만: 답변 · 새 요청 · 취소 · 불분명
    choice: str | None = None          # 대기 중 의도 선택 답 (spending · budget_adjust)
    confirm: bool | None = None        # 대기 중 예/아니오 답 (현재 예산 기간으로 볼지)
    unclear: tuple = field(default=())  # LLM 이 불분명하다고 본 부분 (설명 문자열)
    refers_to: str | None = None       # 직전 분석을 이어받는 후속 질문인지 (이어받기는 rules 가 한다)
    explain: bool | None = None        # "왜 늘었어?" 같은 설명 요청 (정형 조회로 바꾸지 않는다)
    resume: bool | None = None         # 보류한 예산 조정을 이어서 하자는 요청 ("아까 예산 조정 계속하자")
    after_budget_change: bool | None = None  # "바뀐 예산으로" — 최신 적용 예산 재조회가 필요하다는 표시
    all_categories: bool | None = None  # 카테고리 전체를 명시 ("전체는?", "전체 소비는?") — 언급 안 함(None)과 구분


def _one_of(data, key, allowed):
    value = data.get(key)
    if value is not None and value not in allowed:
        raise InterpretFailed(key)
    return value


def _int(data, key):
    value = data.get(key)
    # bool 은 int 의 하위형이라 따로 막는다
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise InterpretFailed(key)
    return value


def _strings(data, key, allowed=None):
    value = data.get(key) or []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise InterpretFailed(key)
    if allowed is not None and any(v not in allowed for v in value):
        raise InterpretFailed(key)
    return tuple(value)


def _categories(data):
    value = data.get("categories") or []
    if not isinstance(value, (list, tuple)):
        raise InterpretFailed("categories")
    for one in value:
        known = isinstance(one, str) and one in CATEGORY_NAMES
        unknown = isinstance(one, dict) and set(one) == {"unknown"} and isinstance(one["unknown"], str)
        if not (known or unknown):
            raise InterpretFailed("categories")
    return tuple(value)


def find_category(text):
    """사용자가 고른 코드 · 화면 이름(가운뎃점 · 띄어쓰기 무시) → 코드. 없으면 None"""
    said = text.strip().replace("·", "").replace(" ", "")
    for code, name in CATEGORY_NAMES.items():
        if said in (code, name.replace("·", "")):
            return code
    return None


def _hints(data):
    """카테고리 · 방향 · 금액 · by/to. 소비 분석에서는 조회 조건, 예산 조정에서는 보조값"""
    if data.get("amount") is None and data.get("amount_mode") is not None:
        raise InterpretFailed("amount_mode")
    return {
        "categories": _categories(data),
        "direction": _one_of(data, "direction", DIRECTIONS),
        "amount": _int(data, "amount"),
        "amount_mode": _one_of(data, "amount_mode", AMOUNT_MODES),
    }


def parse(data):
    """LLM JSON(dict) → Interpretation. 모르는 칸 · 틀린 형식은 InterpretFailed"""
    if not isinstance(data, dict) or set(data) - set(Interpretation.__dataclass_fields__):
        raise InterpretFailed("shape")
    intents = _strings(data, "intents", INTENTS)
    if not intents and data.get("followup") is None:
        raise InterpretFailed("intents")
    for key in ("confirm", "explain", "all_categories", "resume", "after_budget_change"):
        if data.get(key) is not None and not isinstance(data[key], bool):
            raise InterpretFailed(key)
    try:
        hints = _hints(data)
    except InterpretFailed:
        # 예산 조정만 요청했으면 이 칸들은 보조값일 뿐 → 통째로 비우고 원문을 넘긴다 (최종 해석은 예산 조정 에이전트)
        # 소비 분석이 섞이면 카테고리 등이 조회 조건이므로 그대로 실패
        if set(intents) != {"budget_adjust"}:
            raise
        hints = {}
    return Interpretation(
        intents=intents,
        target=_one_of(data, "target", TARGETS),
        period=_one_of(data, "period", PERIODS),
        compare=_one_of(data, "compare", COMPARES),
        months=_int(data, "months"),
        **hints,
        followup=_one_of(data, "followup", FOLLOWUPS),
        choice=_one_of(data, "choice", CHOICES),
        confirm=data.get("confirm"),
        unclear=_strings(data, "unclear"),
        refers_to=_one_of(data, "refers_to", REFERS),
        explain=data.get("explain"),
        all_categories=data.get("all_categories"),
        resume=data.get("resume"),
        after_budget_change=data.get("after_budget_change"),
    )
