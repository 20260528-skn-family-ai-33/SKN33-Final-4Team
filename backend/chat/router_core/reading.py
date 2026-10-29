"""메인 라우터의 해석: 원문(+ 라우터 질문 · 최소 맥락) → RouterReading. 형식 검사 · 프롬프트 · LLM 호출
LLM 호출은 complete(system, user) -> str 로 주입한다. 원문은 LLM 호출에만 쓰고 기록하지 않는다.

단계 B 분리: 분야 분류(소비 분석 · 예산 조정 · 청년 정책 추천 · 범위 밖 · 불분명), 라우터가 던진 질문의 답, 보류한 예산 조정 재개.
소비 세부 조건은 소비 분석 에이전트가 직접 해석한다.
[미정 유지] 예산 조정 보조값(categories · direction · amount · amount_mode)은 예산 담당자가 정할 때까지 기존처럼 뽑아 넘긴다.
청년 정책 의도는 이 프롬프트에 새로 넣었지만 실제 LLM 으로 평가하지 않았다 (MEMO 13번 평가 계획).
"""
import json
from dataclasses import dataclass, field
from importlib import import_module

CATEGORY_NAMES = {code: name for code, name, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES}
INTENTS = ("spending", "budget_adjust", "policy", "unsupported", "unclear")
FOLLOWUPS = ("answer", "new", "cancel", "unclear")
# 라우터 질문의 답: 의도 선택 · 취소 대상(질문 / 예산 조정 작업) · 보류한 조정 작업 처리(이어서 / 취소하고 새로)
CHOICES = ("spending", "budget_adjust", "policy", "question", "budget_task", "resume", "restart")
DIRECTIONS = ("up", "down")
AMOUNT_MODES = ("by", "to")


class ReadFailed(Exception):
    """LLM 이 응답하지 않거나 형식이 깨졌을 때"""


@dataclass(frozen=True)
class RouterReading:
    intents: tuple                     # 복합 의도면 여러 개 (줄이지 않는다)
    followup: str | None = None        # 라우터 질문 중일 때만: 답변 · 새 요청 · 취소 · 불분명
    choice: str | None = None          # 라우터 질문의 답
    resume: bool | None = None         # 보류한 예산 조정을 이어서 하자는 요청
    categories: tuple = ()             # [미정 유지] 예산 조정 보조값
    direction: str | None = None
    amount: int | None = None
    amount_mode: str | None = None
    unclear: tuple = field(default=())


def _one_of(data, key, allowed):
    value = data.get(key)
    if value is not None and value not in allowed:
        raise ReadFailed(key)
    return value


def _strings(data, key, allowed=None):
    value = data.get(key) or []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ReadFailed(key)
    if allowed is not None and any(v not in allowed for v in value):
        raise ReadFailed(key)
    return tuple(value)


def _hints(data):
    """예산 조정 보조값 형식 검사 (기존 통합 형식과 같은 규칙)"""
    if data.get("amount") is None and data.get("amount_mode") is not None:
        raise ReadFailed("amount_mode")
    amount = data.get("amount")
    if amount is not None and (not isinstance(amount, int) or isinstance(amount, bool)):
        raise ReadFailed("amount")
    categories = data.get("categories") or []
    if not isinstance(categories, (list, tuple)):
        raise ReadFailed("categories")
    for one in categories:
        known = isinstance(one, str) and one in CATEGORY_NAMES
        unknown = isinstance(one, dict) and set(one) == {"unknown"} and isinstance(one["unknown"], str)
        if not (known or unknown):
            raise ReadFailed("categories")
    return {"categories": tuple(categories), "direction": _one_of(data, "direction", DIRECTIONS),
            "amount": amount, "amount_mode": _one_of(data, "amount_mode", AMOUNT_MODES)}


def parse(data):
    """LLM JSON(dict) → RouterReading. 모르는 칸 · 틀린 형식은 ReadFailed"""
    if not isinstance(data, dict) or set(data) - set(RouterReading.__dataclass_fields__):
        raise ReadFailed("shape")
    intents = _strings(data, "intents", INTENTS)
    if not intents and data.get("followup") is None:
        raise ReadFailed("intents")
    if data.get("resume") is not None and not isinstance(data["resume"], bool):
        raise ReadFailed("resume")
    try:
        hints = _hints(data)
    except ReadFailed:
        # 예산 조정만 요청했으면 보조값일 뿐 → 통째로 비우고 원문을 넘긴다 (최종 해석은 예산 조정 에이전트)
        if set(intents) != {"budget_adjust"}:
            raise
        hints = {}
    return RouterReading(intents=intents, followup=_one_of(data, "followup", FOLLOWUPS),
                         choice=_one_of(data, "choice", CHOICES), resume=data.get("resume"),
                         unclear=_strings(data, "unclear"), **hints)


