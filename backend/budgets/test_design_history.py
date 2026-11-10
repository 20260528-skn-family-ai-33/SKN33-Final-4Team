"""Budget history wiring: synthetic evidence only, no CODEF, ML or external LLM calls."""
from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import design_history, design_service as service

MONTHS = ['2026-08', '2026-09', '2026-10']
TODAY = date(2026, 11, 7)
CLEAN = dict.fromkeys(design_history.CHECKS, 'verified')


class HistoryProofTest(SimpleTestCase):
    def test_missing_or_invalid_evidence_cannot_become_verified(self):
        for raw in (None, [], 'verified', {}, {MONTHS[0]: 'verified'},
                    {MONTHS[0]: {'refunds': True, 'duplicates': 'maybe'}}):
            with self.subTest(raw=raw):
                out = design_history.history_checks(MONTHS, raw)
                self.assertEqual(out, {m: dict.fromkeys(design_history.CHECKS, 'unknown') for m in MONTHS})

    def test_valid_states_and_partial_unknown_evidence_are_preserved_without_mutation(self):
        raw = {MONTHS[0]: CLEAN.copy(), MONTHS[1]: {'refunds': 'unresolved'},
               MONTHS[2]: {**CLEAN, 'refunds': 'unknown'}}
        before = deepcopy(raw)
        out = design_history.history_checks(MONTHS, raw)
        self.assertEqual(out[MONTHS[0]], CLEAN)
        self.assertEqual(out[MONTHS[1]], {'refunds': 'unresolved', 'duplicates': 'unknown', 'cancellations': 'unknown'})
        self.assertEqual(out[MONTHS[2]]['refunds'], 'unknown')
        self.assertEqual(raw, before)

    def test_adapter_requests_only_the_confirmed_salary_extension(self):
        raw = {m: CLEAN.copy() for m in MONTHS}
        with patch('finance.history_verifier.verify_transaction_history', return_value=raw) as verify:
            self.assertEqual(design_history.verify_budget_history(None, MONTHS), raw)
        verify.assert_called_once_with(None, MONTHS, use_confirmed_salary=True)

    def test_unavailable_dependency_or_db_leaves_all_checks_unknown(self):
        for error in (ImportError('private module details'), DatabaseError('private database details')):
            with self.subTest(error=type(error).__name__), patch(
                    'finance.history_verifier.verify_transaction_history', side_effect=error):
                out = design_history.verify_budget_history(None, MONTHS)
                self.assertTrue(all(v == 'unknown' for row in out.values() for v in row.values()))
                self.assertNotIn('private', str(out))

    def test_programming_or_invalid_month_errors_are_not_swallowed(self):
        with patch('finance.history_verifier.verify_transaction_history', side_effect=ValueError('invalid months')):
            with self.assertRaises(ValueError):
                design_history.verify_budget_history(None, MONTHS)

    def test_service_default_wires_adapter_but_explicit_none_disables_it(self):
        rows = Mock()
        rows.filter.return_value.order_by.return_value = []
        with patch('finance.api.listed_transactions', return_value=rows), \
                patch('spending_analysis.coverage.coverage', return_value={'state': 'complete'}), \
                patch.object(service, 'verify_budget_history', return_value={m: CLEAN.copy() for m in MONTHS}) as verify:
            records = service.history_for_user(None, TODAY)
            verify.assert_called_once_with(None, MONTHS)
            self.assertTrue(all(r['normalization_checks'] == CLEAN for r in records.values()))
            verify.reset_mock()
            with override_settings(BUDGET_HISTORY_VERIFIER=None):
                records = service.history_for_user(None, TODAY)
            verify.assert_not_called()
            self.assertTrue(all(r['normalization_checks']['refunds'] == 'unknown' for r in records.values()))

    def test_invalid_custom_verifier_result_keeps_b_closed(self):
        rows = Mock()
        rows.filter.return_value.order_by.return_value = []
        with patch('finance.api.listed_transactions', return_value=rows), \
                patch('spending_analysis.coverage.coverage', return_value={'state': 'complete'}), \
                override_settings(BUDGET_HISTORY_VERIFIER=lambda user, months: {'2026-08': {'refunds': 'maybe'}}):
            records = service.history_for_user(None, TODAY)
        self.assertEqual(service.select_plan(service.summarize_history(records, as_of=TODAY))['plan'], 'a')


