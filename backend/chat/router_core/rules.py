"""라우터 규칙: 라우터 해석 → 목적지(담당 에이전트) · 라우터 질문 답 합치기 · 예산 전달 정보. LLM · DB · 상태 저장 없는 순수 함수

소비 분석 세부 규칙(유형 1~9 · 되묻기 · 이어받기 · 설명 근거)은 spending_analysis 가 맡는다.
"""
from dataclasses import dataclass, replace

AGENTS = ("spending", "budget", "policy")
# 기능 버튼 · 의도 선택 답 ↔ 라우터 해석의 의도 값
INTENT_OF = {"spending": "spending", "budget": "budget_adjust", "policy": "policy"}
AGENT_OF = {v: k for k, v in INTENT_OF.items()}
# 라우터가 직접 묻는 질문 (답은 담당 에이전트보다 라우터가 먼저 처리)
ROUTER_QUESTIONS = ("intent", "cancel_target", "budget_task_choice")


@dataclass(frozen=True)
class Decision:
    """라우터 결정: 에이전트 전달(handoff) · 라우터 질문 · 라우터 안내 · 실패"""
    # ready(전달) · ask(라우터 질문) · unsupported(범위 밖 · 미정 정책) · failed(해석 · 서버 확인 실패) · cancelled
    status: str
    intent: str | None = None
    agent: str | None = None      # 받을 에이전트 (라우터 자신의 질문 · 안내면 None)
    handoff: dict | None = None   # 에이전트 입력 (공통 계약 + 예산 기존 칸). input_id · today · as_of 는 run_turn 이 붙인다
    notices: tuple = ()           # 전환 안내 코드 (budget_task_suspended · budget_task_saving · policy_work_suspended)
    asking: str | None = None     # 라우터 질문: intent · cancel_target · budget_task_choice
    ask_about: tuple = ()
    options: tuple = ()
    reason: str | None = None
    declined: tuple = ()          # 이번 입력을 not_mine 으로 돌려준 에이전트 (전달 순서대로)
    reading: object = None        # 라우터 해석 (분류했을 때만)
    classified: bool = False      # 이번 입력을 라우터가 분류해서 정한 전달인지 (거절되면 다시 분류하지 않는다)


def destinations(reading):
    """해석 → 맡을 에이전트 목록 (기능 순서). 둘 이상이면 무엇부터 할지 묻는다"""
    return [AGENT_OF[i] for i in ("spending", "budget_adjust", "policy") if i in reading.intents]


def budget_request(reading, text):
    """예산 조정 전달 정보: 원문 + [미정 유지] 보조값 (세부 해석은 예산 조정 에이전트 몫)"""
    hints = {"categories": list(reading.categories), "direction": reading.direction,
             "amount": reading.amount, "amount_mode": reading.amount_mode}
    return Decision("ready", "budget_adjust", agent="budget", handoff={
        "task": "budget_adjust", "text": text, "hints": {k: v for k, v in hints.items() if v not in (None, [])},
    })


def merge_intent(request, answer, options):
    """'무엇부터 할까요?'(의도 선택 · 목적지 확인) 답 → 고른 의도만 남긴 요청. 선택지에 없으면 None"""
    if answer.choice in options:
        return replace(request, intents=(answer.choice,))
    return None
