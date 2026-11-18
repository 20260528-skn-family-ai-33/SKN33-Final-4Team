"""Synthetic PostgreSQL recovery, validation and explicit approval; no external model calls."""
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.utils import timezone
from finance.models import BankAccount, Connection, FinancialTransaction, RecurringSaving
from users.models import CategoryMinimum, CategoryPriority

from . import design_service as service
from .models import Budget, BudgetCategorySetting
from .design_candidates import CATEGORY_NAMES
from . import test_design_recovery as entry_tests
from . import test_recommend as fixtures


@override_settings(BUDGET_DESIGN_ENABLED=True, CHAT_ENGINE='router', CHAT_LLM='fake',
                   BUDGET_DESIGN_POLICY=deepcopy(service.INITIAL_POLICY))
class RecoveryPlanAPITest(TestCase):
    setUpTestData = classmethod(fixtures.RecommendAPITest.setUpTestData.__func__)
    post_recommend = fixtures.RecommendAPITest.post
    chat = entry_tests.DesignRecoveryAPITest.chat
    protected = entry_tests.DesignRecoveryAPITest.protected

    def setUp(self):
        fixtures.RecommendAPITest.setUp(self)
        self.enterContext(patch('chat.engine.today_and_as_of', return_value=(fixtures.TODAY, None)))
        self.enterContext(patch('budgets.adjustment_context.timezone.localdate', return_value=fixtures.TODAY))
        self.enterContext(patch('chat.engine.reading', side_effect=AssertionError('No external LLM')))
        self.enterContext(patch('budgets.design_recovery_plans.coverage', return_value={'state': 'complete'}))
        self.enterContext(patch.object(service, 'history_for_user', return_value={}))
        self.prediction = {'ratios': dict.fromkeys(CATEGORY_NAMES.values(), .1)}
        self.ml = self.enterContext(patch.object(service, 'predict_ml', return_value=self.prediction))
        CategoryPriority.objects.create(user=self.user, category_id='EDUCATION', rank=2)
        CategoryPriority.objects.create(user=self.user, category_id='BEAUTY', rank=3)
        bank = Connection.objects.create(user=self.user, business_type='bank', organization_code='0004', status='connected')
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key='recovery-fixture',
            account_type='demand', currency='KRW', is_selected=True, balance=12000000)
        self.saving.from_account, self.saving.transfer_day, self.saving.evidence = self.account, 28, []
        self.saving.save(update_fields=['from_account', 'transfer_day', 'evidence'])

    def failure(self, minimum=1110001):
        # Fixture amounts only; no production minimum or rate is configured by these tests.
        CategoryMinimum.objects.create(user=self.user, category_id='FOOD', min_amount=minimum)
        # 11/18 자물쇠 기능 제거: 예전 EDUCATION 자물쇠(12,345원) 대신 같은 금액의 최소 금액으로 돈이 모자란 상황을 만든다
        CategoryMinimum.objects.create(user=self.user, category_id='EDUCATION', min_amount=12345)
        response = self.post_recommend()
        self.assertEqual(response.status_code, 202, response.content)
        from chat.models import AsyncTask
        source = AsyncTask.objects.get(pk=response.json()['task']['taskId'])
        self.assertFalse(Budget.objects.filter(task=source).exists())
        self.assertIn('handoff', source.result)
        entered = self.chat(source)
        self.assertEqual(entered.status_code, 200, entered.content)
        return source, entered.json()

    def pick(self, response, value):
        return self.chat(conversation=response['conversationId'], choice={
            'turn': response['messages'][-1]['content']['turn'], 'value': value})

    def preview(self, minimum=1110001, *, ongoing=False, funding=None):
        source, entered = self.failure(minimum)
        before = self.protected()
        response = self.pick(entered, 'recovery_ongoing' if ongoing else 'recovery_cycle')
        self.assertEqual(response.status_code, 200, response.content)
        if funding:
            response = self.pick(response.json(), funding)
            self.assertEqual(response.status_code, 200, response.content)
        content = response.json()['messages'][-1]['content']['budgetAdjustment']
        self.assertIn('proposal', content, response.json()['messages'][-1]['content'])
        self.assertTrue(content['canApprove'])
        proposal = Budget.objects.get(pk=content['proposal']['proposalId'])
        self.assertEqual(proposal.status, 'validated')
        self.assertIsNone(proposal.base_budget_id)
        self.assertEqual(self.protected(), before)
        return source, response.json(), proposal

    def approve(self, proposal, keep=False, *, key=None, path=None, body=None):
        return self.client.post(path or f'/api/budgets/{proposal.pk}/approve/',
            {'keepForNextCycle': keep} if body is None else body, format='json',
            HTTP_IDEMPOTENCY_KEY=key or str(uuid4()))

    def test_spare_plan_saved_then_approved_without_changing_settings(self):
        source, chat, proposal = self.preview()
        before = self.protected()
        self.assertEqual(Budget.objects.count(), 1)
        self.assertGreaterEqual(proposal.items.get(bucket='living', category_id='EDUCATION').allocated_amount, 12345)
        self.assertGreaterEqual(proposal.items.get(bucket='living', category_id='FOOD').allocated_amount, 1110001)
        self.assertGreaterEqual(proposal.items.get(bucket='living', category_id='TRANSPORT_CAR').allocated_amount, 60000)
        key = str(uuid4())
        self.ml.reset_mock()
        response = self.approve(proposal, key=key)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['adjustment']['state'], 'saved')
        self.assertEqual(response.json()['adjustment']['mode'], 'INITIAL_ADJUSTMENT')
        self.assertEqual(self.protected(), before)
        self.ml.assert_not_called()
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'approved')
        self.assertEqual(sum(proposal.items.values_list('allocated_amount', flat=True)) + response.json()['budget']['split']['spare'], proposal.funding_amount)
        again = self.approve(proposal, key=key)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json(), response.json())
        self.assertEqual(self.approve(proposal, keep=True).status_code, 409)
        source.refresh_from_db()
        self.assertNotIn('handoff', source.result)
        restored = self.client.get(f"/api/chat/conversations/{chat['conversationId']}/")
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertEqual(restored.json()['messages'][-1]['content']['budgetAdjustment']['proposal']['state'], 'saved')

    def test_explicit_keep_updates_only_upper_settings_after_approval(self):
        _, _, proposal = self.preview(ongoing=True)
        minimums, locks, ranks = self.protected()['minimums'], self.protected()['locks'], self.protected()['priorities']
        living = sum(proposal.items.filter(bucket='living').values_list('allocated_amount', flat=True))
        response = self.approve(proposal, keep=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.living_budget, living)
        self.assertEqual(self.protected()['minimums'], minimums)
        self.assertEqual(self.protected()['locks'], locks)
        self.assertEqual(self.protected()['priorities'], ranks)

    def test_savings_and_cushion_require_explicit_source_selection_and_respect_floors(self):
        _, _, proposal = self.preview(1300001, funding='recovery_savings')
        self.assertGreaterEqual(proposal.items.get(bucket='savings').allocated_amount, self.saving.amount)
        self.assertEqual(proposal.items.get(bucket='cushion').allocated_amount, self.profile.cushion_monthly)
        self.assertEqual(proposal.status, 'validated')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.savings_floor, 610000)

    def test_custom_cushion_goal_decides_recovery_cushion_floor(self):
        """PR #120 리뷰 P2: 분배 실패 복구도 지킬 돈 화면과 같은 쿠션 목표(직접 정한 금액)로 적립 필수 여부를 판단한다"""
        from .design_recovery_plans import finances
        source, _ = self.failure()
        handoff = source.result['_design_handoff']
        data = finances(self.user, handoff, fixtures.TODAY)
        self.assertEqual(data['floors']['CUSHION_MONTHLY'], 0)  # 잔액 1,200만 ≥ 기본 목표(월급 × 3)
        self.profile.cushion_goal_amount = 15000000
        self.profile.save(update_fields=['cushion_goal_amount'])
        data = finances(self.user, handoff, fixtures.TODAY)
        self.assertEqual(data['floors']['CUSHION_MONTHLY'], 1)

    def test_extended_plan_and_cushion_protection(self):
        _, _, proposal = self.preview(1500001, funding='recovery_extended')
        saving = proposal.items.get(bucket='savings').allocated_amount
        cushion = proposal.items.get(bucket='cushion').allocated_amount
        self.assertGreaterEqual(saving, 300000)
        self.assertGreaterEqual(cushion, 0)
        self.assertNotIn('approvedBudget', self.client.get('/api/budgets/protect/').json())
        self.assertEqual(self.approve(proposal).status_code, 200)
        protect = self.client.get('/api/budgets/protect/').json()
        self.assertEqual(protect['saved']['savingFloor'], self.profile.savings_floor)
        self.assertEqual(protect['saved']['cushionMonthly'], self.profile.cushion_monthly)
        self.assertEqual(protect['approvedBudget'], {
            'spareTransfer': proposal.validation_summary['design_recovery']['transfers']['SPARE'],
            'spare': proposal.funding_amount - sum(proposal.items.values_list('allocated_amount', flat=True)),
            'savingFloor': saving, 'cushionMonthly': cushion,
            'savingTransfer': self.profile.savings_floor - saving,
            'cushionTransfer': self.profile.cushion_monthly - cushion,
        })

    def test_existing_approved_budget_and_other_draft_survive_until_approval(self):
        old = Budget.objects.create(user=self.user, cycle=self.cycle, version_no=1, status='approved',
                                    funding_amount=2800000, approved_at=timezone.now())
        draft = Budget.objects.create(user=self.user, cycle=self.cycle, version_no=2, status='validated', funding_amount=2800000)
        _, _, proposal = self.preview()
        old.refresh_from_db(); draft.refresh_from_db()
        self.assertEqual((old.status, draft.status), ('approved', 'validated'))
        self.assertEqual(self.approve(proposal).status_code, 200)
        old.refresh_from_db(); draft.refresh_from_db()
        self.assertEqual((old.status, draft.status), ('superseded', 'expired'))

    def test_unknown_spending_or_insufficient_money_does_not_create_proposal(self):
        _, entered = self.failure(4000000)
        before = self.protected()
        response = self.pick(entered, 'recovery_cycle')
        response = self.pick(response.json(), 'recovery_extended')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Budget.objects.exists())
        self.assertEqual(self.protected(), before)

    def test_savings_budget_does_not_require_a_transfer_account(self):
        self.saving.from_account = None
        self.saving.save(update_fields=['from_account'])
        _, entered = self.failure(1300001)
        response = self.pick(entered, 'recovery_cycle')
        response = self.pick(response.json(), 'recovery_savings')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['messages'][-1]['content']['budgetAdjustment']['canApprove'])
        proposal = Budget.objects.get(status='validated')
        self.assertGreaterEqual(proposal.items.get(bucket='savings').allocated_amount, self.saving.amount)

    def test_model_failure_does_not_save_unchecked_money(self):
        _, entered = self.failure()
        with patch.object(service, 'predict_ml', side_effect=ValueError('model unavailable')):
            response = self.pick(entered, 'recovery_cycle')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Budget.objects.exists())

    def test_owner_wrong_route_and_client_money_injection_rejected(self):
        _, _, proposal = self.preview()
        self.assertEqual(self.approve(proposal, path=f'/api/budgets/proposals/{proposal.pk}/approve/').status_code, 409)
        self.assertEqual(self.approve(proposal, body={'keepForNextCycle': False, 'allocations': {'FOOD': 0}}).status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.approve(proposal).status_code, 404)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'validated')

    def test_stored_money_tamper_expires_without_setting_changes(self):
        _, _, proposal = self.preview()
        before = self.protected()
        row = proposal.items.get(bucket='living', category_id='EDUCATION')
        row.allocated_amount += 5000
        row.save(update_fields=['allocated_amount'])
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 409, response.content)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'expired')
        self.assertEqual(self.protected(), before)

    def test_spending_or_history_change_expires_proposal(self):
        _, _, proposal = self.preview()
        FinancialTransaction.objects.create(user=self.user, source_kind='manual', occurred_on=fixtures.TODAY,
            amount=10000, direction='out', transaction_type='spending', confirmed_category_id='FOOD', confirmation_state='confirmed')
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'expired')
        self.assertIn('최근 거래나 사용 가능한 재원 정보가 바뀌었어요', response.json()['error']['message'])

    def test_cancel_rejects_only_unapproved_plan(self):
        _, chat, proposal = self.preview()
        before = self.protected()
        response = self.chat(conversation=chat['conversationId'], text='취소')
        self.assertEqual(response.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'rejected')
        self.assertEqual(self.approve(proposal).status_code, 409)
        self.assertEqual(self.protected(), before)

    def test_unverified_current_spending_does_not_create_or_approve_plan(self):
        _, entered = self.failure()
        before = self.protected()
        with patch('budgets.design_recovery_plans.coverage', return_value={'state': 'partial'}):
            response = self.pick(entered, 'recovery_cycle')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Budget.objects.exists())
        self.assertEqual(self.protected(), before)

    def test_duplicate_chat_request_does_not_create_second_proposal(self):
        _, entered = self.failure()
        key = str(uuid4())
        choice = {'turn': entered['messages'][-1]['content']['turn'], 'value': 'recovery_cycle'}
        first = self.chat(conversation=entered['conversationId'], key=key, choice=choice)
        self.assertEqual(first.status_code, 200)
        calls = self.ml.call_count
        second = self.chat(conversation=entered['conversationId'], key=key, choice=choice)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(Budget.objects.count(), 1)
        self.assertEqual(self.ml.call_count, calls)

    def test_history_change_expires_proposal_with_financial_data_message(self):
        _, _, proposal = self.preview()
        before = self.protected()
        with patch.object(service, 'history_for_user', return_value={'changed': True}):
            response = self.approve(proposal)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error'], {
            'code': 'expired',
            'message': '최근 거래나 사용 가능한 재원 정보가 바뀌었어요. 변경안을 다시 계산해 주세요.',
        })
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'expired')
        self.assertEqual(response.json()['error']['code'], 'expired')
        self.assertIn('최근 거래나 사용 가능한 재원 정보가 바뀌었어요', response.json()['error']['message'])
        self.assertEqual(self.protected(), before)

    def b_history(self):
        from .test_design_service import history
        records = {}
        for month, row in zip(('2026-07', '2026-08', '2026-09'), history().values()):
            row = deepcopy(row)
            for item in row['items']:
                item['occurred_on'] = month+'-15'
                if item['category'] != 'FOOD':
                    item['net_amount'] = 10000
            records[month] = row
        return records

    def test_verified_b_history_uses_exact_remaining_spare_for_rounding(self):
        with (patch.object(service, 'history_for_user', return_value=self.b_history()),
              patch('budgets.design_recovery_plans.reserved_spare', return_value=(77655 - 2655, 'fixture'))):  # 11/18: 최소 금액 15,000원(옛 자물쇠 12,345원 대신)만큼 옮김
            _, _, proposal = self.preview()
            plan = proposal.validation_summary['design_recovery']
            self.assertEqual(plan['out']['_design']['selection']['plan'], 'b')
            self.assertEqual(plan['system_floors']['TRANSPORT_CAR'], 10000)
            self.assertEqual(plan['out']['_design']['spare_transfer'], 5000)  # 11/18: 옛 자물쇠 12,345원 → 최소 금액 15,000원
            self.assertEqual(plan['transfers'], {'SPARE': 25000})
            self.assertEqual(proposal.items.get(bucket='living', category_id='EDUCATION').allocated_amount, 15000)
            self.ml.assert_called_once()
            self.assertEqual(self.approve(proposal).status_code, 200)

    def test_b_rounding_one_won_short_reports_exact_shortage_without_saving(self):
        with (patch.object(service, 'history_for_user', return_value=self.b_history()),
              patch('budgets.design_recovery_plans.reserved_spare', return_value=(77656 - 2655, 'fixture'))):  # 11/18: 최소 금액 15,000원(옛 자물쇠 12,345원 대신)만큼 옮김
            _, entered = self.failure()
            before = self.protected()
            response = self.pick(entered, 'recovery_cycle')
        self.assertEqual(response.status_code, 200)
        content = response.json()['messages'][-1]['content']
        self.assertIn('선택한 재원으로 계산하면 생활비가 1원 부족해요.', content['lines'])
        self.assertIn('recovery_savings', [choice['value'] for choice in content['choices']])
        self.assertFalse(content['budgetAdjustment']['canApprove'])
        self.assertFalse(Budget.objects.exists())
        self.assertEqual(self.protected(), before)
        self.ml.assert_called_once()

