"""고정지출 결제 연결 (⑥, budgets/due_match.py) 테스트. 실행: backend 폴더에서 python manage.py test budgets.test_due_match
TODAY 10/21 · 급여일 25일 → 이번 주기 9/25 ~ 10/24, 지난 주기 8/25 ~ 9/24"""
import json
from datetime import date, datetime
from pathlib import Path
from runpy import run_path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.apps import apps
from django.test import TestCase

from finance.api import listed_transactions, transaction_item
from finance.models import Card, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense
from finance.pipeline import process_new_transactions
from finance.spending import spending_summary

from .api import match_withdrawal
from .test_home import TODAY, HomeFixture, remember


class AccountDueTest(HomeFixture):
    """⑥-1 계좌 고정지출 → 납부 예정 연결 (홈에서 옮김, 규칙 그대로)"""

    def setUp(self):
        super().setUp()
        self.rent = self.make_expense(payment_day=10)  # 10/10 회차

    def withdraw(self, amount=700_000, label="월세", on=date(2026, 10, 10)):
        return self.make_tx(on, amount, direction="out", source_description=label)

    def due(self):
        return PaymentDue.objects.get(recurring=self.rent, due_date=date(2026, 10, 10))

    def test_remembered_withdrawal_links_and_paid(self):
        remember(self.rent)
        tx = self.withdraw()
        self.home()
        self.assertEqual((self.due().payment_state, list(self.due().matches.values_list("transaction_id", flat=True))),
                         ("paid", [tx.pk]))

    def test_first_seen_withdrawal_is_not_linked(self):
        self.withdraw(label="카카오페이")
        self.home()
        self.assertFalse(self.due().matches.exists())
        self.assertNotEqual(self.due().payment_state, "paid")

    def test_pipeline_links_without_opening_home(self):
        remember(self.rent)
        tx = self.withdraw()
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            process_new_transactions(self.user, [tx.pk])
        self.assertEqual(self.due().payment_state, "paid")

    def test_home_pipeline_home_links_once(self):
        remember(self.rent)
        tx = self.withdraw()
        self.home()
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            process_new_transactions(self.user, [tx.pk])
        self.home()
        due = self.due()
        self.assertEqual((PaymentMatch.objects.filter(transaction=tx).count(), due.paid_amount), (1, 700_000))
        self.rent.refresh_from_db()
        self.assertEqual(self.rent.evidence.count(str(tx.pk)), 1)


def paid_this_cycle(case, expense):
    """고정지출 목록의 paidThisCycle (11/4)"""
    with patch("django.utils.timezone.localdate", return_value=TODAY):
        items = case.client.get("/api/budgets/fixed/").json()["items"]
    return next(i for i in items if i["recurringId"] == str(expense.pk))["paidThisCycle"]


