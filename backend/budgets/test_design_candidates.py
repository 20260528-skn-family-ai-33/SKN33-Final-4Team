"""Synthetic money invariants and exact D2 proposal cases; no DB or external API."""
from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from budgets.design_candidates import apply_priority_decisions, build_candidates, ml_profile, select_candidate
from budgets.recommend import CATEGORY_CODES, EngineCheckFailed, check_result


def inp(**changes):
    return {"target": 1_000_000, "minimums": {}, "locks": {},
            "priorities": priorities(*(c for c in CATEGORY_CODES if c != 'TRAVEL_LODGING')),
            "age": 27, "sex": "female", "has_vehicle": False,
            "take_home_income": 3_000_000, "housing_fixed": 700_000, **changes}


def priorities(*codes):
    return [{'category':c,'rank':n} for n,c in enumerate(codes,1)]


def limits(data, total=30000, increase=30000):
    # Synthetic fixture numbers only; no production default.
    top = [r['category'] for r in data['priorities'] if r['rank'] <= 3]
    return {'total_limit':total, 'increase_limits':dict.fromkeys(top,increase),
            'increase_rates':dict.fromkeys(top,1000),
            'decrease_limits':dict.fromkeys(CATEGORY_CODES,10000),
            'ceilings':dict.fromkeys(top,1000000)}