class BudgetHistoryEvidenceTest(TestCase):
    """Run against merged main's SalaryDecision schema, using a disposable PostgreSQL DB."""
    def setUp(self):
        from finance.models import BankAccount, Connection, FinancialTransaction, SyncRun

        self.Tx = FinancialTransaction
        self.user = get_user_model().objects.create_user(email='synthetic-budget-proof@example.invalid')
        self.bank = Connection.objects.create(user=self.user, business_type='bank', organization_code='0004', status='connected')
        self.account = BankAccount.objects.create(user=self.user, connection=self.bank, source_account_key='synthetic',
            account_type='demand', currency='KRW', is_selected=True)
        self.collection_run = SyncRun.objects.create(user=self.user, connection=self.bank, product_code='bank_sync', status='success',
            covered_from=date(2026, 8, 1), covered_to=date(2026, 10, 31))
        self.food = []
        for month, count in zip((8, 9, 10), (4, 4, 3)):
            for day in range(10, 10+count):
                self.food.append(self.tx(occurred_on=date(2026, month, day)))
        self.enterContext(patch('django.utils.timezone.localdate', return_value=TODAY))
        self.enterContext(patch.object(service, 'predict_ml', side_effect=AssertionError('No ML calls')))

    def tx(self, **changes):
        data = dict(user=self.user, account=self.account, source_kind='bank', source_key=str(uuid4()),
            amount=20000, occurred_on=date(2026, 8, 20), direction='out', transaction_type='spending',
            source_description='합성 식사', confirmed_category_id='FOOD', confirmation_state='confirmed')
        data.update(changes)
        return self.Tx.objects.create(**data)

    def salary(self, state='confirmed', *, snapshot=True, month=8):
        from .models import SalaryDecision
        from .salary import confirmation_snapshot

        tx = self.tx(direction='in', transaction_type='income', amount=2500000, occurred_on=date(2026, month, 25),
            source_description='합성 급여', confirmed_category_id='UNCLASSIFIED', confirmation_state='needs_review')
        fields = confirmation_snapshot(tx) if snapshot else {}
        decision = SalaryDecision.objects.create(user=self.user, transaction=tx, state=state,
            pay_cycle_on=None if state == 'rejected' else tx.occurred_on, **fields)
        return tx, decision

    def selected(self):
        records = service.history_for_user(self.user, TODAY)
        return service.select_plan(service.summarize_history(records, as_of=TODAY)), records

    def test_valid_confirmed_salary_opens_b_only_in_budget_opt_in(self):
        from finance.history_verifier import verify_transaction_history
        for month in (8, 9, 10):
            self.salary(month=month)
        default = verify_transaction_history(self.user, MONTHS)
        self.assertTrue(all(row['refunds'] == 'unknown' for row in default.values()))
        selection, records = self.selected()
        self.assertEqual((selection['plan'], selection['max_category_count']), ('b', 11))
        self.assertTrue(all(r['normalization_checks'] == CLEAN and r['coverage_complete'] for r in records.values()))

    def test_auto_rejected_recheck_and_legacy_confirmation_do_not_open_b(self):
        for state, snapshot in (('auto', False), ('rejected', False), ('recheck', True), ('confirmed', False)):
            with self.subTest(state=state, snapshot=snapshot):
                tx, decision = self.salary(state, snapshot=snapshot)
                selection, records = self.selected()
                self.assertEqual(selection['plan'], 'a')
                self.assertEqual(records['2026-08']['normalization_checks']['refunds'], 'unknown')
                decision.refresh_from_db()
                self.assertEqual(decision.state, state)
                tx.delete()

    def test_changed_salary_evidence_is_excluded_without_writing_recheck(self):
        from finance.models import BankAccount
        other = BankAccount.objects.create(user=self.user, connection=self.bank, source_account_key='other',
            account_type='demand', currency='KRW', is_selected=True)
        for changed in ({'amount': 2500001}, {'occurred_on': date(2026, 8, 26)},
                        {'source_description': '합성 급여 정정'}, {'account': other}):
            with self.subTest(field=next(iter(changed))):
                tx, decision = self.salary()
                self.Tx.objects.filter(pk=tx.pk).update(**changed)
                selection, records = self.selected()
                self.assertEqual(selection['plan'], 'a')
                self.assertEqual(records['2026-08']['normalization_checks']['refunds'], 'unknown')
                decision.refresh_from_db()
                self.assertEqual(decision.state, 'confirmed')
                self.assertEqual(decision.snapshot_amount, 2500000)
                tx.delete()

    def test_matching_amount_unconfirmed_inflow_is_not_excused_by_salary(self):
        self.salary()
        self.tx(direction='in', transaction_type='income', amount=2500000,
                occurred_on=date(2026, 9, 25), source_description='합성 일반 입금')
        selection, records = self.selected()
        self.assertEqual(selection['plan'], 'a')
        self.assertEqual(records['2026-09']['normalization_checks']['refunds'], 'unknown')

    def test_another_users_confirmation_cannot_prove_this_users_inflow(self):
        tx, decision = self.salary()
        other = get_user_model().objects.create_user(email='synthetic-other-proof@example.invalid')
        type(decision).objects.filter(pk=decision.pk).update(user=other)
        self.assertEqual(self.selected()[0]['plan'], 'a')

    def test_salary_does_not_clear_cancellation_uncertainty(self):
        tx, _ = self.salary()
        self.Tx.objects.filter(pk=tx.pk).update(canceled_amount=1000)
        proofs = design_history.verify_budget_history(self.user, MONTHS)
        self.assertEqual(proofs['2026-08']['cancellations'], 'unknown')
        self.assertEqual(proofs['2026-08']['refunds'], 'unknown')

    def test_suggested_cancellation_stays_unknown_with_valid_salary(self):
        from finance.models import TransactionLink
        self.salary()
        original = self.tx(amount=60000, canceled_amount=10000, occurred_on=date(2026, 8, 1))
        event = self.tx(direction='in', transaction_type='income', amount=10000, occurred_on=date(2026, 8, 9))
        TransactionLink.objects.create(user=self.user, from_transaction=original, to_transaction=event,
            relation_type='CANCELLATION', matched_amount=10000, confirmation_state='suggested')
        checks = design_history.verify_budget_history(self.user, MONTHS)
        self.assertEqual(checks['2026-08']['refunds'], 'unknown')
        self.assertEqual(self.selected()[0]['plan'], 'a')

    def test_other_duplicate_uncertainty_remains_and_partial_coverage_cannot_open_b(self):
        self.salary()
        self.Tx.objects.filter(pk=self.food[0].pk).update(source_key=None)
        selection, records = self.selected()
        self.assertEqual(selection['plan'], 'a')
        self.assertEqual(records['2026-08']['normalization_checks']['duplicates'], 'unknown')
        self.Tx.objects.filter(pk=self.food[0].pk).update(source_key='restored')
        self.collection_run.covered_to = date(2026, 9, 30)
        self.collection_run.save(update_fields=['covered_to'])
        selection, records = self.selected()
        self.assertEqual(selection['plan'], 'a')
        self.assertEqual(records['2026-10']['normalization_checks'], CLEAN)
        self.assertFalse(records['2026-10']['coverage_complete'])

    def test_unclassified_amount_does_not_override_the_ten_eleven_count_boundary(self):
        self.food.pop().delete()
        for day in range(15, 27):
            self.tx(occurred_on=date(2026, 10, day), amount=500000, confirmed_category_id='UNCLASSIFIED')
        selection, _ = self.selected()
        self.assertEqual((selection['plan'], selection['max_category_count']), ('a', 10))
        self.tx(occurred_on=date(2026, 10, 28))
        selection, records = self.selected()
        self.assertEqual((selection['plan'], selection['max_category_count']), ('b', 11))
        self.assertEqual(sum(t['net_amount'] for t in records['2026-10']['items'] if t['category'] == 'UNCLASSIFIED'), 6000000)

    def test_verification_is_read_only_and_proof_result_has_no_identifiers(self):
        tx, decision = self.salary()
        with CaptureQueriesContext(connection) as queries:
            proofs = design_history.verify_budget_history(self.user, MONTHS)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        self.assertNotIn(str(tx.pk), str(proofs))
        self.assertNotIn(tx.source_description, str(proofs))
        decision.refresh_from_db()
        self.assertEqual(decision.state, 'confirmed')

    def test_salary_lookup_db_error_keeps_b_closed(self):
        with patch('budgets.salary.known_deposits', side_effect=DatabaseError('synthetic-private-error')):
            out = design_history.verify_budget_history(self.user, MONTHS)
        self.assertTrue(all(v == 'unknown' for row in out.values() for v in row.values()))
        self.assertNotIn('synthetic-private-error', str(out))

    def test_salary_snapshot_must_match_the_inspected_rows_as_well(self):
        from .models import SalaryDecision
        from finance.models import TransactionLink
        from finance.history_verifier import verify_transaction_history
        tx, decision = self.salary()
        original_filter = TransactionLink.objects.filter
        def change_after_rows_were_loaded(*args, **kwargs):
            self.Tx.objects.filter(pk=tx.pk).update(amount=2600000)
            SalaryDecision.objects.filter(pk=decision.pk).update(snapshot_amount=2600000)
            return original_filter(*args, **kwargs)
        with patch.object(TransactionLink.objects, 'filter', side_effect=change_after_rows_were_loaded):
            proofs = verify_transaction_history(self.user, MONTHS, use_confirmed_salary=True)
        self.assertEqual(proofs['2026-08']['refunds'], 'unknown')

    def test_salary_extension_flag_must_be_boolean_before_any_query(self):
        from finance.history_verifier import verify_transaction_history
        for value in (None, 1, 'true'):
            with self.subTest(value=value), self.assertNumQueries(0), self.assertRaises(ValueError):
                verify_transaction_history(self.user, MONTHS, use_confirmed_salary=value)
