"""중단된 조회 · 분석 작업이 동의 철회 · 탈퇴를 계속 막지 않게 (R20). 실행: backend 에서 python manage.py test finance.test_stale_work

- 시간 경과는 기록의 시작 시각을 과거로 옮겨 흉내 낸다 (sleep 없음)
- 실제 CODEF 는 NoCodefNetwork 로 setUpClass(시드 · setUpTestData) 전부터 막는다
"""
import json
import uuid
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from chat.models import AsyncTask
from config import tasks
from users.models import Consent

from . import codef
from .models import BankAccount, Card, Connection, FinancialTransaction, Loan, SyncRun
from .sync import sync_bank
from .sync_cards import sync_card
from .sync_loans import sync_bank_loans
from .test_bank_response import NoCodefNetwork
from .test_card_sync import CARD_NO, CARD_PW, MONTHS, approval, bill, card
from .test_loan_sync import NUMBER, account, detail

ACCOUNT = {"account_no": "110123456789", "name": "입출금", "balance": 90000, "kind_code": "11", "org": "0088"}
OLD = timedelta(minutes=11)   # 유효 기간(10분)이 지난 시각


def bank_row():
    return codef.normalize_transaction({
        "resAccountTrDate": "20261020", "resAccountTrTime": "123000", "resAccountOut": "10000",
        "resAccountIn": "0", "resAfterTranBalance": "90000", "resAccountDesc3": "시험 거래",
    })


def age(model, pk, ago):
    model.objects.filter(pk=pk).update(started_at=timezone.now() - ago)


def expire_task(pk):
    """작업의 실행 권한이 지난 상태 (정리는 아직 안 돼 running 그대로)"""
    AsyncTask.objects.filter(pk=pk).update(started_at=timezone.now() - OLD, lease_expires_at=timezone.now() - timedelta(seconds=1))


class Base(NoCodefNetwork, TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="stale@example.com", password="Synthetic123!")
        for scope in ("bank", "card", "loan"):
            Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope=scope,
                                   document_version="v2", document_hash="test")
        self.bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0088", status="connected")
        self.cardco = Connection.objects.create(user=self.user, business_type="card", organization_code="0302", status="connected")
        self.client.force_login(self.user)
        pipeline = patch("finance.sync.process_new_transactions")
        pipeline.start()
        self.addCleanup(pipeline.stop)

    def task(self, ago=timedelta(0), task_type="finance_sync", legacy=False, user=None):
        """start_or_reuse 가 만드는 모양. legacy=True 면 lease_expires_at 이 없는 필드 추가 전 기록"""
        started = timezone.now() - ago
        return AsyncTask.objects.create(user=user or self.user, task_type=task_type, request_key=f"{task_type}:{uuid.uuid4()}",
                                        input_hash="h", status="running", started_at=started,
                                        lease_expires_at=None if legacy else started + tasks.TASK_LEASE)

    def sync_run(self, ago=timedelta(0), product_code="card_sync", **extra):
        row = SyncRun.objects.create(user=self.user, connection=self.cardco, product_code=product_code, status="running", **extra)
        age(SyncRun, row.pk, ago)
        return row

    def revoke(self, scope="card"):
        return self.client.delete(f"/api/finance/consents/{scope}/", data=json.dumps({"confirm": True}), content_type="application/json")

    def withdraw(self):
        return self.client.delete("/api/auth/me/", data=json.dumps({"password": "Synthetic123!", "confirm": True}),
                                  content_type="application/json")

    def sync_bank_with(self, during, task_id=None):
        """외부 조회 도중 during() 을 실행 (철회 · 만료 · 재연결 등)"""
        def transactions(*args):
            during()
            return [bank_row()]
        with patch("finance.codef.list_accounts", return_value=[ACCOUNT]), \
                patch("finance.codef.list_transactions", side_effect=transactions):
            return sync_bank(self.user, "cid", "0088", timezone.localdate() - timedelta(days=7), timezone.localdate(),
                             connection_id=self.bank.pk, require_consent=True, task_id=task_id)


class StaleRecordsDoNotBlockPrivacyTest(Base):
    def test_only_stale_task(self):
        stale = self.task(OLD)
        self.assertEqual(self.revoke().status_code, 200)
        self.assertFalse(AsyncTask.objects.filter(pk=stale.pk).exists())  # 철회 정리로 삭제 (이미 failed 로 바뀐 뒤)

    def test_only_stale_sync_run(self):
        self.sync_run(OLD)
        self.assertEqual(self.revoke().status_code, 200)
        self.assertFalse(Card.objects.filter(user=self.user).exists())

    def test_both_stale_then_withdraw(self):
        self.task(OLD)
        self.sync_run(OLD)
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertFalse(get_user_model().objects.filter(email="stale@example.com").exists())

    def test_queued_stale_task_also_expires(self):
        AsyncTask.objects.create(user=self.user, task_type="finance_sync", request_key="q", input_hash="h", status="queued")
        AsyncTask.objects.filter(request_key="q").update(created_at=timezone.now() - OLD)
        self.assertEqual(self.withdraw().status_code, 200)


