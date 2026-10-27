from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import pipeline
from .models import FinancialTransaction


class TransactionPipelineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="pipeline@example.com")
        cls.other = get_user_model().objects.create_user(email="other@example.com")
        fields = dict(
            source_kind="manual", occurred_on="2026-10-27", amount=10000,
            direction="out", transaction_type="spending",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
        )
        cls.tx = FinancialTransaction.objects.create(user=cls.user, **fields)
        cls.unrequested_tx = FinancialTransaction.objects.create(user=cls.user, **fields)
        cls.other_tx = FinancialTransaction.objects.create(user=cls.other, **fields)

    def test_steps_run_in_documented_order(self):
        names = [
            "link_basics", "classify_spending", "match_card_payments",
            "match_loan_repayments", "match_savings", "match_payment_dues",
            "detect_candidates",
        ]
        self.assertEqual(pipeline.AFTER_SAVE_STEPS, [(name, getattr(pipeline, name)) for name in names])
        calls = Mock()
        steps = [(name, getattr(calls, name)) for name in names]

        with patch.object(pipeline, "AFTER_SAVE_STEPS", steps):
            pipeline.process_new_transactions(self.user, [self.tx.pk])

        self.assertEqual([call[0] for call in calls.mock_calls], names)
        for call in calls.mock_calls:
            self.assertEqual(call.args[0], self.user)
            self.assertEqual(list(call.args[1]), [self.tx])

    def test_only_requested_transactions_owned_by_user_reach_each_step(self):
        steps = [(name, Mock()) for name, _ in pipeline.AFTER_SAVE_STEPS]
        with patch.object(pipeline, "AFTER_SAVE_STEPS", steps):
            pipeline.process_new_transactions(self.user, [self.tx.pk, self.other_tx.pk])

        for _, step in steps:
            step.assert_called_once()
            self.assertEqual(step.call_args.args[0], self.user)
            self.assertEqual(list(step.call_args.args[1]), [self.tx])

    def test_failed_step_rolls_back_and_next_step_continues(self):
        for database_error in (False, True):
            with self.subTest(database_error=database_error):
                observed = []

                def first(user, txs):
                    tx = list(txs)[0]
                    tx.source_description = "앞 단계 성공"
                    tx.save(update_fields=["source_description"])

                def failing(user, txs):
                    tx = list(txs)[0]
                    tx.source_description = "실패한 단계 변경"
                    tx.save(update_fields=["source_description"])
                    if database_error:
                        txs.update(amount=-1)
                    raise RuntimeError("단계 실패")

                def following(user, txs):
                    observed.extend(tx.source_description for tx in txs)
                    txs.update(balance_after=50000)

                next_step = Mock(side_effect=following)
                steps = [("first", first), ("failing", failing), ("following", next_step)]
                with patch.object(pipeline, "AFTER_SAVE_STEPS", steps):
                    with self.assertLogs("finance.pipeline", level="ERROR") as logs:
                        pipeline.process_new_transactions(self.user, [self.tx.pk])

                next_step.assert_called_once()
                self.assertEqual(observed, ["앞 단계 성공"])
                self.tx.refresh_from_db()
                self.assertEqual(self.tx.source_description, "앞 단계 성공")
                self.assertEqual(self.tx.amount, 10000)
                self.assertEqual(self.tx.balance_after, 50000)
                self.assertEqual(len(logs.records), 1)
                self.assertIn("failing", logs.records[0].getMessage())
                self.assertIsNone(logs.records[0].exc_info)
                self.assertIn("database_error" if database_error else "step_error", logs.records[0].getMessage())

    def test_empty_ids_skip_all_steps_and_queries(self):
        steps = [(name, Mock()) for name, _ in pipeline.AFTER_SAVE_STEPS]
        with patch.object(pipeline, "AFTER_SAVE_STEPS", steps), self.assertNumQueries(0):
            pipeline.process_new_transactions(self.user, [])

        for _, step in steps:
            step.assert_not_called()

    def test_database_error_log_contains_only_step_and_safe_code(self):
        self.tx.source_description = "시험용 비공개 거래 설명 110123456789"
        self.tx.save(update_fields=["source_description"])

        def failing(user, txs):
            txs.update(amount=-1)

        with patch.object(pipeline, "AFTER_SAVE_STEPS", [("classify_spending", failing)]):
            with self.assertLogs("finance.pipeline", level="ERROR") as logs:
                pipeline.process_new_transactions(self.user, [self.tx.pk])

        self.assertEqual(len(logs.records), 1)
        self.assertIn("classify_spending", logs.output[0])
        self.assertIn("database_error", logs.output[0])
        self.assertNotIn(self.tx.source_description, logs.output[0])
        self.assertNotIn(str(self.tx.pk), logs.output[0])
        self.assertNotIn("Traceback", logs.output[0])
        self.assertIsNone(logs.records[0].exc_info)
        self.assertIsNone(logs.records[0].stack_info)

    def test_retry_preserves_other_metadata_and_does_not_repeat_successful_prefix(self):
        self.tx.field_states = {"original_transaction_type": "transfer"}
        self.tx.save(update_fields=["field_states"])
        before, after = Mock(), Mock()
        secret = "시험 금융 원문 110123456789"
        failing = Mock(side_effect=RuntimeError(secret))
        steps = [("link_basics", before), ("classify_spending", failing), ("match_payment_dues", after)]

        with patch.object(pipeline, "AFTER_SAVE_STEPS", steps):
            with self.assertLogs("finance.pipeline", level="ERROR") as logs:
                pipeline.process_new_transactions(self.user, [self.tx.pk])
            self.assertNotIn(secret, "\n".join(logs.output))
            self.assertIsNone(logs.records[0].exc_info)
            self.tx.refresh_from_db()
            self.assertEqual(self.tx.field_states, {
                "original_transaction_type": "transfer",
                "after_save_pending": {"classify_spending": "step_error", "match_payment_dues": "upstream_failed"},
            })

            with self.assertLogs("finance.pipeline", level="ERROR"):
                pipeline.retry_failed_transactions(self.user, [self.tx.pk])
            self.tx.refresh_from_db()
            self.assertEqual(self.tx.field_states["after_save_pending"]["classify_spending"], "step_error")
            self.assertEqual(before.call_count, 1)
            self.assertEqual(failing.call_count, 2)
            self.assertEqual(after.call_count, 2)

            def success(user, txs):
                txs.update(field_states={"original_transaction_type": "transfer", "step_evidence": "유지"})

            failing.side_effect = success
            pipeline.retry_failed_transactions(self.user, [self.tx.pk])
            self.tx.refresh_from_db()
            self.assertEqual(self.tx.field_states, {"original_transaction_type": "transfer", "step_evidence": "유지"})
            self.assertEqual(before.call_count, 1)
            self.assertEqual(failing.call_count, 3)
            self.assertEqual(after.call_count, 3)
            pipeline.retry_failed_transactions(self.user, [self.tx.pk])
            self.assertEqual(failing.call_count, 3)

    def test_retry_selects_pending_steps_per_transaction_and_checks_owner(self):
        FinancialTransaction.objects.filter(pk__in=[self.tx.pk, self.other_tx.pk]).update(
            field_states={"after_save_pending": {"classify_spending": "step_error"}},
        )
        self.unrequested_tx.field_states = {"after_save_pending": {"match_payment_dues": "database_error"}}
        self.unrequested_tx.save(update_fields=["field_states"])
        classify, match = Mock(), Mock()
        with patch.object(pipeline, "AFTER_SAVE_STEPS", [
            ("classify_spending", classify), ("match_payment_dues", match),
        ]):
            pipeline.retry_failed_transactions(self.user, [self.tx.pk, self.unrequested_tx.pk, self.other_tx.pk])
        self.assertEqual(list(classify.call_args.args[1]), [self.tx])
        self.assertEqual(list(match.call_args.args[1]), [self.unrequested_tx])
        self.other_tx.refresh_from_db()
        self.assertEqual(self.other_tx.field_states, {"after_save_pending": {"classify_spending": "step_error"}})
