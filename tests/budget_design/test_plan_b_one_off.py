"""Transaction outlier policy, privacy and 150 synthetic allocation invariants."""
from copy import deepcopy
from datetime import date
from fractions import Fraction
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
from plan_b_history import CATEGORY_CODES, CHECKS, summarize_history, detect_one_offs
from plan_b_budget import build_history_budget, build_priority_history_budget
import plan_b_llm

AS_OF = date(2026, 11, 3)


def history(rows, category='MEDICAL_HEALTH'):
    months = {}
    for month, amounts in zip(('2026-08', '2026-09', '2026-10'), rows):
        months[month] = {'coverage_complete': True, 'normalization_checks': dict.fromkeys(CHECKS, 'verified'),
            'items': [{'occurred_on': month+'-15', 'net_amount': a, 'transaction_type': 'spending',
                       'funding': 'living', 'category': category} for a in amounts]}
    return months


def budget_input(target=10000):
    return {'target': target, 'minimums': {}, 'locks': {},
            'priorities': [{'category': c, 'rank': n} for n, c in enumerate(('FOOD','EDUCATION','MEDICAL_HEALTH'), 1)]}


def sensitivity_results():
    # Ground truth labels are fixture assumptions, not inferred real-world necessity.
    cases = (
        ('5건·실제 일회성(합성)', [10000,10100,9900,10050,30000]),
        ('8건·실제 일회성(합성)', [10000,10100,9900,10050,9950,10200,9800,50000]),
        ('5건·정기 결제지만 키 누락(합성)', [10000,10100,9900,10050,30000]),
        ('8건·정상 필요 결제(합성)', [10000,10100,9900,10050,9950,10200,9800,50000]),
        ('10건·큰 결제', [9000,9500,9800,9900,10000,10100,10200,10500,11000,170000]),
        ('10건·고른 결제', [1000*i for i in range(1,11)]),
        ('11건·MAD 0', [10000]*10+[170000]),
    )
    results = []
    for name, amounts in cases:
        transactions = [{'id':f'fixture-{i}', 'amount':amount,'month':f'2026-{8+i%3:02}',
                         'category':'FOOD','merchant_key':None} for i,amount in enumerate(amounts)]
        results.append({'case':name, 'amounts':amounts, 'thresholds':{
            n:[{'amount':c['amount'],'method':c['method']} for c in detect_one_offs(transactions,min_count=n)]
            for n in (5,8,10)}})
    return results


class OneOffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING': 'false'}))
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')))

    def summary(self, rows, **kwargs):
        return summarize_history(history(rows), as_of=AS_OF, **kwargs)

    def test_medical_and_culture_values(self):
        for code, rows, avg, med, adjusted, excluded in (
            ('MEDICAL_HEALTH', [[10000],[10000,170000],[]], Fraction(190000,3), 10000, Fraction(20000,3),170000),
            ('CULTURE_LEISURE', [[10000],[10000,183000],[23000]], Fraction(226000,3),23000,Fraction(43000,3),183000)):
            with self.subTest(category=code):
                out = summarize_history(history(rows,code),as_of=AS_OF)
                row = out['categories'][code]
                self.assertEqual((row['average'],row['median'],row['baseline_amount']), (avg,med,adjusted))
                self.assertEqual(len(out['one_off_candidates']),1)
                self.assertEqual(out['one_off_candidates'][0]['amount'],excluded)
                self.assertEqual(out['one_off_candidates'][0]['method'],'dominance')

    def test_minimum_count_sensitivity_does_not_change_default(self):
        from plan_b_history import MAD_MIN_TRANSACTIONS
        rows=sensitivity_results()
        self.assertEqual(rows[0]['thresholds'],{5:[{'amount':30000,'method':'mad'}],8:[],10:[]})
        self.assertEqual(rows[1]['thresholds'],{5:[{'amount':50000,'method':'mad'}],8:[{'amount':50000,'method':'mad'}],10:[]})
        self.assertEqual(rows[2]['thresholds'],rows[0]['thresholds'])
        self.assertEqual(rows[3]['thresholds'],rows[1]['thresholds'])
        self.assertEqual(MAD_MIN_TRANSACTIONS,10)

    def test_shopping_and_beauty_never_use_dominance(self):
        for code, amounts in (
            ('FASHION_SHOPPING', [30000, 40000, 50000, 300000]),
            ('BEAUTY', [40000, 20000, 400000]),
            ('FASHION_SHOPPING', [10000]*10 + [300000]),
            ('BEAUTY', [10000]*10 + [400000]),
        ):
            with self.subTest(category=code, amounts=amounts):
                months = history([[], amounts, []], code)
                out = summarize_history(months, as_of=AS_OF)
                self.assertEqual(out['one_off_candidates'], [])
                self.assertEqual(out['categories'][code]['baseline_amount'], Fraction(sum(amounts), 3))
                budget = build_priority_history_budget(budget_input(1000000), months, as_of=AS_OF,
                                                       shortfall_cut_rates=None)
                explanation = next(e for e in budget['explanations'] if e['category'] == code)
                self.assertEqual(explanation['text'], '')
                self.assertNotIn('one_off_excluded', [c['code'] for c in explanation['causes']])

    def test_shopping_and_beauty_mad_extreme_and_recurring_protection(self):
        values = [9000,9500,9800,9900,10000,10100,10200,10500,11000,400000]
        for code in ('FASHION_SHOPPING', 'BEAUTY'):
            for recurring in (False, True):
                with self.subTest(category=code, recurring=recurring):
                    months = history([[values[0]], values[1:], []], code)
                    if recurring:
                        months['2026-08']['items'][0]['merchant_key'] = ' synthetic-salon '
                        months['2026-09']['items'][-1]['merchant_key'] = 'SYNTHETIC-SALON'
                    out = summarize_history(months, as_of=AS_OF)
                    self.assertEqual(len(out['one_off_candidates']), 1)
                    candidate = out['one_off_candidates'][0]
                    self.assertEqual((candidate['amount'], candidate['method']), (400000, 'mad'))
                    self.assertEqual(candidate['excluded_by_recurrence'], recurring)
                    self.assertEqual(candidate['excluded'], not recurring)
                    expected = sum(values) - (0 if recurring else 400000)
                    self.assertEqual(out['categories'][code]['baseline_amount'], Fraction(expected, 3))
                    self.assertNotIn('synthetic-salon', json.dumps(out, default=str).lower())

    def test_mad_upper_only_and_even_distribution(self):
        values = [9000,9500,9800,9900,10000,10100,10200,10500,11000,170000]
        out = self.summary([values,[],[]])
        self.assertEqual([(c['amount'],c['method']) for c in out['one_off_candidates']],[(170000,'mad')])
        self.assertGreater(out['one_off_candidates'][0]['score'],3.5)
        self.assertFalse(self.summary([[1000*i for i in range(1,11)],[],[]])['one_off_candidates'])
        lower = self.summary([[1,9000,9500,9800,9900,10000,10100,10200,10500,11000],[],[]])
        self.assertFalse(lower['one_off_candidates'])

    def test_zero_mad_falls_back(self):
        out=self.summary([[10000]*10+[170000],[],[]])
        self.assertEqual(out['one_off_candidates'][0]['method'],'dominance')

    def test_one_or_two_purchases_stay_in_average(self):
        for code, rows in (
            ('BEAUTY', [[50000], [], [45000]]),
            ('HOUSEHOLD', [[50000], [], [45000]]),
            ('MEDICAL_HEALTH', [[], [150000], []]),
            ('MEDICAL_HEALTH', [[], [10000, 170000], []]),
            ('MEDICAL_HEALTH', [[170000, 150000], [], []]),
        ):
            with self.subTest(category=code, rows=rows):
                out = summarize_history(history(rows, code), as_of=AS_OF)
                self.assertEqual(out['one_off_candidates'], [])
                self.assertEqual(out['categories'][code]['baseline_amount'], Fraction(sum(map(sum, rows)), 3))
                self.assertEqual(out['categories'][code]['detail']['reason'], 'too_few_transactions')
                self.assertEqual(out['baseline'], 'one_off_adjusted')

    def test_170k_150k_20k_equal_remainder_is_not_dominant(self):
        self.assertFalse(self.summary([[170000,150000,20000],[],[]])['one_off_candidates'])

    def test_recurrence_and_private_fields_never_escape(self):
        months=history([[10000],[170000],[10000]])
        for row in months.values():
            for item in row['items']:
                item.update(merchant_key=' SECRET-HOSPITAL ',transaction_id='private-'+item['occurred_on'])
        before=deepcopy(months)
        out=summarize_history(months,as_of=AS_OF)
        candidate=out['one_off_candidates'][0]
        self.assertTrue(candidate['excluded_by_recurrence'])
        self.assertFalse(candidate['excluded'])
        self.assertEqual(out['categories']['MEDICAL_HEALTH']['baseline_amount'],Fraction(190000,3))
        self.assertEqual(months,before)
        payload=json.dumps(out,default=str)
        for forbidden in ('SECRET-HOSPITAL','secret-hospital','private-','transaction_id','merchant_key'):
            self.assertNotIn(forbidden,payload)

    def test_fixed_zero_income_and_transfers_not_candidates(self):
        months=history([[0,10000],[10000],[]])
        items=months['2026-08']['items']
        template={**items[1],'net_amount':1000000}
        items.extend([{**template,'funding':'fixed'}, {**template,'transaction_type':'income'},
                      {**template,'transaction_type':'transfer'}])
        self.assertFalse(summarize_history(months,as_of=AS_OF)['one_off_candidates'])

    def test_reinclude_stable_ids_and_validation(self):
        rows=[[10000],[10000,170000],[]]
        out=self.summary(rows)
        identifier=out['one_off_candidates'][0]['id']
        restored=self.summary(rows,user_reincluded_one_offs=[identifier])
        self.assertEqual(restored['categories']['MEDICAL_HEALTH']['baseline_amount'],Fraction(190000,3))
        self.assertTrue(restored['one_off_candidates'][0]['reincluded_by_user'])
        self.assertEqual(self.summary(rows)['one_off_candidates'][0]['id'],identifier)
        for ids in ([identifier,identifier],[123],['missing','missing'],identifier):
            with self.subTest(ids=ids),self.assertRaises(ValueError):
                self.summary(rows,user_reincluded_one_offs=ids)

    def test_ids_survive_transaction_reordering_with_or_without_source_ids(self):
        for with_ids in (False, True):
            with self.subTest(with_ids=with_ids):
                months = history([[10000], [10000, 170000], []])
                if with_ids:
                    for month, record in months.items():
                        for n, item in enumerate(record['items']):
                            item['transaction_id'] = f'private-{month}-{n}'
                first = summarize_history(months, as_of=AS_OF)
                identifier = first['one_off_candidates'][0]['id']
                if with_ids:
                    self.assertEqual(identifier, 'oneoff_' + sha256(b'private-2026-09-1').hexdigest())
                shuffled = deepcopy(months)
                for record in shuffled.values():
                    record['items'].reverse()
                before = deepcopy(shuffled)
                restored = summarize_history(shuffled, as_of=AS_OF, user_reincluded_one_offs=[identifier])
                self.assertEqual(restored['one_off_candidates'][0]['id'], identifier)
                self.assertTrue(restored['one_off_candidates'][0]['reincluded_by_user'])
                self.assertEqual(restored['stale_reincluded_ids'], [])
                self.assertEqual(restored['categories']['MEDICAL_HEALTH']['baseline_amount'], Fraction(190000, 3))
                self.assertEqual(shuffled, before)

    def test_identical_content_has_distinct_stable_candidate_ids(self):
        amounts = [10000 + 100*n for n in range(10)] + [170000, 170000]
        months = history([[], amounts, []])
        first = summarize_history(months, as_of=AS_OF)
        ids = {c['id'] for c in first['one_off_candidates']}
        self.assertEqual(len(ids), 2)
        random.Random(17).shuffle(months['2026-09']['items'])
        identifier = sorted(ids)[0]
        second = summarize_history(months, as_of=AS_OF, user_reincluded_one_offs=[identifier])
        self.assertEqual({c['id'] for c in second['one_off_candidates']}, ids)
        self.assertEqual(sum(c['excluded'] for c in second['one_off_candidates']), 1)
        self.assertEqual(sum(c['reincluded_by_user'] for c in second['one_off_candidates']), 1)

    def test_shifted_window_ignores_stale_id_and_restores_current_candidate(self):
        months = history([[170000], [10000], [10000]])
        stale = summarize_history(months, as_of=AS_OF)['one_off_candidates'][0]['id']
        months.pop('2026-08')
        months['2026-11'] = deepcopy(months['2026-09'])
        months['2026-11']['items'][0].update(occurred_on='2026-11-15', net_amount=170000)
        now = date(2026, 12, 3)
        current = summarize_history(months, as_of=now)['one_off_candidates'][0]['id']
        for builder in (build_history_budget, build_priority_history_budget):
            options = {'shortfall_cut_rates': None} if builder is build_priority_history_budget else {}
            with self.subTest(builder=builder.__name__):
                out = builder(budget_input(100000), months, as_of=now,
                              user_reincluded_one_offs=[stale, current], **options)
                self.assertEqual(out['status'], 'proposed')
                self.assertEqual(out['stale_reincluded_ids'], [stale])
                self.assertEqual(out['history']['stale_reincluded_ids'], [stale])
                self.assertTrue(out['one_off_candidates'][0]['reincluded_by_user'])
                self.assertEqual(out['allocations']['MEDICAL_HEALTH'], 63000)

    def test_stale_ids_do_not_block_monthly_totals_or_missing_history(self):
        stale = 'oneoff_' + 'a'*64
        totals = history([[], [], []])
        for row in totals.values():
            row.pop('items')
            row['monthly_totals'] = {'MEDICAL_HEALTH': 10000}
        for months, status in ((totals, 'ready'), ({}, 'needs_data')):
            with self.subTest(status=status):
                out = summarize_history(months, as_of=AS_OF, user_reincluded_one_offs=[stale])
                self.assertEqual(out['status'], status)
                self.assertEqual(out['stale_reincluded_ids'], [stale])

    def test_monthly_totals_use_median_not_fake_transactions(self):
        months=history([[],[],[]])
        for row,amount in zip(months.values(),(10000,180000,0)):
            row.pop('items')
            row['monthly_totals']={'MEDICAL_HEALTH':amount}
        out=summarize_history(months,as_of=AS_OF)
        self.assertEqual(out['baseline'],'median')
        self.assertEqual(out['one_off_unavailable'],'monthly_totals_only')
        self.assertFalse(out['one_off_candidates'])
        self.assertEqual(out['categories']['MEDICAL_HEALTH']['baseline_amount'],10000)
        self.assertEqual(out['categories']['MEDICAL_HEALTH']['monthly_purchase_counts'],[None]*3)
        budget=build_priority_history_budget(budget_input(),months,as_of=AS_OF,
                                             shortfall_cut_rates=dict.fromkeys(CATEGORY_CODES,50))
        self.assertEqual(budget['allocations']['MEDICAL_HEALTH'],10000)
        self.assertEqual(budget['baseline'],'median')

    def test_gaps_surplus_and_explanations(self):
        months=history([[10000],[10000,170000],[]])
        original=build_history_budget(budget_input(20000),months,as_of=AS_OF,baseline='average')
        adjusted=build_history_budget(budget_input(20000),months,as_of=AS_OF)
        self.assertEqual(original['gap_type'],'shortfall')
        self.assertEqual(adjusted['gap_type'],'surplus')
        self.assertEqual(adjusted['status'],'proposed')
        self.assertEqual(adjusted['allocations']['MEDICAL_HEALTH'],7000)
        self.assertEqual(adjusted['unallocated'],13000)
        self.assertIn('9월 의료비',adjusted['summary'][1]['text'])
        self.assertIn('170,000원',next(e['text'] for e in adjusted['baseline_explanations'] if e['category']=='MEDICAL_HEALTH'))

    def test_llm_payload_and_reinclude_override(self):
        months=history([[10000],[10000,170000],[]])
        months['2026-09']['items'][1]['merchant_key']='secret-clinic'
        identifier=summarize_history(months,as_of=AS_OF)['one_off_candidates'][0]['id']
        with patch.dict(os.environ,{'LLM_MODEL':'test-model'}),patch.object(plan_b_llm,'_request_tool',return_value={identifier:'one_off'}) as call:
            out=plan_b_llm.design_history_budget(budget_input(5000),months,as_of=AS_OF,
                shortfall_cut_rates=dict.fromkeys(CATEGORY_CODES,100),variant='one_off_review',
                allow_external=True,api_key='synthetic',user_reincluded_one_offs=[identifier])
        self.assertEqual(out['llm_status'],'completed')
        self.assertFalse(out['one_off_candidates'][0]['excluded'])
        payload=json.dumps(call.call_args.args[2])
        for forbidden in ('170000','secret-clinic','amount','merchant_key'):
            self.assertNotIn(forbidden,payload)
        self.assertEqual(call.call_args.args[0],'classify_one_offs')

    def test_bad_llm_enum_ids_timeout_use_identical_rule_result(self):
        months=history([[10000],[10000,170000],[]])
        identifier=summarize_history(months,as_of=AS_OF)['one_off_candidates'][0]['id']
        kwargs=dict(as_of=AS_OF,shortfall_cut_rates=dict.fromkeys(CATEGORY_CODES,100),variant='one_off_review')
        rule=plan_b_llm.design_history_budget(budget_input(5000),months,**kwargs)
        for answer in ({'wrong':'one_off'},{identifier:'bad'},{},None,[]):
            with patch.dict(os.environ,{'LLM_MODEL':'test-model'}),patch.object(plan_b_llm,'_request_tool',return_value=answer):
                out=plan_b_llm.design_history_budget(budget_input(5000),months,**kwargs,allow_external=True,api_key='test')
            self.assertEqual(out['llm_status'],'fallback')
            self.assertEqual(out['allocations'],rule['allocations'])
        with patch.dict(os.environ,{'LLM_MODEL':'test-model'}),patch.object(plan_b_llm,'_request_tool',side_effect=TimeoutError):
            out=plan_b_llm.design_history_budget(budget_input(5000),months,**kwargs,allow_external=True,api_key='test')
        self.assertEqual(out['llm_status'],'fallback')
        with patch.object(plan_b_llm,'_request_tool') as call:
            plan_b_llm.design_history_budget(budget_input(),months,**kwargs)
        call.assert_not_called()

    def test_llm_recurring_recalculates_surplus_and_free_money(self):
        months=history([[10000],[10000,170000],[]])
        identifier=summarize_history(months,as_of=AS_OF)['one_off_candidates'][0]['id']
        with patch.dict(os.environ,{'LLM_MODEL':'test-model'}),patch.object(plan_b_llm,'_request_tool',return_value={identifier:'recurring'}):
            out=plan_b_llm.design_history_budget(budget_input(100000),months,as_of=AS_OF,
                shortfall_cut_rates=None,variant='one_off_review',allow_external=True,api_key='synthetic')
        self.assertEqual(out['llm_status'],'completed')
        self.assertEqual(out['status'],'proposed')
        self.assertEqual(out['allocations']['MEDICAL_HEALTH'],63000)
        self.assertEqual(out['unallocated'],37000)
        self.assertEqual(out['surplus_policy'],'leave_as_free')
        self.assertEqual([r['code'] for r in out['summary']],['surplus_left_as_free'])

    def test_150_random_cases_money_settings_and_ledger(self):
        from budgets.test_design_explain import assert_ledger
        rng=random.Random(150)
        for number in range(150):
            months=history([[],[],[]])
            for c in ('FOOD','EDUCATION','MEDICAL_HEALTH'):
                for month,row in months.items():
                    amounts=[rng.randint(8000,12000) for _ in range(4)]
                    row['items'].extend({'occurred_on':month+'-15','category':c,'funding':'living',
                        'transaction_type':'spending','net_amount':amount} for amount in amounts)
            months['2026-09']['items'].append({'occurred_on':'2026-09-15','category':'MEDICAL_HEALTH',
                'funding':'living','transaction_type':'spending','net_amount':170000+number})
            # Test-only fixtures cycle through sparse, MAD and zero-MAD shopping; beauty has a recurring large purchase.
            shopping = ([30000,40000,50000,300000] if number % 3 == 0 else
                        [9000,9500,9800,9900,10000,10100,10200,10500,11000,300000] if number % 3 == 1 else
                        [10000]*10 + [300000])
            beauty = [9000,9500,9800,9900,10000,10100,10200,10500,11000,400000]
            for code, rows in (('FASHION_SHOPPING', [[],shopping,[]]),
                               ('BEAUTY', [[beauty[0]],beauty[1:],[]]),
                               ('HOUSEHOLD', [[50000],[],[45000]]),
                               ('CULTURE_LEISURE', [[10000],[10000,183000],[23000]])):
                for month, record in history(rows, code).items():
                    for item in record['items']:
                        if code == 'BEAUTY':
                            item['merchant_key'] = 'synthetic-recurring-salon'
                    months[month]['items'].extend(record['items'])
            for record in months.values():
                rng.shuffle(record['items'])
            data=budget_input(100000)
            data['minimums']={'FOOD':30000}
            data['locks']={'EDUCATION':25000}
            original=deepcopy((data,months))
            out=build_priority_history_budget(data,months,as_of=AS_OF,shortfall_cut_rates=dict.fromkeys(CATEGORY_CODES,100))
            self.assertEqual(out['status'],'proposed',number)
            self.assertEqual(sum(out['allocations'].values()),100000)
            self.assertGreaterEqual(out['allocations']['FOOD'],30000)
            self.assertEqual(out['allocations']['EDUCATION'],25000)
            self.assertTrue(any(c['excluded'] for c in out['one_off_candidates']))
            shopping_candidates = [c for c in out['one_off_candidates'] if c['category']=='FASHION_SHOPPING']
            self.assertEqual([(c['method'],c['excluded']) for c in shopping_candidates],
                             [('mad',True)] if number % 3 == 1 else [])
            beauty_candidates = [c for c in out['one_off_candidates'] if c['category']=='BEAUTY']
            self.assertEqual([(c['method'],c['excluded_by_recurrence'],c['excluded']) for c in beauty_candidates],
                             [('mad',True,False)])
            self.assertFalse(any(c['category']=='HOUSEHOLD' for c in out['one_off_candidates']))
            self.assertTrue(any(c['category']=='CULTURE_LEISURE' and c['excluded'] for c in out['one_off_candidates']))
            self.assertEqual((data,months),original)
            assert_ledger(self,out['explanations'],out['allocations'])


if __name__ == '__main__':
    unittest.main()
