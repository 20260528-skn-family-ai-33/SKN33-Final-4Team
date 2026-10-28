"""카드 확인 명령의 요청 필드 회귀 검사. 실제 CODEF 조회·DB 접근 없음."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from finance.management.commands import codef_card_check as command

APPROVAL = "/v1/kr/card/p/account/approval-list"
BILLING = "/v1/kr/card/p/account/billing-list"


@override_settings(CODEF={"CONNECTED_ID": "test-connected-id", "PUBLIC_KEY": "test-key"})
class CardCheckTest(SimpleTestCase):
    def run_check(self, *args, error=None):
        sent, references = [], []

        def request(path, params):
            sent.append((path, dict(params)))
            references.append(params)
            if error:
                raise error
            return []

        self.sent, self.references = sent, references
        with patch.object(command, "_card_secret", side_effect=["1111-2222-3333-4444", "test-password"]), \
                patch.object(command, "encrypt_rsa", return_value="encrypted-password") as encrypt, \
                patch.object(command.codef, "_request", side_effect=request):
            call_command(command.Command(), *args, stdout=StringIO())
        return sent[0][1], encrypt

    def test_approval_uses_login_card_number_for_hyundai_and_hana(self):
        for org in ("0302", "0320"):
            with self.subTest(org=org):
                params, encrypt = self.run_check("--path", APPROVAL, "--org", org, "--card-auth")
                self.assertEqual(params["loginCardNo"], "1111222233334444")
                self.assertNotIn("cardNo", params)
                self.assertEqual(params["cardPassword"], "encrypted-password")
                encrypt.assert_called_once_with("test-password", "test-key")

    def test_approval_keeps_query_card_separate_from_auth_card(self):
        params, _ = self.run_check("--path", APPROVAL, "--card-auth",
                                  "--extra", "inquiryType=0", "--extra", "cardNo=query-card")
        self.assertEqual(params["loginCardNo"], "1111222233334444")
        self.assertEqual(params["cardNo"], "query-card")
        self.assertEqual(params["inquiryType"], "0")

    def test_card_list_and_billing_keep_card_number_auth(self):
        for path in (command.CARD_LIST, BILLING):
            with self.subTest(path=path):
                params, _ = self.run_check("--path", path, "--card-auth")
                self.assertEqual(params["cardNo"], "1111222233334444")
                self.assertNotIn("loginCardNo", params)

    def test_without_card_auth_sends_no_auth_fields(self):
        params, encrypt = self.run_check("--path", APPROVAL, "--start", "20260901", "--end", "20260930")
        self.assertEqual(params["startDate"], "20260901")
        self.assertEqual(params["endDate"], "20260930")
        self.assertFalse({"loginCardNo", "cardNo", "cardPassword"} & params.keys())
        encrypt.assert_not_called()

    def test_billing_preserves_month_format(self):
        params, _ = self.run_check("--path", BILLING, "--start", "202609")
        self.assertEqual(params["startDate"], "202609")
        self.assertNotIn("endDate", params)

    def test_auth_fields_removed_after_success_or_failure(self):
        for error in (None, command.codef.CodefError("TEST", "failed")):
            with self.subTest(error=error):
                if error:
                    with self.assertRaises(CommandError):
                        self.run_check("--path", APPROVAL, "--card-auth", error=error)
                else:
                    self.run_check("--path", APPROVAL, "--card-auth")
                self.assertFalse({"loginCardNo", "cardNo", "cardPassword"} & self.references[0].keys())
