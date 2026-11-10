"""Synthetic spare amounts/rates only; no external calls, DB writes or service defaults."""
from copy import deepcopy
from datetime import date
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
from plan_b_history import CATEGORY_CODES, CHECKS
from plan_b_budget import build_priority_history_budget, check_result_with_unallocated


class SpareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')))
        self.enterContext(patch.dict(os.environ, {'LLM_MODEL': 'test-model', 'LANGSMITH_TRACING': 'false'}))
        self.active = [c for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')]
        self.inp = {'target': 500000, 'minimums': {}, 'locks': {}, 'priorities': [
            {'category': c, 'rank': i} for i, c in enumerate(self.active[:3], 1)]}
        self.months = {m: {'monthly_totals': dict.fromkeys(self.active, 100000),
            'coverage_complete': True, 'normalization_checks': dict.fromkeys(CHECKS, 'verified')}
            for m in ('2026-08', '2026-09', '2026-10')}
        self.options = {'as_of': date(2026, 11, 4), 'shortfall_cut_rates': dict.fromkeys(self.active, 25)}

    def run_budget(self, **options):
        return build_priority_history_budget(self.inp, self.months, **{**self.options, **options})

    def assert_conserved(self, out, spare):
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(sum(out['allocations'].values()) + out['total_spare'], self.inp['target'] + spare)
        self.assertEqual(out['spare_remaining'], spare - out['spare_transfer'])
        self.assertEqual(out['total_spare'], out['spare_remaining'] + out['unallocated'])
        check_result_with_unallocated({**self.inp, 'target': out['funded_target']}, out['result'])
        for e in out['explanations']:
            self.assertEqual(sum(c['amount'] for c in e['causes']), e['diff'])

    def test_only_remaining_shortage_is_funded_and_originals_unchanged(self):
        before = deepcopy((self.inp, self.months))
        out = self.run_budget(available_spare=150000)
        self.assert_conserved(out, 150000)
        self.assertEqual(out['spare_transfer'], 100000)
        self.assertEqual(out['funded_target'], 600000)
        self.assertTrue(all(out['allocations'][c] == 75000 for c in self.active))
        self.assertEqual(out['spare_remaining'], 50000)
        self.assertIn('100,000원', out['summary'][-1]['text'])
        self.assertEqual((self.inp, self.months), before)
        self.assertEqual(self.run_budget(available_spare=150000), out)

    def test_valid_cuts_do_not_spend_spare(self):
        self.inp['target'] = 700000
        old = self.run_budget()
        out = self.run_budget(available_spare=150000)
        self.assert_conserved(out, 150000)
        self.assertEqual(out['allocations'], old['allocations'])
        self.assertEqual(out['spare_transfer'], 0)

    def test_insufficient_spare_returns_shortage_without_partial_transfer(self):
        out = self.run_budget(available_spare=50000)
        self.assertEqual(out['status'], 'needs_upper_allocation_review')
        self.assertIsNone(out['allocations'])
        self.assertEqual(out['funding_required'], 100000)
        self.assertEqual(out['funding_shortfall'], 50000)
        self.assertEqual(out['spare_transfer'], 0)
        self.assertEqual(out['spare_remaining'], 50000)

    def test_minimum_locks_restore_and_ml_floor_remain_protected(self):
        self.inp['minimums']['FOOD'] = 95000
        self.inp['locks']['TRANSPORT_CAR'] = 100000
        ml = {c: 90000 if c in self.active else 0 for c in CATEGORY_CODES}
        out = self.run_budget(available_spare=400000, ml_reference=ml, user_protected=['HOUSEHOLD'])
        self.assert_conserved(out, 400000)
        self.assertEqual(out['allocations']['TRANSPORT_CAR'], 100000)
        self.assertEqual(out['allocations']['HOUSEHOLD'], 100000)
        self.assertGreaterEqual(out['allocations']['FOOD'], 95000)
        self.assertTrue(all(out['allocations'][c] >= 90000 for c in self.active))
        self.assertEqual(out['spare_transfer'], 245000)

    def test_all_locks_can_be_funded_but_lock_minimum_conflict_cannot(self):
        self.inp['locks'] = dict.fromkeys(self.active, 100000)
        out = self.run_budget(available_spare=400000)
        self.assert_conserved(out, 400000)
        self.assertEqual(out['spare_transfer'], 300000)
        self.inp['minimums']['FOOD'] = 100001
        out = self.run_budget(available_spare=400000)
        self.assertEqual(out['status'], 'infeasible')
        self.assertEqual(out['spare_transfer'], 0)

    def test_surplus_balance_is_not_reused_as_upstream_spare(self):
        self.inp['target'] = 900000
        out = self.run_budget(available_spare=50000)
        self.assert_conserved(out, 50000)
        self.assertEqual(out['unallocated'], 100000)
        self.assertEqual(out['total_spare'], 150000)
        self.assertEqual(out['spare_transfer'], 0)

    def test_no_funding_for_missing_history_and_invalid_spare_rejected(self):
        for value in (-1, True, 1.5, '10000', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_available_spare'):
                self.run_budget(available_spare=value)
        self.months['2026-08']['coverage_complete'] = False
        out = self.run_budget(available_spare=400000)
        self.assertEqual(out['status'], 'needs_data')
        self.assertEqual(out['spare_transfer'], 0)

    def test_existing_llm_rule_path_forwards_spare(self):
        from plan_b_llm import design_history_budget
        out = design_history_budget(self.inp, self.months, **self.options, available_spare=150000)
        self.assert_conserved(out, 150000)
        self.assertEqual(out['spare_transfer'], 100000)

    def test_forecast_funds_rule_then_band_once_and_fallback_does_not_spend_band_money(self):
        import plan_b_forecast as agent
        from test_plan_b_forecast import ForecastTests
        options = dict(**self.options, target_month=11, forecast_band={'down_percent': 20, 'up_percent': 20},
                       allow_external=True, synthetic=True, api_key='synthetic', available_spare=200000)
        with patch.object(agent, '_request_completion', side_effect=ForecastTests.response) as call:
            out = agent.forecast_budget(self.inp, self.months, **options)
        self.assert_conserved(out, 200000)
        self.assertEqual(out['llm_status'], 'completed')
        self.assertEqual(out['spare_transfer'], 140000)
        self.assertEqual(call.call_args.args[2]['target'], 640000)
        with patch.object(agent, '_request_completion', side_effect=ValueError('private text')):
            failed = agent.forecast_budget(self.inp, self.months, **options)
        self.assert_conserved(failed, 200000)
        self.assertEqual(failed['llm_status'], 'fallback')
        self.assertEqual(failed['spare_transfer'], 100000)
        self.assertNotIn('private text', str(failed))

    def test_forecast_no_call_when_spare_does_not_cover_band(self):
        import plan_b_forecast as agent
        with patch.object(agent, '_request_completion') as call:
            out = agent.forecast_budget(self.inp, self.months, **self.options, target_month=11,
                forecast_band={'down_percent': 20, 'up_percent': 20}, available_spare=120000,
                allow_external=True, synthetic=True, api_key='synthetic')
        self.assertEqual(out['reason'], 'forecast_band_infeasible')
        self.assertEqual(out['funding_shortfall'], 20000)
        self.assertEqual(out['spare_transfer'], 100000)
        self.assert_conserved(out, 120000)
        call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
