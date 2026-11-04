"""Stage 3 previews and cumulative constraints, with synthetic inputs only."""
from copy import deepcopy
from datetime import date
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/budget_design'))
from plan_b_adjustments import apply_history_adjustments, preview_history_adjustment
from plan_b_history import CATEGORY_CODES
from test_plan_b_budget import inputs
from test_plan_b_history import fixture, purchase


def months():
    data = fixture()
    for month, row in data.items():
        row['items'] = [purchase(month, amount, category) for category, amount in (
            ('FOOD', 300_000), ('EDUCATION', 200_000), ('MEDICAL_HEALTH', 100_000),
            ('FASHION_SHOPPING', 200_000), ('BEAUTY', 100_000), ('CULTURE_LEISURE', 100_000))]
    return data


def policy():
    # Synthetic experiment rates only; not service defaults or approved thresholds.
    top = ('FOOD', 'EDUCATION', 'MEDICAL_HEALTH')
    return {'total_limit': 100_000, 'increase_rates': dict.fromkeys(top, 20),
            'reduction_rates': dict.fromkeys(CATEGORY_CODES, 0.5),
            'increase_limits': dict.fromkeys(top, 100_000),
            'decrease_limits': dict.fromkeys(CATEGORY_CODES, 50_000),
            'ceilings': dict.fromkeys(top, 1_000_000)}


def request(category, action):
    return {'category': category, 'action': action}


class AdjustmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()

    def setUp(self):
        self.enterContext(patch('django.db.backends.base.base.BaseDatabaseWrapper.connect',
                                side_effect=AssertionError('No DB')))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))

    def run_tools(self, decisions, *, data=None, history=None, limits=None, **options):
        result = apply_history_adjustments(
            inputs() if data is None else data, months() if history is None else history,
            as_of=date(2026, 11, 2), policy=policy() if limits is None else limits,
            decisions=decisions, **options)
        if result['status'] == 'proposed':
            from plan_b_budget import check_result_with_unallocated
            check_result_with_unallocated(result['effective_input'], result['result'])
            self.assertEqual(result['result']['allocations']['TRAVEL_LODGING'], 0)
        return result

    def test_no_requests_keeps_baseline(self):
        out = self.run_tools([])
        self.assertEqual(out['result']['allocations'], out['base_allocations'])
        self.assertEqual(out['transfers'], [])

    def test_increase_reuses_a_donor_order_and_history_rate(self):
        out = self.run_tools([request('FOOD', 'increase')])
        self.assertEqual(out['transfers'], [
            {'from': 'FASHION_SHOPPING', 'to': 'FOOD', 'amount': 50_000},
            {'from': 'BEAUTY', 'to': 'FOOD', 'amount': 10_000}])
        self.assertEqual(out['result']['allocations']['FOOD'], 360_000)

    def test_decrease_meets_explicit_rate_target(self):
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')])
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 199_000)
        self.assertEqual(out['result']['allocations']['FOOD'], 301_000)
        self.assertEqual(out['reduced_categories'], ['FASHION_SHOPPING'])
        self.assertEqual(out['remaining_transfer_limit'], 99_000)

    def test_above_history_reduction_reaches_below_not_just_lower_than_base(self):
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], data=inputs(locks={'MEDICAL_HEALTH': 0}))
        self.assertEqual(out['base_allocations']['FASHION_SHOPPING'], 220_000)
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 199_000)
        self.assertEqual(sum(t['amount'] for t in out['transfers']), 21_000)

    def test_insufficient_cut_rolls_back_whole_action(self):
        limits = policy()
        limits['decrease_limits']['FASHION_SHOPPING'] = 20_000
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(locks={'MEDICAL_HEALTH': 0}), limits=limits)
        self.assertEqual(out['actions'][0]['reason'], 'decrease_limit_or_floor')
        self.assertEqual(out['result']['allocations'], out['base_allocations'])
        self.assertEqual(out['transfers'], [])

    def test_partial_recipient_capacity_is_rolled_back(self):
        limits = policy()
        limits['increase_limits'] = dict.fromkeys(limits['increase_limits'], 5_000)
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(locks={'MEDICAL_HEALTH': 0}), limits=limits)
        self.assertEqual(out['actions'][0]['reason'], 'donor_order_or_receiver_capacity')
        self.assertEqual(out['transfers'], [])
        self.assertEqual(out['result']['allocations'], out['base_allocations'])

    def test_reduction_can_fill_several_recipients_in_rank_order(self):
        limits = policy()
        limits['increase_limits'] = dict.fromkeys(limits['increase_limits'], 10_000)
        limits['increase_limits']['EDUCATION'] = 20_000
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(locks={'MEDICAL_HEALTH': 0}), limits=limits)
        self.assertEqual([(t['to'], t['amount']) for t in out['transfers']],
                         [('FOOD', 10_000), ('EDUCATION', 11_000)])
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 199_000)

    def test_fractional_history_is_compared_without_rounding_average(self):
        history = months()
        history['2026-10']['items'].append(purchase('2026-10', 1, 'FASHION_SHOPPING'))
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], history=history)
        self.assertEqual(out['base_allocations']['FASHION_SHOPPING'], 200_000)
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 199_000)
        self.assertGreater(out['reduction_targets']['FASHION_SHOPPING'], 199_000)
        self.assertLess(out['reduction_targets']['FASHION_SHOPPING'], 199_001)

    def test_total_limit_blocks_reduction_without_partial_moves(self):
        limits = policy()
        limits['total_limit'] = 20_000
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(locks={'MEDICAL_HEALTH': 0}), limits=limits)
        self.assertEqual(out['actions'][0]['reason'], 'total_limit')
        self.assertEqual(out['transfers'], [])

    def test_first_priority_discretionary_is_protected_too(self):
        data = inputs(priorities=[{'category': c, 'rank': i} for i, c in enumerate(
            ('FASHION_SHOPPING', 'FOOD', 'MEDICAL_HEALTH'), 1)])
        limits = policy()
        for key in ('increase_rates', 'increase_limits', 'ceilings'):
            limits[key]['FASHION_SHOPPING'] = limits[key].pop('EDUCATION')
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], data=data, limits=limits)
        self.assertEqual(out['actions'][0]['reason'], 'protected_priority')
        self.assertEqual(out['transfers'], [])

    def test_reduction_cannot_jump_earlier_donor(self):
        out = self.run_tools([request('BEAUTY', 'decrease')])
        self.assertEqual(out['actions'][0]['status'], 'unavailable')
        self.assertEqual(out['transfers'], [])

    def test_reduction_can_use_later_donor_after_earlier_is_protected(self):
        out = self.run_tools([request('BEAUTY', 'decrease')],
                             data=inputs(locks={'FASHION_SHOPPING': 200_000}))
        self.assertEqual(out['transfers'], [{'from': 'BEAUTY', 'to': 'FOOD', 'amount': 1000}])

    def test_lock_and_minimum_prevent_reduction(self):
        for data, reason in ((inputs(locks={'FASHION_SHOPPING': 200_000}), 'locked_category'),
                             (inputs(minimums={'FASHION_SHOPPING': 200_000}), 'minimum_prevents_reduction')):
            with self.subTest(reason=reason):
                out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], data=data)
                self.assertEqual(out['actions'][0]['reason'], reason)
                self.assertEqual(out['transfers'], [])

    def test_non_discretionary_top_priority_protected(self):
        for code in ('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'):
            with self.subTest(code=code):
                out = self.run_tools([request(code, 'decrease')])
                self.assertEqual(out['actions'][0]['reason'], 'protected_priority')

    def test_lower_top_discretionary_is_only_last_resort_and_cannot_be_reincreased(self):
        data = inputs(priorities=[{'category': c, 'rank': i} for i, c in enumerate(
            ('FOOD', 'FASHION_SHOPPING', 'MEDICAL_HEALTH'), 1)])
        limits = policy()
        for key in ('increase_rates', 'increase_limits', 'ceilings'):
            limits[key]['FASHION_SHOPPING'] = limits[key].pop('EDUCATION')
        denied = self.run_tools([request('FASHION_SHOPPING', 'decrease')], data=data, limits=limits)
        self.assertEqual(denied['actions'][0]['status'], 'unavailable')
        limits['decrease_limits'] = dict.fromkeys(CATEGORY_CODES, 0)
        limits['decrease_limits']['FASHION_SHOPPING'] = 50_000
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease'),
                              request('FOOD', 'keep'), request('FASHION_SHOPPING', 'increase')],
                             data=data, limits=limits)
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 199_000)
        self.assertEqual(out['actions'][-1]['reason'], 'reduction_protected')

    def test_index_low_or_missing_does_not_authorize_overspending_reduction(self):
        for savings, reason in ((1_500_000, 'index_below_review'), (None, 'index_unavailable')):
            data = months()
            for row in data.values():
                row['actual_savings'] = savings
            out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], history=data)
            self.assertEqual(out['actions'][0]['reason'], reason)
            self.assertEqual(out['transfers'], [])

    def test_decrease_and_increase_share_rate_and_money_limits(self):
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease'), request('FOOD', 'increase'),
                              request('EDUCATION', 'increase'), request('MEDICAL_HEALTH', 'increase')])
        self.assertEqual(out['result']['allocations']['FOOD'], 360_000)
        self.assertEqual(sum(t['amount'] for t in out['transfers']), 100_000)
        self.assertEqual(out['remaining_transfer_limit'], 0)
        self.assertLess(out['result']['allocations']['FASHION_SHOPPING'], 200_000)
        self.assertEqual(sum(t['amount'] for t in out['transfers'] if t['from'] == 'FASHION_SHOPPING'), 50_000)

    def test_repeated_decrease_does_not_reset_or_spend_limits(self):
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')] * 3)
        self.assertEqual(out['remaining_transfer_limit'], 99_000)
        self.assertEqual(out['actions'][-1]['status'], 'satisfied')

    def test_already_below_average_keeps_current_amount(self):
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], data=inputs(target=900_000))
        self.assertEqual(out['actions'][0]['reason'], 'already_at_reduction_target')
        self.assertEqual(out['transfers'], [])
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 180_000)

    def test_keep_and_increase_follow_priority_and_repeats_are_rejected(self):
        for decisions in ([request('EDUCATION', 'increase')],
                          [request('FOOD', 'increase')] * 2):
            with self.subTest(decisions=decisions), self.assertRaisesRegex(ValueError, 'priority_order_required'):
                self.run_tools(decisions)
        out = self.run_tools([request('FOOD', 'keep'), request('EDUCATION', 'increase')])
        self.assertEqual(out['result']['allocations']['FOOD'], 300_000)
        self.assertEqual(out['result']['allocations']['EDUCATION'], 240_000)

    def test_round_half_up_and_sub_thousand_limits(self):
        limits = policy()
        limits['increase_rates']['FOOD'] = 0.5
        out = self.run_tools([request('FOOD', 'increase')], limits=limits)
        self.assertEqual(out['result']['allocations']['FOOD'], 302_000)
        limits['total_limit'] = 999
        out = self.run_tools([request('FOOD', 'increase')], limits=limits)
        self.assertEqual(out['transfers'], [])

    def test_ceiling_limits_receipt_and_does_not_lower_minimum(self):
        limits = policy()
        limits['ceilings']['FOOD'] = 301_500
        out = self.run_tools([request('FOOD', 'increase')], limits=limits)
        self.assertEqual(out['result']['allocations']['FOOD'], 301_000)
        out = self.run_tools([request('FOOD', 'increase')],
                             data=inputs(minimums={'FOOD': 350_000}), limits=limits)
        self.assertEqual(out['result']['allocations']['FOOD'], 350_000)

    def test_zero_history_category_and_travel_cannot_be_reduced(self):
        for code, reason in (('HOUSEHOLD', 'minimum_prevents_reduction'),
                             ('TRAVEL_LODGING', 'inactive_category')):
            out = self.run_tools([request(code, 'decrease')])
            self.assertEqual(out['actions'][0]['reason'], reason)
            self.assertEqual(out['transfers'], [])

    def test_invalid_money_in_request_and_invalid_policy_rejected(self):
        for decision in ({'category': 'FOOD', 'action': 'increase', 'amount': 1000},
                         {'category': 'FOOD', 'action': 'anything'},
                         {'category': 'ETC', 'action': 'decrease'}):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                self.run_tools([decision])
        for limits in ({}, {**policy(), 'total_limit': True}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.run_tools([], limits=limits)

    def test_unready_and_infeasible_baselines_are_not_adjusted(self):
        incomplete = months()
        del incomplete['2026-09']
        for kwargs in ({'history': incomplete}, {'data': inputs(minimums={'FOOD': 1_000_001})}):
            out = self.run_tools([request('FOOD', 'increase')], **kwargs)
            self.assertEqual(out['adjustment_status'], 'unavailable')
            self.assertEqual(out['transfers'], [])

    def test_surplus_does_not_move_the_reserved_free_money(self):
        out = self.run_tools([request('FOOD', 'increase')], data=inputs(target=1_100_000))
        self.assertEqual(out['adjustment_status'], 'surplus_retained')
        self.assertEqual(out['transfers'], [])
        self.assertGreater(out['unallocated'], 0)

    def test_preview_matches_replay_and_preserves_inputs(self):
        data, history, limits = inputs(), months(), policy()
        decisions = [request('FASHION_SHOPPING', 'decrease')]
        next_request = request('FOOD', 'increase')
        before = deepcopy((data, history, limits, decisions, next_request))
        preview = preview_history_adjustment(data, history, as_of=date(2026, 11, 2),
            policy=limits, decisions=decisions, request=next_request)
        actual = self.run_tools(decisions + [next_request], data=data, history=history, limits=limits)
        self.assertEqual(preview, actual)
        self.assertEqual((data, history, limits, decisions, next_request), before)

    def test_failed_action_preserves_previous_success(self):
        first = request('FASHION_SHOPPING', 'decrease')
        before = self.run_tools([first])
        out = self.run_tools([first, request('FOOD', 'decrease')])
        self.assertEqual(out['actions'][-1]['status'], 'unavailable')
        self.assertEqual(out['result']['allocations'], before['result']['allocations'])
        self.assertEqual(out['transfers'], before['transfers'])

    def test_scaled_down_protected_food_does_not_block_later_increase(self):
        out = self.run_tools([request('FOOD', 'decrease'), request('FOOD', 'increase')],
                             data=inputs(target=900_000))
        self.assertEqual(out['actions'][0]['reason'], 'protected_priority')
        self.assertNotIn('FOOD', out['reduced_categories'])
        self.assertEqual(out['actions'][1]['status'], 'applied')
        self.assertGreater(out['result']['allocations']['FOOD'], out['base_allocations']['FOOD'])

    def test_locked_below_history_is_not_registered_as_reduced(self):
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(locks={'FASHION_SHOPPING': 100_000}))
        self.assertEqual(out['actions'][0]['reason'], 'locked_category')
        self.assertEqual(out['reduced_categories'], [])

    def test_reduction_rate_changes_target_without_hidden_default(self):
        for rate, expected in ((5, 190_000), (10, 180_000), (20, 160_000)):
            limits = policy()
            limits['reduction_rates']['FASHION_SHOPPING'] = rate
            out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], limits=limits)
            self.assertEqual(out['reduction_targets']['FASHION_SHOPPING'], expected)
            self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], expected)

    def test_below_average_but_above_rate_target_still_requires_reduction(self):
        limits = policy()
        limits['reduction_rates']['FASHION_SHOPPING'] = 20
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(target=900_000), limits=limits)
        self.assertEqual(out['base_allocations']['FASHION_SHOPPING'], 180_000)
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 160_000)
        self.assertEqual(sum(t['amount'] for t in out['transfers']), 20_000)

    def test_missing_or_invalid_reduction_rates_are_rejected(self):
        for rates in (None, {}, {'FASHION_SHOPPING': True}, {'FASHION_SHOPPING': -1},
                      {'FASHION_SHOPPING': 0}, {'FASHION_SHOPPING': 101},
                      {'FASHION_SHOPPING': float('nan')}, {'FASHION_SHOPPING': float('inf')},
                      {'UNKNOWN': 10}):
            with self.subTest(rates=rates), self.assertRaisesRegex(ValueError, 'reduction_rates'):
                limits = policy()
                if rates is None:
                    limits.pop('reduction_rates')
                else:
                    limits['reduction_rates'] = rates
                self.run_tools([request('FASHION_SHOPPING', 'decrease')], limits=limits)

    def test_rate_target_below_minimum_is_not_accepted(self):
        limits = policy()
        limits['reduction_rates']['FASHION_SHOPPING'] = 20
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(minimums={'FASHION_SHOPPING': 170_000}), limits=limits)
        self.assertEqual(out['actions'][0]['reason'], 'minimum_prevents_reduction')
        self.assertEqual(out['transfers'], [])

    def test_reason_is_carried_through_preview_without_blanket_protection(self):
        data = inputs(minimums={'FASHION_SHOPPING': 150_000},
                      minimum_reasons={'FASHION_SHOPPING': '패션 마케팅 업무용 의류'})
        before = deepcopy(data)
        decisions = [request('FOOD', 'increase')]
        out = self.run_tools(decisions, data=data)
        without_reason = self.run_tools(decisions, data=inputs(minimums=data['minimums']))
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 150_000)
        self.assertEqual(out['result'], without_reason['result'])
        self.assertEqual(out['minimum_reasons'], data['minimum_reasons'])
        preview = preview_history_adjustment(data, months(), as_of=date(2026, 11, 2),
            policy=policy(), decisions=decisions, request=request('EDUCATION', 'keep'))
        self.assertEqual(preview['minimum_reasons'], data['minimum_reasons'])
        self.assertNotIn('minimum_reasons', preview['effective_input'])
        self.assertEqual(data, before)

    def test_reason_does_not_authorize_cutting_below_minimum(self):
        data = inputs(minimums={'FASHION_SHOPPING': 200_000},
                      minimum_reasons={'FASHION_SHOPPING': '업무 비용'})
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')], data=data)
        self.assertEqual(out['actions'][0]['reason'], 'minimum_prevents_reduction')
        self.assertEqual(out['result']['allocations']['FASHION_SHOPPING'], 200_000)

    def test_tool_cannot_replace_user_minimums_or_priorities(self):
        data = inputs(minimums={'FOOD': 300_000})
        original = deepcopy(data)
        for extra in ({'minimums': {'FOOD': 100_000}}, {'priorities': []},
                      {'rank': 9}, {'allocations': {'FOOD': 100_000}}):
            with self.subTest(field=next(iter(extra))), self.assertRaisesRegex(
                    ValueError, '^invalid_adjustment_decision$'):
                self.run_tools([{**request('FOOD', 'keep'), **extra}], data=data)
            self.assertEqual(data, original)

    def test_final_check_rejects_result_below_user_minimum_even_if_total_matches(self):
        data = inputs(minimums={'FOOD': 300_000})
        base = self.run_tools([], data=data)['result']['allocations']
        invalid = {**base, 'FOOD': base['FOOD'] - 1, 'EDUCATION': base['EDUCATION'] + 1}
        self.assertEqual(sum(invalid.values()), data['target'])
        with patch('budgets.design_candidates._adjust', return_value=(invalid, [], {}, {})), \
                self.assertRaises(ValueError):
            self.run_tools([request('FOOD', 'increase')], data=data)

    def test_shortfall_without_savings_stays_blocked_after_user_deferral(self):
        history = months()
        for row in history.values():
            row.pop('actual_savings')
        out = self.run_tools([request('FASHION_SHOPPING', 'decrease')],
                             data=inputs(target=900_000), history=history)
        self.assertEqual(out['gap_type'], 'shortfall')
        self.assertEqual(out['scope_state'], 'aligned')
        self.assertEqual(out['actions'][0]['reason'], 'index_unavailable')
        self.assertEqual(out['transfers'], [])

    def test_preview_and_apply_forward_classification_gate_and_etc_reservation(self):
        data, history = inputs(target=600_000), months()
        for month, row in history.items():
            row['items'].append(purchase(month, 200_000, 'UNCLASSIFIED'))
        action = request('FOOD', 'keep')
        options = {'other_reserved': 50_000, 'unclassified_share_limit': 0.2}
        out = self.run_tools([action], data=data, history=history, **options)
        preview = preview_history_adjustment(data, history, as_of=date(2026, 11, 2),
            policy=policy(), decisions=[], request=action, **options)
        self.assertEqual(out, preview)
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['etc_comparison']['reserved_amount'], 50_000)
        blocked = self.run_tools([action], data=data, history=history, unclassified_share_limit=0.1)
        self.assertEqual(blocked['status'], 'needs_data')
        self.assertEqual(blocked['actions'], [])


if __name__ == '__main__':
    unittest.main()
