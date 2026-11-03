"""호출 장부 — 저장소 밖 JSON {"limit", "used"(, "units")}. 처음 만들 때의 상한을 고정하고, 시도마다 바로 늘려 쓴다 (재실행해도 초기화 안 됨).
평가 도구(run.Ledger)에서 옮김 (MEMO 25-12). 갱신 · 상한 확인은 경로별 스레드 잠금 + 운영체제 파일 잠금 안에서 '읽기 → 확인 → 더하기 → 쓰기' 한 번으로
→ 스레드 · 프로세스 여러 개가 같은 장부를 써도 수가 맞는다. limit = None 이면 상한 없이 세기만 한다.
Windows는 msvcrt.locking, macOS · Linux는 fcntl.flock. 잠금 실패 시 장부를 갱신하지 않고 호출도 보내지 않는다.
"""
import json
import os
import sys
import threading
from weakref import WeakValueDictionary
from contextlib import contextmanager
from pathlib import Path

from .openai_rest import CapReached

ROOT = Path(__file__).resolve().parents[3]  # 저장소 루트 (backend/chat/llm/ledger.py)


# 여러 Ledger 인스턴스도 같은 경로의 잠금을 공유한다. 사용이 끝난 잠금은 보관하지 않는다.
_thread_locks = WeakValueDictionary()
_thread_locks_guard = threading.Lock()


def _thread_lock(path):
    key = os.path.normcase(str(path))  # path는 resolve 완료. Windows의 대소문자 차이도 같은 경로로 본다.
    with _thread_locks_guard:
        lock = _thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[key] = lock
        return lock


def _reset_thread_locks():
    # fork한 자식은 부모의 잠긴 스레드 잠금을 물려받지 않는다. 프로세스 간 잠금은 파일이 담당한다.
    global _thread_locks, _thread_locks_guard
    _thread_locks = WeakValueDictionary()
    _thread_locks_guard = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_thread_locks)


class Ledger:
    def __init__(self, path, limit):
        self.path = Path(path).expanduser().resolve()
        if self.path == ROOT or ROOT in self.path.parents:
            raise SystemExit("--ledger 는 저장소 밖 경로여야 해요")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        with self._locked():
            if self.path.exists():
                self.data = self._read()
            else:
                self.data = {"limit": limit, "used": 0}
                self._write()

    @property
    def remaining(self):
        return float("inf") if self.data["limit"] is None else max(self.data["limit"] - self.data["used"], 0)

    def claim(self, ids):
        """이 장부로 보낼 단위를 실행 전에 기록. 이미 보낸 단위가 섞이면 아무것도 보내지 않고 멈춘다 (점검 단위 재실행 방지)"""
        with self._locked():
            self.data = self._read()
            done = set(self.data.get("units", []))  # 이 칸이 생기기 전 장부는 빈 목록으로 본다
            again = [i for i in ids if i in done]
            if again:
                raise SystemExit(f"이 장부로 이미 실행한 단위예요: {', '.join(again)}")
            self.data["units"] = [*self.data.get("units", []), *ids]
            self._write()

    def spend(self):
        """시도 하나를 센다. 상한에 닿았으면 세지 않고 CapReached (보내지 않음)"""
        with self._locked():
            self.data = self._read()
            if self.data["limit"] is not None and self.data["used"] >= self.data["limit"]:
                raise CapReached()
            self.data["used"] += 1
            self._write()

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self):
        temp = self.path.with_name(self.path.name + ".tmp")  # 다 쓴 뒤 바꿔 끼운다 (쓰다 멈춰도 장부가 깨지지 않게)
        temp.write_text(json.dumps(self.data), encoding="utf-8")
        temp.replace(self.path)

    @contextmanager
    def _locked(self):
        # 빈 파일도 0번 바이트 잠금이 가능하다. 데이터를 쓰지 않아 생성/초기화 경합도 없다.
        # 실제 장부 파일은 원자적으로 교체하므로 잠금 파일은 따로 유지한다.
        # Windows CRT 잠금을 여러 스레드가 동시에 기다리지 않게 프로세스 내부에서 먼저 줄을 세운다.
        with _thread_lock(self.path), open(self._lock_path, "a+b") as lock:
            if sys.platform == "win32":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                def unlock():
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
                def unlock():
                    fcntl.flock(lock, fcntl.LOCK_UN)
            try:
                yield
            finally:
                unlock()