PROMPT = f"""너는 가계부 챗봇의 메인 라우터다. 사용자 문장을 어느 기능이 맡을지만 정해 JSON 하나로 답한다. 설명 · 계산 · 세부 조건 해석은 하지 않는다.
intents: 해당하는 것 모두 (복합이면 여러 개)
- "spending": 내 소비 · 남은 예산의 조회 · 분석 · 비교 · 추이 · 설명 (예: "이달 외식비 정리해줘", "병원비 예산 남은 거 알려줘")
- "budget_adjust": 예산을 늘리기 · 줄이기 · 바꾸기 (예: "교육비 예산 3만 원 올려줘")
- "policy": 청년 정책 · 지원 제도 추천이나 찾기 (예: "청년 월세 지원 받을 수 있어?")
- "unsupported": 위 세 기능이 아닌 주제 (예: "주식 종목 추천해줘")
- "unclear": 무엇을 원하는지 알 수 없음
- 소비 분석 직전 맥락(context.previous_spending)이 있고 문장이 그것을 이어받는 짧은 후속 질문이면 "spending" (예: "쇼핑 쪽은?")
- 내 소비 · 지출을 묻는 문장이면 무엇을 가리키는지 불분명해도(직전 맥락이 없어도) "spending". 가리키는 대상은 소비 분석이 되묻는다.
- 소비 조회에서 어떤 말을 어느 카테고리로 볼지 정하는 말도 "spending" (예: "데이트비는 식비로 볼게") — 예산을 바꾸는 요청이 아니다.
  "unclear" 는 어느 기능의 일인지조차 알 수 없을 때만 쓴다
- context.declined_by 가 있으면 그 기능이 이 문장을 자기 일이 아니라고 했다. 문장 뜻대로만 정한다
resume: 보류해 둔 예산 조정을 이어서 하자는 말이면 true ("아까 예산 조정 계속하자"). 새 조정 요청이면 null
예산 조정 보조값 (예산 조정 요청일 때만, 확실하지 않으면 null. 최종 해석은 예산 조정 담당이 한다):
  categories: 말한 순서대로 코드({", ".join(f"{c}={n}" for c, n in CATEGORY_NAMES.items())}) 또는 {{"unknown": "원문 그대로"}},
  direction: "up" | "down", amount: 사용자가 말한 금액(원) 정수 그대로(계산 · 나누기 금지),
  amount_mode: "by"(그만큼, "5만 원 줄여줘") | "to"(그 금액으로, "5만 원으로 줄여줘")
unclear: 분류가 불분명한 부분 설명 목록
입력에 pending 이 있으면 라우터가 asking 항목을 묻는 중이다. followup 을 채운다:
"answer"(질문에 대한 답) | "new"(새 요청) | "cancel"(그만두기) | "unclear"(답인지 모름, 예: "응").
asking 이 intent 면 고른 기능을 choice("spending" | "budget_adjust" | "policy")에,
cancel_target 이면 choice 에 "question"(방금 질문 취소) | "budget_task"(예산 조정 취소). 이때 취소할 대상을 고른 말은
"취소" 말투여도 질문의 답이다 → followup="answer",
budget_task_choice 면 choice 에 "resume"(기존 조정 이어서) | "restart"(기존 조정 취소하고 새로)."""


def read(text, pending, complete, context=None):
    """→ RouterReading. LLM 오류 · JSON 아님 · 형식 오류는 모두 ReadFailed
    pending = 라우터 질문(묻는 항목 · 선택지만), context = {active, budget_task, previous_spending, declined_by} 중 필요한 것만"""
    asked = pending and {"asking": pending["asking"], "options": list(pending["options"])}
    try:
        payload = {"text": text, "pending": asked, "context": context or {}}
        data = json.loads(complete(PROMPT, json.dumps(payload, ensure_ascii=False)))
    except Exception as error:  # SDK · 호출 오류 종류를 모른다
        raise ReadFailed("call") from error
    return parse(data)
