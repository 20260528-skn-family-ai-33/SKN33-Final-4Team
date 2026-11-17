"""Synthetic DB tests for consent boundaries, revisions and final approval replay."""
from copy import deepcopy
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.db import close_old_connections, connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from chat import test_budget_adjustment as api_fixtures
from chat.models import Conversation
from users.models import Consent
from .ai_consent import consent_state, has_consent
from .models import Budget, BudgetCategorySetting, BudgetCycle, BudgetItem
from .adjustment_core.financial_planner import synthetic_diagnostics
from .adjustment_core.calculation import proportional_group_reduction
from .test_adjustment_core import item, reading
from .test_adjustment_core import context, envelope
from .test_financial_planner import draft, funding, review


class FinancialReplyFormattingTest(unittest.TestCase):
    def test_live_feedback_schema_requires_every_property(self):
        from chat.llm.schemas import schema_for
        from .adjustment_core.financial_conversation import FEEDBACK_PROMPT
        name, schema = schema_for(FEEDBACK_PROMPT)
        self.assertEqual(name, 'budget_adjust_feedback')
        self.assertEqual(set(schema['required']), set(schema['properties']))
        self.assertIn('inspection', schema['required'])

    def setUp(self):
        from .test_adjustment_core import Store
        self.store, self.ctx = Store(), context()
        self.ctx.update(today='2026-11-17', spending_status='partial', budget_preview=True,
                        observed_spending={'transaction_count': 4, 'through': '2026-11-15'})

    def test_unknown_amount_keeps_capacity_rows_and_observed_basis_separate(self):
        from .adjustment_core.financial_conversation import unknown_amount_reply
        from .adjustment_core.responses import guidance
        state = {'requests': [item('FOOD', None)], 'excluded': ['SAVINGS']}
        before = deepcopy(self.ctx)
        with patch('budgets.adjustment_core.advisor.suggested_amount', return_value=0):
            result = unknown_amount_reply(self.store, 'readable', state, self.ctx, self.ctx)
        lines = result['reply']['lines']
        self.assertIn('충분해 보여요', lines[0])
        self.assertTrue(any(line.startswith('다른 생활비 · 최대 ') for line in lines))
        self.assertTrue(any(line.startswith('· ') for line in lines))
        self.assertTrue(any(line.startswith('여윳돈 · 최대 ') for line in lines))
        self.assertNotIn('저축', '\n'.join(lines))
        self.assertIn('2026-11-15', lines[-1])
        self.assertIn('참고 금액', lines[-1])
        self.assertIn('\n', guidance(result, state)['message'])
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertIsNone(self.store.state['requests'][0]['amount'])
        self.assertEqual(self.ctx, before)

    def test_maxima_keeps_unknown_spare_distinct_from_zero(self):
        from .adjustment_core.financial_conversation import maxima
        self.ctx['bucket_status']['SPARE'] = 'unknown'
        self.ctx['bucket_available'].pop('SPARE')
        result = maxima(self.ctx, envelope(item('FOOD', 50000)))
        lines = result['reply']['lines']
        self.assertTrue(any('여윳돈' in line and '확인 필요' in line for line in lines))
        self.assertFalse(any('여윳돈' in line and ('0원' in line or '보충' in line) for line in lines))
        self.assertTrue(any(line.startswith('· ') for line in lines))
        self.assertIn('2026-11-15', lines[-1])

    def test_capacity_summary_and_source_details_keep_the_same_protected_limits(self):
        from .adjustment_core.financial_conversation import maxima
        request = {**envelope(item('FOOD', 50000), excluded=['SAVINGS']), 'spare_minimum': 350000}
        refinement = {'donors': {'HOUSEHOLD': {'target': 40000}, 'MEDICAL_HEALTH': {'target': 30000}}}
        before = deepcopy((self.ctx, request, refinement))
        summary = maxima(self.ctx, request, refinement, inspection='capacity')['reply']['lines']
        details = maxima(self.ctx, request, refinement, inspection='sources')['reply']['lines']
        self.assertEqual(summary[0], '다른 생활비에서 식비에 최대 13만 원을 더할 수 있어요.')
        self.assertFalse(any(line.startswith('· ') for line in summary))
        self.assertIn('다른 생활비 · 최대 13만 원', details)
        self.assertIn('· 생활용품 1만 원', details)
        self.assertIn('· 의료·건강 2만 원', details)
        self.assertIn('여윳돈 · 최대 5만 원', details)
        self.assertIn('월 머니쿠션 · 최대 10만 원', details)
        for lines in (summary, details):
            self.assertNotIn('저축', '\n'.join(lines))
            self.assertTrue(any('2026-11-15' in line for line in lines))
            self.assertIn('직접 정한 감액 제한과 남길 금액을 반영했어요.', lines)
        self.assertEqual((self.ctx, request, refinement), before)

    def test_decrease_capacity_uses_the_original_direction_and_distinguishes_unknown(self):
        from .adjustment_core.financial_conversation import maxima
        self.ctx['minimums']['HOUSEHOLD'] = 20000
        request = envelope(item('HOUSEHOLD', 10000, 'down'), destinations=['SAVINGS'])
        before = deepcopy((self.ctx, request))
        result = maxima(self.ctx, request, inspection='capacity')
        self.assertEqual(result['reply']['lines'][0], '생활용품에서 최대 3만 원을 줄일 수 있어요.')
        self.assertEqual((self.ctx, request), before)
        for missing in ('transactions', 'destination'):
            with self.subTest(missing=missing):
                ctx, inquiry = deepcopy(self.ctx), deepcopy(request)
                if missing == 'transactions':
                    ctx.update(budget_preview=False, observed_spending={})
                else:
                    inquiry['destinations'] = []
                result = maxima(ctx, inquiry, inspection='capacity')
                self.assertEqual(result['status'], 'awaiting_input')
                self.assertNotIn('최대 0원', '\n'.join(result['reply']['lines']))
                self.assertNotIn('더할 수 있어요', '\n'.join(result['reply']['lines']))

    def test_total_living_capacity_uses_external_sources_without_inventing_a_destination(self):
        from .adjustment_core.financial_conversation import maxima
        request = {**envelope(item('LIVING', 50000), sources=['SPARE']), 'spare_minimum': 350000}
        before = deepcopy((self.ctx, request))
        result = maxima(self.ctx, request, inspection='sources')
        self.assertIn('여윳돈 · 최대 5만 원', result['reply']['lines'])
        self.assertNotIn('어느 생활비 항목', '\n'.join(result['reply']['lines']))
        self.assertEqual((self.ctx, request), before)
        request['sources'] = ['HOUSEHOLD']
        result = maxima(self.ctx, request, inspection='sources')
        self.assertEqual(result['reply']['lines'][0], '생활비 항목끼리 옮기면 생활비 전체 금액은 늘어나지 않아요.')
        self.assertNotIn('확인 필요', '\n'.join(result['reply']['lines']))

    def test_feedback_capacity_preserves_source_restrictions_and_original_decrease(self):
        from .adjustment_core.financial_conversation import feedback
        cases = [
            ({'requests': [item('FOOD', 50000)], 'sources': ['SPARE']}, '여윳돈 · 최대 5만 원'),
            ({'requests': [item('FOOD', 50000)], 'sources': [], 'permitted_sources': ['SPARE']}, '여윳돈 · 최대 5만 원'),
            ({'requests': [item('HOUSEHOLD', 10000, 'down')], 'destinations': ['SAVINGS'],
              'primary_request': envelope(item('SAVINGS', 10000))}, '생활용품에서 최대 5만 원을 줄일 수 있어요.'),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                state = {**fields, 'spare_minimum': 350000, 'proposal': {'task_ref': 'preview',
                    'proposal_id': 'preview', 'changes': [], 'allocations': deepcopy(self.ctx['allocations']),
                    'buckets': deepcopy(self.ctx['buckets'])}}
                before = deepcopy(state)
                ports = {'get_context': lambda: deepcopy(self.ctx), 'store': self.store, 'propose': Mock(),
                    'task_status': Mock(return_value={'state': 'awaiting_approval'}),
                    'complete': Mock(return_value=json.dumps({'action': 'maxima', 'category': None,
                        'amount': None, 'amount_mode': None, 'suggested_target': None, 'inspection': 'sources'}))}
                result = feedback({'conversation_id': 'inspect', 'text': '얼마까지 옮길 수 있어?'}, state, ports)
                self.assertIn(expected, result['reply']['lines'])
                if fields['requests'][0]['direction'] == 'up':
                    self.assertNotIn('다른 생활비', '\n'.join(result['reply']['lines']))
                    self.assertNotIn('저축', '\n'.join(result['reply']['lines']))
                self.assertEqual(state, before)
                ports['propose'].assert_not_called()

    def test_feedback_inspection_preserves_preview_and_accepts_optional_legacy_fields(self):
        from .adjustment_core.financial_conversation import feedback
        primary = {**envelope(item('FOOD', 50000), excluded=['SAVINGS']), 'spare_minimum': 350000}
        preview = {'task_ref': 'preview', 'proposal_id': 'preview', 'changes': [],
                   'allocations': deepcopy(self.ctx['allocations']), 'buckets': deepcopy(self.ctx['buckets'])}
        state = {'requests': deepcopy(primary['items']), 'primary_request': primary,
                 'proposal': preview, 'spare_minimum': 350000, 'excluded': ['SAVINGS'],
                 'financial_overrides': {'planned_spare': {'status': 'user_confirmed',
                     'scope': 'unbudgeted_only', 'amount': 300000}}}
        before = deepcopy((self.ctx, state))
        for inspection in ('capacity', 'sources', None):
            for has_destination in (False, True):
                with self.subTest(inspection=inspection, destination=has_destination):
                    interpreted = {'action': 'maxima', 'category': None, 'amount': None,
                                   'amount_mode': None, 'suggested_target': None}
                    if inspection is not None:
                        interpreted['inspection'] = inspection
                    if has_destination:
                        interpreted['destination'] = None
                    ports = {'get_context': lambda: deepcopy(self.ctx), 'store': self.store,
                             'complete': Mock(return_value=json.dumps(interpreted)), 'propose': Mock(),
                             'task_status': Mock(return_value={'state': 'awaiting_approval'})}
                    result = feedback({'conversation_id': 'inspect', 'text': '가능한 범위가 궁금해'}, state, ports)
                    self.assertEqual(result['status'], 'replied', result)
                    lines = result['reply']['lines']
                    if inspection == 'capacity':
                        self.assertTrue(lines[0].startswith('다른 생활비에서 식비에 최대 '))
                        self.assertFalse(any(line.startswith('· ') for line in lines))
                    else:
                        self.assertIn('여윳돈 · 최대 5만 원', lines)
                        self.assertTrue(any(line.startswith('· ') for line in lines))
                    self.assertNotIn('저축', '\n'.join(lines))
                    ports['propose'].assert_not_called()
                    self.assertEqual((self.ctx, state), before)

    def test_inspection_during_diagnostic_questions_answers_without_losing_the_request(self):
        from .adjustment_core.financial_conversation import _inquiry
        for kind in ('financial_data', 'one_off_review'):
            for inspection in ('capacity', 'sources'):
                with self.subTest(pending=kind, inspection=inspection):
                    self.store.state = {'requests': [item('FOOD', 50000)], 'excluded': ['SAVINGS'],
                        'spare_minimum': 350000, 'pending': {'kind': kind, 'field': 'planned',
                            'line': '예정 지출을 확인해 주세요.', 'choices': []}}
                    before = deepcopy(self.store.state)
                    ports = {'get_context': lambda: deepcopy(self.ctx), 'store': self.store,
                        'allocator': proportional_group_reduction, 'simulate': Mock(), 'propose': Mock(),
                        'task_status': Mock(), 'cancel': Mock(),
                        'complete': Mock(return_value=json.dumps(reading(action='inspect', inspection=inspection)))}
                    result = _inquiry({'conversation_id': 'inspect', 'text': '얼마나 가능한지 먼저 알려줘'},
                                      self.store.state, ports)
                    self.assertIsNotNone(result)
                    self.assertEqual(result['status'], 'replied', result)
                    lines = result['reply']['lines']
                    if inspection == 'capacity':
                        self.assertTrue(lines[0].startswith('다른 생활비에서 식비에 최대 '))
                    else:
                        self.assertIn('여윳돈 · 최대 5만 원', lines)
                    for field in ('requests', 'excluded', 'spare_minimum'):
                        self.assertEqual(self.store.state[field], before[field])
                    ports['simulate'].assert_not_called()
                    ports['propose'].assert_not_called()
                    ports['cancel'].assert_not_called()

    def test_inspection_during_plan_selection_keeps_existing_candidates(self):
        from .adjustment_core.financial_conversation import _handle
        for inspection in ('capacity', 'sources'):
            with self.subTest(inspection=inspection):
                self.store.state = {'requests': [item('FOOD', 50000)], 'excluded': ['SAVINGS'],
                    'spare_minimum': 350000, 'pending': {'kind': 'financial_selection',
                        'line': '어떤 안으로 조정할까요?', 'choices': []},
                    'financial_offer': {'answer': {'plans': []}, 'comparison': {'amount': 50000}}}
                before = deepcopy(self.store.state)
                ports = {'get_context': lambda: deepcopy(self.ctx), 'store': self.store,
                    'allocator': proportional_group_reduction, 'simulate': Mock(), 'propose': Mock(),
                    'task_status': Mock(), 'cancel': Mock(),
                    'complete': Mock(return_value=json.dumps(reading(action='inspect', inspection=inspection)))}
                result = _handle({'conversation_id': 'inspect', 'text': '가능한 금액부터 알려줘'}, **ports)
                self.assertEqual(result['status'], 'replied', result)
                if inspection == 'capacity':
                    self.assertTrue(result['reply']['lines'][0].startswith('다른 생활비에서 식비에 최대 '))
                else:
                    self.assertIn('여윳돈 · 최대 5만 원', result['reply']['lines'])
                for field in ('requests', 'excluded', 'spare_minimum', 'financial_offer'):
                    self.assertEqual(self.store.state[field], before[field])
                ports['propose'].assert_not_called()
                ports['cancel'].assert_not_called()

    def test_unknown_amount_preserves_a_named_category_source(self):
        from .adjustment_core.financial_conversation import unknown_amount_reply
        state = {'requests': [item('FOOD', None)], 'sources': ['MEDICAL_HEALTH']}
        with patch('budgets.adjustment_core.advisor.suggested_amount', return_value=0):
            result = unknown_amount_reply(self.store, 'readable', state, self.ctx, self.ctx)
        limits = [line for line in result['reply']['lines'] if ' · 최대 ' in line]
        self.assertEqual(limits, ['의료·건강 · 최대 5만 원'])
        self.assertEqual(self.store.state['pending']['capacities'], {'MEDICAL_HEALTH': 50000})
        self.assertEqual(self.store.state['sources'], ['MEDICAL_HEALTH'])

    def test_comparison_keeps_observed_forecast_basis_in_budget_preview(self):
        from .test_financial_planner import financial_context
        from .adjustment_core import financial_conversation as conversation
        from .adjustment_core.financial_planner import recommend
        ctx = financial_context()
        ctx.update(today='2026-11-12', spending_status='partial', budget_preview=True,
                   observed_spending={'transaction_count': 4, 'through': '2026-11-09'})
        history = ctx['diagnostic_inputs']['history']
        history.update(status='observed', dates=history['dates'][-2:], samples=history['samples'][-2:],
                       candidates=[], complete_dates=[], observation={'through': '2026-11-09', 'limited': True})
        request = envelope(item('FOOD', 50000))
        answer = recommend(ctx, request, Mock(side_effect=RuntimeError('offline')))
        self.assertEqual(answer['diagnosis']['basis'], 'forecast')
        self.assertTrue(answer['plans'])
        state = {'requests': deepcopy(request['items'])}
        with patch.object(conversation, '_increase_only', return_value=False), \
                patch.object(conversation, 'recommend', return_value=answer):
            result = conversation.comparison_reply(self.store, 'readable', state, ctx, ctx, Mock())
        notice = result['reply']['comparison']['notice']
        self.assertIn('2026-11-09', notice)
        self.assertIn('참고 금액', notice)
        self.assertNotIn('예산 기준으로만', notice)
        self.assertEqual(answer['diagnosis']['basis'], 'forecast')
        self.assertEqual(self.store.state['financial_offer']['comparison']['notice'], notice)


class ComparisonNavigationTest(unittest.TestCase):
    def setUp(self):
        from .test_adjustment_core import Store
        from .adjustment_core.financial_conversation import comparison_reply
        from .adjustment_core.funding import simulate_funding
        self.ctx, self.store = context(), Store()
        self.complete = Mock()
        self.simulate = lambda ctx, request: simulate_funding(ctx, request, allocate_group_reduction=proportional_group_reduction)
        self.propose = Mock(side_effect=lambda ctx, result, **options:
            {**result, **options, 'task_ref': 'preview', 'proposal_id': 'preview', 'state': 'awaiting_approval'})
        self.ports = dict(store=self.store, complete=self.complete, get_context=lambda: deepcopy(self.ctx),
                          simulate=self.simulate, propose=self.propose, allocator=proportional_group_reduction,
                          task_status=Mock(return_value={'state': 'awaiting_approval'}), cancel=Mock())
        state = {'requests': [item('FOOD', 100000, 'down')], 'destinations': ['SPARE']}
        comparison_reply(self.store, 'test', state, self.ctx, self.ctx, self.complete)
        self.plan = next(p for p in self.store.state['comparison']['comparison']['plans'] if p['status'] == 'ready')

    def add_plan(self, *, status='ready', duplicate=False):
        alternative = deepcopy(self.plan)
        alternative.update(id='living', label='다른 생활비', status=status)
        if not duplicate:
            alternative['envelope'] = envelope(item('FOOD', 100000, 'down'), destinations=['MEDICAL_HEALTH'])
            alternative['result'] = self.simulate(self.ctx, alternative['envelope'])
        self.store.state['comparison']['comparison']['plans'].append(alternative)

    def select(self):
        from .adjustment_core.financial_conversation import handle
        return handle({'conversation_id': 'test', 'text': 'plan_' + self.plan['id']}, **self.ports)

    def test_single_verified_plan_has_no_other_methods_navigation(self):
        result = self.select()
        self.assertIn('proposal', result['reply'])
        self.assertNotIn('compare', {c['value'] for c in result['reply']['choices']})
        self.assertFalse(self.store.state['plan_alternatives']['available'])

    def test_duplicate_and_unverified_plans_are_not_alternatives(self):
        self.add_plan(duplicate=True)
        self.add_plan(status='needs_data')
        result = self.select()
        self.assertFalse(self.store.state['plan_alternatives']['available'])
        self.assertNotIn('compare', {c['value'] for c in result['reply']['choices']})

    def test_distinct_verified_result_keeps_other_methods_navigation(self):
        self.add_plan()
        result = self.select()
        self.assertTrue(self.store.state['plan_alternatives']['available'])
        self.assertIn('compare', {c['value'] for c in result['reply']['choices']})

    def test_stale_compare_button_and_natural_request_explain_without_replacing_preview(self):
        from .adjustment_core.financial_conversation import handle
        preview = self.select()['reply']['proposal']
        original_requests = deepcopy(self.store.state['requests'])
        for text in ('다른 방법 보기', '다른 선택지도 보여줄래?', '또 다른 방법은 없어?'):
            with self.subTest(text=text):
                self.complete.side_effect = [json.dumps({'action': 'unknown', 'category': None, 'destination': None,
                    'amount': None, 'amount_mode': None, 'suggested_target': None}), json.dumps(reading(action='compare'))]
                result = handle({'conversation_id': 'test', 'text': text}, **self.ports)
                self.assertIn('확인된 다른 조정안은 없어요', result['reply']['lines'][0])
                self.assertEqual(result['reply']['proposal'], preview)
                self.assertNotIn('compare', {c['value'] for c in result['reply']['choices']})
                self.assertEqual(self.store.state['requests'], original_requests)
                self.propose.assert_called_once()
                self.ports['cancel'].assert_not_called()

    def test_financial_offer_alternatives_compare_verified_amounts(self):
        from .adjustment_core.financial_conversation import _has_other_plans
        self.add_plan(duplicate=True)
        self.add_plan(status='needs_data')
        state = {'proposal': self.plan['result'], 'financial_offer': self.store.state['comparison']}
        self.assertFalse(_has_other_plans(state, self.ctx))
        self.add_plan()
        self.assertTrue(_has_other_plans(state, self.ctx))

    def test_financial_example_selection_asks_amount_without_creating_proposal(self):
        from .adjustment_core.financial_conversation import selected_preview
        state = {'requests': [item('FOOD', None, 'up', 'by')], 'excluded': ['SAVINGS'],
                 'spare_minimum': 200000, 'clear_targets': ['FOOD'],
                 'financial_offer': {'context': deepcopy(self.ctx),
                     'comparison': {'amount_was_given': False, 'basis_kind': 'budget_example'},
                     'answer': {'plans': [{'funding': [{'source': 'SPARE', 'amount': 100000}]}]}}}
        response = selected_preview(self.store, 'test', state, 0, self.ports)
        self.assertEqual(response['status'], 'awaiting_input')
        self.assertEqual(self.store.state['pending']['kind'], 'amount')
        self.assertIsNone(self.store.state['requests'][0]['amount'])
        self.assertEqual(self.store.state['sources'], ['SPARE'])
        self.assertEqual(self.store.state['excluded'], ['SAVINGS'])
        self.assertEqual(self.store.state['spare_minimum'], 200000)
        self.assertEqual(self.store.state['clear_targets'], ['FOOD'])
        self.assertNotIn('proposal', response['reply'])
        self.propose.assert_not_called()

    def test_changed_preview_or_context_invalidates_previous_alternatives(self):
        from .adjustment_core.financial_conversation import _has_other_plans
        self.add_plan()
        self.select()
        self.assertTrue(_has_other_plans(self.store.state, self.ctx))
        for change in ('context', 'preview', 'conditions'):
            with self.subTest(change=change):
                state, ctx = deepcopy(self.store.state), deepcopy(self.ctx)
                if change == 'context':
                    ctx['spent']['FOOD'] += 10000
                elif change == 'preview':
                    state['proposal']['allocations']['FOOD'] += 10000
                    state['proposal']['buckets']['SPARE'] -= 10000
                else:
                    from .adjustment_core.conversation import _merge
                    _merge(state, reading(excluded=['MEDICAL_HEALTH']), {})
                self.assertFalse(_has_other_plans(state, ctx))


@override_settings(CHAT_ENGINE="router", CHAT_LLM="fake", CHAT_TODAY="2026-11-10",
                   BUDGET_ADJUST_ALLOCATOR=proportional_group_reduction)
class FinancialAdjustmentApiTest(TestCase):
    post, say = api_fixtures.BudgetAdjustmentApiTest.post, api_fixtures.BudgetAdjustmentApiTest.say

    def test_router_to_zero_budget_reduction_explains_and_offers_other_categories(self):
        self.ctx['allocations']['EDUCATION'] = 0
        self.ctx['buckets']['SPARE'] += 50000
        response = self.chat(text='교육비를 5만 원 줄여줘', answers=[{'intents': ['budget_adjust']},
            reading(requests=[item('EDUCATION', 50000, 'down')], action='compare')])
        content = response['messages'][-1]['content']
        self.assertIn('교육의 현재 예산은 0원', content['text'])
        self.assertEqual(content['budgetAdjustment']['phase'], 'category')
        self.assertNotIn('방법별 비교를 할 수', content['text'])
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        choices = {row['value'] for row in content['choices']}
        self.assertIn('FOOD', choices)
        self.assertNotIn('EDUCATION', choices)
        corrected = self.chat(response['conversationId'], choice={'value': 'FOOD', 'turn': content['turn']})
        correction = corrected['messages'][-1]['content']['budgetAdjustment']
        self.assertIn('comparison', correction)
        self.assertTrue(any(p['status'] == 'ready' for p in correction['comparison']['plans']))
        self.assertEqual(Budget.objects.count(), 1)

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in zero-budget natural language routing')
    def test_live_router_zero_budget_reduction_explains_actual_reason(self):
        from chat.llm.live import LiveReading
        self.ctx['allocations']['EDUCATION'] = 0
        self.ctx['buckets']['SPARE'] += 50000
        with patch('chat.engine.reading', return_value=LiveReading(6, None)):
            response = self.say(text='교육비를 5만 원 줄여줘')
        content = self._assert_live_budget_content(response)
        self.assertIn('교육의 현재 예산은 0원', content['text'])
        self.assertEqual(content['budgetAdjustment']['phase'], 'category')
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        self.assertTrue(content['choices'])
        self.assertEqual(Budget.objects.count(), 1)

    def plan_choice(self, content, plan_id):
        value = next(c['value'] for c in content['choices']
                     if c['value'] in {'plan_' + plan_id, 'financial_' + plan_id})
        return {'value': value, 'turn': content['turn']}

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in real LLM routing and budget interpretation')
    def test_declarative_next_cycle_expense_routes_and_keeps_amount_without_saving(self):
        from chat.llm.live import LiveReading
        from chat.engine import ConversationStore, KEY
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        with patch('chat.engine.reading', return_value=LiveReading(12, None)):
            response = self.say(text='다음달부터 교육비 50만원 추가될 예정')
        content = response['messages'][-1]['content']
        self.assertIn('budgetAdjustment', content, content)
        cid = response['conversationId']
        state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(state['requests'], [item('EDUCATION', 500000)])
        self.assertEqual(state['scope'], 'next_cycle')
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)

    def _assert_live_budget_content(self, response):
        content = response['messages'][-1]['content']
        self.assertIn('budgetAdjustment', content, content)
        adjustment = content['budgetAdjustment']
        self.assertNotIn(adjustment.get('status'), ('failed', 'not_mine'), content)
        self.assertIsNone(adjustment.get('error'), content)
        self.assertTrue({'kind', 'context', 'message', 'reason', 'next', 'details'} <= set(adjustment['guidance']), content)
        return content

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in previous user messages through actual LLM and API')
    def test_live_previous_initial_requests_keep_category_amount_and_scope(self):
        from chat.llm.live import LiveReading
        from chat.engine import ConversationStore, KEY
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        cases = [
            ('의료비 줄이고싶어', 'MEDICAL_HEALTH', 'down', None, None),
            ('나 교육에 돈 필요 없는데', 'EDUCATION', 'down', None, None),
            ('생활비가 부족해', 'LIVING', 'up', None, None),
            ('생활비 늘려줘', 'LIVING', 'up', None, None),
            ('생활비를 5만원 늘리고 싶어요', 'LIVING', 'up', 50000, 'by'),
            ('교통비 18만원으로 늘려줘', 'TRANSPORT_CAR', 'up', 180000, 'to'),
            ('저축 30만원 늘려줘', 'SAVINGS', 'up', 300000, 'by'),
            ('다음달부터 교육비 50만원 추가될 예정', 'EDUCATION', 'up', 500000, 'by'),
        ]
        for text, code, direction, amount, mode in cases:
            with self.subTest(text=text), patch('chat.engine.reading', return_value=LiveReading(12, None)):
                response = self.say(text=text)
                self._assert_live_budget_content(response)
                state = ConversationStore.load(Conversation.objects.get(pk=response['conversationId']).slots).get_agent_state(KEY, 'budget')
                request = next(row for row in state['requests'] if row['category'] == code)
                if mode == 'to':
                    self.assertIn(request['direction'], (None, direction))
                else:
                    self.assertEqual(request['direction'], direction)
                if amount is not None:
                    self.assertEqual((request['amount'], request['amount_mode']), (amount, mode))
                if text.startswith('다음달'):
                    self.assertEqual(state['scope'], 'next_cycle')
                self.assertEqual(Budget.objects.filter(status='approved').count(), 1)

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in contextual previous user dialogue')
    def test_live_previous_short_replies_advance_and_keep_entered_amount(self):
        from chat.llm.live import LiveReading
        from chat.engine import ConversationStore, KEY
        with patch('chat.engine.reading', return_value=LiveReading(25, None)):
            response = self.say(text='생활비 늘려줘')
            cid = response['conversationId']
            replies = [response['messages'][-1]['content']]
            for text in ['조정해', '교통·차량', '5만원 늘려줘']:
                content = self._assert_live_budget_content(self.say(cid, text=text))
                replies.append(content)
            state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
            self.assertEqual(state['requests'], [item('TRANSPORT_CAR', 50000)], replies)
            self.assertNotEqual(content['budgetAdjustment']['phase'], 'amount')

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in source uncertainty and guidance')
    def test_live_previous_source_help_keeps_target_and_amount(self):
        from chat.llm.live import LiveReading
        from chat.engine import ConversationStore, KEY
        with patch('chat.engine.reading', return_value=LiveReading(30, None)):
            response = self.say(text='교통비 18만원으로 늘려줘')
            cid = response['conversationId']
            for text in ['잘 모르겠어요', '어디서 가져올 수 있어?', '어케할수있는데']:
                self._assert_live_budget_content(self.say(cid, text=text))
                state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
                self.assertEqual(len(state['requests']), 1)
                goal = state['requests'][0]
                self.assertEqual((goal['category'], goal['amount'], goal['amount_mode']), ('TRANSPORT_CAR', 180000, 'to'))
                self.assertIn(goal['direction'], (None, 'up'))
            self.assertEqual(Budget.objects.filter(status='approved').count(), 1)

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in multi-category user request')
    def test_live_previous_multi_source_transfer_preserves_each_amount(self):
        from chat.llm.live import LiveReading
        from chat.engine import ConversationStore, KEY
        with patch('chat.engine.reading', return_value=LiveReading(12, None)):
            response = self.say(text='쇼핑에서 3만원, 교육에서 2만원 줄여서 교통비에 5만원 옮겨줘.')
        content = self._assert_live_budget_content(response)
        state = ConversationStore.load(Conversation.objects.get(pk=response['conversationId']).slots).get_agent_state(KEY, 'budget')
        requests = {r['category']: (r['amount'], r['direction']) for r in state['requests']}
        self.assertEqual(requests['FASHION_SHOPPING'], (30000, 'down'))
        self.assertEqual(requests['EDUCATION'], (20000, 'down'))
        # The recipient can be a named destination; verify the actual movement, not one encoding.
        card = content['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['FASHION_SHOPPING'], 170000)
        self.assertEqual(card['allocations']['EDUCATION'], 30000)
        self.assertEqual(card['allocations']['TRANSPORT_CAR'], 150000)
        self.assertEqual(card['buckets'], self.ctx['buckets'])

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in topic change and future salary context')
    def test_live_previous_new_topic_and_salary_change_keep_future_goal(self):
        from chat.llm.live import LiveReading
        from chat.engine import ConversationStore, KEY
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        with patch('chat.engine.reading', return_value=LiveReading(30, None)):
            response = self.say(text='저축 30만원 늘려줘')
            cid = response['conversationId']
            for text in ['어디서 가져올 수 있는데', '다음달부터 교육비에 50만원 더 나감', '다음달부터 월급 50만원 늘어서 그냥 그것만 추가해']:
                self._assert_live_budget_content(self.say(cid, text=text))
            state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
            self.assertEqual(state['requests'], [item('EDUCATION', 500000)])
            self.assertEqual(state['scope'], 'next_cycle')
            self.assertNotIn('income_change', state['planning'])
            self.assertEqual(state['pending']['kind'], 'salary_handoff')
            self.assertEqual(Budget.objects.filter(status='approved').count(), 1)

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in recommendation help before selection')
    def test_live_previous_recommendation_uncertainty_explains_without_selecting(self):
        from chat.llm.live import LiveReading
        with patch('chat.engine.reading', return_value=LiveReading(25, None)):
            response = self.say(text='식비 5만원 늘릴 방법 비교해줘')
            self._assert_live_budget_content(response)
            cid = response['conversationId']
            for text in ['어떤게 가장 나아?', '잘 모르겠는데']:
                content = self._assert_live_budget_content(self.say(cid, text=text))
                self.assertIn(content['budgetAdjustment']['guidance']['kind'], {'explanation', 'comparison'})
                self.assertTrue(content['budgetAdjustment'].get('comparison'))
                self.assertEqual(Budget.objects.count(), 1)

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in spare concern and minimum follow-up')
    def test_live_previous_spare_concern_is_not_an_unknown_source(self):
        from chat.llm.live import LiveReading
        for repeat in range(3):
            with self.subTest(repeat=repeat):
                live = LiveReading(25, None)
                readings = []
                def complete(system, payload):
                    response = live(system, payload)
                    readings.append(json.loads(response))
                    return response
                with patch('chat.engine.reading', return_value=complete):
                    response = self.say(text='여윳돈에서 생활용품에 95000원 옮겨줘')
                    cid = response['conversationId']
                    content = self._assert_live_budget_content(self.say(cid, text='여윳돈이 너무 없어지잖아'))
                    self.assertIn('최소', content['text'], {'reply': content, 'readings': readings})
                    content = self._assert_live_budget_content(self.say(cid, text='35만원은 남겨줘'))
                    comparison = content['budgetAdjustment'].get('comparison')
                    if comparison:
                        self.assertTrue(all(p['result']['buckets']['SPARE'] >= 350000 for p in comparison['plans']))
                    else:
                        self.assertGreaterEqual(content['budgetAdjustment']['proposal']['buckets']['SPARE'], 350000)
                    self.assertEqual(Budget.objects.filter(status='approved').count(), 1)

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in redistribution preference after preview')
    def test_live_previous_distribution_preference_opens_category_editing(self):
        from chat.llm.live import LiveReading
        with patch('chat.engine.reading', return_value=LiveReading(25, None)):
            response = self.say(text='교육 5만원을 줄여서 식비로 옮겨줘')
            cid = response['conversationId']
            content = self._assert_live_budget_content(self.say(cid, text='식비 말고 다른 데다가도 분배하고 싶은데'))
            adjustment = content['budgetAdjustment']
            self.assertTrue('distributionInput' in adjustment or adjustment.get('phase') in {'distribution', 'destinations'}, content)
            self.assertEqual(Budget.objects.filter(status='approved').count(), 1)

    def test_insufficient_selected_savings_opens_combined_examples_before_partial_preview(self):
        self.ctx['diagnostic_inputs'] = {}
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='저축에서 식비에 50만원 가져와줘', answers=[
            reading(requests=[item(amount=500000)], sources=['SAVINGS']),
            draft(funding(LIVING=50000, SAVINGS=150000, CUSHION_MONTHLY=100000, SPARE=200000)), review()
        ])['messages'][-1]['content']
        comparison = reply['budgetAdjustment']['comparison']
        self.assertTrue(all(t['from'] == 'SAVINGS' for p in comparison['plans'] for t in p['result']['transfers']))
        self.assertEqual(comparison['plans'][0]['status'], 'partial')
        self.assertEqual(comparison['plans'][0]['amount'], self.ctx['bucket_available']['SAVINGS'])
        self.assertNotIn('proposal', reply['budgetAdjustment'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_sparse_combined_preview_without_initial_reference_can_be_approved(self):
        self.ctx['diagnostic_inputs'] = {}
        self.ctx.pop('ml_reference')
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='식비를 10만원 늘리되 여러 곳에서 조금씩 가져와줘', answers=[
            reading(requests=[item(amount=100000)], action='compare')])['messages'][-1]['content']
        choice = next(c['value'] for c in reply['choices'] if c['value'].startswith('plan_'))
        selected = self.chat(cid, choice={'value': choice, 'turn': reply['turn']})['messages'][-1]['content']
        card = selected['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['FOOD'], 500000)
        for code, before in self.ctx['buckets'].items():
            self.assertGreaterEqual(card['buckets'][code], (before * 3 + 9) // 10)
        approved = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(approved.status_code, 200, approved.content)
        proposal = Budget.objects.get(pk=card['proposalId'])
        self.assertEqual(proposal.status, 'approved')

    @unittest.skipUnless(os.environ.get('BUDGET_LIVE_SYNTHETIC_TEST') == '1', 'opt-in synthetic LLM')
    def test_live_sparse_history_combines_sources_and_replays_preview(self):
        from chat.llm.live import LiveReading
        self.ctx['diagnostic_inputs'] = {}
        self.ctx['bucket_available'] = dict(self.ctx['buckets'])
        cid = self.chat(choice='budget')['conversationId']
        with patch('chat.engine.reading', return_value=LiveReading(12, None)):
            reply = self.say(cid, text='식비가 75만원 더 필요해. 소비 기록이 별로 없는데 저축, 월 머니쿠션, 여윳돈, 다른 생활비에서 조금씩 나눠 가져오는 방법을 비교해줘. 각 돈은 남겨두고 싶어.')['messages'][-1]['content']
            plans = reply['budgetAdjustment']['comparison']['plans']
            plan = next(p for p in plans if p['status'] == 'ready' and len({t['from'] for t in p['result']['transfers']}) > 1)
            for code, before in self.ctx['buckets'].items():
                self.assertGreaterEqual(plan['result']['buckets'][code], (before * 3 + 9) // 10)
            self.assertTrue(any(plan['result']['buckets'][code] < before // 2 for code, before in self.ctx['buckets'].items()))
            selected = self.say(cid, choice=self.plan_choice(reply, plan['id']))['messages'][-1]['content']
            self.assertEqual(selected['budgetAdjustment']['proposal']['allocations']['FOOD'], 1150000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_next_cycle_income_handoff_preserves_education_without_changing_current_budget(self):
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='다음달 교육비 50만원 더 나가', answers=[
            reading(requests=[item('EDUCATION', 500000)], scope='next_cycle')])
        reply = self.chat(cid, text='다음달부터 월급 50만원 늘어서 그것만 추가해', answers=[
            reading(scope='next_cycle', income_change={'amount': 500000, 'mode': 'by', 'direction': 'up'},
                    sources=['SPARE'], reference='previous')])['messages'][-1]['content']
        self.assertEqual(reply['budgetAdjustment']['phase'], 'salary_handoff')
        self.assertFalse(reply['budgetAdjustment']['canApprove'])
        self.assertNotIn('proposal', reply['budgetAdjustment'])
        self.assertIn('월급 변경은 실제 변경 시점에 마이페이지에서 하실 수 있어요. 진행 중인 예산 조정은 그대로 유지해 둘게요.', reply['text'])
        from chat.engine import ConversationStore, KEY
        state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(state['requests'], [item('EDUCATION', 500000)])
        self.assertEqual(state['planning'], {'scope': 'next_cycle'})
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    @unittest.skipUnless(os.environ.get('BUDGET_LIVE_SYNTHETIC_TEST') == '1', 'opt-in synthetic LLM')
    def test_live_no_amount_sparse_history_asks_amount_before_example_selection(self):
        from chat.llm.live import LiveReading
        self.ctx['diagnostic_inputs'] = {}
        self.ctx['bucket_available'] = dict(self.ctx['buckets'])
        cid = self.chat(choice='budget')['conversationId']
        with patch('chat.engine.reading', return_value=LiveReading(12, None)):
            reply = self.say(cid, text='식비 늘려줘')['messages'][-1]['content']
            self.assertEqual(reply['budgetAdjustment']['phase'], 'amount')
            self.assertNotIn('comparison', reply['budgetAdjustment'])
            reply = self.say(cid, text='가능한 금액으로 예시를 보여줘')['messages'][-1]['content']
            comparison = reply['budgetAdjustment']['comparison']
            self.assertEqual(comparison['basisKind'], 'budget_example')
            self.assertFalse(comparison['amountWasGiven'])
            plan = next(p for p in comparison['plans'] if p['status'] == 'ready' and len({t['from'] for t in p['result']['transfers']}) > 1)
            for code, before in self.ctx['buckets'].items():
                self.assertGreaterEqual(plan['result']['buckets'][code], (before * 3 + 9) // 10)
            selected = self.say(cid, choice=self.plan_choice(reply, plan['id']))['messages'][-1]['content']
            self.assertEqual(selected['budgetAdjustment']['phase'], 'amount')
            self.assertNotIn('proposal', selected['budgetAdjustment'])
            self.assertEqual(Budget.objects.count(), 1)

    @unittest.skipUnless(os.environ.get('BUDGET_LIVE_SYNTHETIC_TEST') == '1', 'opt-in synthetic LLM')
    def test_live_next_cycle_expense_and_income_change(self):
        from chat.llm.live import LiveReading
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        cid = self.chat(choice='budget')['conversationId']
        live = LiveReading(10, None)
        with patch('chat.engine.reading', return_value=live):
            reply = self.say(cid, text='다음달부터 교육비 50만원 더 나감')['messages'][-1]['content']
            self.assertEqual(reply['budgetAdjustment']['planning']['effectiveOn'], '2026-11-25')
            self.assertNotIn('지원하지 않', reply['text'])
            reply = self.say(cid, text='다음달에 월급 50만원 늘어서 그냥 그거 쓰면 됨')['messages'][-1]['content']
            self.assertEqual(reply['budgetAdjustment']['phase'], 'salary_handoff')
            self.assertFalse(reply['budgetAdjustment']['canApprove'])
            self.assertNotIn('proposal', reply['budgetAdjustment'])
            self.assertIn('월급 변경은 실제 변경 시점에 마이페이지에서 하실 수 있어요. 진행 중인 예산 조정은 그대로 유지해 둘게요.', reply['text'])
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    @unittest.skipUnless(os.environ.get('BUDGET_LIVE_SYNTHETIC_TEST') == '1', 'opt-in synthetic LLM')
    def test_live_planned_trip_and_distinct_available_money(self):
        from chat.llm.live import LiveReading
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        cid = self.chat(choice='budget')['conversationId']
        with patch('chat.engine.reading', return_value=LiveReading(16, None)):
            first = self.say(cid, text='다음달에 여행이 예정되어있어')['messages'][-1]['content']
            self.assertNotIn('지원하지 않', first['text'])
            self.assertIn('여행', first['text'])
            second = self.say(cid, text='다음달에 여행으로 100만원정도 나갈 예정이라 지금 미리 예산 조정 하고 싶어')['messages'][-1]['content']
            self.assertEqual(second['budgetAdjustment']['phase'], 'prepared_funds', second)
            self.assertNotIn('문화·여가', second['text'])
            third = self.say(cid, text='이번 예산과 별도로 70만원 모아놨어. 나머지는 미배정 여윳돈에서 쓸게')['messages'][-1]['content']
            final = third
            if 'proposal' not in third['budgetAdjustment']:
                if third['budgetAdjustment']['phase'] == 'financial_data':
                    third = self.say(cid, text='여윳돈은 12만원 남겨줘')['messages'][-1]['content']
                comparison = third['budgetAdjustment']['comparison']
                plan = next(p for p in comparison['plans'] if p.get('amount', 0) > 0)
                self.assertEqual(plan['amount'], 280000)
                self.assertEqual({t['from'] for t in plan['result']['transfers']}, {'SPARE'})
                choice = self.plan_choice(third, plan['id'])
                final = self.say(cid, choice=choice)['messages'][-1]['content']
            card = final['budgetAdjustment']['proposal']
            self.assertEqual(card['allocations']['TRAVEL_LODGING'], 280000)
            self.assertEqual(card['buckets']['SPARE'], 120000)
            self.assertEqual(card['shortfalls'][0]['missingAmount'], 20000)
            self.assertEqual(card['buckets']['SAVINGS'], self.ctx['buckets']['SAVINGS'])
            self.assertEqual(card['buckets']['CUSHION_MONTHLY'], self.ctx['buckets']['CUSHION_MONTHLY'])
            with patch('budgets.funds_overview.get_adjustment_context', return_value=self.ctx), patch('budgets.funds_overview.cushion_data', return_value={'cushion': {'now': 900000, 'monthly': 200000}}):
                funds = self.say(cid, text='이번달 여윳돈 얼마 있어')['messages'][-1]['content']
            for label in ('남은 생활비', '미배정 여윳돈', '쌓인 머니쿠션', '적립 예산'):
                self.assertIn(label, funds['text'])

    def setUp(self):
        api_fixtures.BudgetAdjustmentApiTest.setUp(self)
        self.ctx.update(today="2026-11-10")
        self.ctx["diagnostic_inputs"] = synthetic_diagnostics(self.ctx["allocations"])
        self.ctx["diagnostic_inputs"]["planned_spare"]["amount"] = 0
        self.model = Mock(explains=False)
        def complete(system, payload, **kwargs):
            from .adjustment_core.followups import PROMPT
            # Keep scripted intent answers separate from optional follow-up selection.
            return '{"questions": []}' if system == PROMPT else self.model(system, payload, **kwargs)
        self.reader = patch("chat.engine.reading", return_value=complete)
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def chat(self, cid=None, text=None, choice=None, answers=()):
        self.model.side_effect = [json.dumps(v, ensure_ascii=False) for v in answers]
        return self.say(cid, **({"text": text} if text else {"choice": choice}))

    def offer(self, recommendation_index=0):
        start = self.chat(choice="budget")
        self.cid = start["conversationId"]
        reply = self.chat(self.cid, text="식비를 5만 원 늘리고 싶어. 비교해줘", answers=[
            reading(requests=[item(amount=50000)], action="compare", sources=["SPARE", "LIVING"]),
            {"plans": [{"funding": [{"source": "SPARE", "amount": 50000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": recommendation_index, "reason_fact_ids": ["reserve", "plan_0.preserved"]}])
        return reply["messages"][-1]["content"]

    def select(self, content):
        # 추천안 카드(plan_*)를 고른다. 예전 재정 추천(financial_*) 카드도 같은 자리를 쓴다.
        value = next(c["value"] for c in content["choices"] if c["value"].startswith(("plan_", "financial_")))
        reply = self.chat(self.cid, choice={"value": value, "turn": content["turn"]})
        return reply["messages"][-1]["content"]["budgetAdjustment"]["proposal"]

    def test_salary_handoff_card_saves_nothing_by_itself(self):
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        cid = self.chat(choice='budget')['conversationId']
        content = self.chat(cid, text='월급이 50만원 올랐어', answers=[
            reading(income_change={'amount': 500000, 'mode': 'by', 'direction': 'up'})])['messages'][-1]['content']
        adjustment = content['budgetAdjustment']
        self.assertEqual(adjustment['phase'], 'salary_handoff')
        self.assertNotIn('salaryChange', adjustment)
        self.assertFalse(adjustment['canApprove'])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.monthly_take_home_income, 3000000)  # 월급 저장은 마이페이지에서만
        self.assertEqual(Budget.objects.count(), 1)

    def test_fixed_expense_explains_why_and_followup_keeps_specific_context(self):
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='주거비 줄이려고 해', answers=[reading(action='fixed_expense')])
        content = reply['messages'][-1]['content']
        self.assertIn('고정지출로 따로 관리', content['text'])
        self.assertIn('고정지출 설정', content['text'])
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        reply = self.chat(cid, text='어떤 게 있는데?', answers=[reading(action='explain', reference='previous')])
        content = reply['messages'][-1]['content']
        self.assertIn('식비·교통비·쇼핑', content['text'])
        self.assertNotIn('서로 충돌하는 부분', content['text'])
        self.assertEqual(Budget.objects.count(), 1)
        reply = self.chat(cid, text='그럼 식비를 여윳돈에서 1만원 늘려줘', answers=[reading(
            requests=[item(amount=10000)], sources=['SPARE'], reference='new')])
        self.assertTrue(reply['messages'][-1]['content']['budgetAdjustment']['canApprove'])

    def test_combined_savings_card_keeps_default_reserves_and_can_be_selected(self):
        self.ctx['diagnostic_inputs'] = {}
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='저축 30만원 늘리는 방법 비교해줘', answers=[
            reading(requests=[item('SAVINGS', 300000)], action='compare')])['messages'][-1]['content']
        # 생활비로 부족한 만큼만 여윳돈에서 채우는 안이 추천된다.
        self.assertEqual(reply['budgetAdjustment']['comparison']['recommendation']['planId'], 'spare')
        chosen = self.chat(cid, choice={'value': 'plan_spare', 'turn': reply['turn']})['messages'][-1]['content']
        proposal = chosen['budgetAdjustment']['proposal']
        self.assertEqual(proposal['buckets']['SAVINGS'], self.ctx['buckets']['SAVINGS'] + 300000)
        self.assertGreaterEqual(proposal['buckets']['SPARE'], 200000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_new_education_topic_replaces_savings_question_without_applying_future_change(self):
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='저축 30만원 늘려줘', answers=[reading(requests=[item('SAVINGS', 300000)])])
        reply = self.chat(cid, text='다음달부터 교육비에 50만원 더 나감', answers=[
            reading(requests=[item('EDUCATION', 500000)], reference='new',
                    unclear=['다음 달부터 교육비가 50만 원 더 필요하시군요. 이번 예산도 미리 조정할까요?'])])['messages'][-1]['content']
        self.assertIn('교육', reply['budgetAdjustment']['requestSummary'])
        self.assertNotIn('저축', reply['budgetAdjustment']['requestSummary'])
        self.assertIn('이번 예산도', reply['text'])
        self.assertFalse(reply['budgetAdjustment']['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_living_shortage_keeps_increase_intent_through_short_replies(self):
        first = self.chat(text='생활비가 부족해', answers=[{'intents': ['budget_adjust']},
            reading(requests=[item('LIVING', None, 'up', None)])])
        cid = first['conversationId']
        content = first['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'category')
        self.assertIn('어느 항목이 부족한가요', content['text'])
        reply = self.chat(cid, text='전체 생활비', answers=[reading(selected_choice='LIVING', reference='previous')])
        self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'amount')
        reply = self.chat(cid, text='5만원 늘려줘', answers=[reading(requests=[item('LIVING', 50000, 'up')], reference='previous')])
        self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'destinations')
        content = reply['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'destinations')
        self.assertNotIn('줄인 돈', json.dumps(content, ensure_ascii=False))
        self.assertIn('어느 항목에 넣을까요', content['text'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_revised_goal_survives_adding_and_withdrawing_another_request(self):
        from chat.engine import ConversationStore, KEY
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='교통비에 여윳돈에서 5만원 더 넣어줘', answers=[
            reading(requests=[item('TRANSPORT_CAR', 50000)], sources=['SPARE'])])
        result = self.chat(cid, text='거기에 2만원만 늘리는 걸로', answers=[
            {'action': 'change_goal', 'category': 'TRANSPORT_CAR', 'amount': 20000,
             'amount_mode': 'by', 'suggested_target': None}])
        current = result['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(current['proposal']['allocations']['TRANSPORT_CAR'], 120000)
        state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(state['requests'], [item('TRANSPORT_CAR', 120000, 'up', 'to')])
        self.assertIn('12만 원', current['guidance']['context'])
        for data, expected in [
            (reading(requests=[item('EDUCATION', 10000)], sources=['SPARE'], reference='previous'), 60000),
            (reading(requests=[item('TRANSPORT_CAR', 120000, 'up', 'to')], request_update='replace', reference='previous'), 50000),
        ]:
            result = self.chat(cid, text='기존 요청에 대한 수정', answers=[
                {'action': 'revise', 'category': None, 'amount': None, 'amount_mode': None, 'suggested_target': None}, data])
            current = result['messages'][-1]['content']['budgetAdjustment']
            self.assertEqual(current['proposal']['allocations']['TRANSPORT_CAR'], 120000)
            self.assertEqual(current['proposal']['allocations']['EDUCATION'], expected)
            self.assertEqual(current['guidance']['kind'], 'preview')
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_goal_change_to_amount_below_current_budget_means_the_increase_amount(self):
        """'5만원 더 넣어줘' 뒤 '2만원으로 바꿔줘'를 최종 금액(10만 → 2만)으로 읽어도, 늘리는 목표라 2만 원 늘리기로 고친다 (11/17)"""
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='교통비에 여윳돈에서 5만원 더 넣어줘', answers=[
            reading(requests=[item('TRANSPORT_CAR', 50000)], sources=['SPARE'])])
        result = self.chat(cid, text='2만원으로 바꿔줘', answers=[
            {'action': 'change_goal', 'category': 'TRANSPORT_CAR', 'amount': 20000,
             'amount_mode': 'to', 'suggested_target': None}])
        current = result['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(current['proposal']['allocations']['TRANSPORT_CAR'], 120000)
        self.assertNotIn('확인하지 못했어요', json.dumps(result['messages'][-1]['content'], ensure_ascii=False))

    def test_impossible_living_increase_is_rejected_before_funding_and_accepts_correction(self):
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='생활비를 천만원 늘리고 싶어요', answers=[
            reading(requests=[item('LIVING', 10000000)])])
        content = response['messages'][-1]['content']
        adjustment = content['budgetAdjustment']
        self.assertEqual(adjustment['phase'], 'amount')
        self.assertEqual(adjustment['amountInput']['maxAmount'], 1200000)
        self.assertEqual(adjustment['amountInput']['beforeAmount'], sum(self.ctx['allocations'].values()))
        self.assertIn('10,000,000원', adjustment['guidance']['reason'])
        self.assertIn('1,200,000원', adjustment['guidance']['reason'])
        self.assertNotIn('proposal', adjustment)
        self.assertFalse(content['choices'])
        self.assertFalse(adjustment['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)
        response = self.chat(cid, text='그럼 5만원만', answers=[
            reading(requests=[item('LIVING', 50000)], reference='previous')])
        self.assertEqual(response['messages'][-1]['content']['budgetAdjustment']['phase'], 'destinations')

    def test_amount_input_supplies_current_category_total_and_bucket_budgets(self):
        amounts = {**self.ctx['allocations'], **self.ctx['buckets'], 'LIVING': sum(self.ctx['allocations'].values())}
        for code in ['FOOD', 'LIVING', 'SAVINGS', 'SPARE']:
            with self.subTest(code=code):
                cid = self.chat(choice='budget')['conversationId']
                reply = self.chat(cid, text='금액을 다시 정할게', answers=[reading(requests=[item(code, 10000000)])])
                self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['amountInput']['beforeAmount'], amounts[code])
        self.assertEqual(Budget.objects.count(), 1)

    @unittest.skipUnless(os.environ.get('BUDGET_LIVE_SYNTHETIC_TEST') == '1', 'opt-in synthetic live LLM test')
    def test_live_living_funding_question_returns_chat_response(self):
        from chat.llm.live import LiveReading
        from chat.engine import ConversationStore, KEY
        live = LiveReading(30, None)  # Seven turns also generate suggested follow-up inputs.
        with patch('chat.engine.reading', return_value=live):
            cid = None
            for index, text in enumerate(['생활비를 100만원 늘리고 싶어요', '어디서 가져올 수 있어?',
                                         '가져올 수 있는 곳마다 얼마나 쓸 수 있는지 알려줘',
                                         '저축이랑 월 머니쿠션에서 가져온다는 게 무슨 뜻이야?']):
                reply = self.say(cid, text=text)
                cid = reply['conversationId']
                content = reply['messages'][-1]['content']
                self.assertEqual(content['agent'], 'budget', content)
                self.assertTrue(content['text'], content)
                self.assertFalse(content['budgetAdjustment']['canApprove'], content)
                self.assertIn(content['budgetAdjustment']['status'], {'awaiting_input', 'replied'})
                state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
                self.assertEqual(state['requests'], [item('LIVING', 1000000)])
                if index:
                    guidance = content['budgetAdjustment']['guidance']
                    self.assertEqual(guidance['kind'], 'explanation', content)
                    if index < 3:
                        from .adjustment_core.funding import available_bucket_source
                        for code, name in [('SAVINGS', '저축'), ('SPARE', '여윳돈')]:
                            limit = available_bucket_source(self.ctx, code)['maximum']
                            self.assertIn(f'{name} · 최대 {limit:,}원', guidance['message'])
                    else:
                        self.assertIn('저축', guidance['message'])
                        self.assertIn('월 머니쿠션', guidance['message'])
                        self.assertNotIn('돈을 가져올 항목을 골라 주세요', guidance['message'])
                    self.assertNotIn('어디에서 가져올까요', guidance['message'])
                    self.assertFalse(state.get('sources'))
            reply = self.say(cid, text='모르겠어. 어떤 기준으로 골라야 해?')
            content = reply['messages'][-1]['content']
            self.assertEqual(content['budgetAdjustment']['phase'], 'destinations', content)
            self.assertTrue(content['budgetAdjustment']['guidance']['message'])
            self.assertNotIn('돈을 찾지 못했어요', content['text'])
            reply = self.say(cid, text='그럼 조정 예시를 비교해줘')
            content = reply['messages'][-1]['content']
            self.assertEqual(content['budgetAdjustment']['phase'], 'destinations', content)
            self.assertNotIn('돈을 찾지 못했어요', content['text'])
            reply = self.say(cid, choice={'value': 'FOOD', 'turn': content['turn']})
            self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'comparison')
        self.assertEqual(Budget.objects.count(), 1)
        self.assertEqual(live.usage()['failed'], 0)

    def test_funding_inspection_keeps_examples_and_selection_only_creates_preview(self):
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='교통비 18만원으로 늘려줘', answers=[
            reading(requests=[item('TRANSPORT_CAR', 180000, 'up', 'to')])])
        original = first['messages'][-1]['content']['budgetAdjustment']['comparison']
        self.assertEqual(original['basisAmount'], 80000)
        reply = self.chat(cid, text='어디서 가져올 수 있어?', answers=[reading(action='inspect', reference='previous')])
        content = reply['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['guidance']['kind'], 'comparison')
        self.assertEqual(content['budgetAdjustment']['comparison'], original)
        self.assertLessEqual(len(content['lines']), 1)
        selected = self.chat(cid, choice={'value': 'plan_living', 'turn': content['turn']})
        card = selected['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['TRANSPORT_CAR'], 180000)
        self.assertEqual(card['buckets'], self.ctx['buckets'])
        self.assertEqual(sum(t['amount'] for t in card['transfers']), 80000)
        self.assertEqual(Budget.objects.filter(status='approved').count(), 1)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    @unittest.skipUnless(os.environ.get('CHAT_LIVE_ROUTER_TEST') == '1', 'opt-in large increase reason dialogue')
    def test_live_large_increase_reason_does_not_waive_warning(self):
        from chat.llm.live import LiveReading
        self.ctx['monthly_income'] = 3000000
        cid = self.chat(choice='budget')['conversationId']
        with patch('chat.engine.reading', return_value=LiveReading(10, None)):
            first = self.say(cid, text='생활비를 100만원 늘리고 싶어요')['messages'][-1]['content']
            self.assertIn('어떤 지출에 얼마', first['text'])
            self.assertEqual(first['budgetAdjustment']['phase'], 'increase_review')
            second = self.say(cid, text='특별한 이유는 없고 그냥 많이 쓰고 싶어')['messages'][-1]['content']
            self.assertIn('30% 이상', second['text'])
            self.assertEqual(second['budgetAdjustment']['phase'], 'increase_review')
            self.assertFalse(second['budgetAdjustment']['canApprove'])
            continued = self.say(cid, text='주의사항 이해했어. 그래도 필요한 금액이니까 계속 진행해줘')['messages'][-1]['content']
            self.assertEqual(continued['budgetAdjustment']['phase'], 'destinations')
            self.assertFalse(continued['budgetAdjustment']['canApprove'])
            third = self.say(cid, text='그러면 생활비는 5만원만 늘릴래')['messages'][-1]['content']
            self.assertNotIn('increaseWarning', third['budgetAdjustment'])
        self.assertEqual(Budget.objects.filter(status='approved').count(), 1)

    def test_multiple_increases_keep_both_amounts_through_source_selection_and_preview(self):
        from chat.engine import ConversationStore, KEY
        self.ctx['spending_status'] = 'complete'
        for action in (None, 'compare', 'inspect', 'help_decide'):
            with self.subTest(action=action):
                cid = self.chat(choice='budget')['conversationId']
                requests = [item('FOOD', 50000), item('TRANSPORT_CAR', 30000)]
                response = self.chat(cid, text='식비 5만 원, 교통비 3만 원 늘려줘',
                                     answers=[reading(requests=requests, action=action)])
                asked = response['messages'][-1]['content']
                # 재원 이름을 고르게 하지 않고, 두 요청을 함께 계산한 추천안을 보여 준다.
                source = next((c['value'] for c in asked['choices'] if c['value'].startswith('plan_')), None)
                self.assertIsNotNone(source, asked)
                self.assertFalse(asked['budgetAdjustment']['canApprove'])
                state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
                self.assertEqual(state['requests'], requests)
                self.model.reset_mock()
                response = self.chat(cid, choice={'value': source, 'turn': asked['turn']})
                content = response['messages'][-1]['content']
                card = content['budgetAdjustment']['proposal']
                self.assertTrue(content['budgetAdjustment']['canApprove'])
                self.assertEqual(card['allocations']['FOOD'], self.ctx['allocations']['FOOD'] + 50000)
                self.assertEqual(card['allocations']['TRANSPORT_CAR'], self.ctx['allocations']['TRANSPORT_CAR'] + 30000)
                received = {}
                for t in card['transfers']:
                    received[t['to']] = received.get(t['to'], 0) + t['amount']
                self.assertEqual(received, {'FOOD': 50000, 'TRANSPORT_CAR': 30000})
                # 생활비 안에서 채울 수 있어 여윳돈·저축은 그대로다.
                self.assertTrue(all(t['from'] in self.ctx['allocations'] for t in card['transfers']))
                self.assertEqual(card['buckets'], self.ctx['buckets'])
                self.assertEqual(Budget.objects.filter(status='approved').count(), 1)
                self.model.assert_not_called()  # A displayed source button needs no further interpretation.

    def test_large_increase_reviews_before_category_selection_and_correction_clears_warning(self):
        self.ctx['monthly_income'] = 3000000
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='생활비를 90만원 늘려줘', answers=[reading(requests=[item('LIVING', 900000)])])
        content = reply['messages'][-1]['content']
        self.assertIn('30% 이상', content['text'])
        self.assertIn('어떤 지출에 얼마', content['text'])
        self.assertEqual(content['budgetAdjustment']['phase'], 'increase_review')
        self.assertNotIn('increaseWarning', content['budgetAdjustment'])  # No duplicate warning below the question.
        self.assertEqual([c['value'] for c in content['choices']], ['revise_increase', 'continue_increase'])
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        reply = self.chat(cid, text='그냥 많이 쓰고 싶어서', answers=[reading(reason='그냥 많이 쓰고 싶어서', reference='previous')])
        self.assertIn('꼭 필요한지는 확인하기 어려워요', reply['messages'][-1]['content']['text'])
        self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'increase_review')
        reply = self.chat(cid, text='아니 5만원만', answers=[reading(requests=[item('LIVING', 50000)], reference='previous')])
        self.assertNotIn('increaseWarning', reply['messages'][-1]['content']['budgetAdjustment'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_large_increase_acknowledgment_keeps_requests_and_requires_new_review_for_changed_amount(self):
        from chat.engine import ConversationStore, KEY
        self.ctx['monthly_income'] = 3000000
        cid = self.chat(choice='budget')['conversationId']
        requests = [item('LIVING', 900000)]
        response = self.chat(cid, text='생활비 90만원 늘려줘', answers=[reading(requests=requests)])
        content = response['messages'][-1]['content']
        response = self.chat(cid, choice={'value': 'continue_increase', 'turn': content['turn']})
        content = response['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'destinations')
        self.assertIn('30% 이상', content['budgetAdjustment']['increaseWarning'])
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(state['requests'], requests)
        response = self.chat(cid, text='아니 100만원', answers=[reading(requests=[item('LIVING', 1000000)], reference='previous')])
        content = response['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'increase_review')
        response = self.chat(cid, text='어떻게 정해야 할지 모르겠어', answers=[reading(action='help_decide')])
        content = response['messages'][-1]['content']
        self.assertIn('30% 이상', content['text'])
        self.assertIn('필요한 금액', content['text'])
        self.assertEqual(content['budgetAdjustment']['phase'], 'increase_review')
        response = self.chat(cid, choice={'value': 'revise_increase', 'turn': content['turn']})
        self.assertIn('다시 알려 주세요', response['messages'][-1]['content']['text'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_uncertain_source_reply_continues_to_destination_then_verified_comparison(self):
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='생활비를 100만원 늘려줘', answers=[reading(requests=[item('LIVING', 1000000)])])
        self.chat(cid, text='어디서 가져올 수 있어?', answers=[reading(action='inspect')])
        reply = self.chat(cid, text='방법을 비교해줘', answers=[reading(action='compare')])
        content = reply['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'destinations', content)
        self.assertNotIn('돈을 찾지 못했어요', content['text'])
        reply = self.chat(cid, choice={'value': 'FOOD', 'turn': content['turn']})
        content = reply['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'comparison', content)
        self.assertTrue(any(p['amount'] > 0 for p in content['budgetAdjustment']['comparison']['plans']))
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_impossible_preview_goal_does_not_replace_existing_proposal(self):
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='여윳돈에서 식비 1만원 늘려줘', answers=[
            reading(requests=[item(amount=10000)], sources=['SPARE'])])
        old = response['messages'][-1]['content']['budgetAdjustment']['proposal']
        response = self.chat(cid, text='아니 식비 천만원 늘릴래', answers=[
            {'action': 'change_goal', 'category': 'FOOD', 'amount': 10000000,
             'amount_mode': 'by', 'suggested_target': None}])
        content = response['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'amount')
        self.assertIn('다른 예산의 총액', content['budgetAdjustment']['guidance']['reason'])
        self.assertEqual(Budget.objects.count(), 2)
        self.assertEqual(Budget.objects.get(pk=old['proposalId']).status, 'validated')
        response = self.chat(cid, text='그럼 2만원만 늘려줘', answers=[
            reading(requests=[item(amount=20000)], reference='previous')])
        self.assertEqual(response['messages'][-1]['content']['budgetAdjustment']['proposal']['allocations']['FOOD'], 420000)

    def test_spare_minimum_survives_selection_and_final_approval(self):
        cid = self.chat(choice='budget')['conversationId']
        self.ctx['spending_status'] = 'complete'
        self.ctx['diagnostic_inputs'] = {}
        first = self.chat(cid, text='여윳돈에서 생활용품에 95000원 옮겨줘', answers=[
            reading(requests=[item('HOUSEHOLD', 95000)], sources=['SPARE'])])
        old = first['messages'][-1]['content']['budgetAdjustment']['proposal']
        reply = self.chat(cid, text='여윳돈을 더 남겨야겠어', answers=[
            {'action': 'refine_less', 'category': 'SPARE', 'amount': None, 'amount_mode': None, 'suggested_target': None}])
        content = reply['messages'][-1]['content']
        self.assertIn('최소 얼마', content['text'])
        reply = self.chat(cid, text='35만원은 남겨줘', answers=[{'value': 350000},
            draft(funding(SPARE=50000, SAVINGS=45000)), review()])
        content = reply['messages'][-1]['content']
        plan = content['budgetAdjustment']['comparison']['plans'][0]
        self.assertEqual(plan['result']['buckets']['SPARE'], 350000)
        self.assertEqual(Budget.objects.get(pk=old['proposalId']).status, 'rejected')
        choice = next(c['value'] for c in content['choices'] if c['value'].startswith('plan_'))
        reply = self.chat(cid, choice={'value': choice, 'turn': content['turn']})
        content = reply['messages'][-1]['content']
        self.assertIn('budgetAdjustment', content, reply)
        card = content['budgetAdjustment']['proposal']
        self.assertGreaterEqual(card['buckets']['SPARE'], 350000)
        if content['budgetAdjustment']['phase'] in {'partial', 'financial_partial'}:
            reply = self.chat(cid, choice={'value': 'partial_accept', 'turn': content['turn']})
            card = reply['messages'][-1]['content']['budgetAdjustment']['proposal']
        approved = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(approved.status_code, 200, approved.content)

    def test_etc_is_not_a_distribution_option_or_a_direct_adjustment_target(self):
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='기타에 여윳돈을 넣어줘', answers=[
            reading(requests=[item('ETC', 10000)], sources=['SPARE'])])
        content = reply['messages'][-1]['content']
        self.assertIn('소비 내역을 분류하는 항목', content['text'])
        self.assertNotIn('proposal', content['budgetAdjustment'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_old_living_destination_question_displays_transport_and_accepts_its_amount(self):
        from chat.engine import ConversationStore, KEY
        cid = self.chat(choice='budget')['conversationId']
        talk = Conversation.objects.get(pk=cid)
        store = ConversationStore.load(talk.slots)
        state = store.get_agent_state(KEY, 'budget')
        state.update(requests=[item('LIVING', None, 'up', None)], pending={
            'kind': 'destinations', 'index': 0, 'line': '어느 생활비 항목에 배분할까요?',
            'choices': [['TRANSPORT_CAR', '교통·차량']]})
        store.save_agent_state(KEY, 'budget', state)
        talk.slots = store.dump()
        talk.save(update_fields=['slots'])
        response = self.chat(cid, text='교통·차량', answers=[reading(selected_choice='TRANSPORT_CAR', reference='previous')])
        content = response['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['amountInput']['category'], 'TRANSPORT_CAR')
        self.assertIn('교통·차량 예산을 얼마 늘릴까요?', content['text'])
        self.assertEqual(content['budgetAdjustment']['explanation'], {})
        self.assertNotIn('comparison', content['budgetAdjustment'])
        response = self.chat(cid, text='생활비를 5만원 늘리고 싶어요', answers=[reading(
            requests=[item('LIVING', 50000)], reference='previous')])
        content = response['messages'][-1]['content']
        self.assertNotEqual(content['budgetAdjustment']['phase'], 'amount')
        self.assertNotIn('amountInput', content['budgetAdjustment'])
        state = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(state['requests'], [item('TRANSPORT_CAR', 50000)])
        self.assertEqual(Budget.objects.count(), 1)

    def test_unavailable_housing_category_does_not_become_generic_clarification(self):
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='주거비 줄여줘', answers=[reading(
            requests=[item('HOUSING_TELECOM', None, 'down')], unclear=['예산 항목을 확인해 주세요.'])])
        content = reply['messages'][-1]['content']
        self.assertIn('고정지출', content['text'])
        self.assertNotIn('예산 항목을 확인', content['text'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_no_automatic_capacity_keeps_manual_living_adjustment_available(self):
        self.ctx['minimums'].update({c: n for c, n in self.ctx['allocations'].items() if c not in self.ctx['priorities']})
        self.ctx['spent']['ETC'] = self.ctx['allocations']['ETC']
        self.ctx['bucket_status'] = {code: 'unknown' for code in self.ctx['buckets']}
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='교통비 18만원으로 늘려줘', answers=[reading(
            requests=[item('TRANSPORT_CAR', 180000, 'up', 'to')])])
        content = response['messages'][-1]['content']
        self.assertEqual({c['value'] for c in content['choices']}, {'manual_living'})
        self.assertIn('추천에서 제외', content['budgetAdjustment']['explanation']['brief'])
        self.assertIn('생활비', content['budgetAdjustment']['explanation']['why'])
        self.assertIn('여윳돈', content['budgetAdjustment']['explanation']['why'])
        opened = self.chat(cid, choice={'value': 'manual_living', 'turn': content['turn']})
        content = opened['messages'][-1]['content']
        editor = content['budgetAdjustment']['distributionInput']
        self.assertEqual(editor['target'], 'TRANSPORT_CAR')
        self.assertEqual(editor['amount'], 80000)
        self.assertIn('FASHION_SHOPPING', editor['categories'])
        self.assertEqual(editor['maximums']['FASHION_SHOPPING'], 190000)
        self.assertEqual(Budget.objects.count(), 1)
        result = self.chat(cid, text='쇼핑 3만원 교육 5만원을 줄여 교통비를 8만원 늘려줘', answers=[reading(
            action='distribute', requests=[item('FASHION_SHOPPING', 30000, 'down'), item('EDUCATION', 50000, 'down'), item('TRANSPORT_CAR', 80000)])])
        adjustment = result['messages'][-1]['content']['budgetAdjustment']
        self.assertTrue(adjustment['canApprove'])
        self.assertEqual(adjustment['proposal']['allocations']['TRANSPORT_CAR'], 180000)
        self.assertEqual(adjustment['proposal']['allocations']['EDUCATION'], 0)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_missing_spending_hides_automatic_choices_without_disabling_manual_input(self):
        self.ctx['spending_status'] = 'partial'
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='교통비 18만원으로 늘려줘', answers=[reading(
            requests=[item('TRANSPORT_CAR', 180000, 'up', 'to')])])
        content = response['messages'][-1]['content']
        self.assertEqual({c['value'] for c in content['choices']}, {'manual_living'})
        opened = self.chat(cid, choice={'value': 'manual_living', 'turn': content['turn']})
        self.assertIn('distributionInput', opened['messages'][-1]['content']['budgetAdjustment'])
        result = self.chat(cid, text='쇼핑 8만원을 교통으로 옮겨줘', answers=[reading(
            requests=[item('FASHION_SHOPPING', 80000, 'down'), item('TRANSPORT_CAR', 80000)])])
        self.assertFalse(result['messages'][-1]['content']['budgetAdjustment']['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_direct_transfer_keeps_amounts_and_budget_preview_while_history_refreshes(self):
        self.ctx['spending_status'] = 'partial'
        cid = self.chat(choice='budget')['conversationId']
        requests = [item('FASHION_SHOPPING', 30000, 'down'), item('EDUCATION', 20000, 'down'), item('TRANSPORT_CAR', 50000)]
        reply = self.chat(cid, text='쇼핑 3만원 교육 2만원을 줄여 교통비에 5만원 옮겨줘', answers=[reading(requests=requests)])
        content = reply['messages'][-1]['content']
        editor = content['budgetAdjustment']['distributionInput']
        self.assertTrue(editor['needsRefresh'])
        self.assertEqual(editor['amount'], 50000)
        self.assertEqual(editor['before']['TRANSPORT_CAR'], 100000)
        self.assertEqual(editor['values'], [{'category': 'FASHION_SHOPPING', 'amount': 30000}, {'category': 'EDUCATION', 'amount': 20000}])
        self.assertEqual(Budget.objects.count(), 1)
        self.ctx['spending_status'] = 'complete'
        refreshed = self.chat(cid, choice={'value': 'manual_living', 'turn': content['turn']})
        refreshed_editor = refreshed['messages'][-1]['content']['budgetAdjustment']['distributionInput']
        self.assertFalse(refreshed_editor['needsRefresh'])
        self.assertEqual(refreshed_editor['values'], editor['values'])
        result = self.chat(cid, text='쇼핑 3만원 교육 2만원을 줄여 교통비에 5만원 옮겨줘', answers=[reading(requests=requests)])
        adjustment = result['messages'][-1]['content']['budgetAdjustment']
        self.assertTrue(adjustment['canApprove'])
        self.assertEqual(adjustment['proposal']['allocations']['TRANSPORT_CAR'], 150000)

    def test_manual_living_requires_lock_consent_and_keeps_minimum_and_spent(self):
        self.ctx['locks']['EDUCATION'] = 50000
        self.ctx['minimums']['EDUCATION'] = 20000
        self.ctx['spent']['EDUCATION'] = 15000
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='교통비 3만원 늘려줘', answers=[reading(requests=[item('TRANSPORT_CAR', 30000)])])
        content = response['messages'][-1]['content']
        opened = self.chat(cid, choice={'value': 'manual_living', 'turn': content['turn']})
        self.assertEqual(opened['messages'][-1]['content']['budgetAdjustment']['distributionInput']['maximums']['EDUCATION'], 30000)
        locked = self.chat(cid, text='교육 3만원을 교통에 옮겨줘', answers=[reading(
            requests=[item('EDUCATION', 30000, 'down'), item('TRANSPORT_CAR', 30000)])])
        self.assertEqual(locked['messages'][-1]['content']['budgetAdjustment']['phase'], 'unlock')
        self.assertEqual(Budget.objects.count(), 1)
        unlocked = self.chat(cid, text='잠금 풀어도 돼', answers=[reading(unlock=True)])
        card = unlocked['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['EDUCATION'], 20000)
        self.assertEqual(card['unlockCategories'], ['EDUCATION'])

    def test_manual_living_can_ask_amount_before_source_categories(self):
        self.ctx['priorities'] = {code: rank for rank, code in enumerate(self.ctx['allocations'], 1)}
        self.ctx['diagnostic_inputs']['history'] = {}
        self.ctx['bucket_status'] = {code: 'unknown' for code in self.ctx['buckets']}
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='교통비 늘리고 싶어', answers=[reading(requests=[item('TRANSPORT_CAR')])])
        content = response['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['amountInput']['direction'], 'up')
        amount = self.chat(cid, text='교통비 5만원 늘려줘', answers=[reading(requests=[item('TRANSPORT_CAR', 50000)])])
        content = amount['messages'][-1]['content']
        opened = self.chat(cid, choice={'value': 'manual_living', 'turn': content['turn']})
        self.assertEqual(opened['messages'][-1]['content']['budgetAdjustment']['distributionInput']['amount'], 50000)

    def test_empty_financial_recommendations_still_allow_manual_adjustment(self):
        cid = self.chat(choice='budget')['conversationId']
        with patch('budgets.adjustment_core.financial_conversation.recommend', return_value={
                'diagnosis': {'status': 'ready', 'allowed': [], 'capacities': {}, 'excluded': []}, 'plans': []}):
            response = self.chat(cid, text='식비 5만원 늘리는 방법 추천해줘', answers=[reading(
                requests=[item('FOOD', 50000)], action='compare')])
        content = response['messages'][-1]['content']
        self.assertIn('manual_living', {c['value'] for c in content['choices']})
        opened = self.chat(cid, choice={'value': 'manual_living', 'turn': content['turn']})
        self.assertEqual(opened['messages'][-1]['content']['budgetAdjustment']['distributionInput']['target'], 'FOOD')

    def test_living_first_choices_hide_unverified_buckets_but_allow_explicit_savings(self):
        self.ctx['bucket_status'].update(SPARE='unknown', CUSHION_MONTHLY='unknown')
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='식비 5만원 늘려줘', answers=[reading(requests=[item('FOOD', 50000)])])
        values = {c['value'] for c in response['messages'][-1]['content']['choices']}
        self.assertNotIn('SPARE', values)
        self.assertNotIn('CUSHION_MONTHLY', values)
        selected = self.chat(cid, text='저축에서 5만원 가져와', answers=[reading(requests=[item('FOOD', 50000)], sources=['SAVINGS'], reference='previous')])
        card = selected['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(card['buckets']['SAVINGS'], self.ctx['buckets']['SAVINGS'] - 50000)

    def test_distribution_preserves_unlock_consent_until_approval(self):
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.base.cycle,
            category_id='EDUCATION', locked=True, locked_amount=50000, revision_no=2)
        self.ctx.update(locks={'EDUCATION': 50000}, lock_revisions={'EDUCATION': 2})
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='교육 5만원을 식비로 옮겨줘', answers=[reading(
            requests=[item('EDUCATION', 50000, 'down')], destinations=['FOOD'])])
        first = self.chat(cid, text='잠금 풀어줘', answers=[reading(unlock=True)])
        old = first['messages'][-1]['content']['budgetAdjustment']['proposal']
        response = self.chat(cid, text='식비 3만원 교통 2만원으로 나눠줘', answers=[
            {'action': 'distribute', 'category': None, 'destination': None, 'amount': None,
             'amount_mode': None, 'suggested_target': None},
            reading(action='distribute', requests=[item('FOOD', 30000), item('TRANSPORT_CAR', 20000)])])
        card = response['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(card['unlockCategories'], ['EDUCATION'])
        self.assertEqual(card['allocations']['EDUCATION'], 0)
        self.assertEqual(card['allocations']['FOOD'], 430000)
        self.assertEqual(card['allocations']['TRANSPORT_CAR'], 120000)
        self.assertEqual(Budget.objects.get(pk=old['proposalId']).status, 'expired')
        setting.refresh_from_db()
        self.assertTrue(setting.locked)
        result = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(result.status_code, 200, result.content)
        setting.refresh_from_db()
        self.assertFalse(setting.locked)

    def test_distribution_rejects_changed_unlock_consent(self):
        self.ctx.update(locks={'EDUCATION': 50000}, lock_revisions={'EDUCATION': 2})
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='교육 5만원을 식비로 옮겨줘', answers=[reading(
            requests=[item('EDUCATION', 50000, 'down')], destinations=['FOOD'])])
        self.chat(cid, text='잠금 풀어줘', answers=[reading(unlock=True)])
        self.chat(cid, text='다시 나눌래', answers=[
            {'action': 'distribute', 'category': None, 'destination': None, 'amount': None,
             'amount_mode': None, 'suggested_target': None}, reading(action='distribute')])
        count = Budget.objects.count()
        self.ctx['lock_revisions']['EDUCATION'] = 3
        response = self.chat(cid, text='식비 3만원 교통 2만원으로 나눠줘', answers=[reading(
            action='distribute', requests=[item('FOOD', 30000), item('TRANSPORT_CAR', 20000)])])
        self.assertIn('잠금 조건이 바뀌었어요', str(response))
        self.assertEqual(Budget.objects.count(), count)

    def test_distribution_preserves_cleared_target_until_approval(self):
        self.profile.budget_category_targets = {'EDUCATION': 50000}
        self.profile.save(update_fields=['budget_category_targets'])
        self.ctx['category_targets'] = {'EDUCATION': 50000}
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='교육 5만원을 식비로 옮기고 교육 유지 목표도 해제해줘', answers=[reading(
            requests=[item('EDUCATION', 50000, 'down')], destinations=['FOOD'], clear_targets=['EDUCATION'])])
        old = first['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertIn({'category': 'EDUCATION', 'amount': None}, old['targetItems'])
        response = self.chat(cid, text='식비 3만원 교통 2만원으로 나눠줘', answers=[
            {'action': 'distribute', 'category': None, 'destination': None, 'amount': None,
             'amount_mode': None, 'suggested_target': None},
            reading(action='distribute', requests=[item('FOOD', 30000), item('TRANSPORT_CAR', 20000)])])
        card = response['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertIn({'category': 'EDUCATION', 'amount': None}, card['targetItems'])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {'EDUCATION': 50000})
        result = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(result.status_code, 200, result.content)
        self.profile.refresh_from_db()
        self.assertNotIn('EDUCATION', self.profile.budget_category_targets)

    def test_financial_selection_back_returns_to_previous_step(self):
        from chat.engine import ConversationStore, KEY
        self.offer()
        before = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(before['pending']['kind'], 'comparison')
        response = self.chat(self.cid, text='뒤로', answers=[reading(action='back')])
        after = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(len(after['history']), len(before['history']) - 1)
        self.assertNotEqual(after.get('pending', {}).get('kind'), 'financial_selection')
        self.assertFalse(response['messages'][-1]['content']['budgetAdjustment']['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_distribution_request_opens_picker_and_splits_without_saving(self):
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='교육 예산을 5만원 줄여줘', answers=[reading(requests=[item('EDUCATION', 50000, 'down')])])
        content = first['messages'][-1]['content']
        self.assertIn('distribute', {choice['value'] for choice in content['choices']})
        opened = self.chat(cid, text='식비 말고 다른 데도 분배하고 싶은데', answers=[reading(action='distribute', reference='previous')])
        editor = opened['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(editor['phase'], 'distribution')
        self.assertEqual(editor['distributionInput']['amount'], 50000)
        self.assertIn('FOOD', editor['distributionInput']['categories'])
        self.assertNotIn('EDUCATION', editor['distributionInput']['categories'])
        self.assertEqual(Budget.objects.count(), 1)
        result = self.chat(cid, text='교육에서 줄인 5만원을 식비 2만원, 교통 3만원으로 나눠줘', answers=[reading(
            action='distribute', requests=[item('EDUCATION', 50000, 'down'), item('FOOD', 20000), item('TRANSPORT_CAR', 30000)])])
        adjustment = result['messages'][-1]['content']['budgetAdjustment']
        self.assertTrue(adjustment['canApprove'])
        card = adjustment['proposal']
        self.assertEqual(card['allocations']['EDUCATION'], 0)
        self.assertEqual(card['allocations']['FOOD'], 420000)
        self.assertEqual(card['allocations']['TRANSPORT_CAR'], 130000)
        self.assertEqual(Budget.objects.get(pk=card['proposalId']).status, 'validated')

    def test_reduction_living_choice_waits_for_user_distribution_before_preview_and_approval(self):
        cid = self.chat(choice='budget')['conversationId']
        offered = self.chat(cid, text='식비 10만원 줄여줘', answers=[
            reading(requests=[item('FOOD', 100000, 'down')])])['messages'][-1]['content']
        living = next(plan for plan in offered['budgetAdjustment']['comparison']['plans'] if plan['id'] == 'living')
        self.assertEqual(living['status'], 'needs_destination')
        self.assertIsNone(living['result'])
        self.assertFalse(living['recommended'])
        self.assertEqual({choice['value'] for choice in offered['choices'] if choice['value'].startswith('plan_')},
                         {'plan_living', 'plan_spare', 'plan_savings', 'plan_cushion'})
        selected = self.chat(cid, choice={'value': 'plan_living', 'turn': offered['turn']})['messages'][-1]['content']
        adjustment = selected['budgetAdjustment']
        self.assertEqual(adjustment['phase'], 'distribution')
        self.assertEqual(adjustment['distributionInput']['amount'], 100000)
        self.assertEqual(adjustment['distributionInput']['sources'], [{'category': 'FOOD', 'amount': 100000}])
        self.assertNotIn('proposal', adjustment)
        self.assertFalse(adjustment['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)
        allocated = self.chat(cid, text='식비에서 줄인 10만원을 교육 6만원, 병원비 4만원에 나눠줘', answers=[reading(
            action='distribute', requests=[item('FOOD', 100000, 'down'), item('EDUCATION', 60000),
                                           item('MEDICAL_HEALTH', 40000)])])['messages'][-1]['content']
        card = allocated['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['FOOD'], self.ctx['allocations']['FOOD'] - 100000)
        self.assertEqual(card['allocations']['EDUCATION'], self.ctx['allocations']['EDUCATION'] + 60000)
        self.assertEqual(card['allocations']['MEDICAL_HEALTH'], self.ctx['allocations']['MEDICAL_HEALTH'] + 40000)
        self.assertEqual(card['allocations']['TRANSPORT_CAR'], self.ctx['allocations']['TRANSPORT_CAR'])
        self.assertEqual(card['buckets'], self.ctx['buckets'])
        self.assertEqual(sum(card['allocations'].values()) + sum(card['buckets'].values()) + self.ctx['fixed_amount'],
                         self.ctx['funding_amount'])
        self.assertEqual(sum(row['amount'] for row in card['transfers']), 100000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')
        approved = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(Budget.objects.get(pk=card['proposalId']).status, 'approved')

    def test_distribution_choice_needs_no_llm_and_rejects_invalid_totals_and_locked_receivers(self):
        self.ctx['locks']['TRANSPORT_CAR'] = self.ctx['allocations']['TRANSPORT_CAR']
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='교육 예산을 5만원 줄여줘', answers=[reading(requests=[item('EDUCATION', 50000, 'down')])])
        content = first['messages'][-1]['content']
        opened = self.chat(cid, choice={'value': 'distribute', 'turn': content['turn']})
        self.assertNotIn('TRANSPORT_CAR', opened['messages'][-1]['content']['budgetAdjustment']['distributionInput']['categories'])
        for receivers in ([item('FOOD', 20000)], [item('FOOD', 20000), item('TRANSPORT_CAR', 30000)],
                          [item('FOOD', 25000), item('FOOD', 25000)]):
            result = self.chat(cid, text='이렇게 나눠줘', answers=[reading(action='distribute', requests=[item('EDUCATION', 50000, 'down'), *receivers])])
            adjustment = result['messages'][-1]['content']['budgetAdjustment']
            self.assertEqual(adjustment['phase'], 'distribution')
            self.assertNotIn('proposal', adjustment)
            self.assertEqual(Budget.objects.count(), 1)

    def test_distribution_after_preview_preserves_old_card_until_valid_replacement(self):
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='교육 예산을 5만원 줄여 식비에 넣어줘', answers=[reading(requests=[item('EDUCATION', 50000, 'down')], destinations=['FOOD'])])
        old = first['messages'][-1]['content']['budgetAdjustment']['proposal']
        opened = self.chat(cid, text='한 곳에 다 넣지 말고 나눌래', answers=[{'action': 'distribute', 'category': None, 'destination': None,
            'amount': None, 'amount_mode': None, 'suggested_target': None}, reading(action='distribute')])
        self.assertIn('distributionInput', opened['messages'][-1]['content']['budgetAdjustment'])
        self.assertEqual(Budget.objects.get(pk=old['proposalId']).status, 'validated')
        result = self.chat(cid, text='식비 2만원, 교통 3만원으로 나눠줘', answers=[reading(action='distribute',
            requests=[item('FOOD', 20000), item('TRANSPORT_CAR', 30000)])])
        new = result['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(new['allocations']['FOOD'], 420000)
        self.assertEqual(Budget.objects.get(pk=old['proposalId']).status, 'expired')
        self.assertEqual(Budget.objects.get(pk=new['proposalId']).status, 'validated')

    def test_distribution_rechecks_changed_context_and_does_not_spend_used_money(self):
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='교육에서 5만원 나누고 싶어', answers=[reading(action='distribute', requests=[item('EDUCATION', 50000, 'down')])])
        self.ctx['spent']['EDUCATION'] = 10000
        for _ in range(2):
            result = self.chat(cid, text='식비에 2만원 교통에 3만원 나눠줘', answers=[reading(action='distribute',
                requests=[item('FOOD', 20000), item('TRANSPORT_CAR', 30000)])])
            adjustment = result['messages'][-1]['content']['budgetAdjustment']
            self.assertEqual(adjustment['phase'], 'distribution')
            self.assertNotIn('proposal', adjustment)
            self.assertFalse(adjustment['canApprove'])
            self.assertEqual(Budget.objects.count(), 1)

    def test_living_plan_at_zero_source_can_reallocate_its_existing_distribution(self):
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='교육 예산을 5만원 줄여 식비에 넣어줘', answers=[reading(
            requests=[item('EDUCATION', 50000, 'down')], destinations=['FOOD'])])
        adjustment = first['messages'][-1]['content']['budgetAdjustment']
        old = adjustment['proposal']
        self.assertEqual(old['allocations']['EDUCATION'], 0)
        self.assertEqual(adjustment['distributionEdit']['amount'], 50000)
        self.assertEqual(adjustment['distributionEdit']['values'], [{'category': 'FOOD', 'amount': 50000}])
        response = self.chat(cid, text='기존 배분 대신 교육 예산을 50000원 줄여 식비에 30000원, 교통·차량에 20000원 나눠줘.', answers=[
            {'action': 'distribute', 'category': None, 'destination': None, 'amount': None, 'amount_mode': None, 'suggested_target': None},
            reading(action='distribute', requests=[item('EDUCATION', 50000, 'down'), item('FOOD', 30000), item('TRANSPORT_CAR', 20000)])])
        new = response['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(new['allocations']['EDUCATION'], 0)
        self.assertEqual(new['allocations']['FOOD'], 430000)
        self.assertEqual(new['allocations']['TRANSPORT_CAR'], 120000)
        self.assertEqual(sum(new['allocations'].values()), sum(old['allocations'].values()))
        self.assertEqual(Budget.objects.get(pk=old['proposalId']).status, 'expired')
        self.assertEqual(Budget.objects.get(pk=new['proposalId']).status, 'validated')

    def test_distribution_can_replace_a_plan_funded_by_multiple_categories(self):
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='쇼핑 2만원과 교통 3만원을 식비로', answers=[reading(
            requests=[item('FASHION_SHOPPING', 20000, 'down'), item('TRANSPORT_CAR', 30000, 'down')], destinations=['FOOD'])])
        adjustment = first['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['distributionEdit']['amount'], 50000)
        self.assertEqual(len(adjustment['distributionEdit']['sources']), 2)
        result = self.chat(cid, text='기존 배분 대신 쇼핑 2만원, 교통 3만원 줄여 식비 3만원 교육 2만원 나눠줘', answers=[
            {'action': 'distribute', 'category': None, 'destination': None, 'amount': None, 'amount_mode': None, 'suggested_target': None},
            reading(action='distribute', requests=[item('FASHION_SHOPPING', 20000, 'down'), item('TRANSPORT_CAR', 30000, 'down'),
                item('FOOD', 30000), item('EDUCATION', 20000)])])
        card = result['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['FOOD'], 430000)
        self.assertEqual(card['allocations']['EDUCATION'], 70000)

    def test_recommendation_after_examples_explains_without_selecting_or_recalculating(self):
        from chat.engine import ConversationStore, KEY
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='의료비를 만원으로 줄여줘', answers=[reading(requests=[item('MEDICAL_HEALTH', 10000, 'down', 'to')])])
        before = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')['comparison']
        for text in ('잘 모르겠는데', '어떤게 가장 나아?', '어떤걸 가장 추천해?'):
            response = self.chat(cid, text=text, answers=[reading(action='recommend')])
            reply = response['messages'][-1]['content']
            self.assertIn('한 가지 방법을 추천하기 어려워요', reply['budgetAdjustment']['recommendationAdvice']['title'])
            self.assertTrue(reply['budgetAdjustment']['recommendationAdvice']['reasons'])
            self.assertEqual([(plan['id'], plan['status']) for plan in reply['budgetAdjustment']['comparison']['plans']],
                             [(plan['id'], plan['status']) for plan in before['comparison']['plans']])
            self.assertIn('plan_living', {c['value'] for c in reply['choices']})
            self.assertNotIn('proposal', reply['budgetAdjustment'])
            self.assertEqual(Budget.objects.count(), 1)
            self.assertEqual(ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')['comparison'], before)

    def test_financial_recommendation_keeps_reasons_visible_without_selection(self):
        self.offer()
        response = self.chat(self.cid, text='어느 쪽이 나을까?', answers=[reading(action='recommend')])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertIn('추천해요', adjustment['recommendationAdvice']['title'])
        self.assertTrue(adjustment['recommendationAdvice']['reasons'])
        self.assertNotIn('proposal', adjustment)
        self.assertEqual(Budget.objects.count(), 1)

    def test_transfer_from_current_preview_keeps_other_changes_and_requires_new_approval(self):
        first = self.select(self.offer())
        response = self.chat(self.cid, text='현재 변경안에서 식비에서 10000원을 교육 항목으로 옮겨줘. 다른 변경은 유지해줘.', answers=[{
            'action': 'transfer', 'category': 'FOOD', 'destination': 'EDUCATION', 'amount': 10000,
            'amount_mode': 'by', 'suggested_target': None}])
        revised = response['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(revised['allocations']['FOOD'], 440000)
        self.assertEqual(revised['allocations']['EDUCATION'], 60000)
        self.assertEqual(revised['buckets']['SPARE'], 400000)
        self.assertNotEqual(first['proposalId'], revised['proposalId'])
        self.assertEqual(Budget.objects.get(pk=first['proposalId']).status, 'expired')
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')
        self.assertEqual(self.post(f"/api/budgets/proposals/{first['proposalId']}/approve/", {}).status_code, 409)

        saved = self.post(f"/api/budgets/proposals/{revised['proposalId']}/approve/", {})
        self.assertEqual(saved.status_code, 200, saved.content)

    def test_transfer_that_breaks_spent_floor_keeps_the_previous_preview(self):
        first = self.select(self.offer())
        response = self.chat(self.cid, text='현재 변경안에서 식비에서 400000원을 교육으로 옮겨줘', answers=[{
            'action': 'transfer', 'category': 'FOOD', 'destination': 'EDUCATION', 'amount': 400000,
            'amount_mode': 'by', 'suggested_target': None}])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['proposal']['proposalId'], first['proposalId'])
        self.assertEqual(adjustment['status'], 'awaiting_input')
        self.assertEqual(Budget.objects.count(), 2)
        self.assertEqual(Budget.objects.get(pk=first['proposalId']).status, 'validated')

    def test_followup_uses_recent_conversation_and_moves_to_a_verified_amount(self):
        cid = self.chat(choice='budget')['conversationId']
        first = self.chat(cid, text='식비가 조금 모자라', answers=[reading(requests=[item()]),
            {'plans': [{'funding': [{'source': 'SPARE', 'amount': 120000}]}], 'recommendation_index': None, 'reason_fact_ids': []},
            {'plans': [], 'recommendation_index': 0, 'reason_fact_ids': ['reserve', 'plan_0.preserved']}])
        self.assertEqual(first['messages'][-1]['content']['budgetAdjustment']['phase'], 'amount')
        response = self.chat(cid, text='저축은 그대로 두고 남겨둔 돈에서 보태면?', answers=[
            reading(sources=['SPARE'], excluded=['SAVINGS'], reference='previous')])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['phase'], 'amount')
        self.assertNotIn('comparison', adjustment)
        self.assertEqual(adjustment['amountInput']['category'], 'FOOD')
        payload = json.loads(self.model.call_args.args[1])
        self.assertEqual(payload['previous']['requests'][0]['category'], 'FOOD')
        self.assertEqual(payload['conversation'][-1]['user'], '식비가 조금 모자라')
        self.assertEqual(Budget.objects.count(), 1)
        response = self.chat(cid, text='5만원', answers=[
            reading(requests=[item(amount=50000)], reference='previous')])
        card = response['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['FOOD'], 450000)
        self.assertEqual({t['from'] for t in card['transfers']}, {'SPARE'})
        self.assertEqual(card['buckets']['SAVINGS'], self.ctx['buckets']['SAVINGS'])

    def test_multiple_donors_keep_each_amount_and_server_sums_the_destination(self):
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='쇼핑에서 2만원, 교통에서 3만원 빼서 식비에 보태줘', answers=[
            reading(requests=[item('FASHION_SHOPPING', 20000, 'down'), item('TRANSPORT_CAR', 30000, 'down')],
                    destinations=['FOOD'])])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['proposal']['allocations']['FASHION_SHOPPING'], 180000)
        self.assertEqual(adjustment['proposal']['allocations']['TRANSPORT_CAR'], 70000)
        self.assertEqual(adjustment['proposal']['allocations']['FOOD'], 450000)
        self.assertIn('2만 원', adjustment['requestSummary'])
        self.assertIn('3만 원', adjustment['requestSummary'])
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_semantic_plan_selection_uses_the_offered_choice_without_saving(self):
        self.offer()
        response = self.chat(self.cid, text='남겨둔 돈에서 보태는 쪽으로 볼게', answers=[
            reading(selected_choice='plan_chosen', approval=True, reference='previous')])
        self.assertIn('proposal', response['messages'][-1]['content']['budgetAdjustment'])
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_interpreter_cannot_select_a_plan_that_was_not_offered(self):
        offered = self.offer()
        response = self.chat(self.cid, text='그 안으로 볼게', answers=[reading(selected_choice='financial_999')])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['comparison'], offered['budgetAdjustment']['comparison'])
        self.assertNotIn('proposal', adjustment)
        self.assertEqual(Budget.objects.count(), 1)

    def test_question_word_does_not_swallow_a_new_amount(self):
        self.offer()
        response = self.chat(self.cid, text='왜 이렇게 커? 2만원만 여윳돈에서 보태줘', answers=[
            reading(requests=[item(amount=20000)], sources=['SPARE'], reference='previous')])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['proposal']['allocations']['FOOD'], 420000)
        # 비교 1번 + 이번 문장 1번만 해석한다(추가 모델 호출 없음).
        self.assertEqual(self.model.call_count, 2)

    def test_unknown_amount_asks_first_even_when_interpreter_requests_comparison(self):
        for action in (None, 'compare'):
            for direction in ('up', 'down'):
                with self.subTest(action=action, direction=direction):
                    cid = self.chat(choice='budget')['conversationId']
                    response = self.chat(cid, text='의료비는 좀 조정하고 싶은데', answers=[
                        reading(requests=[item('MEDICAL_HEALTH', None, direction, None)], action=action)])
                    adjustment = response['messages'][-1]['content']['budgetAdjustment']
                    self.assertEqual(adjustment['phase'], 'amount')
                    self.assertEqual(adjustment['amountInput']['category'], 'MEDICAL_HEALTH')
                    self.assertEqual(adjustment['amountInput']['direction'], direction)
                    self.assertNotIn('comparison', adjustment)
                    self.assertNotIn('proposal', adjustment)
                    self.assertFalse(adjustment['canApprove'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_no_amount_comparison_keeps_the_named_destination(self):
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='의료비 좀 줄여서 교통에 넣고 싶은데 얼마나 가능해?', answers=[
            reading(requests=[item('MEDICAL_HEALTH', None, 'down', None)], destinations=['TRANSPORT_CAR'],
                    action='compare', use_available=True)])
        content = response['messages'][-1]['content']
        comparison = content['budgetAdjustment']['comparison']
        plans = [p for p in comparison['plans'] if p.get('result')]
        self.assertEqual(len(plans), 1)
        self.assertTrue(all(t['to'] == 'TRANSPORT_CAR' for t in plans[0]['result']['transfers']))
        self.assertEqual(Budget.objects.count(), 1)
        selected = self.chat(cid, choice=self.plan_choice(content, plans[0]['id']))['messages'][-1]['content']
        self.assertEqual(selected['budgetAdjustment']['phase'], 'amount')
        self.assertNotIn('proposal', selected['budgetAdjustment'])
        self.assertEqual(Budget.objects.count(), 1)
        entered = self.chat(cid, text='1만원만 옮겨줘', answers=[
            reading(requests=[item('MEDICAL_HEALTH', 10000, 'down')], reference='previous')])['messages'][-1]['content']
        card = entered['budgetAdjustment']['proposal']
        self.assertEqual(card['allocations']['MEDICAL_HEALTH'], self.ctx['allocations']['MEDICAL_HEALTH'] - 10000)
        self.assertEqual(card['allocations']['TRANSPORT_CAR'], self.ctx['allocations']['TRANSPORT_CAR'] + 10000)
        approved = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(approved.status_code, 200, approved.content)

    def test_missing_amount_preserves_the_interpreted_request_without_running_planner(self):
        from chat.engine import ConversationStore, KEY
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='식비를 좀 넉넉하게 잡아줘', answers=[reading(requests=[item()])])
        content = response['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['status'], 'awaiting_input')
        self.assertEqual(content['budgetAdjustment']['phase'], 'amount')
        self.assertNotIn('comparison', content['budgetAdjustment'])
        self.assertEqual(self.model.call_count, 1)
        stored = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(stored['requests'][0]['category'], 'FOOD')
        self.assertEqual(stored['requests'][0]['direction'], 'up')
        self.assertEqual(Budget.objects.count(), 1)

    def test_comparison_explanation_keeps_the_card_and_opens_details_in_web_response(self):
        from chat.engine import ConversationStore, KEY
        offered = self.offer()["budgetAdjustment"]["comparison"]
        before = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, "budget")
        budgets = Budget.objects.count()
        response = self.chat(self.cid, text="왜 추천해?", answers=[reading(action="explain")])
        adjustment = response["messages"][-1]["content"]["budgetAdjustment"]
        self.assertEqual(adjustment["comparison"], offered)
        self.assertTrue(adjustment["detail"])
        self.assertFalse(adjustment["canApprove"])
        self.assertTrue(any(choice["value"].startswith("plan_") for choice in response["messages"][-1]["content"]["choices"]))
        self.assertEqual(Budget.objects.count(), budgets)
        after = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, "budget")
        for key in ("requests", "pending", "comparison"):
            self.assertEqual(after[key], before[key])

    def test_server_recommendation_survives_llm_recommendation_abstention(self):
        offered = self.offer(recommendation_index=None)
        comparison = offered['budgetAdjustment']['comparison']
        # 추천은 서버 계산으로 정한다. 모델이 추천을 정하지 않아도 그대로 남는다.
        self.assertEqual(comparison['recommendation']['planId'], 'chosen')
        proposal = self.select(offered)
        self.assertEqual(proposal['allocations']['FOOD'], 450000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_preview_explanation_keeps_the_card_and_opens_details_in_web_response(self):
        first = self.select(self.offer())
        budgets = Budget.objects.count()
        response = self.chat(self.cid, text="왜 이렇게 바꿨어?", answers=[{
            "action": "explain", "category": None, "amount": None, "amount_mode": None, "suggested_target": None}])
        adjustment = response["messages"][-1]["content"]["budgetAdjustment"]
        self.assertEqual(adjustment["proposal"]["proposalId"], first["proposalId"])
        self.assertTrue(adjustment["detail"])
        self.assertTrue(adjustment["canApprove"])
        self.assertEqual(Budget.objects.count(), budgets)

    def test_missing_history_uses_verified_budget_comparison_and_can_make_a_preview(self):
        self.ctx['diagnostic_inputs']['history'] = {}
        start = self.chat(choice='budget')
        cid = start['conversationId']
        response = self.chat(cid, text='식비를 5만 원 늘리고 싶어. 비교해줘', answers=[
            reading(requests=[item(amount=50000)], action='compare', sources=['SPARE']),
            draft(funding(SPARE=50000)), review()])
        reply = response['messages'][-1]['content']
        # 금액을 정한 요청은 소비 기록 없이도 예산 기준으로 계산하고, 고른 재원(여윳돈)만 쓴다.
        self.assertIn('comparison', reply['budgetAdjustment'])
        values = {c['value'] for c in reply['choices']}
        self.assertIn('plan_chosen', values)
        plan = reply['budgetAdjustment']['comparison']['plans'][0]
        self.assertEqual({t['from'] for t in plan['result']['transfers']}, {'SPARE'})
        self.assertEqual(Budget.objects.count(), 1)
        selected = self.chat(cid, choice={'value': 'plan_chosen', 'turn': reply['turn']})
        proposal = selected['messages'][-1]['content']['budgetAdjustment']['proposal']
        self.assertEqual(proposal['allocations']['FOOD'], 450000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_missing_current_spending_is_not_treated_as_zero_for_comparison(self):
        self.ctx['diagnostic_inputs']['history'] = {}
        self.ctx['spending_status'] = 'partial'
        start = self.chat(choice='budget')
        reply = self.chat(start['conversationId'], text='식비를 5만 원 늘리고 싶어. 비교해줘', answers=[
            reading(requests=[item(amount=50000)], action='compare', sources=['SPARE'])])['messages'][-1]['content']
        self.assertNotIn('comparison', reply['budgetAdjustment'])
        self.assertFalse(reply['budgetAdjustment']['canApprove'])
        self.assertFalse(any(c['value'].startswith('plan_') for c in reply['choices']))
        self.assertEqual(Budget.objects.count(), 1)

    def test_no_amount_and_no_history_preserves_savings_reserve_through_approval(self):
        self.ctx['diagnostic_inputs'] = {}
        self.ctx['allocations']['FOOD'] = 320000
        self.ctx['buckets']['SAVINGS'] = 500000
        self.ctx['bucket_floors']['SAVINGS'] = 0
        self.ctx['bucket_available']['SAVINGS'] = 500000
        self.ctx['bucket_status'].update(SPARE='unknown', CUSHION_MONTHLY='unknown')
        self.ctx['locks'] = {c: amount for c, amount in self.ctx['allocations'].items() if c != 'FOOD'}
        self.ctx['funding_amount'] = sum(self.ctx['allocations'].values()) + sum(self.ctx['buckets'].values()) + self.ctx['fixed_amount']
        self.cid = self.chat(choice='budget')['conversationId']
        content = self.chat(self.cid, text='식비 늘려줘', answers=[
            reading(requests=[item()], action='compare')])['messages'][-1]['content']
        # 재원과 관계없이 사용자가 정하지 않은 금액은 먼저 묻는다.
        self.assertEqual(content['budgetAdjustment']['phase'], 'amount')
        self.assertNotIn('comparison', content['budgetAdjustment'])
        content = self.chat(self.cid, text='5만원', answers=[
            reading(requests=[item(amount=50000)], reference='previous')])['messages'][-1]['content']
        plan = content['budgetAdjustment']['comparison']['plans'][0]
        self.assertEqual({t['from'] for t in plan['result']['transfers']}, {'SAVINGS'})
        self.assertIn('저축', ' '.join(plan['assessment']['cautions']))
        card = self.select(content)
        self.assertEqual(card['buckets']['SAVINGS'], 450000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')
        approved = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(approved.status_code, 200, approved.content)

    def test_no_automatic_example_explains_the_limit_without_an_example_notice(self):
        self.ctx['diagnostic_inputs']['history'] = {}
        self.ctx['ml_reference']['MEDICAL_HEALTH'] = self.ctx['allocations']['MEDICAL_HEALTH']
        start = self.chat(choice='budget')
        self.ctx['priorities']['MEDICAL_HEALTH'] = 4
        cid = start['conversationId']
        response = self.chat(cid, text='의료비 줄이는 방법을 비교해줘', answers=[
            reading(requests=[item('MEDICAL_HEALTH', direction='down')], action='compare')])
        reply = response['messages'][-1]['content']
        self.assertEqual(reply['text'], '의료·건강 예산을 얼마 줄일까요?')
        self.assertEqual(reply['budgetAdjustment']['amountInput'], {'category': 'MEDICAL_HEALTH', 'direction': 'down', 'maxAmount': 50000, 'beforeAmount': 50000})
        self.assertFalse(any('예시 금액' in notice for notice in reply.get('notices', [])))
        self.assertFalse(any(c['value'].startswith('plan_') for c in reply['choices']))
        self.assertEqual(Budget.objects.count(), 1)

        repeated = self.chat(cid, text='잘 모르겠는데', answers=[reading(action='recommend')])['messages'][-1]['content']
        self.assertEqual(repeated['text'], '의료·건강 예산을 얼마 줄일까요?')
        self.assertFalse(any('예시 금액' in notice for notice in repeated.get('notices', [])))

        response = self.chat(cid, text='의료·건강을 1만원 줄이고 싶어요', answers=[
            reading(requests=[item('MEDICAL_HEALTH', 10000, 'down', 'by')], action='compare')])
        reply = response['messages'][-1]['content']
        self.assertTrue(any(c['value'].startswith('plan_') for c in reply['choices']))
        self.assertNotIn('amountInput', reply['budgetAdjustment'])
        living = next(p for p in reply['budgetAdjustment']['comparison']['plans'] if p['id'] == 'living')
        self.assertEqual(living['status'], 'needs_destination')
        self.assertIsNone(living['result'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_reduction_input_limit_uses_budget_minus_spent_and_minimum_not_ml_reference(self):
        self.ctx['ml_reference']['EDUCATION'] = 50000
        for spent, minimum, maximum in ((0, 0, 50000), (12000, 20000, 30000), (45000, 0, 5000)):
            with self.subTest(spent=spent, minimum=minimum):
                self.ctx['spent']['EDUCATION'] = spent
                self.ctx['minimums']['EDUCATION'] = minimum
                cid = self.chat(choice='budget')['conversationId']
                response = self.chat(cid, text='교육 예산 줄여줘', answers=[
                    reading(requests=[item('EDUCATION', direction='down')], action='compare')])
                amount_input = response['messages'][-1]['content']['budgetAdjustment']['amountInput']
                self.assertEqual(amount_input['maxAmount'], maximum)
                self.assertEqual(amount_input['beforeAmount'], self.ctx['allocations']['EDUCATION'])
        self.assertEqual(Budget.objects.count(), 1)

    def test_explicit_amount_ignores_empty_placeholder_in_new_and_persisted_state(self):
        from chat.engine import ConversationStore, KEY
        blank = dict.fromkeys(("category", "amount", "amount_mode", "direction"))
        for persisted in (False, True):
            with self.subTest(persisted=persisted):
                cid = self.chat(choice="budget")["conversationId"]
                if persisted:
                    talk = Conversation.objects.get(pk=cid)
                    store = ConversationStore.load(talk.slots)
                    state = store.get_agent_state(KEY, "budget")
                    state["requests"] = [deepcopy(blank)]
                    store.save_agent_state(KEY, "budget", state)
                    talk.slots = store.dump()
                    talk.save(update_fields=["slots"])
                response = self.chat(cid, text="여윳돈에서 가져와 식비 예산을 1만 원 더 늘려줘", answers=[{
                    "reading": reading(requests=[item(amount=10000), deepcopy(blank)], sources=["SPARE"], destinations=["FOOD"]),
                    "reply": "말씀하신 조건으로 확인할게요."}])
                content = response["messages"][-1]["content"]["budgetAdjustment"]
                self.assertTrue(content["canApprove"])
                self.assertEqual(content["proposal"]["allocations"]["FOOD"], 410000)
                self.assertEqual(content["proposal"]["buckets"]["SPARE"], 390000)
                stored = ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(KEY, "budget")
                self.assertEqual(len(stored["requests"]), 1)

    def test_discussion_keeps_conditions_until_user_confirms_example_amount(self):
        start = self.chat(choice="budget")
        cid = start["conversationId"]
        first = self.chat(cid, text="식비가 부족해", answers=[{
            "reading": reading(requests=[item()], reference="previous"),
            "reply": "최근 식비에서 달라진 지출이 있었나요?"},
            {'plans': [{'funding': [{'source': 'SPARE', 'amount': 120000}]}], 'recommendation_index': None, 'reason_fact_ids': []},
            {'plans': [], 'recommendation_index': 0, 'reason_fact_ids': ['reserve', 'plan_0.preserved']}])["messages"][-1]["content"]
        self.assertNotIn("proposal", first["budgetAdjustment"])
        self.assertEqual(first['budgetAdjustment']['phase'], 'amount')
        second = self.chat(cid, text="요즘 외식이 늘었어", answers=[
            reading(reason="외식이 늘어남", reference="previous")])["messages"][-1]["content"]
        self.assertEqual(second['budgetAdjustment']['phase'], 'amount')
        offered = self.chat(cid, text="저축은 유지하고 여윳돈에서 가능한 금액으로 예시 보여줘", answers=[
            reading(sources=["SPARE"], excluded=["SAVINGS"], reference="previous", action='compare',
                    use_available=True)])["messages"][-1]["content"]
        self.assertNotIn("proposal", offered["budgetAdjustment"])
        plan = offered["budgetAdjustment"]["comparison"]["plans"][0]
        self.assertEqual({t['from'] for t in plan['result']['transfers']}, {'SPARE'})
        self.assertEqual(Budget.objects.count(), 1)
        selected = self.chat(cid, choice={'value': 'plan_chosen', 'turn': offered['turn']})['messages'][-1]['content']
        self.assertEqual(selected['budgetAdjustment']['phase'], 'amount')
        self.assertNotIn('proposal', selected['budgetAdjustment'])
        self.assertEqual(Budget.objects.count(), 1)
        selected = self.chat(cid, text='12만원 늘릴게', answers=[
            reading(requests=[item(amount=120000)], reference='previous')])['messages'][-1]['content']
        card = selected["budgetAdjustment"]["proposal"]
        self.assertEqual(card["allocations"]["FOOD"], 520000)
        self.assertEqual(card["buckets"]["SAVINGS"], 600000)
        self.assertEqual(Budget.objects.get(pk=card["proposalId"]).status, "validated")
        self.assertNotIn('compare', {choice['value'] for choice in selected['choices']})
        self.assertEqual(Budget.objects.count(), 2)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")

    def test_compare_select_refine_and_approve_only_direct_goal(self):
        content = self.offer()
        self.assertEqual(Budget.objects.count(), 1)
        first = self.select(content)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")
        response = self.chat(self.cid, text="쇼핑 2만 원 더 줄여줘", answers=[
            {"action": "refine_more", "category": "FASHION_SHOPPING", "amount": 20000,
             "amount_mode": "by", "suggested_target": None}])
        second = response["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        self.assertEqual(second["allocations"]["FASHION_SHOPPING"], 180000)
        self.assertEqual(second["allocations"]["FOOD"], 450000)
        self.assertEqual(Budget.objects.get(pk=first["proposalId"]).status, "expired")
        self.assertEqual(self.post(f"/api/budgets/proposals/{first['proposalId']}/approve/", {}).status_code, 409)
        result = self.post(f"/api/budgets/proposals/{second['proposalId']}/approve/", {"keepForNextCycle": True})
        self.assertEqual(result.status_code, 200, result.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets, {"FOOD": 450000})

    def test_unspecified_feedback_does_not_take_more_than_needed(self):
        for code, expected in [('FASHION_SHOPPING', 200000), ('CUSHION_MONTHLY', 150000)]:
            with self.subTest(code=code):
                first = self.select(self.offer())
                response = self.chat(self.cid, text='여기는 더 줄여도 돼', answers=[
                    {'action': 'refine_more', 'category': code, 'amount': None,
                     'amount_mode': None, 'suggested_target': 0}])
                content = response['messages'][-1]['content']
                card = content['budgetAdjustment']['proposal']
                self.assertEqual({**card['allocations'], **card['buckets']}[code], expected, content.get('text'))
                self.assertEqual(card['allocations']['FOOD'], 450000)
                if code == 'FASHION_SHOPPING':
                    self.assertEqual(card['proposalId'], first['proposalId'])
                else:
                    self.assertNotEqual(card['proposalId'], first['proposalId'])
                self.assertGreaterEqual(card['buckets']['CUSHION_MONTHLY'], 60000)

    def test_unspecified_feedback_keeps_category_and_bucket_reserves(self):
        self.ctx['diagnostic_inputs'] = {}
        self.ctx['bucket_available'] = dict(self.ctx['buckets'])
        for code, expected in [('FASHION_SHOPPING', 200000), ('CUSHION_MONTHLY', 60000)]:
            with self.subTest(code=code):
                self.cid = self.chat(choice='budget')['conversationId']
                offered = self.chat(self.cid, text='식비를 50만원 늘려줘', answers=[
                    reading(requests=[item(amount=500000)], action='compare'),
                    draft(funding(LIVING=50000, SAVINGS=150000, CUSHION_MONTHLY=100000, SPARE=200000)), review()
                ])['messages'][-1]['content']
                first = self.select(offered)
                response = self.chat(self.cid, text='여기는 더 줄여도 돼', answers=[
                    {'action': 'refine_more', 'category': code, 'amount': None,
                     'amount_mode': None, 'suggested_target': 0}])
                content = response['messages'][-1]['content']
                card = content['budgetAdjustment']['proposal']
                self.assertEqual({**card['allocations'], **card['buckets']}[code], expected, content.get('text'))
                self.assertEqual(card['allocations']['FOOD'], 900000)
                if code == 'FASHION_SHOPPING':
                    self.assertEqual(card['proposalId'], first['proposalId'])
                else:
                    self.assertNotEqual(card['proposalId'], first['proposalId'])

    def test_free_text_recommendation_shows_examples_before_selection(self):
        cid = self.chat(choice="budget")["conversationId"]
        response = self.chat(cid, text="식비를 5만 원 늘릴 추천안 좀 보여줘", answers=[
            {"reading": reading(requests=[item(amount=50000)], action="compare", sources=["SPARE"]), "reply": "원하는 조건으로 확인할게요."},
            {"plans": [{"funding": [{"source": "SPARE", "amount": 50000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}])
        result = response["messages"][-1]["content"]["budgetAdjustment"]
        self.assertIn("comparison", result)
        self.assertNotIn('proposal', result)
        self.assertEqual(result['comparison']['plans'][0]['result']['allocations']['FOOD'], 450000)
        self.assertEqual(Budget.objects.count(), 1)

    def test_first_new_request_after_initial_approval_uses_current_approved_budget(self):
        from chat.engine import ConversationStore, KEY
        self.base.status, self.base.approved_at = "validated", None
        self.base.save(update_fields=["status", "approved_at"])
        self.ctx.update(mode="INITIAL_ADJUSTMENT", source_status="validated")
        first = self.select(self.offer())
        result = self.post(f"/api/budgets/{first['proposalId']}/approve/", {})
        self.assertEqual(result.status_code, 200, result.content)
        self.ctx.update(base_budget_id=first["proposalId"], mode="NORMAL_ADJUSTMENT", source_status="approved",
                        allocations=deepcopy(first["allocations"]), buckets=deepcopy(first["buckets"]))
        self.ctx["bucket_available"]["SPARE"] = self.ctx["buckets"]["SPARE"]
        response = self.chat(self.cid, text="여윳돈에서 가져와 식비 예산을 1만 원 더 늘려줘", answers=[{
            "reading": reading(requests=[item(amount=10000)], sources=["SPARE"], reference="previous"),
            "reply": "말씀하신 조건으로 확인할게요."}])
        content = response["messages"][-1]["content"]["budgetAdjustment"]
        self.assertEqual(content["status"], "replied")
        second = content["proposal"]
        self.assertEqual(second["mode"], "NORMAL_ADJUSTMENT")
        self.assertEqual(second["allocations"]["FOOD"], 460000)
        self.assertEqual(second["buckets"]["SPARE"], 390000)
        self.assertNotEqual(second["taskRef"], first["taskRef"])
        self.assertEqual(str(Budget.objects.get(pk=second["proposalId"]).base_budget_id), first["proposalId"])
        state = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, "budget")
        self.assertEqual(state["llm_call_count"], 1)
        self.assertEqual(state["reference_context"]["base_budget_id"], first["proposalId"])
        self.assertEqual(state["reference_context"]["ml_reference"], self.ctx["ml_reference"])
        self.assertEqual(Budget.objects.get(pk=first["proposalId"]).status, "approved")

    def test_changed_spending_expires_before_feedback_calls_model(self):
        first = self.select(self.offer())
        self.model.reset_mock()
        self.ctx["spent"]["FOOD"] = 130000
        response = self.chat(self.cid, text="쇼핑 더 줄여도 돼")
        self.assertEqual(response["messages"][-1]["content"]["budgetAdjustment"]["proposal"]["state"], "expired")
        self.model.assert_not_called()

    def test_unlock_consent_survives_comparison_and_revision_but_applies_only_on_approval(self):
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.base.cycle,
            category_id="FOOD", locked=True, locked_amount=400000, revision_no=2)
        self.ctx.update(locks={"FOOD": 400000}, lock_revisions={"FOOD": 2})
        self.cid = self.chat(choice="budget")["conversationId"]
        asked = self.chat(self.cid, text="식비 5만 원 늘려줘", answers=[
            reading(requests=[item(amount=50000)], sources=["SPARE", "LIVING"])])
        self.assertIn("잠겨", str(asked))
        offered = self.chat(self.cid, text="잠금 풀고 비교해줘", answers=[
            reading(unlock=True, action="compare"),
            {"plans": [{"funding": [{"source": "SPARE", "amount": 50000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}])
        first = self.select(offered["messages"][-1]["content"])
        revised = self.chat(self.cid, text="쇼핑 2만 원 더 줄여줘", answers=[
            {"action": "refine_more", "category": "FASHION_SHOPPING", "amount": 20000,
             "amount_mode": "by", "suggested_target": None}])
        card = revised["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        setting.refresh_from_db()
        self.assertTrue(setting.locked)
        self.assertEqual(setting.revision_no, 2)
        self.assertEqual(card["allocations"]["FOOD"], 450000)
        self.assertEqual(Budget.objects.get(pk=first["proposalId"]).status, "expired")
        result = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(result.status_code, 200, result.content)
        setting.refresh_from_db()
        self.assertFalse(setting.locked)
        self.assertEqual(setting.revision_no, 3)

    def test_analysis_cannot_be_approved_after_source_availability_changes(self):
        card = self.select(self.offer())
        self.ctx["bucket_available"]["SPARE"] = 10000
        reply = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
        self.assertEqual(reply.status_code, 409)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")

    @unittest.skipUnless(os.environ.get("BUDGET_LIVE_SYNTHETIC_TEST") == "1", "Opt-in real LLM; synthetic fixture only")
    @override_settings(CHAT_LLM="live")
    def test_live_synthetic_comparison_feedback_and_final_approval(self):
        from chat.llm.live import LiveReading
        from .ai_consent import PURPOSES, document, document_hash
        for purpose in PURPOSES["adjustment"]:
            doc = document(purpose)
            Consent.objects.create(user=self.user, purpose=purpose, document_version=doc["version"], document_hash=document_hash(doc))
        self.reader.stop()
        live = LiveReading(30, None)
        self.enterContext(patch("chat.engine.reading", return_value=live))
        start = self.say(choice="budget")
        cid = start["conversationId"]
        first = self.say(cid, text="식비가 부족해")["messages"][-1]["content"]
        compared = first
        if 'comparison' not in first['budgetAdjustment']:
            next_input = ({'choice': {'value': 'compare', 'turn': first['turn']}}
                          if any(c['value'] == 'compare' for c in first['choices'])
                          else {'text': '가능한 조정 방법을 비교해줘'})
            compared = self.say(cid, **next_input)['messages'][-1]['content']
        self.assertIn('comparison', compared['budgetAdjustment'], compared)
        comparison = compared["budgetAdjustment"]["comparison"]
        selected = comparison["recommendation"]["planId"]
        self.assertIsNotNone(selected)
        preview = self.say(cid, choice=self.plan_choice(compared, selected))["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        revision_reply = self.say(cid, text="쇼핑은 더 줄여도 돼")["messages"][-1]["content"]
        revision = revision_reply["budgetAdjustment"]["proposal"]
        self.assertEqual(revision["allocations"]["FOOD"], preview["allocations"]["FOOD"])
        self.assertEqual(revision["allocations"]["FASHION_SHOPPING"], preview["allocations"]["FASHION_SHOPPING"])
        revision_reply = self.say(cid, text="식비 증액은 유지하고 쇼핑에서 3만원을 가져오는 안으로 바꿔줘")["messages"][-1]["content"]
        revision = revision_reply["budgetAdjustment"]["proposal"]
        self.assertEqual(revision["allocations"]["FOOD"], preview["allocations"]["FOOD"])
        self.assertEqual(revision["allocations"]["FASHION_SHOPPING"], self.ctx["allocations"]["FASHION_SHOPPING"] - 30000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, "approved")
        result = self.post(f"/api/budgets/proposals/{revision['proposalId']}/approve/", {"keepForNextCycle": True})
        self.assertEqual(result.status_code, 200, result.content)
        self.profile.refresh_from_db()
        self.assertEqual(set(self.profile.budget_category_targets), {"FOOD"})
        self.assertTrue(all(a["kind"] == "ok" for a in live.client.attempts))
        print("SYNTHETIC_SERVICE_LLM_CALLS", len(live.client.attempts))


@override_settings(CHAT_ENGINE="router", CHAT_LLM="live", BUDGET_DESIGN_ENABLED=True, BUDGET_DESIGN_EXTERNAL_ENABLED=True)
class BudgetConsentTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="budget-consent@example.test")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def grant(self, scope):
        path = "/api/budgets/ai-consent/" if scope == "design" else "/api/budgets/adjustment-ai-consent/"
        state = self.client.get(path).json()
        body = {"agreed": True, "healthAgreed": True,
                **{k: state[k] for k in ("documentVersion", "documentHash", "healthDocumentVersion", "healthDocumentHash")}}
        result = self.client.put(path, body, format="json")
        self.assertEqual(result.status_code, 200, result.content)
        return path, body

    def test_design_permission_allows_routing_but_not_adjustment(self):
        from chat.llm.fake import FakeReading
        from chat.router_core.reading import PROMPT
        self.grant("design")
        reader = Mock(wraps=FakeReading(), explains=False)
        with patch("chat.engine.reading", return_value=reader):
            response = self.client.post("/api/chat/messages/", {"conversationId": None, "text": "식비 예산 줄여줘"}, format="json")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "budget_adjust_consent_required")
            self.assertEqual([call.args[0] for call in reader.call_args_list], [PROMPT])
            reader.reset_mock()
            denied = self.client.post("/api/chat/messages/", {"conversationId": None, "choice": "budget"}, format="json")
            self.assertEqual(denied.status_code, 403)
            reader.assert_not_called()
        self.assertTrue(has_consent(self.user, "design"))
        self.assertFalse(has_consent(self.user, "adjustment"))

    def test_new_spending_chat_works_without_adjustment_consent(self):
        from chat.llm.fake import FakeReading
        with patch("chat.engine.reading", return_value=FakeReading()):
            response = self.client.post("/api/chat/messages/", {"conversationId": None, "text": "이번 달 얼마 썼어?"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["route"]["intent"], "spending")

    def test_separate_explicit_checkboxes_hash_and_revocation(self):
        path, body = self.grant("adjustment")
        self.assertTrue(has_consent(self.user))
        invalid = self.client.put(path, {**body, "documentHash": "old"}, format="json")
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(invalid.json()["error"]["code"], "document_outdated")
        invalid = self.client.put(path, {**body, "healthAgreed": False}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.delete(path, {"confirm": True}, format="json").status_code, 200)
        self.assertFalse(has_consent(self.user))
        self.assertEqual(Consent.objects.filter(user=self.user, revoked_at__isnull=False).count(), 2)

    def test_disabled_features_reject_grants_and_allow_old_consent_revocation(self):
        for scope, flags in (("design", {"BUDGET_DESIGN_ENABLED": False}),
                             ("design", {"BUDGET_DESIGN_EXTERNAL_ENABLED": False}),
                             ("adjustment", {"CHAT_LLM": "fake"})):
            with self.subTest(scope=scope, flags=flags):
                path, body = self.grant(scope)
                Consent.objects.filter(user=self.user, purpose=consent_state(self.user, scope)["purpose"]).update(document_hash="old")
                with override_settings(**flags):
                    state = self.client.get(path).json()
                    self.assertFalse(state["available"])
                    self.assertTrue(state["canRevoke"])
                    before = Consent.objects.count()
                    response = self.client.put(path, body, format="json")
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json()["error"]["code"], "budget_ai_unavailable")
                    self.assertEqual(Consent.objects.count(), before)
                    response = self.client.delete(path, {"confirm": True}, format="json")
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(response.json()["canRevoke"])


@override_settings(CHAT_LLM="fake", BUDGET_ADJUST_ALLOCATOR=proportional_group_reduction)
class ConcurrentAdjustmentApprovalTest(TransactionTestCase):
    serialized_rollback = True
    def test_two_conversations_cannot_spend_the_same_spare_twice(self):
        from users.models import FinancialProfile
        from .adjustment_context import AdjustmentContextError
        from .adjustment_service import AdjustmentAdapter
        from .api import lock_user
        user = get_user_model().objects.create_user(email="parallel-adjustment@example.test")
        FinancialProfile.objects.create(user=user, payday=1, living_budget=1000000, savings_floor=600000,
                                        cushion_monthly=200000, monthly_take_home_income=3000000)
        today = timezone.localdate()
        cycle = BudgetCycle.objects.create(user=user, starts_on=today.replace(day=1), ends_before=today.replace(year=today.year + 1, month=1, day=1))
        base = Budget.objects.create(user=user, cycle=cycle, version_no=1, status="approved", funding_amount=3000000,
                                     approved_at=timezone.now())
        ctx = context()
        ctx.update(base_budget_id=str(base.pk), cycle_id=str(cycle.pk), spending_status="complete")
        BudgetItem.objects.bulk_create([BudgetItem(user=user, budget=base, bucket="living", category_id=c, allocated_amount=n)
                                       for c, n in ctx["allocations"].items()] + [
            BudgetItem(user=user, budget=base, bucket=b, allocated_amount=n) for b, n in (("fixed", 800000), ("savings", 600000), ("cushion", 200000))])
        def current(*args, **kwargs):
            if Budget.objects.get(pk=base.pk).status != "approved":
                raise AdjustmentContextError("expired")
            return deepcopy(ctx)
        barrier = Barrier(2)
        def approve(number, proposal):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user)
                barrier.wait(timeout=10)
                reply = client.post(f"/api/budgets/proposals/{proposal}/approve/", {}, format="json", HTTP_IDEMPOTENCY_KEY=f"parallel-{number}")
                return reply.status_code
            finally:
                connections.close_all()
        with patch("budgets.adjustment_service.get_adjustment_context", side_effect=current), patch("budgets.overview.budget_data", return_value={}):
            proposals = []
            for _ in range(2):
                talk = Conversation.objects.create(user=user)
                adapter = AdjustmentAdapter(user, conversation=talk)
                inp = adapter.get_context()
                card = adapter.propose(inp, adapter.simulate(inp, envelope(item(amount=250000), sources=["SPARE"])), scope="cycle")
                with transaction.atomic():
                    lock_user(user)
                    adapter.flush(talk)
                proposals.append(card["proposal_id"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda pair: approve(*pair), enumerate(proposals)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(Budget.objects.filter(user=user, status="approved").count(), 1)
        self.assertEqual(Budget.objects.filter(user=user, status="expired").count(), 1)
