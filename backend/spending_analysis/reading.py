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
PERIODS = ("this_month", "last_month", "recent")
COMPARES = ("prev_month", "category")
FOLLOWUPS = ("answer", "new", "cancel", "unclear")
REFERS = ("previous", "none", "unclear")


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
    explain: bool | None = None         # "왜 늘었어?" 같은 설명 요청
    after_budget_change: bool | None = None  # "바뀐 예산으로"
    all_categories: bool | None = None  # 카테고리 전체를 명시 ("전체는?")
    include_current: bool | None = None  # 최근 N개월에 진행 중인 이번 달을 포함한다고 명시 ("이번 달 포함 최근 3개월")
    alias: dict | None = None           # "데이트비는 식비로 볼게" — 이 대화의 조회 기준을 정하는 말 {word, category}


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


def _detail(one):
    """카테고리 안의 세부 지출 {detail: 원문, category: 속한 코드 | null}. accepted=true 는 사용자가 대체 범위에 동의한 뒤 서버만 붙인다
    (Structured Outputs 스키마에는 accepted 가 없다)"""
    return isinstance(one, dict) and set(one) in ({"detail", "category"}, {"detail", "category", "accepted"}) \
        and isinstance(one["detail"], str) and (one["category"] is None or one["category"] in CATEGORY_NAMES) \
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
    for key in ("confirm", "explain", "all_categories", "after_budget_change", "include_current"):
        if data.get(key) is not None and not isinstance(data[key], bool):
            raise ReadFailed(key)
    return SpendingReading(
        mine=data["mine"], target=_one_of(data, "target", TARGETS), period=_one_of(data, "period", PERIODS),
        compare=_one_of(data, "compare", COMPARES), months=_int(data, "months"), categories=_categories(data),
        followup=_one_of(data, "followup", FOLLOWUPS), confirm=data.get("confirm"), unclear=_strings(data, "unclear"),
        refers_to=_one_of(data, "refers_to", REFERS), explain=data.get("explain"),
        after_budget_change=data.get("after_budget_change"), all_categories=data.get("all_categories"),
        include_current=data.get("include_current"), alias=_alias(data),
    )


def find_category(text):
    """사용자가 고른 코드 · 화면 이름(가운뎃점 · 띄어쓰기 무시) → 코드. 없으면 None"""
    said = text.strip().replace("·", "").replace(" ", "")
    for code, name in CATEGORY_NAMES.items():
        if said in (code, name.replace("·", "").replace(" ", "")):
            return code
    return None


