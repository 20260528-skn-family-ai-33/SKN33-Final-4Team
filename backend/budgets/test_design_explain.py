"""Explanation accounting uses exact server values; policy values are fixtures."""
from copy import deepcopy
from fractions import Fraction
import random
from django.test import SimpleTestCase
from .design_candidates import build_candidates, apply_priority_decisions, CATEGORY_NAMES
from .design_explain import explain_allocations
from .recommend import CATEGORY_CODES, EngineCheckFailed
from .test_design_candidates import inp, priorities, limits


def assert_ledger(test, explanations, allocations):
    test.assertEqual({e['category'] for e in explanations}, set(allocations))
    for row in explanations:
        if row['text']:
            if any(c['code'] == 'one_off_excluded' for c in row['causes']):
                test.assertIn('매달 나가는 돈이라면 다시 포함할 수 있어요.', row['text'])
            else:
                test.assertEqual(row['text'].count('.'), 1)
            test.assertNotIn('\n', row['text'])
            test.assertNotIn('배정 예산은', row['text'])
        test.assertEqual(row['final_amount'], allocations[row['category']])
        test.assertEqual(row['diff'], row['final_amount'] - row['reference_amount'])
        test.assertEqual(sum(c['amount'] for c in row['causes']), row['diff'])


class ExplainTests(SimpleTestCase):
    def test_one_off_methods_and_exact_ledger(self):
        for method, phrase in (('dominance', '모두 합친 것보다 커서'), ('mad', '평소 결제보다 크게 벗어나')):
            refs = {'MEDICAL_HEALTH': Fraction(190000,3)}
            final = {'MEDICAL_HEALTH': Fraction(20000,3)}
            ledger = {'MEDICAL_HEALTH': [{'code':'one_off_excluded', 'amount':-Fraction(170000,3),
                       'detail':{'id':'server-id','month':'2026-09','amount':170000,'method':method}}]}
            out = explain_allocations(refs,final,ledger,reference_type='history_average',priorities=[],names=CATEGORY_NAMES)
            assert_ledger(self,out,final)
            self.assertIn('9월 170,000원 결제 1건',out[0]['text'])
            self.assertIn(phrase,out[0]['text'])
            self.assertNotIn('server-id',out[0]['text'])

    def test_one_off_shows_largest_two_and_keeps_all_causes(self):
        causes=[{'code':'one_off_excluded','amount':-Fraction(v,3),
                 'detail':{'id':str(v),'month':'2026-09','amount':v,'method':'mad'}} for v in (100000,300000,200000)]
        out=explain_allocations({'FOOD':300000},{'FOOD':100000},{'FOOD':causes},reference_type='history_average',priorities=[],names=CATEGORY_NAMES)
        self.assertIn('300,000원',out[0]['text'])
        self.assertIn('200,000원',out[0]['text'])
        self.assertNotIn('100,000원',out[0]['text'])
        self.assertEqual(len(out[0]['causes']),3)
        assert_ledger(self,out,{'FOOD':100000})

    def test_bad_ledger_fails_and_small_rounding_is_silent(self):
        refs = {'FOOD': Fraction(100001, 3)}
        amounts = {'FOOD': 33000}
        ledger = {'FOOD': [{'code': 'rounding', 'amount': 33000 - refs['FOOD'], 'detail': {}}]}
        result = explain_allocations(refs, amounts, ledger, reference_type='history_average', priorities=[], names=CATEGORY_NAMES)
        self.assertEqual(result[0]['text'], '')
        ledger['FOOD'][0]['amount'] += 1
        with self.assertRaisesRegex(EngineCheckFailed, 'explanation_ledger_mismatch'):
            explain_allocations(refs, amounts, ledger, reference_type='history_average', priorities=[], names=CATEGORY_NAMES)

    def test_minimum_floor_lock_inactive_and_redistribution(self):
        data = inp(minimums={'EDUCATION': 250000}, locks={'BEAUTY': 12345})
        ratios = dict.fromkeys(CATEGORY_CODES, 1)
        ratios['TRANSPORT_CAR'] = .01
        result = build_candidates(data, ratios, policy=limits(data), reality_profile={'has_vehicle': False})
        base = result['candidates'][0]
        rows = {e['category']: e for e in base['explanations']}
        for category, cause in (('EDUCATION', 'user_minimum_raise'), ('TRANSPORT_CAR', 'system_floor_raise'),
                                ('BEAUTY', 'lock_fixed'), ('TRAVEL_LODGING', 'inactive_category')):
            self.assertIn(cause, [c['code'] for c in rows[category]['causes']])
        self.assertIn('constraint_redistribution', [c['code'] for c in rows['FOOD']['causes']])
        self.assertEqual(rows['FOOD']['text'], '')  # The narrated redistribution is below the notice threshold.
        self.assertNotIn('뷰티·미용 자물쇠 금액', rows['FOOD']['text'])
        self.assertFalse(rows['TRANSPORT_CAR']['text'])
        for candidate in result['candidates']:
            assert_ledger(self, candidate['explanations'], candidate['result']['allocations'])

    def test_top_rank_floor_redistribution_and_lower_ranks_silent(self):
        ratios = dict.fromkeys(CATEGORY_CODES, 0)
        ratios.update(FOOD=990, TRANSPORT_CAR=10)
        for rank in (1, 4, None):
            order = ('FOOD', 'EDUCATION', 'MEDICAL_HEALTH') if rank == 1 else ('EDUCATION', 'MEDICAL_HEALTH', 'BEAUTY')
            if rank == 4:
                order += ('FOOD',)
            data = inp(priorities=priorities(*order))
            base = build_candidates(data, ratios, reality_profile={'has_vehicle': False})['candidates'][0]
            row = next(r for r in base['explanations'] if r['category'] == 'FOOD')
            if rank == 1:
                self.assertEqual(row['text'], '')  # 45,000 won is now silent.
                replay = apply_priority_decisions(data, ratios, policy=limits(data),
                    decisions=[{'category': 'FOOD', 'action': 'keep'}], reality_profile={'has_vehicle': False})
                self.assertEqual(next(r['text'] for r in replay['explanations'] if r['category'] == 'FOOD'), row['text'])
            else:
                self.assertEqual(row['text'], '')
            assert_ledger(self, base['explanations'], base['result']['allocations'])

    def test_multiple_constraint_types_and_small_amount_are_grouped(self):
        ledger = {'FOOD': [{'code': 'constraint_redistribution', 'amount': -500,
                           'detail': {'constraints': [
                               {'category': 'TRANSPORT_CAR', 'type': 'system_floor'},
                               {'category': 'EDUCATION', 'type': 'user_minimum'},
                               {'category': 'BEAUTY', 'type': 'lock'}]}}]}
        row = explain_allocations({'FOOD': 10000}, {'FOOD': 9500}, ledger,
            reference_type='ml_share', priorities=priorities('FOOD'), names=CATEGORY_NAMES)[0]
        self.assertEqual(row['text'], '')
        self.assertEqual(sum(c['amount'] for c in row['causes']), row['diff'])

        ledger['FOOD'][0]['amount'] = -50001
        large = explain_allocations({'FOOD':100000},{'FOOD':49999},ledger,
            reference_type='ml_share',priorities=priorities('FOOD'),names=CATEGORY_NAMES)[0]
        self.assertIn('교통 하한과 교육 최소 금액 등',large['text'])
        self.assertIn('50,001원',large['text'])

    def test_fractional_history_difference_in_conflict_text(self):
        average = Fraction(2400001, 3)
        final = 500000
        ledger = {'FOOD': [{'code': 'shortfall_top_rank_cut', 'amount': final-average, 'detail': {'rank': 1}}]}
        row = explain_allocations({'FOOD': average}, {'FOOD': final}, ledger,
            reference_type='history_average', priorities=priorities('FOOD'), names=CATEGORY_NAMES)[0]
        self.assertIn('지난 3개월 평균보다 약 300,000원 줄였어요', row['text'])

    def test_history_above_average_does_not_claim_net_reduction(self):
        ledger = {'FOOD': [
            {'code': 'rounding', 'amount': 500, 'detail': {'unit': 1000}},
            {'code': 'shortfall_top_rank_cut', 'amount': -100, 'detail': {'rank': 1}}]}
        row = explain_allocations({'FOOD': 99500}, {'FOOD': 99900}, ledger,
            reference_type='history_average', priorities=priorities('FOOD'), names=CATEGORY_NAMES)[0]
        self.assertEqual(row['text'], '')
        self.assertNotIn('평균보다 400원 줄였어요', row['text'])

    def test_adjustment_notice_threshold_and_ledger_preserved(self):
        for amount in (0, 6368, 50000, 50001, Fraction(150001,3)):
            causes = [{'code':'shortfall_top_rank_cut','amount':-amount,'detail':{'rank':1}}]
            original = deepcopy(causes)
            row = explain_allocations({'FOOD':300000},{'FOOD':300000-amount}, {'FOOD':causes},
                reference_type='history_average',priorities=priorities('FOOD'),names=CATEGORY_NAMES)[0]
            self.assertEqual(bool(row['text']), amount > 50000)
            self.assertEqual(row['causes'], original)
            self.assertEqual(row['diff'], -amount)

    def test_only_unsatisfied_priority_emits_text_not_ml_difference(self):
        data = inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        ratios = dict.fromkeys(CATEGORY_CODES, 1)
        policy = limits(data)
        policy['decrease_limits'] = dict.fromkeys(CATEGORY_CODES, 0)
        engine = build_candidates(data, ratios, policy=policy)
        rows = {r['category']: r for r in engine['candidates'][0]['explanations']}
        self.assertIn('옮길 수 있는 금액이 부족', rows['FOOD']['text'])
        self.assertNotIn('ML', rows['FOOD']['text'])
        self.assertNotIn('→', rows['FOOD']['text'])
        result = apply_priority_decisions(data, ratios, policy=policy,
            decisions=[{'category': 'FOOD', 'action': 'keep'}])
        self.assertTrue(all(not row['text'] for row in result['explanations']))
        result = apply_priority_decisions(data, ratios, policy=policy,
            decisions=[{'category': 'FOOD', 'action': 'increase'}])
        self.assertTrue(next(r['text'] for r in result['explanations'] if r['category'] == 'FOOD'))
        assert_ledger(self, result['explanations'], result['allocations'])

    def test_random_a_and_llm_replay_reconcile_and_preserve_input(self):
        rng = random.Random(42)
        for case in range(150):
            data = inp(target=rng.randrange(500000, 2000000),
                       priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'),
                       minimums={'EDUCATION': rng.randrange(150000)},
                       locks={'BEAUTY': rng.randrange(100000)} if case % 3 == 0 else {})
            ratios = {c: rng.randrange(1, 100) for c in CATEGORY_CODES}
            policy = limits(data, total=50000, increase=25000)
            before = deepcopy(data)
            profile = {'has_vehicle': False} if case % 2 else None
            engine = build_candidates(data, ratios, policy=policy, reality_profile=profile)
            for candidate in engine['candidates']:
                assert_ledger(self, candidate['explanations'], candidate['result']['allocations'])
            replay = apply_priority_decisions(data, ratios, policy=policy,
                decisions=[{'category': 'FOOD', 'action': 'increase'}], reality_profile=profile)
            assert_ledger(self, replay['explanations'], replay['allocations'])
            self.assertEqual(data, before)

    def test_rounding_below_floor_records_rounding_and_raise_separately(self):
        ratios = dict.fromkeys(CATEGORY_CODES, 0)
        ratios.update(FOOD=885, TRANSPORT_CAR=115)
        base = build_candidates(inp(target=500000), ratios, reality_profile={'has_vehicle': False})['candidates'][0]
        row = next(e for e in base['explanations'] if e['category'] == 'TRANSPORT_CAR')
        self.assertEqual(row['reference_amount'], 57500)
        self.assertEqual(row['final_amount'], 60000)
        self.assertIn(('system_floor_raise', 2500), [(c['code'], c['amount']) for c in row['causes']])
        assert_ledger(self, base['explanations'], base['result']['allocations'])

    def test_minimum_consuming_budget_is_not_mislabeled_rounding(self):
        data = inp(target=600000, minimums={'FOOD': 600000})
        base = build_candidates(data, dict.fromkeys(CATEGORY_CODES, 1))['candidates'][0]
        assert_ledger(self, base['explanations'], base['result']['allocations'])
        transport = next(e for e in base['explanations'] if e['category'] == 'TRANSPORT_CAR')
        self.assertEqual(transport['causes'][0]['code'], 'constraint_redistribution')

    def test_skipped_ml_is_explicit_and_transfer_details_have_counterparty(self):
        data = inp(target=800000, locks={c: 100000 for c in CATEGORY_CODES if c not in ('TRAVEL_LODGING', 'HOUSING_TELECOM')})
        base = build_candidates(data, None)['candidates'][0]
        assert_ledger(self, base['explanations'], base['result']['allocations'])
        self.assertEqual(base['explanations'][0]['text'], '')
        self.assertTrue(base['explanations'][0]['causes'][0]['detail']['reference_unavailable'])
        data = inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        candidate = build_candidates(data, dict.fromkeys(CATEGORY_CODES, 1), policy=limits(data))['candidates'][-1]
        causes = [c for e in candidate['explanations'] for c in e['causes'] if c['code'].startswith('priority_transfer')]
        self.assertTrue(causes)
        self.assertTrue(all('counterparty' in c['detail'] and 'receiver_rank' in c['detail'] for c in causes))
