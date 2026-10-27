"""10. 연결 해제 API와 은행 저장(sync_bank)이 겹칠 때 (10/27 나은과 맞춘 규칙)

- 해제가 먼저 끝나면: 해제 뒤 도착한 조회 결과는 저장하지 않고 버린다.
- 저장이 먼저 잠그면: 해제 요청은 저장이 끝날 때까지 기다렸다가 해제한다 (해제 전에 도착한 데이터라 저장 유지).
두 쪽이 같은 Connection 행 잠금으로 순서를 지키는지 실제 해제 API로 확인한다.
실행: backend 폴더에서 python manage.py test finance.test_disconnect_sync
"""
import threading
import time
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection as db_connection
from django.test import Client, TestCase, TransactionTestCase

from . import codef
from .models import BankAccount, Connection, FinancialTransaction, SyncRun
from .sync import sync_bank

ORG = "0088"
START, END = date(2026, 10, 1), date(2026, 10, 27)
ACCOUNTS = [{"account_no": "110123456789", "name": "입출금", "balance": 90000, "kind_code": "11", "org": ORG}]


def bank_rows():
    def row(out, inn, day):
        return codef.normalize_transaction({
            "resAccountTrDate": day, "resAccountTrTime": "123000", "resAccountOut": out, "resAccountIn": inn,
            "resAfterTranBalance": "90000", "resAccountDesc3": "시험 거래",
        })
    return [row("10000", "0", "20261020"), row("0", "20000", "20261021")]


def delete_url(conn):
    return f"/api/finance/connections/{conn.connection_id}/"


def active_connection(user):
    return Connection.objects.get(user=user, organization_code=ORG, disconnected_at=None)


class DisconnectDuringSyncTest(TestCase):
    """조회(CODEF 호출) 도중 사용자가 연결을 해제하면 조회 결과는 저장되지 않는다"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="sync-heri@example.com", password="Example123!")

    def test_disconnect_api_during_codef_call_discards_result(self):
        client = Client()
        client.force_login(self.user)
        statuses = []

        def list_transactions(*args):
            # 외부 조회가 끝나기 전에 사용자가 해제 버튼을 누른 상황
            statuses.append(client.delete(delete_url(active_connection(self.user))).status_code)
            return bank_rows()

        with patch("finance.codef.list_accounts", return_value=ACCOUNTS), \
                patch("finance.codef.list_transactions", side_effect=list_transactions), \
                patch("finance.sync.process_new_transactions") as pipeline:
            with self.assertRaisesMessage(ValueError, "조회 중 연결이 해제"):
                sync_bank(self.user, "test-connected-id", ORG, START, END)

        self.assertEqual(statuses, [204])
        conn = Connection.objects.get(user=self.user, organization_code=ORG)
        self.assertEqual(conn.status, "disconnected")
        self.assertIsNotNone(conn.disconnected_at)
        self.assertFalse(BankAccount.objects.filter(user=self.user).exists())
        self.assertFalse(FinancialTransaction.objects.filter(user=self.user).exists())
        self.assertEqual(SyncRun.objects.get(user=self.user).status, "failed")
        pipeline.assert_not_called()


class DisconnectWaitsForSyncTest(TransactionTestCase):
    """저장이 연결 행을 잠근 동안 온 해제 요청은 기다렸다가, 저장이 끝난 뒤 해제된다"""

    serialized_rollback = True

    def test_disconnect_waits_until_sync_commits(self):
        user = get_user_model().objects.create_user(email="sync-heri@example.com", password="Example123!")
        locked, release = threading.Event(), threading.Event()
        result, errors = {}, []

        def hold_lock(*args):
            # sync_bank가 연결 행을 잠근 트랜잭션 안에서 부르는 단계. 여기서 잠시 붙잡아 둔다
            locked.set()
            release.wait(timeout=10)

        def run_sync():
            try:
                result["sync"] = sync_bank(user, "test-connected-id", ORG, START, END)
            except Exception as exc:
                errors.append(exc)
            finally:
                db_connection.close()

        def run_delete():
            try:
                client = Client()
                client.force_login(user)
                result["delete"] = client.delete(delete_url(active_connection(user))).status_code
            except Exception as exc:
                errors.append(exc)
            finally:
                db_connection.close()

        with patch("finance.codef.list_accounts", return_value=ACCOUNTS), \
                patch("finance.codef.list_transactions", return_value=bank_rows()), \
                patch("finance.sync.process_new_transactions", side_effect=hold_lock):
            sync_thread = threading.Thread(target=run_sync)
            sync_thread.start()
            self.assertTrue(locked.wait(timeout=10), "저장 단계까지 가지 못했어요")
            delete_thread = threading.Thread(target=run_delete)
            delete_thread.start()
            time.sleep(0.5)
            # 저장이 잠금을 쥐고 있는 동안 해제는 끝나지 않아야 한다
            self.assertTrue(delete_thread.is_alive())
            self.assertNotIn("delete", result)
            release.set()
            sync_thread.join(timeout=30)
            delete_thread.join(timeout=30)

        if errors:
            raise errors[0]
        self.assertEqual(result["delete"], 204)
        self.assertEqual(result["sync"]["new_transaction_count"], 2)
        conn = Connection.objects.get(user=user, organization_code=ORG)
        self.assertEqual(conn.status, "disconnected")
        self.assertEqual(FinancialTransaction.objects.filter(user=user).count(), 2)
        self.assertEqual(SyncRun.objects.get(user=user).status, "success")
