"""새 연결 POST /api/finance/connections/ (finance-connect.md '금융기관 인증 · 새 연결', 10/27 나은 결정)
CODEF 는 가짜 클라이언트로 바꿔 끼운다. 실행: backend 폴더에서 python manage.py test finance.test_new_connection
"""
import json
import logging
import threading
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import serializers
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from users.models import Consent

from . import connected_id
from .models import BankAccount, Connection, SyncRun
from .sync_ranges import first_load_start
from .test_concurrency import run_together

URL = "/api/finance/connections/"
SECRETS = ("hong-login", "Bank-Pass-9!", "4111222233334444", "8642")
CARD_NO_SAMPLE = "5555666677778888"


def ok(cid="cid-1"):
    return {"result": {"code": "CF-00000", "message": "성공"},
            "data": {"connectedId": cid, "successList": [{"organization": "x"}], "errorList": []}}


def sms_needed(jti="j-1"):
    return {"result": {"code": "CF-03002", "message": "추가 인증 정보 입력이 필요합니다."},
            "data": {"continue2Way": True, "method": "smsAuthNo", "jobIndex": 0, "threadIndex": 0,
                     "jti": jti, "twoWayTimestamp": 1793088000000}}


def fail(code, message):
    return {"result": {"code": "CF-04000", "message": "요청 정보 중 오류가 있습니다."},
            "data": {"errorList": [{"code": code, "message": message, "extraMessage": ""}]}}


class FakeCodef:
    """CODEF 대역. responses: [응답 dict] 를 차례로 돌려주거나, 함수(path, params) → 응답"""

    def __init__(self, responses):
        self.responses, self.calls, self.lock = responses, [], threading.Lock()

    def _answer(self, kind, path, params):
        with self.lock:
            self.calls.append((kind, path, deepcopy(params)))
            answer = self.responses(path, params) if callable(self.responses) else self.responses.pop(0)
        return json.dumps(answer)

    def request_product(self, path, service, params):
        return self._answer("product", path, params)

    def request_certification(self, path, service, params):
        return self._answer("certification", path, params)


def bank(code="0088", **extra):
    return {"businessType": "bank", "organizationCode": code, "loginId": "hong-login", "password": "Bank-Pass-9!", **extra}


def card(code="0311", **extra):
    return {"businessType": "card", "organizationCode": code, "loginId": "hong-login", "password": "Bank-Pass-9!", **extra}


class ConnectBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="heri@example.com", password="Example123!")
        cls.other = get_user_model().objects.create_user(email="other@example.com", password="Example123!")
        for scope in ("bank", "card"):
            Consent.objects.create(user=cls.user, purpose="finance_inquiry", product_scope=scope,
                                   document_version="v1", document_hash="x")

    def setUp(self):
        self.client.force_login(self.user)
        self.fake = FakeCodef([])
        for target, kwargs in (
            ("finance.codef._client", {"return_value": self.fake}),
            ("finance.codef._service_type", {"return_value": "demo"}),
            ("finance.codef_accounts.encrypt_rsa", {"side_effect": lambda value, key: f"rsa({len(value)})"}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        sync = patch("finance.api_new_connection.sync_bank", side_effect=self.fake_sync)
        self.sync = sync.start()
        self.addCleanup(sync.stop)

    def fake_sync(self, user, cid, org, start, end, connection_id=None, require_consent=False):
        conn = Connection.objects.get(user=user, organization_code=org, disconnected_at=None)
        BankAccount.objects.get_or_create(user=user, connection=conn, source_account_key=f"key-{org}",
                                          defaults={"masked_number": "****1234", "account_type": "demand"})
        return {"account_count": 1, "new_transaction_count": 0, "existing_transaction_count": 0}

    def post(self, *institutions):
        return self.client.post(URL, data=json.dumps({"institutions": list(institutions)}), content_type="application/json")

    def assert_no_secret(self, text):
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertNotIn("CF-", text)


class NewConnectionTest(ConnectBase):
    def test_first_bank_creates_connected_id_connection_and_loads_accounts(self):
        self.fake.responses = [ok("cid-1")]
        res = self.post(bank())
        self.assertEqual(res.status_code, 200)
        item = res.json()["results"][0]
        conn = Connection.objects.get(user=self.user)
        self.assertEqual(item, {
            "businessType": "bank", "organizationCode": "0088", "organizationName": "신한은행", "status": "connected",
            "connectionId": str(conn.connection_id), "reconnected": False, "syncStatus": "done", "accountCount": 1,
        })
        self.assertEqual((conn.status, conn.auth_method), ("connected", "id_password"))
        self.assertIsNotNone(conn.last_authenticated_at)

        # connectedId 는 암호화해서만 저장
        self.user.refresh_from_db()
        self.assertNotIn("cid-1", self.user.codef_connected_id_ciphertext)
        self.assertEqual(connected_id.load(self.user), "cid-1")
        self.assertIsNotNone(self.user.codef_connected_at)

        kind, path, params = self.fake.calls[0]
        self.assertEqual((kind, path), ("product", "/v1/account/create"))
        self.assertNotIn("connectedId", params)
        self.assertEqual(params["accountList"][0], {
            "countryCode": "KR", "businessType": "BK", "clientType": "P", "organization": "0088",
            "loginType": "1", "id": "hong-login", "password": "rsa(12)",
        })
        user, cid, org, start, end = self.sync.call_args.args
        self.assertEqual(self.sync.call_args.kwargs, {"connection_id": conn.pk, "require_consent": True})
        self.assertEqual((user, cid, org, end), (self.user, "cid-1", "0088", timezone.localdate()))
        self.assertEqual(start, first_load_start(end))  # 4달 전 1일
        self.assert_no_secret(res.content.decode())

    def test_next_bank_adds_and_reconnect_updates_with_same_or_new_row(self):
        self.fake.responses = [ok("cid-1"), ok("cid-1")]
        self.post(bank("0088"))
        self.assertEqual(self.post(bank("0004")).json()["results"][0]["reconnected"], False)
        self.assertEqual(self.fake.calls[1][1], "/v1/account/add")
        self.assertEqual(self.fake.calls[1][2]["connectedId"], "cid-1")

        # 다시 연결 필요(relogin) → 같은 행을 connected 로
        shinhan = Connection.objects.get(organization_code="0088")
        Connection.objects.filter(pk=shinhan.pk).update(status="relogin")
        self.fake.responses = [ok("cid-1")]
        item = self.post(bank("0088")).json()["results"][0]
        self.assertEqual((item["connectionId"], item["reconnected"]), (str(shinhan.connection_id), True))
        self.assertEqual(self.fake.calls[-1][1], "/v1/account/update")
        shinhan.refresh_from_db()
        self.assertEqual(shinhan.status, "connected")

        # 해제한 기관 → 새 연결 행 (해제 기록은 그대로), CODEF 에서는 아직 안 뺐으니 update
        self.assertEqual(self.client.delete(f"{URL}{shinhan.connection_id}/").status_code, 204)
        self.fake.responses = [ok("cid-1")]
        item = self.post(bank("0088")).json()["results"][0]
        self.assertNotEqual(item["connectionId"], str(shinhan.connection_id))
        self.assertTrue(item["reconnected"])
        self.assertEqual(self.fake.calls[-1][1], "/v1/account/update")
        self.assertEqual(Connection.objects.filter(organization_code="0088").count(), 2)
        self.user.refresh_from_db()
        self.assertEqual(connected_id.load(self.user), "cid-1")

    def test_add_already_registered_falls_back_to_update(self):
        self.fake.responses = [ok("cid-1"), fail("CF-99991", "이미 등록된 기관입니다."), ok("cid-1")]
        self.post(bank("0088"))
        item = self.post(bank("0004")).json()["results"][0]
        self.assertEqual(item["status"], "connected")
        self.assertEqual([call[1] for call in self.fake.calls[1:]], ["/v1/account/add", "/v1/account/update"])

    def test_partial_success_is_200_with_reason_per_institution(self):
        self.fake.responses = [ok("cid-1"), fail("CF-12100", "아이디 또는 비밀번호가 일치하지 않습니다.")]
        with self.assertLogs("finance.api_new_connection", "WARNING") as logs:
            res = self.post(bank("0088"), bank("0004"))
        self.assertEqual(res.status_code, 200)
        first, second = res.json()["results"]
        self.assertEqual(first["status"], "connected")
        self.assertEqual(second, {
            "businessType": "bank", "organizationCode": "0004", "organizationName": "국민은행", "status": "failed",
            "error": {"code": "invalid", "message": "아이디 또는 비밀번호를 확인해 주세요."},
        })
        self.assertEqual(list(Connection.objects.values_list("organization_code", flat=True)), ["0088"])
        # CODEF 원래 코드는 로그에만
        self.assertIn("CF-12100", "\n".join(logs.output))
        self.assert_no_secret(res.content.decode())
        self.assert_no_secret("\n".join(logs.output).replace("CF-12100", ""))

    def test_all_failed_uses_failure_table(self):
        cases = [
            (fail("CF-12100", "비밀번호 오류"), 400, "invalid", "아이디 또는 비밀번호를 확인해 주세요."),
            (fail("CF-90001", "비밀번호 오류 횟수를 초과하여 이용이 정지되었습니다."), 400, "invalid",
             "신한은행 홈페이지에서 직접 로그인해 잠금을 풀어 주세요."),
            (fail("CF-90002", "기관 점검 시간입니다."), 502, "provider_unavailable",
             "신한은행 연결이 잠시 어려워요. 잠시 후 다시 시도해 주세요."),
            ({"result": {"code": "CF-09999", "message": "서버 오류"}, "data": {}}, 502, "provider_unavailable",
             "신한은행 연결이 잠시 어려워요. 잠시 후 다시 시도해 주세요."),
        ]
        for answer, http, code, message in cases:
            with self.subTest(message=message):
                self.fake.responses = [answer]
                res = self.post(bank())
                self.assertEqual(res.status_code, http)
                self.assertEqual(res.json(), {"error": {"code": code, "message": message}})
                self.assert_no_secret(res.content.decode())
        self.assertFalse(Connection.objects.exists())
        self.user.refresh_from_db()
        self.assertIsNone(self.user.codef_connected_id_ciphertext)

    def test_all_failed_with_many_institutions_lists_each(self):
        self.fake.responses = [fail("CF-12100", "비밀번호 오류"), fail("CF-90002", "기관 점검 중")]
        res = self.post(bank("0088"), bank("0004"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"], {
            "code": "invalid", "message": "연결하지 못했어요. 기관별 안내를 확인해 주세요.",
            "fields": {"institutions[0]": ["아이디 또는 비밀번호를 확인해 주세요."],
                       "institutions[1]": ["국민은행 연결이 잠시 어려워요. 잠시 후 다시 시도해 주세요."]},
        })
        self.fake.responses = [fail("CF-90002", "기관 점검 중"), {}]
        self.assertEqual(self.post(bank("0088"), bank("0004")).status_code, 502)

    def test_sync_failure_keeps_connection(self):
        self.fake.responses = [ok("cid-1")]
        self.sync.side_effect = RuntimeError("조회 실패")
        item = self.post(bank()).json()["results"][0]
        self.assertEqual((item["status"], item["syncStatus"], item["accountCount"]), ("connected", "failed", 0))
        self.assertEqual(Connection.objects.get().status, "connected")


class SmsAuthTest(ConnectBase):
    def start(self, inst=None):
        self.fake.responses = [sms_needed("j-1")]
        res = self.post(inst or card())
        self.assertEqual(res.status_code, 200)
        return res.json()["results"][0]

    def test_two_way_flow(self):
        first = self.start()
        self.assertEqual(first["status"], "sms_required")
        self.assertEqual(set(first["smsAuth"]), {"authToken", "expiresAt", "phoneHint"})
        self.assertFalse(Connection.objects.exists())
        self.assert_no_secret(json.dumps(first))

        self.fake.responses = [ok("cid-7")]
        res = self.post(card(smsAuth={"authToken": first["smsAuth"]["authToken"], "code": "123456"}))
        item = res.json()["results"][0]
        self.assertEqual((item["status"], item["organizationName"], item["syncStatus"]), ("connected", "롯데카드", "not_supported"))
        kind, path, params = self.fake.calls[-1]
        self.assertEqual((kind, path), ("certification", "/v1/account/create"))
        self.assertEqual((params["smsAuthNo"], params["is2Way"]), ("123456", True))
        self.assertEqual(params["twoWayInfo"], {"jobIndex": 0, "threadIndex": 0, "jti": "j-1", "twoWayTimestamp": 1793088000000})
        self.assertEqual(params["accountList"][0]["businessType"], "CD")
        self.sync.assert_not_called()
        self.assertEqual(self.client.get(URL).json()["items"][0]["organizationName"], "롯데카드")

    def test_wrong_code_asks_again_with_new_token(self):
        first = self.start()
        self.fake.responses = [sms_needed("j-2")]
        item = self.post(card(smsAuth={"authToken": first["smsAuth"]["authToken"], "code": "000000"})).json()["results"][0]
        self.assertEqual(item["status"], "sms_required")
        self.assertEqual(item["error"], {"code": "invalid", "message": "인증번호가 맞지 않아요."})
        self.assertNotEqual(item["smsAuth"]["authToken"], first["smsAuth"]["authToken"])

    def test_expired_or_foreign_token_restarts_without_codef(self):
        first = self.start()
        calls = len(self.fake.calls)
        with patch("finance.api_new_connection.SMS_SECONDS", -1):
            res = self.post(card(smsAuth={"authToken": first["smsAuth"]["authToken"], "code": "123456"}))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["message"], "인증 시간이 지났어요. 처음부터 다시 연결해 주세요.")
        # 다른 기관 · 다른 회원 · 위조 토큰
        for inst in (card("0306", smsAuth={"authToken": first["smsAuth"]["authToken"], "code": "123456"}),
                     card(smsAuth={"authToken": first["smsAuth"]["authToken"] + "x", "code": "123456"})):
            res = self.post(inst)
            self.assertEqual(res.json()["error"]["message"], "문자 인증 정보가 맞지 않아요. 처음부터 다시 연결해 주세요.")
        self.client.force_login(self.other)
        Consent.objects.create(user=self.other, purpose="finance_inquiry", product_scope="card", document_version="v1", document_hash="x")
        res = self.post(card(smsAuth={"authToken": first["smsAuth"]["authToken"], "code": "123456"}))
        self.assertEqual(res.json()["error"]["message"], "문자 인증 정보가 맞지 않아요. 처음부터 다시 연결해 주세요.")
        self.assertEqual(len(self.fake.calls), calls)

    def test_create_finished_elsewhere_during_sms_restarts(self):
        first = self.start()
        self.fake.responses = [ok("cid-1")]
        self.post(bank())  # 문자 인증을 기다리는 사이 은행 연결로 connectedId 가 생김
        res = self.post(card(smsAuth={"authToken": first["smsAuth"]["authToken"], "code": "123456"}))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(Connection.objects.count(), 1)

    def test_other_extra_auth_is_not_supported(self):
        answer = sms_needed()
        answer["data"]["method"] = "secureNo"
        self.fake.responses = [answer]
        res = self.post(card())
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["message"], "롯데카드에서 요구한 추가 인증은 아직 지원하지 않아요.")


