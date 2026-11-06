"""Synthetic cut rates are experiment fixtures, never service defaults."""
from copy import deepcopy
from datetime import date
import os
from fractions import Fraction
import random
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
from plan_b_budget import (build_history_budget, build_priority_history_budget,
                           fixed_shortfall_order, validate_shortfall_order)
from plan_b_history import CATEGORY_CODES
from test_plan_b_budget import inputs
from test_plan_b_history import fixture, purchase

AS_OF = date(2026, 11, 3)


def scenario(name='increase'):
    history = fixture()
    profile = {'age': 27, 'sex': 1, 'disposable_income': 3_000_000,
               'has_vehicle': 0, 'fixed_expense_amount': 700_000, 'living_budget_amount': 800_000}
    data = inputs(target=800_000)
    values = {'FOOD': [400_000]*3, 'EDUCATION': [100_000]*3, 'MEDICAL_HEALTH': [100_000]*3,
              'FASHION_SHOPPING': [100_000, 100_000, 400_000], 'CULTURE_LEISURE': [200_000]*3}
    protected = []
    if name == 'top':
        values = {'FOOD': [800_000]*3, 'EDUCATION': [100_000]*3, 'MEDICAL_HEALTH': [100_000]*3}
        data['target'] = 600_000
    elif name == 'undo':
        protected = ['FASHION_SHOPPING', 'CULTURE_LEISURE']
    elif name == 'surplus':
        data['target'] = 1_200_000
    elif name == 'travel':
        values['TRAVEL_LODGING'] = [0, 600_000, 0]
    for i, (month, row) in enumerate(history.items()):
        row['items'] = [purchase(month, amounts[i], c) for c, amounts in values.items()]
        row.pop('actual_savings')
    profile['living_budget_amount'] = data['target']
    # Explicit synthetic test rate; this is not a policy recommendation.
    rates = dict.fromkeys(CATEGORY_CODES, 50)
    return data, history, rates, protected, profile


class PriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))

    def build(self, name='increase', **overrides):
        overrides.setdefault('baseline', 'average')  # Existing allocation/rounding regressions.
        data, history, rates, protected, _ = scenario(name)
        return build_priority_history_budget(overrides.pop('inp', data), overrides.pop('months', history),
            as_of=AS_OF, shortfall_cut_rates=overrides.pop('rates', rates),
            user_protected=overrides.pop('user_protected', protected), **overrides)

    def test_proportional_split_and_protection(self):
        data, history, rates, _, _ = scenario()
        for month,row in history.items():
            row['items']=[purchase(month,a,c) for c,a in [('FOOD',400000),('EDUCATION',100000),('MEDICAL_HEALTH',100000),('FASHION_SHOPPING',200000),('CULTURE_LEISURE',200000),('BEAUTY',100000)]]
        data['target']=1_000_000  # deficit 100,000; 50% is a test rate.
        out=self.build(inp=data,months=history,rates=rates)
        self.assertEqual({x['category']:x['amount'] for x in out['cuts']}, {'FASHION_SHOPPING':40000,'CULTURE_LEISURE':40000,'BEAUTY':20000})
        out=self.build(inp=data,months=history,rates=rates,user_protected=['BEAUTY'])
        self.assertEqual(out['allocations']['BEAUTY'],100000)
        self.assertEqual({x['amount'] for x in out['cuts']},{50000})
    def test_split_small_capacity_and_remainder(self):
        data,history,rates,_,_=scenario()
        data['target']=899500
        rates['FASHION_SHOPPING']=5
        out=self.build(inp=data,months=history,rates=rates)
        self.assertEqual(sum(out['allocations'].values()),899500)
        self.assertLessEqual(sum(x['amount'] for x in out['cuts'] if x['category']=='FASHION_SHOPPING'),10000)
        self.assertTrue(out['rounding_adjustments'])

    def test_unranked_first_protects_all_three_top_categories(self):
        out = self.build()
        self.assertEqual(out['variant'], 'priority_order')
        self.assertEqual([(c['category'], c['amount']) for c in out['cuts']],
                         [('FASHION_SHOPPING', 100_000), ('CULTURE_LEISURE', 100_000)])
        self.assertEqual(out['allocations']['FOOD'], 400_000)
        self.assertEqual(out['allocations']['EDUCATION'], 100_000)
        self.assertEqual(out['remaining_shortfall'], 0)
        self.assertEqual(sum(out['allocations'].values()), 800_000)

    def test_system_floor_above_and_below_history(self):
        data, history, rates, _, _ = scenario()
        for amount in (80000,30000):
            with self.subTest(amount=amount):
                local = deepcopy(history)
                for month,row in local.items():
                    row['items'].append(purchase(month,amount,'TRANSPORT_CAR'))
                out = self.build(inp=data,months=local,rates=rates,reality_profile={'has_vehicle':False})
                self.assertGreaterEqual(out['allocations']['TRANSPORT_CAR'],min(amount,60000))
                if amount == 30000:
                    self.assertEqual(out['allocations']['TRANSPORT_CAR'],30000)

    def test_system_floor_can_block_shortfall(self):
        data,history,rates,_,_ = scenario()
        for month,row in history.items():
            row['items'].append(purchase(month,30000,'TRANSPORT_CAR'))
        data['target']=510000
        out=self.build(inp=data,months=history,rates=dict.fromkeys(CATEGORY_CODES,100),
                       user_protected=['FOOD','EDUCATION','MEDICAL_HEALTH','FASHION_SHOPPING','CULTURE_LEISURE'],
                       reality_profile={'has_vehicle':False})
        self.assertEqual(out['reason'],'shortfall_exceeds_cut_limits')
        self.assertTrue(out['system_floor_limited'])

    def test_only_top_spending_cuts_third_then_second_then_first(self):
        out = self.build('top')
        self.assertEqual([c['rank'] for c in out['cuts']], [3, 2, 1])
        self.assertEqual(out['allocations']['FOOD'], 500_000)

    def test_undo_unranked_can_force_top_priority_cuts(self):
        out = self.build('undo')
        self.assertEqual(out['allocations']['FASHION_SHOPPING'], 200_000)
        self.assertEqual(out['allocations']['CULTURE_LEISURE'], 200_000)
        self.assertEqual([c['rank'] for c in out['cuts']], [3, 2, 1])
        self.assertEqual(out['allocations']['FOOD'], 300_000)

    def test_undo_recalculation_preserves_history_input(self):
        data, history, rates, _, _ = scenario()
        original = deepcopy((data, history, rates))
        first = self.build(inp=data, months=history, rates=rates)
        second = self.build(inp=data, months=history, rates=rates, user_protected=['FASHION_SHOPPING'])
        self.assertEqual(second['allocations']['FASHION_SHOPPING'], first['start_allocations']['FASHION_SHOPPING'])
        self.assertEqual((data, history, rates), original)

    def test_low_rank_precedes_top_and_order_is_reverse_rank(self):
        data, _, _, _, _ = scenario()
        data['priorities'] += [{'category': 'FASHION_SHOPPING', 'rank': 4}, {'category': 'CULTURE_LEISURE', 'rank': 5}]
        out = self.build(inp=data)
        self.assertEqual([c['rank'] for c in out['cuts']], [5, 4])
        self.assertEqual([c['tier'] for c in out['cuts']], ['low', 'low'])

    def test_minimum_and_lock_exactly_preserved(self):
        data, _, _, _, _ = scenario()
        data.update(minimums={'FASHION_SHOPPING': 190_000}, locks={'CULTURE_LEISURE': 200_000})
        out = self.build(inp=data)
        self.assertEqual(out['allocations']['FASHION_SHOPPING'], 190_000)
        self.assertEqual(out['allocations']['CULTURE_LEISURE'], 200_000)
        self.assertEqual(out['effective_input']['priorities'], data['priorities'])

    def test_insufficient_rate_capacity_has_no_partial_budget(self):
        out = self.build(rates=dict.fromkeys(CATEGORY_CODES, 1))
        self.assertEqual(out['reason'], 'shortfall_exceeds_cut_limits')
        self.assertEqual(out['remaining_shortfall'], 190_000)
        self.assertIsNone(out['result'])
        self.assertIsNone(out['allocations'])

    def test_invalid_rates_and_protection_rejected(self):
        for rates in (None, {}, {'FOOD': 10}, dict.fromkeys(CATEGORY_CODES, True),
                      dict.fromkeys(CATEGORY_CODES, float('nan')), dict.fromkeys(CATEGORY_CODES, 0),
                      dict.fromkeys(CATEGORY_CODES, 101)):
            with self.subTest(rates=rates), self.assertRaises(ValueError):
                self.build(rates=rates)
        for protected in ('FOOD', ['TRAVEL_LODGING'], ['HOUSING_TELECOM'], ['FOOD','FOOD'], ['NOPE'], [True]):
            with self.subTest(protected=protected), self.assertRaises(ValueError):
                self.build(user_protected=protected)

    def test_fractional_start_and_sub_thousand_gap_use_rounding(self):
        data, history, _, _, _ = scenario()
        history['2026-10']['items'].append(purchase('2026-10', 1, 'FOOD'))
        out = self.build(months=history)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(sum(out['allocations'].values()), 800_000)
        self.assertNotEqual(out['starting_rounding']['FOOD'], 0)
        data['target'] -= 500
        out = self.build(inp=data)
        self.assertEqual(sum(out['allocations'].values()), 799_500)
        self.assertEqual(sum(r['amount'] for r in out['rounding_adjustments']), -500)

    def test_rounding_half_up_not_bankers_rounding(self):
        _, history, _, _, _ = scenario()
        for month, row in history.items():
            row['items'].append(purchase(month, 500, 'FOOD'))
        out = self.build(months=history)
        self.assertEqual(out['start_allocations']['FOOD'], 401_000)
        self.assertEqual(sum(out['allocations'].values()), 800_000)

    def test_rounding_residual_cannot_violate_minimum_lock_or_user_protection(self):
        data, _, _, _, _ = scenario()
        data.update(target=799_500, minimums={'FASHION_SHOPPING': 199_999}, locks={'CULTURE_LEISURE': 200_000})
        out = self.build(inp=data, user_protected=['EDUCATION'])
        self.assertGreaterEqual(out['allocations']['FASHION_SHOPPING'], 199_999)
        self.assertEqual(out['allocations']['CULTURE_LEISURE'], 200_000)
        self.assertEqual(out['allocations']['EDUCATION'], 100_000)
        self.assertEqual(sum(out['allocations'].values()), 799_500)

    def test_rounding_cannot_exceed_cut_rate(self):
        data, _, _, _, _ = scenario()
        data['target'] = 499_500  # 500,500 required; all category caps together are 500,000.
        out = self.build(inp=data)
        self.assertIsNone(out['result'])
        self.assertEqual(out['remaining_shortfall'], 500)

    def test_rounding_loss_is_added_back_without_changing_protected_start(self):
        data, history, _, _, _ = scenario()
        for month,row in history.items():
            row['items'] = [purchase(month, 400_100, 'FOOD'), purchase(month, 100_100, 'EDUCATION'),
                            purchase(month, 100_100, 'MEDICAL_HEALTH')]
        data['target'] = 600_200
        out = self.build(inp=data, months=history, user_protected=['FOOD'])
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(sum(out['allocations'].values()), 600_200)
        self.assertEqual(out['allocations']['FOOD'], 400_000)
        self.assertEqual(sum(r['amount'] for r in out['rounding_adjustments']), 200)

    def test_travel_and_inactive_housing_are_separate_not_mixed(self):
        data, history, _, _, _ = scenario('travel')
        for month, row in history.items():
            row['items'].append(purchase(month, 200_000, 'HOUSING_TELECOM'))
        out = self.build(inp=data, months=history)
        self.assertEqual(out['scope_state'], 'aligned')
        self.assertEqual(out['excluded_spending'], {'TRAVEL_LODGING': 200_000, 'HOUSING_TELECOM': 200_000})
        self.assertEqual(out['comparison_history_total'], 1_000_000)
        self.assertEqual(out['allocations']['TRAVEL_LODGING'], 0)

    def test_housing_cannot_be_user_protected(self):
        with self.assertRaises(ValueError):
            self.build(user_protected=['HOUSING_TELECOM'])

    def test_surplus_experiment_and_proportional_variant_retained(self):
        out = self.build('surplus', experiment_capped=True)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['unallocated'], 200000)
        self.assertIsNotNone(out['experiment_capped_result']['result'])
        data, history, _, _, _ = scenario()
        proportional = build_history_budget(data, history, as_of=AS_OF, baseline='average')
        self.assertEqual(proportional['variant'], 'proportional')
        self.assertEqual(proportional['result']['allocations']['FOOD'], 320_000)

    def test_surplus_start_and_free_money_preserve_settings_and_ledger(self):
        from budgets.test_design_explain import assert_ledger
        data, history, _, _, _ = scenario('surplus')
        data['minimums'] = {'FOOD':450001}
        data['locks'] = {'EDUCATION':120002}
        before = deepcopy((data,history))
        out = self.build(inp=data, months=history)
        self.assertEqual(out['allocations'], out['start_allocations'])
        self.assertEqual(out['unallocated'],129997)
        self.assertEqual(out['result']['unallocated'],out['unallocated'])
        self.assertEqual(out['effective_input']['target'],1200000)
        self.assertEqual(out['allocations']['FOOD'],450001)
        self.assertEqual(out['allocations']['EDUCATION'],120002)
        self.assertEqual((data,history),before)
        assert_ledger(self,out['explanations'],out['allocations'])

    def test_surplus_minimum_raises_start_over_target_then_uses_rank_cuts(self):
        data,history,_,_,_ = scenario('surplus')
        data['minimums']={'FOOD':700000}
        out=self.build(inp=data,months=history)
        self.assertEqual(out['status'],'proposed')
        self.assertTrue(out['settings_require_shortfall'])
        self.assertEqual(out['variant'],'priority_order')
        self.assertEqual(out['unallocated'],0)
        self.assertEqual(out['allocations']['FOOD'],700000)
        self.assertEqual(sum(c['amount'] for c in out['cuts']),100000)
        self.assertEqual(sum(out['allocations'].values()),1200000)

    def test_surplus_applies_system_floor_but_lock_wins(self):
        from budgets.test_design_explain import assert_ledger
        data,history,_,_,_ = scenario('surplus')
        out=self.build(inp=data,months=history,reality_profile={'has_vehicle':False})
        self.assertEqual(out['allocations']['TRANSPORT_CAR'],0)
        self.assertEqual(out['unallocated'],200000)
        assert_ledger(self,out['explanations'],out['allocations'])
        data['locks']={'TRANSPORT_CAR':12345}
        out=self.build(inp=data,months=history,reality_profile={'has_vehicle':False})
        self.assertEqual(out['allocations']['TRANSPORT_CAR'],12345)
        self.assertEqual(out['unallocated'],187655)

    def test_surplus_floor_pushes_start_to_shortfall_without_breaking_floor(self):
        data,history,_,_,_ = scenario('surplus')
        data['target']=1020000
        out=self.build(inp=data,months=history,reality_profile={'has_vehicle':False})
        self.assertEqual(out['status'],'proposed')
        self.assertEqual(out['allocations']['TRANSPORT_CAR'],0)
        self.assertEqual(out['unallocated'],20000)
        self.assertEqual(sum(out['allocations'].values()),1000000)
        self.assertEqual(out['cuts'],[])

    def test_surplus_all_locked_leaves_remainder(self):
        data,history,_,_,_=scenario('surplus')
        data['locks']={c:100000 for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM','TRAVEL_LODGING')}
        out=self.build(inp=data,months=history)
        self.assertEqual(out['status'],'proposed')
        self.assertEqual(out['unallocated'],400000)
        self.assertEqual(sum(out['allocations'].values()),800000)

    def test_lock_below_history_uses_actual_start_and_leaves_remainder(self):
        months = fixture()
        for month, row in months.items():
            row['items'] = [purchase(month, 100000, 'FOOD'), purchase(month, 100000, 'EDUCATION')]
        data = inputs(target=150000, locks={'EDUCATION': 10000})
        before = deepcopy((data, months))
        out = self.build(inp=data, months=months, available_spare=200000)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['allocations']['FOOD'], 100000)
        self.assertEqual(out['allocations']['EDUCATION'], 10000)
        self.assertEqual(out['unallocated'], 40000)
        self.assertEqual(out['spare_transfer'], 0)
        self.assertEqual(out['gap_type'], 'shortfall')
        self.assertEqual((data, months), before)
        for target in (110000, 90000):
            with self.subTest(target=target):
                result = self.build(inp={**data, 'target': target}, months=months)
                self.assertEqual(result['status'], 'proposed')
                self.assertEqual(result['allocations']['EDUCATION'], 10000)
                self.assertEqual(result['allocations']['FOOD'], target-10000)
                self.assertEqual(result['unallocated'], 0)

    def test_all_locked_below_history_keeps_remainder_even_with_historical_shortfall(self):
        months = fixture()
        active = [c for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')]
        for month, row in months.items():
            row['items'] = [purchase(month, 100000, c) for c in active]
        data = inputs(target=500000, locks=dict.fromkeys(active, 50000))
        out = self.build(inp=data, months=months)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['unallocated'], 100000)
        self.assertTrue(all(out['allocations'][c] == 50000 for c in active))
        self.assertEqual(out['allocation_gap'], -300000)
        self.assertEqual(out['scope_state'], 'aligned')
        self.assertIsNone(out.get('next_action'))
        self.assertEqual(out['result']['conflicts'], [])

    def test_smaller_lock_and_rounding_excess_uses_existing_cut_path(self):
        months = fixture()
        for month, row in months.items():
            row['items'] = [purchase(month, 100600, 'FOOD'), purchase(month, 100000, 'EDUCATION')]
        data = inputs(target=110800, locks={'EDUCATION': 10000})
        out = self.build(inp=data, months=months)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['allocations']['EDUCATION'], 10000)
        self.assertEqual(out['allocations']['FOOD'], 100800)
        self.assertEqual(out['unallocated'], 0)

    def test_local_validator_rejects_bad_remainder_and_broken_protection(self):
        from plan_b_budget import check_result_with_unallocated
        from budgets.recommend import EngineCheckFailed
        data,history,_,_,_=scenario('surplus')
        for month,row in history.items():
            row['items'].append(purchase(month,60000,'TRANSPORT_CAR'))
        data['minimums']={'FOOD':400000}
        data['locks']={'EDUCATION':100000}
        out=self.build(inp=data,months=history,reality_profile={'has_vehicle':False})
        before=deepcopy((data,out))
        for free in (-1, True, 145001, 145000.0):
            with self.subTest(free=free),self.assertRaises(EngineCheckFailed):
                check_result_with_unallocated(data,{**out['result'],'unallocated':free},floors=out['system_floors'])
        for code,value in (('FOOD',399999),('EDUCATION',99999),('TRANSPORT_CAR',59999),('BEAUTY',-1)):
            broken=deepcopy(out['result'])
            broken['unallocated']+=broken['allocations'][code]-value
            broken['allocations'][code]=value
            with self.subTest(code=code),self.assertRaises(EngineCheckFailed):
                check_result_with_unallocated(data,broken,floors=out['system_floors'])
        self.assertEqual((data,out),before)

    def test_transport_history_does_not_get_raised_and_car_floor_is_protected(self):
        for vehicle, history_amount, expected in ((False, 30000, 30000), (True, 30000, 30000),
                                                   (False, 80000, 60000), (True, 140000, 100000)):
            with self.subTest(vehicle=vehicle, history=history_amount):
                data, history, rates, _, _ = scenario()
                for month, row in history.items():
                    row['items'].append(purchase(month, history_amount, 'TRANSPORT_CAR'))
                out = self.build(inp=data, months=history, rates=rates, reality_profile={'has_vehicle': vehicle})
                self.assertEqual(out['system_floors']['TRANSPORT_CAR'], expected)
                self.assertGreaterEqual(out['allocations']['TRANSPORT_CAR'], expected)

    def test_rule_order_growth_then_spike_then_code_and_null_last(self):
        data, history, _, _, _ = scenario()
        base = build_history_budget(data, history, as_of=AS_OF)
        orders, reasons = fixed_shortfall_order(base)
        self.assertEqual(orders['unranked'][:2], ['FASHION_SHOPPING', 'CULTURE_LEISURE'])
        self.assertEqual(reasons['FASHION_SHOPPING'], 'recent_increase')
        validate_shortfall_order(base, orders, reasons)

    def test_group_crossing_or_invalid_reason_cannot_change_priority(self):
        data, history, _, _, _ = scenario()
        base = build_history_budget(data, history, as_of=AS_OF)
        orders, reasons = fixed_shortfall_order(base)
        orders['unranked'][0], orders['rank_1'][0] = orders['rank_1'][0], orders['unranked'][0]
        with self.assertRaises(ValueError):
            self.build(group_orders=orders, reason_codes=reasons)

    def test_explanations_use_server_cut_amounts_and_codes(self):
        out = self.build()
        self.assertEqual(out['result']['reasons'], [])
        top = self.build('top')
        self.assertEqual(top['result']['reasons'], [r['text'] for r in top['explanations'] if r['text']])
        first = [text for text in top['result']['reasons'] if text.startswith('식비(1순위)')]
        self.assertEqual(len(first), 1)
        self.assertIn('식비 말고는 더 줄일 수 있는 항목이 없었어요.', first[0])
        self.assertTrue(all(c['order_source']=='rule' for c in out['cuts']))

    def test_category_explanations_cut_tiers_protection_and_inactive(self):
        for name in ('increase', 'top', 'undo', 'travel'):
            out = self.build(name)
            rows = {r['category']: r for r in out['explanations']}
            from budgets.test_design_explain import assert_ledger
            assert_ledger(self, out['explanations'], out['allocations'])
            if name == 'increase':
                self.assertEqual(rows['FASHION_SHOPPING']['text'], '')
            if name == 'top':
                self.assertIn('식비 말고는 더 줄일 수 있는 항목이 없었어요.', rows['FOOD']['text'])
                third = next(c for c in rows['MEDICAL_HEALTH']['causes'] if c['code'] == 'shortfall_top_rank_cut')
                self.assertNotIn('EDUCATION', third['detail']['exhausted_categories'])
                self.assertIn('cut_limit_reached', [c['code'] for c in rows['EDUCATION']['causes']])
            if name == 'undo':
                self.assertEqual(rows['FASHION_SHOPPING']['causes'][0]['code'], 'user_protected')
            if name == 'travel':
                self.assertEqual(rows['TRAVEL_LODGING']['diff'], -200000)
                self.assertEqual(rows['TRAVEL_LODGING']['text'], '')

    def test_only_culture_can_be_cut_explains_third_priority_conflict(self):
        data, history, rates, _, _ = scenario()
        data['target'] = 700000
        data['priorities'] = [{'category': c, 'rank': n} for n,c in enumerate(('FOOD','EDUCATION','CULTURE_LEISURE'),1)]
        data['locks'] = {'FOOD': 400000, 'EDUCATION': 100000, 'MEDICAL_HEALTH': 100000}
        for month, row in history.items():
            row['items'] = [purchase(month, a, c) for c,a in {**data['locks'], 'CULTURE_LEISURE': 200000}.items()]
        out = self.build(inp=data, months=history, rates=rates)
        row = next(r for r in out['explanations'] if r['category'] == 'CULTURE_LEISURE')
        self.assertEqual(row['text'], '문화·여가(3순위): 문화·여가 말고는 더 줄일 수 있는 항목이 없었어요.')
        self.assertEqual(out['result']['reasons'], [row['text']])
        self.assertEqual(out['allocations']['CULTURE_LEISURE'], 100000)
        self.assertEqual(sum(out['allocations'].values()), 700000)
        ordinary = self.build('top')
        third = next(r for r in ordinary['explanations'] if r['category'] == 'MEDICAL_HEALTH')
        self.assertNotIn('말고는 더 줄일 수 있는 항목이 없었어요', third['text'])

    def test_floor_and_minimum_explanation(self):
        data, history, rates, _, _ = scenario()
        for month, row in history.items():
            row['items'].append(purchase(month, 80000, 'TRANSPORT_CAR'))
        data['minimums']['EDUCATION'] = 120000
        out = self.build(inp=data, months=history, rates=rates, reality_profile={'has_vehicle': False})
        rows = {r['category']: r for r in out['explanations']}
        self.assertIn('system_floor_protected', [c['code'] for c in rows['TRANSPORT_CAR']['causes']])
        self.assertIn('user_minimum_raise', [c['code'] for c in rows['EDUCATION']['causes']])

    def test_varied_rounding_rates_and_protection_preserve_constraints(self):
        rng = random.Random(23)
        for case in range(150):
            data, history, rates, _, _ = scenario()
            for month, row in history.items():
                row['items'] = [purchase(month, rng.randrange(1, 400_000), c) for c in
                                ('FOOD','EDUCATION','MEDICAL_HEALTH','FASHION_SHOPPING','CULTURE_LEISURE')]
            total = sum(i['net_amount'] for row in history.values() for i in row['items']) // 3
            data['target'] = rng.randrange(max(1,total//3), total)
            data['minimums']['FASHION_SHOPPING'] = rng.randrange(0,10_000)
            if case % 4 == 0:
                data['locks']['MEDICAL_HEALTH'] = rng.randrange(0,100_000)
            protected = ['CULTURE_LEISURE'] if case % 3 == 0 else []
            rates = {c:rng.choice((10,25,50,100)) for c in CATEGORY_CODES}
            out = self.build(inp=data,months=history,rates=rates,user_protected=protected)
            if out['status'] != 'proposed':
                continue
            self.assertEqual(sum(out['allocations'].values())+out['unallocated'],data['target'])
            from budgets.test_design_explain import assert_ledger
            assert_ledger(self, out['explanations'], out['allocations'])
            for code, amount in out['allocations'].items():
                self.assertGreaterEqual(amount,data['minimums'].get(code,0))
                if code in data['locks']:
                    self.assertEqual(amount,data['locks'][code])
                if code in protected:
                    self.assertEqual(amount,out['start_allocations'][code])
                donated=sum(c['amount'] for c in out['cuts'] if c['category']==code)
                self.assertLessEqual(donated,out['start_allocations'][code]*Fraction(rates[code],100))


if __name__ == '__main__':
    unittest.main()