class PaidThisCycleTest(HomeFixture):
    """목록 paidThisCycle: 이번 주기에 낸 거래만"""

    def test_account_paid_due_shows_withdrawal(self):
        rent = self.make_expense(payment_day=10)
        remember(rent)
        tx = self.make_tx(date(2026, 10, 10), 700_000, direction="out", source_description="월세")
        self.assertIsNone(paid_this_cycle(self, rent))  # 홈을 열기 전 = 아직 연결 전
        self.home()
        self.assertEqual(paid_this_cycle(self, rent)["transactionId"], str(tx.pk))

    def test_unpaid_account_is_null(self):
        rent = self.make_expense(payment_day=10)
        self.make_tx(date(2026, 10, 10), 700_000, direction="out", source_description="카카오페이")  # 처음 보는 출금 → 묻기만
        self.home()
        self.assertIsNone(paid_this_cycle(self, rent))

    def test_card_linked_this_cycle(self):
        card = Card.objects.create(user=self.user, name="신한", masked_number="****1234", origin="manual")
        old = FinancialTransaction.objects.create(
            user=self.user, source_kind="card", card=card, occurred_on=date(2026, 8, 8), amount=12_000, direction="out",
            transaction_type="spending", merchant_name="넷플릭스", confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        netflix = self.make_expense(name="넷플릭스", amount=12_000, payment_day=8, category_id="CULTURE_LEISURE",
                                    payment_account=None, payment_card=card, evidence=[str(old.pk)])
        self.home()
        self.assertIsNone(paid_this_cycle(self, netflix))  # 지난 기록만 있음
        tx = FinancialTransaction.objects.create(
            user=self.user, source_kind="card", card=card, occurred_on=date(2026, 10, 8), amount=12_000, direction="out",
            transaction_type="spending", merchant_name="넷플릭스", confirmed_category_id="CULTURE_LEISURE", confirmation_state="confirmed")
        self.home()
        self.assertEqual(paid_this_cycle(self, netflix)["transactionId"], str(tx.pk))


class NinetyPercentTest(HomeFixture):
    """⑥-3 연결된 금액이 예정 금액의 90%(올림) 이상이면 paid"""

    def setUp(self):
        super().setUp()
        self.telecom = self.make_expense(name="통신비", amount=15_000, payment_day=10)
        remember(self.telecom, label="SKT")

    def test_close_amount_is_paid_and_leaves_must_pay(self):
        self.make_tx(date(2026, 10, 10), 14_900, direction="out", source_description="SKT")
        must_pay = self.home()["must_pay"]
        due = PaymentDue.objects.get(recurring=self.telecom, due_date=date(2026, 10, 10))
        self.assertEqual(due.payment_state, "paid")
        self.assertNotIn(str(due.pk), [m["due_id"] for m in must_pay])

    def saved_partial(self, amount):
        """90% 규칙 전에 연결돼 partial 로 저장된 회차"""
        self.home()
        due = PaymentDue.objects.get(recurring=self.telecom, due_date=date(2026, 10, 10))
        tx = self.make_tx(date(2026, 10, 10), amount, direction="out", source_description="SKT")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=amount)
        PaymentDue.objects.filter(pk=due.pk).update(paid_amount=amount, payment_state="partial")
        return due

    def test_old_partial_close_amount_becomes_paid(self):
        due = self.saved_partial(14_900)
        must_pay = self.home()["must_pay"]
        due.refresh_from_db()
        self.assertEqual(due.payment_state, "paid")
        self.assertNotIn(str(due.pk), [m["due_id"] for m in must_pay])

    def test_old_partial_much_less_stays_partial(self):
        due = self.saved_partial(10_000)
        must_pay = {m["due_id"]: m for m in self.home()["must_pay"]}
        due.refresh_from_db()
        self.assertEqual((due.payment_state, must_pay[str(due.pk)]["amount"]), ("partial", 5_000))

    def test_much_less_is_partial(self):
        self.home()
        due = PaymentDue.objects.get(recurring=self.telecom, due_date=date(2026, 10, 10))
        match_withdrawal(due, self.make_tx(date(2026, 10, 10), 10_000, direction="out", source_description="SKT"), TODAY)
        due.refresh_from_db()
        self.assertEqual((due.payment_state, due.paid_amount), ("partial", 10_000))


