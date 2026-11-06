"""R20 실제 경쟁 테스트 — 스레드마다 별도 DB 연결. 실행: backend 에서 python manage.py test finance.test_stale_concurrency

잠금 획득 순서를 스레드 시작 순서나 sleep 길이로 가정하지 않는다:
- 시험하는 쪽이 회원 행을 먼저 잠근 채 두 작업을 출발시키고, 둘 다 실제로 잠금을 기다리는 것(pg_stat_activity)을 확인한 뒤 놓는다.
  어느 쪽이 먼저 잠금을 얻든 결과가 같은지(또는 허용된 두 결과 중 하나인지)를 본다.
- 외부 조회 도중 멈춤은 Event 로 제어한다. 모든 대기에는 제한 시간이 있고, worker 예외 · 남은 스레드 · DB 연결 정리를 확인한다.
"""
import json
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client, TransactionTestCase
from django.utils import timezone

from chat.models import AsyncTask
from config import tasks
from users.models import Consent

from .models import BankAccount, Connection, FinancialTransaction, SyncRun
from .sync import sync_bank
from .test_bank_response import NoCodefNetwork
from .test_stale_work import ACCOUNT, bank_row

WAIT = 20  # 모든 대기의 상한 (초)


class Worker(threading.Thread):
    """job() 을 실행하고 결과 · 예외를 담는다. 끝날 때 이 스레드의 DB 연결을 닫는다"""

    def __init__(self, job):
        super().__init__(daemon=True)
        self.job, self.result, self.error = job, None, None

    def run(self):
        try:
            self.result = self.job()
        except BaseException as exc:  # 시험에서 확인한다
            self.error = exc
        finally:
            connection.close()


def finish(*workers):
    for worker in workers:
        worker.join(WAIT)
    alive = [w for w in workers if w.is_alive()]
    assert not alive, f"끝나지 않은 스레드: {alive}"   # 교착 · 무한 대기 없음


def wait_until(check, what):
    deadline = time.monotonic() + WAIT
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"제한 시간 안에 {what} 되지 않음")
        time.sleep(0.01)   # 조건을 다시 보는 간격일 뿐, 순서를 이것으로 정하지 않는다


def lock_waiters():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")  # 트랜잭션 안에서는 통계 화면이 첫 조회 때 고정된다
        cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                       "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()")
        return cursor.fetchone()[0]


class RaceBase(NoCodefNetwork, TransactionTestCase):
    serialized_rollback = True  # 카테고리 시드 복원 (test_concurrency 와 같음)

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="race@example.com", password="Synthetic123!")
        for scope in ("bank", "card", "loan"):
            Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope=scope,
                                   document_version="v2", document_hash="test")
        self.bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0088", status="connected")
        pipeline = patch("finance.sync.process_new_transactions")
        pipeline.start()
        self.addCleanup(pipeline.stop)

    def task(self, lease):
        now = timezone.now()
        return AsyncTask.objects.create(user=self.user, task_type="finance_sync", request_key=f"race:{now.timestamp()}",
                                        input_hash="h", status="running", started_at=now - timedelta(minutes=30),
                                        lease_expires_at=now + lease)

    def race(self, *jobs, waiting=None):
        """회원 행을 잡은 채 jobs 를 출발시키고, waiting 명이 잠금을 기다리는 것을 본 뒤 놓는다"""
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)
            workers = [Worker(job) for job in jobs]
            for worker in workers:
                worker.start()
            try:
                wait_until(lambda: lock_waiters() >= (waiting or len(jobs)), "모든 작업이 회원 잠금을 기다리게")
            except AssertionError:
                finish(*workers)
                raise AssertionError([repr(w.error) for w in workers])
        finish(*workers)
        return workers


class RenewVsExpireTest(RaceBase):
    """1. 저장 시 연장(claim_run)과 만료 처리가 동시에"""

    def claim(self, run, task):
        def job():
            with transaction.atomic():
                get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)
                tasks.claim_run(run, task.pk)
        return job

    def test_valid_task_renewed_and_not_expired(self):
        task = self.task(timedelta(minutes=1))
        run = tasks.start_run(self.user, task.pk, connection=self.bank, product_code="bank_sync")
        AsyncTask.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() + timedelta(minutes=1))
        claim, expire = self.race(self.claim(run, task), lambda: tasks.expire_stale(self.user))
        self.assertIsNone(claim.error)
        self.assertIsNone(expire.error)
        task.refresh_from_db(); run.refresh_from_db()
        self.assertEqual((task.status, run.status), ("running", "running"))
        self.assertGreater(task.lease_expires_at, timezone.now() + timedelta(minutes=9))

    def test_expired_task_cannot_be_renewed_whoever_wins(self):
        task = self.task(timedelta(minutes=1))
        run = tasks.start_run(self.user, task.pk, connection=self.bank, product_code="bank_sync")
        AsyncTask.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))  # 정리 전 running
        claim, expire = self.race(self.claim(run, task), lambda: tasks.expire_stale(self.user))
        self.assertIsInstance(claim.error, tasks.WorkExpired)
        self.assertIsNone(expire.error)
        task.refresh_from_db(); run.refresh_from_db()
        self.assertEqual((task.status, task.error_code), ("failed", "stale"))
        self.assertEqual((run.status, run.error_code), ("failed", "stale"))   # 만료된 작업의 조회도 닫힘


