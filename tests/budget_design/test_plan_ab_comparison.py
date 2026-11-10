"""Synthetic A/B runner tests. Model/network responses are mocked; real server math."""
from copy import deepcopy
import contextlib
import io
import os
import subprocess
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

    def test_cli_comparison_imports_without_tkinter(self):
        script = '''import builtins, sys
original = builtins.__import__
def without_tk(name, *args, **kwargs):
    if name == 'tkinter' or name.startswith('tkinter.'):
        raise ModuleNotFoundError('tkinter unavailable', name='tkinter')
    return original(name, *args, **kwargs)
builtins.__import__ = without_tk
sys.path.append(sys.argv[1])
import compare_plans, run_agent_test, simulator
assert simulator.tk is None
assert len(compare_plans.test_input()['months']) == 3
print('headless_cli_ready')
'''
        result = subprocess.run([sys.executable, '-c', script,
            str(Path(__file__).resolve().parents[2] / 'scripts/budget_design')],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('headless_cli_ready', result.stdout)

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

    def test_b_priority_cut_notice_uses_history_after_rounding(self):
        from test_plan_b_history import purchase
        case = runner.test_input()
        case['input'].update(target=1000000, minimums={'TRANSPORT_CAR': 180000}, locks={},
            priorities=[{'category': code, 'rank': rank} for rank, code in enumerate(
                ('TRANSPORT_CAR', 'FASHION_SHOPPING', 'BEAUTY'), 1)])
        amounts = {'FOOD': 555000, 'TRANSPORT_CAR': 181000, 'HOUSEHOLD': 23000,
                   'FASHION_SHOPPING': 320000, 'MEDICAL_HEALTH': 10000,
                   'BEAUTY': 65000, 'CULTURE_LEISURE': 23000}
        for month, row in case['months'].items():
            row['items'] = [purchase(month, amount, code) for code, amount in amounts.items()]
        case['b_cut_rates'] = dict.fromkeys(runner.CATEGORIES, 50)
        for code in ('FOOD', 'HOUSEHOLD', 'MEDICAL_HEALTH', 'CULTURE_LEISURE'):
            case['b_cut_rates'][code] = 5
        before = deepcopy(case)
        result = runner.compare(case)['b']
        self.assertEqual(result['status'], 'proposed')
        self.assertLess(result['allocations']['BEAUTY'] * 100, 65000 * 70)
        self.assertTrue(any('뷰티·미용(3순위): 평소 기준보다' in text for text in result['result']['reasons']))
        self.assertEqual(sum(result['allocations'].values()) + result['unallocated'], 1000000)
        self.assertEqual(case, before)

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

    def test_llm_can_increase_all_three_priorities_without_using_full_limit_on_first(self):
        case = runner.test_input()
        case['input']['locks'].pop('EDUCATION', None)
        def choose(messages, category, key, timeout):
            action = 'apply' if 'review_adjustment_preview' in messages[-1]['content'] else 'increase_medium'
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'synthetic',
                'type': 'function', 'function': {'name': 'adjust_budget',
                'arguments': '{"category":"' + category + '","action":"' + action + '"}'}}]}
        with patch('budgets.design_selection._request_action', side_effect=choose), \
                patch('plan_b_llm._request_tool', return_value={'increased': []}):
            out = runner.compare(case, use_llm=True)['a']
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['selection_source'], 'llm_tools')
        base = out['engine']['candidates'][0]['result']['allocations']
        for code in ('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'):
            self.assertGreater(out['allocations'][code], base[code])
        total = sum(max(0, out['allocations'][c] - base[c]) for c in base)
        self.assertLessEqual(total, case['input']['target'] * 20 // 100)

    def test_synthetic_note_reaches_a_decision_context_only(self):
        case = runner.test_input()
        case['user_note'] = '합성: 다음 달 이사할 예정이에요'
        seen = []
        def finish(messages, category, key, timeout):
            seen.append(messages[1]['content'])
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'synthetic',
                'type': 'function', 'function': {'name': 'adjust_budget',
                'arguments': '{"category":"' + category + '","action":"finish"}'}}]}
        with patch('budgets.design_selection._request_action', side_effect=finish), \
                patch('plan_b_llm._request_tool', return_value={'increased': []}):
            out = runner.compare(case, use_llm=True)['a']
        self.assertEqual(out['selection_source'], 'llm_tools')
        self.assertIn(case['user_note'], seen[0])
        self.assertNotIn(case['user_note'], str(out))

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

    def test_final_a_b_amount_units_except_user_locks(self):
        case = runner.test_input('surplus')
        case['input']['locks']['EDUCATION'] = 12345
        before = deepcopy(case)
        out = runner.compare(case)
        for name in ('a', 'b'):
            result = out[name]
            self.assertEqual(result['status'], 'proposed', result.get('reason'))
            self.assertEqual(result['allocations']['EDUCATION'], 12345)
            for code, amount in result['allocations'].items():
                if code != 'EDUCATION':
                    self.assertEqual(amount % 5000, 0)
            self.assertEqual(sum(result['allocations'].values()) + result['other_reserved'] + result['total_spare'],
                             case['input']['target'] + case['other_reserved'])
            for e in result['explanations']:
                self.assertEqual(e['final_amount'], result['allocations'][e['category']])
                self.assertEqual(sum(c['amount'] for c in e['causes']), e['diff'])
        self.assertEqual(case, before)

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
