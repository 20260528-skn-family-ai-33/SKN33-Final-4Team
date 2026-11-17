"""Budget basis questions and early adjustment steps use the real message API."""
import json

from django.test import TestCase, override_settings
from unittest.mock import patch

from budgets.models import Budget
from budgets.budget_explanation import explain_current_budget
from chat.test_agent_collab import CollabFixture, SETTINGS
from django.utils import timezone


@override_settings(**SETTINGS)
class BudgetQuestionTest(CollabFixture, TestCase):
    def test_medical_reduction_is_an_adjustment_without_premature_plan_buttons(self):
        for text in ('의료비 줄이고싶어', '의료비 줄이고 싶다고'):
            with self.subTest(text=text):
                self.cid = None
                data, reply = self.say(text)
                self.assertEqual(data['agent'], 'budget')
                adjustment = reply['budgetAdjustment']
                self.assertEqual(adjustment['phase'], 'amount')
                self.assertEqual(adjustment['amountInput']['category'], 'MEDICAL_HEALTH')
                self.assertEqual(adjustment['amountInput']['direction'], 'down')
                self.assertIn('의료·건강', adjustment['guidance']['context'])
                self.assertNotIn('comparison', adjustment)
                self.assertNotIn('proposal', adjustment)
                self.assertEqual(self.budget_state()['requests'][0]['category'], 'MEDICAL_HEALTH')
                self.assertEqual(self.budget_state()['requests'][0]['direction'], 'down')
                self.assertNotIn('recommend', {c['value'] for c in reply['choices']})
                self.assertFalse({'추천안 보기', '비교하기'} & {c['label'] for c in reply['choices']})
                self.assertNotIn('추천안 보기나 비교하기', str(reply))
        self.assertEqual(Budget.objects.count(), 1)

    def test_medical_reduction_can_leave_spending_analysis(self):
        self.say(choice='spending')
        data, _ = self.say('의료비 줄이고싶어')
        self.assertEqual(data['agent'], 'budget')
        self.assertEqual(self.budget_state()['requests'][0]['direction'], 'down')

    def test_medical_reduction_waits_for_a_selected_destination_before_preview(self):
        _, reply = self.say('의료비를 1만 원으로 줄여줘')
        comparison = reply['budgetAdjustment']['comparison']
        plan = next(p for p in comparison['plans'] if p['id'] == 'living')
        self.assertEqual(plan['status'], 'needs_destination')
        self.assertIsNone(plan.get('result'))
        self.assertEqual(Budget.objects.count(), 1)
        _, selected = self.pick(reply, 'plan_living')
        self.assertEqual(selected['budgetAdjustment']['phase'], 'distribution')
        self.assertEqual(selected['budgetAdjustment']['distributionInput']['source'], 'MEDICAL_HEALTH')
        self.assertEqual(selected['budgetAdjustment']['distributionInput']['amount'], 40000)
        self.assertNotIn('proposal', selected['budgetAdjustment'])
        self.assertEqual(Budget.objects.count(), 1)
        from chat.llm.fake import BUDGET_SAID
        from budgets.adjustment_core.financial_conversation import DIALOGUE_PROMPT
        text = '의료비에서 줄인 4만원을 식비에 넣어줘'

        def distribution_reading(system, user, **kwargs):
            response = self.llm(system, user, **kwargs)
            if system == DIALOGUE_PROMPT:
                response = json.loads(response)
                response['reading'].update(action='distribute', reference='previous',
                                           sources=[], destinations=[], excluded=[], funding_scope=None, use_available=None)
                return json.dumps(response, ensure_ascii=False)
            return response

        with patch.dict(BUDGET_SAID, {text: [
            {'category': 'MEDICAL_HEALTH', 'amount': 40000, 'amount_mode': 'by', 'direction': 'down'},
            {'category': 'FOOD', 'amount': 40000, 'amount_mode': 'by', 'direction': 'up'},
        ]}), patch('chat.engine.reading', return_value=distribution_reading):
            _, preview = self.say(text)
        self.assertIn('proposal', preview['budgetAdjustment'], preview)
        self.assertEqual(preview['budgetAdjustment']['proposal']['allocations']['MEDICAL_HEALTH'], 10000)
        self.assertEqual(preview['budgetAdjustment']['proposal']['allocations']['FOOD'], 440000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_budget_basis_uses_saved_conditions_without_a_spending_comparison(self):
        self.base.input_snapshot = {'engine_input': {'minimums': {'MEDICAL_HEALTH': 50000}}}
        self.base.validation_summary = {'design': {'selection': {'plan': 'a'}, 'explanations': [
            {'category': 'MEDICAL_HEALTH', 'text': '설정한 최소 금액을 유지한 배정이에요.'}]}}
        self.base.save(update_fields=['input_snapshot', 'validation_summary'])
        data, reply = self.say('의료비 예산 왜이렇게 크게 잡았어')
        self.assertEqual(data['agent'], 'spending')
        self.assertIn('50,000원', reply['text'])
        self.assertIn('설정한 최소 금액', reply['text'])
        self.assertIn('생활 조건이 비슷한 사람들이 쓴 돈', reply['text'])
        for term in ('모델', '카테고리 비율', '보호 조건', '주기 유지 목표'):
            self.assertNotIn(term, reply['text'])
        self.assertNotIn('비교한 기간이 없어', reply['text'])
        self.base.refresh_from_db()
        self.assertEqual(self.base.input_snapshot, {'engine_input': {'minimums': {'MEDICAL_HEALTH': 50000}}})
        self.assertEqual(Budget.objects.count(), 1)

    def test_budget_without_basis_does_not_invent_a_reason(self):
        _, reply = self.say('의료비 예산 책정 근거 알려줘')
        self.assertIn('50,000원', reply['text'])
        self.assertIn('자세한 이유가 남아 있지 않아', reply['text'])
        self.assertNotIn('지난달', reply['text'])

    def test_basis_question_during_adjustment_keeps_the_existing_adjustment(self):
        self.proposal()
        before, budgets = self.budget_state(), Budget.objects.count()
        data, reply = self.say('의료비 예산 책정 근거 알려줘')
        self.assertEqual(data['agent'], 'budget')  # 조정 중의 예산 질문은 예산 대화가 답한다 (소비 분석 연결 없음)
        self.assertIn('50,000원', reply['text'])
        self.assertEqual(self.budget_state()['requests'], before['requests'])
        self.assertEqual(self.budget_state().get('proposal'), before.get('proposal'))
        self.assertEqual(Budget.objects.count(), budgets)  # 질문만으로 새 예산안을 만들지 않는다

    def test_basis_question_right_after_starting_adjustment_is_answered_in_budget(self):
        """'예산 조정' → '패션쇼핑 예산 왜 이렇게 높게 책정됐어?' (11/14 화면): 소비 분석으로 넘기지 않고 근거 + 다음 안내"""
        self.say(choice='budget')
        data, reply = self.say('의료비 예산 왜이렇게 크게 잡았어')
        self.assertEqual(data['agent'], 'budget')
        self.assertIn('50,000원', reply['text'])
        self.assertIn('줄이거나 다른 항목으로 옮기고 싶으면', reply['text'])
        self.assertNotIn('불러오지 못했어요', reply['text'])

    def test_closed_cycle_left_by_a_payday_change_does_not_break_budget_lookups(self):
        """급여일을 바꾸면 이전 주기가 closed 로 남아 날짜가 겹친다 — 진행 중 주기만 보고 답한다 (11/14 '예산을 불러오지 못했어요')"""
        from datetime import timedelta
        from budgets.models import BudgetCycle
        cycle = self.base.cycle
        BudgetCycle.objects.create(user=self.base.user, starts_on=cycle.starts_on - timedelta(days=3),
                                   ends_before=cycle.ends_before - timedelta(days=3), status='closed')
        lines = explain_current_budget(self.base.user, ['MEDICAL_HEALTH'], timezone.localdate())
        self.assertIn('50,000원', ' '.join(lines))

    def test_budget_basis_does_not_select_another_users_budget(self):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user(email='other-basis@example.test')
        self.assertEqual(explain_current_budget(other, ['MEDICAL_HEALTH'], timezone.localdate()),
                         ['지금 사용 중인 예산이 없어요. 먼저 예산을 만들어 주세요.'])
