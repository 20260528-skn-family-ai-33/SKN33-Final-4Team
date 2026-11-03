"""chat/llm 호출 장부 · 호출 어댑터의 동시 사용 (MEMO 25-12) · 서비스 오류 처리 (25-13) — 네트워크 · DB 없음
실행: python manage.py test chat.test_llm --settings=spending_fixture.settings
"""
import json
import os
import tempfile
import threading

from django.test import SimpleTestCase

from .llm.ledger import Ledger
from .llm.openai_rest import CapReached, ResponsesClient


def _together(n, work):
    """스레드 n 개가 동시에 work() — 결과 [(성공 여부)]"""
    start, out = threading.Barrier(n), []

    def run():
        start.wait()
        try:
            work()
            out.append(True)
        except CapReached:
            out.append(False)
    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return out


class _Ok:
    def __init__(self, request, timeout):
        pass

    def read(self):
        return json.dumps({"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "{}"}]}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LedgerTest(SimpleTestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "ledger.json")

    def test_threads_never_pass_the_cap(self):
        ledgers = [Ledger(self.path, 7) for _ in range(20)]  # 요청마다 따로 연 장부(다른 프로세스와 같은 상황)
        out = _together(20, lambda: ledgers[threading.get_ident() % 20].spend())
        self.assertEqual((out.count(True), out.count(False)), (7, 13))
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"limit": 7, "used": 7})

    def test_no_limit_counts_every_call(self):
        ledger = Ledger(self.path, None)
        self.assertEqual(_together(15, ledger.spend), [True] * 15)
        self.assertEqual((Ledger(self.path, 3).data, ledger.remaining), ({"limit": None, "used": 15}, float("inf")))

    def test_client_with_ledger_counts_once_per_sent_request(self):
        ledger = Ledger(self.path, 5)
        client = ResponsesClient("test-model", "sk-test-not-real", None, opener=_Ok, on_attempt=ledger.spend)
        out = _together(12, lambda: client.complete("s", "u"))
        self.assertEqual((out.count(True), len(client.attempts), client.blocked, ledger.data["used"]), (5, 5, 7, 5))

    def test_client_memory_cap_with_threads(self):
        client = ResponsesClient("test-model", "sk-test-not-real", 4, opener=_Ok)
        out = _together(10, lambda: client.complete("s", "u"))
        self.assertEqual((out.count(True), len(client.attempts), client.blocked), (4, 4, 6))


class _Http:
    """정해 둔 순서로 HTTP 오류(상태, Retry-After) 또는 정상 응답을 돌려주는 가짜 연결. 보낸 요청 수를 센다"""

    def __init__(self, *plan):
        self.plan, self.sent = list(plan), 0

    def __call__(self, request, timeout):
        import io
        import urllib.error
        from email.message import Message
        self.sent += 1
        step = self.plan.pop(0) if self.plan else None
        if step is None:
            return _Ok(request, timeout)
        status, retry_after = step
        headers = Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        raise urllib.error.HTTPError("https://example.invalid", status, "err", headers, io.BytesIO(b"{}"))


class ServiceErrorTest(SimpleTestCase):
    """MEMO 25-13: 서비스(SERVICE_STOP)는 429 · 400 다음 요청을 다시 보내고, Retry-After 동안만 쉬고, 설정 오류만 계속 막는다.
    평가 도구 기본(FATAL)은 그대로 멈춘다"""

    def service_client(self, opener, **kw):
        from .llm.openai_rest import SERVICE_STOP
        self.now = 100.0
        return ResponsesClient("test-model", "sk-test-not-real", None, opener=opener, stop_on=SERVICE_STOP,
                               clock=lambda: self.now, **kw)

    def test_rate_limit_without_retry_after_sends_next_request(self):
        from .llm.openai_rest import ResponseError
        http = _Http((429, None), (400, None))
        c = self.service_client(http)
        for _ in range(2):
            with self.assertRaises(ResponseError):
                c.complete("s", "u")  # 이번 요청만 실패 (재시도 없음)
        self.assertEqual((c.complete("s", "u"), http.sent, c.fatal), ("{}", 3, None))

    def test_retry_after_pauses_only_that_long(self):
        from .llm.openai_rest import ResponseError
        http = _Http((429, "30"))
        c = self.service_client(http)
        with self.assertRaises(ResponseError):
            c.complete("s", "u")
        self.now += 29
        with self.assertRaises(CapReached) as blocked:
            c.complete("s", "u")  # 쉬는 동안은 보내지 않는다
        self.assertEqual(blocked.exception.kind, "rate_limit")
        self.assertEqual(http.sent, 1)
        self.now += 1
        self.assertEqual((c.complete("s", "u"), http.sent), ("{}", 2))

    def test_config_errors_keep_blocking_and_log_once(self):
        from .llm.openai_rest import ResponseError
        for status in (401, 403, 404):
            with self.subTest(status=status):
                http = _Http((status, None))
                c = self.service_client(http)
                with self.assertLogs("chat.llm.openai_rest", "ERROR") as logs, self.assertRaises(ResponseError):
                    c.complete("s", "u")
                for _ in range(2):
                    with self.assertRaises(CapReached):
                        c.complete("s", "u")
                self.assertEqual((http.sent, len(logs.output)), (1, 1))
                self.assertNotIn("sk-test-not-real", "".join(logs.output))

    def test_cap_logs_once(self):
        c = self.service_client(_Http())
        c.max_calls = 1
        c.complete("s", "u")
        with self.assertLogs("chat.llm.openai_rest", "WARNING") as logs:
            for _ in range(3):
                with self.assertRaises(CapReached) as blocked:
                    c.complete("s", "u")
                self.assertEqual(blocked.exception.kind, "call_limit")
        self.assertEqual(len(logs.output), 1)

    def test_eval_default_still_stops_on_rate_limit(self):
        from .llm.openai_rest import ResponseError
        http = _Http((429, None))
        c = ResponsesClient("test-model", "sk-test-not-real", 5, opener=http)
        with self.assertRaises(ResponseError):
            c.complete("s", "u")
        with self.assertRaises(CapReached):
            c.complete("s", "u")
        self.assertEqual((http.sent, c.fatal), (1, "rate_limit"))

    def test_live_reading_uses_service_policy(self):
        from unittest import mock
        from .llm.live import LiveReading
        from .llm.openai_rest import SERVICE_STOP
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-real", "LLM_MODEL": "test-model"}):
            self.assertEqual(LiveReading(None, None).client.stop_on, SERVICE_STOP)
