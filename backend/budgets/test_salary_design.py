"""Salary residue uses the real A/B engines, with synthetic model/history inputs."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import design_service, salary_design
from .design_candidates import CATEGORY_NAMES
from .recommend import ALLOCATION_CODES
from .test_design_candidates import inp, priorities
from .test_design_service import history

approved_requests = salary_design._approved_requests


class SalaryDesignTest(SimpleTestCase):
    def setUp(self):
        self.context = {'base_budget_id': 'base', 'cycle_id': 'cycle', 'monthly_income': 4000000,
            'allocations': dict.fromkeys(ALLOCATION_CODES, 100000), 'minimums': {}, 'locks': {},
            'category_targets': {}}
        self.snapshot = {'engine_input': inp(priorities=priorities('FOOD', 'TRANSPORT_CAR', 'EDUCATION')),
                         'etc': 0, 'minimums': {}, 'locks': {}, 'category_targets': {}}
        self.enterContext(override_settings(BUDGET_DESIGN_POLICY={'a': {}}, BUDGET_HISTORY_VERIFIER=None,
                                            BUDGET_DESIGN_ENABLED=True, BUDGET_DESIGN_EXTERNAL_ENABLED=False))
        self.enterContext(patch.object(salary_design.BudgetCycle.objects, 'get', return_value=SimpleNamespace(pk='cycle')))
        self.snapshot_reader = self.enterContext(patch.object(salary_design, 'budget_input_snapshot',
                                                              side_effect=lambda *a, **k: deepcopy(self.snapshot)))
        self.prior_requests = self.enterContext(patch.object(salary_design, '_approved_requests', return_value=set()))
        self.history = self.enterContext(patch.object(design_service, 'history_for_user', return_value={}))
        self.prediction = {'ratios': {name: .5 if code == 'FOOD' else .05 for code, name in CATEGORY_NAMES.items()},
                           'source': 'model', 'artifact_id': 'synthetic', 'model_checksum': 'a' * 64,
                           'used_fallback': False}
        self.model = self.enterContext(patch.object(design_service, 'predict_ml', return_value=self.prediction))
        self.enterContext(patch('budgets.price_reference.price_context', return_value={'status': 'disabled'}))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))

    def review(self, amount=300000, targets=None, explicit=(), **kwargs):
        return salary_design.review_remaining(None, self.context, targets or {}, explicit, amount,
                                               today=date(2026, 11, 5), **kwargs)

    def test_a_uses_updated_model_need_instead_of_inflating_all_existing_categories(self):
        before = deepcopy(self.context)
        out = self.review()
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['selection']['plan'], 'a')
        self.assertEqual(out['allocations'], {'FOOD': 300000})
        self.assertEqual(out['unallocated'], 0)
        self.assertEqual(self.model.call_args.args[0]['disposable_income'], 4000000)
        self.assertEqual(self.context, before)

    def test_model_uses_confirmed_cycle_income_instead_of_different_profile_salary(self):
        self.context.update(funding_amount=3500000, effective_income=3500000, monthly_income=4000000)
        out = self.review()
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(self.model.call_args.args[0]['disposable_income'], 3500000)
        self.context['salary_review'] = {'income_after': 3750000}
        self.review()
        self.assertEqual(self.model.call_args.args[0]['disposable_income'], 3750000)

    @override_settings(BUDGET_DESIGN_EXTERNAL_ENABLED=True)
    def test_a_external_selection_uses_same_feature_flag_and_requires_current_consent(self):
        def selected_base(inputs, engine, **kwargs):
            base = next(candidate for candidate in engine['candidates'] if candidate['id'] == 'base')
            return {'allocations': base['result']['allocations'], 'explanations': base['explanations'],
                    'selection_source': 'llm_tools'}

        with patch.object(design_service, 'has_current_consent', return_value=False) as consent, \
                patch.object(design_service, 'choose_budget', side_effect=selected_base) as choose:
            self.assertEqual(self.review()['status'], 'ready')
            choose.assert_not_called()
            consent.return_value = True
            out = self.review(targets={'EDUCATION': 600000}, explicit=['EDUCATION'])
            choose.assert_called_once()
            self.assertEqual(out['allocations'], {'FOOD': 300000})
            self.assertEqual(choose.call_args.args[0]['locks']['EDUCATION'], 600000)

    @override_settings(BUDGET_DESIGN_EXTERNAL_ENABLED=True,
                       BUDGET_DESIGN_POLICY={'a': {}, 'b_forecast_band': {'down_percent': 20, 'up_percent': 20}})
    def test_consented_b_forecast_can_reconsider_need_within_server_bounds(self):
        self.history.return_value = history()
        self.prediction['ratios'] = dict.fromkeys(CATEGORY_NAMES.values(), .1)
        self.context['allocations']['FOOD'] = 50000

        def forecast(name, schema, payload, *args, **kwargs):
            amounts = {code: max(100000, bound['lower']) for code, bound in payload['bounds'].items()}
            amounts['HOUSEHOLD'] = 110000
            return {'allocations': amounts,
                    'reasons': {code: {'code': 'keep', 'text': '확인된 소비 흐름을 반영했어요.'} for code in amounts}}

        with patch.object(design_service, 'has_current_consent', return_value=True), \
                patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key', 'LLM_MODEL': 'synthetic-test-model'}), \
                patch('plan_b_forecast._request_completion', side_effect=forecast) as completion:
            out = self.review()
        completion.assert_called_once()
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['selection']['plan'], 'b')
        self.assertEqual(out['allocations'], {'FOOD': 50000, 'HOUSEHOLD': 10000})
        self.assertEqual(out['unallocated'], 240000)

    @override_settings(BUDGET_DESIGN_EXTERNAL_ENABLED=True,
                       BUDGET_DESIGN_POLICY={'a': {}, 'b_forecast_band': {'down_percent': 20, 'up_percent': 20}})
    def test_failed_b_external_forecast_retains_validated_rule_result(self):
        self.history.return_value = history()
        self.prediction['ratios'] = dict.fromkeys(CATEGORY_NAMES.values(), .1)
        self.context['allocations']['FOOD'] = 50000
        with patch.object(design_service, 'has_current_consent', return_value=True), \
                patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key', 'LLM_MODEL': 'synthetic-test-model'}), \
                patch('plan_b_forecast._request_completion', side_effect=RuntimeError('synthetic failure')) as completion:
            out = self.review()
        self.assertEqual(completion.call_count, 2)
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['allocations'], {'FOOD': 50000})
        self.assertEqual(out['unallocated'], 250000)

    def test_failed_a_external_selection_retains_validated_rule_result(self):
        self.prediction['ratios'] = dict.fromkeys(CATEGORY_NAMES.values(), .1)
        rule = self.review()
        with override_settings(BUDGET_DESIGN_EXTERNAL_ENABLED=True), \
                patch.object(design_service, 'has_current_consent', return_value=True), \
                patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-test-key'}), \
                patch('budgets.design_selection._request_action', side_effect=RuntimeError('synthetic failure')) as completion:
            out = self.review()
        completion.assert_called_once()
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['allocations'], rule['allocations'])
        self.assertEqual(out['unallocated'], rule['unallocated'])

    def test_request_and_savings_are_reserved_before_remaining_salary_is_reviewed(self):
        targets = {'EDUCATION': 600000, 'SAVINGS': 800000}
        out = self.review(targets=targets, explicit=['EDUCATION'])
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['allocations'], {'FOOD': 300000})
        self.assertNotIn('EDUCATION', out['allocations'])
        self.assertNotIn('SAVINGS', out['allocations'])
        self.assertEqual(self.model.call_args.args[0]['living_budget_amount'], 1600000)
        self.assertEqual(targets, {'EDUCATION': 600000, 'SAVINGS': 800000})

    def test_b_is_selected_from_current_verified_history_and_only_fills_need(self):
        self.history.return_value = history()
        self.prediction['ratios'] = dict.fromkeys(CATEGORY_NAMES.values(), .1)
        self.context['allocations']['FOOD'] = 50000
        out = self.review()
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['selection']['plan'], 'b')
        self.assertEqual(out['allocations'], {'FOOD': 50000})
        self.assertEqual(out['unallocated'], 250000)

    def test_b_does_not_reduce_previously_approved_amounts_that_exceed_history(self):
        self.history.return_value = history()
        self.prediction['ratios'] = dict.fromkeys(CATEGORY_NAMES.values(), .1)
        self.context['allocations']['FOOD'] = 400000
        out = self.review()
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['allocations'], {})
        self.assertEqual(out['unallocated'], 300000)

    def test_old_direct_adjustments_profile_targets_locks_exclusions_and_unused_zero_stay_fixed(self):
        for protection in ('approved_request', 'profile_target', 'lock', 'excluded', 'unused_zero'):
            with self.subTest(protection=protection):
                self.prior_requests.return_value = {'FOOD'} if protection == 'approved_request' else set()
                self.context.update(category_targets={'FOOD': 100000} if protection == 'profile_target' else {},
                                    locks={'FOOD': 100000} if protection == 'lock' else {})
                self.context['allocations']['FOOD'] = 0 if protection == 'unused_zero' else 100000
                out = self.review(excluded=['FOOD'] if protection == 'excluded' else [])
                self.assertEqual(out['status'], 'ready')
                self.assertNotIn('FOOD', out['allocations'])
                self.assertTrue(all(value >= 0 for value in out['allocations'].values()))
                self.assertEqual(sum(out['allocations'].values()) + out['unallocated'], 300000)

    def test_cleared_goal_can_be_reconsidered_and_new_explicit_request_still_wins(self):
        self.context['category_targets'] = {'FOOD': 100000}
        self.prior_requests.return_value = {'FOOD'}
        out = self.review(clear_targets=['FOOD'])
        self.assertEqual(out['allocations'], {'FOOD': 300000})
        out = self.review(targets={'FOOD': 200000}, explicit=['FOOD'], clear_targets=['FOOD'])
        self.assertNotIn('FOOD', out['allocations'])

    def test_missing_model_or_unverified_fallback_keeps_all_remaining_salary_in_spare(self):
        self.model.side_effect = ValueError('private environment missing')
        out = self.review()
        self.assertEqual((out['status'], out['reason']), ('needs_review', 'ml_unavailable'))
        self.assertEqual(out['allocations'], {})
        self.assertEqual(out['unallocated'], 300000)
        self.assertNotIn('private', str(out))
        self.model.side_effect = None
        self.prediction['used_fallback'] = True
        out = self.review()
        self.assertEqual((out['status'], out['reason']), ('needs_review', 'ml_unavailable'))
        self.assertEqual(out['unallocated'], 300000)

    def test_no_remainder_or_all_categories_preserved_does_not_run_model(self):
        self.assertEqual(self.review(0)['status'], 'skipped')
        out = self.review(excluded=['LIVING'])
        self.assertEqual(out['status'], 'skipped')
        self.assertEqual(out['unallocated'], 300000)
        self.snapshot_reader.assert_not_called()
        self.model.assert_not_called()

    def test_invalid_review_snapshot_preserves_remainder_but_missing_owned_cycle_is_not_hidden(self):
        self.snapshot_reader.side_effect = ValueError('invalid_saved_profile')
        out = self.review()
        self.assertEqual((out['status'], out['reason']), ('needs_review', 'salary_design_inputs_unavailable'))
        self.assertEqual((out['allocations'], out['unallocated']), ({}, 300000))
        with patch.object(salary_design.BudgetCycle.objects, 'get', side_effect=salary_design.BudgetCycle.DoesNotExist):
            with self.assertRaises(salary_design.BudgetCycle.DoesNotExist):
                self.review()

    def test_approved_request_lookup_follows_own_cycle_ancestry_and_ignores_automatic_rows(self):
        rows = [
            {'pk': 'base', 'base_budget_id': 'earlier', 'approved_at': date(2026, 11, 1),
             'validation_summary': {'adjustment': {'result': {'requests': {'items': [
                 {'category': 'FOOD', 'automatic': False}, {'category': 'BEAUTY', 'automatic': True}]}}}}},
            {'pk': 'earlier', 'base_budget_id': None, 'approved_at': date(2026, 10, 1),
             'validation_summary': {'adjustment': {'result': {'requests': {'items': [{'category': 'EDUCATION'}]}}}}},
            {'pk': 'unrelated', 'base_budget_id': None, 'approved_at': date(2026, 11, 1),
             'validation_summary': {'adjustment': {'result': {'requests': {'items': [{'category': 'HOUSEHOLD'}]}}}}},
        ]
        with patch.object(salary_design.Budget.objects, 'filter') as query:
            query.return_value.values.return_value = rows
            self.assertEqual(approved_requests(None, self.context), {'FOOD', 'EDUCATION'})
            query.assert_called_once_with(user=None, cycle_id='cycle')

    def test_previously_approved_goal_release_is_not_restored_by_older_requests(self):
        rows = [
            {'pk': 'base', 'base_budget_id': 'earlier', 'approved_at': date(2026, 11, 1),
             'validation_summary': {'adjustment': {'approval': {'target_changes': {'FOOD': None}}}}},
            {'pk': 'earlier', 'base_budget_id': None, 'approved_at': date(2026, 10, 1),
             'validation_summary': {'adjustment': {'result': {'requests': {'items': [{'category': 'FOOD'}]}}}}},
        ]
        with patch.object(salary_design.Budget.objects, 'filter') as query:
            query.return_value.values.return_value = rows
            self.assertEqual(approved_requests(None, self.context), set())

    def test_existing_odd_won_direct_amount_and_other_reservation_are_not_rounded_away(self):
        self.context['allocations']['FOOD'] = 100003
        self.context['allocations']['ETC'] = 3176
        self.context['locks']['FOOD'] = 100003
        out = self.review()
        self.assertEqual(out['status'], 'ready')
        self.assertNotIn('FOOD', out['allocations'])
        self.assertNotIn('ETC', out['allocations'])
        self.assertEqual(sum(out['allocations'].values()) + out['unallocated'], 300000)
