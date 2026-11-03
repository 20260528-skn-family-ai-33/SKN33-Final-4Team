"""OS 파일 잠금 · Windows 체크아웃 회귀. DB/LLM 없이 Windows에서도 실행 가능:
backend에서 python -m unittest chat.test_portability
"""
import errno
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from types import SimpleNamespace
from unittest import TestCase, mock, skipUnless

from chat.llm.ledger import Ledger
from chat.llm.openai_rest import CapReached, ResponsesClient


def _spend_process(path):
    ledger = Ledger(path, 7)
    used = 0
    for _ in range(5):
        try:
            ledger.spend()
            used += 1
        except CapReached:
            pass
    return used


def _claim_process(path):
    try:
        Ledger(path, 7).claim(["same-unit"])
        return True
    except SystemExit:
        return False


def _spend_once(path):
    Ledger(path, None).spend()


class PortableLedgerTest(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.json"

    def test_processes_share_cap_and_initialization(self):
        with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("spawn")) as pool:
            used = list(pool.map(_spend_process, [str(self.path)] * 4))
        self.assertEqual(sum(used), 7)
        self.assertEqual(json.loads(self.path.read_text()), {"limit": 7, "used": 7})
        self.assertEqual(Ledger(self.path, 100).remaining, 0)  # 다시 열어도 상한을 늘리지 않는다

    def test_processes_cannot_claim_same_unit_twice(self):
        with ProcessPoolExecutor(2, mp_context=multiprocessing.get_context("spawn")) as pool:
            claimed = list(pool.map(_claim_process, [str(self.path)] * 2))
        self.assertEqual(sorted(claimed), [False, True])
        self.assertEqual(json.loads(self.path.read_text())["units"], ["same-unit"])

    def windows_threads(self, limit):
        # Windows CRT처럼 같은 프로세스 내 경합을 오류로 돌려주는 대역.
        # 실제 OS 파일 잠금은 아래 프로세스 검사에서 따로 확인한다.
        gate, ready = threading.Lock(), threading.Barrier(15, timeout=10)

        def locking(_fd, mode, _size):
            if mode == 1:
                if not gate.acquire(blocking=False):
                    raise OSError(errno.EDEADLK, "Resource deadlock avoided")
                time.sleep(0.005)  # 다른 스레드가 진입할 기회를 주어 수정 전 경합도 재현
            else:
                gate.release()

        def spend(number):
            ready.wait()
            # 각 스레드가 새 인스턴스를 만든다. ../로 표현된 경로도 같은 파일이다.
            path = self.path if number % 2 else str(self.path.parent / "unused") + "/../ledger.json"
            ledger = Ledger(path, limit)
            try:
                ledger.spend()
                return True
            except CapReached:
                return False

        crt = SimpleNamespace(LK_LOCK=1, LK_UNLCK=0, locking=locking)
        with mock.patch("chat.llm.ledger.sys.platform", "win32"), mock.patch.dict(sys.modules, msvcrt=crt):
            with ThreadPoolExecutor(15) as pool:
                results = list(pool.map(spend, range(15), timeout=20))
        return results, json.loads(self.path.read_text())

    def test_windows_threads_multiple_instances_count_every_call(self):
        results, data = self.windows_threads(None)
        self.assertEqual(results, [True] * 15)
        self.assertEqual(data, {"limit": None, "used": 15})

    def test_windows_threads_multiple_instances_never_pass_cap(self):
        results, data = self.windows_threads(7)
        self.assertEqual((results.count(True), results.count(False)), (7, 8))
        self.assertEqual(data, {"limit": 7, "used": 7})

    def test_different_ledger_paths_do_not_block_each_other(self):
        ledgers = [Ledger(self.path, None), Ledger(self.path.with_name("other.json"), None)]
        ready = threading.Barrier(2, timeout=5)

        def enter(ledger):
            with ledger._locked():
                ready.wait()  # 경로와 무관한 전역 잠금이면 이 검사에서 실패한다.

        with ThreadPoolExecutor(2) as pool:
            list(pool.map(enter, ledgers, timeout=10))

    @skipUnless("fork" in multiprocessing.get_all_start_methods(), "fork를 제공하는 OS")
    def test_fork_does_not_inherit_locked_thread_mutex(self):
        ledger = Ledger(self.path, None)
        child = multiprocessing.get_context("fork").Process(target=_spend_once, args=(str(self.path),))
        try:
            with ledger._locked():
                child.start()  # 부모가 잠근 동안 fork해도 자식은 새 스레드 잠금을 사용한다.
            child.join(timeout=10)
            self.assertFalse(child.is_alive(), "자식이 부모의 스레드 잠금을 기다리며 멈췄어요")
            self.assertEqual(child.exitcode, 0)
            self.assertEqual(json.loads(self.path.read_text())["used"], 1)
        finally:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
            child.close()

    def test_windows_locks_and_unlocks_same_byte_even_on_failure(self):
        calls = []

        def locking(fd, mode, size):
            calls.append((mode, size, os.lseek(fd, 0, os.SEEK_CUR)))

        crt = SimpleNamespace(LK_LOCK=1, LK_UNLCK=0, locking=locking)
        with mock.patch("chat.llm.ledger.sys.platform", "win32"), mock.patch.dict(sys.modules, msvcrt=crt):
            ledger = Ledger(self.path, 7)
            with self.assertRaisesRegex(ValueError, "write failed"), ledger._locked():
                raise ValueError("write failed")
        self.assertEqual(calls, [(1, 1, 0), (0, 1, 0)] * 2)

    def test_windows_lock_failure_does_not_change_ledger(self):
        ledger = Ledger(self.path, 7)
        fail = mock.Mock(side_effect=OSError(errno.EACCES, "locked"))
        crt = SimpleNamespace(LK_LOCK=1, LK_UNLCK=0, locking=fail)
        opener = mock.Mock()
        client = ResponsesClient("test-model", "not-a-real-key", None, opener=opener, on_attempt=ledger.spend)
        with mock.patch("chat.llm.ledger.sys.platform", "win32"), mock.patch.dict(sys.modules, msvcrt=crt):
            with self.assertRaises(OSError):
                client.complete("system", "user")
        self.assertEqual(fail.call_count, 1)  # 얻지 못한 잠금은 풀지 않는다
        self.assertEqual(json.loads(self.path.read_text())["used"], 0)
        opener.assert_not_called()
        self.assertEqual(client.attempts, [])
        ledger.spend()  # 실패 시 프로세스 내부 잠금도 해제되어 다음 갱신이 가능하다.
        self.assertEqual(json.loads(self.path.read_text())["used"], 1)

    def test_live_without_ledger_does_not_import_ledger_or_os_lock(self):
        # 이미 import된 모듈에 가려지지 않도록 새 인터프리터에서 import를 차단한다.
        code = '''
import builtins, os, subprocess  # 호스트 표준 라이브러리 초기화 뒤 앱의 OS 의존성을 차단
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in ("fcntl", "msvcrt"):
        raise ModuleNotFoundError(name)  # Django 자체의 선택적 OS 의존성은 ImportError로 처리하도록 둔다
    if name in ("ledger", "chat.llm.ledger"):
        raise AssertionError("unexpected import: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
os.environ.update(OPENAI_API_KEY="not-a-real-key", LLM_MODEL="test-model")
from chat.llm.live import LiveReading
assert LiveReading(None, None).ledger is None
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


class FixtureCheckoutTest(TestCase):
    @skipUnless(shutil.which("git"), "Git 체크아웃 테스트")
    def test_autocrlf_checkout_preserves_fixture_hashes(self):
        from spending_fixture.seed import SHA256
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            data = repo / "backend/spending_fixture/data"
            data.mkdir(parents=True)
            shutil.copyfile(root / ".gitattributes", repo / ".gitattributes")
            files = [f"backend/spending_fixture/data/{name}" for name in ("design.json", "expected.json")]
            for path in files:
                shutil.copyfile(root / path, repo / path)
            for args in (["init", "-q"], ["-c", "core.autocrlf=true", "add", "."]):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            for path in files:
                (repo / path).unlink()
            subprocess.run(["git", "-c", "core.autocrlf=true", "checkout-index", "-f", "--", *files],
                           cwd=repo, check=True, capture_output=True)
            for path in files:
                raw = (repo / path).read_bytes()
                self.assertNotIn(b"\r\n", raw)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), SHA256[Path(path).name])
