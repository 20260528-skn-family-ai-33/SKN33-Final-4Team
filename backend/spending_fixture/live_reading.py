"""실제 LLM 해석 (명시적으로 켤 때만) — 로컬 채팅(local_chat --live)과 더미 서비스 실행(FIXTURE_LIVE_LLM=1)이 같이 쓴다
평가 도구의 역할별 호출 어댑터(ResponsesClient) · 장부를 그대로 쓴다. 재시도 없음. 키 · 요청 · 응답 원문은 화면에 보내지 않는다
"""
import os
import sys
from pathlib import Path

ROUTER_EVAL = Path(__file__).resolve().parents[2] / "scripts" / "router_eval"


class LiveReading:
    """실제 호출: 평가 도구의 ResponsesClient(역할별 Structured Outputs) + 저장소 밖 장부. 재시도 없음
    설명 LLM 도 같은 클라이언트 · 상한 · 장부를 쓴다 ("왜 늘었어?" 한 번에 1회 더)"""
    explains = True

    def __init__(self, max_calls, ledger_path, model=None):
        """max_calls · ledger_path 가 None 이면 상한 · 장부 없이 (더미 서비스 실행에서 사용자가 상한 없이 확인할 때, MEMO 22절)"""
        sys.path.insert(0, str(ROUTER_EVAL))
        import run as eval_run  # noqa: PLC0415  (평가 도구: 장부)
        import stage_b  # noqa: PLC0415  (역할별 스키마 · 호출 어댑터)
        key, model = os.environ.get("OPENAI_API_KEY"), model or os.environ.get("LLM_MODEL")
        if not key or not model:
            raise SystemExit("OPENAI_API_KEY · LLM_MODEL 이 필요해요")
        self.ledger = eval_run.Ledger(ledger_path, max_calls) if ledger_path else None
        limit = min(max_calls, self.ledger.remaining) if self.ledger else (max_calls or float("inf"))
        self.client = stage_b.ResponsesClient(model, key, limit, schema=stage_b.schema_for,
                                              on_attempt=self.ledger.spend if self.ledger else None)
        self.model, self.missed = model, 0

    def __call__(self, system, user):
        return self.client.complete(system, user)

    def usage(self):
        used, limit = (self.ledger.data["used"], self.ledger.data["limit"]) if self.ledger else (len(self.client.attempts), None)
        return {"used": used, "limit": limit, "blocked": self.client.blocked,
                "failed": sum(1 for a in self.client.attempts if a["kind"] not in (None, "ok"))}