class LiveWorkStillBlocksTest(Base):
    def test_recent_task_and_run_block(self):
        self.task()
        self.assertEqual(self.revoke().json()["error"]["code"], "privacy_busy")
        AsyncTask.objects.all().delete()
        self.sync_run(timedelta(minutes=9))
        self.assertEqual(self.withdraw().json()["error"]["code"], "privacy_busy")

    def test_old_task_renewed_by_its_own_runs_blocks(self):
        # 여러 기관을 차례로 부르는 작업: 시작은 25분 전이지만 자기 조회를 시작할 때마다 연장됐다
        task = self.task(timedelta(minutes=25))
        AsyncTask.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now() + timedelta(minutes=5))  # 5분 전 마지막 연장
        for _ in range(2):   # 다음 기관, 그다음 기관
            run = tasks.start_run(self.user, task.pk, connection=self.bank, product_code="bank_sync")
            tasks.finish_run(run.pk, status="success")
        task.refresh_from_db()
        self.assertGreater(task.lease_expires_at, timezone.now() + timedelta(minutes=9))
        self.assertEqual(self.revoke().status_code, 409)
        task.refresh_from_db()
        self.assertEqual(task.status, "running")

    def test_unrelated_runs_do_not_extend_stuck_task(self):
        stuck = self.task(timedelta(minutes=9))     # 1분 뒤 기한
        other = get_user_model().objects.create_user(email="other-stale@example.com")
        other_conn = Connection.objects.create(user=other, business_type="bank", organization_code="0088", status="connected")
        other_task = self.task(user=other)
        for _ in range(3):   # 같은 회원의 작업 없는 조회 · 다른 회원 작업의 조회를 여러 번
            tasks.finish_run(tasks.start_run(self.user, None, connection=self.bank, product_code="bank_sync").pk, status="success")
            tasks.finish_run(tasks.start_run(other, other_task.pk, connection=other_conn, product_code="bank_sync").pk, status="success")
        stuck.refresh_from_db()
        self.assertEqual(stuck.lease_expires_at, stuck.started_at + tasks.TASK_LEASE)   # 연장 안 됨
        AsyncTask.objects.filter(pk=stuck.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.revoke().status_code, 200)

    def test_other_users_or_other_type_task_cannot_be_used(self):
        other = get_user_model().objects.create_user(email="other-owner@example.com")
        foreign = self.task(user=other)
        budget = self.task(task_type="budget_recommend")
        for task_id in (foreign.pk, budget.pk):
            with self.subTest(task=task_id), self.assertRaises(tasks.WorkExpired):
                tasks.start_run(self.user, task_id, connection=self.bank, product_code="bank_sync")
        self.assertFalse(SyncRun.objects.exists())

    def test_legacy_task_without_lease_uses_start_and_never_extends(self):
        legacy = self.task(timedelta(minutes=9), legacy=True)
        tasks.finish_run(tasks.start_run(self.user, legacy.pk, connection=self.bank, product_code="bank_sync").pk, status="success")
        legacy.refresh_from_db()
        self.assertIsNotNone(legacy.lease_expires_at)   # 유효할 때 자기 조회로는 연장된다
        old = self.task(OLD, legacy=True)
        with self.assertRaises(tasks.WorkExpired):
            tasks.start_run(self.user, old.pk, connection=self.bank, product_code="bank_sync")
        old.refresh_from_db()
        self.assertIsNone(old.lease_expires_at)

    def test_expiry_is_kept_even_when_other_live_work_returns_409(self):
        stale_run = self.sync_run(OLD)
        self.task()  # 지금 진행 중인 정상 작업
        self.assertEqual(self.revoke().status_code, 409)
        stale_run.refresh_from_db()
        self.assertEqual((stale_run.status, stale_run.error_code), ("failed", "stale"))  # 409 로 롤백되지 않음