PROMPT = f"""너는 가계부 챗봇의 '소비 분석' 담당 해석기다. 사용자 문장을 JSON 하나로만 답한다. 설명 · 계산은 하지 않는다.
먼저 mine 을 정한다:
- mine=true: 내 소비 · 남은 예산의 조회 · 분석 · 비교 · 추이 · 설명 요청, pending(내가 물은 질문)의 답 · 취소,
  또는 이 대화에서 어떤 말을 어느 카테고리로 조회할지 정하는 말("데이트비는 식비로 볼게" → alias).
- mine=false: 예산을 늘리기/줄이기/바꾸기, 청년 정책 추천, 그 밖의 주제, 소비 분석과 다른 일을 함께 요청한 문장, 뜻을 알 수 없는 말.
  mine=false 면 다른 칸은 비운다 (어디로 보낼지는 정하지 않는다).
- 내 소비 · 지출을 묻는데 무엇을 가리키는지만 불분명하면 '뜻을 알 수 없는 말'이 아니다 → mine=true, refers_to="unclear" (범위는 내가 되묻는다).
- 입력의 chosen 이 true 면 사용자가 이 문장에서 소비 분석을 고른 것이다 → 소비 분석 부분만 해석하고 mine=true.
원칙 (mine=true 일 때):
1. **이번 문장에서 사용자가 말한 조건만** 채운다. 말하지 않은 칸은 null 또는 빈 목록 (기본값을 넣지 않는다).
2. 입력의 previous(직전 분석 조건)는 refers_to 를 판단하는 데만 쓴다. previous 의 target · period · compare · months ·
   categories 를 이번 추출값에 복사하지 않는다 — 빠진 조건은 서버가 이어받는다.
3. 후속 질문은 이번에 바꾼 것만 적는다. 카테고리만 바꾸면 categories 만, 카테고리 전체로 넓히면 all_categories 만,
   기간만 바꾸면 period · months 만 채우고 나머지 조건은 null.
   예) previous 가 "최근 3개월 의료비"일 때 "쇼핑 쪽은?" → refers_to="previous", categories=["FASHION_SHOPPING"], target · period · months = null
4. "생활비" · "전체"처럼 범위 전체를 가리키는 말은 특정 카테고리가 아니다 → categories 를 비운다.
   단 그 안의 특정 카테고리를 말하면 그 카테고리만 넣는다. 예) "생활비 중 의료비" → categories=["MEDICAL_HEALTH"]
5. 서로 맞지 않는 조건을 함께 말해도 하나를 고르거나 고쳐 쓰지 않는다. 말한 조건을 각자의 칸에 모두 채운다
   (보려는 기간 이번 달 · 지난달 → period, N개월 → months, 비교 기준 → compare, 전체 → all_categories, 특정 카테고리 → categories).
   어느 쪽인지는 서버가 사용자에게 되묻는다. 예) "지난달 2개월 교통비" → period="last_month", months=2, categories=["TRANSPORT_CAR"]
6. "지난달보다" · "지난달 대비" · "지난달이랑 비교"의 지난달은 비교 기준이다 → compare="prev_month" 만 채우고 period 는 비운다.
   period="last_month" 는 지난달 자체를 볼 때만. 예) "지난달 대비 교통비 늘었어?" → compare="prev_month", period=null
7. 카테고리 안의 품목 · 가맹점 · 세부 지출(커피, 배달, 택시, 넷플릭스 등)은 카테고리 자체가 아니다 → categories 에
   {{"detail": "원문 그대로", "category": 속한 코드 또는 속한 곳이 분명하지 않으면 null}} 로 넣는다 (코드로 바꾸지 않는다).
   여러 카테고리에 걸친 사용자만의 이름(데이트비 등)은 {{"unknown": ...}}.
칸:
- target: 사용자가 무엇을 알고 싶은지(분석 대상)로 정한다. "알려줘" · "보여줘" 같은 요청 말투는 target 을 정하지 않는다.
  "spend" = 총액 · 합계 · 얼마 썼는지 | "budget_left" = 남은 예산 | "overview" = 전반적인 분석 · 평가(어때, 분석, 흐름)
  분석 대상을 따로 말하지 않은 독립된 소비 조회 요청은 overview. 후속 질문에서 대상을 말하지 않았으면 null (overview 로 채우지 않는다).
  예) "병원비 합계 보여줘" → spend, "병원비 예산 남은 거 알려줘" → budget_left, "병원비 쓴 거 어때?" → overview,
      "병원비 좀 보여줘" → overview (대상을 말하지 않은 독립 조회)
- period: "this_month" | "last_month"(지난달 자체를 물음) | "recent"(최근 몇 달)
- compare: "prev_month"(지난달보다/대비) | "category"(카테고리끼리 비교)
- months: 사용자가 말한 개월 수 정수 그대로 (6개월 → 6, 0개월 → 0)
- include_current: 최근 몇 달에 진행 중인 이번 달까지 포함한다고 말하면 true ("이번 달 포함 최근 3개월", "이번 달까지 3개월").
  이때 "이번 달"은 포함 표현이라 period 에 this_month 를 넣지 않는다. 말하지 않았으면 null
- categories: 말한 순서대로. 각 항목은 코드({", ".join(f"{c}={n}" for c, n in CATEGORY_NAMES.items())}) 또는
  위 코드에 확실히 맞지 않는 말이면 {{"unknown": "원문 그대로"}} (예: "데이트비랑 식비" → [{{"unknown": "데이트비"}}, "FOOD"])
- unclear: 해석이 불분명한 부분 설명 목록
- refers_to: 입력의 previous(직전 분석 조건)를 이어받는 후속 질문이면 "previous"(예: "쇼핑 쪽은?", "이번 달로 보면?"),
  그 자체로 완결된 새 요청이면 "none", 판단이 안 되면 "unclear".
  previous 가 null 인데 문장이 앞선 분석을 이어받아야 뜻이 통하면 "unclear"
- explain: 결과의 이유 · 배경을 묻는 질문이면 true (예: "무엇 때문에 이렇게 나왔어?")
- all_categories: 특정 카테고리가 아니라 카테고리 전체를 명시하면 true ("그럼 전체는?", "전체 소비는?", "전체 예산은?").
  "전체 기간"처럼 카테고리가 아닌 범위를 뜻하면 넣지 않는다. 카테고리를 말하지 않았을 뿐이면 null
- alias: 이 대화에서 어떤 말을 어느 카테고리로 조회할지 정하거나 바꾸는 말이면 {{"word": "원문 그대로", "category": 코드}}
  ("데이트비는 식비로 볼게"). 그 밖에는 null. 조회도 함께 요청하면 다른 칸도 채운다
- after_budget_change: "바뀐 예산으로 분석해줘"처럼 예산 변경 뒤의 분석을 뜻하면 true (분석 종류 · 카테고리는 문장에 있는 것만)
- previous 는 바로 직전 분석 하나뿐이다. "그 전에 본 거"처럼 더 이전 분석을 가리키면 refers_to 는 "unclear"
입력에 pending 이 있으면 내가 asking 항목을 되묻는 중이다. followup 을 채운다:
"answer"(질문에 대한 답) | "new"(새 요청) | "cancel"(그만두기) | "unclear"(답인지 모름, 예: "응").
asking 이 months 면 months 에, category 면 categories 에 (묻는 말이 여러 개면 그 순서대로),
explain_target 이면 고른 카테고리를 categories 에, period_choice(어느 기간인지)면 고른 기간 조건을 period · compare · months 에,
category_choice(전체인지 특정 카테고리인지)면 all_categories 또는 categories 에,
include_choice(이번 달까지 포함할지)면 포함 true · 완료된 달만 false 를 include_current 에,
budget_current(현재 예산 기간으로 볼지) · fallback_scope(대신 카테고리 전체를 볼지)면 동의 true · 거절 false 를 confirm 에.
confirm 은 asking 이 budget_current · fallback_scope 일 때만 채운다. 그 밖에는 "응"이어도 confirm 은 null.
되묻는 중에 조건을 고쳐 말하면("아니다 이번 달 2개월") 답으로 보고 고친 조건을 모두 채운다.
새 요청(followup="new")이 소비 분석이 아니면 mine=false."""


def read(text, pending, complete, previous=None, chosen=False):
    """→ SpendingReading. LLM 오류 · JSON 아님 · 형식 오류는 모두 ReadFailed
    pending = 소비 되묻기(묻는 항목 · 선택지만), previous = 직전 분석 조건(금액 · 결과 없음), chosen = 사용자가 소비 분석을 고름"""
    options = [{k: v for k, v in o.items() if k != "set"} if isinstance(o, dict) else o for o in pending["options"]] \
        if pending else None
    asked = pending and {"asking": pending["asking"], "options": options}
    try:
        payload = {"text": text, "pending": asked, "previous": previous, "chosen": chosen}
        data = json.loads(complete(PROMPT, json.dumps(payload, ensure_ascii=False)))
    except Exception as error:  # SDK · 호출 오류 종류를 모른다
        raise ReadFailed("call") from error
    return parse(data)
