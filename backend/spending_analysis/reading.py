"""소비 분석 에이전트의 해석: 원문(+ 자기 되묻기 · 직전 조건) → SpendingReading. 형식 검사 · 프롬프트 · LLM 호출
LLM 호출은 complete(system, user) -> str 로 주입한다. 원문은 LLM 호출에만 쓰고 기록하지 않는다.

단계 B 분리: 기존 통합 형식(chat/interpretation)의 소비 칸을 그대로 가져오고, 의도(intents) 대신 mine(자기 분야 여부)을 둔다.
예산 조정 보조값 · 라우터 질문 답(choice) · 재개(resume)는 라우터 몫이라 없다.
이 프롬프트는 아직 실제 LLM 으로 평가하지 않았다 (MEMO 13번 평가 계획).
"""
import json
from dataclasses import dataclass, field
from importlib import import_module

# shortcut: 초기 데이터 상수를 읽는다, 통합 때 Category(is_active=True, display_order 순) 조회로 바꾼다
CATEGORY_NAMES = {code: name for code, name, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES}
TARGETS = ("spend", "budget_left", "overview")
PERIODS = ("this_month", "last_month", "recent", "this_week", "last_week", "month", "range")
COMPARES = ("prev_month", "category", "other_period", "month")
FOLLOWUPS = ("answer", "new", "cancel", "unclear")
REFERS = ("previous", "none", "unclear")
PAYMENTS = ("card", "bank")
EXPLAIN_KINDS = ("change", "composition", "allocation")
WEEKDAYS = ("weekday", "weekend", "mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME_SLOTS = ("dawn", "morning", "lunch", "afternoon", "evening", "night", "late_night", "after_work")
MEASURES = ("sum", "count", "avg", "daily_avg", "max")
GROUPS = ("category", "merchant", "weekday", "time_slot", "month")


class ReadFailed(Exception):
    """LLM 이 응답하지 않거나 형식이 깨졌을 때"""


@dataclass(frozen=True)
class SpendingReading:
    mine: bool                          # 소비 분석 몫(새 소비 요청 · 소비 되묻기의 답 · 취소)인지. 아니면 not_mine
    target: str | None = None           # 무엇을: 쓴 돈 · 남은 예산 · 전반 분석
    period: str | None = None
    compare: str | None = None
    months: int | None = None           # 사용자가 말한 개월 수 그대로 (6이면 6)
    categories: tuple = ()              # 말한 순서대로, 코드 또는 {"unknown": 원문 조각}
    followup: str | None = None         # 소비 되묻기 중일 때만: 답변 · 새 요청 · 취소 · 불분명
    confirm: bool | None = None         # 현재 예산 기간으로 볼지 되묻기의 예/아니오
    unclear: tuple = field(default=())
    refers_to: str | None = None        # 직전 분석을 이어받는 후속 질문인지
    explain: bool | None = None         # 결과의 이유를 묻는 설명 요청
    after_budget_change: bool | None = None  # "바뀐 예산으로"
    all_categories: bool | None = None  # 카테고리 전체를 명시 ("전체는?")
    include_current: bool | None = None  # 최근 N개월에 진행 중인 이번 달을 포함한다고 명시 ("이번 달 포함 최근 3개월")
    alias: dict | None = None           # "데이트비는 식비로 볼게" — 이 대화의 조회 기준을 정하는 말 {word, category}
    payment: str | None = None          # "카드로 얼마 긁었어?" — 카드 결제만 (계좌 이체 · 자동이체 제외)
    explain_kind: str | None = None     # 설명 종류: change(왜 늘었/줄었) · composition(왜 이렇게 많이 · 어디에 썼는지)
    # 범용 조회 (MEMO 23절 2단계): 조건 · 계산 · 묶기
    weekday: tuple = ()                 # weekday(평일) · weekend(주말) · mon~sun
    time_slot: tuple = ()               # 시간대 6구간 + late_night(야식 = 밤 · 새벽) · after_work(퇴근 후 = 저녁 · 밤)
    amount_min: int | None = None       # 한 건 금액 조건 (원)
    amount_max: int | None = None
    measure: str | None = None          # sum · count(몇 번) · avg(건당 평균) · daily_avg(하루 평균) · max(가장 큰 1건)
    group_by: str | None = None         # category · merchant · weekday · time_slot · month
    top_n: int | None = None            # 상위 N (1~5, 서버가 상한)
    # 지정 기간 · 고정지출 (사용자 테스트 보완 10/30)
    month: int | None = None            # period="month" 일 때 몇 월 (1~12)
    date_from: str | None = None        # period="range" 일 때 "MM-DD"
    date_to: str | None = None
    period_said: str | None = None      # 사용자가 말한 기간 표현 원문 (코드가 해석이 기간을 놓쳤는지 안다)
    include_fixed: bool | None = None   # "고정지출 포함해서"
    hypothesis: dict | None = None      # 분석 가설: 지표·대상 코드는 조사 계약을 공유한다
    compare_month: int | None = None    # compare="month" 일 때 견줄 달 (1~12) — "9월보다" · "11월과 9월 비교"
    other_person: bool | None = None    # 본인이 아닌 사람(가족 · 친구 · 연인 등)의 소비를 물음 — 볼 수 없다 (서버가 '내 것'을 제안)
    multi_step: bool | None = None      # 비교 뒤 한 번 더 확인할 새 질문 (CHAT_AGENT 가 켜졌을 때 추가 판단 1회, MEMO 31)


def _one_of(data, key, allowed):
    value = data.get(key)
    if value is not None and value not in allowed:
        raise ReadFailed(key)
    return value


def _int(data, key):
    value = data.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):  # bool 은 int 의 하위형
        raise ReadFailed(key)
    return value


