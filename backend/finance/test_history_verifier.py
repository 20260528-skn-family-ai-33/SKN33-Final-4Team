"""정합성 검증: 합성 거래만 사용하며 CODEF·ML·예산 서비스는 호출하지 않는다."""
from datetime import date, time
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .api import listed_transactions, transaction_item
from .card_review import save_transaction
from .codef_cards import normalize_approvals
from .history_verifier import transaction_history_report, verify_transaction_history
from .models import BankAccount, Card, Connection, FinancialTransaction as Tx, TransactionLink
from .spending import spending_summary
from .test_card_sync import approval
from .transfer_rules import apply

MONTHS = ["2026-08", "2026-09", "2026-10"]
CLEAN = dict(duplicates="verified", cancellations="verified", refunds="verified")


class HistoryFixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="history@example.invalid")
        self.other = get_user_model().objects.create_user(email="history-other@example.invalid")
        self.card = Card.objects.create(user=self.user, origin="provider", issuer_code="0306")
        conn = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=conn, source_account_key="history")
        clock = patch("finance.history_verifier.timezone.localdate", return_value=date(2026, 11, 6))
        clock.start()
        self.addCleanup(clock.stop)

    def tx(self, **changes):
        fields = dict(user=self.user, source_kind="card", card=self.card, source_key=str(uuid4()),
                      approval_no=str(uuid4()), occurred_on=date(2026, 8, 10), amount=10000,
                      direction="out", transaction_type="spending", merchant_name="테스트상점",
                      confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
                      source_detail={"cancelStatus": "0"})
        fields.update(changes)
        return Tx.objects.create(**fields)

    def bank(self, **changes):
        return self.tx(**dict(dict(source_kind="bank", card=None, account=self.account,
                                  approval_no=None, merchant_name=None, source_description="테스트상점",
                                  source_detail={}), **changes))

    def link(self, original, event, relation="CANCELLATION", **changes):
        fields = dict(user=self.user, from_transaction=original, to_transaction=event,
                      relation_type=relation, matched_amount=event.amount, confirmation_state="confirmed")
        fields.update(changes)
        return TransactionLink.objects.create(**fields)

    def result(self, month="2026-08"):
        return verify_transaction_history(self.user, MONTHS)[month]

    def total(self, month="2026-08"):
        year, m = map(int, month.split("-"))
        rows = listed_transactions(self.user).filter(occurred_on__year=year, occurred_on__month=m)
        return spending_summary(map(transaction_item, rows))["spent_total"]


