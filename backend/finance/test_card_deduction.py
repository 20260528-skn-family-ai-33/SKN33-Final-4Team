"""첫 카드 청구서 선차감 테스트(Q · C-17)."""
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from budgets.models import BudgetCycle
from users.models import Consent

from .api_connect import ConsentRequired
from .card_deduction import calculate_first_bill_deduction, get_issuer_first_bill_deduction
from .models import BankAccount, Card, CardBill, Connection, FinancialTransaction, TransactionLink


class FirstBillDeductionTests(SimpleTestCase):
    def calculate(self, total=420_000, spending=120_000, first=True):
        return calculate_first_bill_deduction(
            is_first_bill=first, total_amount=total, cycle_card_spending=spending,
        )

    def test_c17_example_without_bill_items(self):
        # 상세 항목 없이 계산
        self.assertEqual(self.calculate(), 300_000)

    def test_spending_equal_to_bill_deducts_nothing(self):
        self.assertEqual(self.calculate(spending=420_000), 0)

    def test_spending_above_bill_does_not_add_money_to_budget(self):
        self.assertEqual(self.calculate(spending=500_000), 0)

    def test_confirmed_no_spending_reserves_entire_bill(self):
        self.assertEqual(self.calculate(spending=0), 420_000)

    def test_confirmed_zero_bill(self):
        self.assertEqual(self.calculate(total=0, spending=0), 0)
        self.assertEqual(self.calculate(total=0), 0)

    def test_second_and_later_bills_are_not_deducted_again(self):
        self.assertEqual(self.calculate(first=False), 0)
        self.assertEqual(self.calculate(first=False, total=900_000, spending=0), 0)

    def test_later_bill_does_not_require_unused_amounts(self):
        self.assertEqual(self.calculate(first=False, total=None, spending=None), 0)

    def test_unknown_first_bill_is_not_assumed_first_or_later(self):
        self.assertIsNone(self.calculate(first=None))

    def test_missing_amounts_are_not_confirmed_zero(self):
        for total, spending in ((None, 120_000), (420_000, None), (None, None), (0, None)):
            with self.subTest(total=total, spending=spending):
                self.assertIsNone(self.calculate(total=total, spending=spending))

    def test_recalculation_does_not_consume_first_bill(self):
        self.assertEqual(self.calculate(), 300_000)
        self.assertEqual(self.calculate(), 300_000)
        # 갱신된 금액으로 재계산
        self.assertEqual(self.calculate(total=450_000), 330_000)

    def test_won_precision_is_preserved(self):
        self.assertEqual(self.calculate(total=420_001, spending=120_002), 299_999)
        # 큰 정수의 정밀도 유지
        self.assertEqual(self.calculate(total=9_007_199_254_740_993, spending=1), 9_007_199_254_740_992)

    def test_rejects_negative_amounts_instead_of_guessing_adjustments(self):
        for total, spending in ((-1, 0), (0, -1)):
            with self.subTest(total=total, spending=spending):
                with self.assertRaises(ValueError):
                    self.calculate(total=total, spending=spending)

    def test_rejects_coercion_or_rounding_of_money(self):
        for value in (True, False, "120000", 120_000.0, 1.5, Decimal("120000")):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.calculate(total=value)
                with self.assertRaises(TypeError):
                    self.calculate(spending=value)

    def test_first_bill_flag_must_be_explicit_boolean_or_unknown(self):
        for value in (0, 1, "false", "true", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.calculate(first=value)
        with self.assertRaises(TypeError):
            calculate_first_bill_deduction(total_amount=420_000, cycle_card_spending=120_000)


class StoredFirstBillDeductionTests(TestCase):
    """카드사 단위 첫 청구서 (10/29 나은): CODEF 청구서는 명세서 단위 카드사 통합(issuer)이다."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="deduction@example.invalid")
        self.other = get_user_model().objects.create_user(email="other-deduction@example.invalid")
        self.connection = Connection.objects.create(
            user=self.user, business_type="card", organization_code="0306", status="connected",
        )
        # 한국 시간 10/10 00시
        self.connected_at = datetime(2026, 10, 9, 15, tzinfo=dt_timezone.utc)
        self.card = self.make_card()
        self.cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=date(2026, 10, 1), ends_before=date(2026, 11, 1),
        )
        self.consent = Consent.objects.create(
            user=self.user, purpose="finance_inquiry", product_scope="card",
            document_version="v1", document_hash="test",
        )
        self.first = self.bill(date(2026, 10, 25), items_state="complete")
        # 11/06: 첫 청구서 상세 = 주기 전(9/20) 300,000 + 이번 주기(10/15) 120,000 — tx() 기본값(10/15 · 120,000)과 같은 지출
        self.item(self.first, date(2026, 9, 20), 300_000)
        self.item(self.first, date(2026, 10, 15), 120_000)
        self.bill(date(2026, 9, 25), amount=999_999)
        self.bill(date(2026, 11, 25), amount=800_000)
        external = patch("finance.codef._request", side_effect=AssertionError("계산 중 CODEF 호출 금지"))
        external.start()
        self.addCleanup(external.stop)

    def make_card(self, **overrides):
        fields = dict(user=self.user, connection=self.connection, origin="provider",
                      issuer_code="0306", first_connected_at=self.connected_at, is_selected=True)
        fields.update(overrides)
        return Card.objects.create(**fields)

    def bill(self, due, *, amount=420_000, bill_type=None, **overrides):
        fields = dict(user=self.user, connection=self.connection, card=None,
                      issuer_code="0306", billing_month=(due or date(2026, 10, 1)).replace(day=1),
                      total_amount=amount, source_type="provider", scope_type="issuer", withdrawal_due_date=due,
                      field_states={"codefBillType": bill_type} if bill_type is not None else {})
        fields.update(overrides)
        return CardBill.objects.create(**fields)

    def item(self, bill, purchased_on, amount, billed=None, **fields):
        from .models import CardBillItem
        return CardBillItem.objects.create(user=self.user, bill=bill, item_type=fields.pop("item_type", "one_time"),
                                           purchased_on=purchased_on, original_purchase_amount=amount,
                                           billed_amount=amount if billed is None else billed, **fields)

    def tx(self, day=date(2026, 10, 15), amount=120_000, **overrides):
        fields = dict(user=self.user, card=self.card, source_kind="card", occurred_on=day,
                      amount=amount, direction="out", transaction_type="spending",
                      confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        fields.update(overrides)
        return FinancialTransaction.objects.create(**fields)

    def result(self, **overrides):
        fields = dict(user=self.user, issuer_code="0306", card_ids=[self.card.pk], cycle=self.cycle,
                      bill_history_complete=True, spending_history_complete=True, as_of=date(2026, 10, 20))
        fields.update(overrides)
        return get_issuer_first_bill_deduction(**fields)

    def test_selects_first_due_after_connection_not_oldest_imported_bill(self):
        self.tx()
        result = self.result()
        self.assertEqual(result.bill_ids, (self.first.pk,))
        self.assertEqual((result.total_amount, result.cycle_card_spending, result.amount), (420_000, 120_000, 300_000))
        self.assertIsNone(result.reason)

    # ---------- 카드사 단위 (10/29 나은 규칙 1 ~ 4) ----------

    def test_unmanaged_card_share_is_deducted_with_first_bill(self):
        # 같은 카드사 카드 2장, A만 관리 대상 → 청구서에 든 B 구매는 생활비에 없으니 빼지 않아 B 몫까지 같이 뗀다
        CardBill.objects.filter(pk=self.first.pk).update(total_amount=470_000)
        self.item(self.first, date(2026, 10, 16), 50_000)
        other = self.make_card(is_selected=False)
        self.tx()
        self.tx(date(2026, 10, 16), card=other, amount=50_000)
        self.assertEqual(self.result().amount, 350_000)
        # 둘 다 관리 대상이면 둘 다 생활비라 둘 다 뺀다
        self.assertEqual(self.result(card_ids=[self.card.pk, other.pk]).amount, 300_000)

    def test_issuer_bills_are_not_added_per_card(self):
        # 11/06: 같은 카드사 카드가 여러 장이어도 카드사 통합 청구서는 한 번씩만 (이번 주기 청구서 2장 = 10/7 + 10/25)
        self.make_card(first_connected_at=datetime(2026, 10, 4, 15, tzinfo=dt_timezone.utc), is_selected=False)
        self.make_card()
        earlier = self.bill(date(2026, 10, 7), amount=100_000)
        result = self.result()
        self.assertEqual((result.bill_ids, result.total_amount), (tuple(sorted((earlier.pk, self.first.pk))), 520_000))

    def test_card_selected_later_does_not_start_new_first_bill(self):
        # 카드사에 첫 청구서는 한 번뿐: 나중에 연결 · 선택한 카드도 같은 첫 청구서
        later = self.make_card(first_connected_at=datetime(2026, 10, 30, 15, tzinfo=dt_timezone.utc))
        result = self.result(card_ids=[later.pk])
        self.assertEqual((result.bill_ids, result.amount), ((self.first.pk,), 420_000))
        cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=date(2026, 11, 1), ends_before=date(2026, 12, 1),
        )
        result = self.result(card_ids=[later.pk], cycle=cycle, as_of=date(2026, 11, 20), first_cycle=False)
        self.assertEqual((result.amount, result.reason, result.bill_ids), (0, "first_budget_applied", ()))

    def test_other_issuer_bills_are_not_used(self):
        self.bill(date(2026, 10, 12), amount=50_000, issuer_code="0301")
        self.assertEqual(self.result().bill_ids, (self.first.pk,))

    def test_card_ids_must_be_this_issuers_cards(self):
        other_issuer = self.make_card(issuer_code="0301")
        for card_ids in ([], [other_issuer.pk], [self.card.pk, other_issuer.pk], [self.make_card(user=self.other).pk]):
            with self.subTest(card_ids=card_ids), self.assertRaises(Http404):
                self.result(card_ids=card_ids)

    # ---------- 같은 날 여러 장 (규칙 5) ----------

    def test_same_day_usage_statements_are_summed_and_card_loan_excluded(self):
        CardBill.objects.filter(pk=self.first.pk).update(field_states={"codefBillType": "카드이용대금명세서"})
        extra = self.bill(date(2026, 10, 25), amount=30_000, bill_type="해외이용 명세서")
        self.bill(date(2026, 10, 25), amount=1_000_000, bill_type="카드론 명세서")
        self.tx()
        result = self.result()
        self.assertEqual(result.bill_ids, tuple(sorted((self.first.pk, extra.pk))))
        self.assertEqual((result.total_amount, result.amount), (450_000, 330_000))

    def test_blank_type_is_usage_only_when_alone_that_day(self):
        self.assertEqual(self.result().amount, 420_000)  # 빈 값 + 그날 한 장 (10/29 데모 현대카드)
        self.bill(date(2026, 10, 25), amount=30_000, bill_type="카드론")
        result = self.result()
        self.assertEqual((result.amount, result.reason), (None, "first_bill_ambiguous"))
        self.assertEqual(len(result.bill_ids), 2)

    def test_two_blank_statements_same_day_are_not_chosen_by_uuid(self):
        self.bill(date(2026, 10, 25), amount=430_000)
        self.assertEqual(self.result().reason, "first_bill_ambiguous")

    def test_single_non_loan_statement_is_used_regardless_of_type(self):
        self.tx()
        for value in ("", "0", "1", "기타", "카드사용"):
            with self.subTest(value=value):
                CardBill.objects.filter(pk=self.first.pk).update(field_states={"codefBillType": value})
                result = self.result()
                self.assertEqual((result.amount, result.reason), (300_000, None))
                self.assertEqual(result.bill_ids, (self.first.pk,))

    def test_multiple_statements_with_unknown_type_are_pending(self):
        self.bill(date(2026, 10, 25), amount=30_000, bill_type="카드사용")
        for value in ("0", "1", "기타"):
            with self.subTest(value=value):
                CardBill.objects.filter(pk=self.first.pk).update(field_states={"codefBillType": value})
                self.assertEqual((self.result().amount, self.result().reason), (None, "bill_type_unknown"))

    def test_single_card_loan_statement_is_skipped_for_next_usage_bill(self):
        self.tx()
        for value in ("장기카드대출(카드론)", "카드론"):
            with self.subTest(value=value):
                loan = self.bill(date(2026, 10, 12), amount=1_000_000, bill_type=value)
                result = self.result()
                self.assertEqual((result.bill_ids, result.amount), ((self.first.pk,), 300_000))
                loan.delete()

    def test_multiple_card_loan_statements_are_skipped_for_next_usage_bill(self):
        self.bill(date(2026, 10, 12), amount=1_000_000, bill_type="장기카드대출(카드론)")
        self.bill(date(2026, 10, 12), amount=200_000, bill_type="카드론")
        self.assertEqual(self.result().bill_ids, (self.first.pk,))

    # ---------- 날짜 · 기간 ----------

    def test_old_bill_connection_scope_does_not_block_deduction(self):
        old = CardBill.objects.get(withdrawal_due_date=date(2026, 9, 25))
        mismatched = Connection.objects.create(
            user=self.user, business_type="card", organization_code="0302", status="connected",
        )
        CardBill.objects.filter(pk=old.pk).update(connection=mismatched)
        self.tx()
        for business_type, organization_code in (("card", "0302"), ("bank", "0306")):
            with self.subTest(business_type=business_type):
                Connection.objects.filter(pk=mismatched.pk).update(
                    business_type=business_type, organization_code=organization_code,
                )
                result = self.result()
                self.assertEqual(result.bill_ids, (self.first.pk,))
                self.assertEqual(result.amount, 300_000)
                self.assertIsNone(result.reason)

    def test_current_bill_connection_scope_still_blocks_deduction(self):
        mismatched = Connection.objects.create(
            user=self.user, business_type="card", organization_code="0302", status="connected",
        )
        CardBill.objects.filter(pk=self.first.pk).update(connection=mismatched)
        for business_type, organization_code in (("card", "0302"), ("bank", "0306")):
            with self.subTest(business_type=business_type):
                Connection.objects.filter(pk=mismatched.pk).update(
                    business_type=business_type, organization_code=organization_code,
                )
                result = self.result()
                self.assertIsNone(result.amount)
                self.assertEqual(result.reason, "bill_scope_unknown")

    def test_old_bill_foreign_connection_owner_is_still_rejected(self):
        old = CardBill.objects.get(withdrawal_due_date=date(2026, 9, 25))
        foreign = Connection.objects.create(
            user=self.other, business_type="card", organization_code="0306", status="connected",
        )
        CardBill.objects.filter(pk=old.pk).update(connection=foreign)
        with self.assertRaises(Http404):
            self.result()

    def test_bill_due_before_connection_in_this_cycle_is_included(self):
        # 11/06 기획: 대상 = 이번 주기에 결제일이 든 청구서. 연결(10/10)보다 결제일이 앞서도 포함, 주기 밖(9/25 · 11/25)은 빼기
        before = self.bill(date(2026, 10, 9), amount=900_000)
        same_day = self.bill(date(2026, 10, 10), amount=100_000)
        result = self.result()
        self.assertEqual(result.bill_ids, tuple(sorted((before.pk, same_day.pk, self.first.pk))))
        self.assertEqual(result.total_amount, 1_420_000)

    def test_first_bill_is_not_reset_in_next_cycle(self):
        # 이미 승인 예산에 반영한 카드사(first_cycle=False, budgets.protect.first_budget_cycle)는 다음 주기에 다시 떼지 않는다
        cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=date(2026, 11, 1), ends_before=date(2026, 12, 1),
        )
        result = self.result(cycle=cycle, as_of=date(2026, 11, 20), spending_history_complete=False, first_cycle=False)
        self.assertEqual((result.bill_ids, result.amount, result.reason), ((), 0, "first_budget_applied"))

    def test_cycle_end_is_exclusive_for_due_date(self):
        # 종료 경계일(11/1) 결제는 다음 주기 청구서 → 이번 주기 대상 없음 (대신 가져오지 않음)
        self.first.withdrawal_due_date = date(2026, 11, 1)
        self.first.save()
        self.assertEqual((self.result().amount, self.result().reason), (None, "first_bill_unavailable"))

    def test_cycle_start_is_inclusive_for_due_date(self):
        self.cycle.starts_on = date(2026, 10, 25)
        self.cycle.ends_before = date(2026, 11, 25)
        self.assertEqual(self.result(as_of=date(2026, 10, 25)).amount, 420_000)

    def test_overlap_uses_cycle_start_and_as_of_not_connection_date(self):
        # 주기 시작일(10/1, 연결 10/10 전) 구매도 이번 주기 생활비 → 청구서에 있으면 중복. 기준일 뒤 구매는 아직 생활비에 없음
        CardBill.objects.filter(pk=self.first.pk).update(total_amount=520_000)
        self.item(self.first, date(2026, 10, 1), 100_000)
        self.tx(date(2026, 10, 1), 100_000)
        self.tx()
        self.assertEqual(self.result().amount, 300_000)                       # 520,000 − 100,000 − 120,000
        self.assertEqual(self.result(as_of=date(2026, 10, 14)).amount, 420_000)  # 10/15 구매는 기준일 뒤 → 빼지 않음

    def test_spending_outside_the_bill_is_not_subtracted(self):
        # 11/06: 청구서에 없는 이번 주기 소비(10/31) · 다음 주기 소비(11/1)는 이 청구서에서 빼지 않는다
        self.tx()
        self.tx(date(2026, 10, 31), 120_000)
        self.tx(date(2026, 11, 1), 900_000)
        self.assertEqual(self.result(as_of=date(2026, 11, 20)).amount, 300_000)

    def test_full_and_partial_cancellation_use_existing_net_rule(self):
        # 청구서에 든 이번 주기 구매(10/15 · 120,000)의 거래가 일부 취소 → 생활비엔 순액만 → 그 순액까지만 중복
        tx = self.tx(canceled_amount=20_000)
        self.tx(amount=80_000, direction="in")  # 환불 입금은 짝으로 보지 않음
        result = self.result()
        self.assertEqual((result.cycle_card_spending, result.amount), (100_000, 320_000))
        FinancialTransaction.objects.filter(pk=tx.pk).update(is_canceled=True, canceled_amount=120_000)
        self.assertEqual(self.result().amount, 420_000)   # 전액 취소 → 생활비 0원 → 청구된 금액은 그대로 떼어 둔다

    def test_same_purchase_bank_withdrawal_and_card_payment_are_not_counted(self):
        card_tx = self.tx()
        connection = Connection.objects.create(
            user=self.user, business_type="bank", organization_code="0004", status="connected",
        )
        account = BankAccount.objects.create(user=self.user, connection=connection, source_account_key="test")
        bank_tx = self.tx(card=None, source_kind="bank", account=account)
        self.tx(card=None, source_kind="bank", account=account, transaction_type="card_payment", amount=420_000)
        self.tx(transaction_type="income", amount=50_000)
        self.tx(transaction_type="transfer", amount=60_000)
        TransactionLink.objects.create(user=self.user, from_transaction=card_tx, to_transaction=bank_tx,
                                      relation_type="SAME_PURCHASE", matched_amount=120_000, confirmation_state="confirmed")
        self.assertEqual(self.result().cycle_card_spending, 120_000)

    def test_zero_transactions_requires_successful_coverage(self):
        self.assertIsNone(self.result(spending_history_complete=False).amount)
        self.assertEqual(self.result().amount, 420_000)

    def test_incomplete_bill_history_does_not_promote_later_bill(self):
        self.first.delete()
        result = self.result(bill_history_complete=False)
        self.assertIsNone(result.amount)
        self.assertEqual(result.reason, "bill_history_incomplete")

    def test_no_first_bill_is_unknown_not_zero(self):
        CardBill.objects.all().delete()
        self.assertEqual(self.result().reason, "first_bill_unavailable")
        self.assertIsNone(self.result().amount)

    def test_unknown_due_date_blocks_guessing(self):
        self.bill(None)
        self.assertEqual(self.result().reason, "bill_due_date_unknown")

    def test_uses_payment_due_date_only_when_withdrawal_date_missing(self):
        self.first.withdrawal_due_date = None
        self.first.payment_due_date = date(2026, 10, 25)
        self.first.save()
        self.assertEqual(self.result().bill_ids, (self.first.pk,))
        self.first.withdrawal_due_date = date(2026, 11, 1)
        self.first.save()
        self.assertEqual(self.result().reason, "first_bill_unavailable")   # 출금 예정일이 우선 → 다음 주기

    def test_card_or_unknown_scope_is_not_issuer_total(self):
        for scope in (None, "unknown", "card"):
            with self.subTest(scope=scope):
                CardBill.objects.filter(pk=self.first.pk).update(scope_type=scope)
                self.assertEqual(self.result().reason, "bill_scope_unknown")

    def test_negative_adjustment_bill_needs_confirmation(self):
        CardBill.objects.filter(pk=self.first.pk).update(total_amount=-100)
        self.assertEqual(self.result().reason, "bill_amount_invalid")

    def test_reconnection_and_reselection_keep_original_start(self):
        Connection.objects.filter(pk=self.connection.pk).update(status="disconnected", disconnected_at=timezone.now())
        reconnected = Connection.objects.create(
            user=self.user, business_type="card", organization_code="0306", status="connected",
        )
        self.card.connection = reconnected
        self.card.is_selected = False
        self.card.save()
        self.card.is_selected = True
        self.card.save()
        self.card.refresh_from_db()
        self.assertEqual(self.card.first_connected_at, self.connected_at)
        self.assertEqual(self.result().bill_ids, (self.first.pk,))

    def test_new_card_records_first_connection_once(self):
        with patch("finance.models.timezone.now", return_value=self.connected_at):
            card = self.make_card(first_connected_at=None)
        self.assertEqual(card.first_connected_at, self.connected_at)
        card.save()
        card.refresh_from_db()
        self.assertEqual(card.first_connected_at, self.connected_at)

    def test_legacy_unknown_date_stays_unknown_after_save(self):
        Card.objects.filter(pk=self.card.pk).update(first_connected_at=None)
        self.card.refresh_from_db()
        self.card.save()
        self.assertIsNone(self.card.first_connected_at)
        self.assertEqual(self.result().reason, "first_connection_unknown")

    def test_unknown_date_of_any_issuer_card_is_pending(self):
        # 그 카드는 관리 대상이 아니어도 더 먼저 연결됐을 수 있다
        legacy = self.make_card(is_selected=False)
        Card.objects.filter(pk=legacy.pk).update(first_connected_at=None)
        self.assertEqual(self.result().reason, "first_connection_unknown")

    def test_unconnected_manual_card_does_not_invent_connection_date(self):
        card = self.make_card(origin="manual", connection=None, first_connected_at=None)
        self.assertIsNone(card.first_connected_at)
        self.assertEqual(self.result().bill_ids, (self.first.pk,))  # 직접 입력 카드는 카드사 첫 연결일에 안 씀

    def test_other_user_and_foreign_cycle_are_rejected(self):
        other_card = self.make_card(user=self.other)
        with self.assertRaises(Http404):
            self.result(card_ids=[other_card.pk])
        self.cycle.user = self.other
        with self.assertRaises(Http404):
            self.result()

    def test_inconsistent_card_connection_owner_is_rejected(self):
        foreign = Connection.objects.create(user=self.other, business_type="card", organization_code="0306", status="connected")
        Card.objects.filter(pk=self.card.pk).update(connection=foreign)
        with self.assertRaises(Http404):
            self.result()

    def test_inconsistent_bill_connection_owner_is_rejected(self):
        foreign = Connection.objects.create(user=self.other, business_type="card", organization_code="0306", status="connected")
        CardBill.objects.filter(pk=self.first.pk).update(connection=foreign)
        with self.assertRaises(Http404):
            self.result()

    def test_other_users_transaction_is_never_used_as_evidence(self):
        # 11/06: 중복 근거는 이 회원 거래만 본다 (남의 거래는 짝 후보에도 안 들어감 → 이 항목은 확인 못 함으로 남음)
        self.tx(user=self.other)
        result = self.result()
        self.assertEqual((result.amount, result.cycle_card_spending, result.uncertain_amount), (420_000, 0, 120_000))

    def test_revoked_consent_blocks_reading(self):
        self.consent.revoked_at = timezone.now()
        self.consent.save()
        with self.assertRaises(ConsentRequired):
            self.result()

    def test_repeated_reads_do_not_change_bill_or_create_dues(self):
        from .models import PaymentDue
        self.tx()
        before = list(CardBill.objects.values())
        self.assertEqual(self.result(), self.result())
        self.assertEqual(list(CardBill.objects.values()), before)
        self.assertEqual(PaymentDue.objects.count(), 0)

    def test_paid_bill_still_uses_total_not_remaining_balance(self):
        from .models import PaymentDue
        self.tx()
        due = PaymentDue.objects.create(
            user=self.user, bill=self.first, obligation_type="card_bill", occurrence_key="first-card-bill",
            due_date=date(2026, 10, 25), confirmed_amount=420_000,
            source_type="provider", confirmation_state="confirmed", paid_amount=100_000, payment_state="partial",
        )
        self.assertEqual(self.result().amount, 300_000)
        due.paid_amount, due.payment_state = 420_000, "paid"
        due.save()
        self.assertEqual(self.result().amount, 300_000)

    def test_foreign_payment_account_is_rejected(self):
        connection = Connection.objects.create(
            user=self.other, business_type="bank", organization_code="0004", status="connected",
        )
        account = BankAccount.objects.create(user=self.other, connection=connection, source_account_key="foreign")
        CardBill.objects.filter(pk=self.first.pk).update(payment_account=account)
        with self.assertRaises(Http404):
            self.result()

    def test_bank_connection_cannot_be_used_as_card_connection(self):
        Connection.objects.filter(pk=self.connection.pk).update(business_type="bank")
        with self.assertRaises(Http404):
            self.result()

    def test_cycle_before_first_connection_deducts_nothing(self):
        cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=date(2026, 9, 1), ends_before=date(2026, 10, 1),
        )
        self.assertEqual(self.result(cycle=cycle, as_of=date(2026, 9, 20)).amount, 0)

    def test_dates_and_coverage_flags_must_be_explicit(self):
        with self.assertRaises(TypeError):
            self.result(bill_history_complete="true")
        with self.assertRaises(TypeError):
            self.result(as_of="2026-10-20")
        with self.assertRaises(ValueError):
            self.result(as_of=date(2026, 9, 30))
        self.cycle.ends_before = self.cycle.starts_on
        with self.assertRaises(ValueError):
            self.result()


class FirstConnectionMigrationTests(TransactionTestCase):
    """마이그레이션 후 기존 연결일 null 유지 검사."""
    serialized_rollback = True

    def test_existing_card_stays_unknown_and_new_card_records_time(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            previous = [("finance", "0009_merchant_embedding")]
            executor.migrate(previous)
            old_apps = executor.loader.project_state(previous).apps
            old_user = old_apps.get_model("users", "User").objects.create(email="migration-card@example.invalid")
            old_connection = old_apps.get_model("finance", "Connection").objects.create(
                user_id=old_user.pk, business_type="card", organization_code="0306", status="connected",
            )
            old_card = old_apps.get_model("finance", "Card").objects.create(
                user_id=old_user.pk, connection_id=old_connection.pk, origin="provider",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            card = Card.objects.get(pk=old_card.pk)
            self.assertIsNone(card.first_connected_at)
            card.save()
            card.refresh_from_db()
            self.assertIsNone(card.first_connected_at)
            new_card = Card.objects.create(user_id=old_user.pk, connection_id=old_connection.pk, origin="provider")
            self.assertIsNotNone(new_card.first_connected_at)
        finally:
            MigrationExecutor(connection).migrate(latest)


class CycleBillSelectionTests(TestCase):
    """11/06 실제 사례: 주기 10/10 ~ 11/10(미만), 현대카드 청구서 결제일 11/5, 카드 최초 연결 11/6 → 11/5 청구서가 대상"""

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="cycle-bill@example.invalid")
        self.connection = Connection.objects.create(user=self.user, business_type="card", organization_code="0302", status="connected")
        self.card = Card.objects.create(user=self.user, connection=self.connection, origin="provider", issuer_code="0302",
                                        first_connected_at=datetime(2026, 11, 5, 15, tzinfo=dt_timezone.utc), is_selected=True)
        self.cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 10), ends_before=date(2026, 11, 10))
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="card", document_version="v1", document_hash="t")
        external = patch("finance.codef._request", side_effect=AssertionError("계산 중 CODEF 호출 금지"))
        external.start()
        self.addCleanup(external.stop)

    def bill(self, due, amount=511_515, key=None, **fields):
        return CardBill.objects.create(**{**dict(
            user=self.user, connection=self.connection, issuer_code="0302", statement_key=key or str(due),
            billing_month=due.replace(day=1), total_amount=amount, source_type="provider",
            scope_type="issuer", withdrawal_due_date=due, field_states={"codefBillType": ""}), **fields})

    def result(self, cycle=None, as_of=date(2026, 11, 7)):
        return get_issuer_first_bill_deduction(self.user, "0302", cycle or self.cycle, card_ids=[self.card.pk],
                                               bill_history_complete=True, spending_history_complete=True, as_of=as_of)

    def test_bill_due_before_connection_day_in_cycle_is_used(self):
        bill = self.bill(date(2026, 11, 5))
        self.bill(date(2026, 10, 5), amount=999)    # 지난 주기
        self.bill(date(2026, 12, 5), amount=888)    # 다음 주기 — 대신 가져오지 않음
        for connected in (datetime(2026, 11, 3, 15, tzinfo=dt_timezone.utc), datetime(2026, 11, 4, 15, tzinfo=dt_timezone.utc),
                          datetime(2026, 11, 5, 15, tzinfo=dt_timezone.utc)):   # 한국 날짜 11/4 · 11/5(결제 당일) · 11/6
            with self.subTest(connected=connected):
                Card.objects.filter(pk=self.card.pk).update(first_connected_at=connected)
                result = self.result()
                self.assertEqual((result.bill_ids, result.total_amount, result.reason), ((bill.pk,), 511_515, None))

    def test_cycle_start_included_end_excluded_across_year(self):
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 12, 25), ends_before=date(2027, 1, 25))
        Card.objects.filter(pk=self.card.pk).update(first_connected_at=datetime(2026, 12, 30, 15, tzinfo=dt_timezone.utc))
        start = self.bill(date(2026, 12, 25), amount=100)
        self.bill(date(2027, 1, 25), amount=200)    # 종료 경계일 = 다음 주기
        jan = self.bill(date(2027, 1, 5), amount=300)
        result = self.result(cycle, as_of=date(2027, 1, 10))
        self.assertEqual((result.bill_ids, result.total_amount), (tuple(sorted((start.pk, jan.pk))), 400))

    def test_no_bill_in_cycle_is_pending_for_caller(self):
        self.bill(date(2026, 12, 5))
        result = self.result()
        self.assertEqual((result.amount, result.reason), (None, "first_bill_unavailable"))

    def test_date_corrected_old_bill_is_not_added_twice(self):
        from finance.models import PaymentDue
        old = self.bill(date(2026, 11, 5), key="old", field_states={"codefBillType": "", "codefStartedAt": "2026-11-06T10:00"})
        new = self.bill(date(2026, 11, 6), key="new", field_states={"codefBillType": "", "codefStartedAt": "2026-11-06T11:00"})
        PaymentDue.objects.create(user=self.user, obligation_type="card_bill", bill=new, occurrence_key="k", due_date=date(2026, 11, 6),
                                  confirmed_amount=511_515, source_type="provider", confirmation_state="unknown")
        result = self.result()
        self.assertEqual((result.bill_ids, result.total_amount), ((new.pk,), 511_515))
        self.assertNotIn(old.pk, result.bill_ids)

