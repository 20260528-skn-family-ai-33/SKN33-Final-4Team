"""동의 철회 · 회원 탈퇴의 삭제 실패 복구 (R21, users/deletion.py). 실행: backend 에서 python manage.py test users.test_privacy_deletion

- 실제 CODEF 는 NoCodefNetwork 로 setUpClass 부터 막고, 각 테스트는 합성 CODEF(FakeCodef)를 꽂는다
- 실패 주입: 외부 삭제 오류 · 타임아웃(실제로는 삭제됨) · 일부 삭제 · 상태 저장 실패 · 로컬 정리 실패 · 실행 중단(권한 만료)
"""
import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from finance.connected_id import seal
from finance.models import BankAccount, Card, Connection, FinancialTransaction
from finance.test_bank_response import NoCodefNetwork
from users import deletion
from users.models import Consent, PrivacyDeletion


def account(kind, org):
    return {"countryCode": "KR", "businessType": kind, "clientType": "P", "loginType": "1", "organizationCode": org}


class FakeCodef:
    """합성 계정 목록 · 삭제. mode: ok · error · timeout(지우고 예외) · partial(첫 대상만 지우고 errorList) · list_error"""

    def __init__(self, *accounts):
        self.accounts, self.mode, self.deleted, self.on_delete = list(accounts), "ok", [], None

    def get_account_list(self, service, params):
        if self.mode == "list_error":
            return json.dumps({"result": {"code": "CF-09999", "message": "secret-raw"}, "data": {}})
        if not self.accounts:
            return json.dumps({"result": {"code": "CF-04014"}, "data": {"accountList": []}})
        return json.dumps({"result": {"code": "CF-00000"}, "data": {"accountList": self.accounts}})

    def delete_account(self, service, params):
        targets = params["accountList"]
        self.deleted.append([row["organization"] for row in targets])
        if self.on_delete:
            self.on_delete()
        if self.mode == "error":
            return json.dumps({"result": {"code": "CF-09999", "message": "secret-raw"}})
        gone = targets[:1] if self.mode == "partial" else targets
        orgs = {row["organization"] for row in gone}
        self.accounts = [row for row in self.accounts if row["organizationCode"] not in orgs]
        if self.mode == "timeout":
            raise TimeoutError("read timed out")
        errors = [{"code": "CF-1"}] if self.mode == "partial" else []
        return json.dumps({"result": {"code": "CF-00000"}, "data": {"errorList": errors}})


