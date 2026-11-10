"""ML cut floors: synthetic prices/rates only, no external calls or DB access."""
from copy import deepcopy
from datetime import date
from fractions import Fraction
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
from plan_b_history import CATEGORY_CODES, CHECKS
from plan_b_budget import build_priority_history_budget, build_history_budget, ml_cut_floors


class MLCutFloorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')))
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING':'false', 'LLM_MODEL':'test-model'}))
        self.inp = {'target': 1000000, 'minimums': {}, 'locks': {}, 'priorities': [
            {'category':c, 'rank':i} for i,c in enumerate(('TRANSPORT_CAR','FASHION_SHOPPING','BEAUTY'),1)]}
        values = dict(FOOD=555000, TRANSPORT_CAR=183900, HOUSEHOLD=23000, FASHION_SHOPPING=500000,
                      MEDICAL_HEALTH=10000, BEAUTY=65000, EDUCATION=0, CULTURE_LEISURE=23000)
        self.months = {m:{'coverage_complete':True, 'normalization_checks':dict.fromkeys(CHECKS,'verified'),
                          'monthly_totals':deepcopy(values)} for m in ('2026-08','2026-09','2026-10')}
        self.ml = dict.fromkeys(CATEGORY_CODES, 0)
        self.ml.update(FOOD=Fraction(991989,2), TRANSPORT_CAR=105353, FASHION_SHOPPING=107291,
                       HOUSEHOLD=50000, MEDICAL_HEALTH=60000, BEAUTY=78000, EDUCATION=10000, CULTURE_LEISURE=30000)
        self.options = dict(as_of=date(2026,11,4), shortfall_cut_rates=dict.fromkeys(CATEGORY_CODES,50),
                            reality_profile={'has_vehicle':False}, ml_reference=self.ml)

    def run_budget(self, **kwargs):
        return build_priority_history_budget(self.inp, self.months, **{**self.options, **kwargs})

    def assert_valid(self, out):
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(sum(out['allocations'].values())+out['unallocated'], self.inp['target'])
        for c, floor in out['ml_floors'].items():
            if c not in self.inp['locks']:
                self.assertGreaterEqual(out['allocations'][c], floor)
        for row in out['explanations']:
            self.assertEqual(sum(c['amount'] for c in row['causes']), row['diff'])

    def test_food_and_other_categories_cannot_cross_raw_ml_share(self):
        before = deepcopy((self.inp,self.months,self.ml))
        old = self.run_budget(ml_reference=None)
        out = self.run_budget()
        self.assert_valid(out)
        self.assertEqual(out['ml_floors']['FOOD'],496000)
        self.assertLess(old['allocations']['FOOD'],out['allocations']['FOOD'])
        self.assertLess(out['allocations']['FASHION_SHOPPING'],old['allocations']['FASHION_SHOPPING'])
        self.assertEqual(before,(self.inp,self.months,self.ml))

    def test_history_below_or_equal_ml_not_raised(self):
        out = self.run_budget()
        self.assertNotIn('MEDICAL_HEALTH',out['ml_floors'])
        self.assertLessEqual(out['allocations']['MEDICAL_HEALTH'],10000)
        self.assertNotIn('HOUSEHOLD',out['ml_floors'])
        self.ml['FOOD']=555000
        self.assertNotIn('FOOD',self.run_budget()['ml_floors'])

    def test_user_lock_wins_without_changing_minimums(self):
        self.inp['locks']['FOOD']=200000
        out=self.run_budget()
        self.assert_valid(out)
        self.assertEqual(out['allocations']['FOOD'],200000)
        self.assertIn('FOOD',out['ml_floor_ignored_locked'])

    def test_user_minimum_and_floor_unaffordable_returns_shortfall(self):
        self.inp['minimums']['FASHION_SHOPPING']=500000
        out=self.run_budget()
        self.assertEqual(out['status'],'needs_upper_allocation_review')
        self.assertEqual(out['reason'],'ml_floor_unaffordable')
        self.assertEqual(out['shortfall'],102000)
        self.assertIsNone(out['result'])

    def test_all_categories_use_same_rule(self):
        from plan_b_history import summarize_history
        hist=summarize_history(self.months,as_of=self.options['as_of'])
        for c in CATEGORY_CODES:
            hist['categories'][c]['baseline_amount']=20000
        ml=dict.fromkeys(CATEGORY_CODES,15001)
        floors=ml_cut_floors(hist,ml)
        self.assertEqual(set(floors),set(CATEGORY_CODES)-{'HOUSING_TELECOM','TRAVEL_LODGING'})
        self.assertEqual(set(floors.values()),{16000})

    def test_invalid_or_partial_ml_reference_rejected(self):
        for ml in ({}, {**self.ml,'FOOD':True}, {**self.ml,'FOOD':-1}, {**self.ml,'FOOD':float('nan')}):
            with self.subTest(ml=ml),self.assertRaisesRegex(ValueError,'invalid_ml_reference'):
                self.run_budget(ml_reference=ml)

    def test_surplus_rounding_does_not_cross_ml_floor(self):
        self.inp['target']=2000000
        for m in self.months.values():
            m['monthly_totals']['FOOD']=555400
        self.ml['FOOD']=555300
        out=self.run_budget()
        self.assert_valid(out)
        self.assertEqual(out['allocations']['FOOD'],556000)

    def test_rule_llm_failure_fallback_retains_ml_floor(self):
        from plan_b_llm import design_history_budget
        with patch('plan_b_llm._request_tool',side_effect=TimeoutError):
            out=design_history_budget(self.inp,self.months,**self.options,
                variant='priority_increase_first',increase_threshold=0,allow_external=True,api_key='test')
        self.assertEqual(out['llm_status'],'fallback')
        self.assert_valid(out)

    def test_forecast_bounds_and_fallback_keep_ml_floor(self):
        from plan_b_forecast import forecast_budget
        # Floor-aware rule budget is the mandatory fallback; malformed proposals cannot bypass it.
        with patch('plan_b_forecast._request_completion',return_value={}) as call:
            out=forecast_budget(self.inp,self.months,**self.options,target_month=11,
                forecast_band={'down_percent':80,'up_percent':20},synthetic=True,
                allow_external=True,api_key='test')
        self.assertEqual(out['llm_status'],'fallback')
        self.assertGreaterEqual(out['bounds']['FOOD']['lower'],496000)
        self.assert_valid(out)
        self.assertEqual(call.call_count,2)

    def test_proportional_comparison_also_checked(self):
        out=build_history_budget(self.inp,self.months,as_of=self.options['as_of'],ml_reference=self.ml)
        out['allocations']=out['result']['allocations']
        self.assert_valid(out)