def _strings(data, key):
    value = data.get(key) or []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ReadFailed(key)
    return tuple(value)


def _mmdd(data, key):
    """'MM-DD' 또는 월을 말하지 않았으면 일만 'DD' (생략한 월 · 연도는 서버가 기준일로 채운다)"""
    value = data.get(key)
    if value is not None and not (isinstance(value, str) and (
            (len(value) == 5 and value[2] == "-" and value[:2].isdigit() and value[3:].isdigit())
            or (len(value) == 2 and value.isdigit()))):
        raise ReadFailed(key)
    return value


def _text(data, key):
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise ReadFailed(key)
    return value or None


def _choices(data, key, allowed):
    value = _strings(data, key)
    if any(v not in allowed for v in value):
        raise ReadFailed(key)
    return value


def _detail(one):
    """카테고리 안의 세부 지출 {detail: 원문, category: 속한 코드 | null, names?: 가게 이름의 다른 표기}.
    accepted=true 는 사용자가 대체 범위에 동의한 뒤 서버만 붙인다 (Structured Outputs 스키마에는 accepted 가 없다)"""
    names = one.get("names", []) if isinstance(one, dict) else None
    return isinstance(one, dict) and {"detail", "category"} <= set(one) <= {"detail", "category", "names", "accepted"} \
        and isinstance(one["detail"], str) and (one["category"] is None or one["category"] in CATEGORY_NAMES) \
        and isinstance(names, list) and all(isinstance(n, str) for n in names) \
        and one.get("accepted", True) is True and not (one.get("accepted") and one["category"] is None)


def _categories(data):
    value = data.get("categories") or []
    if not isinstance(value, (list, tuple)):
        raise ReadFailed("categories")
    for one in value:
        known = isinstance(one, str) and one in CATEGORY_NAMES
        unknown = isinstance(one, dict) and set(one) == {"unknown"} and isinstance(one["unknown"], str)
        if not (known or unknown or _detail(one)):
            raise ReadFailed("categories")
    return tuple(value)


def _alias(data):
    value = data.get("alias")
    if value is not None and not (isinstance(value, dict) and set(value) == {"word", "category"}
                                  and isinstance(value["word"], str) and value["word"].strip()
                                  and value["category"] in CATEGORY_NAMES):
        raise ReadFailed("alias")
    return value