class DeletionTests(NoCodefNetwork, TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="del@example.com", password="Synthetic123!")
        self.other = get_user_model().objects.create_user(email="del-other@example.com", password="Other123!")
        self.user.codef_connected_id_ciphertext = seal("synthetic-cid")
        self.user.codef_connected_at = timezone.now()
        self.user.save()
        for scope in ("bank", "card", "loan"):
            Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope=scope, document_version="v2", document_hash="t")
        self.bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.cardco = Connection.objects.create(user=self.user, business_type="card", organization_code="0302", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=self.bank, source_account_key="a", account_type="demand", currency="KRW", is_selected=True)
        self.card = Card.objects.create(user=self.user, connection=self.cardco, source_card_key="c", issuer_code="0302", origin="provider", is_selected=True)
        self.cardtx = FinancialTransaction.objects.create(user=self.user, card=self.card, source_kind="card", source_key="c", occurred_on=date(2026, 11, 1),
            amount=5000, direction="out", transaction_type="spending", confirmation_state="needs_review", confirmed_category_id="UNCLASSIFIED")
        self.banktx = FinancialTransaction.objects.create(user=self.user, account=self.account, source_kind="bank", source_key="b", occurred_on=date(2026, 11, 2),
            amount=7000, direction="out", transaction_type="spending", confirmation_state="needs_review", confirmed_category_id="UNCLASSIFIED")
        self.fake = FakeCodef(account("BK", "0004"), account("CD", "0302"), account("CD", "0301"))
        patcher = patch("finance.codef._client", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.login(self.user)

    def login(self, user):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        client.get("/api/csrf/")
        client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        return client

    def revoke(self, scope="card", client=None):
        return (client or self.client).delete(f"/api/finance/consents/{scope}/", {"confirm": True}, format="json")

    def withdraw(self, password="Synthetic123!", **extra):
        return self.client.delete("/api/auth/me/", {"password": password, "confirm": True, **extra}, format="json")

    def grant(self, scope):
        return self.client.post("/api/finance/consents/", {"consents": [{"purpose": "finance_inquiry", "productScope": scope, "documentVersion": "v2"}]}, format="json")

    def row(self, scope="card"):
        return PrivacyDeletion.objects.get(user_id=self.user.pk, scope=scope)

    def remaining_kinds(self):
        return sorted(row["businessType"] for row in self.fake.accounts)

    # ---------- 정상 ----------

    def test_card_revoke_completes_in_request_and_keeps_bank_auth(self):
        response = self.revoke()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["items"][1]["deletionStatus"])
        self.assertEqual(self.remaining_kinds(), ["BK"])
        self.assertFalse(Card.objects.filter(user=self.user).exists())
        row = self.row()
        self.assertEqual((row.status, row.attempts, row.lease_expires_at, row.error_code), ("completed", 1, None, None))
        self.assertIsNotNone(row.provider_deleted_at)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.codef_connected_id_ciphertext)  # 은행 동의가 남아 connectedId 유지

    def test_withdraw_completes_and_keeps_minimal_record_after_user_delete(self):
        user_id = self.user.pk
        response = self.withdraw()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"ok": True})
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertEqual(self.fake.accounts, [])
        row = PrivacyDeletion.objects.get(user_id=user_id)
        self.assertEqual((row.scope, row.status), ("all", "completed"))

    def test_all_scopes_revoked_clears_connected_id(self):
        for scope in ("card", "loan", "bank"):
            self.assertEqual(self.revoke(scope).status_code, 200)
        self.assertEqual(self.fake.accounts, [])
        self.user.refresh_from_db()
        self.assertIsNone(self.user.codef_connected_id_ciphertext)
        self.assertIsNone(self.user.codef_connected_at)

    # ---------- 실패 뒤 재시도 ----------

    def test_revoke_local_cleanup_failure_rolls_back_acceptance_before_provider_call(self):
        # 범위 철회는 로컬 정리를 접수 트랜잭션에서 한다 → 실패하면 접수 · 철회 모두 없던 일, 외부는 건드리지 않음
        client = APIClient(enforce_csrf_checks=True, raise_request_exception=False)
        client.force_login(self.user); client.get("/api/csrf/")
        client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        with patch("users.api_privacy.purge_scope", side_effect=RuntimeError("db down")):
            self.assertEqual(self.revoke(client=client).status_code, 500)
        self.assertFalse(PrivacyDeletion.objects.exists())
        self.assertTrue(Consent.objects.filter(user=self.user, product_scope="card", revoked_at=None).exists())
        self.assertEqual(self.fake.deleted, [])
        self.assertEqual(self.revoke().status_code, 200)

    def test_withdraw_local_failure_after_provider_delete_then_retry_completes(self):
        with patch.object(get_user_model(), "delete", side_effect=DatabaseError("db down")):
            response = self.withdraw()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"ok": False, "status": "retry_required"})
        row = self.row("all")
        self.assertEqual((row.status, row.failed_step, row.error_code, row.lease_token), ("local_pending", "local", "local_cleanup_failed", None))
        self.assertEqual(self.fake.accounts, [])
        call_command("privacy_deletion_retry", stdout=StringIO())
        self.assertEqual(len(self.fake.deleted), 1)  # 외부 삭제는 확인된 뒤라 다시 부르지 않음
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertEqual((self.row("all").status, self.row("all").attempts), ("completed", 2))

    def test_status_save_failure_after_provider_delete_recovers_by_relisting(self):
        real = QuerySet.update

        def flaky(qs, **fields):
            if "provider_deleted_at" in fields:
                raise DatabaseError("connection lost")
            return real(qs, **fields)

        with patch.object(QuerySet, "update", flaky):
            self.assertEqual(self.revoke().status_code, 202)
        self.assertEqual(self.row().status, "provider_pending")
        self.assertEqual(self.remaining_kinds(), ["BK"])  # 외부는 이미 지워짐
        self.assertEqual(self.revoke().status_code, 200)
        self.assertEqual(len(self.fake.deleted), 1)  # 목록 재조회로 남은 카드 인증이 없음을 확인 → 삭제를 다시 하지 않음

    def test_timeout_but_actually_deleted(self):
        self.fake.mode = "timeout"
        self.assertEqual(self.revoke().status_code, 202)
        self.assertEqual((self.row().failed_step, self.row().error_code), ("provider", "provider_unavailable"))
        self.fake.mode = "ok"
        self.assertEqual(self.revoke().status_code, 200)
        self.assertEqual(len(self.fake.deleted), 1)

    def test_partial_delete_retries_only_remaining_institution(self):
        self.fake.mode = "partial"
        self.assertEqual(self.revoke().status_code, 202)
        self.assertEqual(self.fake.deleted, [["0302", "0301"]])
        self.fake.mode = "ok"
        self.assertEqual(self.revoke().status_code, 200)
        self.assertEqual(self.fake.deleted[1], ["0301"])
        self.assertEqual(self.remaining_kinds(), ["BK"])

    def test_provider_error_is_not_treated_as_already_deleted(self):
        self.fake.mode = "list_error"
        response = self.revoke()
        self.assertEqual(response.status_code, 202)
        self.assertNotIn("secret-raw", response.content.decode())
        row = self.row()
        self.assertEqual((row.status, row.error_code), ("provider_pending", "provider_unavailable"))
        self.assertFalse(Card.objects.filter(pk=self.card.pk).exists())  # 로컬 자료는 접수 때 정리
        self.assertNotIn("secret-raw", json.dumps(list(PrivacyDeletion.objects.values()), default=str))

    def test_crashed_runner_lease_blocks_until_expiry_then_command_resumes(self):
        self.fake.mode = "error"
        self.revoke()
        # 실행 도중 프로세스가 죽은 상태: 권한이 아직 유효 → 재시도 · 명령 모두 건드리지 않음
        PrivacyDeletion.objects.filter(pk=self.row().pk).update(lease_expires_at=timezone.now() + timedelta(minutes=3), error_code=None)
        self.fake.mode = "ok"
        response = self.revoke()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["items"][1]["deletionStatus"], "processing")
        call_command("privacy_deletion_retry", stdout=StringIO())
        self.assertEqual(len(self.fake.deleted), 1)
        PrivacyDeletion.objects.filter(pk=self.row().pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        # 오류 코드 없이 죽은 실행도 권한이 만료되면 화면에 재시도 가능으로 보인다
        self.assertEqual(self.client.get("/api/finance/consents/").json()["items"][1]["deletionStatus"], "retry_required")
        out = StringIO()
        call_command("privacy_deletion_retry", stdout=out)
        call_command("privacy_deletion_retry", stdout=StringIO())  # 중복 실행해도 완료 기록은 그대로
        self.assertIn("완료 1건", out.getvalue())
        self.assertEqual(self.row().status, "completed")
        self.assertFalse(Card.objects.filter(user=self.user).exists())
        self.assertEqual(len(self.fake.deleted), 2)

    def test_repeated_request_during_run_does_not_delete_twice(self):
        seen = []
        self.fake.on_delete = lambda: seen.append(self.revoke().status_code)  # 외부 삭제 도중 같은 요청이 또 옴
        self.assertEqual(self.revoke().status_code, 200)
        self.assertEqual(seen, [202])
        self.assertEqual(len(self.fake.deleted), 1)
        self.assertEqual(PrivacyDeletion.objects.filter(user_id=self.user.pk).count(), 1)

    def test_late_runner_cannot_overwrite_or_release_takeover(self):
        """A 가 외부 삭제 중 권한이 만료 → B 가 이어받아 끝냄 → 늦게 실패한 A 는 아무것도 기록하지 않는다"""
        self.fake.mode = "error"
        self.revoke()  # 접수 + 1차 실패
        row = self.row()

        def takeover():
            self.fake.on_delete = None
            PrivacyDeletion.objects.filter(pk=row.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
            self.fake.mode = "ok"
            self.assertEqual(deletion.run(row).status, "completed")  # B
            self.fake.mode = "error"  # A 의 외부 응답은 늦게 실패로 돌아온다

        self.fake.on_delete = takeover
        PrivacyDeletion.objects.filter(pk=row.pk).update(error_code=None)
        deletion.run(row)  # A
        row.refresh_from_db()
        self.assertEqual((row.status, row.error_code, row.failed_step, row.lease_token, row.lease_expires_at),
                         ("completed", None, None, None, None))
        self.assertEqual(row.attempts, 3)

    def test_other_process_holding_user_lock_blocks_command(self):
        """다른 연결(살아 있는 실행)이 회원 잠금을 잡고 있으면 명령은 외부 삭제를 시작하지 않는다"""
        self.fake.mode = "error"
        self.revoke()
        self.fake.mode = "ok"
        from django.db import connection
        from finance.api_new_connection import LOCK_NAMESPACE
        other = connection.copy()
        key = int.from_bytes(self.user.pk.bytes[:4], "big", signed=True)
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s, %s)", [LOCK_NAMESPACE, key])
            call_command("privacy_deletion_retry", stdout=StringIO())
            self.assertEqual(len(self.fake.deleted), 1)
            self.assertEqual(self.row().status, "provider_pending")
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, key])
        finally:
            other.close()
        call_command("privacy_deletion_retry", stdout=StringIO())
        self.assertEqual(self.row().status, "completed")

    # ---------- 공유 인증 · 삭제 중 보호 ----------

    def test_bank_loan_shared_auth_deleted_only_when_both_revoked(self):
        self.assertEqual(self.revoke("bank").status_code, 200)
        self.assertEqual(self.row("bank").provider_kinds, [])
        self.assertIn("BK", self.remaining_kinds())
        self.assertEqual(self.revoke("loan").status_code, 200)
        self.assertEqual(self.row("loan").provider_kinds, ["BK"])
        self.assertNotIn("BK", self.remaining_kinds())
        self.assertIn("CD", self.remaining_kinds())

    def test_pending_deletion_blocks_reconsent_reads_and_late_sync_but_not_other_scopes(self):
        self.fake.mode = "error"
        self.assertEqual(self.revoke().status_code, 202)
        response = self.grant("card")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "privacy_deleting")
        self.assertFalse(Consent.objects.filter(user=self.user, product_scope="card", revoked_at=None).exists())
        self.assertEqual(self.client.get("/api/finance/connect/cards/").status_code, 403)
        self.assertEqual(self.client.get("/api/finance/connect/accounts/").status_code, 200)  # 다른 범위는 그대로
        self.cardco.refresh_from_db()
        self.assertEqual(self.cardco.status, "disconnected")
        from finance.sync_cards import CardConnectionGone, sync_card
        with self.assertRaises(CardConnectionGone):  # 접수 뒤 새 카드 조회: 연결이 접수 때 끊겨 외부 조회 · 저장 안 함
            sync_card(self.user, "synthetic-cid", "0302", date(2026, 10, 1), date(2026, 11, 1), ["202611"], connection_id=self.cardco.pk)
        self.assertFalse(Card.objects.filter(user=self.user).exists())
        # 삭제 대기 중에도 철회 범위 자료는 일반 조회에 나오지 않고, 다른 범위(계좌 거래)는 그대로
        listed = self.client.get("/api/finance/transactions/", {"from": "2026-10-01", "to": "2026-11-30"})
        self.assertEqual(listed.status_code, 200, listed.content)
        ids = {item["transactionId"] for item in listed.json()["items"]}
        self.assertNotIn(str(self.cardtx.pk), ids)
        self.assertIn(str(self.banktx.pk), ids)
        self.fake.mode = "ok"
        self.assertEqual(self.revoke().status_code, 200)
        self.assertEqual(self.grant("card").status_code, 201)  # 완료 뒤에는 재동의 가능

    def test_pending_bank_auth_deletion_blocks_bank_and_loan_reconsent(self):
        self.revoke("bank")
        self.fake.mode = "error"
        self.assertEqual(self.revoke("loan").status_code, 202)
        for scope in ("bank", "loan"):
            self.assertEqual(self.grant(scope).status_code, 409, scope)

    def test_withdraw_over_pending_revoke_completes_both_and_keeps_records(self):
        self.fake.mode = "error"
        self.revoke()
        self.fake.mode = "ok"
        user_id = self.user.pk
        self.assertEqual(self.withdraw().status_code, 200)
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertEqual(sorted(PrivacyDeletion.objects.filter(user_id=user_id).values_list("scope", "status")),
                         [("all", "completed"), ("card", "completed")])
        self.assertTrue(get_user_model().objects.filter(pk=self.other.pk).exists())

    def test_withdraw_failure_blocks_login_and_command_finishes_after_logout(self):
        self.fake.mode = "error"
        response = self.withdraw()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"ok": False, "status": "retry_required"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.is_active, self.user.status), (False, "withdrawn"))
        login = APIClient().post("/api/auth/login/", {"email": "del@example.com", "password": "Synthetic123!"}, format="json")
        self.assertNotEqual(login.status_code, 200)
        self.fake.mode = "ok"
        call_command("privacy_deletion_retry", stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertEqual(PrivacyDeletion.objects.get(user_id=self.user.pk).status, "completed")

    # ---------- 접수 전 검증 실패 · 남의 요청 ----------

    def test_validation_failures_do_not_accept_deletion(self):
        self.assertEqual(self.withdraw(password="wrong").status_code, 400)
        self.assertEqual(self.client.delete("/api/auth/me/", {"password": "Synthetic123!"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete("/api/finance/consents/card/", {}, format="json").status_code, 400)
        other = self.login(self.other)
        self.assertEqual(self.revoke("card", client=other).status_code, 200)  # 남의 동의 없음 → 아무것도 접수 안 함
        self.assertFalse(PrivacyDeletion.objects.exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.fake.deleted, [])

    def test_other_user_cannot_see_or_run_my_pending_deletion(self):
        self.fake.mode = "error"
        self.revoke()
        other = self.login(self.other)
        items = other.get("/api/finance/consents/").json()["items"]
        self.assertTrue(all(item["deletionStatus"] is None for item in items))
        self.fake.mode = "ok"
        other.delete("/api/finance/consents/card/", {"confirm": True}, format="json")
        self.assertEqual(self.row().status, "provider_pending")

    def test_old_completed_records_are_purged(self):
        self.revoke()
        PrivacyDeletion.objects.update(completed_at=timezone.now() - timedelta(days=31))
        call_command("privacy_deletion_retry", stdout=StringIO())
        self.assertFalse(PrivacyDeletion.objects.exists())
