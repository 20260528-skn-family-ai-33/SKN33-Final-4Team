"""Deployment check uses a fixed synthetic profile, never credentials or an LLM."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.budget_design import inference


class InferenceCheckTests(unittest.TestCase):
    def test_ready_check_reports_model_and_uses_synthetic_profile(self):
        output = StringIO()
        result = {'model_name': 'catboost_shallow', 'artifact_id': 'synthetic-artifact',
                  'used_fallback': False, 'ratios': dict.fromkeys(inference.CATEGORIES.values(), .1),
                  'draft_amounts': dict.fromkeys(inference.CATEGORIES.values(), 100000)}
        with patch.object(inference, 'predict_ml', return_value=result) as predict, redirect_stdout(output):
            self.assertEqual(inference.main(), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'ready')
        self.assertTrue(payload['synthetic_only'])
        self.assertEqual(payload['draft_sum'], 1000000)
        self.assertEqual(predict.call_args.args[0]['living_budget_amount'], 1000000)
        self.assertNotIn('user', predict.call_args.args[0])

    def test_failure_has_nonzero_exit_and_never_echoes_exception_text(self):
        output = StringIO()
        with patch.object(inference, 'predict_ml', side_effect=ValueError('private path or secret')), redirect_stdout(output):
            self.assertEqual(inference.main(), 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['error_type'], 'ValueError')
        self.assertNotIn('private', output.getvalue())
        self.assertNotIn('secret', output.getvalue())


@unittest.skipUnless(all(Path(os.environ.get(key, '')).is_file() for key in
    ('BUDGET_INFERENCE_PYTHON', 'BUDGET_MODEL_PATH')), 'Real ML runtime and model must be explicitly configured')
class RealInferenceTests(unittest.TestCase):
    def profile(self, **changes):
        # Synthetic profiles only; these numbers are not service policy defaults.
        return {'age': 27, 'sex': 1, 'disposable_income': 3000000, 'has_vehicle': 0,
                'fixed_expense_amount': 700000, 'living_budget_amount': 1000000, **changes}

    def test_actual_model_has_valid_shares_and_repeatable_monthly_won_amounts(self):
        profiles = [self.profile(), self.profile(age=20, sex=0, disposable_income=1800000,
                    fixed_expense_amount=0, living_budget_amount=600000),
                    self.profile(age=34, sex=0, has_vehicle=1, disposable_income=5000000,
                    fixed_expense_amount=1200000, living_budget_amount=1500000)]
        first = None
        for profile in profiles:
            with self.subTest(age=profile['age'], car=profile['has_vehicle']):
                before = deepcopy(profile)
                result = inference.predict_ml(profile)
                self.assertEqual(profile, before)
                self.assertEqual(result['source'], 'model')
                self.assertFalse(result['used_fallback'])
                self.assertEqual(set(result['ratios']), set(inference.CATEGORIES.values()))
                self.assertAlmostEqual(sum(result['ratios'].values()), 1, places=12)
                self.assertAlmostEqual(sum(result['draft_amounts'].values()),
                                       profile['living_budget_amount'], places=6)
                if first is None:
                    first = result
        self.assertEqual(first, inference.predict_ml(profiles[0]))

    def test_actual_worker_rejects_invalid_inputs_without_echo_or_traceback(self):
        invalid = [self.profile(age=35), self.profile()]
        invalid[1].pop('living_budget_amount')
        for profile in invalid:
            profile['synthetic_private_marker'] = 'DO_NOT_ECHO_SYNTHETIC_INPUT'
            with self.subTest(keys=sorted(profile)):
                result = subprocess.run([os.environ['BUDGET_INFERENCE_PYTHON'],
                    str(Path(inference.__file__).with_name('predict_worker.py'))],
                    input=json.dumps(profile), capture_output=True, text=True,
                    encoding='utf-8', timeout=30, cwd=inference.ROOT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {'error_type': 'ValueError'})
                self.assertEqual(result.stderr, '')
                self.assertNotIn('DO_NOT_ECHO_SYNTHETIC_INPUT', result.stdout)

    def test_both_service_plans_use_actual_ml_without_database_or_llm(self):
        sys.path.insert(0, str(inference.ROOT / 'backend'))
        import django
        django.setup()
        from django.db.backends.base.base import BaseDatabaseWrapper
        from django.test import override_settings
        from budgets import design_service as service
        from budgets.test_design_candidates import inp, priorities
        from budgets.test_design_service import history

        data = inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'),
                   minimums={'FOOD': 150001}, locks={'EDUCATION': 12345})
        snapshot = {'engine_input': data, 'etc': 0, 'minimums': data['minimums'], 'locks': data['locks']}
        before = deepcopy(snapshot)
        with override_settings(BUDGET_DESIGN_POLICY=service.INITIAL_POLICY), \
                patch.object(BaseDatabaseWrapper, 'connect', side_effect=AssertionError('No DB allowed')), \
                patch.object(service, 'choose_budget', side_effect=AssertionError('No LLM allowed')), \
                patch.object(service, 'forecast_budget', side_effect=AssertionError('No LLM allowed')):
            for plan, records in (('a', {}), ('b', history())):
                with self.subTest(plan=plan), patch.object(service, 'history_for_user', return_value=records):
                    result = service.recommend(None, snapshot, 0, date(2026, 11, 7))
                    self.assertEqual(result['status'], 'proposed', result)
                    meta = result['_design']
                    self.assertEqual(meta['selection']['plan'], plan)
                    self.assertEqual(meta['references']['ml_reference_meta']['source'], 'model')
                    self.assertEqual(meta['references']['ml_reference_meta']['model_id'], '20261026T074827Z_53769d64')
                    self.assertEqual(sum(result['allocations'].values())+result['unallocated'], meta['funded_target'])
                    self.assertGreaterEqual(result['allocations']['FOOD'], 150001)
                    self.assertEqual(result['allocations']['EDUCATION'], 12345)
                    self.assertTrue(all(v % 5000 == 0 for c, v in result['allocations'].items() if c != 'EDUCATION'))
                    self.assertEqual(meta['llm']['status'], 'not_run')
                    self.assertEqual(snapshot, before)
