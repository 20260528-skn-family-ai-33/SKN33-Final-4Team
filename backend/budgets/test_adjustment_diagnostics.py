"""Observed history remains useful without treating missing days as no spending."""
from copy import deepcopy
from fractions import Fraction
import unittest

from budgets.adjustment_core.diagnostics import build_history, resolve_history
from budgets.adjustment_core.protection import daily_averages, expected_need, remaining_days, usage_floors


class ObservedDiagnosticsTest(unittest.TestCase):
    categories = {'FOOD': 100000, 'EDUCATION': 50000, 'TRANSPORT_CAR': 100000}
    checks = dict.fromkeys(('duplicates', 'cancellations', 'refunds'), 'verified')

    def record(self, identifier, date, amount, category='FOOD'):
        return {'transaction_id': identifier, 'occurred_on': date, 'net_amount': amount,
                'transaction_type': 'spending', 'funding': 'living', 'category': category}

    def context(self, history, as_of='2026-11-17', today='2026-11-17'):
        return {'allocations': dict(self.categories), 'spent': {'FOOD': 20000}, 'today': today,
                'diagnostic_inputs': {'as_of': as_of, 'remaining_days': 10, 'history': history}}

    def test_one_observed_day_is_usable_without_fourteen_days(self):
        history = build_history('2026-11-17', self.categories,
            [self.record('one', '2026-11-16', 20000)], ['2026-11-16'], self.checks)
        self.assertEqual(history['status'], 'ready')
        self.assertEqual(history['observation']['days'], 1)
        self.assertTrue(history['observation']['limited'])
        self.assertEqual(daily_averages(self.context(history))['FOOD'], 20000)
        self.assertEqual(expected_need(self.context(history), 'FOOD'), 120000)

    def test_old_partial_history_keeps_gaps_and_unknown_categories_unknown(self):
        history = build_history('2026-11-17', self.categories, [
            self.record('one', '2026-11-10', 20000),
            self.record('two', '2026-11-12', 10000, 'EDUCATION'),
            self.record('three', '2026-11-14', 40000),
        ], [], dict.fromkeys(self.checks, 'unknown'))
        self.assertEqual(history['status'], 'observed')
        self.assertEqual(history['dates'], ['2026-11-10', '2026-11-12', '2026-11-14'])
        self.assertEqual(history['observation']['through'], '2026-11-14')
        self.assertEqual(history['observation']['category_days'], {'FOOD': 2, 'EDUCATION': 1, 'TRANSPORT_CAR': 0})
        self.assertEqual(daily_averages(self.context(history)), {'FOOD': 30000, 'EDUCATION': 10000})
        self.assertIsNone(expected_need(self.context(history), 'TRANSPORT_CAR'))
        self.assertNotIn('TRANSPORT_CAR', usage_floors(self.context(history)))

    def test_verified_zero_spending_days_remain_in_the_denominator(self):
        history = build_history('2026-11-17', self.categories,
            [self.record('one', '2026-11-12', 20000)], ['2026-11-11'], self.checks)
        self.assertEqual(history['status'], 'observed')
        self.assertEqual(daily_averages(self.context(history))['FOOD'], Fraction(20000, 2))
        self.assertEqual(daily_averages(self.context(history))['EDUCATION'], 0)

    def test_snapshot_age_does_not_discard_samples_or_freeze_remaining_days(self):
        history = build_history('2026-11-15', self.categories,
            [self.record('one', '2026-11-14', 20000)], ['2026-11-14'], self.checks)
        context = self.context(history, as_of='2026-11-15')
        self.assertEqual(daily_averages(context)['FOOD'], 20000)
        self.assertEqual(remaining_days(context), 8)
        self.assertEqual(expected_need(context, 'FOOD'), 80000)

    def test_unclassified_spending_does_not_erase_known_categories(self):
        history = build_history('2026-11-17', self.categories, [
            self.record('one', '2026-11-16', 20000),
            self.record('two', '2026-11-16', 10000, 'UNKNOWN'),
        ], ['2026-11-16'], self.checks)
        self.assertEqual(history['status'], 'observed')
        self.assertEqual(history['complete_dates'], [])
        self.assertEqual(daily_averages(self.context(history)), {'FOOD': 20000})

    def test_no_records_and_no_observed_days_cannot_create_a_forecast(self):
        history = build_history('2026-11-17', self.categories, [], [], self.checks)
        self.assertEqual(history['status'], 'unknown')
        self.assertEqual(history['reason'], 'no_observed_days')
        self.assertIsNone(daily_averages(self.context(history)))

    def test_invalid_money_duplicate_ids_and_invalid_dates_stay_rejected(self):
        valid = self.record('one', '2026-11-16', 20000)
        for records in ([valid, valid], [{**valid, 'net_amount': -1}], [{**valid, 'net_amount': True}]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                build_history('2026-11-17', self.categories, records, [], self.checks)
        history = build_history('2026-11-17', self.categories, [valid], [], self.checks)
        for change in ('future', 'missing_category', 'negative', 'fake_complete_date'):
            altered = deepcopy(history)
            if change == 'future':
                altered['dates'] = ['2026-11-18']
            elif change == 'missing_category':
                altered['samples'][0].pop('FOOD')
            elif change == 'negative':
                altered['samples'][0]['FOOD'] = -1
            else:
                altered['complete_dates'] = ['2026-11-15']
            with self.subTest(change=change), self.assertRaises(ValueError):
                resolve_history(altered, '2026-11-17', self.categories)

    def test_snapshot_from_the_future_is_not_used(self):
        history = build_history('2026-11-18', self.categories,
            [self.record('one', '2026-11-17', 20000)], [], self.checks)
        self.assertIsNone(daily_averages(self.context(history, as_of='2026-11-18')))
