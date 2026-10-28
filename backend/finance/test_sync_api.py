"""다시 불러오기 POST /api/finance/sync/ · 작업 확인 GET /api/tasks/<id>/ · 자동 불러오기 명령 (10/27 나은 결정)
CODEF 를 부르는 저장 함수(sync_bank · 대출 · 카드)는 가짜로 바꿔 끼운다.
실행: backend 폴더에서 python manage.py test finance.test_sync_api
"""
import json
import logging
from datetime import date, timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from chat.models import AsyncTask
from users.models import Consent

from . import codef, connected_id
from .models import Connection, SyncRun
from .sync_ranges import bill_months, first_load_start

URL = "/api/finance/sync/"
CARD_NO, CARD_PW = "4111222233334444", "8642"


class SyncBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(email="heri@example.com", password="Example123!")
        cls.user.codef_connected_id_ciphertext = connected_id.seal("cid-1")
        cls.user.save(update_fields=["codef_connected_id_ciphertext"])
        cls.other = User.objects.create_user(email="other@example.com", password="Example123!")
        for scope in ("bank", "card", "loan"):
            Consent.objects.create(user=cls.user, purpose="finance_inquiry", product_scope=scope,
                                   document_version="v1", document_hash="x")
        make = lambda user, kind, org, status="connected": Connection.objects.create(
            user=user, business_type=kind, organization_code=org, status=status)
        cls.shinhan = make(cls.user, "bank", "0088")
        cls.lotte = make(cls.user, "card", "0311")
        cls.hyundai = make(cls.user, "card", "0302")
        cls.kb = make(cls.user, "bank", "0004", "relogin")
        cls.others = make(cls.other, "bank", "0088")

    def setUp(self):
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.bank = self.patch("finance.api_sync.sync_bank", return_value={"account_count": 2, "new_transaction_count": 5, "existing_transaction_count": 1})
        self.loans = MagicMock(return_value={})
        self.cards = MagicMock(return_value={})
        self.loan_syncer = self.patch("finance.api_sync.loan_syncer", return_value=None)
        self.card_syncer = self.patch("finance.api_sync.card_syncer", return_value=None)

    def patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def post(self, body=None):
        return self.client.post(URL, data=json.dumps(body or {}), content_type="application/json")

    def run_sync(self, body=None):
        res = self.post(body)
        self.assertEqual(res.status_code, 202, res.content)
        task_id = res.json()["taskId"]
        task = self.client.get(f"/api/tasks/{task_id}/").json()
        return res.json(), task

    def items(self, task):
        return {item["organizationCode"]: item for item in task["result"]["items"]}


