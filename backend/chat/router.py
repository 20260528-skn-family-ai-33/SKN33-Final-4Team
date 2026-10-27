"""
문장 → 의도 라우터 (chat.md '챗봇에 메시지 보내기', AGT-001)

- 4단계에서 LLM 라우터를 붙인다. 지금은 연결 전이라 route_text()가 항상 RouterUnavailable을 낸다.
- 라우터가 실패하면 API는 에러 대신 intent: unknown, fallback: true 로 답한다 (10/21 확정).
- 선택지(choice)로 온 요청은 라우터를 거치지 않는다 → route_choice().
"""

INTENTS = ("spending", "budget", "big", "out", "unknown")
# 화면에 보여 줄 선택지 (unknown · out 답변에 붙는다). value 가 choice 로 다시 들어온다
CHOICES = (
    {"value": "spending", "label": "소비 분석"},
    {"value": "budget", "label": "예산 조정"},
)
CHOICE_LABELS = {c["value"]: c["label"] for c in CHOICES}


class RouterUnavailable(Exception):
    """LLM 라우터가 응답하지 않거나 형식이 깨졌을 때"""


def route_choice(choice):
    """선택지 → (intent, slots). 라우터를 거치지 않으므로 항상 fallback=False"""
    return choice, {}


def route_text(text, conversation=None):
    """문장 → (intent, slots). LLM 라우터 연결 전이라 항상 실패로 처리한다.
    붙일 때: 이 함수만 바꾸고, 응답이 깨지면 RouterUnavailable 을 낸다. DB 잠금 밖에서 불린다."""
    raise RouterUnavailable("LLM 라우터 연결 전")
