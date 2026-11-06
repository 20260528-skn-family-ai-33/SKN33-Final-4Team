"""Synthetic Plan B stage-1 checks; no credentials, network or database."""
from copy import deepcopy
from datetime import date
from fractions import Fraction
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/budget_design/plan_b_history.py'
spec = importlib.util.spec_from_file_location('plan_b_history', SCRIPT)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


def fixture():
    return {month: {
        'items': [], 'coverage_complete': True,
        'normalization_checks': dict.fromkeys(history.CHECKS, 'verified'),
        'actual_income': 3_000_000, 'actual_savings': 900_000,
    } for month in ('2026-08', '2026-09', '2026-10')}


def purchase(month, amount, category='FOOD', funding='living', **extra):
    return {'occurred_on': month + '-15', 'net_amount': amount,
            'transaction_type': 'spending', 'category': category, 'funding': funding, **extra}


class HistoryTests(unittest.TestCase):
    def summarize(self, data):
        return history.summarize_history(data, as_of=date(2026, 11, 2))

    def test_completed_months_exclude_current_month_and_cross_year(self):
        self.assertEqual(history.completed_months(date(2026, 1, 1)),
                         ['2025-10', '2025-11', '2025-12'])
        self.assertEqual(history.completed_months(date(2024, 3, 31)),
                         ['2023-12', '2024-01', '2024-02'])

    def test_monthly_average_changes_and_frequency_use_net_spending(self):
        data = fixture()
        for month, amount in zip(data, (100_001, 200_000, 300_000)):
            data[month]['items'] = [purchase(month, amount), purchase(month, 0)]
        result = self.summarize(data)
        self.assertEqual(result['status'], 'ready')
        food = result['categories']['FOOD']
        self.assertEqual(food['average'], Fraction(600_001, 3))
        self.assertEqual(food['month_changes'], [99_999, 100_000])
        self.assertEqual(food['monthly_purchase_counts'], [1, 1, 1])

    def test_pattern_values_and_median_do_not_assign_labels(self):
        data = fixture()
        for month, amount in zip(data, (100, 200, 600)):
            data[month]['items'] = [purchase(month, amount)]
        row = self.summarize(data)['categories']['FOOD']
        self.assertEqual(row['average'], 300)
        self.assertEqual(row['median'], 200)
        self.assertEqual(row['previous_two_month_average'], 150)
        self.assertEqual(row['latest_growth_rate'], 3)
        self.assertEqual(row['max_to_median_ratio'], 3)
        self.assertNotIn('label', row)

    def test_zero_denominators_are_unknown_not_zero_or_infinity(self):
        data = fixture()
        data['2026-10']['items'] = [purchase('2026-10', 100_000)]
        result = self.summarize(data)
        for code in ('FOOD', 'EDUCATION'):
            row = result['categories'][code]
            self.assertIsNone(row['latest_growth_rate'])
            self.assertIsNone(row['max_to_median_ratio'])
            self.assertEqual(row['spike_ratio_reason'], 'zero_median')
        self.assertEqual(result['categories']['FOOD']['median'], 0)
        self.assertIsNone(result['discretionary_share_trend'][0]['share'])

    def test_discretionary_monthly_share_includes_full_variable_denominator(self):
        data = fixture()
        for month, amount in zip(data, (100, 200, 300)):
            data[month]['items'] = [purchase(month, amount, 'FASHION_SHOPPING'),
                purchase(month, 100, 'BEAUTY'), purchase(month, 100, 'CULTURE_LEISURE'),
                purchase(month, 100, 'UNCLASSIFIED'), purchase(month, 100, 'TRAVEL_LODGING'),
                purchase(month, 1000, 'HOUSING_TELECOM', 'fixed')]
        rows = self.summarize(data)['discretionary_share_trend']
        self.assertEqual([r['month'] for r in rows], list(data))
        self.assertEqual([r['share'] for r in rows], [Fraction(3, 5), Fraction(2, 3), Fraction(5, 7)])

    def test_missing_history_does_not_emit_complete_pattern_trend(self):
        data = fixture()
        del data['2026-09']
        out = self.summarize(data)
        self.assertEqual(out['categories'], {})
        self.assertEqual(out['discretionary_share_trend'], [])

    def test_fixed_travel_and_unclassified_keep_separate_scopes(self):
        data = fixture()
        data['2026-10']['items'] = [
            purchase('2026-10', 500_000, 'HOUSING_TELECOM', 'fixed'),
            purchase('2026-10', 200_000, 'TRAVEL_LODGING'),
            purchase('2026-10', 50_000, 'UNCLASSIFIED'),
            purchase('2026-10', 30_000, 'ETC'),
        ]
        result = self.summarize(data)
        october = result['monthly']['2026-10']
        self.assertEqual(october['all_spending']['spent_total'], 780_000)
        self.assertEqual(october['variable_spending']['spent_total'], 280_000)
        self.assertEqual(result['categories']['HOUSING_TELECOM']['average'], 0)
        self.assertEqual(result['categories']['TRAVEL_LODGING']['average'], Fraction(200_000, 3))
        self.assertEqual(result['categories']['UNCLASSIFIED']['average'], Fraction(50_000, 3))
        self.assertEqual(result['categories']['FOOD']['average'], 0)

    def test_missing_month_does_not_become_zero(self):
        data = fixture()
        del data['2026-09']
        result = self.summarize(data)
        self.assertEqual(result['status'], 'needs_data')
        self.assertEqual(result['categories'], {})
        self.assertEqual(result['missing_data'], [{'month': '2026-09', 'reason': 'missing_month'}])
        self.assertEqual(result['overspending_index']['status'], 'unavailable')

    def test_empty_but_verified_month_is_zero_not_missing(self):
        result = self.summarize(fixture())
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['categories']['FOOD']['average'], 0)

    def test_unknown_coverage_and_refunds_block_comparison(self):
        data = fixture()
        del data['2026-08']['coverage_complete']
        data['2026-09']['normalization_checks']['refunds'] = 'unresolved'
        data['2026-10']['normalization_checks'] = {}
        result = self.summarize(data)
        self.assertEqual(result['status'], 'needs_data')
        self.assertEqual(len(result['missing_data']), 5)
        self.assertEqual(result['categories'], {})

    def test_future_month_and_wrong_transaction_month_are_rejected(self):
        for mutation in ('extra_month', 'wrong_date'):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                data = fixture()
                if mutation == 'extra_month':
                    data['2026-11'] = data['2026-10']
                else:
                    data['2026-10']['items'] = [purchase('2026-11', 10_000)]
                self.summarize(data)

    def test_duplicate_id_is_not_counted_twice(self):
        data = fixture()
        data['2026-10']['items'] = [purchase('2026-10', 10_000, transaction_id='same')] * 2
        with self.assertRaisesRegex(ValueError, 'duplicate_or_empty_transaction_id'):
            self.summarize(data)

    def test_invalid_money_category_and_funding_are_rejected(self):
        for changes in ({'net_amount': True}, {'net_amount': -1}, {'net_amount': 0.5},
                        {'category': 'UNKNOWN'}, {'funding': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                data = fixture()
                data['2026-10']['items'] = [purchase('2026-10', 1)]
                data['2026-10']['items'][0].update(changes)
                self.summarize(data)

    def test_no_input_mutation_or_network(self):
        data = fixture()
        data['2026-10']['items'] = [purchase('2026-10', 123, merchant_name='synthetic merchant')]
        original = deepcopy(data)
        with patch('socket.socket', side_effect=AssertionError('No network')):
            result = self.summarize(data)
        self.assertEqual(data, original)
        self.assertNotIn('synthetic merchant', repr(result))


class IndexTests(unittest.TestCase):
    def test_exact_boundaries(self):
        for savings, band in ((501, 'frugal'), (500, 'adequate'), (301, 'adequate'),
                              (300, 'review_overspending'), (1, 'review_overspending'),
                              (0, 'review_cashflow'), (-100, 'review_cashflow')):
            with self.subTest(savings=savings):
                result = history.overspending_index([
                    {'actual_income': 1000, 'actual_savings': savings} for _ in range(3)])
                self.assertEqual(result['band'], band)
                self.assertEqual(result['review_reductions'], savings <= 300)

    def test_ratio_of_totals_not_mean_of_monthly_ratios(self):
        result = history.overspending_index([
            {'actual_income': 100, 'actual_savings': 0},
            {'actual_income': 900, 'actual_savings': 900},
            {'actual_income': 0, 'actual_savings': 0},
        ])
        self.assertEqual(result['value'], 0.1)

    def test_missing_actual_savings_does_not_use_planned_savings(self):
        data = fixture()
        del data['2026-09']['actual_savings']
        data['2026-09']['savings_floor'] = 900_000
        result = history.summarize_history(data, as_of=date(2026, 11, 2))
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['overspending_index']['reason'], 'missing_actual_finances')

    def test_zero_income_is_unavailable_and_zero_savings_is_valid(self):
        result = history.overspending_index([{'actual_income': 0, 'actual_savings': 0}] * 3)
        self.assertEqual(result['reason'], 'zero_income')
        result = history.overspending_index([{'actual_income': 100, 'actual_savings': 0}] * 3)
        self.assertEqual(result['value'], 1)
        self.assertNotIn('spending_exceeds_income', result)

    def test_invalid_financial_values_and_month_count(self):
        for rows in ([], [{'actual_income': True, 'actual_savings': 0}] * 3,
                     [{'actual_income': -1, 'actual_savings': 0}] * 3,
                     [{'actual_income': 100, 'actual_savings': 1.5}] * 3):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                history.overspending_index(rows)


if __name__ == '__main__':
    unittest.main()