def parse(data):
    """LLM JSON(dict) → SpendingReading. 모르는 칸 · 틀린 형식 · mine 없음은 ReadFailed"""
    if not isinstance(data, dict) or set(data) - set(SpendingReading.__dataclass_fields__):
        raise ReadFailed("shape")
    if not isinstance(data.get("mine"), bool):
        raise ReadFailed("mine")
    for key in ("confirm", "explain", "all_categories", "after_budget_change", "include_current", "include_fixed", "other_person", "multi_step"):
        if data.get(key) is not None and not isinstance(data[key], bool):
            raise ReadFailed(key)
    from .investigation import parse as parse_hypothesis
    try:
        hypothesis = parse_hypothesis(data.get("hypothesis"))
    except (ValueError, TypeError) as error:
        raise ReadFailed("hypothesis") from error
    if hypothesis is not None and data.get("explain") is not True:
        raise ReadFailed("hypothesis_without_explanation")
    return SpendingReading(
        mine=data["mine"], hypothesis=hypothesis, target=_one_of(data, "target", TARGETS), period=_one_of(data, "period", PERIODS),
        compare=_one_of(data, "compare", COMPARES), months=_int(data, "months"), categories=_categories(data),
        followup=_one_of(data, "followup", FOLLOWUPS), confirm=data.get("confirm"), unclear=_strings(data, "unclear"),
        refers_to=_one_of(data, "refers_to", REFERS), explain=data.get("explain"),
        after_budget_change=data.get("after_budget_change"), all_categories=data.get("all_categories"),
        include_current=data.get("include_current"), alias=_alias(data), payment=_one_of(data, "payment", PAYMENTS),
        explain_kind=_one_of(data, "explain_kind", EXPLAIN_KINDS),
        weekday=_choices(data, "weekday", WEEKDAYS), time_slot=_choices(data, "time_slot", TIME_SLOTS),
        amount_min=_int(data, "amount_min"), amount_max=_int(data, "amount_max"),
        measure=_one_of(data, "measure", MEASURES), group_by=_one_of(data, "group_by", GROUPS), top_n=_int(data, "top_n"),
        month=_int(data, "month"), date_from=_mmdd(data, "date_from"), date_to=_mmdd(data, "date_to"),
        period_said=_text(data, "period_said"), include_fixed=data.get("include_fixed"), other_person=data.get("other_person"),
        compare_month=_int(data, "compare_month"), multi_step=data.get("multi_step"),
    )


def find_category(text):
    """사용자가 고른 코드 · 화면 이름(가운뎃점 · 띄어쓰기 무시) → 코드. 없으면 None"""
    said = text.strip().replace("·", "").replace(" ", "")
    for code, name in CATEGORY_NAMES.items():
        if said in (code, name.replace("·", "").replace(" ", "")):
            return code
    return None


