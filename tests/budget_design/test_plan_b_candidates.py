"""Synthetic candidate hypotheses; no production policy values, DB or network."""
from copy import deepcopy
from datetime import date
from fractions import Fraction
import os
import random
import unittest
from unittest.mock import patch

from plan_b_budget import build_priority_history_budget
from plan_b_candidates import _candidate
from plan_b_history import CATEGORY_CODES
from test_plan_b_budget import inputs
from test_plan_b_history import fixture, purchase


def records():
    months = fixture()
    for month, row in months.items():
        row['items'] = [purchase(month, 25000, transaction_id=f'{month}-known-{i}') for i in range(4)]
        row['items'] += [purchase(month, 25000, 'UNCLASSIFIED', transaction_id=f'{month}-hidden-{i}',
            suggestion_candidates=[{'category': 'FOOD', 'confidence': .8}], suggestion_source='ml') for i in range(2)]
    return months


class CandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))

    def build(self, inp=None, months=None, **options):
        return build_priority_history_budget(inp or inputs(target=400000), months or records(),
            as_of=date(2026, 11, 6), shortfall_cut_rates=dict.fromkeys(CATEGORY_CODES, 100), **options)

    def test_recalculation_adds_only_from_existing_unallocated_preserves_input_and_history(self):
        data, months = inputs(target=400000), records()
        before = deepcopy((data, months))
        out = self.build(data, months, available_spare=200000)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['history']['categories']['FOOD']['baseline_amount'], 100000)
        self.assertEqual(out['history']['categories']['UNCLASSIFIED']['average'], 50000)
        self.assertEqual(out['allocations']['FOOD'], 150000)
        self.assertEqual(out['unallocated'], 250000)
        self.assertEqual(out['spare_transfer'], 0)
        self.assertEqual(out['total_spare'], 450000)
        self.assertEqual(out['candidate_adjustment']['status'], 'applied')
        self.assertEqual((data, months), before)
        for e in out['explanations']:
            self.assertEqual(sum(c['amount'] for c in e['causes']), e['diff'])
        cause = next(c for c in out['explanations'][0]['causes'] if c['code'] == 'unclassified_candidate_adjustment')
        self.assertEqual(cause['amount'], 50000)
        self.assertNotIn('hidden-', str(out))

    def test_unknown_size_does_not_disable_b_and_unscored_transactions_stay_unknown(self):
        months = records()
        for month, row in months.items():
            row['items'].append(purchase(month, 2000000, 'UNCLASSIFIED'))
        out = self.build(months=months)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['allocations']['FOOD'], 150000)
        self.assertEqual(out['history']['categories']['UNCLASSIFIED']['average'], 2050000)

    def test_candidate_hypothesis_reruns_dominance_instead_of_adding_every_hidden_amount(self):
        months = fixture()
        for month, row in months.items():
            row['items'] = [purchase(month, 10000, 'MEDICAL_HEALTH')]
        months['2026-09']['items'].append(purchase('2026-09', 170000, 'UNCLASSIFIED',
            suggestion_candidates=[{'category': 'MEDICAL_HEALTH', 'confidence': .9}]))
        out = self.build(months=months)
        self.assertEqual(out['allocations']['MEDICAL_HEALTH'], 10000)
        self.assertEqual(out['candidate_adjustment']['status'], 'no_positive_difference')

    def test_recurrence_and_user_reinclusion_still_apply_in_shadow_calculation(self):
        months = fixture()
        for month, row in months.items():
            row['items'] = [purchase(month, 10000, 'MEDICAL_HEALTH', merchant_key='synthetic-hospital')]
        months['2026-09']['items'].append(purchase('2026-09', 170000, 'UNCLASSIFIED',
            merchant_key='synthetic-hospital', suggestion_candidates=[{'category': 'MEDICAL_HEALTH', 'confidence': .9}]))
        out = self.build(months=months)
        self.assertEqual(out['candidate_adjustment']['status'], 'applied')
        self.assertNotIn('synthetic-hospital', str(out))

    def test_invalid_ambiguous_or_mixed_candidates_are_not_assumed(self):
        for candidates in (None, [], [{'category': 'FOOD', 'confidence': None}],
                [{'category': 'FOOD', 'confidence': True}], [{'category': 'FOOD', 'confidence': float('nan')}],
                [{'category': 'UNKNOWN', 'confidence': .9}], [{'category': 'FOOD', 'confidence': .6}]*2,
                [{'category': 'FOOD', 'confidence': .7}, {'category': 'BEAUTY', 'confidence': .7}],
                [{'category': 'FOOD', 'confidence': .5}, {'category': 'BEAUTY', 'confidence': .5}]):
            with self.subTest(candidates=candidates):
                self.assertIsNone(_candidate({'suggestion_candidates': candidates}))
        self.assertIsNone(_candidate({'suggestion_source': 'llm',
            'suggestion_candidates': [{'category': 'FOOD', 'confidence': .9}]}))

    def test_shadow_calculation_preserves_reinclusion_and_decisions_without_db_ids(self):
        from plan_b_history import summarize_history
        months = fixture()
        for month in ('2026-08', '2026-09'):
            months[month]['items'] = [purchase(month, 10000, 'MEDICAL_HEALTH'),
                purchase(month, 10000, 'UNCLASSIFIED', suggestion_candidates=[
                    {'category': 'MEDICAL_HEALTH', 'confidence': .9}])]
        months['2026-09']['items'].append(purchase('2026-09', 170000, 'MEDICAL_HEALTH'))
        candidates = summarize_history(months, as_of=date(2026, 11, 6))['one_off_candidates']
        identifier = candidates[0]['id']
        normal = self.build(months=months)
        for options in ({'user_reincluded_one_offs': [identifier]},
                        {'one_off_decisions': {identifier: 'recurring'}}):
            out = self.build(months=months, **options)
            self.assertGreater(out['allocations']['MEDICAL_HEALTH'], normal['allocations']['MEDICAL_HEALTH'])
            self.assertNotIn(identifier, out['history']['stale_reincluded_ids'])

    def test_insufficient_remainder_goes_to_higher_priority_before_unranked(self):
        months = records()
        for month, row in months.items():
            row['items'].append(purchase(month, 40000, 'BEAUTY'))
            row['items'].append(purchase(month, 50000, 'UNCLASSIFIED', suggestion_candidates=[
                {'category': 'BEAUTY', 'confidence': .9}]))
        out = self.build(inputs(target=160000), months)
        self.assertEqual(out['allocations']['FOOD'], 120000)
        self.assertEqual(out['allocations']['BEAUTY'], 40000)
        self.assertEqual(out['unallocated'], 0)

    def test_all_active_locks_with_unknown_spending_keep_remainder_feasible(self):
        locks = {c: 0 for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')}
        locks['FOOD'] = 100000
        months = records()
        for month, row in months.items():
            row['items'].append(purchase(month, 1000000, 'UNCLASSIFIED'))
        out = self.build(inputs(target=400000, locks=locks), months)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['allocations']['FOOD'], 100000)
        self.assertEqual(out['unallocated'], 300000)

    def test_missing_fixed_cancelled_and_inactive_candidates_do_not_add_budget(self):
        months = records()
        for row in months.values():
            for item in row['items']:
                item.pop('suggestion_candidates', None)
        base = self.build(months=months)
        for mode in ('fixed', 'cancelled', 'travel', 'housing'):
            copy = deepcopy(months)
            for row in copy.values():
                for item in row['items']:
                    if item['category'] == 'UNCLASSIFIED':
                        item['suggestion_candidates'] = [{'category': {'travel': 'TRAVEL_LODGING',
                            'housing': 'HOUSING_TELECOM'}.get(mode, 'FOOD'), 'confidence': .8}]
                        if mode == 'fixed': item['funding'] = 'fixed'
                        if mode == 'cancelled': item['net_amount'] = 0
            self.assertEqual(self.build(months=copy)['allocations'], base['allocations'])

    def test_minimum_and_exact_lock_are_preserved_and_finalization_keeps_addition(self):
        from budgets.design_units import finalize_budget
        data = inputs(target=400000, minimums={'FOOD': 120000}, locks={'EDUCATION': 12345})
        out = self.build(data)
        final = finalize_budget(data, out, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
        self.assertEqual(out['allocations']['FOOD'], 150000)
        self.assertEqual(final['allocations']['FOOD'], 150000)
        self.assertEqual(final['allocations']['EDUCATION'], 12345)
        self.assertEqual(data['minimums'], {'FOOD': 120000})
        self.assertEqual(data['locks'], {'EDUCATION': 12345})
        locked = self.build(inputs(target=400000, locks={'FOOD': 100001}))
        self.assertEqual(locked['allocations']['FOOD'], 100001)

    def test_surplus_start_is_an_independent_snapshot(self):
        out = self.build()
        self.assertIsNot(out['allocations'], out['start_allocations'])
        self.assertEqual(out['start_allocations']['FOOD'], 100000)
        self.assertEqual(out['allocations']['FOOD'], 150000)

    def test_final_rounding_spends_only_final_remainder_and_keeps_other_categories(self):
        from budgets.design_units import finalize_budget
        values = {'FOOD': 130000, 'TRANSPORT_CAR': 107000, 'HOUSEHOLD': 31000,
                  'FASHION_SHOPPING': 90000, 'MEDICAL_HEALTH': 40000, 'BEAUTY': 47000}
        months = fixture()
        for month, row in months.items():
            row['items'] = [purchase(month, amount, code) for code, amount in values.items()]
            row['items'].append(purchase(month, 30000, 'UNCLASSIFIED',
                suggestion_candidates=[{'category': 'FOOD', 'confidence': .8}]))
        data = inputs(target=480000, locks={'EDUCATION': 21345}, priorities=[
            {'category': c, 'rank': r} for r, c in enumerate(
                ('HOUSEHOLD', 'FASHION_SHOPPING', 'MEDICAL_HEALTH', 'FOOD', 'CULTURE_LEISURE', 'TRANSPORT_CAR'), 1)])
        plain = deepcopy(months)
        for row in plain.values():
            for item in row['items']: item.pop('suggestion_candidates', None)
        baseline = finalize_budget(data, self.build(data, plain, available_spare=200000),
                                   b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
        raw = self.build(data, months, available_spare=200000)
        final = finalize_budget(data, raw, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
        self.assertEqual(baseline['allocations']['BEAUTY'], 50000)
        self.assertEqual(baseline['allocations']['TRANSPORT_CAR'], 110000)
        self.assertEqual(final['allocations']['BEAUTY'], 50000)
        self.assertEqual(final['allocations']['TRANSPORT_CAR'], 110000)
        self.assertEqual(final['allocations']['FOOD'], 135000)
        self.assertEqual(final['spare_transfer'], baseline['spare_transfer'])
        self.assertEqual(final['candidate_adjustment']['corrections'][0]['amount'], 5000)
        self.assertEqual(final['candidate_adjustment']['corrections'][0]['proposed_amount'], 10000)
        self.assertEqual(final['unallocated'], baseline['unallocated']-5000)
        for code in CATEGORY_CODES:
            if code != 'FOOD':
                self.assertEqual(final['allocations'][code], baseline['allocations'][code])
        repeated = finalize_budget(data, final, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
        self.assertEqual(repeated['allocations'], final['allocations'])
        self.assertEqual(repeated['candidate_adjustment'], final['candidate_adjustment'])

    def test_rounding_absorbed_addition_is_not_reported_as_applied(self):
        from budgets.design_units import finalize_budget
        months = fixture()
        for month, row in months.items():
            row['items'] = [purchase(month, 140000), purchase(month, 5000, 'UNCLASSIFIED',
                suggestion_candidates=[{'category': 'FOOD', 'confidence': .8}])]
        data = inputs(target=200000)
        raw = self.build(data, months)
        self.assertEqual(raw['candidate_adjustment']['corrections'][0]['amount'], 5000)
        final = finalize_budget(data, raw, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
        self.assertEqual(final['allocations']['FOOD'], 140000)
        self.assertEqual(final['candidate_adjustment']['status'], 'no_effect_after_rounding')
        self.assertEqual(final['candidate_adjustment']['corrections'], [])
        self.assertFalse(any(r['category'] == 'FOOD' for r in final['rounding']))
        self.assertFalse(any(c['code'] == 'unclassified_candidate_adjustment'
                             for e in final['explanations'] for c in e['causes']))

    def test_unit_failure_does_not_report_an_applied_candidate(self):
        from budgets.design_units import finalize_budget
        months = fixture()
        for month, row in months.items():
            row['items'] = [purchase(month, 1000, c) for c in
                            ('FOOD', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING', 'BEAUTY')]
            row['items'].append(purchase(month, 5000, 'UNCLASSIFIED',
                suggestion_candidates=[{'category': 'FOOD', 'confidence': .8}]))
        data = inputs(target=10000)
        raw = self.build(data, months)
        self.assertEqual(raw['candidate_adjustment']['status'], 'applied')
        final = finalize_budget(data, raw, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
        self.assertEqual(final['status'], 'needs_upper_allocation_review')
        self.assertIsNone(final['result'])
        self.assertEqual(final['candidate_adjustment']['status'], 'rounding_unavailable')
        self.assertEqual(final['candidate_adjustment']['corrections'], [])

    def test_no_unallocated_never_takes_more_upstream_spare(self):
        out = self.build(inputs(target=100000), available_spare=200000)
        self.assertEqual(out['candidate_adjustment']['status'], 'no_unallocated')
        self.assertEqual(out['allocations']['FOOD'], 100000)
        self.assertEqual(out['spare_transfer'], 0)

    def test_partial_budget_and_priority_order_never_reduce_another_category(self):
        months = records()
        for row in months.values():
            for item in row['items']:
                if item['category'] == 'UNCLASSIFIED':
                    item['suggestion_candidates'] = [{'category': 'BEAUTY', 'confidence': .8}]
            row['items'].append(purchase(row['items'][0]['occurred_on'][:7], 40000, 'BEAUTY'))
        out = self.build(inputs(target=160000), months)
        self.assertEqual(out['allocations']['FOOD'], 100000)
        self.assertEqual(out['allocations']['BEAUTY'], 60000)
        self.assertEqual(out['unallocated'], 0)

    def test_monthly_totals_do_not_invent_transaction_candidates(self):
        months = fixture()
        for row in months.values():
            row.pop('items')
            row['monthly_totals'] = {'FOOD': 100000, 'UNCLASSIFIED': 100000}
        out = self.build(months=months)
        self.assertEqual(out['history']['baseline'], 'median')
        self.assertEqual(out['allocations']['FOOD'], 100000)
        self.assertEqual(out['candidate_adjustment']['status'], 'no_usable_candidates')

    def test_150_synthetic_hypotheses_preserve_money_settings_and_ledger(self):
        from budgets.design_units import finalize_budget
        from plan_b_budget import check_result_with_unallocated
        rng = random.Random(20261106)
        for _ in range(150):
            months = records()
            for row in months.values():
                for item in row['items']:
                    item['net_amount'] = rng.randrange(1, 40)*5000
            data = inputs(target=rng.randrange(50, 200)*10000,
                          minimums={'FOOD': rng.randrange(1, 5)*10000}, locks={'EDUCATION': 12345})
            before = deepcopy((data, months))
            out = self.build(data, months)
            self.assertEqual(out['status'], 'proposed')
            check_result_with_unallocated(data, out['result'])
            plain = deepcopy(months)
            for row in plain.values():
                for item in row['items']: item.pop('suggestion_candidates', None)
            base = self.build(data, plain)
            self.assertTrue(all(out['allocations'][c] >= base['allocations'][c] for c in CATEGORY_CODES))
            self.assertEqual(out['allocations']['EDUCATION'], 12345)
            final = finalize_budget(data, out, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
            base_final = finalize_budget(data, base, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100))
            self.assertEqual(final['status'], 'proposed')
            self.assertTrue(all(final['allocations'][c] >= base_final['allocations'][c] for c in CATEGORY_CODES))
            check_result_with_unallocated(data, final['result'])
            for e in final['explanations']:
                self.assertEqual(sum(c['amount'] for c in e['causes']), e['diff'])
            self.assertEqual((data, months), before)

    def test_400_multicategory_final_comparisons_preserve_unrelated_amounts_and_actual_audit(self):
        from budgets.design_units import finalize_budget
        from plan_b_budget import check_result_with_unallocated
        active = [c for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')]
        rng = random.Random(20261107)
        compared = applied = 0
        for case in range(400):
            with self.subTest(case=case):
                months = fixture()
                values = {c: rng.randrange(0, 150)*1000 for c in active}
                values['FOOD'] = max(10000, values['FOOD'])
                values['TRANSPORT_CAR'] = max(100000, values['TRANSPORT_CAR'])
                locked = rng.randrange(0, 10)*1000+1345
                values['EDUCATION'] = locked
                for month, row in months.items():
                    row['items'] = [purchase(month, amount, c) for c, amount in values.items()]
                    for c in rng.sample(active, 3):
                        row['items'].append(purchase(month, rng.randrange(1, 60)*1000, 'UNCLASSIFIED',
                            suggestion_candidates=[{'category': c, 'confidence': .7}]))
                priorities = [{'category': c, 'rank': r} for r, c in enumerate(rng.sample(active, len(active)), 1)]
                minimum = rng.randrange(0, max(values['FOOD'], 1))
                data = inputs(target=sum(v for c, v in values.items() if c != 'EDUCATION')+locked+rng.randrange(0, 20)*5000,
                    priorities=priorities, minimums={'FOOD': minimum}, locks={'EDUCATION': locked})
                reference = {c: rng.randrange(0, values.get(c, 0)+1) for c in CATEGORY_CODES}
                options = {'available_spare': 200000, 'reality_profile': {'has_vehicle': rng.choice((True, False, None))},
                           'ml_reference': reference, 'user_protected': rng.sample(active, 2)}
                plain = deepcopy(months)
                for row in plain.values():
                    for item in row['items']: item.pop('suggestion_candidates', None)
                before = deepcopy((data, months, options))
                original = self.build(data, plain, **options)
                corrected = self.build(data, months, **options)
                baseline = finalize_budget(data, original, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100),
                                           other_reserved=1345, other_minimum=0)
                final = finalize_budget(data, corrected, b_cut_rates=dict.fromkeys(CATEGORY_CODES, 100),
                                        other_reserved=1345, other_minimum=0)
                self.assertEqual(final['status'], baseline['status'])
                if baseline['status'] != 'proposed':
                    continue
                compared += 1
                actual = {r['category']: r['amount'] for r in final['candidate_adjustment']['corrections']}
                applied += bool(actual)
                for c in CATEGORY_CODES:
                    self.assertEqual(final['allocations'][c]-baseline['allocations'][c], actual.get(c, 0))
                    self.assertGreaterEqual(actual.get(c, 0), 0)
                    if c not in data['locks']:
                        self.assertEqual(final['allocations'][c] % 5000, 0)
                self.assertLessEqual(sum(actual.values()), baseline['unallocated'])
                self.assertEqual(final['unallocated'], baseline['unallocated']-sum(actual.values()))
                self.assertEqual(final['other_reserved'], baseline['other_reserved'])
                self.assertEqual(final['spare_transfer'], baseline['spare_transfer'])
                self.assertEqual(final['allocations']['EDUCATION'], locked)
                self.assertGreaterEqual(final['allocations']['FOOD'], minimum)
                check_result_with_unallocated({**data, 'target': final['funded_target']}, final['result'])
                for e in final['explanations']:
                    self.assertEqual(sum(c['amount'] for c in e['causes']), e['diff'])
                    recorded = sum(c['amount'] for c in e['causes'] if c['code'] == 'unclassified_candidate_adjustment')
                    self.assertEqual(recorded, actual.get(e['category'], 0))
                self.assertEqual((data, months, options), before)
        self.assertEqual(compared, 400)
        self.assertGreater(applied, 0)
