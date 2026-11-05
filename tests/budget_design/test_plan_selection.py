"""A/B routing with synthetic normalized history; no DB/network or model inference."""
from copy import deepcopy
from datetime import date
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
from plan_b_history import CHECKS, summarize_history
from plan_selection import select_plan
import compare_plans as runner

AS_OF = date(2026, 11, 5)


def months_with_counts(food=(4, 4, 3), shopping=(1, 1, 0), leisure=(2, 2, 1)):
    months = {}
    for i, month in enumerate(('2026-08', '2026-09', '2026-10')):
        items = []
        for category, counts in [('FOOD', food), ('FASHION_SHOPPING', shopping), ('CULTURE_LEISURE', leisure)]:
            items += [{'occurred_on': month+'-15', 'category': category, 'net_amount': 10000,
                       'transaction_type': 'spending', 'funding': 'living'} for _ in range(counts[i])]
        months[month] = {'coverage_complete': True, 'normalization_checks': dict.fromkeys(CHECKS, 'verified'), 'items': items}
    return months


class PlanSelectionTests(unittest.TestCase):
    def select(self, months):
        return select_plan(summarize_history(months, as_of=AS_OF))

    def test_user_examples_and_exact_boundary(self):
        for counts, plan in [((4, 4, 3), 'b'), ((3, 3, 3), 'a'), ((3, 3, 4), 'a')]:
            with self.subTest(counts=counts):
                choice = self.select(months_with_counts(food=counts))
                self.assertEqual(choice['plan'], plan)
                self.assertEqual(choice['max_category_count'], sum(counts))

    def test_use_max_not_sum_and_not_all_categories_need_eleven(self):
        sparse = months_with_counts(food=(3, 3, 3), shopping=(3, 3, 3), leisure=(3, 3, 3))
        self.assertEqual(self.select(sparse)['plan'], 'a')
        many = months_with_counts(food=(0, 0, 0), shopping=(4, 4, 3), leisure=(0, 0, 0))
        self.assertEqual(self.select(many)['plan'], 'b')

    def test_missing_month_or_incomplete_coverage_selects_a_even_with_high_count(self):
        for mode in ('missing_month', 'coverage', 'refund_unknown'):
            months = months_with_counts(food=(11, 11, 11))
            if mode == 'missing_month':
                del months['2026-08']
            elif mode == 'coverage':
                months['2026-08']['coverage_complete'] = False
            else:
                months['2026-08']['normalization_checks']['refunds'] = 'unknown'
            with self.subTest(mode=mode):
                self.assertEqual(self.select(months)['plan'], 'a')
                self.assertEqual(self.select(months)['reason'], 'history_not_ready')

    def test_no_history_and_verified_empty_history(self):
        self.assertEqual(self.select({})['plan'], 'a')
        choice = self.select(months_with_counts((0, 0, 0), (0, 0, 0), (0, 0, 0)))
        self.assertEqual(choice['plan'], 'a')
        self.assertEqual(choice['max_category_count'], 0)

    def test_monthly_totals_are_not_fabricated_transaction_counts(self):
        months = months_with_counts()
        months['2026-10'].pop('items')
        months['2026-10']['monthly_totals'] = {'FOOD': 1000000}
        choice = self.select(months)
        self.assertEqual(choice['plan'], 'a')
        self.assertEqual(choice['reason'], 'transaction_counts_unavailable')
        self.assertIsNone(choice['max_category_count'])

    def test_income_transfer_fixed_zero_and_unclassified_do_not_raise_category_max(self):
        months = months_with_counts(food=(3, 3, 4), shopping=(0, 0, 0), leisure=(0, 0, 0))
        normal = months['2026-10']['items'][0]
        extras = [dict(normal, transaction_type='income'), dict(normal, transaction_type='transfer'),
                  dict(normal, funding='fixed'), dict(normal, net_amount=0), dict(normal, category='UNCLASSIFIED'),
                  dict(normal, category='ETC')]
        months['2026-10']['items'] += extras * 11
        choice = self.select(months)
        self.assertEqual(choice['plan'], 'a')
        self.assertEqual(choice['max_category_count'], 10)

    def test_count_before_one_off_removal_and_preserve_input(self):
        months = months_with_counts()
        months['2026-10']['items'][0]['net_amount'] = 10000000
        before = deepcopy(months)
        history = summarize_history(months, as_of=AS_OF)
        self.assertTrue(any(c['excluded'] for c in history['one_off_candidates']))
        self.assertEqual(select_plan(history)['category_counts']['FOOD'], 11)
        self.assertEqual(select_plan(history)['plan'], 'b')
        self.assertEqual(months, before)

    def test_invalid_count_cannot_enable_b(self):
        history = summarize_history(months_with_counts(), as_of=AS_OF)
        for value in (True, -1, 11.5):
            history['categories']['FOOD']['monthly_purchase_counts'][0] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_selection_purchase_counts'):
                select_plan(history)


class AutoExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        from test_plan_ab_comparison import ml_response
        self.enterContext(patch.object(runner, 'predict_ml', side_effect=ml_response))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')))
        self.case = runner.test_input()

    def test_sparse_or_missing_history_runs_only_a(self):
        for months in (months_with_counts(food=(3, 3, 4)), {}):
            self.case['months'] = months
            with patch.object(runner, 'run_a', return_value={'status': 'proposed'}) as a, patch.object(runner, 'run_b') as b, patch.object(runner, 'run_forecast') as forecast:
                out = runner.recommend_budget(self.case)
            a.assert_called_once()
            b.assert_not_called()
            forecast.assert_not_called()
            self.assertEqual(out['selection']['plan'], 'a')

    def test_dense_history_runs_only_b_and_preserves_settings(self):
        self.case['months'] = months_with_counts()
        before = deepcopy(self.case)
        with patch.object(runner, 'run_a') as a, patch.object(runner, 'run_b', return_value={'status': 'proposed'}) as b:
            out = runner.recommend_budget(self.case)
        a.assert_not_called()
        b.assert_called_once()
        self.assertEqual(out['selection']['plan'], 'b')
        self.assertEqual(b.call_args.args[0]['input'], before['input'])
        self.assertIn('ml_reference', b.call_args.args[0])
        self.assertEqual(self.case, before)

    def test_forecast_is_used_only_for_b_when_explicitly_enabled(self):
        self.case.update(include_forecast=True, forecast_enabled=True, months=months_with_counts())
        with patch.object(runner, 'run_a') as a, patch.object(runner, 'run_b') as b, patch.object(runner, 'run_forecast', return_value={'status': 'proposed'}) as forecast:
            out = runner.recommend_budget(self.case)
        a.assert_not_called()
        b.assert_not_called()
        forecast.assert_called_once()
        self.assertEqual(out['selected_variant'], 'b_forecast')

    def test_b_failure_does_not_run_a(self):
        self.case['months'] = months_with_counts()
        for failure in ({'status': 'needs_upper_allocation_review'}, RuntimeError('private')):
            with patch.object(runner, 'run_a') as a, patch.object(runner, 'run_b', side_effect=failure if isinstance(failure, Exception) else None, return_value=failure):
                out = runner.recommend_budget(self.case)
            a.assert_not_called()
            self.assertEqual(out['selection']['plan'], 'b')
            self.assertNotEqual(out['result']['status'], 'proposed')
            self.assertNotIn('private', str(out))

    def test_actual_selected_budget_is_valid(self):
        for i, record in enumerate(self.case['months'].values()):
            food = next(t for t in record['items'] if t['category'] == 'FOOD')
            amount = food['net_amount']
            record['items'].remove(food)
            count = (4, 4, 3)[i]
            record['items'] += [dict(food, net_amount=amount//count + (amount % count if j == 0 else 0)) for j in range(count)]
        out = runner.recommend_budget(self.case)
        self.assertEqual(out['selection']['plan'], 'b')
        self.assertEqual(out['result']['status'], 'proposed')
        self.assertEqual(sum(out['result']['allocations'].values()) + out['result']['total_spare'],
                         self.case['input']['target'])

    def test_comparison_keeps_both_results_but_exposes_chosen_result(self):
        out = runner.compare(self.case)
        self.assertEqual(out['selection']['plan'], 'a')
        self.assertIs(out['selected_result'], out['a'])
        self.assertIn('b', out)

    def test_auto_cli_calls_only_the_auto_entry(self):
        import contextlib
        import io
        for status, code in [('proposed', 0), ('error', 2)]:
            answer = {'selection': {'plan': 'a', 'reason': 'sparse_transactions', 'max_category_count': 3},
                      'selected_variant': 'a', 'result': {'status': status}}
            with patch.object(runner, 'recommend_budget', return_value=answer) as auto, patch.object(runner, 'compare') as compare, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(['--auto']), code)
            auto.assert_called_once()
            compare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
