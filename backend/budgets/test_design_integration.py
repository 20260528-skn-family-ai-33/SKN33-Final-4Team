"""Merged service flow using synthetic transactions and real coverage/salary verification."""
from datetime import date
import os
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase, override_settings

from finance.models import BankAccount, Connection, FinancialTransaction, SyncRun
from users.models import CategoryMinimum
from .models import Budget, BudgetCategorySetting, Bucket, SalaryDecision
from .salary import confirmation_snapshot
from . import test_design_service as service_fixtures
from . import design_service as service


@override_settings(BUDGET_DESIGN_ENABLED=True)
class DesignIntegrationTest(TestCase):
    setUpTestData = classmethod(service_fixtures.DesignAPITest.setUpTestData.__func__)
    post, task = service_fixtures.DesignAPITest.post, service_fixtures.DesignAPITest.task

    def setUp(self):
        service_fixtures.DesignAPITest.setUp(self)
        # This fixture verifies collected bank history; a manual card has no collection evidence.
        self.card.delete()
        bank = Connection.objects.create(user=self.user, business_type='bank',
            organization_code='0004', status='connected')
        self.account = BankAccount.objects.create(user=self.user, connection=bank,
            source_account_key='synthetic-integration', account_type='demand',
            currency='KRW', is_selected=True)
        self.collection = SyncRun.objects.create(user=self.user, connection=bank,
            product_code='bank_sync', status='success', covered_from=date(2026, 8, 1),
            covered_to=date(2026, 10, 31))
        self.deposits = []
        for month, count in ((8, 4), (9, 4), (10, 3)):
            for day in range(10, 10+count):
                self.transaction(month, day, amount=20000, direction='out',
                    transaction_type='spending', confirmed_category_id='FOOD',
                    confirmation_state='confirmed')
            deposit = self.transaction(month, 25, amount=2500000, direction='in',
                transaction_type='income', source_description='합성 급여',
                confirmed_category_id='UNCLASSIFIED', confirmation_state='needs_review')
            SalaryDecision.objects.create(user=self.user, transaction=deposit, state='confirmed',
                pay_cycle_on=deposit.occurred_on, **confirmation_snapshot(deposit))
            self.deposits.append(deposit)
        CategoryMinimum.objects.create(user=self.user, category_id='FOOD', min_amount=150001)
        BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle,
            category_id='EDUCATION', locked=True, locked_amount=12345)

    def transaction(self, month, day, **values):
        return FinancialTransaction.objects.create(user=self.user, account=self.account,
            source_kind='bank', source_key=str(uuid4()), occurred_on=date(2026, month, day), **values)

    def propose(self, plan):
        task = self.task(self.post())
        self.assertEqual(task.result['outcome'], 'proposed', task.result)
        budget = Budget.objects.get(pk=task.result['budget_id'])
        self.assertEqual(budget.status, 'validated')
        selection = budget.validation_summary['design']['selection']
        self.assertEqual(selection['plan'], plan, selection)
        return budget

    def approve(self, budget):
        return self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json',
            HTTP_IDEMPOTENCY_KEY=str(uuid4()))

    def assert_settings_preserved(self):
        self.assertEqual(CategoryMinimum.objects.get(user=self.user, category_id='FOOD').min_amount, 150001)
        setting = BudgetCategorySetting.objects.get(user=self.user, cycle=self.cycle, category_id='EDUCATION')
        self.assertEqual((setting.locked, setting.locked_amount), (True, 12345))

    def test_real_history_selects_b_saves_spare_and_approves_once(self):
        budget = self.propose('b')
        lines = {r.category_id: r.allocated_amount for r in budget.items.filter(bucket=Bucket.LIVING)}
        self.assertGreaterEqual(lines['FOOD'], 150001)
        self.assertEqual(lines['EDUCATION'], 12345)
        before = list(budget.items.order_by('pk').values_list('pk', 'allocated_amount'))
        view = self.client.get('/api/budgets/').json()
        spare = budget.funding_amount-sum(budget.items.values_list('allocated_amount', flat=True))
        self.assertEqual(view['split']['spare'], spare)
        for _ in range(2):
            response = self.approve(budget)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()['split']['spare'], spare)
        budget.refresh_from_db()
        self.assertEqual(budget.status, 'approved')
        self.assertEqual(list(budget.items.order_by('pk').values_list('pk', 'allocated_amount')), before)
        self.assert_settings_preserved()

    def test_missing_collection_uses_a_and_can_be_approved(self):
        self.collection.covered_to = date(2026, 9, 30)
        self.collection.save(update_fields=['covered_to'])
        budget = self.propose('a')
        response = self.approve(budget)
        self.assertEqual(response.status_code, 200, response.content)
        self.assert_settings_preserved()

    def test_salary_evidence_changed_after_proposal_expires_b(self):
        budget = self.propose('b')
        FinancialTransaction.objects.filter(pk=self.deposits[0].pk).update(amount=2500001)
        response = self.approve(budget)
        self.assertEqual(response.status_code, 409, response.content)
        budget.refresh_from_db()
        self.assertEqual(budget.status, 'expired')
        self.assert_settings_preserved()

    def test_card_reservation_changed_after_proposal_expires_design_budget(self):
        budget = self.propose('b')
        frozen = budget.input_snapshot['fixed']['card_reserved']
        with patch('budgets.protect.card_reserved', return_value={'amount': frozen+1}):
            response = self.approve(budget)
        self.assertEqual(response.status_code, 409, response.content)
        budget.refresh_from_db()
        self.assertEqual(budget.status, 'expired')
        self.assert_settings_preserved()


