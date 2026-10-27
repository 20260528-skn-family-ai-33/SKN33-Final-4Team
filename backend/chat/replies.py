"""
의도별 봇 답변 (chat.md 의도 표 · '1차 답변')

각 함수는 (content_type, content) 를 돌려준다. content 모양은 chat.md '공통으로 쓰는 값'과 같다.
- 3단계: spending → 소비 분석 카드(서버 집계 + 템플릿 문장)로 교체 (세진 · 카드 칸 spending-analysis.md)
- 예산 조정 에이전트가 나오면: budget → 에이전트 호출 + task 로 교체 (나은 · 세진)
"""
from .router import CHOICES


def text(message, choices=()):
    return "text", {"text": message, "choices": [dict(c) for c in choices]}


def reply(intent, slots, fallback):
    if intent == "spending":
        # 임시: 소비 분석 카드는 3단계(집계)에서 붙인다
        return text("소비 분석 카드는 곧 연결돼요. 지금은 거래 화면에서 이번 달 소비를 볼 수 있어요.")
    if intent == "budget":
        # 임시 (10/27 결정): 예산 조정 챗봇은 1차 MVP 범위 — 예산 엔진·조정 에이전트가 나오면 교체
        return text("예산 조정은 곧 챗봇에서 할 수 있어요. 지금은 예산 화면에서 바꿔 주세요.")
    if intent == "big":
        return text("큰 지출 상담은 준비 중이에요.")
    if intent == "out":
        return text("머니쿠션은 소비 분석과 예산 조정을 도와드려요.", CHOICES)
    # unknown: 라우터 실패(fallback)와 진짜 unknown 의 문구가 다르다
    if fallback:
        return text("지금은 이해하지 못했어요, 아래에서 골라 주세요.", CHOICES)
    return text("조금 더 자세히 적어 주세요.", CHOICES)