class SyncTest(SyncBase):
    def test_default_targets_every_connection_with_consented_scopes(self):
        started, task = self.run_sync()
        self.assertEqual(started["status"], "succeeded")
        self.assertEqual(set(started), {"taskId", "status"})
        self.assertEqual((task["taskType"], task["status"], task["error"]), ("finance_sync", "succeeded", None))
        self.assertEqual(task["result"]["summary"], {"total": 4, "succeeded": 1, "failed": 2, "skipped": 1})
        items = self.items(task)

        self.assertEqual(items["0088"], {
            "connectionId": str(self.shinhan.connection_id), "businessType": "bank", "organizationCode": "0088",
            "organizationName": "신한은행", "status": "succeeded", "newTransactionCount": 5, "error": None,
            "parts": [{"kind": "accounts", "status": "succeeded"},
                      {"kind": "loans", "status": "skipped", "reason": "not_supported"}],
        })
        # 카드 불러오기 함수가 아직 없음 → 실패가 아니라 건너뜀
        self.assertEqual((items["0311"]["status"], items["0311"]["parts"]),
                         ("skipped", [{"kind": "cards", "status": "skipped", "reason": "not_supported"}]))
        # 현대카드: 카드 값이 없으면 그 기관만 실패
        self.assertEqual(items["0302"]["error"], {
            "code": "invalid", "message": "카드 비밀번호가 필요해요.",
            "fields": {"cardNo": ["카드 비밀번호가 필요해요."], "cardPassword": ["카드 비밀번호가 필요해요."]},
        })
        # 다시 연결 필요한 기관은 CODEF 를 부르지 않고 실패로 알림
        self.assertEqual(items["0004"]["error"]["code"], "relogin_required")
        self.assertEqual(items["0004"]["error"]["message"], "국민은행 다시 연결이 필요해요. 연결 관리에서 다시 연결해 주세요.")

        self.bank.assert_called_once()
        user, cid, org, start, end = self.bank.call_args.args
        self.assertEqual((user, cid, org, start, end), (self.user, "cid-1", "0088", first_load_start(self.today), self.today))

    def test_refresh_range_is_last_success_minus_7_days(self):
        last = self.today - timedelta(days=3)
        SyncRun.objects.create(user=self.user, connection=self.shinhan, product_code="bank_sync", status="success",
                               covered_from=last - timedelta(days=30), covered_to=last, completed_at=timezone.now())
        SyncRun.objects.create(user=self.user, connection=self.shinhan, product_code="bank_sync", status="failed",
                               completed_at=timezone.now() + timedelta(seconds=1))
        self.run_sync({"connectionIds": [str(self.shinhan.connection_id)]})
        self.assertEqual(self.bank.call_args.args[3], last - timedelta(days=7))

    def test_loans_and_cards_when_available(self):
        self.loan_syncer.return_value = self.loans
        self.card_syncer.return_value = self.cards
        _, task = self.run_sync({
            "connectionIds": [str(self.shinhan.connection_id), str(self.hyundai.connection_id)],
            "cardCredentials": [{"connectionId": str(self.hyundai.connection_id), "cardNo": CARD_NO, "cardPassword": CARD_PW}],
        })
        items = self.items(task)
        self.assertEqual(items["0088"]["parts"][1], {"kind": "loans", "status": "succeeded"})
        self.loans.assert_called_once_with(self.user, "cid-1", "0088", first_load_start(self.today), self.today)
        self.assertEqual(items["0302"]["status"], "succeeded")
        args, kwargs = self.cards.call_args
        self.assertEqual(args, (self.user, "cid-1", "0302", first_load_start(self.today), self.today, bill_months(self.today)))
        self.assertEqual(kwargs, {"card_no": CARD_NO, "card_password": CARD_PW})

    def test_card_values_are_not_stored_or_logged(self):
        self.card_syncer.return_value = self.cards
        self.cards.side_effect = codef.CodefError("CF-90002", "기관 점검 중")
        with self.assertLogs("finance", "DEBUG") as logs:
            logging.getLogger("finance").debug("시험 표시")
            _, task = self.run_sync({"cardCredentials": [
                {"connectionId": str(self.hyundai.connection_id), "cardNo": CARD_NO, "cardPassword": CARD_PW}]})
        self.assertEqual(self.items(task)["0302"]["error"]["code"], "provider_unavailable")
        dump = serializers.serialize("json", [*AsyncTask.objects.all(), *SyncRun.objects.all(), *Connection.objects.all()])
        for text in (dump, "\n".join(logs.output), json.dumps(task)):
            self.assertNotIn(CARD_NO, text)
            self.assertNotIn(CARD_PW, text)
        self.assertNotIn("CF-", json.dumps(task))

    def test_card_value_error_from_codef_points_to_missing_field(self):
        self.card_syncer.return_value = self.cards
        self.cards.side_effect = codef.CodefError("CF-12401", "로그인 파라미터 누락", "{cardNo}")
        _, task = self.run_sync({"connectionIds": [str(self.lotte.connection_id)]})
        self.assertEqual(self.items(task)["0311"]["error"], {
            "code": "invalid", "message": "카드번호와 카드 비밀번호를 확인해 주세요.",
            "fields": {"cardNo": ["카드번호와 카드 비밀번호를 확인해 주세요."]},
        })

    def test_login_error_marks_relogin_and_others_still_saved(self):
        bank2 = Connection.objects.create(user=self.user, business_type="bank", organization_code="0020", status="connected")

        def by_org(user, cid, org, start, end):
            if org == "0088":
                raise codef.CodefError("CF-12100", "아이디 또는 비밀번호가 일치하지 않습니다.")
            return {"new_transaction_count": 1}

        self.bank.side_effect = by_org
        _, task = self.run_sync({"connectionIds": [str(self.shinhan.connection_id), str(bank2.connection_id)]})
        items = self.items(task)
        self.assertEqual(items["0088"]["error"], {"code": "relogin_required", "message": "아이디 또는 비밀번호를 확인해 주세요."})
        self.assertEqual(items["0020"]["status"], "succeeded")
        self.shinhan.refresh_from_db()
        self.assertEqual(self.shinhan.status, "relogin")
        self.assertEqual(task["result"]["summary"], {"total": 2, "succeeded": 1, "failed": 1, "skipped": 0})

    def test_provider_error_keeps_connection_and_stops_loans(self):
        self.loan_syncer.return_value = self.loans
        self.bank.side_effect = codef.CodefError("CF-90002", "기관 점검 시간입니다.")
        _, task = self.run_sync({"connectionIds": [str(self.shinhan.connection_id)]})
        item = self.items(task)["0088"]
        self.assertEqual(item["error"], {"code": "provider_unavailable", "message": "신한은행 연결이 잠시 어려워요. 잠시 후 다시 시도해 주세요."})
        self.assertEqual(item["parts"][1], {"kind": "loans", "status": "skipped", "reason": "not_run"})
        self.loans.assert_not_called()
        self.shinhan.refresh_from_db()
        self.assertEqual(self.shinhan.status, "connected")

    def test_disconnected_during_sync_is_discarded_not_failed(self):
        def disconnect(*args):
            Connection.objects.filter(pk=self.shinhan.pk).update(status="disconnected", disconnected_at=timezone.now())
            raise ValueError("조회 중 연결이 해제되어 저장하지 않았어요.")

        self.bank.side_effect = disconnect
        _, task = self.run_sync({"connectionIds": [str(self.shinhan.connection_id)]})
        item = self.items(task)["0088"]
        self.assertEqual((item["status"], item["parts"][0]), ("skipped", {"kind": "accounts", "status": "skipped", "reason": "disconnected"}))

    def test_scope_without_consent_is_skipped_not_failed(self):
        Consent.objects.filter(user=self.user, product_scope__in=["bank", "loan"]).update(revoked_at=timezone.now())
        _, task = self.run_sync({"connectionIds": [str(self.shinhan.connection_id)]})
        item = self.items(task)["0088"]
        self.assertEqual(item["status"], "skipped")
        self.assertEqual([part["reason"] for part in item["parts"]], ["no_consent", "no_consent"])
        self.bank.assert_not_called()

    def test_missing_connected_id_needs_relogin(self):
        get_user_model().objects.filter(pk=self.user.pk).update(codef_connected_id_ciphertext=None)
        _, task = self.run_sync({"connectionIds": [str(self.shinhan.connection_id)]})
        self.assertEqual(self.items(task)["0088"]["error"]["code"], "relogin_required")
        self.shinhan.refresh_from_db()
        self.assertEqual(self.shinhan.status, "relogin")
        self.bank.assert_not_called()

    def test_bad_input_is_400_without_task(self):
        disconnected = Connection.objects.create(user=self.user, business_type="bank", organization_code="0020",
                                                 status="disconnected", disconnected_at=timezone.now())
        cases = [
            ({"connectionIds": [str(self.others.connection_id)]}, "connectionIds"),
            ({"connectionIds": [str(disconnected.connection_id)]}, "connectionIds"),
            ({"connectionIds": ["abc"]}, "connectionIds"),
            ({"connectionIds": []}, "connectionIds"),
            ({"connectionIds": [str(self.shinhan.connection_id)] * 2}, "connectionIds"),
            ({"memo": "x"}, "memo"),
            ({"cardCredentials": [{"connectionId": str(self.shinhan.connection_id), "cardNo": CARD_NO, "cardPassword": CARD_PW}]},
             "cardCredentials[0].connectionId"),
            ({"connectionIds": [str(self.shinhan.connection_id)],
              "cardCredentials": [{"connectionId": str(self.hyundai.connection_id), "cardNo": CARD_NO, "cardPassword": CARD_PW}]},
             "cardCredentials[0].connectionId"),
            ({"cardCredentials": [{"connectionId": str(self.hyundai.connection_id), "cardNo": "12", "cardPassword": CARD_PW}]},
             "cardCredentials[0].cardNo"),
        ]
        for body, field in cases:
            with self.subTest(field=field, body=body):
                res = self.post(body)
                self.assertEqual(res.status_code, 400)
                self.assertIn(field, res.json()["error"]["fields"])
        self.assertFalse(AsyncTask.objects.exists())
        self.bank.assert_not_called()

    def test_no_connection_is_400(self):
        self.client.force_login(get_user_model().objects.create_user(email="new@example.com", password="Example123!"))
        res = self.post()
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["fields"], {"nonFieldErrors": ["연결된 기관이 없어요. 먼저 기관을 연결해 주세요."]})

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get(f"/api/tasks/{AsyncTask().task_id}/").status_code, 403)


