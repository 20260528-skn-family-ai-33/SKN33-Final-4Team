"""R04 카드 청구서 → 납부 예정(PaymentDue) → 홈 mustPay. 실행: python manage.py test finance.test_card_bill_dues"""
import uuid
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase

from budgets.home import home_data
from users.models import Consent, FinancialProfile

from . import codef
from .models import BankAccount, Card, CardBill, Connection, FinancialTransaction as Tx, PaymentDue, PaymentMatch
from .sync_cards import sync_card
from .test_card_sync import CARD_NO, ORG, approval, bill, card

TODAY = date(2026, 10, 28)  # 급여일 25일 → 이번 주기 10/25 ~ 11/24, 지난 주기 9/25 ~


class BillDueFixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email=f"bill-due-{uuid.uuid4().hex}@example.com")
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        self.connection = Connection.objects.create(user=self.user, business_type="card", organization_code=ORG,
                                                    status="connected")
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card",
                               document_version="test", document_hash="test-only")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="pay")
        self.cards = [card()]
        self.bills = {"202610": [], "202611": bill(due="20261105")}
        self.approvals = []
        for target, fake in (("finance.codef._request", self.response),
                             ("finance.codef.encrypt_rsa", lambda value, key: "encrypted"),
                             ("finance.classifier.predict", lambda names: [[] for _ in names]),
                             ("django.utils.timezone.localdate", lambda *a: TODAY)):
            started = patch(target, side_effect=fake)
            started.start()
            self.addCleanup(started.stop)

    def response(self, path, params):
        if path == codef.PATH_CARD_LIST:
            return deepcopy(self.cards)
        if path == codef.PATH_CARD_APPROVALS:
            return deepcopy(self.approvals)
        return deepcopy(self.bills[params["startDate"]])

    def sync(self):
        return sync_card(self.user, "cid", ORG, date(2026, 7, 1), TODAY, ("202610", "202611"),
                         connection_id=self.connection.pk)

    def must_pay(self):
        return [i for i in home_data(self.user, TODAY)["must_pay"] if i["type"] == "card_bill"]


