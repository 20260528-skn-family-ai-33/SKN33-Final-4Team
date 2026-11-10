"""Manual runner defaults, mode forwarding and visible fallback; no live API."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/budget_design/run_agent_test.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('budget_agent_runner', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
sys.path.remove(str(SCRIPT.parent))


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Load Django before replacing HTTP classes used by import-time dependencies.
        from django.db.backends.base.base import BaseDatabaseWrapper
        with patch.object(BaseDatabaseWrapper, 'connect', side_effect=AssertionError('No DB')):
            runner.calculate(runner.test_input())

    def test_default_runs_real_engine_without_network_or_db(self):
        from django.db.backends.base.base import BaseDatabaseWrapper
        with patch.object(BaseDatabaseWrapper, 'connect', side_effect=AssertionError('No DB')), \
                patch('http.client.HTTPSConnection', side_effect=AssertionError('No API')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runner.main([])
        self.assertEqual(code, 0)
        self.assertIn('OpenAI=OFF', output.getvalue())
        self.assertIn('1,050,000', output.getvalue())
        self.assertIn('최종 추천', output.getvalue())

    def test_real_flags_forwarded_and_fallback_is_not_reported_as_llm_success(self):
        result = runner.calculate(runner.test_input())
        result['selected'].update(selection_source='rule_fallback', fallback='missing_api_key')
        with patch.object(runner, 'calculate', return_value=result) as calculate, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runner.main(['--ml', '--llm'])
        self.assertEqual(code, 3)
        self.assertEqual(calculate.call_args.kwargs, {'use_ml': True, 'use_llm': True})
        self.assertIn('missing_api_key', output.getvalue())
        self.assertIn('규칙 계산으로 대체', output.getvalue())

    def test_infeasible_and_invalid_input_return_nonzero_without_traceback(self):
        form = runner.test_input()
        for code in ('FOOD', 'EDUCATION'):
            form['rows'][code]['minimum'] = '600000'
        with patch.object(runner, 'test_input', return_value=form), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main([]), 2)
        self.assertIn('조건 충돌', output.getvalue())
        with patch.object(runner, 'calculate', side_effect=RuntimeError('private details')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main([]), 2)
        self.assertNotIn('private details', output.getvalue())
        self.assertIn('RuntimeError', output.getvalue())

    def test_help_does_not_execute_agent(self):
        with patch.object(runner, 'calculate') as calculate, contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as exit:
            runner.main(['--help'])
        self.assertEqual(exit.exception.code, 0)
        calculate.assert_not_called()
