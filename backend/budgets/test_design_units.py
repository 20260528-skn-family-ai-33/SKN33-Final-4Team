"""Final budget units; all monetary fixtures below are synthetic, not policy defaults."""
from copy import deepcopy
from fractions import Fraction
import random

from django.test import SimpleTestCase

from .design_candidates import build_candidates, select_candidate
from .design_units import finalize_budget, round_allocations
from .design_explain import priority_reduction_notices
from .recommend import check_result
from .test_design_candidates import inp, limits, priorities


class BudgetUnitsTest(SimpleTestCase):
    def test_priority_notice_threshold_and_protections(self):
        history = {'status': 'ready', 'categories': {'BEAUTY': {'baseline_amount': 100000}}}
        for final, rank, locked, expected in ((71000, 3, False, False), (70000, 3, False, False),
                                               (69999, 3, False, True), (40000, 2, False, True),
                                               (40000, 4, False, False), (40000, 3, True, False)):
            with self.subTest(final=final, rank=rank, locked=locked):
                explanations = [{'category': 'BEAUTY', 'text': ''}]
                result = priority_reduction_notices(explanations, {'BEAUTY': final},
                    [{'category': 'BEAUTY', 'rank': rank}], {'BEAUTY': final} if locked else {},
                    history, {'BEAUTY': '뷰티·미용'})
                self.assertEqual(bool(result[0]['text']), expected)
        empty = [{'category': 'BEAUTY', 'text': ''}]
        self.assertEqual(priority_reduction_notices(empty, {'BEAUTY': 0},
            [{'category': 'BEAUTY', 'rank': 1}], {}, {'status': 'ready', 'categories':
            {'BEAUTY': {'baseline_amount': 0}}}, {'BEAUTY': '뷰티·미용'})[0]['text'], '')

    def test_notice_uses_final_rounded_amount_and_keeps_money(self):
        data = inp(target=100000, priorities=priorities('BEAUTY', 'FOOD', 'EDUCATION'))
        amounts = dict.fromkeys(data_codes(), 0)
        amounts.update(BEAUTY=41000, FOOD=59000)
        engine = build_candidates(data, dict.fromkeys(data_codes(), 1))
        explanations = engine['candidates'][0]['explanations']
        # Reconcile each synthetic entry before finalization.
        for e in explanations:
            delta = amounts[e['category']] - e['final_amount']
            if delta:
                e['causes'].append({'code': 'rounding', 'amount': delta, 'detail': {}})
            e.update(final_amount=amounts[e['category']], diff=amounts[e['category']]-e['reference_amount'])
        view = {'status': 'proposed', 'allocations': amounts, 'explanations': explanations,
                'history': {'status': 'ready', 'categories': {'BEAUTY': {'baseline_amount': 65000}}}}
        result = finalize_budget(data, view)
        self.assertEqual(result['allocations']['BEAUTY'], 40000)
        self.assertIn('25,000원 줄었어요', result['result']['reasons'][0])
        self.assertEqual(result['explanations'][next(i for i,e in enumerate(result['explanations']) if e['category']=='BEAUTY')]['text'], result['result']['reasons'][0])
        check_result(data, result['result'])

    def test_prefers_ten_thousand_and_keeps_remainder_free(self):
        result = round_allocations({'FOOD': 121000, 'BEAUTY': 34000}, 155000)
        self.assertEqual(result['allocations'], {'FOOD': 120000, 'BEAUTY': 30000})
        self.assertEqual(result['unallocated'], 5000)

    def test_five_thousand_when_ten_thousand_cannot_fit(self):
        result = round_allocations(dict.fromkeys(('FOOD', 'BEAUTY', 'EDUCATION'), 145000), 435000)
        self.assertEqual(sorted(result['allocations'].values()), [140000, 145000, 150000])
        self.assertEqual(result['unallocated'], 0)
        bounded = round_allocations({'FOOD': 23000}, 30000,
                                    minimums={'FOOD': 23000}, upper_bounds={'FOOD': 26000})
        self.assertEqual(bounded['allocations']['FOOD'], 25000)

    def test_locks_remain_exact_even_below_system_floor(self):
        result = round_allocations({'FOOD': 12345, 'BEAUTY': 87000}, 100000,
            locks={'FOOD': 12345}, floors={'FOOD': 55000})
        self.assertEqual(result['allocations']['FOOD'], 12345)
        self.assertEqual(result['allocations']['BEAUTY'] % 5000, 0)
        self.assertEqual(sum(result['allocations'].values()) + result['unallocated'], 100000)

    def test_minimum_floor_caps_and_source_preserved(self):
        values = {'FOOD': 58000, 'BEAUTY': 27000}
        before = deepcopy(values)
        out = round_allocations(values, 85000, minimums={'BEAUTY': 27000},
                                floors={'FOOD': 55000}, upper_bounds={'FOOD': 59000})
        self.assertEqual(out['allocations'], {'FOOD': 55000, 'BEAUTY': 30000})
        self.assertEqual(values, before)

    def test_impossible_unit_bound_is_not_silently_violated(self):
        out = round_allocations({'FOOD': 12345}, 20000,
                                lower_bounds={'FOOD': 12345}, upper_bounds={'FOOD': 14000})
        self.assertEqual(out['reason'], 'budget_unit_conflict')
        self.assertIsNone(out['allocations'])

    def test_positive_category_not_zeroed_and_only_available_spare_is_used(self):
        out = round_allocations({'FOOD': 1000}, 1000)
        self.assertEqual(out['reason'], 'budget_unit_unaffordable')
        out = round_allocations({'FOOD': 1000}, 1000, spare=4000)
        self.assertEqual(out['allocations']['FOOD'], 5000)
        self.assertEqual(out['rounding_spare_transfer'], 4000)

    def test_total_increase_limit_survives_rounding(self):
        out = round_allocations({'FOOD': 106000, 'BEAUTY': 94000}, 200000,
            increase_reference={'FOOD': 100000, 'BEAUTY': 100000}, increase_limit=5000)
        self.assertLessEqual(sum(max(0, out['allocations'][c]-100000) for c in out['allocations']), 5000)

    def test_final_a_checks_locks_other_and_explanation_ledger(self):
        data = inp(locks={'FOOD': 12345}, minimums={'BEAUTY': 23456},
                   priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        before = deepcopy(data)
        policy = limits(data)
        engine = build_candidates(data, dict.fromkeys(data_codes(), 1), policy=policy)
        selected = select_candidate(data, engine['candidates'])
        view = {**selected['result'], 'result': selected['result'], 'engine': engine,
                'explanations': selected['explanations']}
        out = finalize_budget(data, view, policy=policy, other_reserved=12345,
                              other_minimum=12345, other_locked=True)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['allocations']['FOOD'], 12345)
        self.assertEqual(out['other_reserved'], 12345)
        self.assertGreaterEqual(out['allocations']['BEAUTY'], 23456)
        check_result({**data, 'target': out['funded_target']}, out['result'])
        for c, v in out['allocations'].items():
            if c != 'FOOD':
                self.assertEqual(v % 5000, 0)
        for e in out['explanations']:
            self.assertEqual(sum(c['amount'] for c in e['causes']), e['diff'])
            self.assertEqual(e['final_amount'], out['allocations'][e['category']])
        self.assertEqual(data, before)

    def test_final_b_cut_limit_and_user_protection_survive(self):
        data = inp(target=150000, priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        amounts = dict.fromkeys(data_codes(), 0)
        amounts.update(FOOD=75000, BEAUTY=75000)
        start = {**amounts, 'FOOD': 100000, 'BEAUTY': 100000}
        out = finalize_budget(data, {'status': 'proposed', 'allocations': amounts,
            'start_allocations': start, 'user_protected': ['BEAUTY'], 'available_spare': 30000},
            b_cut_rates=dict.fromkeys(data_codes(), 25))
        self.assertEqual(out['status'], 'proposed')
        self.assertGreaterEqual(out['allocations']['FOOD'], 75000)
        self.assertEqual(out['allocations']['BEAUTY'], 100000)
        self.assertEqual(out['spare_transfer'], 25000)

    def test_completed_forecast_keeps_bounds_and_signed_ledger(self):
        data = inp(target=200000)
        amounts = dict.fromkeys(data_codes(), 0)
        amounts['FOOD'] = 145000
        out = finalize_budget(data, {'status': 'proposed', 'variant': 'b_forecast',
            'llm_status': 'completed', 'allocations': amounts, 'unallocated': 55000,
            'explanations': [{'category': 'FOOD', 'reference_type': 'history_baseline',
                'reference_amount': Fraction(250001, 3), 'final_amount': 145000,
                'diff': 145000-Fraction(250001, 3), 'text': '',
                'causes': [{'code': 'trend_up', 'amount': 145000-Fraction(250001, 3),
                            'detail': {'text': '최근 소비 흐름을 반영했어요.'}}]}]},
            lower_bounds={'FOOD': 145000}, upper_bounds={'FOOD': 145000})
        self.assertEqual(out['allocations']['FOOD'], 145000)
        e = out['explanations'][0]
        self.assertEqual(sum(c['amount'] for c in e['causes']), e['diff'])
        self.assertIn('145,000원', e['text'])

    def test_random_constraints_conservation_and_input_preservation(self):
        rng = random.Random(20261105)
        for _ in range(150):
            codes = ('FOOD', 'BEAUTY', 'EDUCATION', 'MEDICAL_HEALTH')
            values = {c: rng.randrange(1000, 200000) for c in codes}
            lower = {c: rng.randrange(0, values[c]//5000+1)*5000 for c in codes}
            upper = {c: (values[c]//5000+1)*5000 for c in codes}
            locks = {'FOOD': values['FOOD']} if rng.choice((False, True)) else {}
            original = deepcopy(values)
            target, spare = sum(values.values())+rng.randrange(10000), 20000
            out = round_allocations(values, target, spare=spare, locks=locks,
                                    lower_bounds=lower, upper_bounds=upper)
            self.assertEqual(out['status'], 'proposed')
            self.assertEqual(sum(out['allocations'].values())+out['unallocated'],
                             target+out['rounding_spare_transfer'])
            self.assertLessEqual(out['rounding_spare_transfer'], spare)
            for c, v in out['allocations'].items():
                if c in locks:
                    self.assertEqual(v, locks[c])
                else:
                    self.assertEqual(v % 5000, 0)
                    self.assertTrue(lower[c] <= v <= upper[c])
            self.assertEqual(values, original)


def data_codes():
    from .design_candidates import CATEGORY_NAMES
    return CATEGORY_NAMES
