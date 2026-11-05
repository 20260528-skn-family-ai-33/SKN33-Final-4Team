"""R07 은행 응답 검증: 누락 · 비정상 값을 정상 0원 · 정상 빈 목록으로 바꾸지 않는다 (합성 응답만, 실제 CODEF 호출 없음)
실행: backend 폴더에서 python manage.py test finance.test_bank_response
"""
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from . import codef
from .models import BankAccount, Connection, FinancialTransaction, SyncRun
from .sync import sync_bank


class NoCodefNetwork:
    """setUpClass 부터(setUpTestData · 시드 포함) 실제 CODEF 클라이언트를 막는다. 모킹이 빠지면 네트워크 대신 AssertionError
    _request 를 가짜로 바꾼 테스트에서도 그 아래 _client 는 따로 막혀 있다"""

    @classmethod
    def setUpClass(cls):
        patcher = patch("finance.codef._client", side_effect=AssertionError("테스트에서 실제 CODEF 클라이언트를 쓰면 안 된다"))
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        super().setUpClass()


def tx(**fields):
    return {"resAccountTrDate": "20261020", "resAccountTrTime": "123000", "resAfterTranBalance": "90000",
            "resAccountDesc3": "시험 거래", **fields}


def account(**fields):
    return {"resAccount": "110123456789", "resAccountName": "입출금", "resAccountBalance": "90000",
            "resAccountDeposit": "11", **fields}


def fake_request(data):
    return patch("finance.codef._request", return_value=data)


class GuardTest(NoCodefNetwork, SimpleTestCase):
    def test_missing_mock_fails_instead_of_network(self):
        with self.assertRaises(AssertionError):
            codef.list_accounts("cid", "0088")
        with self.assertRaises(AssertionError):
            codef.connect_bank("0088", "id", "pw")