class DesignTests(SimpleTestCase):
    def setUp(self):
        self.ratios = dict.fromkeys(CATEGORY_CODES, 0.1)

    def test_default_does_not_enable_unapproved_policy(self):
        result = build_candidates(inp(), self.ratios)
        self.assertEqual([c['id'] for c in result['candidates']], ['base'])
        self.assertEqual(result['candidates'][0]['result']['allocations']['HOUSING_TELECOM'],0)

    def test_unranked_discretionary_before_other_unranked(self):
        data = inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        result = build_candidates(data, self.ratios, policy=limits(data))
        self.assertEqual(result['transfers'], [{'from':'FASHION_SHOPPING','to':'FOOD','amount':10000},
                                               {'from':'BEAUTY','to':'FOOD','amount':10000},
                                               {'from':'CULTURE_LEISURE','to':'FOOD','amount':10000}])

    def test_system_floor_reallocation_and_original_unchanged(self):
        data = inp()
        before = deepcopy(data)
        ratios = dict(self.ratios)
        ratios['TRANSPORT_CAR'] = 0.001
        plain = build_candidates(data, ratios)
        out = build_candidates(data, ratios, reality_profile={'has_vehicle': False})
        self.assertLess(plain['candidates'][0]['result']['allocations']['TRANSPORT_CAR'],55000)
        self.assertEqual(out['candidates'][0]['result']['allocations']['TRANSPORT_CAR'],55000)
        self.assertIn('TRANSPORT_CAR',out['floor_applied'])
        self.assertEqual(data,before)
        for candidate in out['candidates']:
            check_result(data,candidate['result'])
            self.assertEqual(sum(candidate['result']['allocations'].values()),data['target'])

    def test_system_floor_user_minimum_lock_and_unaffordable(self):
        data = inp(minimums={'TRANSPORT_CAR':80000})
        out = build_candidates(data,self.ratios,reality_profile={'has_vehicle':False})
        self.assertGreaterEqual(out['candidates'][0]['result']['allocations']['TRANSPORT_CAR'],80000)
        data = inp(locks={'TRANSPORT_CAR':20000})
        out = build_candidates(data,self.ratios,reality_profile={'has_vehicle':False})
        self.assertEqual(out['candidates'][0]['result']['allocations']['TRANSPORT_CAR'],20000)
        self.assertEqual(out['floor_ignored_locked'],['TRANSPORT_CAR'])
        data = inp(target=50000)
        out = build_candidates(data,self.ratios,reality_profile={'has_vehicle':False})
        self.assertEqual(out['status'],'needs_upper_allocation_review')
        self.assertEqual(out['reason'],'system_floor_unaffordable')
        self.assertEqual(out['shortfall'],5000)
        self.assertIsNone(out['result'])

    def test_floor_protects_donor_and_none_is_unchanged(self):
        data = inp(priorities=priorities('FOOD','EDUCATION','MEDICAL_HEALTH'))
        out = build_candidates(data,self.ratios,policy=limits(data,total=400000),reality_profile={'has_vehicle':False})
        for candidate in out['candidates']:
            self.assertGreaterEqual(candidate['result']['allocations']['TRANSPORT_CAR'],55000)
        self.assertEqual(build_candidates(data,self.ratios),build_candidates(data,self.ratios,reality_profile=None))

    def test_llm_replay_floor_protects_donor_and_input(self):
        # Synthetic policy: only transport can fund the requested food increase.
        data = inp(target=500000, priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        policy = limits(data, total=100000, increase=100000)
        policy['decrease_limits'] = dict.fromkeys(CATEGORY_CODES, 0)
        policy['decrease_limits']['TRANSPORT_CAR'] = 100000
        before = deepcopy((data, policy))
        result = apply_priority_decisions(data, self.ratios, policy=policy,
            decisions=[{'category': 'FOOD', 'action': 'increase'}], reality_profile={'has_vehicle': False})
        self.assertEqual(result['allocations']['TRANSPORT_CAR'], 55000)
        self.assertEqual(result['allocations']['FOOD'], 85000)
        self.assertEqual(result['transfers'], [{'from': 'TRANSPORT_CAR', 'to': 'FOOD', 'amount': 5000}])
        self.assertEqual(sum(result['allocations'].values()), 500000)
        self.assertEqual((data, policy), before)

    def test_llm_replay_floor_none_unaffordable_and_lock(self):
        data = inp()
        kwargs = {'policy': limits(data), 'decisions': []}
        self.assertEqual(apply_priority_decisions(data, self.ratios, **kwargs),
                         apply_priority_decisions(data, self.ratios, **kwargs, reality_profile=None))
        data['target'] = 50000
        result = apply_priority_decisions(data, self.ratios, **kwargs, reality_profile={'has_vehicle': False})
        self.assertEqual(result, build_candidates(data, self.ratios, policy=kwargs['policy'],
                                                  reality_profile={'has_vehicle': False}))
        self.assertEqual(result['reason'], 'system_floor_unaffordable')
        data = inp(locks={'TRANSPORT_CAR': 20000})
        result = apply_priority_decisions(data, self.ratios, **kwargs, reality_profile={'has_vehicle': False})
        self.assertEqual(result['allocations']['TRANSPORT_CAR'], 20000)
        self.assertEqual(result['floor_ignored_locked'], ['TRANSPORT_CAR'])

    def test_system_floor_is_not_user_minimum_warning(self):
        data = inp()
        policy = limits(data)
        policy['ceilings']['TRANSPORT_CAR'] = 30000
        for minimum, expected in (({}, False), ({'TRANSPORT_CAR': 60000}, True)):
            data['minimums'] = minimum
            for result in (build_candidates(data, self.ratios, policy=policy, reality_profile={'has_vehicle': False}),
                           apply_priority_decisions(data, self.ratios, policy=policy, decisions=[], reality_profile={'has_vehicle': False})):
                self.assertEqual(any(w['code'] == 'minimum_above_ceiling' for w in result['warnings']), expected)

    def test_floor_explanation_uses_reference_label(self):
        from .design_reality import REALITY_FLOORS
        for category, label in (('TRANSPORT_CAR', '교통 테스트 기준'), ('FOOD', '식비 테스트 기준')):
            ratios = {**self.ratios, category: 0.001}
            with patch('budgets.design_reality.system_floors', return_value={category: 55000}), \
                    patch.dict(REALITY_FLOORS[category], {'label': label}):
                result = build_candidates(inp(), ratios, reality_profile={'has_vehicle': False})
            self.assertIn(f'{label}(55,000원)', result['candidates'][0]['result']['reasons'][0])

    def test_replay_checks_system_floor_after_adjustment(self):
        data = inp()
        invalid = dict.fromkeys(CATEGORY_CODES, 0)
        invalid['FOOD'] = data['target']
        engine = build_candidates(data, self.ratios, policy=limits(data), reality_profile={'has_vehicle': False})
        with patch('budgets.design_candidates.build_candidates', return_value=engine), \
                patch('budgets.design_candidates._adjust', return_value=(invalid, [], {}, {})):
            with self.assertRaisesRegex(EngineCheckFailed, 'system_floor_violated'):
                apply_priority_decisions(data, self.ratios, policy=limits(data), decisions=[],
                                         reality_profile={'has_vehicle': False})

    def test_multiple_receivers_in_priority_order(self):
        data = inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        result = build_candidates(data, self.ratios, policy=limits(data, total=30000, increase=10000))
        self.assertEqual([t['to'] for t in result['transfers']], ['FOOD','EDUCATION','MEDICAL_HEALTH'])
        self.assertTrue(all(v == 'target_or_category_limit' for v in result['stop_reasons'].values()))

    def test_unranked_before_ranked_even_if_ranked_discretionary(self):
        data = inp(priorities=priorities('FOOD','EDUCATION','MEDICAL_HEALTH','FASHION_SHOPPING'))
        result = build_candidates(data,self.ratios,policy=limits(data,total=10000))
        self.assertNotEqual(result['transfers'][0]['from'],'FASHION_SHOPPING')

    def test_low_rank_discretionary_before_other_low_rank(self):
        data = inp()
        result = build_candidates(data,self.ratios,policy=limits(data,total=10000))
        self.assertEqual(result['transfers'][0]['from'],'CULTURE_LEISURE')

    def test_travel_is_never_monthly_funding_or_receiver(self):
        data = inp(priorities=priorities('FOOD','EDUCATION','MEDICAL_HEALTH'))
        result = build_candidates(data,self.ratios,policy=limits(data))
        self.assertNotIn('TRAVEL_LODGING',result['active_categories'])
        for candidate in result['candidates']:
            self.assertEqual(candidate['result']['allocations']['TRAVEL_LODGING'],0)
            self.assertEqual(sum(candidate['result']['allocations'].values()),data['target'])
        self.assertTrue(all(t['from'] != 'TRAVEL_LODGING' and t['to'] != 'TRAVEL_LODGING'
                            for t in result['transfers']))

    def test_travel_priority_is_rejected_even_with_minimum_or_lock(self):
        for order in (priorities('TRAVEL_LODGING','FOOD','EDUCATION'),
                      priorities(*CATEGORY_CODES)):
            for changes in ({}, {'minimums':{'TRAVEL_LODGING':50000}},
                            {'locks':{'TRAVEL_LODGING':80000}}):
                with self.subTest(order=order,changes=changes):
                    # Shared input checks reject positive out-of-budget settings before rank checks.
                    expected = '예산 밖 카테고리' if changes else '^travel_not_rankable$'
                    with self.assertRaisesRegex(ValueError, expected):
                        build_candidates(inp(priorities=order,**changes),self.ratios)

    def test_reasons_use_transfer_amounts_and_receiver_rank(self):
        data = inp(priorities=priorities('FOOD','EDUCATION','MEDICAL_HEALTH'))
        result = build_candidates(data,self.ratios,policy=limits(data,total=30000,increase=10000))
        self.assertEqual(result['candidates'][0]['result']['reasons'],[])
        self.assertEqual(result['candidates'][1]['result']['reasons'],[
            '패션·쇼핑에서 10,000원을 식비 예산으로 옮겼어요(식비 1순위)',
            '뷰티·미용에서 10,000원을 교육 예산으로 옮겼어요(교육 2순위)',
            '문화·여가에서 10,000원을 의료·건강 예산으로 옮겼어요(의료·건강 3순위)',
        ])

    def test_rule_selection_prefers_priority_and_uses_base_when_alone(self):
        data = inp()
        candidates = build_candidates(data,self.ratios,policy=limits(data))['candidates']
        selected = select_candidate(data,candidates)
        self.assertEqual(selected['candidate_id'],'priority')
        self.assertEqual(selected['result'],candidates[1]['result'])
        self.assertIsNone(selected['fallback'])
        self.assertEqual(select_candidate(data,candidates[:1])['candidate_id'],'base')

    def test_travel_minimum_and_lock_are_rejected_without_changing_input(self):
        for changes, code in (({'minimums':{'TRAVEL_LODGING':50000}}, '예산 밖 카테고리'),
                              ({'locks':{'TRAVEL_LODGING':80000}}, '예산 밖 카테고리'),
                              ({'locks':{'TRAVEL_LODGING':0}}, '^travel_lock_not_allowed$')):
            with self.subTest(changes=changes):
                data = inp(**changes)
                before = deepcopy(data)
                with self.assertRaisesRegex(ValueError, code):
                    build_candidates(data, self.ratios, policy=limits(data))
                self.assertEqual(data, before)

    def test_default_zero_travel_minimum_does_not_activate_travel(self):
        result = build_candidates(inp(minimums={'TRAVEL_LODGING': 0}), self.ratios)
        self.assertNotIn('TRAVEL_LODGING', result['active_categories'])
        self.assertEqual(result['candidates'][0]['result']['allocations']['TRAVEL_LODGING'], 0)

    def test_protected_discretionary_is_last_resort_and_only_lower_rank(self):
        for donor in ('CULTURE_LEISURE', 'BEAUTY'):
            with self.subTest(donor=donor):
                data = inp(priorities=priorities('FOOD',donor,'EDUCATION'))
                policy = limits(data,total=30000)
                policy['decrease_limits'] = dict.fromkeys(CATEGORY_CODES,0)
                policy['decrease_limits'][donor] = 12000
                result = build_candidates(data,self.ratios,policy=policy)
                self.assertEqual(result['transfers'],[{'from':donor,'to':'FOOD','amount':12000}])
                self.assertEqual(result['stop_reasons']['FOOD'],'no_funding')
                data['priorities'] = priorities(donor,'FOOD','EDUCATION')
                self.assertEqual(build_candidates(data,self.ratios,policy=policy)['transfers'],[])

    def test_beauty_last_resort_keeps_minimum_and_lock(self):
        for changes in ({'minimums':{'BEAUTY':120000}}, {'locks':{'BEAUTY':110000}}):
            with self.subTest(changes=changes):
                data = inp(priorities=priorities('FOOD','BEAUTY','EDUCATION'), **changes)
                policy = limits(data)
                policy['decrease_limits'] = dict.fromkeys(CATEGORY_CODES,0)
                policy['decrease_limits']['BEAUTY'] = 30000
                result = build_candidates(data,self.ratios,policy=policy)
                self.assertEqual(result['transfers'],[])
                protected = changes.get('minimums', changes.get('locks'))['BEAUTY']
                self.assertEqual(result['candidates'][0]['result']['allocations']['BEAUTY'],protected)

    def test_existing_order_does_not_make_a_donor_receive_later(self):
        data = inp(priorities=priorities('FOOD','CULTURE_LEISURE','BEAUTY'))
        # No ordinary funding: exhaust lower-ranked extra donors in order.
        for increase, expected in (
            (10000, [{'from':'BEAUTY','to':'FOOD','amount':10000},
                     {'from':'BEAUTY','to':'CULTURE_LEISURE','amount':10000}]),
            (30000, [{'from':'BEAUTY','to':'FOOD','amount':20000},
                     {'from':'CULTURE_LEISURE','to':'FOOD','amount':10000}]),
            (50000, [{'from':'BEAUTY','to':'FOOD','amount':20000},
                     {'from':'CULTURE_LEISURE','to':'FOOD','amount':20000}]),
        ):
            with self.subTest(increase=increase):
                policy = limits(data,total=80000,increase=increase)
                policy['increase_rates'] = dict.fromkeys(('FOOD','CULTURE_LEISURE','BEAUTY'),50)
                policy['decrease_limits'] = dict.fromkeys(CATEGORY_CODES,0)
                policy['decrease_limits'].update(BEAUTY=20000,CULTURE_LEISURE=20000)
                result = build_candidates(data,self.ratios,policy=policy)
                self.assertEqual(result['transfers'],expected)
                donors = {t['from'] for t in result['transfers']}
                receivers = {t['to'] for t in result['transfers']}
                self.assertFalse(donors & receivers)
                base, adjusted = [c['result'] for c in result['candidates']]
                for code in CATEGORY_CODES:
                    delta = sum(t['amount'] for t in expected if t['to'] == code)
                    delta -= sum(t['amount'] for t in expected if t['from'] == code)
                    self.assertEqual(adjusted['allocations'][code],base['allocations'][code]+delta)
                check_result(data,adjusted)

    def test_beauty_high_minimum_warns_without_reducing(self):
        result = build_candidates(inp(minimums={'BEAUTY':300000}),self.ratios,
                                  warning_thresholds={'BEAUTY':200000})
        self.assertEqual(result['warnings'],[{'code':'high_minimum','category':'BEAUTY',
                                             'amount':300000,'reference':200000}])
        self.assertGreaterEqual(result['candidates'][0]['result']['allocations']['BEAUTY'],300000)

    def test_no_headroom_no_duplicate(self):
        data = inp(target=800000, minimums={c:100000 for c in CATEGORY_CODES if c not in ('TRAVEL_LODGING','HOUSING_TELECOM')})
        result = build_candidates(data,self.ratios,policy=limits(data))
        self.assertEqual(len(result['candidates']),1)
        self.assertTrue(all(v == 'no_funding' for v in result['stop_reasons'].values()))

    def test_lock_is_never_changed(self):
        data = inp(locks={'FOOD':100000,'HOUSEHOLD':100000})
        result = build_candidates(data,self.ratios,policy=limits(data))
        self.assertTrue(result['transfers'])
        for candidate in result['candidates']:
            self.assertEqual(candidate['result']['allocations']['FOOD'],100000)
            self.assertEqual(candidate['result']['allocations']['HOUSEHOLD'],100000)

    def test_zero_prediction_has_no_percentage_increase_but_minimum_is_guaranteed(self):
        data = inp(priorities=priorities('EDUCATION','FOOD','MEDICAL_HEALTH'))
        ratios = {**self.ratios,'EDUCATION':0,'FOOD':0.2}
        result = build_candidates(data,ratios,policy=limits(data,total=20000))
        self.assertEqual(result['candidates'][-1]['result']['allocations']['EDUCATION'],0)
        self.assertEqual(result['increase_targets']['EDUCATION'],0)
        self.assertEqual(result['stop_reasons']['EDUCATION'],'zero_rate_target')
        data['minimums']['EDUCATION'] = 30000
        result = build_candidates(data,ratios,policy=limits(data,total=0))
        self.assertGreaterEqual(result['candidates'][0]['result']['allocations']['EDUCATION'],30000)

    def test_minimum_is_independent_of_transfer_limit_and_ceiling(self):
        data = inp(minimums={'FOOD':300000})
        policy = limits(data,total=0)
        policy['ceilings']['FOOD'] = 200000
        result = build_candidates(data,self.ratios,policy=policy)
        self.assertEqual(result['candidates'][0]['result']['allocations']['FOOD'],300000)
        self.assertIn('minimum_above_ceiling',[w['code'] for w in result['warnings']])
        self.assertEqual(result['transfers'],[])

    def test_infeasible_warns_and_offers_chat_without_fabricating_candidate(self):
        data = inp(minimums={'FOOD':600000,'EDUCATION':600000})
        result = build_candidates(data,self.ratios)
        self.assertEqual(result['candidates'],[])
        self.assertEqual(result['result']['conflicts'],[{'type':'minimums_over_living','shortfall':200000}])
        self.assertEqual(result['next_action'],'review_settings_or_budget_chat')
        check_result(data,result['result'])

    def test_housing_excluded_unless_minimum_or_lock(self):
        data = inp()
        result = build_candidates(data,self.ratios)
        self.assertNotIn('HOUSING_TELECOM',result['active_categories'])
        self.assertEqual(result['candidates'][0]['result']['allocations']['HOUSING_TELECOM'],0)
        self.assertEqual(sum(result['candidates'][0]['result']['allocations'].values()),1000000)
        for changes in [{'minimums':{'HOUSING_TELECOM':1000}}, {'locks':{'HOUSING_TELECOM':0}}, {'locks':{'HOUSING_TELECOM':1000}}]:
            with self.assertRaises(ValueError):
                build_candidates(inp(**changes),self.ratios)
        self.assertEqual(build_candidates(inp(minimums={'HOUSING_TELECOM':0}),self.ratios)['candidates'][0]['result']['allocations']['HOUSING_TELECOM'], 0)

    def test_warn_high_discretionary_minimum_without_changing_it(self):
        data = inp(minimums={'FASHION_SHOPPING':300000})
        result = build_candidates(data,self.ratios,warning_thresholds={'FASHION_SHOPPING':200000})
        self.assertEqual(result['warnings'][0]['code'],'high_minimum')
        self.assertGreaterEqual(result['candidates'][0]['result']['allocations']['FASHION_SHOPPING'],300000)

    def test_caps_and_stop_conditions(self):
        data = inp(priorities=priorities('FOOD','EDUCATION','MEDICAL_HEALTH'))
        base = build_candidates(data,self.ratios)['candidates'][0]['result']['allocations']
        policy = limits(data,total=20000,increase=30000)
        policy['ceilings']['FOOD'] = base['FOOD'] + 5000
        result = build_candidates(data,self.ratios,policy=policy)
        self.assertEqual(result['candidates'][1]['result']['allocations']['FOOD'],base['FOOD']+5000)
        self.assertEqual(sum(t['amount'] for t in result['transfers']),20000)
        self.assertEqual(result['stop_reasons']['FOOD'],'target_or_category_limit')
        self.assertEqual(result['stop_reasons']['EDUCATION'],'total_limit')

    def test_explicit_validated_limits_required(self):
        for policy in [{}, {'total_limit':True}, {**limits(inp()),'total_limit':-1},
                       {**limits(inp()),'increase_limits':{}},
                       {**limits(inp()),'ceilings':{'ETC':1}}]:
            with self.subTest(policy=policy),self.assertRaises(ValueError):
                build_candidates(inp(),self.ratios,policy=policy)

    def test_percentage_uses_original_ml_size_not_equal_share_or_minimum(self):
        data = inp(priorities=priorities('FOOD','BEAUTY','EDUCATION'))
        weights = dict.fromkeys(CATEGORY_CODES,0)
        weights.update(FOOD=300000, BEAUTY=50000, EDUCATION=100000,
                       MEDICAL_HEALTH=100000, FASHION_SHOPPING=250000, HOUSEHOLD=200000)
        policy = limits(data,total=200000,increase=1000000)
        policy['increase_rates'] = dict.fromkeys(('FOOD','BEAUTY','EDUCATION'),20)
        policy['decrease_limits'] = dict.fromkeys(CATEGORY_CODES,1000000)
        result = build_candidates(data,weights,policy=policy)
        self.assertEqual(result['increase_targets'],{'FOOD':60000,'BEAUTY':10000,'EDUCATION':20000})
        base, adjusted = [c['result']['allocations'] for c in result['candidates']]
        self.assertEqual([adjusted[c]-base[c] for c in ('FOOD','BEAUTY','EDUCATION')],[60000,10000,20000])
        data['minimums']['FOOD'] = 500000
        self.assertEqual(build_candidates(data,weights,policy=policy)['increase_targets']['FOOD'],60000)

    def test_percentage_round_half_up_and_caps_never_round_up_past_limits(self):
        data = inp(priorities=priorities('FOOD','EDUCATION','MEDICAL_HEALTH'))
        policy = limits(data,total=100000,increase=100000)
        policy['decrease_limits']['HOUSEHOLD'] = 100000
        policy['increase_rates'] = dict.fromkeys(('FOOD','EDUCATION','MEDICAL_HEALTH'),20)
        for original, expected in ((2499,0),(2500,1000),(12499,2000),(12500,3000),(125000,25000)):
            weights = dict.fromkeys(CATEGORY_CODES,0)
            weights.update(FOOD=original,HOUSEHOLD=1000000-original)
            result = build_candidates(data,weights,policy=policy)
            self.assertEqual(result['increase_targets']['FOOD'],expected)
            self.assertEqual(sum(t['amount'] for t in result['transfers']),expected)
        weights.update(FOOD=12500,HOUSEHOLD=987500)
        for restriction in ('total_limit','increase_limits','decrease_limits','ceilings'):
            limited = deepcopy(policy)
            if restriction == 'total_limit': limited['total_limit'] = 2700
            elif restriction == 'increase_limits': limited[restriction]['FOOD'] = 2700
            elif restriction == 'decrease_limits': limited[restriction]['HOUSEHOLD'] = 2700
            else:
                base = build_candidates(data,weights)['candidates'][0]['result']['allocations']
                limited['ceilings']['FOOD'] = base['FOOD']+2700
            result = build_candidates(data,weights,policy=limited)
            self.assertEqual(sum(t['amount'] for t in result['transfers']),2000)
            self.assertTrue(all(t['amount'] % 1000 == 0 for t in result['transfers']))

    def test_bad_or_missing_percentage_is_rejected(self):
        data = inp()
        for value in (True,-1,float('inf'),float('nan'),'20'):
            policy = limits(data)
            policy['increase_rates']['FOOD'] = value
            with self.subTest(value=value),self.assertRaises(ValueError):
                build_candidates(data,self.ratios,policy=policy)
        for rates in ({}, {'ETC':20}):
            policy = {**limits(data),'increase_rates':rates}
            with self.assertRaises(ValueError): build_candidates(data,self.ratios,policy=policy)

    def test_zero_and_all_locked_skip_ratios(self):
        for data in [inp(target=0),inp(target=800000, locks={c:100000 for c in CATEGORY_CODES if c not in ('TRAVEL_LODGING','HOUSING_TELECOM')})]:
            result=build_candidates(data,None)['candidates'][0]['result']
            check_result(data,result)

    def test_all_active_locked_mismatch_returns_conflict_without_changing_input(self):
        locks = {c: 100000 for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')}
        for target in (1000000, 600000):
            for ratios in (self.ratios, None):
                with self.subTest(target=target, ratios=ratios):
                    data = inp(target=target, locks=deepcopy(locks))
                    before = deepcopy(data)
                    result = build_candidates(data, ratios, policy=limits(data))
                    self.assertEqual(result['status'], 'infeasible')
                    self.assertEqual(result['candidates'], [])
                    self.assertEqual(result['result']['allocations'], {})
                    self.assertEqual(result['result']['conflicts'], [
                        {'type': 'locks_mismatch_living', 'difference': 800000 - target}])
                    self.assertIn({'code': 'constraints_conflict'}, result['warnings'])
                    self.assertEqual(result['next_action'], 'review_settings_or_budget_chat')
                    self.assertIn('잠금', result['result']['summary'])
                    self.assertIn('생활비', result['result']['summary'])
                    effective = {**data, 'locks': {**locks, 'HOUSING_TELECOM': 0, 'TRAVEL_LODGING': 0}}
                    check_result(effective, result['result'])
                    self.assertEqual(data, before)

    def test_all_active_locked_exact_match_skips_ratios_and_keeps_inactive_zero(self):
        locks = {c: 100000 for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')}
        data = inp(target=800000, locks=locks)
        before = deepcopy(data)
        result = build_candidates(data, None, policy=limits(data))
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['active_categories'], list(locks))
        self.assertEqual(result['candidates'][0]['result']['allocations'],
                         {**locks, 'HOUSING_TELECOM': 0, 'TRAVEL_LODGING': 0})
        check_result(data, result['candidates'][0]['result'])
        self.assertEqual(data, before)

    def test_remaining_money_can_go_to_unlocked_active_category(self):
        locks = {c: 100000 for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')}
        for category in ('FOOD', 'EDUCATION'):
            with self.subTest(category=category):
                data = inp(locks=deepcopy(locks))
                del data['locks'][category]
                before = deepcopy(data)
                result = build_candidates(data, self.ratios)
                self.assertEqual(result['status'], 'proposed')
                proposal = result['candidates'][0]['result']
                self.assertEqual(proposal['allocations'][category], data['target'] - sum(data['locks'].values()))
                check_result(data, proposal)
                self.assertEqual(data, before)

    def test_zero_free_ratio_does_not_invent_weights(self):
        ratios=dict.fromkeys(CATEGORY_CODES,0); ratios['FOOD']=1
        with self.assertRaisesRegex(ValueError,'zero_free'):
            build_candidates(inp(locks={'FOOD':100000}),ratios)

    def test_invalid_inputs_rejected(self):
        for data in [inp(target=-1),inp(target=True),inp(locks={'ETC':0}),
                     inp(priorities=inp()['priorities'][:2]), inp(priorities=[{'category':'FOOD','rank':True}]*3)]:
            with self.subTest(data=data),self.assertRaises(ValueError): build_candidates(data,self.ratios)
        for value in [float('nan'),float('inf'),-1,True]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                build_candidates(inp(),{**self.ratios,'FOOD':value})

    def test_llm_selection_only_and_failure_default(self):
        data=inp(); candidates=build_candidates(data,self.ratios,policy=limits(data))['candidates']
        choose=Mock(return_value={'candidate_id':'priority','reason_code':'priority_transfer'})
        selected=select_candidate(data,candidates,choose)
        self.assertEqual(selected['result'],candidates[1]['result'])
        payload=choose.call_args.args[0]
        self.assertNotIn('allocations',str(payload)); self.assertNotIn('100000',str(payload))
        failures=[(Mock(side_effect=TimeoutError('private user input')), 'TimeoutError'),
                  (Mock(side_effect=ValueError('private user input')), 'ValueError'),
                  (Mock(side_effect=RuntimeError('private user input')), 'RuntimeError'),
                  (Mock(return_value={'candidate_id':'bad','reason_code':'baseline'}), 'ValueError'),
                  (Mock(return_value={'candidate_id':'priority','reason_code':'priority_transfer','amount':1}), 'ValueError'),
                  (Mock(return_value={'candidate_id':'priority','reason_code':'baseline'}), 'ValueError')]
        for failure, kind in failures:
            selected=select_candidate(data,candidates,failure)
            self.assertEqual(selected['candidate_id'],'priority')
            self.assertEqual(selected['fallback'],f'selector_failed:{kind}')
            self.assertNotIn('private user input',str(selected))

    def test_input_candidate_isolation_and_invalid_candidate_not_sent(self):
        data=inp(); original=deepcopy(data)
        candidates=build_candidates(data,self.ratios,policy=limits(data))['candidates']
        before=deepcopy(candidates)
        def mutate(payload):
            payload.clear(); raise RuntimeError()
        select_candidate(data,candidates,mutate)
        self.assertEqual(data,original); self.assertEqual(candidates,before)
        candidates[0]['result']['allocations']['FOOD']+=1
        choose=Mock()
        with self.assertRaises(ValueError): select_candidate(data,candidates,choose)
        choose.assert_not_called()

    def test_selector_failure_preserves_rule_selection_and_explanations(self):
        data = inp()
        candidates = build_candidates(data,self.ratios,policy=limits(data))['candidates']
        expected = select_candidate(data,candidates)
        actual = select_candidate(data,candidates,Mock(side_effect=TimeoutError))
        self.assertEqual(actual['candidate_id'],expected['candidate_id'])
        self.assertEqual(actual['result'],expected['result'])
        self.assertEqual(actual['fallback'],'selector_failed:TimeoutError')
        choose = Mock(side_effect=TimeoutError)
        self.assertEqual(select_candidate(data,candidates[:1],choose)['candidate_id'],'base')
        choose.assert_not_called()
        chosen_base = select_candidate(data,candidates,lambda _: {
            'candidate_id':'base', 'reason_code':'baseline'})
        self.assertEqual(chosen_base['candidate_id'],'base')
        self.assertIsNone(chosen_base['fallback'])

    def test_thousand_transfers_preserve_exact_minimum_lock_and_total(self):
        data = inp(target=1000123,minimums={'EDUCATION':300001},locks={'MEDICAL_HEALTH':12345},
                   priorities=priorities('FOOD','EDUCATION','MEDICAL_HEALTH'))
        result = build_candidates(data,self.ratios,policy=limits(data))
        self.assertTrue(result['transfers'])
        self.assertTrue(all(t['amount'] % 1000 == 0 for t in result['transfers']))
        for candidate in result['candidates']:
            amounts = candidate['result']['allocations']
            self.assertGreaterEqual(amounts['EDUCATION'],300001)
            self.assertEqual(amounts['MEDICAL_HEALTH'],12345)
            self.assertEqual(sum(amounts.values()),1000123)
            check_result(data,candidate['result'])

    def test_team_ml_mapping_no_scaling_or_double_subtraction(self):
        self.assertEqual(ml_profile(inp()),{'age':27,'sex':1,'has_vehicle':0,
                         'disposable_income':3000000,'fixed_expense_amount':700000,'living_budget_amount':1000000})
        for change in [{'sex':None},{'sex':[]},{'has_vehicle':None},{'age':35},{'target':0}]:
            with self.assertRaises(ValueError): ml_profile(inp(**change))
