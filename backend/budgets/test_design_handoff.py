"""Synthetic model/history handoff and soft targets; no external calls."""
from copy import deepcopy
from datetime import date
from fractions import Fraction
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from . import design_service as service
from .design_candidates import CATEGORY_NAMES
from .design_references import model_reference, transport_reference
from .design_targets import targeted_etc
from . import test_design_service as service_fixtures
from .test_design_service import history, policy
from .models import Budget
from .test_design_candidates import inp, priorities


def prediction():
    return {'ratios': dict.fromkeys(CATEGORY_NAMES.values(), .1), 'source': 'model',
            'used_fallback': False, 'artifact_id': 'synthetic-fixture-artifact', 'model_checksum': 'a'*64}


class DesignHandoffTest(SimpleTestCase):
    def setUp(self):
        self.snapshot = {'engine_input': inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH')),
                         'etc': 0, 'category_targets': {}}
        self.snapshot['engine_input']['category_targets'] = {}
        self.enterContext(override_settings(BUDGET_DESIGN_POLICY=policy()))
        self.predict = self.enterContext(patch.object(service, 'predict_ml', return_value=prediction()))

    def run_plan(self, plan='a', **goals):
        self.snapshot['category_targets'] = goals
        self.snapshot['engine_input']['category_targets'] = goals
        with patch.object(service, 'history_for_user', return_value=history() if plan == 'b' else {}):
            return service.recommend(None, self.snapshot, 0, date(2026, 11, 6))

    def test_both_plans_preserve_original_ml_and_input(self):
        for plan in ('a', 'b'):
            before = deepcopy(self.snapshot)
            out = self.run_plan(plan)
            ref = out['_design']['references']
            self.assertEqual(ref['ml_reference']['FOOD'], 100000)
            self.assertNotIn('TRAVEL_LODGING', ref['ml_reference'])
            self.assertEqual(ref['ml_reference_meta']['target'], 1000000)
            self.assertEqual(ref['ml_reference_meta']['model_checksum'], 'a'*64)
            self.assertEqual(len(ref['ml_reference_meta']['input_hash']), 64)
            self.assertFalse(ref['ml_reference_meta']['normalization']['active_categories_renormalized'])
            self.assertEqual(self.snapshot, before)
            self.assertEqual(self.predict.call_count, 1 if plan == 'a' else 2)

    def test_raw_ratio_share_ceil_is_one_won_not_five_thousand(self):
        data = inp()
        data['target'] = 1000001
        ref = model_reference(data, prediction())
        self.assertEqual(ref['ml_reference']['FOOD'], 100001)
        for bad in ({'used_fallback': True}, {'source': 'synthetic'}, {'model_checksum': 'A'*64}):
            self.assertEqual(model_reference(data, {**prediction(), **bad})['ml_reference_status'], 'unavailable')

    def test_transport_ready_requires_complete_normalization_and_keeps_fraction(self):
        records = history()
        for month, row in records.items():
            for item in row['items']:
                if item['category'] == 'TRANSPORT_CAR':
                    item['net_amount'] = {'2026-08': 20000, '2026-09': 30000, '2026-10': 40001}[month]
                    item['merchant_key'] = 'same-recurring-key'
        for ready in (True, False):
            if not ready:
                records['2026-09']['normalization_checks']['refunds'] = 'unknown'
            with patch.object(service, 'history_for_user', return_value=records):
                out = service.recommend(None, self.snapshot, 0, date(2026, 11, 6))
            ref = out['_design']['references']['transport_history_reference']
            self.assertEqual(ref['status'], 'ready' if ready else 'unavailable')
            if ready:
                self.assertEqual(Fraction(ref['baseline_numerator'], ref['baseline_denominator']), Fraction(90001, 3))
            self.assertNotIn('merchant_key', str(ref))

    def test_category_target_seeds_both_plans_without_becoming_protection(self):
        for plan in ('a', 'b'):
            out = self.run_plan(plan, FOOD=450000)
            self.assertEqual(out['status'], 'proposed', out)
            self.assertEqual(out['allocations']['FOOD'], 450000)
            self.assertEqual(self.snapshot['engine_input']['minimums'], {})
            self.assertEqual(self.snapshot['engine_input']['locks'], {})
            self.assertEqual(out['_design']['target_conflicts'], [])

    def test_target_conflicts_report_requested_possible_and_diff(self):
        for key, changes in (
                ('locked_amount', {'locks': {'FOOD': 12345}}),
                ('minimum_or_floor', {'minimums': {'FOOD': 500000}})):
            self.snapshot['engine_input'].update(changes)
            out = self.run_plan('a', FOOD=450000)
            self.assertEqual(out['status'], 'target_conflict')
            conflict = out['conflicts'][0]
            self.assertEqual(conflict['reason'], key)
            self.assertEqual(conflict['requested_amount'], 450000)
            self.assertEqual(conflict['categories'], ['FOOD'])
            self.assertEqual(conflict['targets'], {'FOOD': 450000})
            self.assertEqual(conflict['diff'], conflict['possible_amount']-450000)
            self.snapshot['engine_input'].update(locks={}, minimums={})

    def test_etc_is_not_a_model_category_and_bad_unit_is_explicit(self):
        self.assertEqual(targeted_etc({'ETC': 50000, 'FOOD': 450000}, {}, {}, 1000000),
                         (50000, {'FOOD': 450000}, []))
        out = self.run_plan('b', FOOD=45555)
        self.assertEqual(out['status'], 'target_conflict')
        self.assertEqual(out['conflicts'][0]['reason'], 'amount_unit')

    def test_funding_does_not_rerun_model_or_raise_original_reference(self):
        self.snapshot['engine_input'].update(target=500000, minimums=dict.fromkeys(
            ('FOOD', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING'), 100000))
        with patch.object(service, 'history_for_user', return_value=history()):
            out = service.recommend(None, self.snapshot, 150000, date(2026, 11, 6))
        self.assertEqual(out['_design']['funded_target'], 600000)
        self.assertEqual(out['_design']['references']['ml_reference_meta']['target'], 500000)
        self.assertEqual(out['_design']['references']['ml_reference']['FOOD'], 50000)
        self.predict.assert_called_once()

    def test_random_soft_targets_keep_money_guards_and_input_unchanged(self):
        import random
        rng = random.Random(89)
        active = [c for c in CATEGORY_NAMES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')]
        for i in range(150):
            # Synthetic amounts only, not service policy defaults.
            goals = {c: rng.randrange(12, 40)*5000 for c in rng.sample(active, rng.randrange(1, 9))}
            plan = 'a' if i % 2 else 'b'
            out = self.run_plan(plan, **goals)
            if out['status'] == 'target_conflict':
                self.assertTrue(out['conflicts'])
                continue
            self.assertEqual(out['status'], 'proposed', (goals, out))
            self.assertEqual(sum(out['allocations'].values())+out['unallocated'], out['_design']['funded_target'])
            self.assertTrue(all(v >= 0 and v % 5000 == 0 for v in out['allocations'].values()))
            self.assertEqual(self.snapshot['engine_input']['category_targets'], goals)
            self.assertEqual(self.snapshot['engine_input']['minimums'], {})
            self.assertEqual(self.snapshot['engine_input']['locks'], {})


@override_settings(BUDGET_DESIGN_ENABLED=True)
class DesignHandoffAPITest(TestCase):
    setUpTestData = classmethod(service_fixtures.DesignAPITest.setUpTestData.__func__)
    setUp = service_fixtures.DesignAPITest.setUp
    post, task = service_fixtures.DesignAPITest.post, service_fixtures.DesignAPITest.task

    def test_a_b_save_identical_raw_reference_and_exact_transport_summary(self):
        for records in ({}, history()):
            with patch.object(service, 'history_for_user', return_value=records), \
                    patch.object(service, 'predict_ml', return_value=prediction()):
                task = self.task(self.post())
            self.assertEqual(task.result['outcome'], 'proposed', task.result)
            saved = Budget.objects.get(pk=task.result['budget_id'])
            reference = saved.input_snapshot
            self.assertEqual(reference['ml_reference_meta']['target'], reference['engine_input']['target'])
            self.assertEqual(reference['ml_reference']['FOOD'], (reference['engine_input']['target']+9)//10)
            self.assertEqual(reference['transport_history_reference']['status'], 'ready' if records else 'unavailable')

    def test_goals_set_changed_and_cleared_expire_prior_draft(self):
        with patch.object(service, 'history_for_user', return_value={}):
            first = self.task(self.post())
            self.profile.budget_category_targets = {'FOOD': 450000}
            self.profile.save(update_fields=['budget_category_targets'])
            response = self.client.post(f"/api/budgets/{first.result['budget_id']}/approve/", {},
                format='json', HTTP_IDEMPOTENCY_KEY='old-goal-condition')
            self.assertEqual(response.status_code, 409)
            next_task = self.task(self.post())
            budget = Budget.objects.get(pk=next_task.result['budget_id'])
            self.assertEqual(budget.input_snapshot['category_targets'], {'FOOD': 450000})
            self.assertEqual(budget.items.get(category_id='FOOD', bucket='living').allocated_amount, 450000)
            self.profile.budget_category_targets = {}
            self.profile.save(update_fields=['budget_category_targets'])
            response = self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json',
                HTTP_IDEMPOTENCY_KEY='cleared-goal-condition')
            self.assertEqual(response.status_code, 409)
            cleared = self.task(self.post())
            self.assertEqual(Budget.objects.get(pk=cleared.result['budget_id']).input_snapshot['category_targets'], {})

    def test_goal_rounding_difference_is_available_before_approval(self):
        self.profile.budget_category_targets = {'FOOD': 455000}
        self.profile.save(update_fields=['budget_category_targets'])
        with patch.object(service, 'history_for_user', return_value={}):
            task = self.task(self.post())
        self.assertEqual(task.result['outcome'], 'proposed', task.result)
        data = self.client.get('/api/budgets/').json()
        conflicts = data['recommendation']['targetConflicts']
        self.assertEqual(conflicts[0]['requestedAmount'], 455000)
        self.assertEqual(conflicts[0]['diff'], conflicts[0]['possibleAmount']-455000)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {'FOOD': 455000})