class BillWithoutWithdrawalTest(BillDueFixture):
    def test_bill_before_withdrawal_shows_in_home(self):
        # 재현(R04): 11/5 결제 42만원 청구서만 있고 출금 없음 → 10/28 홈
        result = self.sync()
        self.assertEqual(result["new_transaction_count"], 0)  # 새 승인 거래가 없어도
        due = PaymentDue.objects.get(user=self.user, obligation_type="card_bill")
        self.assertEqual((due.due_date, due.confirmed_amount, due.payment_state), (date(2026, 11, 5), 420_000, "unpaid"))
        [item] = self.must_pay()
        self.assertEqual((item["amount"], item["due_date"], item["source_id"]),
                         (420_000, "2026-11-05", str(CardBill.objects.get(user=self.user).pk)))

    def due(self, **filters):
        return PaymentDue.objects.get(user=self.user, obligation_type="card_bill", **filters)

    def payment(self, amount=420_000, day=date(2026, 11, 5)):
        tx = Tx.objects.create(
            user=self.user, source_kind="bank", account=self.account, occurred_on=day, amount=amount, direction="out",
            source_description="현대카드", transaction_type="card_payment", confirmed_category_id="UNCLASSIFIED",
            confirmation_state="needs_review",
        )
        from . import pipeline
        pipeline.match_card_payments(self.user, Tx.objects.filter(pk=tx.pk))
        return tx

    def test_first_and_later_bills_in_home_window(self):
        self.bills["202610"] = bill(due="20261005")      # 지난 주기(9/25~) — 출금이 안 보이면 '미납 확인 필요'
        self.bills["202611"] = bill(due="20261105")
        self.bills["202609"] = bill(due="20260905")       # 지난 주기 전 — 만들지 않음
        sync_card(self.user, "cid", ORG, date(2026, 7, 1), TODAY, ("202609", "202610", "202611"),
                  connection_id=self.connection.pk)
        self.assertEqual(sorted(PaymentDue.objects.values_list("due_date", flat=True)), [date(2026, 10, 5), date(2026, 11, 5)])
        items = {i["due_date"]: i["payment_state"] for i in self.must_pay()}
        self.assertEqual(items, {"2026-10-05": "needs_review", "2026-11-05": "unpaid"})

    def test_repeat_sync_keeps_one_due_and_applies_amount_correction(self):
        self.sync()
        first = self.due()
        self.sync()
        self.bills["202611"] = bill(due="20261105", resTotalAmount="380,000")
        self.sync()
        first.refresh_from_db()
        self.assertEqual((PaymentDue.objects.count(), first.confirmed_amount), (1, 380_000))

    def test_due_date_correction_moves_the_same_due(self):
        # 재현(R04 보완 ①): 결제일 11/5 → 11/6 정정이 42만 원 두 줄(84만 원)로 보이던 문제
        self.sync()
        first = self.due()
        PaymentMatch.objects.create(user=self.user, due=first, transaction=self.payment(amount=1, day=date(2026, 10, 1)),
                                    matched_amount=100_000)  # 기존 부분 납부 기록
        self.bills["202611"] = bill(due="20261106")
        self.sync()
        self.assertEqual(CardBill.objects.filter(user=self.user).count(), 2)  # 청구서 행은 지우지 않음
        moved = self.due()
        new_bill = CardBill.objects.get(user=self.user, payment_due_date=date(2026, 11, 6))
        self.assertEqual((moved.pk, moved.bill_id, moved.due_date, moved.matches.count()),
                         (first.pk, new_bill.pk, date(2026, 11, 6), 1))
        self.assertEqual([(i["due_date"], i["amount"], i["source_id"]) for i in self.must_pay()],
                         [("2026-11-06", 320_000, str(new_bill.pk))])
        self.sync()  # 다시 불러와도 그대로
        self.assertEqual(PaymentDue.objects.count(), 1)
        self.bills["202611"] = bill(due="20261105")  # 정정이 되돌려지면 예정도 돌아간다
        self.sync()
        self.assertEqual((self.due().pk, self.due().due_date), (first.pk, date(2026, 11, 5)))
        self.sync()
        self.assertEqual(PaymentDue.objects.count(), 1)

    def test_corrected_bill_and_old_bill_reappearing_together(self):
        """11/5 수집 → 11/6 정정 → 다음 응답에 두 청구서가 함께 재등장 (occurrence_key 충돌 회귀)"""
        with patch("finance.card_payments.logger") as log:
            self.sync()
            first = self.due()
            PaymentMatch.objects.create(user=self.user, due=first, transaction=self.payment(amount=1, day=date(2026, 10, 1)),
                                        matched_amount=100_000)
            self.bills["202611"] = bill(due="20261106")
            self.sync()
            self.bills["202611"] = [bill(due="20261105"), bill(due="20261106")]
            for _ in range(2):  # 반복 조회
                self.sync()
                self.must_pay()
            log.error.assert_not_called()  # 맞추기가 조용히 실패하지 않음
        old_bill = CardBill.objects.get(user=self.user, payment_due_date=date(2026, 11, 5))
        new_bill = CardBill.objects.get(user=self.user, payment_due_date=date(2026, 11, 6))
        first.refresh_from_db()
        # 옮겨 간 예정은 ID · 납부 기록 그대로 11/6 청구서에, 키도 그 청구서 기준
        self.assertEqual((first.bill_id, first.occurrence_key, first.matches.count()),
                         (new_bill.pk, f"card_bill:{new_bill.pk}", 1))
        # 한 응답에 같이 왔으니 서로 다른 청구서 — 11/5 에도 예정이 생긴다 (정정 여부가 불확실하면 합치지 않는 규칙)
        again = PaymentDue.objects.get(bill=old_bill)
        self.assertEqual((again.occurrence_key, again.confirmed_amount), (f"card_bill:{old_bill.pk}", 420_000))
        self.assertEqual(PaymentDue.objects.count(), 2)

    def test_correction_round_trip_keeps_key_with_its_bill(self):
        with patch("finance.card_payments.logger") as log:
            self.sync()
            first = self.due()
            for due in ("20261106", "20261105", "20261106", "20261106"):  # 정정 → 되돌림 → 다시 정정 → 반복 조회
                self.bills["202611"] = bill(due=due)
                self.sync()
                first.refresh_from_db()
                self.assertEqual((PaymentDue.objects.count(), first.occurrence_key), (1, f"card_bill:{first.bill_id}"))
            log.error.assert_not_called()
        self.assertEqual(first.due_date, date(2026, 11, 6))

    def test_old_date_bill_is_not_matched_after_correction(self):
        self.sync()
        self.bills["202611"] = bill(due="20261106")
        self.sync()
        self.payment(day=date(2026, 11, 4))  # 예전 날짜(11/5) 근처 출금 → 옮겨 간 예정(11/6)에 붙고, 예전 청구서에 새 예정을 만들지 않음
        self.assertEqual(PaymentDue.objects.count(), 1)
        self.assertEqual(self.due().payment_state, "paid")

    def test_uncertain_date_changes_are_not_merged(self):
        # 같은 달 같은 구분 명세서가 한 응답에 함께 오면 서로 다른 청구서 — 하나가 빠져도 합치지 않음
        self.bills["202611"] = [bill(due="20261105"), bill(due="20261120", resTotalAmount="50,000")]
        self.sync()
        self.bills["202611"] = bill(due="20261120", resTotalAmount="50,000")
        self.sync()
        self.assertEqual(sorted(PaymentDue.objects.values_list("confirmed_amount", flat=True)), [50_000, 420_000])
        # 예전 하나가 빠지고 새 명세서가 여럿 나타나면 어느 것이 정정인지 몰라 옮기지 않음
        PaymentDue.objects.all().delete()
        CardBill.objects.all().delete()
        self.bills["202611"] = bill(due="20261105")
        self.sync()
        self.bills["202611"] = [bill(due="20261106"), bill(due="20261107", resTotalAmount="10,000")]
        self.sync()
        self.assertEqual(sorted(PaymentDue.objects.values_list("due_date", flat=True)),
                         [date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7)])

    def test_payment_marks_paid_and_existing_partial_is_kept(self):
        self.sync()
        self.payment()
        self.assertEqual(self.due().payment_state, "paid")
        self.assertEqual(self.must_pay(), [])
        # 기존 부분 납부 기록: 지우지 않고 남은 금액을 보인다
        due = self.due()
        PaymentMatch.objects.filter(due=due).update(matched_amount=100_000)
        self.sync()
        due.refresh_from_db()
        self.assertEqual((due.payment_state, due.paid_amount, due.matches.count()), ("partial", 100_000, 1))
        [item] = self.must_pay()
        self.assertEqual(item["amount"], 320_000)

    def test_collection_order_does_not_change_result(self):
        Card.objects.create(user=self.user, connection=self.connection, origin="provider", issuer_code=ORG,
                            source_card_key="pre", payment_account=self.account)
        results = []
        for order in ("bill_first", "withdrawal_first"):
            PaymentMatch.objects.all().delete()
            PaymentDue.objects.all().delete()
            Tx.objects.filter(source_kind="bank").delete()
            CardBill.objects.all().delete()
            if order == "bill_first":
                self.sync()
                self.payment()
            else:
                self.payment()
                self.sync()
            due = self.due()
            results.append((due.due_date, due.confirmed_amount, due.paid_amount, due.payment_state,
                            due.payment_account_id, due.matches.count()))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][3:], ("paid", self.account.pk, 1))

    def test_zero_negative_or_dateless_bills_make_no_due(self):
        for total, due in (("0", "20261105"), ("-5,000", "20261105"), ("420,000", "")):
            with self.subTest(total=total, due=due):
                CardBill.objects.all().delete()
                self.bills["202611"] = bill(due=due, resTotalAmount=total)
                self.sync()
                self.assertFalse(PaymentDue.objects.exists())
                self.assertEqual(self.must_pay(), [])

    def test_bill_corrected_to_zero_cancels_due_and_keeps_payment_records(self):
        self.sync()
        self.bills["202611"] = bill(due="20261105", resTotalAmount="0")
        self.sync()
        self.assertEqual(self.due().obligation_state, "canceled")
        self.assertEqual(self.must_pay(), [])
        self.bills["202611"] = bill(due="20261105")  # 다시 청구되면 같은 예정이 살아남
        self.sync()
        self.assertEqual((self.due().obligation_state, len(self.must_pay())), ("active", 1))
        # 재현(R04 보완 ②): 10만 원 납부 기록이 있는데 0원으로 정정되면 32만 원이 계속 보이던 문제
        due = self.due()
        PaymentMatch.objects.create(user=self.user, due=due, transaction=self.payment(amount=1, day=date(2026, 10, 1)),
                                    matched_amount=100_000)
        self.bills["202611"] = bill(due="20261105", resTotalAmount="0")
        self.sync()
        due.refresh_from_db()
        self.assertEqual((due.obligation_state, due.matches.count(), due.confirmed_amount), ("canceled", 1, 420_000))
        self.assertEqual(self.must_pay(), [])

    def test_statement_rules_card_loan_and_multiple_cards(self):
        self.cards = [card(), card(number="9490981234561111")]  # 같은 카드사 카드 두 장 — 청구서는 카드사 통합
        self.bills["202611"] = [bill(due="20261105", resBillType="일시불이용", resTotalAmount="300,000"),
                                bill(due="20261105", resBillType="카드론", resTotalAmount="500,000"),
                                bill(due="20261105", resBillType="할부이용", resTotalAmount="120,000")]
        self.sync()
        self.assertEqual(sorted(PaymentDue.objects.values_list("confirmed_amount", flat=True)), [120_000, 300_000])
        self.assertEqual(sum(i["amount"] for i in self.must_pay()), 420_000)
        PaymentDue.objects.all().delete()
        CardBill.objects.all().delete()
        self.bills["202611"] = [bill(due="20261105", resBillType=""), bill(due="20261105", resBillType="일시불이용")]
        self.sync()  # 빈 구분이 섞인 여러 장 — 어느 것을 낼지 모름 (매칭 · 선차감과 같이 보류)
        self.assertFalse(PaymentDue.objects.exists())

    def test_card_fixed_expense_is_not_a_separate_due(self):
        from .models import RecurringExpense
        self.sync()
        saved = Card.objects.filter(user=self.user).first()
        RecurringExpense.objects.create(user=self.user, name="넷플릭스", amount=17_000, payment_day=1, category_id="ETC",
                                        status="confirmed", source_type="manual", payment_card=saved)
        items = home_data(self.user, TODAY)["must_pay"]
        self.assertEqual([i["type"] for i in items], ["card_bill"])
        self.assertFalse(PaymentDue.objects.filter(obligation_type="recurring").exists())

    def test_payment_account_only_when_certain(self):
        other = BankAccount.objects.create(user=self.user, connection=self.account.connection, source_account_key="b")
        self.cards = [card(), card(number="9490981234561111")]
        self.sync()
        a, b = Card.objects.filter(user=self.user).order_by("pk")
        Card.objects.filter(pk=a.pk).update(payment_account=self.account)
        Card.objects.filter(pk=b.pk).update(payment_account=other)
        PaymentDue.objects.all().delete()
        self.sync()
        self.assertIsNone(self.due().payment_account_id)  # 카드마다 계좌가 다르면 고르지 않음
        Card.objects.filter(pk=b.pk).update(payment_account=self.account)
        PaymentDue.objects.all().delete()
        self.sync()
        self.assertEqual(self.due().payment_account_id, self.account.pk)

    def test_older_response_does_not_revert_due(self):
        from datetime import timedelta
        from django.utils import timezone as tz
        self.sync()
        row = CardBill.objects.get(user=self.user)
        future = tz.now() + timedelta(minutes=5)
        from .models import SyncRun
        SyncRun.objects.filter(card__user=self.user).update(started_at=future)
        CardBill.objects.filter(pk=row.pk).update(field_states={"codefStartedAt": future.isoformat()}, total_amount=450_000)
        self.sync()  # 450,000 을 저장한 뒤 시작한 조회가 더 늦은 것으로 기록됨 → 이 420,000 응답은 건너뜀
        self.bills["202611"] = bill(due="20261105")
        self.sync()
        self.assertEqual(self.due().confirmed_amount, 450_000)

    def test_saved_bill_without_due_is_filled_when_home_opens(self):
        CardBill.objects.create(user=self.user, connection=self.connection, issuer_code=ORG, statement_key="old",
                                scope_type="issuer", billing_month=date(2026, 11, 1), total_amount=420_000,
                                payment_due_date=date(2026, 11, 5), source_type="provider")
        self.assertFalse(PaymentDue.objects.exists())
        [item] = self.must_pay()
        self.assertEqual((item["amount"], item["due_date"]), (420_000, "2026-11-05"))
        self.must_pay()
        self.assertEqual(PaymentDue.objects.count(), 1)

    def test_prepayment_before_due_date_on_refresh(self):
        """결제일 전 분할 납부(선결제) 뒤 새로고침 (11/5 나은 확인 요청)"""
        self.sync()
        # ① 카드사가 남은 청구액으로 총액을 낮춰 주면 새로고침 때 그 금액으로 바뀐다
        self.bills["202611"] = bill(due="20261105", resTotalAmount="220,000")
        self.sync()
        self.assertEqual([i["amount"] for i in self.must_pay()], [220_000])
        # ② 카드사가 총액을 그대로 주고 일부만 출금되면 자동으로 잇지 않는다 (금액이 정확히 같을 때만 매칭, 분할 탐지 없음)
        self.bills["202611"] = bill(due="20261105")
        self.sync()
        self.payment(amount=200_000, day=date(2026, 10, 30))
        self.sync()
        self.assertFalse(PaymentMatch.objects.exists())
        self.assertEqual([i["amount"] for i in self.must_pay()], [420_000])

    def test_first_bill_deduction_is_unchanged_by_dues(self):
        from types import SimpleNamespace
        from django.utils import timezone as tz
        from datetime import datetime
        from .card_deduction import get_issuer_first_bill_deduction
        self.approvals = [approval()]
        self.sync()
        saved = Card.objects.get(user=self.user)
        Card.objects.filter(pk=saved.pk).update(first_connected_at=tz.make_aware(datetime(2026, 10, 1)))
        cycle = SimpleNamespace(user_id=self.user.pk, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))

        def deduction():
            r = get_issuer_first_bill_deduction(self.user, ORG, cycle, card_ids=[saved.pk], as_of=TODAY,
                                                bill_history_complete=True, spending_history_complete=True)
            return r.amount, r.reason, r.total_amount

        with_due = deduction()
        self.assertTrue(PaymentDue.objects.exists())
        PaymentDue.objects.all().delete()
        self.assertEqual(with_due, deduction())
        self.assertEqual(with_due[2], 420_000)


class BillDueConcurrencyTest(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        BillDueFixture.setUp(self)

    response, sync, must_pay = BillDueFixture.response, BillDueFixture.sync, BillDueFixture.must_pay

    def test_concurrent_sync_and_home_make_one_due(self):
        from .test_concurrency import run_together

        def safe_sync():
            try:
                self.sync()
                return "saved"
            except Exception as exc:  # 더 늦게 시작한 조회가 먼저 저장되면 이전 응답은 막힌다
                return type(exc).__name__

        def home():
            return len(self.must_pay())

        CardBill.objects.create(user=self.user, connection=self.connection, issuer_code=ORG, statement_key="old",
                                scope_type="issuer", billing_month=date(2026, 11, 1), total_amount=100_000,
                                withdrawal_due_date=date(2026, 11, 10), source_type="provider")
        run_together(safe_sync, safe_sync, home)
        self.assertEqual(PaymentDue.objects.filter(user=self.user).count(), CardBill.objects.filter(user=self.user).count())
        self.assertEqual(PaymentDue.objects.filter(user=self.user).values("bill").distinct().count(), 2)
