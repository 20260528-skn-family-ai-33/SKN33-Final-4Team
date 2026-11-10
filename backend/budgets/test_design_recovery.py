"""Synthetic failure -> private task -> chat entry; no model, LLM, or real database writes."""
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings

from chat.models import AsyncTask, Conversation
from users.models import CategoryMinimum, CategoryPriority

from . import design_service as service
from .models import Budget, BudgetCategorySetting, BudgetItem
from .design_candidates import CATEGORY_NAMES
from .test_design_candidates import inp
from . import test_recommend as fixtures


class RecoveryEvidenceTest(SimpleTestCase):
    def test_real_system_floor_shortfall_survives_service_wrapper(self):
        snapshot = {'engine_input': inp(target=50000, priorities=[{'category': 'FOOD', 'rank': 1},
            {'category': 'EDUCATION', 'rank': 2}, {'category': 'BEAUTY', 'rank': 3}]), 'etc': 0}
        with override_settings(BUDGET_DESIGN_POLICY=deepcopy(service.INITIAL_POLICY)), \
                patch.object(service, 'history_for_user', return_value={}), \
                patch.object(service, 'predict_ml', return_value={'ratios': dict.fromkeys(CATEGORY_NAMES.values(), .1)}):
            out = service.recommend(None, snapshot, 0, fixtures.TODAY)
        self.assertEqual(out['reason'], 'system_floor_unaffordable')
        self.assertGreater(out['shortfall'], 0)
        self.assertEqual(out['categories'], ['TRANSPORT_CAR'])


@override_settings(BUDGET_DESIGN_ENABLED=True, CHAT_ENGINE='router', CHAT_LLM='fake',
                   BUDGET_DESIGN_POLICY=deepcopy(service.INITIAL_POLICY))
