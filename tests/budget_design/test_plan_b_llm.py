"""Synthetic one-call order tests; no actual network, DB or production policy."""
from copy import deepcopy
import json
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
import plan_b_llm as agent
from plan_b_budget import build_history_budget, fixed_shortfall_order
from test_plan_b_priority import scenario, AS_OF


def fake_order(name, schema, context, key, timeout, model):
    return {'orders': deepcopy(context['groups']),
            'reason_codes': {c: 'stable' for c in context['categories']}}


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING': 'false'}))
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
        import django
        django.setup()
    def run_case(self, **kw):
        data, history, rates, protected, _ = scenario()
        args = dict(as_of=AS_OF,shortfall_cut_rates=rates,user_protected=protected,baseline='average',**kw)
        return agent.design_history_budget(data,history,**args)
    def test_default_has_no_llm(self):
        with patch.object(agent,'_request_tool') as call:
            out=self.run_case()
        call.assert_not_called()
        self.assertEqual(out['split'],'proportional')

    def test_external_opt_out_also_disables_tracing(self):
        with patch.dict(os.environ, {'LANGSMITH_TRACING': 'true'}), \
                patch('budgets.design_tracing._start') as trace, \
                patch.object(agent, '_request_tool') as call:
            out = self.run_case(variant='priority_increase_first', increase_threshold=0.1)
        trace.assert_not_called()
        call.assert_not_called()
        self.assertEqual(out['langsmith']['status'], 'disabled')

    def test_runner_llm_preserves_settings_and_protection(self):
        from run_plan_b_test import run_comparison, synthetic_input
        data, months, codes = synthetic_input()
        original = deepcopy((data, months))
        with patch.dict(os.environ, {'LLM_MODEL': 'test-model', 'OPENAI_API_KEY': 'test'}), \
                patch('run_plan_b_test.synthetic_input', return_value=(data, months, codes)), \
                patch.object(agent, '_request_tool', return_value={'increased': ['FASHION_SHOPPING']}) as call:
            _, _, out = run_comparison(cut_rate=50, increase_threshold=0.1, use_llm=True,
                                      user_protected=['FASHION_SHOPPING'])
        call.assert_called_once()
        self.assertEqual(out['llm_status'], 'completed')
        self.assertEqual(out['allocations']['FASHION_SHOPPING'], 200_000)
        self.assertEqual(out['allocations']['EDUCATION'], 100_000)
        self.assertGreaterEqual(out['allocations']['FOOD'], 350_000)
        self.assertEqual(sum(out['allocations'].values()), data['target'])
        self.assertEqual((data, months), original)

    def test_runner_failure_returns_same_rule_result(self):
        from run_plan_b_test import run_comparison
        with patch.dict(os.environ, {'LLM_MODEL': 'test-model', 'OPENAI_API_KEY': 'test'}), \
                patch.object(agent, '_request_tool', side_effect=TimeoutError):
            _, rule, out = run_comparison(cut_rate=50, increase_threshold=0.1, use_llm=True)
        self.assertEqual(out['allocations'], rule['allocations'])
        self.assertEqual(out['llm_status'], 'fallback')

    def test_duplicate_or_extra_outputs_rejected(self):
        for answer in ({'increased': ['FASHION_SHOPPING']*2},
                       {'increased': [], 'minimums': {'FOOD': 0}}):
            with self.subTest(answer=answer), patch.dict(os.environ, {'LLM_MODEL': 'test-model'}), \
                    patch.object(agent, '_request_tool', return_value=answer):
                out = self.run_case(variant='priority_increase_first', increase_threshold=0.1,
                                    allow_external=True, api_key='test')
            self.assertEqual(out['llm_status'], 'fallback')

    @unittest.skipUnless(importlib.util.find_spec('langsmith'), 'optional LangSmith SDK not installed')
    def test_trace_records_usage_without_financial_inputs(self):
        from run_plan_b_test import run_comparison
        reply = {'choices': [{'finish_reason': 'tool_calls', 'message': {
            'content': None, 'tool_calls': [{'type': 'function', 'function': {
                'name': 'identify_increased', 'arguments': '{"increased":["FASHION_SHOPPING"]}'}}]}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}
        with patch.dict(os.environ, {'LLM_MODEL': 'test-model', 'OPENAI_API_KEY': 'private-openai-key',
                                     'LANGSMITH_TRACING': 'true', 'LANGSMITH_API_KEY': 'private-trace-key'}), \
                patch('langsmith.Client') as client, patch.object(agent, 'HTTPSConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps(reply).encode()
            _, _, out = run_comparison(cut_rate=50, increase_threshold=0.1, use_llm=True)
        self.assertEqual(out['llm_status'], 'completed')
        self.assertEqual(out['langsmith']['status'], 'queued')
        runs = [call.kwargs for call in client.return_value.create_run.call_args_list]
        llm = next(run for run in runs if run['run_type'] == 'llm')
        self.assertEqual(llm['name'], 'plan_b_identify_increased')
        self.assertEqual(llm['outputs']['usage_metadata']['total_tokens'], 110)
        payload = json.dumps(runs, default=str)
        for forbidden in ('private-openai-key', 'private-trace-key', 'monthly_relative_to_average',
                          'untrusted_minimum_reason', 'minimums', 'allocations', '350000'):
            self.assertNotIn(forbidden, payload)
    def test_alternative_needs_explicit_threshold(self):
        out=self.run_case(variant='priority_increase_first')
        self.assertEqual(out['reason'],'increase_threshold_required')
    def test_alternative_llm_and_fallback(self):
        with patch.dict(os.environ,{'LLM_MODEL':'test-model'}), patch.object(agent,'_request_tool',return_value={'increased':['FASHION_SHOPPING']}) as call:
            out=self.run_case(variant='priority_increase_first',increase_threshold=0.1,allow_external=True,api_key='test')
        self.assertEqual(out['llm_status'],'completed')
        from budgets.test_design_explain import assert_ledger
        assert_ledger(self, out['explanations'], out['allocations'])
        self.assertEqual(out['increased'],['FASHION_SHOPPING'])
        call.assert_called_once()
        with patch.dict(os.environ,{'LLM_MODEL':'test-model'}), patch.object(agent,'_request_tool',return_value={'increased':['FOOD']}):
            out=self.run_case(variant='priority_increase_first',increase_threshold=0.1,allow_external=True,api_key='test')
        self.assertEqual(out['llm_status'],'fallback')
        assert_ledger(self, out['explanations'], out['allocations'])
        self.assertEqual(out['increased'],['FASHION_SHOPPING'])
    def test_match_has_no_adjustment(self):
        data,history,rates,protected,_=scenario()
        data['target']=1_000_000
        out=agent.design_history_budget(data,history,as_of=AS_OF,shortfall_cut_rates=rates,baseline='average')
        self.assertEqual(out['variant'],'priority_order')
        self.assertEqual(sum(out['allocations'].values()),1_000_000)
        self.assertFalse(out['cuts'])


class TransportTests(unittest.TestCase):
    def response(self, **overrides):
        message = {'content': None, 'tool_calls': [{'type': 'function', 'function': {
            'name': 'order_shortfall', 'arguments': '{"choice":"keep"}'}}]}
        return {'choices': [{'finish_reason': 'tool_calls', 'message': {**message, **overrides}}]}

    def request(self, response, status=200, model='test-model'):
        conn = Mock()
        conn.getresponse.return_value.status = status
        conn.getresponse.return_value.read.return_value = json.dumps(response).encode()
        with patch.object(agent, 'HTTPSConnection', return_value=conn):
            result = agent._request_tool('order_shortfall', agent._object({
                'choice': {'type': 'string', 'enum': ['apply', 'keep']}}), {}, 'fake-key', 1, model)
        return result, conn

    def test_strict_tool_request_no_store_and_connection_closed(self):
        result, conn = self.request(self.response())
        self.assertEqual(result, {'choice': 'keep'})
        body = json.loads(conn.request.call_args.kwargs['body'])
        self.assertFalse(body['store'])
        self.assertFalse(body['parallel_tool_calls'])
        self.assertTrue(body['tools'][0]['function']['strict'])
        self.assertFalse(body['tools'][0]['function']['parameters']['additionalProperties'])
        conn.close.assert_called_once()

    def test_http_failure_refusal_text_and_multiple_tools_rejected(self):
        for response, status in ((self.response(), 429), (self.response(refusal='no'), 200),
                                 (self.response(content='explanation'), 200),
                                 (self.response(tool_calls=[]), 200)):
            with self.subTest(status=status, response=response), self.assertRaises(ValueError):
                self.request(response, status)

    def test_forced_function_stop_is_valid_but_truncated_result_is_not(self):
        payload = self.response()
        payload['choices'][0]['finish_reason'] = 'stop'
        self.assertEqual(self.request(payload)[0], {'choice': 'keep'})
        payload['choices'][0]['finish_reason'] = 'length'
        with self.assertRaisesRegex(ValueError, '^llm_output_truncated$'):
            self.request(payload)

    def test_configured_luna_uses_required_non_reasoning_tool_mode(self):
        _, conn = self.request(self.response(), model='gpt-6-luna')
        body = json.loads(conn.request.call_args.kwargs['body'])
        self.assertEqual(body['model'],'gpt-6-luna')
        self.assertEqual(body['reasoning_effort'],'none')


if __name__ == '__main__':
    unittest.main()
