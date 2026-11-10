"""Synthetic-only traces; no LangSmith/OpenAI network calls."""
import importlib.util
import json
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase

from . import design_tracing as tracing
from .design_candidates import build_candidates
from .design_selection import choose_budget
from .recommend import CATEGORY_CODES
from .test_design_candidates import inp, limits, priorities
from .test_design_selection import tool_response

CONFIG = {'LANGSMITH_TRACING': 'true', 'LANGSMITH_API_KEY': 'synthetic-trace-key',
          'LANGSMITH_PROJECT': 'synthetic-budget-test'}


class DisabledTracingTests(SimpleTestCase):
    def test_disabled_and_missing_key_do_not_require_sdk(self):
        @tracing.trace_budget
        def calculate():
            return {'allocations': {'FOOD': 123456}, 'selection_source': 'rule', 'fallback': None}
        with patch.dict('sys.modules', {'langsmith': None}):
            for config, expected in (({}, 'disabled'), ({'LANGSMITH_TRACING': 'true'}, 'missing_api_key'),
                                     (CONFIG, 'unavailable:ModuleNotFoundError')):
                with self.subTest(expected=expected):
                    result = calculate(trace_config=config)
                    self.assertEqual(result['allocations']['FOOD'], 123456)
                    self.assertEqual(result['langsmith']['status'], expected)

    def test_usage_only_accepts_nonnegative_integer_counts(self):
        self.assertEqual(tracing.token_usage({'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12,
                                             'secret': 'private'}),
                         {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12})
        for usage in (None, [], {'prompt_tokens': True, 'completion_tokens': -1, 'total_tokens': '12'}):
            self.assertEqual(tracing.token_usage(usage), {})


@skipUnless(importlib.util.find_spec('langsmith'), 'optional LangSmith SDK not installed')
class TracingTests(SimpleTestCase):
    def setUp(self):
        client_patch = patch('langsmith.Client')
        self.client_type = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.client = self.client_type.return_value

    def runs(self):
        return [call.kwargs for call in self.client.create_run.call_args_list]

    def test_real_sdk_serializes_hierarchy_without_financial_data(self):
        data = inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))
        ratios = dict.fromkeys(CATEGORY_CODES, .1)
        engine = build_candidates(data, ratios, policy=limits(data, total=60000, increase=20000))
        response = tool_response('FOOD', 'finish')
        response['usage'] = {'prompt_tokens': 42, 'completion_tokens': 8, 'total_tokens': 50}
        with patch('budgets.design_selection.HTTPSConnection') as connection:
            connection.return_value.getresponse.return_value.status = 200
            connection.return_value.getresponse.return_value.read.return_value = json.dumps(response).encode()
            result = choose_budget(data, engine, ml_ratios=ratios, api_key='private-openai-key', trace_config=CONFIG)
        self.assertEqual(result['selection_source'], 'llm_tools')
        self.assertEqual(result['langsmith']['status'], 'queued')
        runs = self.runs()
        root = runs[0]
        self.assertEqual(root['name'], 'budget_design')
        self.assertEqual(len(runs), 5)
        for run in runs[1:]:
            self.assertEqual(run['parent_run_id'], root['id'])
            self.assertEqual(run['trace_id'], root['trace_id'])
            self.assertIsNotNone(run['end_time'])
        llm = next(run for run in runs if run['run_type'] == 'llm')
        self.assertEqual(llm['outputs'], {'category': 'FOOD', 'action': 'finish',
                                         'usage_metadata': {'input_tokens': 42, 'output_tokens': 8, 'total_tokens': 50}})
        self.assertEqual(llm['extra']['metadata']['ls_model_name'], 'gpt-4o-mini')
        payload = json.dumps(runs, default=str)
        for forbidden in ('private-openai-key', 'synthetic-trace-key', 'allocations', 'minimums',
                          'messages', '1000000', 'SYSTEM_PROMPT', 'take_home_income'):
            self.assertNotIn(forbidden, payload)
        self.client.flush.assert_called_once_with(timeout=2)
        self.client.close.assert_called_once_with(timeout=0)

    def test_tracing_failure_does_not_retry_or_change_calculation(self):
        calls = []
        @tracing.trace_budget
        def calculate():
            calls.append(1)
            return {'allocations': {'FOOD': 123456}, 'selection_source': 'rule', 'fallback': None}
        self.client.create_run.side_effect = RuntimeError('private-error-body')
        result = calculate(trace_config=CONFIG)
        self.assertEqual(result['allocations'], {'FOOD': 123456})
        self.assertEqual(calls, [1])
        self.assertEqual(result['langsmith']['status'], 'failed:RuntimeError')
        self.assertNotIn('private-error-body', str(result))
        self.assertIsNone(tracing._active.get())

    def test_exception_trace_has_type_only_and_preserves_original_exception(self):
        @tracing.trace_budget
        def calculate():
            with tracing.span('adjust_budget', 'tool'):
                raise ValueError('private-financial-data')
        with self.assertRaisesRegex(ValueError, 'private-financial-data'):
            calculate(trace_config=CONFIG)
        self.assertEqual([run['error'] for run in self.runs()], ['ValueError', 'ValueError'])
        self.assertNotIn('private-financial-data', json.dumps(self.runs(), default=str))
        self.assertIsNone(tracing._active.get())

    def test_input_snapshot_does_not_change_with_later_decisions(self):
        @tracing.trace_budget
        def calculate():
            decisions = [{'category': 'FOOD', 'action': 'increase'}]
            with tracing.span('adjust_budget', 'tool', inputs={'decisions': decisions}):
                pass
            decisions.append({'category': 'EDUCATION', 'action': 'keep'})
            return {'selection_source': 'llm_tools', 'fallback': None}
        calculate(trace_config=CONFIG)
        self.assertEqual(len(self.runs()[1]['inputs']['decisions']), 1)

    def test_agent_address_uses_explicit_environment(self):
        @tracing.trace_budget
        def calculate():
            return {'selection_source': 'rule', 'fallback': None}
        result = calculate(trace_config={**CONFIG, 'LANGSMITH_AGENT_ID': 'budget-design',
                                         'LANGSMITH_AGENT_ENVIRONMENT': 'development'})
        self.assertEqual(result['langsmith']['status'], 'queued')
        self.assertEqual(self.runs()[0]['address'],
                         {'kind': 'AGENT', 'id': 'budget-design', 'environment': 'DEVELOPMENT'})