class LateResultIsRefusedTest(Base):
    def test_expired_run_cannot_save_bank_data(self):
        def expire():
            age(SyncRun, SyncRun.objects.get(product_code="bank_sync").pk, OLD)
        with self.assertRaises(tasks.WorkExpired):
            self.sync_bank_with(expire)
        self.assertFalse(BankAccount.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        run = SyncRun.objects.get(product_code="bank_sync")
        self.assertEqual((run.status, run.error_code, run.covered_to), ("failed", "stale", None))

    def test_late_response_after_revoke_creates_nothing(self):
        def revoke_meanwhile():
            age(SyncRun, SyncRun.objects.get(product_code="bank_sync").pk, OLD)  # 서버가 멈춘 것처럼 오래 걸림
            self.assertEqual(self.revoke("bank").status_code, 200)
        with self.assertRaises(Exception):
            self.sync_bank_with(revoke_meanwhile)
        self.assertFalse(BankAccount.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.assertFalse(SyncRun.objects.filter(product_code="bank_sync").exists())  # 삭제된 기록을 되살리지 않음

    def test_late_response_after_withdraw_creates_nothing(self):
        user_id = self.user.pk

        def withdraw_meanwhile():
            age(SyncRun, SyncRun.objects.get(product_code="bank_sync").pk, OLD)
            self.assertEqual(self.withdraw().status_code, 200)
        with self.assertRaises(Exception):
            self.sync_bank_with(withdraw_meanwhile)
        self.assertFalse(SyncRun.objects.filter(user_id=user_id).exists())
        self.assertFalse(BankAccount.objects.filter(user_id=user_id).exists())

    def test_expired_task_cannot_borrow_new_task_or_new_consent(self):
        old = self.task()

        def replaced_by_new_task():
            expire_task(old.pk)
            age(SyncRun, SyncRun.objects.get(product_code="bank_sync").pk, OLD)
            new, created = tasks.start_or_reuse(self.user, "finance_sync", "h2")   # 새 작업이 옛 작업을 만료시킨다
            self.assertTrue(created)
        with self.assertRaises(tasks.WorkExpired):
            self.sync_bank_with(replaced_by_new_task, task_id=old.pk)
        self.assertFalse(BankAccount.objects.exists())
        old.refresh_from_db()
        self.assertEqual((old.status, old.error_code), ("failed", "stale"))

    def test_expired_task_refused_even_if_its_own_run_is_fresh(self):
        old = self.task()

        def task_expired():   # 작업은 만료 처리됐지만 이 기관 조회 기록은 아직 유효 기간 안
            AsyncTask.objects.filter(pk=old.pk).update(status="failed", error_code="stale")
        with self.assertRaises(tasks.WorkExpired):
            self.sync_bank_with(task_expired, task_id=old.pk)
        self.assertFalse(BankAccount.objects.exists())

    def test_reconsent_and_reconnect_old_task_refused_new_task_saves(self):
        old = self.task()

        def revoke_and_reconnect():
            expire_task(old.pk)
            age(SyncRun, SyncRun.objects.get(product_code="bank_sync").pk, OLD)
            self.assertEqual(self.revoke("bank").status_code, 200)
            Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="bank",
                                   document_version="v2", document_hash="test")
            Connection.objects.filter(pk=self.bank.pk).update(status="connected", disconnected_at=None)  # 같은 연결로 다시 연결된 최악의 경우
        with self.assertRaises(tasks.WorkExpired):
            self.sync_bank_with(revoke_and_reconnect, task_id=old.pk)
        self.assertFalse(BankAccount.objects.exists())

        new, _ = tasks.start_or_reuse(self.user, "finance_sync", "h3")
        self.sync_bank_with(lambda: None, task_id=new.pk)
        self.assertEqual(BankAccount.objects.filter(user=self.user).count(), 1)
        self.assertEqual(SyncRun.objects.get(product_code="bank_sync").status, "success")


class LateCallbacksDoNotFlipStateTest(Base):
    def test_late_completion_keeps_expired_task_failed(self):
        task = self.task()
        AsyncTask.objects.filter(pk=task.pk).update(status="failed", error_code="stale", completed_at=timezone.now())
        tasks.run(task, lambda: {"summary": "늦은 결과"})
        task.refresh_from_db()
        self.assertEqual((task.status, task.error_code, task.result), ("failed", "stale", None))

    def test_late_failure_keeps_expired_task_code(self):
        task = self.task()
        AsyncTask.objects.filter(pk=task.pk).update(status="failed", error_code="stale")
        tasks.run(task, lambda: 1 / 0)
        task.refresh_from_db()
        self.assertEqual(task.error_code, "stale")

    def test_late_completion_does_not_recreate_deleted_task(self):
        task = self.task()
        AsyncTask.objects.filter(pk=task.pk).delete()
        returned = tasks.run(task, lambda: {"ok": True})
        self.assertFalse(AsyncTask.objects.filter(pk=task.pk).exists())
        self.assertEqual(returned.status, "failed")

    def test_late_provider_failure_keeps_stale_run(self):
        def expire_then_fail():
            age(SyncRun, SyncRun.objects.get(product_code="bank_sync").pk, OLD)
            tasks.expire_stale(self.user)
            raise codef.CodefError("CF-12345", "늦은 실패")
        with self.assertRaises(codef.CodefError):
            self.sync_bank_with(expire_then_fail)
        run = SyncRun.objects.get(product_code="bank_sync")
        self.assertEqual((run.status, run.error_code, run.business_code), ("failed", "stale", None))


class BoundaryTest(Base):
    def test_renewal_while_valid_then_cleanup_keeps_task(self):
        task = self.task(tasks.TASK_LEASE - timedelta(seconds=5))   # 기한 5초 전
        tasks.start_run(self.user, task.pk, connection=self.bank, product_code="bank_sync")   # 유효할 때 자기 조회로 연장
        tasks.expire_stale(self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, "running")

    def test_time_passed_without_cleanup_is_refused_everywhere(self):
        task = self.task(OLD)   # 기한이 지났지만 아직 running (정리 안 됨)
        with self.assertRaises(tasks.WorkExpired):   # 새 조회 시작 → 스스로 되살아나지 못함
            tasks.start_run(self.user, task.pk, connection=self.bank, product_code="bank_sync")
        self.assertFalse(SyncRun.objects.exists())
        called = []
        tasks.run(task, lambda: called.append(1))   # 처음부터 work() 를 부르지 않음
        task.refresh_from_db()
        self.assertEqual((called, task.status, task.error_code), ([], "failed", "stale"))

    def test_task_expiring_during_work_is_not_marked_succeeded(self):
        task = self.task()
        tasks.run(task, lambda: expire_task(task.pk) or {"late": True})
        task.refresh_from_db()
        self.assertEqual((task.status, task.error_code, task.result), ("failed", "stale", None))

    def test_task_expiring_during_bank_call_cannot_save(self):
        task = self.task()
        with self.assertRaises(tasks.WorkExpired):   # 저장 경로 자체가 막힌다 (종료 상태 검사만 믿지 않음)
            self.sync_bank_with(lambda: expire_task(task.pk), task_id=task.pk)
        self.assertFalse(BankAccount.objects.exists())

    def test_cleanup_first_then_job_continues_is_refused_and_stays_failed(self):
        task = self.task(tasks.TASK_LEASE + timedelta(seconds=1))
        tasks.expire_stale(self.user)   # 정리가 먼저
        with self.assertRaises(tasks.WorkExpired):
            self.sync_bank_with(lambda: None, task_id=task.pk)   # 작업은 살아 있어 다음 기관을 계속 부름
        tasks.run(task, lambda: {"late": True})
        task.refresh_from_db()
        self.assertEqual((task.status, task.error_code), ("failed", "stale"))
        self.assertFalse(BankAccount.objects.exists())


@override_settings(CODEF={"ENV": "demo", "CONNECTED_ID": "test-id", "PUBLIC_KEY": "test-key"})
class CardAndLoanLateSaveTest(Base):
    def setUp(self):
        super().setUp()
        for target, kwargs in (("finance.codef.encrypt_rsa", {"side_effect": lambda value, key: "encrypted"}),
                               ("finance.classifier.predict", {"side_effect": lambda names: [[] for _ in names]})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def expire_current(self, product_code):
        age(SyncRun, SyncRun.objects.get(product_code=product_code, status="running").pk, OLD)

    def test_card_late_save_refused(self):
        def response(path, params):
            if path == codef.PATH_CARD_LIST:
                self.expire_current("card_sync")
                return [card()]
            if path == codef.PATH_CARD_APPROVALS:
                return [approval()]
            return deepcopy({"202609": bill(due="20260925"), "202610": bill()}[params["startDate"]])
        with patch("finance.codef._request", side_effect=response), self.assertRaises(tasks.WorkExpired):
            sync_card(self.user, "cid", "0302", timezone.localdate() - timedelta(days=30), timezone.localdate(), MONTHS,
                      card_no=CARD_NO, card_password=CARD_PW, connection_id=self.cardco.pk)
        self.assertFalse(Card.objects.exists())
        self.assertEqual(list(SyncRun.objects.values_list("product_code", "status", "error_code")),
                         [("card_sync", "failed", "stale")])

    def test_loan_late_save_refused(self):
        def response(path, params):
            if path == codef.PATH_ACCOUNT_LIST:
                self.expire_current("loan_sync")
                return {"resLoan": [account()]}
            return deepcopy(detail())
        with patch("finance.codef._request", side_effect=response), self.assertRaises(tasks.WorkExpired):
            sync_bank_loans(self.user, "test-id", "0088", timezone.localdate() - timedelta(days=30), timezone.localdate())
        self.assertFalse(Loan.objects.exists())
        self.assertEqual(SyncRun.objects.get().error_code, "stale")
        self.assertTrue(NUMBER)