class CardFieldsAndInputTest(ConnectBase):
    def test_hyundai_card_needs_card_fields_and_nothing_is_stored(self):
        res = self.post(card("0302"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.json()["error"]["fields"]), {"institutions[0].cardNo", "institutions[0].cardPassword"})
        self.assertEqual(self.fake.calls, [])

        self.fake.responses = [ok("cid-3")]
        with self.assertLogs("finance", "DEBUG") as logs:
            logging.getLogger("finance").debug("시험 표시")  # 로그가 하나도 없으면 assertLogs 가 실패해서 한 줄 남긴다
            res = self.post(card("0302", cardNo="4111222233334444", cardPassword="8642"))
        self.assertEqual(res.json()["results"][0]["status"], "connected")
        item = self.fake.calls[-1][2]["accountList"][0]
        self.assertEqual((item["cardNo"], item["cardPassword"]), ("4111222233334444", "rsa(4)"))

        # DB · 로그 · 응답 어디에도 아이디 · 비밀번호 · 카드번호 · 카드 비밀번호가 없다
        dump = serializers.serialize("json", [*get_user_model().objects.all(), *Connection.objects.all(), *SyncRun.objects.all()])
        self.assert_no_secret(dump)
        self.assert_no_secret("\n".join(logs.output))
        self.assert_no_secret(res.content.decode())

    def test_codef_04000_uses_error_list_and_missing_field_names(self):
        # 10/28 나은 데모 확인: 겉은 CF-04000, 진짜 이유는 errorList 첫 줄 CF-12401, 빠진 칸은 extraMessage 의 {칸}
        answer = {"result": {"code": "CF-04000", "message": "계정 등록에 실패했습니다."},
                  "data": {"errorList": [{"code": "CF-12401", "message": "로그인 파라미터 누락", "extraMessage": "{cardPassword}"}]}}
        self.fake.responses = [answer]
        res = self.post(card("0301", cardNo=CARD_NO_SAMPLE))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["fields"], {"institutions[0].cardPassword": ["카드번호와 카드 비밀번호를 확인해 주세요."]})
        self.fake.responses = [{"result": {"code": "CF-04000", "message": "계정 등록에 실패했습니다."},
                                "data": {"errorList": [{"code": "CF-12100", "message": "x", "extraMessage": ""}]}}]
        self.assertEqual(self.post(card("0301")).json()["error"]["message"], "아이디 또는 비밀번호를 확인해 주세요.")

    def test_codef_card_fields_error_points_to_fields(self):
        self.fake.responses = [fail("CF-12401", "카드번호 누락")]
        res = self.post(card("0301"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["fields"], {
            "institutions[0].cardNo": ["카드번호와 카드 비밀번호를 확인해 주세요."],
            "institutions[0].cardPassword": ["카드번호와 카드 비밀번호를 확인해 주세요."],
        })

    def test_consent_required_before_codef(self):
        Consent.objects.filter(user=self.user, product_scope="card").update(revoked_at=timezone.now())
        res = self.post(bank(), card())
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"], {"code": "consent_required", "message": "카드 조회 동의가 필요해요."})
        Consent.objects.filter(user=self.user, product_scope="bank").update(revoked_at=timezone.now())
        self.assertEqual(self.post(bank()).json()["error"]["message"], "계좌 조회 동의가 필요해요.")
        self.assertEqual(self.fake.calls, [])

    def test_bad_input_is_400_without_codef(self):
        token = {"authToken": "t", "code": "123456"}
        cases = [
            ({"institutions": []}, "institutions"),
            ({"institutions": [bank()] * 11}, "institutions"),
            ({"institutions": [bank()], "extra": 1}, "extra"),
            ({"institutions": [bank(cardNo="4111222233334444")]}, "institutions[0].cardNo"),
            ({"institutions": [bank(memo="x")]}, "institutions[0].memo"),
            ({"institutions": [bank("9999")]}, "institutions[0].organizationCode"),
            ({"institutions": [bank("0002")]}, "institutions[0].organizationCode"),  # 공동인증서 전용
            ({"institutions": [{**bank(), "businessType": "loan"}]}, "institutions[0].businessType"),
            ({"institutions": [bank(loginId=" ")]}, "institutions[0].loginId"),
            ({"institutions": [bank(password=1234)]}, "institutions[0].password"),
            ({"institutions": [bank(), bank()]}, "institutions[1].organizationCode"),
            ({"institutions": [card(cardNo="1234")]}, "institutions[0].cardNo"),
            ({"institutions": [card(cardPassword="123")]}, "institutions[0].cardPassword"),
            ({"institutions": [card(smsAuth={**token, "code": "12"})]}, "institutions[0].smsAuth.code"),
            ({"institutions": [card(smsAuth=token), bank()]}, "nonFieldErrors"),
            ({"institutions": ["x"]}, "institutions[0]"),
            # 글자가 아닌 값도 500 이 아니라 400 (PR #53 리뷰)
            ({"institutions": [{**bank(), "businessType": []}]}, "institutions[0].businessType"),
            ({"institutions": [{**bank(), "organizationCode": {}}]}, "institutions[0].organizationCode"),
            ({"institutions": [{**bank(), "businessType": [], "organizationCode": {}}, bank()]}, "institutions[0].businessType"),
            ({"institutions": [card(cardNo=[], cardPassword={})]}, "institutions[0].cardNo"),
            ({"institutions": [card(smsAuth={"authToken": [], "code": {}})]}, "institutions[0].smsAuth.code"),
        ]
        for body, field in cases:
            with self.subTest(field=field):
                res = self.client.post(URL, data=json.dumps(body), content_type="application/json")
                self.assertEqual(res.status_code, 400)
                self.assertIn(field, res.json()["error"]["fields"])
        self.assertEqual(self.fake.calls, [])

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.post(bank()).status_code, 403)