@skipUnless(all(Path(os.environ.get(k, '')).is_file() for k in
    ('BUDGET_INFERENCE_PYTHON', 'BUDGET_MODEL_PATH')), 'Actual ML environment and model required')
@override_settings(BUDGET_DESIGN_ENABLED=True)
class ActualModelAPITest(TestCase):
    setUpTestData = classmethod(DesignIntegrationTest.setUpTestData.__func__)
    post, task = DesignIntegrationTest.post, DesignIntegrationTest.task
    transaction = DesignIntegrationTest.transaction
    propose, approve = DesignIntegrationTest.propose, DesignIntegrationTest.approve
    assert_settings_preserved = DesignIntegrationTest.assert_settings_preserved

    def setUp(self):
        DesignIntegrationTest.setUp(self)

    def check_actual_plan(self, plan):
        from scripts.budget_design.inference import predict_ml
        if plan == 'a':
            self.collection.covered_to = date(2026, 9, 30)
            self.collection.save(update_fields=['covered_to'])
        with patch.object(service, 'predict_ml', wraps=predict_ml) as predict, \
                patch.object(service, 'choose_budget', side_effect=AssertionError('No external LLM')), \
                patch.object(service, 'forecast_budget', side_effect=AssertionError('No external LLM')):
            budget = self.propose(plan)
            self.assertEqual(predict.call_count, 1)
            meta = budget.input_snapshot['ml_reference_meta']
            self.assertEqual(meta['source'], 'model')
            self.assertEqual(meta['model_id'], '20261026T074827Z_53769d64')
            self.assertEqual(meta['model_checksum'], '2fb8d5e15a5175a1d21830bacc51a44bf4b0bf5e90cceee6c2f7bfff6e7c74ce')
            before = dict(budget.items.filter(bucket=Bucket.LIVING).values_list('category_id', 'allocated_amount'))
            response = self.client.get('/api/budgets/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual({r['category']: r['allocated'] for r in response.json()['categories']}, before)
            self.assertEqual(before['EDUCATION'], 12345)
            self.assertGreaterEqual(before['FOOD'], 150001)
            spare = response.json()['split']['spare']
            for _ in range(2):
                approved = self.approve(budget)
                self.assertEqual(approved.status_code, 200, approved.content)
                self.assertEqual(approved.json()['split']['spare'], spare)
            self.assertEqual(predict.call_count, 1)
            budget.refresh_from_db()
            self.assertEqual(budget.status, 'approved')
            self.assertEqual(dict(budget.items.filter(bucket=Bucket.LIVING)
                .values_list('category_id', 'allocated_amount')), before)
            self.assert_settings_preserved()

    def test_actual_ml_a_display_save_and_approval(self):
        self.check_actual_plan('a')

    def test_actual_ml_b_display_save_and_approval(self):
        self.check_actual_plan('b')
