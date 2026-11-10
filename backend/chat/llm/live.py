"""실제 LLM 해석 (CHAT_LLM = "live" 이거나 로컬 채팅 --live 일 때만) — 역할별 Structured Outputs 호출 어댑터 + 호출 장부.
재시도 없음 · 모델을 바꾸지 않는다. 키 · 모델은 환경변수(OPENAI_API_KEY · LLM_MODEL)에서만 읽고 출력하지 않는다. 요청 · 응답 원문은 남기지 않는다.
spending_fixture/live_reading.py 에서 옮김 (MEMO 25-12)
"""
import os

from .openai_rest import SERVICE_STOP, ResponsesClient
from .schemas import schema_for


class LiveReading:
    """B 가설 확인 · 추가 판단도 같은 클라이언트 · 상한 · 장부를 쓴다.
    상한은 call_limit으로 안내하고, 그 밖의 호출 실패는 해석 실패로 답한다 (대화 상태 그대로)"""

    def __init__(self, max_calls, ledger_path, model=None):
        """max_calls = 이 해석기의 호출 상한(None = 상한 없음), ledger_path = 저장소 밖 장부(None = 장부 없이 메모리 수만).
        장부가 있으면 장부의 상한(처음 만들 때 고정)도 같이 지킨다"""
        key, model = os.environ.get("OPENAI_API_KEY"), model or os.environ.get("LLM_MODEL")
        if not key or not model:
            raise SystemExit("OPENAI_API_KEY · LLM_MODEL 이 필요해요")
        self.ledger = None
        if ledger_path:
            from .ledger import Ledger  # 장부를 쓰는 경로에서만 파일 잠금 의존성이 필요하다
            self.ledger = Ledger(ledger_path, max_calls)
        # 서비스: 429 · 400 은 그 요청만 실패(다음 요청은 보냄), 설정 오류(인증 · 권한 · 모델)와 상한만 계속 막는다
        self.client = ResponsesClient(model, key, max_calls, schema=schema_for, stop_on=SERVICE_STOP,
                                      on_attempt=self.ledger.spend if self.ledger else None)
        self.model, self.missed = model, 0

    def __call__(self, system, user, timeout=None):
        return self.client.complete(system, user, timeout)

    def usage(self):
        used, limit = (self.ledger.data["used"], self.ledger.data["limit"]) if self.ledger else \
            (len(self.client.attempts), self.client.max_calls)
        return {"used": used, "limit": limit, "blocked": self.client.blocked,
                "failed": sum(1 for a in self.client.attempts if a["kind"] not in (None, "ok"))}
