"""R11 보완 (11/06): 11/5 전 규칙이 판단 표시 없이 own_transfer 로 바꾼 기록 — DB 를 고치지 않고 '자동 분류'로 읽어 사용자가 정정.
점검 명령 own_transfer_legacy_check 는 읽기 전용."""
import json
from io import StringIO

from django.core.management import call_command

from .models import BankAccount, FinancialTransaction as Tx, TransactionLink
from .tests import URL, FinanceData
from .transfer_rules import OWN_REVIEW


class LegacyOwnTransferTest(FinanceData):
    def setUp(self):
        self.client.force_login(self.user)
        self.savings = BankAccount.objects.create(user=self.user, connection=self.account.connection,
                                                  source_account_key="acc-2", account_type="deposit")

    def legacy_pair(self, amount=300_000):
        """예전 규칙: 양쪽 own_transfer + 연결, 판단 표시 없음"""
        common = dict(transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED",
                      confirmation_state="needs_review", amount=amount, occurred_on=self.today)
        out = self.tx(direction="out", **common)
        income = self.tx(direction="in", account=self.savings, **common)
        TransactionLink.objects.create(user=self.user, from_transaction=out, to_transaction=income,
                                       relation_type="OWN_TRANSFER", matched_amount=amount, confirmation_state="suggested")
        return out, income

    def listed(self):
        return {i["transactionId"]: i for i in self.client.get(URL).json()["items"]}

    def test_legacy_withdrawal_is_listed_as_auto_and_can_be_unlinked(self):
        out, income = self.legacy_pair()
        unlinked = self.tx(direction="out", transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED",
                           confirmation_state="needs_review", occurred_on=self.today, amount=7_000)
        rows = self.listed()
        self.assertEqual(rows[str(out.pk)]["ownTransferReview"], "auto")
        self.assertNotIn(str(income.pk), rows)      # 입금은 출금 한 줄로
        self.assertNotIn(str(unlinked.pk), rows)    # 연결 없는 것은 넓히지 않음 (점검 대상)
        before = Tx.objects.get(pk=out.pk).field_states
        self.assertNotIn(OWN_REVIEW, before)        # 읽기만으로 DB 를 바꾸지 않음
        res = self.client.patch(f"{URL}{out.pk}/", data=json.dumps({"ownTransfer": False}), content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        out.refresh_from_db()
        income.refresh_from_db()
        self.assertEqual((out.transaction_type, out.field_states[OWN_REVIEW]), ("transfer", "rejected"))
        self.assertEqual((income.transaction_type, income.field_states[OWN_REVIEW]), ("income", "rejected"))
        self.assertFalse(TransactionLink.objects.exists())

    def test_legacy_withdrawal_can_be_confirmed(self):
        out, _ = self.legacy_pair()
        res = self.client.patch(f"{URL}{out.pk}/", data=json.dumps({"ownTransfer": True}), content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        out.refresh_from_db()
        self.assertEqual((out.transaction_type, out.field_states[OWN_REVIEW]), ("own_transfer", "confirmed"))

    def test_check_command_reports_without_changing_data(self):
        out, income = self.legacy_pair()
        self.tx(direction="out", transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED",
                confirmation_state="needs_review", amount=7_000)
        marked = self.tx(direction="out", transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED",
                         confirmation_state="needs_review", field_states={OWN_REVIEW: "auto"})
        stamps = dict(Tx.objects.values_list("pk", "updated_at"))
        buffer = StringIO()
        call_command("own_transfer_legacy_check", "--ids", stdout=buffer)
        text = buffer.getvalue()
        self.assertIn("linked_in 1, linked_out 1, unlinked 1", text)
        self.assertIn(str(out.pk), text)
        self.assertNotIn(str(marked.pk), text)       # 표시가 있는 새 기록은 대상 아님
        self.assertEqual(dict(Tx.objects.values_list("pk", "updated_at")), stamps)
