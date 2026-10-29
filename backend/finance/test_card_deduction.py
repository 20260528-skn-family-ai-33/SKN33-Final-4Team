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
        self.first = self.bill(date(2026, 10, 25))
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
        # 같은 카드사 카드 2장, A만 관리 대상 → B로 쓴 돈은 빼지 않아 B 몫까지 같이 뗀다
        other = self.make_card(is_selected=False)
        self.tx()
        self.tx(card=other, amount=50_000)
        self.assertEqual(self.result().amount, 300_000)
        # 둘 다 관리 대상이면 둘 다 뺀다
        self.assertEqual(self.result(card_ids=[self.card.pk, other.pk]).amount, 250_000)

    def test_issuer_first_connection_is_earliest_card(self):
        # 한국 시간 10/5에 먼저 연결한 카드가 있으면 카드사 첫 연결일은 10/5
        self.make_card(first_connected_at=datetime(2026, 10, 4, 15, tzinfo=dt_timezone.utc), is_selected=False)
        earlier = self.bill(date(2026, 10, 7), amount=100_000)
        result = self.result()
        self.assertEqual((result.bill_ids, result.amount), ((earlier.pk,), 100_000))

    def test_card_selected_later_does_not_start_new_first_bill(self):
        # 카드사에 첫 청구서는 한 번뿐: 나중에 연결 · 선택한 카드도 같은 첫 청구서
        later = self.make_card(first_connected_at=datetime(2026, 10, 30, 15, tzinfo=dt_timezone.utc))
        result = self.result(card_ids=[later.pk])
        self.assertEqual((result.bill_ids, result.amount), ((self.first.pk,), 420_000))
        cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=date(2026, 11, 1), ends_before=date(2026, 12, 1),
        )
        result = self.result(card_ids=[later.pk], cycle=cycle, as_of=date(2026, 11, 20))
        self.assertEqual((result.amount, result.reason), (0, "first_bill_outside_cycle"))

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

    def test_unknown_bill_type_code_is_pending_not_guessed(self):
        # CODEF 공통 코드표가 없어 숫자 코드는 카드론인지 모른다
        for value in ("0", "1", "기타"):
            with self.subTest(value=value):
                CardBill.objects.filter(pk=self.first.pk).update(field_states={"codefBillType": value})
                self.assertEqual((self.result().amount, self.result().reason), (None, "bill_type_unknown"))

    def test_card_loan_only_day_is_skipped_for_next_usage_bill(self):
        self.bill(date(2026, 10, 12), amount=1_000_000, bill_type="장기카드대출(카드론)")
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

    def test_connection_day_is_inclusive_and_uses_korean_date(self):
        self.bill(date(2026, 10, 9), amount=900_000)  # 한국 날짜로 연결 전
        same_day = self.bill(date(2026, 10, 10), amount=100_000)
        result = self.result()
        self.assertEqual((result.bill_ids, result.amount), ((same_day.pk,), 100_000))

    def test_first_bill_is_not_reset_in_next_cycle(self):
        cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=date(2026, 11, 1), ends_before=date(2026, 12, 1),
        )
        result = self.result(cycle=cycle, as_of=date(2026, 11, 20), spending_history_complete=False)
        self.assertEqual((result.bill_ids, result.amount), ((self.first.pk,), 0))

    def test_cycle_end_is_exclusive_for_due_date(self):
        self.first.withdrawal_due_date = date(2026, 11, 1)
        self.first.save()
        self.assertEqual(self.result().amount, 0)

    def test_cycle_start_is_inclusive_for_due_date(self):
        self.cycle.starts_on = date(2026, 10, 25)
        self.cycle.ends_before = date(2026, 11, 25)
        self.assertEqual(self.result(as_of=date(2026, 10, 25)).amount, 420_000)

    def test_spending_uses_cycle_start_and_as_of_not_connection_date(self):
        self.tx(date(2026, 9, 30), 999_999)
        self.tx(date(2026, 10, 1), 100_000)  # 연결 전 주기 사용액도 포함
        self.tx(date(2026, 10, 20), 20_000)
        self.tx(date(2026, 10, 21), 900_000)  # 조회 기준일 이후는 미포함
        self.assertEqual(self.result().amount, 300_000)

    def test_spending_excludes_next_cycle_even_when_as_of_is_later(self):
        self.tx(date(2026, 10, 31), 120_000)
        self.tx(date(2026, 11, 1), 900_000)
        self.assertEqual(self.result(as_of=date(2026, 11, 20)).amount, 300_000)

    def test_full_and_partial_cancellation_use_existing_net_rule(self):
        self.tx(amount=200_000, canceled_amount=80_000)
        self.tx(amount=90_000, is_canceled=True)
        self.tx(amount=80_000, direction="in")  # 환불 중복 차감 방지
        result = self.result()
        self.assertEqual((result.cycle_card_spending, result.amount), (120_000, 300_000))

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
        self.assertEqual(self.result().amount, 0)

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

    def test_inconsistent_transaction_owner_is_rejected(self):
        self.tx(user=self.other)
        with self.assertRaises(Http404):
            self.result()

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