class HistoryVerifierTest(HistoryFixture):
    def test_clean_unclassified_and_empty_months_do_not_block(self):
        self.tx()
        self.assertEqual(verify_transaction_history(self.user, MONTHS), {m: CLEAN for m in MONTHS})

    def test_input_validation_before_query(self):
        for value in (None, "2026-08", [], MONTHS[:2], [*MONTHS, "2026-07"],
                      ["2026-08"] * 3, ["2026-8", *MONTHS[1:]], ["2026-13", *MONTHS[1:]],
                      ["2026-11", *MONTHS[1:]], ["2027-01", *MONTHS[1:]], [None, *MONTHS[1:]]):
            with self.subTest(value=value), self.assertNumQueries(0), self.assertRaises(ValueError):
                verify_transaction_history(self.user, value)

    def test_year_boundary_and_caller_order(self):
        months = ["2026-01", "2025-11", "2025-12"]
        self.assertEqual(list(verify_transaction_history(self.user, iter(months))), months)

    def test_db_error_is_unknown_without_error_details(self):
        with patch("finance.history_verifier.Tx.objects.filter", side_effect=DatabaseError("secret")):
            report = transaction_history_report(self.user, MONTHS)
        self.assertNotIn("secret", str(report))
        self.assertTrue(all(v["status"] == "unknown" for m in report.values() for v in m.values()))

    def test_other_user_history_ignored(self):
        other_card = Card.objects.create(user=self.other, origin="provider", issuer_code="0306")
        self.tx(user=self.other, card=other_card, is_canceled=True, source_detail={})
        self.assertEqual(self.result(), CLEAN)

    def test_missing_identity_is_unknown(self):
        self.tx(source_key=None)
        self.assertEqual(self.result()["duplicates"], "unknown")

    def test_manual_request_identity_is_supported(self):
        self.tx(source_kind="manual", card=None, source_key=None, request_key="manual-one", source_detail={})
        self.assertEqual(self.result(), CLEAN)

    def test_duplicate_approval_and_fingerprint_are_candidates_not_proven_duplicates(self):
        self.tx(approval_no="same")
        self.tx(approval_no="same")
        report = transaction_history_report(self.user, MONTHS)["2026-08"]["duplicates"]
        self.assertEqual(report["status"], "unknown")
        self.assertEqual({r["code"] for r in report["reasons"]}, {"duplicate_approval", "duplicate_fingerprint"})

    def test_cross_source_spending_without_link_is_unknown(self):
        self.tx()
        self.bank()
        self.assertEqual(self.total(), 20000)
        self.assertEqual(self.result()["duplicates"], "unknown")

    def test_unlinked_transfer_has_no_duplicate_spending_effect(self):
        self.tx()
        self.bank(transaction_type="transfer")
        self.assertEqual(self.total(), 10000)
        self.assertEqual(self.result()["duplicates"], "verified")

    def test_confirmed_same_purchase_is_excluded_once(self):
        card, bank = self.tx(), self.bank()
        self.link(card, bank, "SAME_PURCHASE")
        self.assertEqual(self.total(), 10000)
        self.assertEqual(self.result()["duplicates"], "verified")

    def test_confirmed_same_purchase_transfer_is_also_verified(self):
        self.link(self.tx(), self.bank(transaction_type="transfer"), "SAME_PURCHASE")
        self.assertEqual(self.result()["duplicates"], "verified")

    def test_suggested_same_purchase_spending_is_unknown(self):
        self.link(self.tx(), self.bank(), "SAME_PURCHASE", confirmation_state="suggested")
        self.assertEqual(self.result()["duplicates"], "unknown")

    def test_suggested_same_purchase_transfer_has_proven_zero_effect(self):
        self.link(self.tx(), self.bank(transaction_type="transfer"), "SAME_PURCHASE", confirmation_state="suggested")
        self.assertEqual(self.result()["duplicates"], "verified")

    def test_invalid_same_purchase_and_foreign_owner_are_unresolved(self):
        card, bank = self.tx(), self.bank()
        link = self.link(card, bank, "SAME_PURCHASE", matched_amount=1)
        self.assertEqual(self.result()["duplicates"], "unresolved")
        link.matched_amount, link.user = 10000, self.other
        link.save()
        self.assertEqual(self.result()["duplicates"], "unresolved")

    def test_multiple_same_purchase_links_are_unresolved(self):
        card = self.tx()
        self.link(card, self.bank(), "SAME_PURCHASE")
        self.link(card, self.bank(), "SAME_PURCHASE")
        self.assertEqual(self.result()["duplicates"], "unresolved")

    def test_in_place_partial_and_full_cancellation_are_reflected(self):
        for status, canceled, flag, expected in (("2", 3000, False, 7000), ("1", None, True, 0), ("3", 10000, True, 0)):
            with self.subTest(status=status):
                tx = self.tx(source_detail={"cancelStatus": status}, canceled_amount=canceled, is_canceled=flag)
                self.assertEqual(self.total(), expected)
                self.assertEqual(self.result(), CLEAN)
                tx.delete()

    def test_partial_status_missing_amount_and_conflicting_status_are_unknown(self):
        for detail, fields in (({"cancelStatus": "2"}, {}), ({"cancelStatus": "future"}, {}),
                               ({"cancelStatus": "1"}, {"is_canceled": True, "canceled_amount": 0}),
                               ({"cancelStatus": "0"}, {"canceled_amount": 1000})):
            with self.subTest(detail=detail):
                tx = self.tx(source_detail=detail, **fields)
                self.assertEqual(self.result()["cancellations"], "unknown")
                tx.delete()

    def test_card_cancellation_without_proven_origin_is_unknown(self):
        self.tx(is_canceled=True, source_detail={})
        self.assertEqual(self.result()["cancellations"], "unknown")

    def test_separate_unlinked_card_cancel_checks_original_month(self):
        self.tx(approval_no="same")
        self.tx(approval_no="same", occurred_on=date(2026, 9, 1), is_canceled=True,
                source_detail={"cancelStatus": "1"})
        self.assertEqual(self.result()["cancellations"], "unknown")

    def test_link_alone_does_not_subtract_cancellation(self):
        original = self.tx()
        event = self.tx(is_canceled=True, occurred_on=date(2026, 9, 1), source_detail={"cancelStatus": "1"})
        self.link(original, event)
        self.assertEqual(self.total(), 10000)
        result = transaction_history_report(self.user, MONTHS)
        self.assertEqual(result["2026-08"]["cancellations"]["status"], "unresolved")
        self.assertEqual(result["2026-08"]["cancellations"]["impact_upper_bound"], 10000)
        self.assertEqual(result["2026-09"]["cancellations"]["status"], "verified")

    def test_partial_refund_already_applied_to_original_is_verified(self):
        original = self.tx(canceled_amount=3000, source_detail={"cancelStatus": "2"})
        event = self.bank(direction="in", transaction_type="income", amount=3000, occurred_on=date(2026, 9, 1))
        self.link(original, event)
        self.assertEqual(self.total(), 7000)
        self.assertEqual(self.result(), CLEAN)
        self.assertEqual(self.result("2026-09"), CLEAN)

    def test_current_month_refund_changes_prior_month_status(self):
        original = self.tx()
        refund = self.bank(direction="in", transaction_type="income", amount=1, occurred_on=date(2026, 11, 5))
        self.link(original, refund)
        self.assertEqual(self.result()["refunds"], "unresolved")
        self.assertEqual(transaction_history_report(self.user, MONTHS)["2026-08"]["refunds"]["impact_upper_bound"], 1)

    def test_no_arbitrary_ratio_or_amount_tolerance(self):
        original = self.tx(amount=100_000_000)
        refund = self.bank(direction="in", transaction_type="income", amount=1)
        self.link(original, refund)
        self.assertEqual(self.result()["refunds"], "unresolved")

    def test_refund_on_hidden_bank_original_is_not_silently_ignored(self):
        card, original = self.tx(), self.bank(transaction_type="transfer", canceled_amount=1000)
        self.link(card, original, "SAME_PURCHASE", confirmation_state="suggested")
        event = self.bank(direction="in", transaction_type="income", amount=1000)
        self.link(original, event)
        self.assertEqual(self.result()["refunds"], "unknown")

    def test_unlinked_inflow_counted_as_spending_has_two_sided_bound(self):
        self.bank(direction="in", amount=1000)
        report = transaction_history_report(self.user, MONTHS)["2026-08"]["refunds"]
        self.assertEqual(report["impact_upper_bound"], 2000)

    def test_partial_cancel_event_residual_is_not_spending(self):
        original = self.tx(canceled_amount=3000, source_detail={"cancelStatus": "2"})
        event = self.tx(is_canceled=True, canceled_amount=3000, source_detail={}, occurred_on=date(2026, 9, 1))
        self.link(original, event, matched_amount=3000)
        self.assertEqual(self.result("2026-09")["refunds"], "unresolved")
        report = transaction_history_report(self.user, MONTHS)["2026-09"]["refunds"]
        self.assertEqual(report["impact_upper_bound"], 7000)

    def test_multiple_refunds_sum_and_over_refund_is_unresolved(self):
        original = self.tx(canceled_amount=10000, is_canceled=True, source_detail={"cancelStatus": "1"})
        for amount in (4000, 6000):
            self.link(original, self.bank(direction="in", transaction_type="income", amount=amount))
        self.assertEqual(self.result()["refunds"], "verified")
        self.link(original, self.bank(direction="in", transaction_type="income", amount=1))
        self.assertEqual(self.result()["refunds"], "unresolved")

    def test_reused_refund_and_reverse_date_are_unresolved(self):
        original, other = self.tx(), self.tx(occurred_on=date(2026, 8, 11))
        event = self.bank(direction="in", transaction_type="income")
        self.link(original, event)
        self.link(other, event)
        self.assertEqual(self.result()["refunds"], "unresolved")

    def test_suggested_cancellation_link_is_not_proof(self):
        original = self.tx(is_canceled=True, source_detail={"cancelStatus": "1"})
        event = self.bank(direction="in", transaction_type="income")
        self.link(original, event, confirmation_state="suggested")
        self.assertEqual(self.result()["refunds"], "unknown")

    def test_unlinked_bank_cancel_and_inflow_affect_preceding_months(self):
        self.bank(is_canceled=True, amount=3000, occurred_on=date(2026, 10, 31))
        self.bank(direction="in", transaction_type="income", amount=1000, occurred_on=date(2026, 11, 1))
        result = verify_transaction_history(self.user, MONTHS)
        for month in MONTHS:
            self.assertEqual(result[month]["cancellations"], "unknown")
            self.assertEqual(result[month]["refunds"], "unknown")

    def test_zero_amount_unprocessed_events_are_harmless(self):
        self.bank(is_canceled=True, amount=0)
        self.bank(direction="in", transaction_type="income", amount=0)
        self.assertEqual(self.result(), CLEAN)

    def test_only_link_processing_failure_blocks_integrity(self):
        tx = self.tx(field_states={"after_save_pending": {"classify_spending": "step_error"}})
        self.assertEqual(self.result(), CLEAN)
        tx.field_states = {"after_save_pending": {"link_basics": "step_error"}}
        tx.save()
        self.assertEqual(self.result(), dict.fromkeys(CLEAN, "unknown"))

    def test_read_only_and_deterministic_with_two_queries(self):
        self.tx()
        with CaptureQueriesContext(connection) as queries:
            first = transaction_history_report(self.user, MONTHS)
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries))
        self.assertEqual(first, transaction_history_report(self.user, MONTHS))