class NewConnectionConcurrencyTest(TransactionTestCase):
    """같은 회원이 은행 두 곳을 동시에 연결해도 connectedId 는 하나만 만든다 (나머지는 add)"""

    serialized_rollback = True

    def test_parallel_first_connections_create_once(self):
        user = get_user_model().objects.create_user(email="heri@example.com", password="Example123!")
        Consent.objects.create(user=user, purpose="finance_inquiry", product_scope="bank", document_version="v1", document_hash="x")
        fake = FakeCodef(lambda path, params: ok("cid-1"))

        def connect(code):
            def job():
                client = Client()
                client.force_login(user)
                body = json.dumps({"institutions": [bank(code)]})
                return client.post(URL, data=body, content_type="application/json").status_code
            return job

        with patch("finance.codef._client", return_value=fake), patch("finance.codef._service_type", return_value="demo"), \
                patch("finance.codef_accounts.encrypt_rsa", return_value="rsa"), \
                patch("finance.api_new_connection.sync_bank", return_value={}):
            statuses = run_together(connect("0088"), connect("0004"))
        self.assertEqual(statuses, [200, 200])
        self.assertEqual(sorted(call[1] for call in fake.calls), ["/v1/account/add", "/v1/account/create"])
        self.assertEqual(Connection.objects.filter(user=user, status="connected").count(), 2)


