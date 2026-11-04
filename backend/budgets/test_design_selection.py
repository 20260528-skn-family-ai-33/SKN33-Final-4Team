"""Tool decisions, cumulative money rules and failure rollback; synthetic only."""
from copy import deepcopy
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from . import design_selection as selection
from .design_candidates import apply_priority_decisions, build_candidates
from .recommend import CATEGORY_CODES
from .test_design_candidates import inp, limits, priorities


def tool_response(category, action, **extra):
    arguments = {'category': category, 'action': action, **extra}
    return {'choices': [{'finish_reason': 'tool_calls', 'message': {
        'content': None, 'tool_calls': [{'id': 'call_test', 'type': 'function', 'function': {
            'name': 'adjust_budget', 'arguments': json.dumps(arguments)}}]}}]}


class SelectionTests(SimpleTestCase):
    def setUp(self):
        tracing = patch.dict('os.environ', {'LANGSMITH_TRACING': 'false'})
        tracing.start()
        self.addCleanup(tracing.stop)
        self.data = inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        self.ratios = dict.fromkeys(CATEGORY_CODES, .1)
        self.policy = limits(self.data, total=60000, increase=20000)
        self.engine = build_candidates(self.data, self.ratios, policy=self.policy)
        transport = patch.object(selection, 'HTTPSConnection')
        self.connection_class = transport.start()
        self.addCleanup(transport.stop)
        self.connection = self.connection_class.return_value
        self.response = self.connection.getresponse.return_value
        self.response.status = 200
        self.actions = ['increase', 'keep', 'increase']
        self.requests = []
        def respond(*args, **kwargs):
            body = json.loads(kwargs['body'])
            self.requests.append(body)
            category = body['tools'][0]['function']['parameters']['properties']['category']['enum'][0]
            action = self.actions[len(self.requests)-1]
            self.response.read.return_value = json.dumps(tool_response(category, action)).encode()
        self.connection.request.side_effect = respond

    def choose(self, key='synthetic-test-key', **kwargs):
        return selection.choose_budget(self.data, self.engine, ml_ratios=self.ratios, api_key=key, **kwargs)

    def replay(self, decisions):
        return apply_priority_decisions(self.data, self.ratios, policy=self.policy, decisions=decisions)

    def test_floor_reaches_previews_tools_and_fallback(self):
        self.data['target'] = 500000
        self.policy = limits(self.data, total=100000, increase=100000)
        self.policy['decrease_limits'] = dict.fromkeys(CATEGORY_CODES, 0)
        self.policy['decrease_limits']['TRANSPORT_CAR'] = 100000
        # An unfloored engine must be rebuilt before any fallback/LLM selection.
        self.engine = build_candidates(self.data, self.ratios, policy=self.policy)
        result = self.choose(reality_profile={'has_vehicle': False})
        self.assertEqual(result['selection_source'], 'llm_tools')
        from .test_design_explain import assert_ledger
        assert_ledger(self, result['explanations'], result['allocations'])
        self.assertEqual(result['allocations']['TRANSPORT_CAR'], 55000)
        self.assertEqual(result['allocations']['FOOD'], 85000)
        context = json.loads(self.requests[0]['messages'][1]['content'])
        self.assertEqual(context['system_floors'], {'TRANSPORT_CAR': 55000})
        self.assertEqual(context['minimums'], {})
        preview = json.loads(self.requests[0]['messages'][-1]['content'])
        self.assertEqual(preview['increase_preview']['allocations']['TRANSPORT_CAR'], 55000)
        for missing_key in (True, False):
            with patch.object(selection, '_request_action', side_effect=TimeoutError):
                result = self.choose(key='' if missing_key else 'synthetic-test-key', reality_profile={'has_vehicle': False})
            self.assertGreaterEqual(result['allocations']['TRANSPORT_CAR'], 55000)
            self.assertEqual(result['selection_source'], 'rule_fallback')

    def test_floor_unaffordable_skips_llm(self):
        self.data['target'] = 50000
        result = self.choose(reality_profile={'has_vehicle': False})
        self.assertEqual(result['reason'], 'system_floor_unaffordable')
        self.assertEqual(result['shortfall'], 5000)
        self.assertIsNone(result['result'])
        self.connection_class.assert_not_called()

    def test_partial_adjustment_is_neither_old_base_nor_old_priority(self):
        before = deepcopy((self.data, self.engine))
        result = self.choose()
        base, full = [c['result']['allocations'] for c in self.engine['candidates']]
        self.assertNotEqual(result['allocations'], base)
        self.assertNotEqual(result['allocations'], full)
        self.assertEqual(result['allocations']['FOOD'], base['FOOD'] + 20000)
        self.assertEqual(result['allocations']['EDUCATION'], base['EDUCATION'])
        self.assertEqual(result['allocations']['MEDICAL_HEALTH'], base['MEDICAL_HEALTH'] + 20000)
        self.assertEqual(sum(result['allocations'].values()), self.data['target'])
        self.assertEqual(result['selection_source'], 'llm_tools')
        from .test_design_explain import assert_ledger
        assert_ledger(self, result['explanations'], result['allocations'])
        self.assertEqual([d['action'] for d in result['decisions']], self.actions)
        self.assertEqual((self.data, self.engine), before)
        self.assertNotIn('reasons', result)
        self.assertNotIn('summary', result)

    def test_all_increases_match_existing_rule_engine(self):
        self.actions = ['increase'] * 3
        result = self.choose()
        self.assertEqual(result['allocations'], self.engine['candidates'][1]['result']['allocations'])
        self.assertEqual(result['explanations'], self.engine['candidates'][1]['explanations'])

    def test_finish_after_one_step_and_immediate_finish(self):
        self.actions = ['increase', 'finish']
        result = self.choose()
        self.assertEqual(len(result['decisions']), 1)
        self.assertEqual(result['allocations'], self.replay(result['decisions'])['allocations'])
        self.assertEqual(len(self.requests), 2)
        self.requests.clear()
        self.actions = ['finish']
        self.assertEqual(self.choose()['allocations'], self.engine['candidates'][0]['result']['allocations'])
        self.assertEqual(len(self.requests), 1)

    def test_amounts_constraints_policy_previews_and_tool_results_reach_llm(self):
        self.choose(living_budget=1050000, other_reserved=50000)
        first = self.requests[0]
        context = json.loads(first['messages'][1]['content'])
        self.assertEqual(context['living_budget'], 1050000)
        self.assertEqual(context['other_reserved'], 50000)
        self.assertEqual(context['allocation_budget'], 1000000)
        self.assertEqual(context['ml_draft_amounts']['FOOD'], 100000)
        self.assertEqual(context['policy'], self.policy)
        for key in ('minimums', 'locks', 'priorities'):
            self.assertEqual(context[key], self.data[key])
        preview = json.loads(first['messages'][-1]['content'])
        self.assertEqual(preview['current_category'], 'FOOD')
        self.assertEqual(preview['increase_preview']['remaining_transfer_limit'], 40000)
        self.assertEqual([m['role'] for m in self.requests[1]['messages']],
                         ['system', 'user', 'user', 'assistant', 'tool', 'user'])
        tool = self.requests[1]['messages'][-2]
        self.assertEqual(tool['tool_call_id'], 'call_test')
        self.assertEqual(json.loads(tool['content'])['allocations']['FOOD'], 180000)
        self.assertFalse(first['parallel_tool_calls'])
        self.assertEqual(first['tool_choice'], 'required')
        self.assertTrue(first['tools'][0]['function']['strict'])
        self.assertFalse(first['store'])
        for key in ('take_home_income', 'housing_fixed', 'age', 'sex', 'synthetic-test-key'):
            self.assertNotIn(key, json.dumps(first['messages']))

    def test_same_ratios_different_budget_scale_is_visible(self):
        observed = []
        for target in (500000, 2000000):
            self.data['target'] = target
            self.engine = build_candidates(self.data, self.ratios, policy=self.policy)
            self.requests.clear()
            self.actions = ['finish']
            self.choose()
            observed.append(json.loads(self.requests[0]['messages'][1]['content']))
        self.assertEqual([c['living_budget'] for c in observed], [500000, 2000000])
        self.assertEqual([c['ml_draft_amounts']['FOOD'] for c in observed], [50000, 200000])

    def test_missing_key_single_candidate_and_zero_skip_network(self):
        self.assertEqual(self.choose(key='')['fallback'], 'missing_api_key')
        for data in (inp(target=0), inp(target=800000, locks={c:100000 for c in CATEGORY_CODES if c not in ('TRAVEL_LODGING', 'HOUSING_TELECOM')})):
            engine = build_candidates(data, None)
            result = selection.choose_budget(data, engine, ml_ratios=None, api_key='synthetic-test-key')
            self.assertEqual(result['selection_source'], 'single_candidate')
            self.assertEqual(sum(result['allocations'].values()), data['target'])
        self.connection_class.assert_not_called()

    def test_failure_after_one_step_discards_partial_adjustment(self):
        original = self.connection.request.side_effect
        def failing(*args, **kwargs):
            if self.requests:
                raise TimeoutError('private')
            original(*args, **kwargs)
        self.connection.request.side_effect = failing
        result = self.choose()
        self.assertEqual(result['fallback'], 'adjustment_failed:TimeoutError')
        self.assertEqual(result['allocations'], self.engine['candidates'][1]['result']['allocations'])
        self.assertEqual(result['explanations'], self.engine['candidates'][1]['explanations'])
        self.assertEqual(result['decisions'], [])
        self.assertNotIn('private', str(result))

    def test_invalid_tools_numbers_order_and_free_text_fall_back(self):
        valid = tool_response('FOOD', 'increase')
        invalid = []
        for args in (('EDUCATION', 'increase', {}), ('FOOD', 'increase', {'amount': 10000}),
                     ('FOOD', 'invent', {}), ('FOOD', 'increase', {'rate': 20})):
            invalid.append(tool_response(args[0], args[1], **args[2]))
        text = deepcopy(valid); text['choices'][0]['message']['content'] = '추천합니다'
        invalid.append(text)
        wrong_tool = deepcopy(valid); wrong_tool['choices'][0]['message']['tool_calls'][0]['function']['name'] = 'save'
        invalid.append(wrong_tool)
        parallel = deepcopy(valid); parallel['choices'][0]['message']['tool_calls'] *= 2
        invalid.append(parallel)
        for response in invalid:
            with self.subTest(response=response):
                self.connection.request.side_effect = None
                self.response.read.return_value = json.dumps(response).encode()
                self.assertEqual(self.choose()['selection_source'], 'rule_fallback')

    def test_http_refusal_truncation_bad_json_and_oversize(self):
        self.connection.request.side_effect = None
        self.response.status = 429
        self.assertEqual(self.choose()['selection_source'], 'rule_fallback')
        self.response.read.assert_not_called()
        self.response.status = 200
        for finish, refusal in (('length', None), ('tool_calls', 'refused')):
            value = tool_response('FOOD', 'increase')
            value['choices'][0]['finish_reason'] = finish
            value['choices'][0]['message']['refusal'] = refusal
            self.response.read.return_value = json.dumps(value).encode()
            self.assertEqual(self.choose()['selection_source'], 'rule_fallback')
        for raw in (b'not json', b'x' * 65537):
            self.response.read.return_value = raw
            self.assertEqual(self.choose()['selection_source'], 'rule_fallback')

    def test_elapsed_deadline_discards_late_result(self):
        with patch.object(selection, 'monotonic', side_effect=[0, 1, 31]):
            result = self.choose()
        self.assertEqual(result['fallback'], 'adjustment_failed:TimeoutError')
        self.assertEqual(result['decisions'], [])

    def test_invalid_candidate_and_total_context_rejected_before_network(self):
        with self.assertRaises(ValueError):
            self.choose(living_budget=999)
        self.engine['candidates'][1]['result']['allocations']['FOOD'] += 1
        with self.assertRaises(ValueError):
            self.choose()
        self.connection_class.assert_not_called()

    def test_replay_enforces_priority_prefix_and_rejects_extra_money(self):
        for decisions in ([{'category': 'EDUCATION', 'action': 'increase'}],
                          [{'category': 'FOOD', 'action': 'increase', 'amount': 1000}],
                          [{'category': 'FOOD', 'action': 'increase'}] * 2,
                          [{'category': 'TRAVEL_LODGING', 'action': 'increase'}]):
            with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                self.replay(decisions)

    def test_cumulative_limits_never_reset_between_tool_calls(self):
        decisions = []
        previous_moved = 0
        for code in ('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'):
            decisions.append({'category': code, 'action': 'increase'})
            current = self.replay(decisions)
            moved = sum(t['amount'] for t in current['transfers'])
            self.assertGreaterEqual(moved, previous_moved)
            self.assertLessEqual(moved, self.policy['total_limit'])
            self.assertEqual(current['remaining_transfer_limit'], self.policy['total_limit'] - moved)
            for donor in CATEGORY_CODES:
                self.assertLessEqual(sum(t['amount'] for t in current['transfers'] if t['from'] == donor),
                                     self.policy['decrease_limits'][donor])
            self.assertTrue(all(t['amount'] % 1000 == 0 for t in current['transfers']))
            previous_moved = moved

    def test_partial_plan_preserves_exact_minimum_and_lock(self):
        self.data['minimums'] = {'EDUCATION': 300001}
        self.data['locks'] = {'MEDICAL_HEALTH': 12345}
        for decision in ('increase', 'keep'):
            result = self.replay([{'category': 'FOOD', 'action': decision},
                                  {'category': 'EDUCATION', 'action': 'increase'}])
            self.assertGreaterEqual(result['allocations']['EDUCATION'], 300001)
            self.assertEqual(result['allocations']['MEDICAL_HEALTH'], 12345)
            self.assertEqual(sum(result['allocations'].values()), self.data['target'])