PROMPT = f"""너는 가계부 챗봇의 '소비 분석' 담당 해석기다. 사용자 문장을 JSON 하나로만 답한다. 설명 · 계산은 하지 않는다.
설명 질문의 가설은 hypothesis_contract와 hypothesis_targets에 따라 hypothesis에만 적는다.
categories·weekday·time_slot은 설명할 결과의 조회 조건이다. 원인으로 짐작한 대상은 hypothesis에 넣고 조회 조건에 중복하지 않는다.
직전 분석의 이유를 물으면 refers_to=previous로 결과 범위를 유지한다. 명시적으로 새 범위를 조회하라는 요청이면 그 조건을 보존한다.
먼저 mine 을 정한다:
- mine=true: 내 소비 · 남은 예산의 조회 · 분석 · 비교 · 추이 · 설명 요청, pending(내가 물은 질문)의 답 · 취소,
  또는 이 대화에서 어떤 말을 어느 카테고리로 조회할지 정하는 말("데이트비는 식비로 볼게" → alias).
- mine=false: 예산을 늘리기/줄이기/바꾸기(예산을 보거나 나눠 보는 조회는 mine=true), 청년 정책 추천, 그 밖의 주제, 소비 분석과 다른 일을 함께 요청한 문장, 뜻을 알 수 없는 말.
  '의료비 줄이고싶어', '의료비 줄이고 싶다고', '병원비 좀 줄여줘'는 예산 감액 의사이므로 mine=false다. '예산'을 생략해도 소비 조회로 해석하지 않는다.
  mine=false 면 다른 칸은 비운다 (어디로 보낼지는 정하지 않는다).
- 내 소비 · 지출을 묻는데 무엇을 가리키는지만 불분명하면 '뜻을 알 수 없는 말'이 아니다 → mine=true, refers_to="unclear" (범위는 내가 되묻는다).
- 입력의 chosen 이 true 면 사용자가 이 문장에서 소비 분석을 고른 것이다 → 소비 분석 부분만 해석하고 mine=true.
추출 원칙:
0. 예산을 왜 그 금액으로 책정·배정했는지 묻는 말('의료비 예산 왜 이렇게 크게 잡았어?', '식비 예산 책정 근거 알려줘')은
   mine=true, target=budget_left, explain=true, explain_kind=allocation이다. 의료비·병원비는 MEDICAL_HEALTH다.
   이것은 지출 증감·지출 구성의 원인 질문이 아니다. 별도로 말하지 않은 지난달 비교나 소비 조회 조건을 만들지 않는다.
1. 이번 문장에서 말한 조건만 채운다. 기본값·지원 여부·조건 충돌·날짜 계산은 서버가 처리한다. 말하지 않은 칸은 null/빈 목록.
2. previous는 refers_to 판단에만 쓴다. 후속 질문도 이번에 바꾼 조건만 적고 이전 조건을 복사하지 않는다.
3. 기간·비교·개월·전체·카테고리처럼 실제로 따로 말한 조건은 모두 보존한다. 전체/생활비는 카테고리가 아니라 전체 범위다.
   생활비 잔액 요청은 target=budget_left, categories=[]이며 unknown/detail로 만들지 않는다.
   예산을 넘은(초과한) 카테고리가 무엇인지 · 있는지 묻는 말도 예산 조회다(예산을 바꾸는 요청이 아니다)
   → mine=true, target=budget_left, compare="category", categories=[] ("초과한 카테고리 뭐야?", "예산 넘은 항목 있어?").
4. 지난달보다/대비 또는 지난달과·이랑·하고 비교·분석하는 요청은 현재와 지난달의 비교다 → compare=prev_month, period=null.
   비교 기준만 말한 요청도 동일하다. 비교·증감 조회 자체는 이유 질문이 아니므로 explain=null, hypothesis=null이다.
   금액이 많은지·적은지·괜찮은지 평가만 덧붙여 물어도 이유 질문이 아니다(explain=null).
   지난달이 아닌 특정 달과 견주면 compare=month, 견줄 달을 compare_month에 적는다. 견주는 쪽이 특정 달이면 period=month와 month에, 이번 달이거나 말하지 않았으면 period=null.
   이번 달과 지난달을 함께 말해도("이번달 식비를 지난달과 비교") 보는 쪽은 이번 달이다 → period=null, compare=prev_month.
   period=last_month와 compare=prev_month를 함께 쓰지 않는다. 지난달을 다른 특정 달과 견줄 때만("지난달이랑 8월 비교") period=last_month, compare=month.
   지난달 자체의 합계·분석만 요청하고 현재와 견주지 않을 때만 period=last_month이다. 분석이라는 말이 비교 기준을 없애지 않는다.
5. 품목·가게·세부 지출은 코드로 바꾸지 않고 categories에 {{"detail": "원문 대상", "category": 속한 코드 또는 null, "names": []}}.
   가게 이름의 다른 한글·영문·법인 표기만 names에 넣고, 품목·종류이면 비운다. 가게가 아닌 품목 범위가 여러 카테고리에 걸치거나 부모 분류를 정할 수 없으면 {{"unknown": "원문"}}로 둔다.
   식비·밥값·외식비는 서비스의 FOOD 범주 이름이다. 끼니를 시간대로 말하면 FOOD + time_slot. 지원 여부나 대체 범위는 서버가 정한다.
6. 설명할 대상과 짐작한 원인은 구분한다. 대상의 기간·카테고리·필터는 일반 조회와 똑같이 추출한다.
   짐작한 원인 쪽 말은 조회 조건에 넣지 않고 hypothesis에 적는다. 이유를 묻지 않는 합계·건수·증감 조회는 hypothesis=null.
칸:
- target: 먼저 소비 조회인지 예산 조회인지 구분한다. 예산 잔액·앞으로 쓸 수 있는 돈·예산을 나눠 보는 요청은 budget_left이며, 하루 허용액은 소비 평균이 아니다.
  소비 조회는 총액·지출액·결제 통계이면 spend, 전반적인 분석·평가·흐름이면 overview. 요청 말투나 지원 여부로 대상을 바꾸지 않는다.
  합계·계산을 말하지 않은 독립 소비 요청과 복합 요청에서 고른 전반 소비 분석은 overview. 대상 자체를 바꾸지 않고 카테고리·기간만 바꾸면 target=null이다.
  일반 조회의 추이는 period=recent와 months로 나타내고, 달별 묶기를 직접 요구하지 않으면 group_by=null이다.
- period: "this_month" | "last_month"(지난달 자체를 물음) | "recent"(최근 몇 달) | "this_week"(이번 주) | "last_week"(지난주)
  | "month"(특정 달 하나: month 칸에 1~12) | "range"(날짜 범위: date_from · date_to 에 "MM-DD", 끝을 말하지 않았으면 date_to=null).
  같은 달 날짜 범위에서 한 번 말한 월은 양 끝 날짜에 보존해 MM-DD로 쓴다. 월을 전혀 말하지 않았을 때만 DD로 쓰며 월·연도를 짐작하지 않는다.
- period_said: 거래를 조회할 달·주·날짜 범위의 원문. 표현할 수 없는 조회 기간(작년·상반기 등)도 보존하고 period만 비운다.
  예산의 남은 기간·하루 허용액, 평균의 계산 단위, 앞서 본 기간을 가리키는 말은 조회 기간 지정이 아니므로 null이다.
- include_fixed: "고정지출 포함해서 · 월세까지 넣어서"처럼 고정지출을 넣으라고 하면 true
- other_person: 본인이 아닌 다른 사람의 소비 · 예산을 물으면 true (mine=true 로 두고 나머지 조건은 그대로 채운다)
- compare: "prev_month"(지난달보다/대비) | "month"(특정 달과 비교, compare_month 에 1~12) | "category"(카테고리끼리 비교)
  | "other_period"(지난주 · 작년 등 달이 아닌 기간과 비교)
- months: 사용자가 말한 개월 수 정수 그대로 (6개월 → 6, 0개월 → 0)
- include_current: 최근 몇 달에 진행 중인 이번 달까지 포함한다고 말하면 true ("이번 달 포함 최근 3개월", "이번 달까지 3개월").
  이때 "이번 달"은 포함 표현이라 period 에 this_month 를 넣지 않는다. 포함/까지라는 명시가 없으면 null.
  이번 달과 개월 수를 단순히 함께 말한 경우에는 period=this_month와 months를 모두 보존해 서버가 기간을 되묻게 한다.
- 특정 카테고리를 말하지 않고 전체를 카테고리별로 나눠 보여 달라는 말("카테고리별로", "항목별로")은 compare="category" 로 둔다
  (서버가 카테고리별 목록으로 답한다). 후속 질문 "그럼 카테고리별로는?"도 compare="category", refers_to="previous".
  카테고리를 직접 나열하고 "각각"이라고 하면 비교가 아니다 → compare 는 비우고 categories 만
- categories: 말한 순서대로. 각 항목은 코드({", ".join(f"{c}={n}" for c, n in CATEGORY_NAMES.items())}) 또는
  위 코드에 확실히 맞지 않는 말이면 {{"unknown": "원문 그대로"}} (예: "데이트비랑 식비" → [{{"unknown": "데이트비"}}, "FOOD"])
- unclear: 해석이 불분명한 부분 설명 목록
- refers_to: previous가 있고 기간·범위를 새로 정하지 않은 채 대상(소비/남은 예산)·지표만 바꾸거나 일부 조건만 바꾸면 연결어 없이도 previous다.
  이전 분석과 무관한 독립 요청은 none. previous가 없어도 자체로 뜻이 통하는 합계·평균 조회는 none이며, 기간 생략만으로 unclear로 만들지 않는다.
  가리키는 범위를 알 수 없는 지시어 또는 바로 직전보다 더 이전 분석을 요구할 때만 unclear. pending 선택지의 답은 previous 참조와 구분한다.
  이전 조건을 복사하지 않는다. 빈 조건의 상속과 비교 연산 유지 여부는 서버가 처리한다.
- explain: 결과의 이유·배경을 묻거나 원인을 짐작하면 true. 물음표나 '왜'가 없어도 같다. 단순 조회·비교는 null.
- explain_kind: explain=true 일 때 무엇의 이유인지. 전보다 달라진(증가 · 감소) 이유면 "change" ("무엇 때문에 증가한 거야?"),
  금액 자체가 큰 이유나 어디에 썼는지면 "composition" ("이 금액이 나온 까닭이 뭐야?"). 판단이 안 되거나 explain 이 아니면 null
- all_categories: 특정 카테고리가 아니라 카테고리 전체를 명시하면 true ("그럼 전체는?", "전체 소비는?", "전체 예산은?").
  "전체 기간"처럼 카테고리가 아닌 범위를 뜻하면 넣지 않는다. 카테고리를 말하지 않았을 뿐이면 null
- alias: 이 대화에서 어떤 말을 어느 카테고리로 조회할지 정하거나 바꾸는 말이면 {{"word": "원문 그대로", "category": 코드}}
  ("데이트비는 식비로 볼게"). 그 밖에는 null. 조회도 함께 요청하면 다른 칸도 채운다
- payment: 카드로 쓴 돈만 묻으면 "card" ("카드 얼마 긁었지?", "카드로 결제한 거", "카드값"), 계좌 이체 · 계좌에서 나간 돈만이면
  "bank". 말하지 않았으면 null. "카드" · "계좌"는 결제 수단이지 카테고리가 아니다 (categories 에 넣지 않는다)
- 조건 · 계산 · 묶기 (말한 것만, 말하지 않았으면 비우거나 null):
  weekday: 평일 "weekday" · 주말 "weekend" · 특정 요일 "mon"~"sun" (목록)
  time_slot: 새벽 "dawn"(0~6시) · 아침 "morning"(6~11) · 점심 "lunch"(11~14) · 오후 "afternoon"(14~18) · 저녁 "evening"(18~21) ·
    밤 "night"(21~24), 야식 "late_night", 퇴근 후 "after_work" (목록)
  amount_min · amount_max: 한 번 결제 금액 조건(원 정수). "만 원 넘게 쓴 거" → amount_min=10000
  measure: 합계 "sum" · 몇 번 · 몇 건 "count" · 한 번에 평균 "avg" · 하루 평균 "daily_avg" · 가장 큰 지출 하나 "max".
    "얼마 썼어" · "합계" · "다 합치면"처럼 합계만 물으면 null (합계가 서버 기본 — sum 은 묶기와 함께일 때만)
  group_by: 가게별 "merchant" · 요일별 "weekday" · 시간대별 "time_slot" · 달별 "month" · 카테고리별 "category"
    (단 계산 없이 카테고리별 목록만 물으면 위 compare="category" 규칙)
  top_n: "상위 3곳" · "자주 간 가게 3곳" 같은 개수. 자주 = 횟수라 measure="count"
- after_budget_change: "바뀐 예산으로 분석해줘"처럼 예산 변경 뒤의 분석을 뜻하면 true (분석 종류 · 카테고리는 문장에 있는 것만)
- multi_step: 비교 결과를 보고 한 번 더 확인해야 하는 새 질문이면 true — 늘었다면 어디서(시간대·요일·가게) 늘었는지까지,
  예산과 소비를 함께 물을 때. 이유만 묻는 질문(explain) · 조회 하나로 끝나는 질문 · 이어서 묻는 질문 · pending 의 답은 null.
입력에 pending 이 있으면 내가 asking 항목을 되묻는 중이다. followup 을 채운다:
"answer"(질문에 대한 답) | "new"(새 요청) | "cancel"(그만두기) | "unclear"(답인지 모름, 예: "응").
asking 이 months 면 months 에, category 면 categories 에 (묻는 말이 여러 개면 그 순서대로),
explain_target 이면 고른 카테고리를 categories 에, period_choice(어느 기간인지)면 고른 기간 조건을 period · compare · months 에,
category_choice(전체인지 특정 카테고리인지)면 all_categories 또는 categories 에,
include_choice(이번 달까지 포함할지)면 포함 true · 완료된 달만 false 를 include_current 에,
budget_current(현재 예산 기간으로 볼지) · fallback_scope(대신 카테고리 전체를 볼지) · self_instead(대신 내 것을 볼지) ·
spend_instead(예산 대신 사용액을 볼지)면
동의 true · 거절 false 를 confirm 에. confirm 은 asking 이 위 넷일 때만 채운다. 그 밖에는 "응"이어도 confirm 은 null.
되묻는 중에 조건만 고치거나 선택지에 답하면 answer로 보고 고친 조건을 모두 채운다.
독립적인 합계·비교 질문으로 바꾸면 new다. 예산 조정 등 다른 기능의 새 요청은 mine=false이며 나머지 칸을 비운다."""


def read(text, pending, complete, previous=None, chosen=False):
    """→ SpendingReading. LLM 오류 · JSON 아님 · 형식 오류는 모두 ReadFailed
    pending = 소비 되묻기(묻는 항목 · 선택지만), previous = 직전 분석 조건(금액 · 결과 없음), chosen = 사용자가 소비 분석을 고름"""
    options = [{k: v for k, v in o.items() if k != "set"} if isinstance(o, dict) else o for o in pending["options"]] \
        if pending else None
    asked = pending and {"asking": pending["asking"], "options": options}
    try:
        from .investigation import INSTRUCTION, catalog
        payload = {"text": text, "pending": asked, "previous": previous, "chosen": chosen,
                   "hypothesis_contract": INSTRUCTION, "hypothesis_targets": catalog()}
        data = json.loads(complete(PROMPT, json.dumps(payload, ensure_ascii=False)))
    except Exception as error:  # SDK · 호출 오류 종류를 모른다
        raise ReadFailed("call_limit" if getattr(error, "kind", None) == "call_limit" else "call") from error
    return parse(data)
