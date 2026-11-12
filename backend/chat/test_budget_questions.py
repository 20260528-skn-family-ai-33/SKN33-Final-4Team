"""Budget basis questions and early adjustment steps use the real message API."""
from django.test import TestCase, override_settings

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
                self.assertIn('의료·건강', reply['budgetAdjustment']['requestSummary'])
                self.assertTrue(any(plan.get('result', {}).get('changes') for plan in
                                    reply['budgetAdjustment']['comparison']['plans'] if plan.get('result')))
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

    def test_medical_reduction_has_a_calculated_priority_preview_before_selection(self):
        _, reply = self.say('의료비를 1만 원으로 줄여줘')
        comparison = reply['budgetAdjustment']['comparison']
        plan = next(p for p in comparison['plans'] if p['id'] == 'living')
        self.assertEqual(plan['status'], 'ready')
        self.assertEqual(plan['result']['allocations']['MEDICAL_HEALTH'], 10000)
        self.assertEqual(plan['result']['allocations']['FOOD'], 440000)
        self.assertEqual(Budget.objects.count(), 1)
        _, selected = self.pick(reply, 'plan_living')
        self.assertEqual(selected['budgetAdjustment']['proposal']['allocations']['FOOD'], 440000)
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
        before = self.budget_state()
        data, reply = self.say('의료비 예산 책정 근거 알려줘')
        self.assertEqual(data['agent'], 'spending')
        self.assertIn('50,000원', reply['text'])
        self.assertEqual(self.budget_state(), before)

    def test_budget_basis_does_not_select_another_users_budget(self):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user(email='other-basis@example.test')
        self.assertEqual(explain_current_budget(other, ['MEDICAL_HEALTH'], timezone.localdate()),
                         ['지금 사용 중인 예산이 없어요. 먼저 예산을 만들어 주세요.'])
