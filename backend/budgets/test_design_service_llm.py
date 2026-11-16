"""Service LLM wiring with synthetic inputs and mocked HTTP. Overrides are test fixtures."""
from copy import deepcopy
from datetime import date
import json
import os
import random
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import design_service as service
from . import design_tracing as tracing
from .design_candidates import CATEGORY_NAMES
from .test_design_candidates import inp, priorities
from .test_design_service import history


class ServiceLLMFixture(SimpleTestCase):
    def setUp(self):
        self.snapshot = {'engine_input': inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH')), 'etc': 0}
        self.snapshot['engine_input'].update(minimums={'FOOD': 200001}, locks={'EDUCATION': 12345})
        self.policy = {'version': 'synthetic-llm-test', 'a': {},
                       'b_forecast_band': {'down_percent': 20, 'up_percent': 20}}
        self.enterContext(override_settings(BUDGET_DESIGN_POLICY=self.policy, BUDGET_DESIGN_ENABLED=False))
        self.enterContext(patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-key', 'LLM_MODEL': 'synthetic-model',
                                                'LANGSMITH_TRACING': 'true', 'LANGSMITH_API_KEY': 'synthetic-trace-key'}))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No real network')))
        self.enterContext(patch.object(service, 'predict_ml', return_value={
            'ratios': dict.fromkeys(CATEGORY_NAMES.values(), .1), 'source': 'model', 'used_fallback': False,
            'artifact_id': 'synthetic-model', 'model_checksum': 'a'*64}))
        self.a_connection = self.enterContext(patch('budgets.design_selection.HTTPSConnection')).return_value
        self.b_connection = self.enterContext(patch('plan_b_llm.HTTPSConnection')).return_value
        self.a_requests, self.b_requests = [], []
        self.a_transport = self.enterContext(patch('budgets.design_selection.HTTPSConnection', return_value=self.a_connection))
        self.b_transport = self.enterContext(patch('plan_b_llm.HTTPSConnection', return_value=self.b_connection))
        self.actions = iter(('increase_small', 'keep', 'keep'))
        self.bad_b = None
        self.bad_a = False
        self.a_connection.request.side_effect = self.respond_a
        self.b_connection.request.side_effect = self.respond_b

    @staticmethod
    def tool_response(name, arguments):
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None, 'tool_calls': [
            {'id': 'synthetic-call', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}

    def respond_a(self, *args, **kwargs):
        body = json.loads(kwargs['body'])
        self.a_requests.append(body)
        properties = body['tools'][0]['function']['parameters']['properties']
        category = properties['category']['enum'][0]
        action = 'apply' if properties['action']['enum'] == ['apply', 'keep'] else next(self.actions, 'keep')
        response = self.tool_response('adjust_budget', {'category': category, 'action': 'invented' if self.bad_a else action})
        self.a_connection.getresponse.return_value.status = 200
        self.a_connection.getresponse.return_value.read.return_value = json.dumps(response).encode()

    def respond_b(self, *args, **kwargs):
        body = json.loads(kwargs['body'])
        self.b_requests.append(body)
        payload = json.loads(body['messages'][1]['content'])
        proposal = {'allocations': {c: b['lower'] for c, b in payload['bounds'].items()},
                    'reasons': {c: {'code': 'keep', 'text': '평소 소비와 선호를 반영했어요.'} for c in payload['bounds']}}
        room = max(0, payload['target']-sum(proposal['allocations'].values())) // 5000 * 5000
        proposal['allocations']['MEDICAL_HEALTH'] += min(room,
            payload['bounds']['MEDICAL_HEALTH']['upper']-proposal['allocations']['MEDICAL_HEALTH'])
        if self.bad_b:
            self.bad_b(proposal)
        response = self.tool_response('forecast_budget', proposal)
        self.b_connection.getresponse.return_value.status = 200
        self.b_connection.getresponse.return_value.read.return_value = json.dumps(response).encode()

    def run_plan(self, plan, *, records=None, available_spare=0, user=None, **options):
        if records is None:
            records = history() if plan == 'b' else {}
        before = deepcopy((self.snapshot, records, self.policy))
        with patch.object(service, 'history_for_user', return_value=records):
            out = service.recommend(user, self.snapshot, available_spare, date(2026, 11, 7), **options)
        self.assertEqual((self.snapshot, records, self.policy), before)
        if out['status'] == 'proposed':
            json.dumps(out, allow_nan=False)
            for category, amount in self.snapshot['engine_input']['minimums'].items():
                self.assertGreaterEqual(out['allocations'][category], amount)
            for category, amount in self.snapshot['engine_input']['locks'].items():
                self.assertEqual(out['allocations'][category], amount)
            self.assertEqual(out['allocations']['HOUSING_TELECOM'], 0)
            self.assertEqual(out['allocations']['TRAVEL_LODGING'], 0)
            self.assertTrue(all(v % 5000 == 0 for c, v in out['allocations'].items()
                                if c not in self.snapshot['engine_input']['locks']))
            transfer = service.validate_funding(self.snapshot, out, available_spare)
            self.assertGreaterEqual(transfer, 0)
            self.assertLessEqual(transfer, available_spare)
            self.assertEqual(sum(out['allocations'].values())+out['unallocated'], out['_design']['funded_target'])
        return out


class ServiceLLMTest(ServiceLLMFixture):
    def test_approved_default_band_reaches_forecast_and_keeps_minimum_lock_and_floors(self):
        # Synthetic amounts exercise protections overriding the newly approved service band.
        initial = deepcopy(self.snapshot)
        with override_settings(BUDGET_DESIGN_POLICY=None), patch.dict(os.environ):
            os.environ.pop('BUDGET_DESIGN_POLICY', None)
            for larger_minimum, lower_transport_lock in ((False, False), (True, False), (False, True)):
                with self.subTest(larger_minimum=larger_minimum, lower_transport_lock=lower_transport_lock):
                    self.snapshot = deepcopy(initial)
                    if larger_minimum:
                        self.snapshot['engine_input']['minimums']['FOOD'] = 250001
                    if lower_transport_lock:
                        self.snapshot['engine_input']['locks']['TRANSPORT_CAR'] = 30001
                    self.b_requests.clear()
                    with patch.object(service, 'forecast_budget', wraps=service.forecast_budget) as forecast:
                        out = self.run_plan('b', allow_external=True, synthetic=True)
                    self.assertEqual(forecast.call_args.kwargs['forecast_band'], {'down_percent': 20, 'up_percent': 20})
                    self.assertEqual(out['_design']['llm']['status'], 'completed')
                    payload = json.loads(self.b_requests[0]['messages'][1]['content'])
                    for category, bounds in payload['bounds'].items():
                        self.assertGreaterEqual(out['allocations'][category], bounds['lower'])
                        self.assertLessEqual(out['allocations'][category], bounds['upper'])
                    for category, amount in payload['system_floors'].items():
                        if category not in self.snapshot['engine_input']['locks']:
                            self.assertGreaterEqual(out['allocations'][category], amount)
                    if larger_minimum:
                        self.assertEqual(out['allocations']['FOOD'], 255000)
                    if lower_transport_lock:
                        self.assertEqual(out['allocations']['TRANSPORT_CAR'], 30001)
                    self.assertFalse(service.enabled())

    def test_approved_default_band_does_not_enable_external_calls(self):
        with override_settings(BUDGET_DESIGN_POLICY=None), patch.dict(os.environ):
            os.environ.pop('BUDGET_DESIGN_POLICY', None)
            out = self.run_plan('b')
            self.assertEqual(service.configured_policy()['b_forecast_band'], {'down_percent': 20, 'up_percent': 20})
            self.assertEqual(out['_design']['llm'], {'status': 'not_run', 'reason': 'external_disabled'})
            self.assertFalse(service.enabled())
        self.a_connection.request.assert_not_called()
        self.b_connection.request.assert_not_called()

    def test_both_flags_are_required_for_either_plan_and_feature_remains_off(self):
        for plan in ('a', 'b'):
            for flags in ({}, {'synthetic': True}, {'allow_external': True}):
                with self.subTest(plan=plan, flags=flags):
                    out = self.run_plan(plan, **flags)
                    self.assertEqual(out['_design']['llm']['status'], 'not_run')
                    self.assertEqual(out['_design']['selection']['plan'], plan)
        self.a_connection.request.assert_not_called()
        self.b_connection.request.assert_not_called()
        self.assertFalse(service.enabled())

    def test_a_uses_real_tool_loop_and_preserves_protections_and_final_units(self):
        before = self.run_plan('a')
        out = self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(out['_design']['llm']['status'], 'completed')
        self.assertEqual(out['_design']['llm']['source'], 'llm_tools')
        self.assertGreater(out['allocations']['FOOD'], before['allocations']['FOOD'])
        gain = sum(max(0, v-before['allocations'][c]) for c, v in out['allocations'].items())
        self.assertLessEqual(gain, self.snapshot['engine_input']['target']*20//100)
        self.assertGreater(len(self.a_requests), 0)
        self.b_connection.request.assert_not_called()
        self.assertTrue(all(r['store'] is False and r['tools'][0]['function']['strict'] is True for r in self.a_requests))
        self.assertTrue(all(call.args[0] == 'us.api.openai.com' for call in self.a_transport.call_args_list))

    def test_b_proposal_can_differ_from_rule_and_bounds_survive_final_rounding(self):
        before = self.run_plan('b')
        out = self.run_plan('b', allow_external=True, synthetic=True)
        self.assertEqual(out['_design']['llm']['status'], 'completed')
        self.assertEqual(out['_design']['llm']['attempts'], 1)
        self.assertNotEqual(out['allocations']['MEDICAL_HEALTH'], before['allocations']['MEDICAL_HEALTH'])
        payload = json.loads(self.b_requests[0]['messages'][1]['content'])
        for category, bounds in payload['bounds'].items():
            self.assertLessEqual(bounds['lower'], out['allocations'][category])
            self.assertGreaterEqual(bounds['upper'], out['allocations'][category])
        self.assertEqual(payload['target_month'], 11)
        self.a_connection.request.assert_not_called()
        self.assertTrue(all(call.args[0] == 'us.api.openai.com' for call in self.b_transport.call_args_list))

    def test_b_without_explicit_forecast_band_keeps_rule_without_a_call(self):
        baseline = self.run_plan('b')
        with override_settings(BUDGET_DESIGN_POLICY={'a': {}}):
            out = self.run_plan('b', allow_external=True, synthetic=True)
        self.assertEqual(out['allocations'], baseline['allocations'])
        self.assertEqual(out['_design']['llm'], {'status': 'not_run', 'reason': 'forecast_band_required'})
        self.b_connection.request.assert_not_called()

    def test_a_bad_tool_result_returns_base_without_partial_adjustment(self):
        baseline = self.run_plan('a')
        self.bad_a = True
        out = self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(out['allocations'], baseline['allocations'])
        self.assertEqual(out['_design']['llm']['status'], 'fallback')

    def test_b_minimum_or_lock_violation_retries_then_keeps_checked_rule(self):
        baseline = self.run_plan('b')
        for change in (lambda p: p['allocations'].update(FOOD=0),
                       lambda p: p['allocations'].update(EDUCATION=15000)):
            with self.subTest(change=change):
                self.bad_b = change
                self.b_requests.clear()
                out = self.run_plan('b', allow_external=True, synthetic=True)
                self.assertEqual(out['allocations'], baseline['allocations'])
                self.assertEqual(out['_design']['llm']['status'], 'fallback')
                self.assertEqual(out['_design']['llm']['attempts'], 2)
                self.assertEqual(len(self.b_requests), 2)

    def test_timeout_fallback_exposes_error_kind_only_for_both_plans(self):
        for plan, connection in (('a', self.a_connection), ('b', self.b_connection)):
            with self.subTest(plan=plan):
                baseline = self.run_plan(plan)
                connection.request.side_effect = TimeoutError('private bank and account details')
                out = self.run_plan(plan, allow_external=True, synthetic=True)
                self.assertEqual(out['allocations'], baseline['allocations'])
                self.assertEqual(out['_design']['llm']['status'], 'fallback')
                self.assertNotIn('private', json.dumps(out, default=str))

    def test_b_repaired_retry_reaches_service_result(self):
        attempts = 0
        def first_attempt_only(proposal):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                proposal['allocations']['FOOD'] = 0
        self.bad_b = first_attempt_only
        out = self.run_plan('b', allow_external=True, synthetic=True)
        self.assertEqual(out['_design']['llm']['status'], 'completed')
        self.assertEqual(out['_design']['llm']['attempts'], 2)
        self.assertEqual(out['_design']['llm']['validation_failures'], 1)
        self.assertIn('below_minimum', out['_design']['llm']['violation_types'])
        self.assertIn('violations', json.loads(self.b_requests[1]['messages'][1]['content']))

    def test_service_does_not_start_langsmith_even_when_environment_enabled(self):
        with patch.object(tracing, '_start', wraps=tracing._start) as start:
            self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(start.call_args.args[0], {'LANGSMITH_TRACING': 'false'})

    def test_external_payloads_contain_no_transaction_ids_or_merchants(self):
        records = history()
        for month, row in records.items():
            for index, item in enumerate(row['items']):
                item.update(transaction_id=f'synthetic-private-transaction-{month}-{index}',
                            merchant_key='synthetic-private-merchant')
        with patch.object(service, 'history_for_user', return_value=records):
            out = service.recommend(None, self.snapshot, 0, date(2026, 11, 7), allow_external=True, synthetic=True)
        self.assertEqual(out['_design']['llm']['status'], 'completed')
        payload = json.dumps(self.b_requests)
        for forbidden in ('synthetic-private-transaction', 'synthetic-private-merchant', 'transaction_id', 'merchant_key'):
            self.assertNotIn(forbidden, payload)

    def test_non_boolean_permissions_are_rejected_before_inference(self):
        for flags in ({'allow_external': 1, 'synthetic': True}, {'allow_external': True, 'synthetic': 'true'}):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'invalid_external_permission'):
                service.recommend(None, self.snapshot, 0, date(2026, 11, 7), **flags)
        self.a_connection.request.assert_not_called()
        self.b_connection.request.assert_not_called()


class ServiceLLMScenarioTest(ServiceLLMFixture):
    """Amounts, bands and seed below are synthetic experiment fixtures, not service defaults."""

    def test_a_protects_every_rank_in_candidates_and_llm_selected_budget(self):
        from .design_candidates import apply_priority_decisions

        self.snapshot['engine_input'] = inp(priorities=priorities(
            'FOOD', 'BEAUTY', 'CULTURE_LEISURE', 'TRANSPORT_CAR', 'FASHION_SHOPPING', 'EDUCATION'))
        data = self.snapshot['engine_input']
        config = {'decrease_limits': dict.fromkeys(CATEGORY_NAMES, 1000000)}
        original = deepcopy(config)
        policy = service.initial_a_policy(config, data, 0)
        self.assertEqual(config, original)
        ratios = dict.fromkeys(CATEGORY_NAMES, .1)
        engine = service.build_candidates(data, ratios, policy=policy, reality_profile={'has_vehicle': False})
        base = engine['candidates'][0]['result']['allocations']
        self.assertTrue(engine['transfers'])
        ranked = {r['category'] for r in data['priorities']}
        self.assertTrue(all(t['from'] not in ranked for t in engine['transfers']))
        decisions = [{'category': r['category'], 'action': 'increase_large'} for r in data['priorities'][:3]]
        replay = apply_priority_decisions(data, ratios, policy=policy, decisions=decisions,
                                          reality_profile={'has_vehicle': False})
        self.assertTrue(all(t['from'] not in ranked for t in replay['transfers']))
        self.actions = iter(('increase_large',) * 3)
        result = self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['_design']['llm']['status'], 'completed')
        for category in ranked:
            self.assertEqual(policy['decrease_limits'][category], 0)
            self.assertGreaterEqual(result['allocations'][category], base[category])
        self.assertEqual(sum(result['allocations'].values()) + result['unallocated'], data['target'])

    def test_a_keeps_base_when_every_active_category_is_ranked(self):
        self.snapshot['engine_input'] = inp()
        base = self.run_plan('a')
        self.actions = iter(('increase_large',) * 3)
        result = self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(result['status'], 'proposed')
        self.assertEqual(result['allocations'], base['allocations'])
        self.a_connection.request.assert_not_called()

    def test_missing_vehicle_profile_blocks_before_either_llm(self):
        self.snapshot['engine_input']['has_vehicle'] = None
        for plan in ('a', 'b'):
            with self.subTest(plan=plan):
                out = self.run_plan(plan, allow_external=True, synthetic=True)
                self.assertEqual((out['status'], out['reason']), ('needs_data', 'ml_unavailable'))
                self.assertNotIn('allocations', out)
        self.a_connection.request.assert_not_called()
        self.b_connection.request.assert_not_called()

    def test_a_maximum_requests_cannot_exceed_overall_living_limit_after_rounding(self):
        self.snapshot['etc'] = 100000
        self.snapshot['engine_input']['target'] = 900000
        data = self.snapshot['engine_input']
        resolved = service.initial_a_policy({}, data, self.snapshot['etc'])
        engine = service.build_candidates(data, dict.fromkeys(CATEGORY_NAMES, .1), policy=resolved,
                                          reality_profile={'has_vehicle': data['has_vehicle']})
        base = engine['candidates'][0]['result']['allocations']
        self.actions = iter(('increase_large', 'increase_large', 'increase_large'))
        out = self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(resolved['total_limit'], 200000)
        self.assertLessEqual(sum(max(0, v-base[c]) for c, v in out['allocations'].items()), 200000)

    def test_a_timeout_after_accepted_first_adjustment_discards_partial_session(self):
        base = self.run_plan('a')
        def late_timeout(*args, **kwargs):
            if len(self.a_requests) >= 2:  # First increase and its confirmation already succeeded.
                raise TimeoutError('synthetic-private-error')
            self.respond_a(*args, **kwargs)
        self.a_connection.request.side_effect = late_timeout
        out = self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(len(self.a_requests), 2)
        self.assertEqual(out['allocations'], base['allocations'])
        self.assertEqual(out['_design']['llm']['status'], 'fallback')
        self.assertNotIn('synthetic-private-error', str(out))

    def test_a_late_success_after_deadline_is_discarded(self):
        base = self.run_plan('a')
        with patch('budgets.design_selection.monotonic', side_effect=[0, 1, 31]):
            out = self.run_plan('a', allow_external=True, synthetic=True)
        self.assertEqual(out['allocations'], base['allocations'])
        self.assertEqual(out['_design']['llm']['reason'], 'adjustment_failed:TimeoutError')

    def test_b_can_forecast_under_shortfall_without_exceeding_funding(self):
        self.snapshot['engine_input'].update(target=650000, minimums={}, locks={})
        out = self.run_plan('b', allow_external=True, synthetic=True)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['llm']['status'], 'completed')
        self.assertLessEqual(sum(out['allocations'].values()), 650000)

    def test_b_spare_funding_is_bounded_and_insufficient_funds_block_before_llm(self):
        self.snapshot['engine_input'].update(target=500000, locks={}, minimums=dict.fromkeys(
            ('FOOD', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING'), 100000))
        self.policy['b_forecast_band'] = {'down_percent': 100, 'up_percent': 20}
        out = self.run_plan('b', available_spare=150000, allow_external=True, synthetic=True)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['spare_transfer'], 100000)
        self.assertEqual(out['_design']['funded_target'], 600000)
        self.assertEqual(out['_design']['llm']['status'], 'completed')
        self.b_requests.clear()
        blocked = self.run_plan('b', available_spare=90000, allow_external=True, synthetic=True)
        self.assertEqual(blocked['status'], 'needs_upper_allocation_review')
        self.assertNotIn('allocations', blocked)
        self.assertEqual(self.b_requests, [])

    def test_conflicting_minimum_and_lock_never_reach_either_llm(self):
        self.snapshot['engine_input']['locks']['FOOD'] = 100000
        for plan in ('a', 'b'):
            with self.subTest(plan=plan):
                out = self.run_plan(plan, allow_external=True, synthetic=True)
                self.assertEqual(out['status'], 'infeasible')
        self.a_connection.request.assert_not_called()
        self.b_connection.request.assert_not_called()

    def test_all_locked_categories_remain_exact_for_both_plans(self):
        codes = [c for c in CATEGORY_NAMES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')]
        self.snapshot['engine_input'].update(target=800001, minimums={},
            locks={c: 100001 if c == 'EDUCATION' else 100000 for c in codes})
        for plan in ('a', 'b'):
            with self.subTest(plan=plan):
                out = self.run_plan(plan, allow_external=True, synthetic=True)
                self.assertEqual(out['status'], 'proposed')
                self.assertEqual(sum(out['allocations'].values()), 800001)
                self.assertEqual(out['unallocated'], 0)
        self.a_connection.request.assert_not_called()

    def test_exact_other_reservation_lock_is_not_used_for_llm_adjustment(self):
        self.snapshot.update(etc=12345, minimums={'ETC': 12345}, locks={'ETC': 12345})
        for plan in ('a', 'b'):
            with self.subTest(plan=plan):
                out = self.run_plan(plan, allow_external=True, synthetic=True)
                self.assertEqual(out['status'], 'proposed')
                self.assertEqual(out['_design']['other_reserved'], 12345)

    def test_a_cannot_override_money_or_user_settings_through_extra_tool_fields(self):
        base = self.run_plan('a')
        for field, value in (('amount', 2000000), ('minimums', {'FOOD': 0}),
                             ('locks', {}), ('priorities', [])):
            def extra_field(*args, **kwargs):
                self.respond_a(*args, **kwargs)
                response = self.a_connection.getresponse.return_value
                body = json.loads(response.read.return_value)
                function = body['choices'][0]['message']['tool_calls'][0]['function']
                decision = json.loads(function['arguments'])
                decision[field] = value
                function['arguments'] = json.dumps(decision)
                response.read.return_value = json.dumps(body).encode()
            with self.subTest(field=field):
                self.a_connection.request.side_effect = extra_field
                out = self.run_plan('a', allow_external=True, synthetic=True)
                self.assertEqual(out['allocations'], base['allocations'])
                self.assertEqual(out['_design']['llm']['status'], 'fallback')

    def test_b_one_off_medical_cost_is_not_reintroduced_as_the_regular_baseline(self):
        records = history()
        medical = next(t for t in records['2026-10']['items'] if t['category'] == 'MEDICAL_HEALTH')
        medical['net_amount'] = 600000
        out = self.run_plan('b', records=records, allow_external=True, synthetic=True)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['llm']['status'], 'completed')
        medical_context = json.loads(self.b_requests[0]['messages'][1]['content'])['categories']['MEDICAL_HEALTH']
        self.assertAlmostEqual(medical_context['baseline_amount'], 200000/3)
        self.assertTrue(any(row['amount'] == 600000 and row['excluded']
                            for row in medical_context['one_off_candidates']))

    def test_invalid_money_and_extra_fields_in_b_responses_cannot_escape_validation(self):
        base = self.run_plan('b')
        cases = {
            'bool': lambda p: p['allocations'].update(FOOD=True),
            'float': lambda p: p['allocations'].update(FOOD=205000.0),
            'unit': lambda p: p['allocations'].update(FOOD=205001),
            'negative': lambda p: p['allocations'].update(FOOD=-5000),
            'over_target': lambda p: p['allocations'].update(FOOD=2000000),
            'inactive': lambda p: p['allocations'].update(TRAVEL_LODGING=5000),
            'missing_category': lambda p: p['allocations'].pop('BEAUTY'),
            'minimum_override': lambda p: p.update(minimums={'FOOD': 0}),
            'priority_override': lambda p: p.update(priorities=[]),
            'reason_enum': lambda p: p['reasons']['FOOD'].update(code='ignore_rules'),
            'reason_numbers': lambda p: p['reasons']['FOOD'].update(text='식비를 1원으로 바꿨어요.'),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                self.bad_b = mutate
                out = self.run_plan('b', allow_external=True, synthetic=True)
                self.assertEqual(out['allocations'], base['allocations'])
                self.assertEqual(out['_design']['llm']['status'], 'fallback')
                self.assertEqual(out['_design']['llm']['attempts'], 2)

    def test_bad_wire_responses_for_either_plan_leave_no_partial_budget(self):
        for plan, connection, responder in (('a', self.a_connection, self.respond_a),
                                             ('b', self.b_connection, self.respond_b)):
            base = self.run_plan(plan)
            for failure in ('http', 'bad_json', 'truncated', 'parallel_tools', 'wrong_tool', 'text'):
                def failing(*args, **kwargs):
                    responder(*args, **kwargs)
                    response = connection.getresponse.return_value
                    if failure == 'http':
                        response.status = 429
                    elif failure == 'bad_json':
                        response.read.return_value = b'not json'
                    else:
                        body = json.loads(response.read.return_value)
                        choice = body['choices'][0]
                        if failure == 'truncated':
                            choice['finish_reason'] = 'length'
                        elif failure == 'parallel_tools':
                            choice['message']['tool_calls'] *= 2
                        elif failure == 'wrong_tool':
                            choice['message']['tool_calls'][0]['function']['name'] = 'save_settings'
                        else:
                            choice['message']['content'] = 'synthetic-private-response'
                        response.read.return_value = json.dumps(body).encode()
                with self.subTest(plan=plan, failure=failure):
                    connection.request.side_effect = failing
                    out = self.run_plan(plan, allow_external=True, synthetic=True)
                    self.assertEqual(out['allocations'], base['allocations'])
                    self.assertEqual(out['_design']['llm']['status'], 'fallback')
                    self.assertNotIn('synthetic-private-response', str(out))

    def test_150_varied_synthetic_cases_preserve_money_and_settings(self):
        rng = random.Random(20261107)
        active = [c for c in CATEGORY_NAMES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')]
        completed = {'a': 0, 'b': 0}
        for index in range(150):
            plan = 'a' if index % 2 == 0 else 'b'
            self.snapshot['engine_input'] = inp(target=rng.randrange(500000, 1500001, 5000),
                priorities=priorities(*rng.sample(active, 3)), has_vehicle=rng.choice((False, True)),
                minimums={c: rng.randrange(0, 60001) for c in rng.sample(active, 2)},
                locks={c: rng.randrange(0, 80001) for c in rng.sample(active, 2)})
            # A minimum may not exceed that user's exact lock; contradictions have their own case above.
            for category, amount in self.snapshot['engine_input']['locks'].items():
                if category in self.snapshot['engine_input']['minimums']:
                    self.snapshot['engine_input']['minimums'][category] = min(
                        self.snapshot['engine_input']['minimums'][category], amount)
            records = history() if plan == 'b' else {}
            for row in records.values():
                for category in active:
                    items = [t for t in row['items'] if t['category'] == category]
                    total = rng.randrange(30000, 150001, 5000)
                    for n, item in enumerate(items):
                        item['net_amount'] = total//len(items)+(total % len(items) if n == 0 else 0)
            self.actions = iter(('increase_large', 'increase_large', 'increase_large'))
            self.a_requests.clear()
            self.b_requests.clear()
            with self.subTest(case=index, plan=plan):
                out = self.run_plan(plan, records=records, available_spare=1000000,
                                    allow_external=True, synthetic=True)
                self.assertEqual(out['status'], 'proposed')
                self.assertEqual(out['_design']['selection']['plan'], plan)
                completed[plan] += out['_design']['llm']['status'] == 'completed'
                if plan == 'a':
                    data = self.snapshot['engine_input']
                    policy = service.initial_a_policy({}, data, 0)
                    engine = service.build_candidates(data, dict.fromkeys(CATEGORY_NAMES, .1), policy=policy,
                        reality_profile={'has_vehicle': data['has_vehicle']})
                    base = engine['candidates'][0]['result']['allocations']
                    self.assertLessEqual(sum(max(0, v-base[c]) for c, v in out['allocations'].items()), policy['total_limit'])
                elif out['_design']['llm']['status'] == 'completed':
                    payload = json.loads(self.b_requests[-1]['messages'][1]['content'])
                    for category, bounds in payload['bounds'].items():
                        self.assertLessEqual(bounds['lower'], out['allocations'][category])
                        self.assertGreaterEqual(bounds['upper'], out['allocations'][category])
        self.assertGreater(completed['a'], 0)
        self.assertGreater(completed['b'], 0)
