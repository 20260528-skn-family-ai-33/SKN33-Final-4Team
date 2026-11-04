"""Synthetic A/B runner tests. Model/network responses are mocked; real server math."""
from copy import deepcopy
import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
import compare_plans as runner


def ml_response(profile):
    return {'ratios': dict.fromkeys(runner.CATEGORIES.values(), 0.1), 'artifact_id': 'synthetic-model'}


class ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'LANGSMITH_TRACING': 'false',
            'LLM_MODEL': 'test-model', 'OPENAI_API_KEY': 'synthetic-key'}))
        self.predict = self.enterContext(patch.object(runner, 'predict_ml', side_effect=ml_response))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB')))

    def test_same_input_and_protections_no_mutation(self):
        case = runner.test_input()
        before = deepcopy(case)
        with patch('budgets.design_selection._request_action') as a, patch('plan_b_llm._request_tool') as b:
            result = runner.compare(case)
        a.assert_not_called()
        b.assert_not_called()
        self.assertEqual(case, before)
        self.assertEqual(self.predict.call_count, 1)
        self.assertEqual(result['ml_reference']['FOOD'], 100000)
        self.assertEqual(self.predict.call_args.args[0]['living_budget_amount'], case['input']['target'])
        for name in ('a', 'b'):
            out = result[name]
            self.assertEqual(out['status'], 'proposed')
            self.assertEqual(sum(out['allocations'].values()), case['input']['target'])
            self.assertEqual(out['allocations']['EDUCATION'], 100_000)
            self.assertGreaterEqual(out['allocations']['FOOD'], 350_000)
            self.assertEqual(out['allocations']['TRAVEL_LODGING'], 0)
            for row in out['explanations']:
                self.assertEqual(sum(c['amount'] for c in row['causes']), row['diff'])
        self.assertGreaterEqual(result['a']['allocations']['TRANSPORT_CAR'], 55_000)
        self.assertEqual(result['b']['allocations']['TRANSPORT_CAR'], 0)

    def test_llm_flag_calls_both_and_keeps_protections(self):
        def finish(messages, category, key, timeout):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'synthetic',
                'type': 'function', 'function': {'name': 'adjust_budget',
                'arguments': '{"category":"' + category + '","action":"finish"}'}}]}
        with patch('budgets.design_selection._request_action', side_effect=finish) as a, \
                patch('plan_b_llm._request_tool', return_value={'increased': ['FASHION_SHOPPING']}) as b:
            out = runner.compare(runner.test_input(), use_llm=True)
        a.assert_called_once()
        b.assert_called_once()
        self.assertEqual(out['a']['selection_source'], 'llm_tools')
        self.assertEqual(out['b']['llm_status'], 'completed')
        for name in ('a', 'b'):
            self.assertEqual(sum(out[name]['allocations'].values()), 1_000_000)
            self.assertEqual(out[name]['allocations']['EDUCATION'], 100_000)
            self.assertGreaterEqual(out[name]['allocations']['FOOD'], 350_000)

    def test_surplus_displays_budget_and_free_money(self):
        out = runner.compare(runner.test_input('surplus'))
        self.assertEqual(out['a']['status'], 'proposed')
        self.assertEqual(out['b']['status'], 'proposed')
        self.assertGreater(out['b']['unallocated'], 0)
        self.assertEqual(sum(out['b']['allocations'].values())+out['b']['unallocated'],out['target'])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            runner.print_comparison(out)
        self.assertIn('B 여윳돈:', output.getvalue())
        self.assertIn('여윳돈으로 남겼어요', output.getvalue())

    def test_ml_failure_does_not_silently_disable_b_cut_floor(self):
        self.predict.side_effect = ValueError('private details')
        out = runner.compare(runner.test_input())
        self.assertEqual(out['a']['status'], 'error')
        self.assertEqual(out['a']['reason'], 'ValueError')
        self.assertEqual(out['b']['status'], 'needs_data')
        self.assertEqual(out['b']['reason'], 'ml_reference_unavailable')
        self.assertNotIn('private details', str(out))

    def test_missing_history_still_shows_a(self):
        case = runner.test_input()
        del case['months']['2026-08']
        out = runner.compare(case)
        self.assertEqual(out['a']['status'], 'proposed')
        self.assertEqual(out['b']['status'], 'needs_data')
        with contextlib.redirect_stdout(io.StringIO()):
            runner.print_comparison(out)

    def test_medical_case_and_displayed_difference(self):
        out = runner.compare(runner.test_input('medical'))
        self.assertEqual(out['history']['categories']['MEDICAL_HEALTH']['average'], 200_000)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            runner.print_comparison(out)
        difference = out['b']['allocations']['FOOD'] - out['a']['allocations']['FOOD']
        self.assertIn(f'{difference:+,}', output.getvalue())

    def test_failed_llm_is_visible_and_cli_nonzero(self):
        with patch('budgets.design_selection._request_action', side_effect=TimeoutError), \
                patch('plan_b_llm._request_tool', side_effect=TimeoutError), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runner.main(['--llm'])
        self.assertEqual(code, 3)
        self.assertIn('rule_fallback', output.getvalue())
        self.assertIn('llm_failed:TimeoutError', output.getvalue())


if __name__ == '__main__':
    unittest.main()
