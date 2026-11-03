"""LLM 해석: 문장 → JSON → schema.parse. LLM 호출은 complete(system, user) -> str 로 바꿔 끼운다 (SDK 미정)

원문은 LLM 호출에만 쓰고 로그로 남기지 않는다. 대기 중이면 묻고 있는 항목 · 선택지만 함께 보낸다.
"""
import json

from .schema import CATEGORY_NAMES, InterpretFailed, parse

PROMPT = f"""너는 가계부 챗봇의 요청 해석기다. 사용자 문장을 JSON 하나로만 답한다. 설명 · 계산은 하지 않는다.
원칙:
1. **이번 문장에서 사용자가 말한 조건만** 채운다. 말하지 않은 칸은 null 또는 빈 목록 (기본값을 넣지 않는다).
2. 입력의 previous(직전 분석 조건)는 refers_to 를 판단하는 데만 쓴다. previous 의 target · period · compare · months ·
   categories 를 이번 추출값에 복사하지 않는다 — 빠진 조건은 서버가 이어받는다.
3. 후속 질문은 이번에 바꾼 것만 적는다. 카테고리만 바꾸면 categories 만, 카테고리 전체로 넓히면 all_categories 만,
   기간만 바꾸면 period · months 만 채우고 나머지 조건은 null.
   예) previous 가 "최근 3개월 의료비"일 때 "쇼핑 쪽은?" → refers_to="previous", categories=["FASHION_SHOPPING"], target · period · months = null
4. "생활비" · "전체"처럼 범위 전체를 가리키는 말은 특정 카테고리가 아니다 → categories 를 비운다.
   단 그 안의 특정 카테고리를 말하면 그 카테고리만 넣는다. 예) "생활비 중 의료비" → categories=["MEDICAL_HEALTH"]
칸:
- intents: ["spending"(소비 · 남은 예산 조회/분석), "budget_adjust"(예산을 늘리기/줄이기/바꾸기), "unsupported"(그 밖), "unclear"] 중 해당하는 것 모두
- target: 사용자가 무엇을 알고 싶은지(분석 대상)로 정한다. "알려줘" · "보여줘" 같은 요청 말투는 target 을 정하지 않는다.
  "spend" = 총액 · 합계 · 얼마 썼는지 | "budget_left" = 남은 예산 | "overview" = 전반적인 분석 · 평가(어때, 분석, 흐름)
  분석 대상을 따로 말하지 않은 독립된 소비 조회 요청은 overview. 후속 질문에서 대상을 말하지 않았으면 null (overview 로 채우지 않는다).
  예) "병원비 합계 보여줘" → spend, "병원비 예산 남은 거 알려줘" → budget_left, "병원비 쓴 거 어때?" → overview,
      "병원비 좀 보여줘" → overview (대상을 말하지 않은 독립 조회)
- period: "this_month" | "last_month"(지난달 자체를 물음) | "recent"(최근 몇 달)
- compare: "prev_month"(지난달보다/대비) | "category"(카테고리끼리 비교)
- months: 사용자가 말한 개월 수 정수 그대로 (6개월 → 6, 0개월 → 0)
- categories: 말한 순서대로. 각 항목은 코드({", ".join(f"{c}={n}" for c, n in CATEGORY_NAMES.items())}) 또는
  위 코드에 확실히 맞지 않는 말이면 {{"unknown": "원문 그대로"}} (예: "데이트비랑 식비" → [{{"unknown": "데이트비"}}, "FOOD"])
- 예산 조정 보조값 (최종 해석은 예산 조정 담당이 한다. 확실하지 않으면 null):
  direction: "up" | "down", amount: 사용자가 말한 금액(원) 정수 그대로(계산 · 나누기 금지),
  amount_mode: "by"(그만큼, "5만 원 줄여줘") | "to"(그 금액으로, "5만 원으로 줄여줘")
- unclear: 해석이 불분명한 부분 설명 목록
- refers_to: 입력의 previous(직전 분석 조건)를 이어받는 후속 질문이면 "previous"(예: "쇼핑 쪽은?", "이번 달로 보면?"),
  그 자체로 완결된 새 요청이면 "none", 판단이 안 되면 "unclear".
  previous 가 null 인데 문장이 앞선 분석을 이어받아야 뜻이 통하면 "unclear" (이어받을 대상이 없으므로 previous 가 아님)
- explain: 결과의 이유 · 배경을 묻는 질문이면 true (예: "무엇 때문에 이렇게 나왔어?")
- all_categories: 특정 카테고리가 아니라 카테고리 전체를 명시하면 true ("그럼 전체는?", "전체 소비는?", "전체 예산은?").
  "전체 기간"처럼 카테고리가 아닌 범위를 뜻하면 넣지 않는다. 카테고리를 말하지 않았을 뿐이면 null
- resume: 보류해 둔 예산 조정을 이어서 하자는 말이면 true ("아까 예산 조정 계속하자"). 새 조정 요청이면 null
- after_budget_change: "바뀐 예산으로 분석해줘"처럼 예산 변경 뒤의 분석을 뜻하면 true (분석 종류 · 카테고리는 문장에 있는 것만)
- previous 는 바로 직전 분석 하나뿐이다. "그 전에 본 거"처럼 더 이전 분석을 가리키면 refers_to 는 "unclear"
입력에 pending 이 있으면 챗봇이 asking 항목을 되묻는 중이다. followup 을 채운다:
"answer"(질문에 대한 답) | "new"(새 요청) | "cancel"(그만두기) | "unclear"(답인지 모름, 예: "응").
asking 이 intent 면 고른 것을 choice("spending" | "budget_adjust")에, months 면 months 에,
category 면 categories 에 (묻는 말이 여러 개면 그 순서대로),
budget_current(현재 예산 기간으로 볼지)면 동의 true · 거절 false 를 confirm 에 넣는다.
confirm 은 asking 이 budget_current 일 때만 채운다. 그 밖에는 "응"이어도 confirm 은 null.
cancel_target 이면 choice 에 "question"(방금 질문 취소) | "budget_task"(예산 조정 취소),
budget_task_choice 면 choice 에 "resume"(기존 조정 이어서) | "restart"(기존 조정 취소하고 새로).
입력에 budget_task 가 있으면 예산 조정이 진행 중(suspended=false) 또는 보류(true)다. pending 이 없을 때도 followup 을 채운다:
"answer"(진행 중 조정에 대한 답, 예: "응", "그렇게 해줘") | "new"(다른 새 요청) | "cancel" | "unclear".
진행 중 조정에 대한 "응"은 담당 에이전트에게 넘길 답일 뿐이다 → followup="answer" 만, confirm · choice 는 null."""


def interpret(text, pending, complete, previous=None, budget_task=None):
    """→ Interpretation. LLM 오류 · JSON 아님 · 형식 오류는 모두 InterpretFailed
    previous = 직전 분석 조건(대상 · 기간 · 비교 · 개월 · 카테고리)만. 금액 · 결과는 보내지 않는다
    budget_task = 예산 조정 작업의 단계 · 보류 여부만. 작업 식별자는 보내지 않는다"""
    asked = pending and {"asking": pending["asking"], "options": list(pending["options"])}
    try:
        payload = {"text": text, "pending": asked, "previous": previous, "budget_task": budget_task}
        raw = complete(PROMPT, json.dumps(payload, ensure_ascii=False))
        data = json.loads(raw)
    except InterpretFailed:
        raise
    except Exception as error:  # SDK 미정이라 호출 오류 종류를 모른다
        raise InterpretFailed("call") from error
    return parse(data)