class DuplicateTest(SyncBase):
    def body(self):
        return {"connectionIds": [str(self.shinhan.connection_id)]}

    def test_running_task_is_returned_instead_of_new_one(self):
        running = AsyncTask.objects.create(user=self.user, task_type="finance_sync", request_key="finance_sync_auto:x",
                                           input_hash="h", status="running", started_at=timezone.now())
        res = self.post(self.body())
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.json(), {"taskId": str(running.task_id), "status": "running"})
        self.bank.assert_not_called()

    def test_stale_running_task_is_failed_and_new_one_starts(self):
        stale = AsyncTask.objects.create(user=self.user, task_type="finance_sync", request_key="finance_sync:old",
                                         input_hash="h", status="running", started_at=timezone.now() - timedelta(minutes=11))
        res = self.post(self.body())
        self.assertNotEqual(res.json()["taskId"], str(stale.task_id))
        stale.refresh_from_db()
        self.assertEqual((stale.status, stale.error_code), ("failed", "stale"))
        self.assertEqual(self.client.get(f"/api/tasks/{stale.task_id}/").json()["error"],
                         {"code": "stale", "message": "작업이 중간에 멈췄어요. 다시 시도해 주세요."})

    def test_same_request_within_a_minute_returns_last_result(self):
        first = self.post(self.body()).json()
        second = self.post(self.body()).json()
        self.assertEqual(first, second)
        self.assertEqual(self.bank.call_count, 1)
        # 대상이 다르면 새로 부른다
        self.post({"connectionIds": [str(self.lotte.connection_id)]})
        # 1분이 지나면 새로 부른다
        AsyncTask.objects.update(completed_at=timezone.now() - timedelta(seconds=61))
        third = self.post(self.body()).json()
        self.assertNotEqual(third["taskId"], first["taskId"])
        self.assertEqual(self.bank.call_count, 2)

    def test_new_card_values_run_again_within_a_minute(self):
        body = {"connectionIds": [str(self.hyundai.connection_id)]}
        first = self.post(body).json()
        body["cardCredentials"] = [{"connectionId": str(self.hyundai.connection_id), "cardNo": CARD_NO, "cardPassword": CARD_PW}]
        self.assertNotEqual(self.post(body).json()["taskId"], first["taskId"])

    def test_unexpected_error_fails_task_with_safe_message(self):
        with patch("finance.api_sync.sync_connections", side_effect=RuntimeError("bug")), self.assertLogs("config.tasks", "ERROR"):
            started = self.post(self.body()).json()
        self.assertEqual(started["status"], "failed")
        task = self.client.get(f"/api/tasks/{started['taskId']}/").json()
        self.assertEqual((task["result"], task["error"]), (None, {"code": "server_error", "message": "잠시 후 다시 시도해 주세요."}))

    def test_other_users_task_is_404(self):
        task = AsyncTask.objects.create(user=self.other, task_type="finance_sync", request_key="k", input_hash="h")
        self.assertEqual(self.client.get(f"/api/tasks/{task.task_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{AsyncTask().task_id}/").status_code, 404)