class FirstLoadStartTest(TestCase):
    def test_four_months_ago_first_day(self):
        self.assertEqual(first_load_start(date(2026, 10, 27)), date(2026, 6, 1))
        self.assertEqual(first_load_start(date(2026, 2, 28)), date(2025, 10, 1))
        self.assertEqual(first_load_start(date(2026, 4, 1)), date(2025, 12, 1))


class ConnectedIdKeyTest(TestCase):
    def test_round_trip_and_other_key_cannot_read(self):
        sealed = connected_id.seal("cid-secret")
        self.assertNotIn("cid-secret", sealed)
        self.assertEqual(connected_id.unseal(sealed), "cid-secret")
        with override_settings(CONNECTED_ID_ENCRYPTION_KEY=b"k" * 32):
            with self.assertRaises(connected_id.ConnectedIdUnreadable):
                connected_id.unseal(sealed)

    def test_settings_key_must_be_32_bytes(self):
        from django.core.exceptions import ImproperlyConfigured

        from config.settings import encryption_key

        good = "A" * 43 + "="
        with patch.dict("os.environ", {"TEST_KEY": good}):
            self.assertEqual(len(encryption_key("TEST_KEY")), 32)
        for bad in ("", "short", "!!!not-base64!!!"):
            with patch.dict("os.environ", {"TEST_KEY": bad}), self.assertRaises(ImproperlyConfigured):
                encryption_key("TEST_KEY")
