"""chat/llm 호출 장부 · 호출 어댑터의 동시 사용 (MEMO 25-12) — 네트워크 · DB 없음
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