class HistoryPipelineTest(HistoryFixture):
    """실제 정규화·저장·연결을 사용한다. 자동 연결 상태를 confirmed로 바꾸지 않는다."""

    def save_approval(self, *, existing=None, **changes):
        raw = approval(resUsedDate="20260810", resUsedAmount="10000", resMemberStoreName="테스트상점",
                       commStartDate="20260801", commEndDate="20261031")
        raw.update(changes)
        rows, _ = normalize_approvals([raw], date(2026, 8, 1), date(2026, 10, 31))
        payload = dict(rows[0])
        self.assertIsNone(payload.pop("unknown_reason"))
        identity = payload.pop("identity")
        payload.pop("card_number")
        payload.pop("card_name")
        tx, created = save_transaction(self.user, self.card, identity, payload, existing)
        apply(self.user, Tx.objects.filter(pk=tx.pk))
        tx.refresh_from_db()
        return tx, created

    def test_resynced_partial_cancel_keeps_provider_evidence_and_net_consistent(self):
        original, _ = self.save_approval()
        tx, created = self.save_approval(resCancelYN="2", resCancelAmount="3000")
        self.assertFalse(created)
        self.assertEqual(tx.pk, original.pk)
        self.assertEqual(Tx.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.total(), 7000)
        self.assertEqual(self.result(), CLEAN)
        self.assertEqual((tx.source_detail["cancelStatus"], tx.is_canceled, tx.canceled_amount), ("2", False, 3000))

    def test_resynced_full_cancel_keeps_provider_evidence_and_net_consistent(self):
        original, _ = self.save_approval()
        tx, created = self.save_approval(resCancelYN="1")
        self.assertFalse(created)
        self.assertEqual(tx.pk, original.pk)
        self.assertEqual(Tx.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.total(), 0)
        self.assertEqual(self.result(), CLEAN)
        self.assertEqual((tx.source_detail["cancelStatus"], tx.is_canceled, tx.canceled_amount), ("1", True, None))

    def test_resync_repairs_stale_metadata_even_when_amount_unchanged(self):
        original, _ = self.save_approval()
        for explicit_existing in (False, True):
            with self.subTest(explicit_existing=explicit_existing):
                Tx.objects.filter(pk=original.pk).update(
                    canceled_amount=3000, source_detail={"cancelStatus": "0", "localNote": "유지"},
                    confirmation_state="confirmed", confirmed_category_id="FOOD", field_states={"user_note": "유지"},
                )
                original.refresh_from_db()
                self.assertEqual(self.result()["cancellations"], "unknown")
                tx, created = self.save_approval(existing=original if explicit_existing else None,
                                                 resCancelYN="2", resCancelAmount="3000")
                self.assertFalse(created)
                self.assertEqual(tx.source_detail["cancelStatus"], "2")
                self.assertEqual(tx.source_detail["localNote"], "유지")
                self.assertEqual((tx.confirmation_state, tx.confirmed_category_id, tx.field_states),
                                 ("confirmed", "FOOD", {"user_note": "유지"}))
                self.assertEqual(self.result(), CLEAN)

    def test_resync_restores_normal_status_without_unnecessary_write(self):
        original, _ = self.save_approval(resCancelYN="2", resCancelAmount="3000")
        restored, _ = self.save_approval()
        self.assertEqual(original.pk, restored.pk)
        self.assertEqual((restored.is_canceled, restored.canceled_amount, restored.source_detail["cancelStatus"]),
                         (False, None, "0"))
        self.assertEqual(self.total(), 10000)
        self.assertEqual(self.result(), CLEAN)
        repeated, _ = self.save_approval()
        self.assertEqual(repeated.updated_at, restored.updated_at)

    def test_actual_metadata_conflict_is_still_unknown(self):
        tx, _ = self.save_approval(resCancelYN="2", resCancelAmount="3000")
        Tx.objects.filter(pk=tx.pk).update(source_detail={"cancelStatus": "0"})
        report = transaction_history_report(self.user, MONTHS)["2026-08"]
        self.assertEqual(report["cancellations"]["status"], "unknown")
        self.assertIn("cancel_metadata_conflict", {r["code"] for r in report["cancellations"]["reasons"]})

    def test_auto_separate_card_cancel_without_deduction_is_unresolved(self):
        original, _ = self.save_approval()
        event, _ = self.save_approval(resUsedDate="20260901", resUsedAmount="3000", resCancelYN="1")
        link = TransactionLink.objects.get(relation_type="CANCELLATION")
        self.assertEqual((link.from_transaction_id, link.to_transaction_id, link.confirmation_state),
                         (original.pk, event.pk, "suggested"))
        self.assertEqual(self.total(), 10000)
        self.assertEqual(self.total("2026-09"), 0)
        report = transaction_history_report(self.user, MONTHS)["2026-08"]
        for kind in ("cancellations", "refunds"):
            self.assertEqual(report[kind]["status"], "unresolved")
            self.assertEqual({r["code"] for r in report[kind]["reasons"]},
                             {"suggested_cancellation_link", "cancellation_not_reflected"})
        link.refresh_from_db()
        self.assertEqual(link.confirmation_state, "suggested")

    def test_auto_card_cancel_with_correct_net_still_requires_link_evidence(self):
        self.save_approval(resCancelYN="2", resCancelAmount="3000")
        self.save_approval(resUsedDate="20260901", resUsedAmount="3000", resCancelYN="1")
        self.assertEqual(self.total(), 7000)
        self.assertEqual(self.total("2026-09"), 0)
        self.assertEqual(self.result()["cancellations"], "unknown")
        report = transaction_history_report(self.user, MONTHS)["2026-08"]["refunds"]
        self.assertEqual({r["code"] for r in report["reasons"]}, {"suggested_cancellation_link"})
        self.assertEqual(TransactionLink.objects.get().confirmation_state, "suggested")

    def test_auto_bank_cancel_net_zero_does_not_prove_relationship(self):
        original = self.bank(occurred_on=date(2026, 8, 10))
        event = self.bank(occurred_on=date(2026, 8, 11), is_canceled=True, canceled_amount=10000)
        apply(self.user, Tx.objects.filter(pk__in=[original.pk, event.pk]))
        self.assertEqual(self.total(), 0)
        self.assertEqual(TransactionLink.objects.get().confirmation_state, "suggested")
        self.assertEqual(self.result()["cancellations"], "unknown")

    def test_auto_check_card_purchase_is_counted_once_and_verified(self):
        self.card.name = "테스트 체크카드"
        self.card.save(update_fields=["name"])
        self.save_approval()
        bank = self.bank(transaction_type="transfer")
        apply(self.user, Tx.objects.filter(pk=bank.pk))
        link = TransactionLink.objects.get(relation_type="SAME_PURCHASE")
        self.assertEqual(link.confirmation_state, "suggested")
        self.assertEqual(self.total(), 10000)
        self.assertEqual(self.result(), CLEAN)

    def test_salary_card_cancel_and_check_purchase_keep_dimension_states_separate(self):
        from budgets.salary import salary_reviews
        from rest_framework.test import APIClient
        from users.models import FinancialProfile

        self.card.name = "테스트 체크카드"
        self.card.save(update_fields=["name"])
        self.save_approval()
        bank = self.bank(transaction_type="transfer")
        apply(self.user, Tx.objects.filter(pk=bank.pk))
        # 별도 구매의 원승인 부분취소 재조회. 같은 구매 연결과 취소 연결을 수작업으로 만들지 않는다.
        self.save_approval(resApprovalNo="cancelled", resUsedDate="20260811", resMemberStoreName="다른상점")
        self.save_approval(resApprovalNo="cancelled", resUsedDate="20260811", resMemberStoreName="다른상점",
                           resCancelYN="2", resCancelAmount="3000")
        self.account.account_type, self.account.is_selected = "demand", True
        self.account.save(update_fields=["account_type", "is_selected"])
        FinancialProfile.objects.update_or_create(user=self.user, defaults={"monthly_take_home_income": 2000000, "payday": 25})
        salary = self.bank(direction="in", transaction_type="income", amount=2000000,
                           source_description="급여", occurred_on=date(2026, 10, 25))
        apply(self.user, Tx.objects.filter(pk=salary.pk))
        # R12: 큰 입금은 확인 전 급여가 아니다. 실제 확인 API를 거쳐도 환불 근거를 대신하지 않는다.
        self.assertNotIn(salary.pk, salary_reviews(self.user))
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post("/api/budgets/salary/decisions/", {
            "transactionIds": [str(salary.pk)], "decision": "confirmed",
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(salary_reviews(self.user)[salary.pk], "confirmed")
        self.assertEqual(self.total(), 17000)
        report = verify_transaction_history(self.user, MONTHS)
        for state in report.values():
            self.assertEqual(state, {**CLEAN, "refunds": "unknown"})
        self.assertEqual(TransactionLink.objects.get(relation_type="SAME_PURCHASE").confirmation_state, "suggested")

    def test_auto_own_transfer_does_not_certify_non_refund_inflow(self):
        self.user.nickname = "김나은"
        self.user.save(update_fields=["nickname"])
        second = BankAccount.objects.create(user=self.user, connection=self.account.connection, source_account_key="second")
        # R11: 양쪽 적요가 가입 이름과 같고 시각 차이가 5분 이내인 유일한 쌍.
        out = self.bank(transaction_type="transfer", source_description="김나은", amount=20000, occurred_time=time(9))
        income = self.bank(account=second, direction="in", transaction_type="income", source_description="김나은",
                           amount=20000, occurred_time=time(9, 1))
        apply(self.user, Tx.objects.filter(pk__in=[out.pk, income.pk]))
        link = TransactionLink.objects.get(relation_type="OWN_TRANSFER")
        self.assertEqual(link.confirmation_state, "suggested")
        income.refresh_from_db()
        self.assertEqual(income.transaction_type, "own_transfer")
        self.assertEqual(self.result()["refunds"], "unknown")

    def test_interest_description_and_user_category_confirmation_do_not_prove_non_refund(self):
        income = self.bank(direction="in", transaction_type="income", source_description="예금이자",
                           amount=100, confirmation_state="confirmed", occurred_on=date(2026, 10, 1))
        apply(self.user, Tx.objects.filter(pk=income.pk))
        self.assertTrue(all(state["refunds"] == "unknown" for state in verify_transaction_history(self.user, MONTHS).values()))