class AutoSyncCommandTest(SyncBase):
    def test_runs_living_connections_skips_busy_and_card_value_banks(self):
        busy = get_user_model().objects.create_user(email="busy@example.com", password="Example123!")
        busy.codef_connected_id_ciphertext = connected_id.seal("cid-2")
        busy.save(update_fields=["codef_connected_id_ciphertext"])
        Connection.objects.create(user=busy, business_type="bank", organization_code="0088", status="connected")
        AsyncTask.objects.create(user=busy, task_type="finance_sync", request_key="finance_sync:manual",
                                 input_hash="h", status="running", started_at=timezone.now())
        out = StringIO()
        call_command("finance_auto_sync", stdout=out)
        self.assertEqual(out.getvalue().strip(), "자동 불러오기 1명 · 진행 중이라 건너뜀 1명 · 대상 기관 없음 0명")

        task = AsyncTask.objects.get(user=self.user)
        self.assertTrue(task.request_key.startswith("finance_sync_auto:"))
        codes = [item["organization_code"] for item in task.result["items"]]
        self.assertEqual(codes, ["0088", "0311"])  # relogin(국민) · 카드 값 필요(현대카드) 제외
        self.assertEqual(self.bank.call_count, 1)
        self.assertNotIn("heri@example.com", out.getvalue())


class RangeTest(TestCase):
    def test_bill_months_and_refresh_start(self):
        from .sync_ranges import refresh_start

        self.assertEqual(bill_months(date(2026, 1, 15)), ("202512", "202601"))
        self.assertEqual(refresh_start(date(2026, 10, 27), date(2026, 10, 25)), date(2026, 10, 18))
        self.assertEqual(refresh_start(date(2026, 10, 27), None), date(2026, 6, 1))
