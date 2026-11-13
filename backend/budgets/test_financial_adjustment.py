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


@override_settings(CHAT_ENGINE="router", CHAT_LLM="fake", CHAT_TODAY="2026-11-10",
                   BUDGET_ADJUST_ALLOCATOR=proportional_group_reduction)
class FinancialAdjustmentApiTest(TestCase):
    post, say = api_fixtures.BudgetAdjustmentApiTest.post, api_fixtures.BudgetAdjustmentApiTest.say

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
            reading(requests=[item(amount=100000)], action='compare'),
            draft(funding(LIVING=25000, SAVINGS=25000, CUSHION_MONTHLY=25000, SPARE=25000)), review()
        ])['messages'][-1]['content']
        selected = self.chat(cid, choice={'value': 'financial_0', 'turn': reply['turn']})['messages'][-1]['content']
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
            selected = self.say(cid, choice={'value': 'financial_' + plan['id'], 'turn': reply['turn']})['messages'][-1]['content']
            self.assertEqual(selected['budgetAdjustment']['proposal']['allocations']['FOOD'], 1150000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_next_cycle_income_funds_education_without_changing_current_budget(self):
        self.ctx.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='다음달 교육비 50만원 더 나가', answers=[
            reading(requests=[item('EDUCATION', 500000)], scope='next_cycle')])
        reply = self.chat(cid, text='다음달부터 월급 50만원 늘어서 그것만 추가해', answers=[
            reading(scope='next_cycle', income_change={'amount': 500000, 'mode': 'by', 'direction': 'up'},
                    sources=['SPARE'], reference='previous')])['messages'][-1]['content']
        self.assertTrue(reply['budgetAdjustment']['canApprove'], reply)
        card = reply['budgetAdjustment']['proposal']
        self.assertEqual(card['effectiveOn'], '2026-11-25')
        self.assertEqual(card['incomeChange'], {'before': 3000000, 'after': 3500000})
        self.assertEqual(card['allocations']['EDUCATION'], 550000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    @unittest.skipUnless(os.environ.get('BUDGET_LIVE_SYNTHETIC_TEST') == '1', 'opt-in synthetic LLM')
    def test_live_no_amount_sparse_history_still_offers_protected_combination(self):
        from chat.llm.live import LiveReading
        self.ctx['diagnostic_inputs'] = {}
        self.ctx['bucket_available'] = dict(self.ctx['buckets'])
        cid = self.chat(choice='budget')['conversationId']
        with patch('chat.engine.reading', return_value=LiveReading(12, None)):
            reply = self.say(cid, text='식비 늘려줘')['messages'][-1]['content']
            comparison = reply['budgetAdjustment']['comparison']
            self.assertEqual(comparison['basisKind'], 'budget_example')
            self.assertFalse(comparison['amountWasGiven'])
            plan = next(p for p in comparison['plans'] if p['status'] == 'ready' and len({t['from'] for t in p['result']['transfers']}) > 1)
            for code, before in self.ctx['buckets'].items():
                self.assertGreaterEqual(plan['result']['buckets'][code], (before * 3 + 9) // 10)
            selected = self.say(cid, choice={'value': 'financial_' + plan['id'], 'turn': reply['turn']})['messages'][-1]['content']
            card = selected['budgetAdjustment']['proposal']
            approved = self.post(f"/api/budgets/proposals/{card['proposalId']}/approve/", {})
            self.assertEqual(approved.status_code, 200, approved.content)

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
            reply = self.say(cid, text='다음달부터 월급 50만원 늘어서 그냥 그것만 추가해')['messages'][-1]['content']
            self.assertTrue(reply['budgetAdjustment']['canApprove'], reply)
            card = reply['budgetAdjustment']['proposal']
            self.assertEqual(card['allocations']['EDUCATION'], 550000)
            self.assertEqual(card['incomeChange']['after'], 3500000)
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
                self.assertEqual(third['budgetAdjustment']['phase'], 'financial_data', third)
                final = self.say(cid, text='여윳돈은 10만원 남겨줘')['messages'][-1]['content']
            card = final['budgetAdjustment']['proposal']
            self.assertEqual(card['allocations']['TRAVEL_LODGING'], 300000)
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
        self.reader = patch("chat.engine.reading", return_value=self.model)
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
        reply = self.chat(self.cid, choice={"value": "financial_0", "turn": content["turn"]})
        return reply["messages"][-1]["content"]["budgetAdjustment"]["proposal"]

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
            reading(requests=[item('SAVINGS', 300000)], action='compare'),
            draft(funding(SPARE=200000, CUSHION_MONTHLY=100000)), review()])['messages'][-1]['content']
        self.assertIn('financial_0', {c['value'] for c in reply['choices']})
        chosen = self.chat(cid, choice={'value': 'financial_0', 'turn': reply['turn']})['messages'][-1]['content']
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
        self.assertIn('어느 지출에서 돈이 부족', content['text'])
        reply = self.chat(cid, text='전체 생활비', answers=[reading(selected_choice='LIVING', reference='previous')])
        self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'amount')
        reply = self.chat(cid, text='5만원 늘려줘', answers=[reading(requests=[item('LIVING', 50000, 'up')], reference='previous')])
        self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'sources')
        reply = self.chat(cid, text='여윳돈에서', answers=[reading(selected_choice='SPARE', reference='previous')])
        content = reply['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['phase'], 'destinations')
        self.assertNotIn('줄인 돈', json.dumps(content, ensure_ascii=False))
        self.assertIn('생활비에 더할 돈', content['text'])
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
        self.assertIn('120,000원', current['guidance']['context'])
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
        self.assertEqual(response['messages'][-1]['content']['budgetAdjustment']['phase'], 'sources')

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
        live = LiveReading(10, None)
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
                        self.assertIn('저축 · 이번에 저축할 예산을 줄여 생활비에 최대 150,000원', guidance['message'])
                        self.assertIn('여윳돈 · 먼저 남길 금액을 정하면', guidance['message'])
                    else:
                        self.assertIn('저축', guidance['message'])
                        self.assertIn('월 머니쿠션', guidance['message'])
                        self.assertNotIn('돈을 가져올 항목을 골라 주세요', guidance['message'])
                    self.assertNotIn('어디에서 가져올까요', guidance['message'])
                    self.assertFalse(state.get('sources'))
            reply = self.say(cid, text='모르겠어. 어떤 기준으로 골라야 해?')
            content = reply['messages'][-1]['content']
            self.assertEqual(content['budgetAdjustment']['phase'], 'sources', content)
            self.assertIn('남길 여윳돈 정하기', content['budgetAdjustment']['guidance']['message'])
            self.assertNotIn('돈을 찾지 못했어요', content['text'])
            reply = self.say(cid, text='그럼 조정 예시를 비교해줘')
            content = reply['messages'][-1]['content']
            self.assertEqual(content['budgetAdjustment']['phase'], 'destinations', content)
            self.assertNotIn('돈을 찾지 못했어요', content['text'])
            reply = self.say(cid, choice={'value': 'FOOD', 'turn': content['turn']})
            self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'comparison')
        self.assertEqual(Budget.objects.count(), 1)
        self.assertEqual(live.usage()['failed'], 0)

    def test_funding_inspection_keeps_goal_and_buttons_continue_to_destination(self):
        cid = self.chat(choice='budget')['conversationId']
        self.chat(cid, text='생활비를 5만원 늘리고 싶어요', answers=[reading(requests=[item('LIVING', 50000)])])
        reply = self.chat(cid, text='어디서 가져올 수 있어?', answers=[reading(action='inspect', reference='previous')])
        content = reply['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['guidance']['kind'], 'explanation')
        self.assertIn('최대 150,000원', content['budgetAdjustment']['guidance']['message'])
        reply = self.chat(cid, choice={'value': 'SAVINGS', 'turn': content['turn']})
        self.assertEqual(reply['messages'][-1]['content']['budgetAdjustment']['phase'], 'destinations')
        self.assertEqual(Budget.objects.count(), 1)

    def test_large_increase_warns_without_blocking_and_correction_clears_warning(self):
        self.ctx['monthly_income'] = 3000000
        cid = self.chat(choice='budget')['conversationId']
        reply = self.chat(cid, text='생활비를 90만원 늘려줘', answers=[reading(requests=[item('LIVING', 900000)])])
        content = reply['messages'][-1]['content']
        self.assertIn('30% 이상', content['budgetAdjustment']['increaseWarning'])
        self.assertEqual(content['budgetAdjustment']['phase'], 'sources')
        self.assertTrue(content['choices'])
        reply = self.chat(cid, text='아니 5만원만', answers=[reading(requests=[item('LIVING', 50000)], reference='previous')])
        self.assertNotIn('increaseWarning', reply['messages'][-1]['content']['budgetAdjustment'])
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
        self.assertEqual(content['budgetAdjustment']['phase'], 'financial_selection', content)
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
        reply = self.chat(cid, choice={'value': 'financial_0', 'turn': content['turn']})
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
        opened = self.chat(cid, choice={'value': 'manual_living', 'turn': content['turn']})
        self.assertEqual(opened['messages'][-1]['content']['budgetAdjustment']['amountInput']['direction'], 'up')
        amount = self.chat(cid, text='교통비 5만원 늘려줘', answers=[reading(requests=[item('TRANSPORT_CAR', 50000)])])
        self.assertEqual(amount['messages'][-1]['content']['budgetAdjustment']['distributionInput']['amount'], 50000)

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
        self.assertEqual(before['pending']['kind'], 'financial_selection')
        response = self.chat(self.cid, text='뒤로', answers=[reading(action='back')])
        after = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, 'budget')
        self.assertEqual(len(after['history']), len(before['history']) - 1)
        self.assertNotEqual(after['pending']['kind'], 'financial_selection')
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
        first = self.chat(cid, text='교육 예산을 5만원 줄여줘', answers=[reading(requests=[item('EDUCATION', 50000, 'down')])])
        content = first['messages'][-1]['content']
        selected = self.chat(cid, choice={'value': 'plan_living', 'turn': content['turn']})
        adjustment = selected['messages'][-1]['content']['budgetAdjustment']
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
            self.assertIn('추천해요', reply['budgetAdjustment']['recommendationAdvice']['title'])
            self.assertTrue(reply['budgetAdjustment']['recommendationAdvice']['reasons'])
            self.assertEqual(len(reply['budgetAdjustment']['comparison']['plans']), 1)
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
        self.assertIn('comparison', first['messages'][-1]['content']['budgetAdjustment'])
        response = self.chat(cid, text='저축은 그대로 두고 남겨둔 돈에서 보태면?', answers=[
            reading(sources=['SPARE'], excluded=['SAVINGS'], reference='previous')])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['phase'], 'financial_data')
        self.assertIn('여윳돈을 최소 얼마', response['messages'][-1]['content']['text'])
        self.assertIn('식비', adjustment['requestSummary'])
        payload = json.loads(self.model.call_args.args[1])
        self.assertEqual(payload['previous']['requests'][0]['category'], 'FOOD')
        self.assertEqual(payload['conversation'][-1]['user'], '식비가 조금 모자라')
        self.assertEqual(Budget.objects.count(), 1)

    def test_multiple_donors_keep_each_amount_and_server_sums_the_destination(self):
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='쇼핑에서 2만원, 교통에서 3만원 빼서 식비에 보태줘', answers=[
            reading(requests=[item('FASHION_SHOPPING', 20000, 'down'), item('TRANSPORT_CAR', 30000, 'down')],
                    destinations=['FOOD'])])
        adjustment = response['messages'][-1]['content']['budgetAdjustment']
        self.assertEqual(adjustment['proposal']['allocations']['FASHION_SHOPPING'], 180000)
        self.assertEqual(adjustment['proposal']['allocations']['TRANSPORT_CAR'], 70000)
        self.assertEqual(adjustment['proposal']['allocations']['FOOD'], 450000)
        self.assertIn('20,000원', adjustment['requestSummary'])
        self.assertIn('30,000원', adjustment['requestSummary'])
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_semantic_plan_selection_uses_the_offered_choice_without_saving(self):
        self.offer()
        response = self.chat(self.cid, text='남겨둔 돈에서 보태는 쪽으로 볼게', answers=[
            reading(selected_choice='financial_0', approval=True, reference='previous')])
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
        self.assertEqual(self.model.call_count, 3)

    def test_unknown_amount_has_the_same_examples_with_or_without_compare_action(self):
        comparisons = []
        for action in (None, 'compare'):
            cid = self.chat(choice='budget')['conversationId']
            response = self.chat(cid, text='의료비는 좀 줄이고 싶은데', answers=[
                reading(requests=[item('MEDICAL_HEALTH', None, 'down', None)], action=action)])
            comparisons.append(response['messages'][-1]['content']['budgetAdjustment']['comparison'])
        self.assertEqual(comparisons[0], comparisons[1])
        self.assertEqual(Budget.objects.count(), 1)

    def test_no_amount_comparison_keeps_the_named_destination(self):
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='의료비 좀 줄여서 교통에 넣고 싶은데 얼마나 가능해?', answers=[
            reading(requests=[item('MEDICAL_HEALTH', None, 'down', None)], destinations=['TRANSPORT_CAR'], action='compare')])
        comparison = response['messages'][-1]['content']['budgetAdjustment']['comparison']
        plans = [p for p in comparison['plans'] if p.get('result')]
        self.assertEqual(len(plans), 1)
        self.assertTrue(all(t['to'] == 'TRANSPORT_CAR' for t in plans[0]['result']['transfers']))
        self.assertEqual(Budget.objects.count(), 1)

    def test_planner_failure_preserves_the_interpreted_request_for_retry(self):
        from chat.engine import ConversationStore, KEY
        cid = self.chat(choice='budget')['conversationId']
        response = self.chat(cid, text='식비를 좀 넉넉하게 잡아줘', answers=[reading(requests=[item()])])
        content = response['messages'][-1]['content']
        self.assertEqual(content['budgetAdjustment']['status'], 'awaiting_input')
        self.assertTrue(content['budgetAdjustment']['comparison']['plans'])
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
        self.assertIn("financial_0", {choice["value"] for choice in response["messages"][-1]["content"]["choices"]})
        self.assertEqual(Budget.objects.count(), budgets)
        after = ConversationStore.load(Conversation.objects.get(pk=self.cid).slots).get_agent_state(KEY, "budget")
        for key in ("requests", "pending", "financial_offer"):
            self.assertEqual(after[key], before[key])

    def test_server_recommendation_survives_llm_recommendation_abstention(self):
        offered = self.offer(recommendation_index=None)
        comparison = offered['budgetAdjustment']['comparison']
        self.assertEqual(comparison['recommendation']['planId'], '0')
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
        self.assertIn('현재 예산으로 계산', reply['budgetAdjustment']['comparison']['notice'])
        self.assertIn('예측한 결과는 아니', reply['budgetAdjustment']['comparison']['notice'])
        self.assertFalse(any('예시 금액' in notice for notice in reply.get('notices', [])))
        self.assertIn('comparison', reply['budgetAdjustment'])
        values = {c['value'] for c in reply['choices']}
        self.assertIn('financial_0', values)
        self.assertNotIn('plan_living', values)
        self.assertEqual(Budget.objects.count(), 1)
        selected = self.chat(cid, choice={'value': 'financial_0', 'turn': reply['turn']})
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
            reading(requests=[item()], action='compare'), draft(funding(SAVINGS=320000)), review()
        ])['messages'][-1]['content']
        comparison = content['budgetAdjustment']['comparison']
        self.assertEqual(comparison['basisKind'], 'budget_example')
        self.assertFalse(comparison['amountWasGiven'])
        plan = comparison['plans'][0]
        self.assertEqual(plan['result']['buckets']['SAVINGS'], 180000)
        self.assertEqual(plan['maximum'], 320000)
        self.assertEqual({t['from'] for t in plan['result']['transfers']}, {'SAVINGS'})
        card = self.select(content)
        self.assertEqual(card['buckets']['SAVINGS'], 180000)
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
        self.assertIn('50,000원', reply['budgetAdjustment']['explanation']['why'])
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
        self.assertEqual(living['result']['allocations']['MEDICAL_HEALTH'], 40000)
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

    def test_discussion_keeps_conditions_and_shows_examples_without_automatic_selection(self):
        start = self.chat(choice="budget")
        cid = start["conversationId"]
        first = self.chat(cid, text="식비가 부족해", answers=[{
            "reading": reading(requests=[item()], reference="previous"),
            "reply": "최근 식비에서 달라진 지출이 있었나요?"},
            {'plans': [{'funding': [{'source': 'SPARE', 'amount': 120000}]}], 'recommendation_index': None, 'reason_fact_ids': []},
            {'plans': [], 'recommendation_index': 0, 'reason_fact_ids': ['reserve', 'plan_0.preserved']}])["messages"][-1]["content"]
        self.assertNotIn("proposal", first["budgetAdjustment"])
        self.assertIn("comparison", first["budgetAdjustment"])
        second = self.chat(cid, text="요즘 외식이 늘었어", answers=[
            reading(reason="외식이 늘어남", reference="previous")])["messages"][-1]["content"]
        self.assertEqual(second['budgetAdjustment']['comparison'], first['budgetAdjustment']['comparison'])
        third = self.chat(cid, text="저축은 유지하고 여윳돈을 쓰고 싶어", answers=[
            reading(sources=["SPARE"], excluded=["SAVINGS"], reference="previous")])["messages"][-1]["content"]
        self.assertNotIn("proposal", third["budgetAdjustment"])
        self.assertNotIn("comparison", third["budgetAdjustment"])
        self.assertEqual(Budget.objects.count(), 1)
        self.assertEqual(third['budgetAdjustment']['phase'], 'financial_data')
        offered = self.chat(cid, text="20만원은 남겨줘", answers=[
            {'value': 200000},
            {"plans": [{"funding": [{"source": "SPARE", "amount": 120000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}
        ])["messages"][-1]["content"]
        self.assertIn("comparison", offered["budgetAdjustment"])
        self.assertNotIn('proposal', offered['budgetAdjustment'])
        self.assertEqual(Budget.objects.count(), 1)
        selected = self.chat(cid, choice={'value': 'financial_0', 'turn': offered['turn']})['messages'][-1]['content']
        card = selected["budgetAdjustment"]["proposal"]
        self.assertEqual(card["allocations"]["FOOD"], 520000)
        self.assertEqual(card["buckets"]["SAVINGS"], 600000)
        self.assertEqual(Budget.objects.get(pk=card["proposalId"]).status, "validated")
        compared = self.chat(cid, choice={"value": "compare", "turn": selected["turn"]}, answers=[
            {"plans": [{"funding": [{"source": "SPARE", "amount": 120000}]}], "recommendation_index": None, "reason_fact_ids": []},
            {"plans": [], "recommendation_index": 0, "reason_fact_ids": ["reserve", "plan_0.preserved"]}
        ])["messages"][-1]["content"]
        self.assertIn("comparison", compared["budgetAdjustment"])
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
        live = LiveReading(10, None)
        self.enterContext(patch("chat.engine.reading", return_value=live))
        start = self.say(choice="budget")
        cid = start["conversationId"]
        first = self.say(cid, text="식비가 부족해")["messages"][-1]["content"]
        compared = first if 'comparison' in first['budgetAdjustment'] else self.say(
            cid, choice={"value": "compare", "turn": first["turn"]})["messages"][-1]["content"]
        comparison = compared["budgetAdjustment"]["comparison"]
        selected = comparison["recommendation"]["planId"]
        self.assertIsNotNone(selected)
        preview = self.say(cid, choice={"value": "financial_" + selected, "turn": compared["turn"]})["messages"][-1]["content"]["budgetAdjustment"]["proposal"]
        revision_reply = self.say(cid, text="쇼핑은 더 줄여도 돼")["messages"][-1]["content"]
        revision = revision_reply["budgetAdjustment"]["proposal"]
        self.assertEqual(revision["allocations"]["FOOD"], preview["allocations"]["FOOD"])
        self.assertLess(revision["allocations"]["FASHION_SHOPPING"], preview["allocations"]["FASHION_SHOPPING"], revision_reply.get('text'))
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