class DesignRecoveryAPITest(TestCase):
    setUpTestData = classmethod(fixtures.RecommendAPITest.setUpTestData.__func__)
    post_recommend = fixtures.RecommendAPITest.post

    def setUp(self):
        fixtures.RecommendAPITest.setUp(self)
        self.enterContext(patch('chat.engine.today_and_as_of', return_value=(fixtures.TODAY, None)))
        self.llm = self.enterContext(patch('chat.engine.reading', side_effect=AssertionError('No LLM in recovery entry')))
        self.ml = self.enterContext(patch.object(service, 'predict_ml', side_effect=AssertionError('No model needed for settings conflict')))
        CategoryPriority.objects.create(user=self.user, category_id='EDUCATION', rank=2)
        CategoryPriority.objects.create(user=self.user, category_id='BEAUTY', rank=3)

    def protected(self):
        self.profile.refresh_from_db()
        return {'profile': (self.profile.living_budget, self.profile.savings_floor, self.profile.cushion_monthly,
                            deepcopy(self.profile.budget_category_targets)),
            'minimums': list(CategoryMinimum.objects.filter(user=self.user).order_by('category_id').values()),
            'priorities': list(CategoryPriority.objects.filter(user=self.user).order_by('rank').values()),
            'locks': list(BudgetCategorySetting.objects.filter(user=self.user).order_by('category_id').values())}

    def failed_task(self, *, locked=False):
        CategoryMinimum.objects.create(user=self.user, category_id='FOOD', min_amount=150001 if locked else 1500001)
        BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id='EDUCATION',
                                             locked=True, locked_amount=12345)
        if locked:
            BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id='FOOD',
                                                 locked=True, locked_amount=100000)
        before = self.protected()
        response = self.post_recommend()
        self.assertEqual(response.status_code, 202, response.content)
        task = AsyncTask.objects.get(pk=response.json()['task']['taskId'])
        self.assertEqual(task.status, 'succeeded')
        self.assertIsNone(task.result['budget_id'])
        self.assertEqual(self.protected(), before)
        self.assertFalse(Budget.objects.exists())
        self.assertFalse(BudgetItem.objects.exists())
        return task

    def chat(self, task=None, *, conversation=None, key=None, **extra):
        body = {'conversationId': conversation}
        if task is not None:
            body.update(choice='budget', budgetContext={'mode': 'DESIGN_RECOVERY', 'sourceTaskId': str(task.pk)})
        body.update(extra)
        return self.client.post('/api/chat/messages/', body, format='json', HTTP_IDEMPOTENCY_KEY=key or str(uuid4()))

    def test_failure_is_private_and_public_task_exposes_only_reference(self):
        task = self.failed_task()
        private = task.result['_design_handoff']
        self.assertEqual(private['constraints']['minimums']['FOOD'], 1500001)
        self.assertEqual(private['constraints']['locks']['EDUCATION'], 12345)
        self.assertEqual(private['priorities'][0], {'category': 'FOOD', 'rank': 1})
        public = self.client.get(f'/api/tasks/{task.pk}/').json()['result']
        self.assertEqual(set(public), {'outcome', 'budgetId', 'summary', 'conflicts', 'locksReleased', 'handoff'})
        self.assertNotIn('DesignHandoff', public)
        self.assertNotIn('_designHandoff', public)
        self.assertNotIn('constraints', public)
        self.assertEqual(public['handoff'], {'mode': 'DESIGN_RECOVERY', 'sourceTaskId': str(task.pk)})
        task.refresh_from_db()
        self.assertIn('_design_handoff', task.result)

    def test_starts_without_budget_and_preserves_settings(self):
        task = self.failed_task()
        before = self.protected()
        response = self.chat(task)
        self.assertEqual(response.status_code, 200, response.content)
        content = response.json()['messages'][-1]['content']
        self.assertEqual(content['agent'], 'budget')
        self.assertEqual(content['budgetAdjustment']['phase'], 'design_recovery')
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        self.assertNotIn('proposal', content['budgetAdjustment'])
        self.assertNotIn('constraints', content)
        task.refresh_from_db()
        self.assertEqual(str(task.conversation_id), response.json()['conversationId'])
        self.assertEqual(self.protected(), before)
        self.assertFalse(Budget.objects.exists())
        self.llm.assert_not_called()
        self.ml.assert_not_called()

    def test_handoff_validation_failure_preserves_public_conflict_result(self):
        with (patch('budgets.design_handoff.build_design_handoff', side_effect=ValueError('invalid_handoff_budget')),
              self.assertLogs('budgets.api', level='WARNING') as logs):
            task = self.failed_task()
        self.assertIn('invalid_handoff_budget', logs.output[0])
        response = self.client.get(f'/api/tasks/{task.pk}/')
        self.assertEqual(response.status_code, 200)
        result = response.json()['result']
        self.assertEqual(result['outcome'], 'infeasible')
        self.assertEqual(result['conflicts'][0]['type'], 'minimums_over_living')
        self.assertNotIn('handoff', result)
        self.assertNotIn('_design_handoff', task.result)

    def test_lock_conflict_is_not_automatically_unlocked(self):
        task = self.failed_task(locked=True)
        self.assertEqual(task.result['locks_released'], [])
        self.assertEqual(task.result['_design_handoff']['issue']['reason'], 'lock_below_minimum')
        before = self.protected()
        response = self.chat(task)
        self.assertEqual(response.status_code, 200)
        content = response.json()['messages'][-1]['content']
        self.assertIn('잠금 금액과 최소금액이 서로 충돌', content['text'])
        self.assertEqual(content['choices'], [])
        self.assertEqual(self.protected(), before)

    def test_unallocatable_surplus_is_not_described_as_cash_shortage(self):
        from .recommend import ALLOCATION_CODES
        for code in ALLOCATION_CODES:
            BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id=code,
                                                 locked=True, locked_amount=100000)
        before = self.protected()
        response = self.post_recommend()
        task = AsyncTask.objects.get(pk=response.json()['task']['taskId'])
        self.assertIsNone(task.result['_design_handoff']['issue']['shortfall'])
        entered = self.chat(task)
        self.assertEqual(entered.status_code, 200, entered.content)
        content = entered.json()['messages'][-1]['content']
        self.assertIn('남는 생활비를 배분할 곳이 없어요', content['text'])
        self.assertEqual(content['choices'], [
            {'value': 'recovery_review', 'label': '조건을 유지해서 해결안 보기'}])
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        self.assertFalse(Budget.objects.exists())
        self.assertEqual(self.protected(), before)
        self.llm.assert_not_called()
        self.ml.assert_not_called()

    def test_reuses_bound_conversation_and_same_idempotency_key(self):
        task = self.failed_task()
        key = str(uuid4())
        first = self.chat(task, key=key)
        self.assertEqual(first.status_code, 200, first.content)
        again = self.chat(task, key=key)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(first.json(), again.json())
        resumed = self.chat(task)
        self.assertEqual(resumed.status_code, 200, resumed.content)
        self.assertEqual(first.json()['conversationId'], resumed.json()['conversationId'])
        self.assertEqual(Conversation.objects.count(), 1)

    def test_scope_choice_is_only_intent_not_setting_change(self):
        task = self.failed_task()
        first = self.chat(task).json()
        before = self.protected()
        response = self.chat(conversation=first['conversationId'], choice={'turn': first['messages'][-1]['content']['turn'],
                                                                         'value': 'recovery_ongoing'})
        self.assertEqual(response.status_code, 200, response.content)
        talk = Conversation.objects.get(pk=first['conversationId'])
        self.assertEqual(talk.slots['state']['agent:budget']['recovery_scope'], 'ongoing')
        self.assertFalse(response.json()['messages'][-1]['content']['budgetAdjustment']['canApprove'])
        self.assertEqual(self.protected(), before)
        self.assertFalse(Budget.objects.exists())

    def test_other_users_task_is_not_disclosed(self):
        task = self.failed_task()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.chat(task).status_code, 404)
        self.assertFalse(Conversation.objects.exists())

    def test_fabricated_or_non_recommend_task_is_not_disclosed(self):
        for kind in ('budget_adjust', 'finance_sync'):
            task = AsyncTask.objects.create(user=self.user, task_type=kind, request_key=str(uuid4()), input_hash='x')
            self.assertEqual(self.chat(task).status_code, 404)
        task.pk = uuid4()
        self.assertEqual(self.chat(task).status_code, 404)

    def test_failed_or_non_handoff_task_cannot_start_recovery(self):
        task = self.failed_task()
        for values in ({'status': 'failed'}, {'status': 'running'}, {'result': {'outcome': 'proposed', 'budget_id': str(uuid4())}}):
            original = {key: deepcopy(getattr(task, key)) for key in values}
            for key, value in values.items(): setattr(task, key, value)
            task.save(update_fields=list(values))
            self.assertEqual(self.chat(task).status_code, 409)
            for key, value in original.items(): setattr(task, key, value)
        self.assertFalse(Conversation.objects.exists())

    def test_changed_minimum_lock_priority_or_target_expires_entry(self):
        task = self.failed_task()
        changes = [
            (CategoryMinimum.objects.filter(user=self.user, category_id='FOOD'), {'min_amount': 1600001}, {'min_amount': 1500001}),
            (BudgetCategorySetting.objects.filter(user=self.user, category_id='EDUCATION'), {'locked_amount': 23456}, {'locked_amount': 12345}),
        ]
        for rows, changed, restore in changes:
            with self.subTest(changed=changed):
                rows.update(**changed)
                before = self.protected()
                response = self.chat(task)
                self.assertEqual(response.status_code, 409, response.content)
                self.assertEqual(self.protected(), before)
                rows.update(**restore)
        beauty = CategoryPriority.objects.filter(user=self.user, category_id='BEAUTY')
        education = CategoryPriority.objects.filter(user=self.user, category_id='EDUCATION')
        beauty.update(rank=4)
        education.update(rank=3)
        beauty.update(rank=2)
        self.assertEqual(self.chat(task).status_code, 409)
        beauty.update(rank=4)
        education.update(rank=2)
        beauty.update(rank=3)
        self.profile.budget_category_targets = {'FOOD': 300000}
        self.profile.save(update_fields=['budget_category_targets'])
        self.assertEqual(self.chat(task).status_code, 409)
        self.assertFalse(Conversation.objects.exists())

    def test_deterministic_entry_does_not_call_live_llm_without_consent(self):
        task = self.failed_task()
        before = self.protected()
        with override_settings(CHAT_LLM='live'):
            response = self.chat(task)
        self.assertEqual(response.status_code, 200, response.content)
        self.llm.assert_not_called()
        self.ml.assert_not_called()
        self.assertEqual(self.protected(), before)

    def test_cancel_and_old_choice_do_not_change_settings(self):
        task = self.failed_task()
        first = self.chat(task).json()
        before = self.protected()
        wrong = self.chat(conversation=first['conversationId'], choice={'turn': 999, 'value': 'recovery_cycle'})
        self.assertEqual(wrong.status_code, 409)
        canceled = self.chat(conversation=first['conversationId'], text='취소')
        self.assertEqual(canceled.status_code, 200, canceled.content)
        self.assertEqual(canceled.json()['messages'][-1]['content']['budgetAdjustment']['status'], 'ended')
        self.assertEqual(self.protected(), before)
        self.assertFalse(Budget.objects.exists())

    def test_cycle_and_policy_changes_expire_entry(self):
        task = self.failed_task()
        with patch('chat.engine.today_and_as_of', return_value=(self.cycle.ends_before, None)):
            self.assertEqual(self.chat(task).status_code, 409)
        with override_settings(BUDGET_DESIGN_POLICY={'changed_for_test': True}):
            self.assertEqual(self.chat(task).status_code, 409)
        self.cycle.status = 'closed'
        self.cycle.save(update_fields=['status'])
        self.assertEqual(self.chat(task).status_code, 409)

    def test_changes_between_preparation_and_commit_are_rechecked(self):
        from .design_recovery import DesignRecoveryAdapter
        task = self.failed_task()
        original = DesignRecoveryAdapter.reply
        def changed(adapter, *args):
            response = original(adapter, *args)
            CategoryMinimum.objects.filter(user=self.user, category_id='FOOD').update(min_amount=1600001)
            return response
        with patch.object(DesignRecoveryAdapter, 'reply', changed):
            self.assertEqual(self.chat(task).status_code, 409)
        self.assertFalse(Conversation.objects.exists())
        task.refresh_from_db()
        self.assertIsNone(task.conversation_id)

    def test_technical_failure_is_not_advertised_as_handoff(self):
        with patch.object(service, 'recommend', return_value={'status': 'needs_data', 'reason': 'ml_unavailable',
                'summary': '모델 이용 불가', 'conflicts': []}):
            response = self.post_recommend()
        task = AsyncTask.objects.get(pk=response.json()['task']['taskId'])
        self.assertNotIn('handoff', task.result)
        self.assertNotIn('_design_handoff', task.result)
        self.assertEqual(self.chat(task).status_code, 409)

    def test_client_cannot_inject_money_or_both_basis_ids(self):
        task = self.failed_task()
        for additions in ({'minimums': {'FOOD': 0}}, {'baseBudgetId': str(uuid4())}, {'sourceTaskId': 'not-a-uuid'}):
            body = {'conversationId': None, 'choice': 'budget', 'budgetContext': {
                'mode': 'DESIGN_RECOVERY', 'sourceTaskId': str(task.pk), **additions}}
            response = self.client.post('/api/chat/messages/', body, format='json', HTTP_IDEMPOTENCY_KEY=str(uuid4()))
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Conversation.objects.exists())