class NextRunVsExpireTest(RaceBase):
    """2. 다음 기관 조회 시작(start_run)과 만료 처리가 동시에"""

    def start(self, task):
        return lambda: tasks.start_run(self.user, task.pk, connection=self.bank, product_code="bank_sync")

    def test_valid_task_starts_next_run(self):
        task = self.task(timedelta(minutes=1))
        start, expire = self.race(self.start(task), lambda: tasks.expire_stale(self.user))
        self.assertIsNone(start.error)
        self.assertIsNone(expire.error)
        task.refresh_from_db()
        self.assertEqual(task.status, "running")
        self.assertEqual(SyncRun.objects.get(task=task).status, "running")

    def test_expired_task_never_starts_or_succeeds(self):
        task = self.task(-timedelta(seconds=1))
        start, expire = self.race(self.start(task), lambda: tasks.expire_stale(self.user))
        self.assertIsInstance(start.error, tasks.WorkExpired)
        self.assertFalse(SyncRun.objects.exists())
        tasks.run(task, lambda: {"late": True})
        task.refresh_from_db()
        self.assertEqual((task.status, task.error_code), ("failed", "stale"))


class SaveVsRevokeTest(RaceBase):
    """3. 금융 데이터 저장과 만료 · 철회가 동시에. 외부 조회 도중 Event 로 멈춰 두고 저장 · 철회를 같이 출발시킨다"""

    def sync_job(self, fetched, go, task_id=None):
        def transactions(*args):
            fetched.set()
            if not go.wait(WAIT):
                raise AssertionError("go 신호를 받지 못함")
            return [bank_row()]

        def job():
            return sync_bank(self.user, "cid", "0088", timezone.localdate() - timedelta(days=7), timezone.localdate(),
                             connection_id=self.bank.pk, require_consent=True, task_id=task_id)
        return job, transactions

    def revoke_job(self):
        def job():
            client = Client()
            client.force_login(self.user)
            return client.delete("/api/finance/consents/bank/", data=json.dumps({"confirm": True}),
                                 content_type="application/json").status_code
        return job

    def run_race(self, before_release, task_id=None):
        fetched, go = threading.Event(), threading.Event()
        job, transactions = self.sync_job(fetched, go, task_id)
        with patch("finance.codef.list_accounts", return_value=[ACCOUNT]), \
                patch("finance.codef.list_transactions", side_effect=transactions):
            saver = Worker(job)
            saver.start()
            try:
                self.assertTrue(fetched.wait(WAIT), "외부 조회 지점에 도착하지 않음")
                before_release()
                with transaction.atomic():   # 회원 행을 잡고 저장 · 철회를 함께 출발
                    get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)
                    revoker = Worker(self.revoke_job())
                    revoker.start()
                    go.set()
                    wait_until(lambda: lock_waiters() >= 2, "저장 · 철회가 회원 잠금을 기다리게")
            finally:
                go.set()
                finish(saver)
            finish(revoker)
        self.assertIsNone(revoker.error)
        return saver, revoker

    def test_expired_save_and_revoke(self):
        task = self.task(timedelta(minutes=5))

        def expire():   # 외부 조회가 오래 걸려 작업 · 조회의 기한이 지난 상황 (정리는 아직)
            AsyncTask.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
            SyncRun.objects.filter(task=task).update(started_at=timezone.now() - timedelta(minutes=11))
        saver, revoker = self.run_race(expire, task.pk)
        self.assertEqual(revoker.result, 200)
        self.assertIsNotNone(saver.error)
        self.assertFalse(BankAccount.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.assertFalse(SyncRun.objects.filter(status__in=["running", "success"]).exists())
        self.assertFalse(AsyncTask.objects.filter(status="succeeded").exists())

    def test_live_save_and_revoke_one_consistent_order(self):
        saver, revoker = self.run_race(lambda: None)   # 작업 없는 새 연결 직후 조회처럼, 기한 안
        if revoker.result == 200:   # 철회가 저장 뒤 → 저장된 것도 모두 지움 / 저장 전 → 저장이 거절됨
            self.assertFalse(BankAccount.objects.exists())
            self.assertFalse(FinancialTransaction.objects.exists())
        else:   # 철회가 진행 중인 저장 때문에 기다리라고 함 → 저장은 정상
            self.assertEqual(revoker.result, 409)
            self.assertIsNone(saver.error)
            self.assertEqual(BankAccount.objects.count(), 1)
            client = Client(); client.force_login(self.user)
            self.assertEqual(client.delete("/api/finance/consents/bank/", data=json.dumps({"confirm": True}),
                                           content_type="application/json").status_code, 200)
            self.assertFalse(BankAccount.objects.exists())   # 다시 철회하면 지워지고 재유입 없음
        self.assertFalse(SyncRun.objects.filter(status="running").exists())


class Clock:
    """config.tasks 가 보는 현재 시각만 바꾼다 (저장 기본값 등 다른 시각은 실제 시각)"""

    def __init__(self):
        self.value = timezone.now()

    def now(self):
        return self.value


class ExpiresWhileWaitingForLockTest(RaceBase):
    """4. 잠금을 기다리기 시작할 때는 유효했지만, 잠금을 얻었을 때는 기한이 지난 경우 — 잠금 뒤 시각으로 판단해야 한다"""

    def setUp(self):
        super().setUp()
        self.clock = Clock()
        patcher = patch("config.tasks.timezone", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.base = self.clock.value

    def short_task(self):
        """기다리기 시작할 때 5초 남은 작업"""
        return AsyncTask.objects.create(user=self.user, task_type="finance_sync", request_key=f"short:{self.base.timestamp()}",
                                        input_hash="h", status="running", started_at=self.base - timedelta(minutes=9),
                                        lease_expires_at=self.base + timedelta(seconds=5))

    def hold_then_expire(self, lock, job, ready=None):
        """lock() 으로 행을 잡은 채 job 을 출발시키고, job 이 그 잠금을 기다리는 것을 본 뒤 시계를 기한 너머로 옮기고 놓는다"""
        worker = Worker(job)
        with transaction.atomic():
            lock()
            worker.start()
            if ready is not None:
                ready()
            wait_until(lambda: lock_waiters() >= 1, "작업이 잠금을 기다리게")
            self.clock.value = self.base + timedelta(seconds=10)   # 기다리는 동안 기한(5초)이 지남
        finish(worker)
        return worker

    def lock_user(self):
        get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)

    def test_start_run(self):
        task = self.short_task()
        worker = self.hold_then_expire(self.lock_user, lambda: tasks.start_run(
            self.user, task.pk, connection=self.bank, product_code="bank_sync"))
        self.assertIsInstance(worker.error, tasks.WorkExpired)
        self.assertFalse(SyncRun.objects.exists())
        task.refresh_from_db()
        self.assertEqual(task.lease_expires_at, self.base + timedelta(seconds=5))   # 연장 안 됨

    def test_claim_run(self):
        task = self.short_task()
        AsyncTask.objects.filter(pk=task.pk).update(lease_expires_at=self.base + timedelta(minutes=1))
        run = tasks.start_run(self.user, task.pk, connection=self.bank, product_code="bank_sync")
        AsyncTask.objects.filter(pk=task.pk).update(lease_expires_at=self.base + timedelta(seconds=5))

        def claim():
            with transaction.atomic():
                self.lock_user()
                tasks.claim_run(run, task.pk)
        worker = self.hold_then_expire(lambda: AsyncTask.objects.select_for_update().get(pk=task.pk), claim)
        self.assertIsInstance(worker.error, tasks.WorkExpired)
        task.refresh_from_db()
        self.assertEqual(task.lease_expires_at, self.base + timedelta(seconds=5))

    def test_run_completion(self):
        task = self.short_task()
        started, go = threading.Event(), threading.Event()

        def work():
            started.set()
            if not go.wait(WAIT):
                raise AssertionError("go 신호를 받지 못함")
            return {"late": True}

        def ready():
            go.set()
        worker = Worker(lambda: tasks.run(task, work))
        worker.start()
        try:
            self.assertTrue(started.wait(WAIT), "work() 가 시작되지 않음")   # 시작 검사는 유효할 때 통과
            with transaction.atomic():
                AsyncTask.objects.select_for_update().get(pk=task.pk)
                ready()
                wait_until(lambda: lock_waiters() >= 1, "완료 처리가 작업 잠금을 기다리게")
                self.clock.value = self.base + timedelta(seconds=10)
        finally:
            go.set()
            finish(worker)
        self.assertIsNone(worker.error)
        task.refresh_from_db()
        self.assertEqual((task.status, task.error_code, task.result), ("failed", "stale", None))

    def test_start_or_reuse(self):
        old = self.short_task()
        worker = self.hold_then_expire(self.lock_user, lambda: tasks.start_or_reuse(self.user, "finance_sync", "h2"))
        self.assertIsNone(worker.error)
        new, created = worker.result
        self.assertTrue(created)   # 기다리기 시작할 때 살아 있던 작업을 재사용하지 않음
        old.refresh_from_db()
        self.assertEqual((old.status, old.error_code), ("failed", "stale"))
        later = self.base + timedelta(seconds=10)
        self.assertEqual((new.started_at, new.lease_expires_at), (later, later + tasks.TASK_LEASE))   # 새 기한도 잠금 뒤 시각 기준
