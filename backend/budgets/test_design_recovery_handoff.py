"""Synthetic contract tests: no DB, ML, LLM, or financial setting writes."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase

from .design_handoff import build_design_handoff
from .design_candidates import build_candidates
from .design_targets import target_conflicts
from .recommend import CATEGORY_CODES
from .recommend import infeasible


class DesignHandoffTest(SimpleTestCase):
    def setUp(self):
        self.task = SimpleNamespace(pk=uuid4(), user_id=uuid4(), task_type='budget_recommend', input_hash='saved-input-hash')
        self.cycle = SimpleNamespace(pk=uuid4(), user_id=self.task.user_id)
        # All amounts below are test fixtures, not production policy defaults.
        self.snapshot = {'cycle': {'id': str(self.cycle.pk)}, 'etc': 20000,
            'saved': {'living_budget': 500000, 'saving_floor': 300000, 'cushion_monthly': 100000},
            'minimums': {'FOOD': 150001, 'ETC': 20000}, 'locks': {'EDUCATION': 12345, 'BEAUTY': 0},
            'category_targets': {'CULTURE_LEISURE': 100000},
            'engine_input': {'target': 480000, 'input_version': 1,
                'priorities': [{'category': 'FOOD', 'rank': 1}, {'category': 'EDUCATION', 'rank': 2},
                               {'category': 'BEAUTY', 'rank': 3}]},
            'private_note': 'must not be forwarded', 'transactions': [{'merchant': 'private merchant'}]}
        self.no_network = self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))

    def build(self, result, **kwargs):
        return build_design_handoff(self.task, self.cycle, self.snapshot, result,
                                    available_spare=kwargs.get('available_spare', 100000))

    def test_minimum_total_above_living(self):
        out = self.build(infeasible([{'type': 'minimums_over_living', 'shortfall': 90000}]))
        self.assertEqual(out['issue']['kind'], 'settings_conflict')
        self.assertEqual(out['issue']['shortfall'], 90000)

    def test_lock_below_minimum_does_not_invent_cash_shortage(self):
        out = self.build(infeasible([{'type': 'lock_below_minimum', 'category': 'EDUCATION',
                                     'locked_amount': 12345, 'min_amount': 20000}]))
        self.assertIsNone(out['issue']['shortfall'])
        self.assertEqual(out['constraints']['locks']['EDUCATION'], 12345)

    def test_all_locked_over_and_under_target(self):
        for difference in (10000, -200000):
            with self.subTest(difference=difference):
                out = self.build(infeasible([{'type': 'locks_mismatch_living', 'difference': difference}]))
                self.assertEqual(out['issue']['shortfall'], difference if difference > 0 else None)
                self.assertEqual(out['issue']['conflicts'][0]['difference'], difference)

    def test_target_conflict_reasons(self):
        for reason in ('locked_amount', 'minimum_amount', 'minimum_or_floor', 'amount_unit', 'living_budget'):
            with self.subTest(reason=reason):
                out = self.build({'status': 'target_conflict', 'reason': 'category_target_conflict',
                    'conflicts': [{'type': 'target_conflict', 'category': 'FOOD', 'reason': reason,
                                   'requested_amount': 150001, 'possible_amount': 155000}]})
                self.assertEqual(out['issue']['kind'], 'target_conflict')
                self.assertEqual(out['constraints']['minimums']['FOOD'], 150001)

    def test_funding_failures(self):
        for reason in ('system_floor_unaffordable', 'ml_floor_unaffordable',
                       'shortfall_exceeds_cut_limits', 'budget_unit_unaffordable'):
            with self.subTest(reason=reason):
                out = self.build({'status': 'needs_upper_allocation_review', 'reason': reason, 'shortfall': 5000})
                self.assertEqual(out['issue']['kind'], 'funding_shortfall')
                self.assertEqual(out['issue']['shortfall'], 5000)

    def test_nested_engine_failure_is_supported(self):
        result = infeasible([{'type': 'minimums_over_living', 'shortfall': 5000}])
        self.assertEqual(self.build({'status': 'infeasible', 'result': result})['issue']['shortfall'], 5000)

    def test_funding_shortfall_after_spare_takes_precedence(self):
        out = self.build({'status': 'needs_upper_allocation_review', 'reason': 'shortfall_exceeds_cut_limits',
                          'funding_shortfall': 30000, 'remaining_shortfall': 80000,
                          'funding_required': 130000, 'spare_transfer': 0})
        self.assertEqual(out['issue']['shortfall'], 30000)
        self.assertEqual(out['attempt']['spare_transfer'], 0)
        self.assertEqual(out['budget']['available_spare'], 100000)

    def test_missing_amount_is_unknown_even_when_summary_contains_number(self):
        out = self.build({'status': 'needs_upper_allocation_review', 'reason': 'ml_floor_unaffordable',
                          'summary': '생활비가 999,999원 부족해요.'})
        self.assertIsNone(out['issue']['shortfall'])
        self.assertEqual(out['attempt'], {})

    def test_amount_unit_conflict_is_not_a_cash_shortage(self):
        out = self.build({'status': 'needs_upper_allocation_review', 'reason': 'budget_unit_conflict', 'shortfall': 5000})
        self.assertEqual(out['issue']['kind'], 'amount_unit_conflict')
        self.assertIsNone(out['issue']['shortfall'])

    def test_do_not_route_success_general_priority_cut_or_technical_failures(self):
        for result in (
            {'status': 'proposed', 'explanations': [{'category': 'FOOD', 'text': '감액 안내'}]},
            {'status': 'needs_data', 'reason': 'ml_unavailable'},
            {'status': 'needs_data', 'reason': 'history_validation_failed'},
            {'status': 'needs_policy', 'reason': 'invalid_design_policy'},
            {'status': 'failed', 'reason': 'engine_check_failed'},
            {'status': 'needs_upper_allocation_review', 'reason': 'budget_unit_policy_conflict'},
            {'status': 'needs_upper_allocation_review', 'reason': 'shortfall_start_below_target'},
            {'status': 'needs_upper_allocation_review', 'reason': 'new_unknown_reason'},
            {'status': 'target_conflict', 'conflicts': [{'type': 'target_conflict', 'reason': 'invalid_target'}]},
            infeasible([{'type': 'unknown_conflict'}]), infeasible([]),
        ):
            with self.subTest(result=result):
                self.assertIsNone(self.build(result))

    def test_preserves_all_inputs_and_returns_detached_json(self):
        result = infeasible([{'type': 'minimums_over_living', 'shortfall': 90000,
                              'private_note': 'not for the handoff'}])
        before = deepcopy((self.snapshot, result, vars(self.task), vars(self.cycle)))
        out = self.build(result)
        self.assertEqual(out['priorities'], self.snapshot['engine_input']['priorities'])
        self.assertEqual(out['constraints']['minimums'], self.snapshot['minimums'])
        self.assertEqual(out['constraints']['locks'], self.snapshot['locks'])
        self.assertEqual(out['source_task_id'], str(self.task.pk))
        self.assertEqual(out['cycle_id'], str(self.cycle.pk))
        self.assertEqual(out['input_hash'], self.task.input_hash)
        serialized = json.dumps(out)
        self.assertNotIn('private merchant', serialized)
        self.assertNotIn('private_note', serialized)
        out['priorities'][0]['rank'] = 99
        out['constraints']['minimums']['FOOD'] = 0
        out['constraints']['locks'].clear()
        out['issue']['conflicts'][0]['shortfall'] = 0
        self.assertEqual((self.snapshot, result, vars(self.task), vars(self.cycle)), before)
        self.no_network.assert_not_called()

    def test_does_not_add_inactive_zero_locks(self):
        out = self.build(infeasible([{'type': 'minimums_over_living', 'shortfall': 1}]))
        self.assertNotIn('HOUSING_TELECOM', out['constraints']['locks'])
        self.assertNotIn('TRAVEL_LODGING', out['constraints']['locks'])
        self.assertIn('BEAUTY', out['constraints']['locks'])

    def test_multiple_conflicts_do_not_double_count_shortfall(self):
        out = self.build(infeasible([{'type': 'minimums_over_living', 'shortfall': 90000},
                                     {'type': 'locks_mismatch_living', 'difference': 90000}]))
        self.assertIsNone(out['issue']['shortfall'])
        self.assertEqual(len(out['issue']['conflicts']), 2)

    def test_invalid_spare_and_shortfall_rejected(self):
        result = infeasible([{'type': 'minimums_over_living', 'shortfall': 1}])
        for value in (-1, True, 1.5):
            with self.subTest(spare=value), self.assertRaisesRegex(ValueError, 'invalid_handoff_budget'):
                self.build(result, available_spare=value)
            with self.subTest(shortfall=value), self.assertRaisesRegex(ValueError, 'invalid_handoff_shortfall'):
                self.build(infeasible([{'type': 'minimums_over_living', 'shortfall': value}]))

    def test_task_owner_cycle_and_kind_must_match(self):
        result = infeasible([{'type': 'minimums_over_living', 'shortfall': 1}])
        for obj, key, value in ((self.task, 'user_id', uuid4()), (self.task, 'task_type', 'budget_adjust'),
                                (self.cycle, 'pk', uuid4()), (self.task, 'input_hash', '')):
            original = getattr(obj, key)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'invalid_handoff_source'):
                setattr(obj, key, value)
                try:
                    self.build(result)
                finally:
                    setattr(obj, key, original)

    def test_target_is_not_silently_rewritten(self):
        self.snapshot['engine_input']['target'] = 999999
        with self.assertRaisesRegex(ValueError, 'invalid_handoff_budget'):
            self.build(infeasible([{'type': 'minimums_over_living', 'shortfall': 1}]))

    def test_real_calculator_conflicts_without_approved_budget(self):
        cases = (
            ({'FOOD': 480001}, {}, 'minimums_over_living'),
            ({'FOOD': 150001}, {'FOOD': 12345}, 'lock_below_minimum'),
            ({}, {c: 100000 for c in CATEGORY_CODES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')},
             'locks_mismatch_living'),
        )
        for minimums, locks, reason in cases:
            with self.subTest(reason=reason):
                self.snapshot['minimums'] = deepcopy(minimums)
                self.snapshot['locks'] = deepcopy(locks)
                data = {**self.snapshot['engine_input'], 'minimums': minimums, 'locks': locks}
                before = deepcopy(data)
                result = build_candidates(data, dict.fromkeys(CATEGORY_CODES, .1))
                self.assertEqual(result['status'], 'infeasible')
                payload = self.build(result)
                self.assertEqual(payload['issue']['reason'], reason)
                self.assertEqual(payload['constraints']['locks'], locks)
                self.assertEqual(data, before)
                self.assertNotIn('budget_id', payload)

    def test_real_system_floor_conflict(self):
        self.snapshot.update(etc=0, minimums={}, locks={})
        self.snapshot['saved']['living_budget'] = 50000
        self.snapshot['engine_input']['target'] = 50000
        data = {**self.snapshot['engine_input'], 'minimums': {}, 'locks': {}}
        result = build_candidates(data, dict.fromkeys(CATEGORY_CODES, .1), reality_profile={'has_vehicle': False})
        self.assertEqual(result['reason'], 'system_floor_unaffordable')
        issue = self.build(result)['issue']
        self.assertEqual(issue['shortfall'], result['shortfall'])
        self.assertEqual(issue['details']['system_floors'], result['system_floors'])
        self.assertEqual(issue['details']['categories'], ['TRANSPORT_CAR'])

    def test_real_saved_target_conflict(self):
        data = {**self.snapshot['engine_input'], 'minimums': self.snapshot['minimums'],
                'locks': self.snapshot['locks'], 'category_targets': {'FOOD': 100000}}
        result = {'status': 'target_conflict', 'conflicts': target_conflicts(data, {})}
        payload = self.build(result)
        self.assertEqual(payload['issue']['kind'], 'target_conflict')
        self.assertEqual(payload['issue']['conflicts'][0]['reason'], 'minimum_or_floor')

    def test_malformed_result_and_attempts_rejected(self):
        for result in (None, {}, {'status': 'infeasible', 'result': 'not a result'},
                       {'status': 'infeasible', 'conflicts': {}}, {'status': 'infeasible', 'conflicts': [1]}):
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, 'invalid_handoff_result'):
                self.build(result)
        result = {'status': 'needs_upper_allocation_review', 'reason': 'ml_floor_unaffordable',
                  'spare_transfer': True}
        with self.assertRaisesRegex(ValueError, 'invalid_handoff_attempt'):
            self.build(result)