class CardFixedTest(HomeFixture):
    """⑥-2 카드 고정지출 → 기억한 가맹점 · 같은 카드 · ±10% 결제를 고정비로 (납부 예정 없음)"""

    def setUp(self):
        super().setUp()
        self.card = Card.objects.create(user=self.user, name="신한", masked_number="****1234", origin="manual")
        self.netflix = self.make_expense(name="넷플릭스", amount=12_000, payment_day=8, category_id="CULTURE_LEISURE",
                                         payment_account=None, payment_card=self.card)
        self.first = self.pay(date(2026, 8, 8))           # 사용자가 [맞아요]로 고른 첫 결제 (7/25 주기)
        self.netflix.evidence = [str(self.first.pk)]
        self.netflix.save(update_fields=["evidence"])

    def pay(self, on, amount=12_000, name="넷플릭스"):
        return FinancialTransaction.objects.create(
            user=self.user, source_kind="card", card=self.card, occurred_on=on, amount=amount, direction="out",
            transaction_type="spending", merchant_name=name, confirmed_category_id="CULTURE_LEISURE",
            confirmation_state="confirmed",
        )

    def item(self, tx):
        return transaction_item(listed_transactions(self.user).get(pk=tx.pk))

    def linked(self):
        self.netflix.refresh_from_db()
        return set(self.netflix.evidence) - {str(self.first.pk)}

    def test_next_payment_is_fixed_and_out_of_living(self):
        tx = self.pay(date(2026, 10, 8))
        self.home()
        self.assertEqual(self.linked(), {str(tx.pk)})
        item = self.item(tx)
        self.assertEqual((item["funding"], item["fixed_expense_id"]), ("fixed", self.netflix.pk))
        rows = listed_transactions(self.user).filter(occurred_on__gte=date(2026, 9, 25))
        self.assertEqual(spending_summary(map(transaction_item, rows), living_only=True)["spent_total"], 0)
        self.assertFalse(PaymentDue.objects.filter(recurring=self.netflix).exists())  # 카드는 납부 예정 없음

    def cancel_linked(self, canceled=12_000):
        tx = self.pay(date(2026, 10, 8))
        self.home()
        tx.is_canceled, tx.canceled_amount = True, canceled
        tx.save(update_fields=["is_canceled", "canceled_amount"])
        self.home()
        return tx

    def test_full_cancellation_allows_repayment(self):
        old = self.cancel_linked()
        self.assertIsNone(paid_this_cycle(self, self.netflix))
        new = self.pay(date(2026, 10, 9))
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            process_new_transactions(self.user, [new.pk])
        self.home()
        self.assertEqual(self.linked(), {str(old.pk), str(new.pk)})
        self.assertEqual(paid_this_cycle(self, self.netflix)["transactionId"], str(new.pk))
        self.assertEqual(self.item(new)["funding"], "fixed")
        self.assertFalse(PaymentMatch.objects.filter(transaction__in=[old, new]).exists())
        rows = listed_transactions(self.user).filter(occurred_on__gte=date(2026, 9, 25))
        self.assertEqual(spending_summary(map(transaction_item, rows), living_only=True)["spent_total"], 0)

    def test_full_cancellation_allows_renamed_suggestion(self):
        old = self.cancel_linked()
        new = self.pay(date(2026, 10, 9), name="NFX 정기결제")
        self.home()
        self.assertEqual(self.linked(), {str(old.pk)})
        self.assertIsNone(paid_this_cycle(self, self.netflix))
        self.assertEqual(self.suggestion()["candidates"][0]["transaction_id"], str(new.pk))

    def test_partial_cancellation_still_paid(self):
        tx = self.cancel_linked(2_000)
        self.assertEqual(paid_this_cycle(self, self.netflix)["transactionId"], str(tx.pk))
        self.assertIsNone(self.suggestion())
        self.pay(date(2026, 10, 9))
        self.home()
        self.assertEqual(self.linked(), {str(tx.pk)})

    def test_full_cancellation_not_displayed_as_latest_evidence(self):
        from .api import fixed_json
        self.cancel_linked()
        self.netflix.refresh_from_db()
        self.assertEqual(fixed_json(self.netflix)["evidence"]["transaction_id"], str(self.first.pk))
        self.first.is_canceled = True
        self.first.canceled_amount = None
        self.first.save()
        self.assertIsNone(fixed_json(self.netflix)["evidence"])

    def test_previous_cycle_payment_is_linked_too(self):
        tx = self.pay(date(2026, 9, 8))  # 지난 주기 8/25 ~ 9/24
        self.home()
        self.assertEqual(self.linked(), {str(tx.pk)})

    def test_without_evidence_not_linked(self):
        self.netflix.evidence = []
        self.netflix.save(update_fields=["evidence"])
        tx = self.pay(date(2026, 10, 8))
        self.home()
        self.netflix.refresh_from_db()
        self.assertEqual(self.netflix.evidence, [])
        self.assertEqual(self.item(tx)["funding"], "living")

    def test_amount_outside_ten_percent_not_linked(self):
        self.pay(date(2026, 10, 8), amount=13_300)  # 110% = 13,200
        self.home()
        self.assertEqual(self.linked(), set())

    def test_two_candidates_in_cycle_not_linked(self):
        self.pay(date(2026, 10, 8))
        self.pay(date(2026, 10, 15))
        self.home()
        self.assertEqual(self.linked(), set())

    def test_other_merchant_not_linked(self):
        self.pay(date(2026, 10, 8), name="왓챠")
        self.home()
        self.assertEqual(self.linked(), set())

    def test_home_pipeline_home_links_once(self):
        tx = self.pay(date(2026, 10, 8))
        self.home()
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            process_new_transactions(self.user, [tx.pk])
        self.home()
        self.netflix.refresh_from_db()
        self.assertEqual((self.netflix.evidence.count(str(tx.pk)), len(self.netflix.evidence)), (1, 2))

    def test_other_expense_transaction_untouched(self):
        tx = self.pay(date(2026, 10, 8))
        other = self.make_expense(name="넷플릭스 가족", amount=12_000, payment_day=8, category_id="CULTURE_LEISURE",
                                  payment_account=None, payment_card=self.card, evidence=[str(tx.pk)])
        self.home()
        self.assertEqual(self.linked(), set())
        self.assertEqual(self.item(tx)["fixed_expense_id"], other.pk)

    def test_stopped_expense_keeps_fixed_like_account(self):
        tx = self.pay(date(2026, 10, 8))
        self.home()
        RecurringExpense.objects.filter(pk=self.netflix.pk).update(status="stopped")
        self.assertEqual(self.item(tx)["funding"], "fixed")
        RecurringExpense.objects.filter(pk=self.netflix.pk).update(status="suggested")  # 후보 근거는 고정비 아님
        self.assertEqual(self.item(tx)["funding"], "living")

    def test_evidence_id_compared_like_id_set(self):
        tx = self.pay(date(2026, 10, 8), name="왓챠")  # 자동 연결 대상이 아님 → evidence 에 직접 넣음
        self.netflix.evidence = [str(self.first.pk), tx.pk.hex.upper()]  # 대시 없음 · 대문자
        self.netflix.save(update_fields=["evidence"])
        self.assertEqual(self.item(tx)["fixed_expense_id"], self.netflix.pk)

    # ---- 1. 카드 evidenceSuggestion: 근거 거래가 있어도 이번 주기에 연결된 결제가 없으면 묻는다 ----
    def suggestion(self):
        from .api import evidence_suggestion
        from .models import BudgetCycle
        self.netflix.refresh_from_db()
        return evidence_suggestion(self.netflix, BudgetCycle.objects.get(user=self.user, starts_on=date(2026, 9, 25)), TODAY)

    def test_renamed_payment_is_suggested(self):
        tx = self.pay(date(2026, 10, 8), name="NFX 정기결제")  # 가게 이름이 달라 자동 연결 안 됨
        self.home()
        self.assertEqual(self.linked(), set())
        found = self.suggestion()
        self.assertEqual((found["count"], found["candidates"][0]["transaction_id"]), (1, str(tx.pk)))

    def test_auto_linked_month_has_no_suggestion(self):
        self.pay(date(2026, 10, 8))
        self.home()
        self.assertIsNone(self.suggestion())

    # ---- 3. 근거 비우기 · 결제 수단 바꾸기: 지난 주기는 고정비로 남고, 비운 가게는 기억에서 빠짐 ----
    def patch_fixed(self, body):
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            res = self.client.patch(f"/api/budgets/fixed/{self.netflix.pk}/", body, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_clear_keeps_past_fixed_and_forgets_merchant(self):
        last, this = self.pay(date(2026, 9, 8)), self.pay(date(2026, 10, 8))
        self.home()
        self.assertEqual(self.linked(), {str(last.pk), str(this.pk)})
        body = self.patch_fixed({"evidenceTransactionId": None})
        self.assertIsNone(body["evidence"])  # 화면의 근거 거래는 비워짐
        self.assertEqual([self.item(t)["funding"] for t in (self.first, last, this)], ["fixed", "fixed", "living"])
        upcoming = self.pay(date(2026, 11, 8))
        self.home(date(2026, 11, 21))  # 다음 주기: 같은 가게 결제라도 자동 연결 안 함
        self.assertEqual(self.item(upcoming)["funding"], "living")
        self.assertEqual(self.item(this)["funding"], "living")

    def test_pick_again_after_clear_remembers(self):
        self.patch_fixed({"evidenceTransactionId": None})
        self.patch_fixed({"evidenceTransactionId": str(self.first.pk)})  # [맞아요]로 다시 고름 → 다시 기억
        tx = self.pay(date(2026, 10, 8))
        self.home()
        self.assertEqual(self.linked(), {str(tx.pk)})

    def test_change_to_account_keeps_card_payments_fixed(self):
        last = self.pay(date(2026, 9, 8))
        self.home()
        self.patch_fixed({"paymentAccountId": str(self.account.pk), "paymentCardId": None})
        self.assertEqual([self.item(t)["funding"] for t in (self.first, last)], ["fixed", "fixed"])


ROOT = Path(__file__).resolve().parents[2]
ALPHA = ROOT / "local_workspace/scripts/alpha_test/seed.py"
ANSWERS = ROOT / "local_workspace/reports/alpha_test/answers.json"


class AlphaPersonaTest(TestCase):
    """페르소나 시드(alpha.home) — 카드 구독의 고정비 표시를 시드의 SpendingAllocation 대신 ⑥-2 자동 연결로 만든 뒤
    주기(급여일 1일 = 달력월)마다 홈을 열어, 생활비 사용액 · 고정비가 정답표(living_spent · fixed)와 같은지 본다"""

    @classmethod
    def setUpTestData(cls):
        seed = run_path(str(ALPHA))
        rows, *_ = seed["plan"](datetime(2026, 11, 5, 12, tzinfo=ZoneInfo("Asia/Seoul")))
        models = {m.__name__: m for app in ("users", "finance", "budgets") for m in apps.get_app_config(app).get_models()}
        for name, pk, fields in rows:  # seed.apply_plan 과 같은 순서 (fixture DB 확인 guard 만 뺌)
            models[name](pk=pk, **fields).save(force_insert=True)
        uid = seed["uid"]
        cls.user = models["User"].objects.get(pk=uid("home", "user"))
        subs = [r for r in seed["load"]()[0] if r["role"] in ("ott", "music")]
        sub_ids = [uid("home", "tx", r["source_key"]) for r in subs]
        # 실제 흐름에는 SpendingAllocation 행이 없다 → 지우고, 7월 첫 결제를 사용자가 고른 근거 거래로 둔다
        models["SpendingAllocation"].objects.filter(transaction_id__in=sub_ids).delete()
        for role in ("ott", "music"):
            july = next(r for r in subs if r["role"] == role and r["occurred_on"].startswith("2026-07"))
            RecurringExpense.objects.filter(pk=uid("home", "recurring", role)).update(
                evidence=[str(uid("home", "tx", july["source_key"]))])
        cls.answers = json.loads(ANSWERS.read_text(encoding="utf-8"))["months"]

    def test_living_spent_and_fixed_match_answers(self):
        from .home import home_data
        from .overview import budget_data
        for month in (8, 9, 10):
            end = date(2026, month + 1, 1)
            today = date.fromordinal(end.toordinal() - 1)
            home_data(self.user, today)  # 홈을 열면 지난 · 이번 주기 카드 구독이 고정비로 연결됨
            rows = listed_transactions(self.user).filter(occurred_on__gte=date(2026, month, 1), occurred_on__lt=end)
            items = [transaction_item(t) for t in rows]
            fixed = sum(i["net_amount"] for i in items if i["transaction_type"] == "spending" and i["funding"] == "fixed")
            expected = self.answers[f"2026-{month:02d}"]["before"]
            with self.subTest(month=month):
                self.assertEqual(budget_data(self.user, today)["living"]["spent"], expected["living_spent"])
                self.assertEqual(fixed, expected["fixed"])