class AmountTest(NoCodefNetwork, SimpleTestCase):
    def test_normal_values_and_keys_unchanged(self):
        """키 외 정규화 결과는 수정 전과 같다. 키는 R08 신키 = 'bank:v2:<방향>:' + 수정 전 키(아래 고정값)"""
        cases = [
            (tx(resAccountOut="10000", resAccountIn="0"),
             (10000, "out", False, None, 90000, "362a8530846e0c7f1ff7998c55e2aa4ba6d0e6c0fad3f3ac739afccec7076a8c")),
            (tx(resAccountTrDate="20261021", resAccountTrTime="090000", resAccountOut="0", resAccountIn="1,234,000",
                resAfterTranBalance="1,324,000", resAccountDesc3=None, resAccountDesc2="급여"),
             (1234000, "in", False, None, 1324000, "eec40d5e3c366ddf2311fcdd0712bd5eaf156dd24319c77c6b0bdb4997864ed5")),
            (tx(resAccountTrDate="20261027", resAccountOut="10000"),  # 입금 칸 생략
             (10000, "out", False, None, 90000, "c2316e391c3fd9c1fd3fe0c0580eb343260b8388a5c0653b59baac4624229162")),
            (tx(resAccountTrTime="123001", resAccountOut="-10000", resAccountIn="0", resAfterTranBalance="100000"),
             (10000, "out", True, 10000, 100000, "22e47f9098f44dcd7b4465d6121299841cac6520048e86873af5ba909120d94c")),
            (tx(resAccountTrDate="20261022", resAccountTrTime="100000", resAccountOut="5000", resAccountIn="",
                resAfterTranBalance="0", resAccountDesc3=None, resAccountDesc1="이체"),
             (5000, "out", False, None, 0, "160b4439ded713e770f7240256383d62be0037a93eff7a0440203c80801e197b")),
        ]
        for row, expected in cases:
            with self.subTest(expected[-1][:8]):
                row = {k: v for k, v in row.items() if v is not None}
                r = codef.normalize_transaction(row)
                legacy = expected[-1]
                self.assertEqual((r["amount"], r["direction"], r["is_canceled"], r["canceled_amount"], r["balance_after"]),
                                 expected[:-1])
                self.assertEqual(r["source_key"], f"bank:v2:{r['direction']}:{legacy}")

    def test_integer_values_are_accepted(self):
        r = codef.normalize_transaction(tx(resAccountOut=0, resAccountIn=20000, resAfterTranBalance=110000))
        self.assertEqual((r["amount"], r["direction"], r["balance_after"]), (20000, "in", 110000))

    def test_bad_amounts_are_errors_not_zero(self):
        for bad in ("abc", "1,0,00", "10.5", "1e3", "--1", True, 10.0, [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(codef.BankDataError):
                    codef.normalize_transaction(tx(resAccountOut=bad, resAccountIn="0"))
                with self.assertRaises(codef.BankDataError):  # 비정상 값을 '안 쓰는 반대편'으로 보지 않는다
                    codef.normalize_transaction(tx(resAccountOut="10000", resAccountIn=bad))
                with self.assertRaises(codef.BankDataError):
                    codef.normalize_transaction(tx(resAccountOut="10000", resAfterTranBalance=bad))

    def test_direction_cannot_be_decided(self):
        for out, inn in ((None, None), ("", ""), ("0", "0"), ("0", None), ("10000", "20000")):
            with self.subTest(out=out, inn=inn):
                row = tx(**{k: v for k, v in (("resAccountOut", out), ("resAccountIn", inn)) if v is not None})
                with self.assertRaises(codef.BankDataError):
                    codef.normalize_transaction(row)

    def test_missing_balance_after_is_unknown_but_key_unchanged(self):
        """거래 후 잔액 미제공 = None (0원 아님). 키는 수정 전처럼 0 으로 만든다 — 이미 저장된 거래와 중복되지 않게"""
        row = {k: v for k, v in tx(resAccountOut="10000").items() if k != "resAfterTranBalance"}
        r = codef.normalize_transaction(row)
        self.assertIsNone(r["balance_after"])
        self.assertEqual(r["source_key"], codef.normalize_transaction({**row, "resAfterTranBalance": "0"})["source_key"])
        self.assertEqual(codef.normalize_transaction({**row, "resAfterTranBalance": ""})["balance_after"], None)

    def test_error_message_has_no_raw_value(self):
        with self.assertRaises(codef.BankDataError) as caught:
            codef.normalize_transaction(tx(resAccountOut="110123456789원", resAccountDesc3="비밀 메모"))
        self.assertNotIn("110123456789", str(caught.exception))
        self.assertNotIn("비밀", str(caught.exception))
        self.assertEqual(caught.exception.code, "invalid_bank_data")


class ListShapeTest(NoCodefNetwork, SimpleTestCase):
    def test_account_balance(self):
        for value, expected in (("0", 0), ("1,234,567", 1234567), (90000, 90000), ("-5000", -5000)):
            with self.subTest(value=value), fake_request({"resDepositTrust": [account(resAccountBalance=value)]}):
                self.assertEqual(codef.list_accounts("cid", "0088")[0]["balance"], expected)
        for value in (None, "", "   "):  # 미제공 = None (0원 아님)
            with self.subTest(value=value), fake_request({"resDepositTrust": [account(resAccountBalance=value)]}):
                self.assertIsNone(codef.list_accounts("cid", "0088")[0]["balance"])
        with fake_request({"resDepositTrust": [{k: v for k, v in account().items() if k != "resAccountBalance"}]}):
            self.assertIsNone(codef.list_accounts("cid", "0088")[0]["balance"])
        for value in ("abc", "10.5", "1000.00", "1,0,00", True, 10.0, []):
            with self.subTest(value=value), fake_request({"resDepositTrust": [account(resAccountBalance=value)]}):
                with self.assertRaises(codef.BankDataError):
                    codef.list_accounts("cid", "0088")

    def test_empty_lists_are_normal(self):
        with fake_request({"resDepositTrust": []}):
            self.assertEqual(codef.list_accounts("cid", "0088"), [])
        with fake_request({"resTrHistoryList": []}):
            self.assertEqual(codef.list_transactions("cid", "0088", "110123456789", date(2026, 10, 1), date(2026, 10, 2)),
                             [])

    def test_missing_or_broken_lists_are_errors(self):
        for data in ({}, {"resDepositTrust": None}, {"resDepositTrust": {}}, {"resDepositTrust": "[]"},
                     {"resDepositTrust": [None]}, {"resDepositTrust": ["row"]}):
            with self.subTest(data=data), fake_request(data):
                with self.assertRaises(codef.BankDataError):
                    codef.list_accounts("cid", "0088")
        for data in ({}, {"resTrHistoryList": None}, {"resTrHistoryList": {}}, {"resTrHistoryList": [None]},
                     {"resTrHistoryList": [tx(resAccountOut="1000"), "row"]}):
            with self.subTest(data=data), fake_request(data):
                with self.assertRaises(codef.BankDataError):
                    codef.list_transactions("cid", "0088", "110123456789", date(2026, 10, 1), date(2026, 10, 2))


class BankDataSyncTest(NoCodefNetwork, TestCase):
    """저장 · 롤백 · SyncRun: 뒤쪽 계좌의 응답이 잘못돼도 일부만 저장하거나 성공으로 기록하지 않는다"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="bank-data@example.com")

    def setUp(self):
        self.start, self.end = date(2026, 10, 1), date(2026, 10, 27)
        self.data = {
            "accounts": {"resDepositTrust": [account(), account(resAccount="220987654321", resAccountName="둘째")]},
            "110123456789": {"resTrHistoryList": [tx(resAccountOut="10000", resAccountIn="0")]},
            "220987654321": {"resTrHistoryList": [tx(resAccountOut="0", resAccountIn="20000")]},
        }
        patcher = patch("finance.codef._request", side_effect=self.request)
        patcher.start()
        self.addCleanup(patcher.stop)
        for target in ("finance.sync.process_new_transactions", "finance.sync.retry_failed_transactions"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, path, params):
        return self.data["accounts"] if path == codef.PATH_ACCOUNT_LIST else self.data[params["account"]]

    def sync(self):
        return sync_bank(self.user, "cid", "0088", self.start, self.end)

    def test_bad_later_account_saves_nothing_and_keeps_previous(self):
        self.assertEqual(self.sync()["new_transaction_count"], 2)
        before = {a.pk: (a.balance, a.fetched_at, a.field_states) for a in BankAccount.objects.all()}
        good_run = SyncRun.objects.get(status="success")
        tx_ids = set(FinancialTransaction.objects.values_list("pk", flat=True))
        self.data["accounts"]["resDepositTrust"][0]["resAccountBalance"] = "50000"  # 앞 계좌는 정상 · 새 잔액
        self.data["110123456789"]["resTrHistoryList"].append(tx(resAccountTrTime="130000", resAccountOut="3000"))
        self.data["220987654321"] = {}  # 뒤쪽 계좌의 거래 목록 누락

        with self.assertRaises(codef.BankDataError):
            self.sync()

        self.assertEqual({a.pk: (a.balance, a.fetched_at, a.field_states) for a in BankAccount.objects.all()}, before)
        self.assertEqual(set(FinancialTransaction.objects.values_list("pk", flat=True)), tx_ids)
        failed = SyncRun.objects.exclude(pk=good_run.pk).get()
        self.assertEqual((failed.status, failed.error_code, failed.covered_from, failed.covered_to),
                         ("failed", "invalid_bank_data", None, None))
        self.assertIsNotNone(failed.completed_at)
        good = SyncRun.objects.get(pk=good_run.pk)
        self.assertEqual((good.status, good.covered_from, good.covered_to), ("success", self.start, self.end))

    def test_missing_balance_saves_unknown_and_transactions(self):
        self.data["accounts"]["resDepositTrust"][1].pop("resAccountBalance")
        self.assertEqual(self.sync(), {"account_count": 2, "new_transaction_count": 2, "existing_transaction_count": 0})
        self.assertEqual(BankAccount.objects.get(masked_number="****6789").balance, 90000)
        unknown = BankAccount.objects.get(masked_number="****4321")
        self.assertIsNone(unknown.balance)
        self.assertEqual(FinancialTransaction.objects.filter(account=unknown).count(), 1)
        run = SyncRun.objects.get()
        self.assertEqual((run.status, run.covered_from, run.covered_to), ("success", self.start, self.end))

    def test_newer_missing_balance_replaces_known_balance(self):
        self.sync()
        self.data["accounts"]["resDepositTrust"][0]["resAccountBalance"] = ""
        self.sync()
        self.assertIsNone(BankAccount.objects.get(masked_number="****6789").balance)  # 예전 잔액을 지금 값처럼 남기지 않는다

    def test_bad_balance_on_first_sync_creates_nothing(self):
        self.data["accounts"]["resDepositTrust"][1]["resAccountBalance"] = "abc"
        with self.assertRaises(codef.BankDataError):
            self.sync()
        self.assertFalse(BankAccount.objects.exists())
        self.assertFalse(FinancialTransaction.objects.exists())
        self.assertEqual(SyncRun.objects.get().status, "failed")
        self.assertEqual(Connection.objects.count(), 1)  # 실패 기록의 연결은 남는다 (기존 흐름)

    def test_explicit_empty_history_is_success(self):
        self.data["110123456789"] = {"resTrHistoryList": []}
        self.data["220987654321"] = {"resTrHistoryList": []}
        self.assertEqual(self.sync(), {"account_count": 2, "new_transaction_count": 0, "existing_transaction_count": 0})
        run = SyncRun.objects.get()
        self.assertEqual((run.status, run.covered_from, run.covered_to), ("success", self.start, self.end))
        self.assertEqual(BankAccount.objects.get(masked_number="****6789").balance, 90000)

    def test_zero_balance_is_saved_as_zero(self):
        self.data["accounts"]["resDepositTrust"][0]["resAccountBalance"] = "0"
        self.sync()
        self.assertEqual(BankAccount.objects.get(masked_number="****6789").balance, 0)


@override_settings(CODEF={"ENV": "demo", "CONNECTED_ID": "cid"})
class CodefCheckCommandTest(NoCodefNetwork, SimpleTestCase):
    """codef_check: 거래 후 잔액 미제공 · 실제 0원 표시, BankDataError 는 CommandError (합성 응답, 실제 클라이언트 차단)"""

    def setUp(self):
        self.data = {"accounts": {"resDepositTrust": [account()]},
                     "rows": {"resTrHistoryList": [tx(resAccountOut="1000", resAfterTranBalance=None),
                                                   tx(resAccountTrTime="130000", resAccountOut="2000", resAfterTranBalance="0")]}}
        for target, kwargs in (("finance.codef.token_ok", {"return_value": True}),
                               ("finance.codef._request", {"side_effect": lambda path, params: self.data[
                                   "accounts" if path == codef.PATH_ACCOUNT_LIST else "rows"]})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_command(self):
        out = StringIO()
        call_command("codef_check", "--tx", "6789", stdout=out)
        return out.getvalue()

    def test_account_list_missing_and_zero_balance(self):
        self.data["accounts"] = {"resDepositTrust": [account(resAccountBalance=None),
                                                     account(resAccount="220987654321", resAccountBalance="0")]}
        lines = self.run_command().splitlines()
        self.assertIn("미제공", next(line for line in lines if "****6789" in line))
        zero = next(line for line in lines if "****4321" in line)
        self.assertRegex(zero, r"\s0원")
        self.assertNotIn("미제공", zero)

    def test_missing_and_zero_balance_after(self):
        lines = [line for line in self.run_command().splitlines() if "잔액" in line]
        self.assertEqual(len(lines), 2)
        self.assertIn("미제공", lines[0])
        self.assertRegex(lines[1], r"잔액\s+0원")

    def test_bank_data_error_is_command_error(self):
        for key, word in (("accounts", "계좌 조회 실패"), ("rows", "거래내역 조회 실패")):
            with self.subTest(key):
                saved = self.data[key]
                self.data[key] = {}  # 목록 누락
                with self.assertRaises(CommandError) as caught:
                    self.run_command()
                self.data[key] = saved
                self.assertIn(word, str(caught.exception))
                self.assertNotIn("110123456789", str(caught.exception))
