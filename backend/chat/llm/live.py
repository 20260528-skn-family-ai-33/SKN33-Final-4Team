"""실제 LLM 해석 (CHAT_LLM = "live" 이거나 로컬 채팅 --live 일 때만) — 역할별 Structured Outputs 호출 어댑터 + 호출 장부.
재시도 없음 · 모델을 바꾸지 않는다. 키 · 모델은 환경변수(OPENAI_API_KEY · LLM_MODEL)에서만 읽고 출력하지 않는다. 요청 · 응답 원문은 남기지 않는다.
spending_fixture/live_reading.py 에서 옮김 (MEMO 25-12)
"""
import os

from .ledger import Ledger
from .openai_rest import ResponsesClient
from .schemas import schema_for


class LiveReading:
    """설명 LLM 도 같은 클라이언트 · 상한 · 장부를 쓴다 ("왜 늘었어?" 한 번에 1회 더).
    상한에 닿거나 호출이 실패하면 complete 가 예외 → 해석기(read)가 해석 실패로 받아 라우터 실패 문구로 답한다 (대화 상태 그대로)"""
    explains = True

    def __init__(self, max_calls, ledger_path, model=None):
        """max_calls = 이 해석기의 호출 상한(None = 상한 없음), ledger_path = 저장소 밖 장부(None = 장부 없이 메모리 수만).
        장부가 있으면 장부의 상한(처음 만들 때 고정)도 같이 지킨다"""
        key, model = os.environ.get("OPENAI_API_KEY"), model or os.environ.get("LLM_MODEL")
        if not key or not model:
            raise SystemExit("OPENAI_API_KEY · LLM_MODEL 이 필요해요")
        self.ledger = Ledger(ledger_path, max_calls) if ledger_path else None
        self.client = ResponsesClient(model, key, max_calls, schema=schema_for,
                                      on_attempt=self.ledger.spend if self.ledger else None)
        self.model, self.missed = model, 0

    def __call__(self, system, user):
        return self.client.complete(system, user)

    def usage(self):
        used, limit = (self.ledger.data["used"], self.ledger.data["limit"]) if self.ledger else \
            (len(self.client.attempts), self.client.max_calls)
        return {"used": used, "limit": limit, "blocked": self.client.blocked,
                "failed": sum(1 for a in self.client.attempts if a["kind"] not in (None, "ok"))}
