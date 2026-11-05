import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from budgets.models import Budget, BudgetCycle, CushionEntry
from chat.models import AsyncTask, Conversation, ChatMessage
from finance.connected_id import seal
from finance.models import (BankAccount, Card, CardApprovalCoverage, CardApprovalReview, CardBill, Connection,
                            FinancialTransaction, Loan, PaymentDue, PaymentMatch, RecurringExpense, RecurringSaving, SyncRun)
from users.models import Consent, FinancialProfile, IdempotencyRecord


class PrivacyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='privacy@example.com', password='Synthetic123!')
        self.other = get_user_model().objects.create_user(email='other@example.com', password='Other123!')
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get('/api/csrf/')
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        for scope in ('bank', 'card', 'loan'):
            Consent.objects.create(user=self.user, purpose='finance_inquiry', product_scope=scope, document_version='v1', document_hash='test')
        self.bank = Connection.objects.create(user=self.user, business_type='bank', organization_code='0004', status='connected')
        self.conn = Connection.objects.create(user=self.user, business_type='card', organization_code='0302', status='connected')
        self.account = BankAccount.objects.create(user=self.user, connection=self.bank, source_account_key='bank', account_type='demand', currency='KRW', is_selected=True)
        self.card = Card.objects.create(user=self.user, connection=self.conn, source_card_key='card', issuer_code='0302', origin='provider', is_selected=True)
        self.banktx = self.tx('bank', account=self.account)
        self.cardtx = self.tx('card', card=self.card)
        self.expense = RecurringExpense.objects.create(user=self.user, payment_card=self.card, name='합성 정기 결제', amount=5000,
            category_id='ETC', status='confirmed', source_type='detected', evidence=[str(self.cardtx.pk)])
        self.manual = RecurringExpense.objects.create(user=self.user, payment_card=self.card, name='직접 등록', amount=6000,
            category_id='ETC', status='confirmed', source_type='manual', evidence=[str(self.cardtx.pk)])
        self.bill = CardBill.objects.create(user=self.user, connection=self.conn, issuer_code='0302', card=self.card,
            billing_month=date(2026, 11, 1), total_amount=5000, source_type='provider')
        self.due = PaymentDue.objects.create(user=self.user, obligation_type='card_bill', bill=self.bill,
            occurrence_key='card', due_date=date(2026, 11, 10), confirmed_amount=5000, source_type='provider', confirmation_state='confirmed', paid_amount=5000, payment_state='paid')
        PaymentMatch.objects.create(user=self.user, due=self.due, transaction=self.banktx, matched_amount=5000, matched_at=timezone.now())
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 11, 1), ends_before=date(2026, 12, 1))
        self.budget = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, input_snapshot={'private': str(self.cardtx.pk)})
        CushionEntry.objects.create(user=self.user, cycle=cycle, budget=self.budget, entry_type='allocation', amount=10000, request_key='allocation', occurred_at=timezone.now())
        FinancialProfile.objects.create(user=self.user, monthly_take_home_income=3000000, payday=15, living_budget=1000000)
        conversation = Conversation.objects.create(user=self.user, slots={'private': 'synthetic'})
        ChatMessage.objects.create(user=self.user, conversation=conversation, role='bot', content_type='text', content={'text': 'synthetic'})
        IdempotencyRecord.objects.create(user=self.user, key='old-request', method='POST', path='/api/finance/consents/', request_hash='hash', response_status=201, response_body={'private': 'synthetic'})
        self.other_tx = FinancialTransaction.objects.create(user=self.other, source_kind='manual', source_key='other', occurred_on=date(2026, 11, 1),
            amount=9000, direction='out', transaction_type='spending', confirmation_state='needs_review', confirmed_category_id='UNCLASSIFIED')

    def tx(self, kind, **kwargs):
        return FinancialTransaction.objects.create(user=self.user, source_kind=kind, source_key=kind, occurred_on=date(2026, 11, 1),
            amount=5000, direction='out', transaction_type='spending', confirmation_state='needs_review', confirmed_category_id='UNCLASSIFIED', **kwargs)

    def revoke(self, scope='card', **kwargs):
        return self.client.delete(f'/api/finance/consents/{scope}/', {'confirm': True, **kwargs}, format='json')

    def test_get_returns_actual_grants_revocations_and_absence(self):
        Consent.objects.filter(user=self.user, product_scope='loan').delete()
        Consent.objects.filter(user=self.user, product_scope='bank').update(revoked_at=timezone.now())
        rows = self.client.get('/api/finance/consents/').json()['items']
        self.assertEqual([row['status'] for row in rows], ['revoked', 'granted', 'not_granted'])
        self.assertIsNotNone(rows[0]['revokedAt'])
        self.assertIsNone(rows[2]['grantedAt'])

    def test_card_revoke_deletes_scoped_and_derived_data_preserves_other_scope_and_user(self):
        run = SyncRun.objects.create(user=self.user, connection=self.conn, product_code='card_sync', status='success')
        review = CardApprovalReview.objects.create(user=self.user, issuer_code='0302', source_key='unmatched', identity=[], payload={},
            fetched_at=timezone.now(), started_at=timezone.now(), resolved_card=self.card, transaction=self.cardtx)
        coverage = CardApprovalCoverage.objects.create(run=run, covered_from=date(2026, 10, 1), covered_to=date(2026, 11, 1))
        coverage.reviews.add(review); coverage.cards.add(self.card)
        response = self.revoke()
        self.assertEqual(response.status_code, 200, response.content)
        for model in (Card, CardBill, CardApprovalReview, Budget, BudgetCycle, CushionEntry, ChatMessage, Conversation, IdempotencyRecord):
            self.assertFalse(model.objects.filter(user=self.user).exists(), model.__name__)
        self.assertFalse(CardApprovalCoverage.objects.exists())
        self.assertTrue(FinancialTransaction.objects.filter(pk=self.banktx.pk).exists())
        self.assertFalse(FinancialTransaction.objects.filter(pk=self.cardtx.pk).exists())
        self.assertTrue(FinancialTransaction.objects.filter(pk=self.other_tx.pk).exists())
        self.assertFalse(RecurringExpense.objects.filter(pk=self.expense.pk).exists())
        self.manual.refresh_from_db()
        self.assertIsNone(self.manual.payment_card_id)
        self.assertEqual(self.manual.evidence, [])
        self.conn.refresh_from_db(); self.bank.refresh_from_db()
        self.assertEqual(self.conn.status, 'disconnected')
        self.assertEqual(self.bank.status, 'connected')
        self.assertEqual(self.revoke().status_code, 200)
        self.assertEqual(self.client.get('/api/finance/connect/cards/').status_code, 403)
        self.assertEqual(self.client.post('/api/finance/consents/', {'consents': [{'purpose': 'finance_inquiry', 'productScope': 'card', 'documentVersion': 'v1'}]}, format='json').status_code, 201)
        self.assertEqual(self.client.get('/api/finance/consents/').json()['items'][1]['status'], 'granted')
        self.assertFalse(Card.objects.filter(user=self.user).exists())

    def test_bank_revoke_keeps_loan_shared_connection_and_recalculates_remaining_due(self):
        result = self.revoke('bank')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(BankAccount.objects.filter(user=self.user).exists())
        self.assertTrue(Card.objects.filter(pk=self.card.pk).exists())
        self.due.refresh_from_db()
        self.assertEqual(self.due.paid_amount, 0)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.status, 'connected')
        self.assertEqual(self.client.put('/api/budgets/protect/', {}, format='json').json()['error']['code'], 'consent_required')
        self.assertEqual(self.client.post('/api/budgets/recommend/', {}, format='json', HTTP_IDEMPOTENCY_KEY='new-budget-1').json()['error']['code'], 'consent_required')
        self.assertEqual(self.revoke('loan').status_code, 200)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.status, 'disconnected')

    def test_revoke_needs_confirmation_csrf_and_valid_scope(self):
        self.assertEqual(self.client.delete('/api/finance/consents/card/', {}, format='json').status_code, 400)
        self.assertEqual(self.revoke(unexpected=True).status_code, 400)
        self.assertEqual(self.revoke('invalid').status_code, 404)
        self.client.credentials()
        self.assertEqual(self.revoke().status_code, 403)
        self.assertTrue(Card.objects.filter(pk=self.card.pk).exists())

    @patch('users.api_privacy.codef_privacy.delete_registered_accounts')
    def test_loan_revoke_preserves_manual_loan_bank_data_and_shared_auth(self, remove):
        self.user.codef_connected_id_ciphertext = seal('synthetic-id')
        self.user.save(update_fields=['codef_connected_id_ciphertext'])
        loan = Loan.objects.create(user=self.user, connection=self.bank, origin='provider', status='repaying', source_loan_key='loan')
        manual = Loan.objects.create(user=self.user, origin='manual', status='repaying')
        tx = self.tx('loan', loan=loan)
        due = PaymentDue.objects.create(user=self.user, loan=loan, obligation_type='loan', occurrence_key='loan',
            due_date=date(2026, 11, 10), confirmed_amount=5000, source_type='provider', confirmation_state='confirmed')
        PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=5000)
        response = self.revoke('loan')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(Loan.objects.filter(pk=loan.pk).exists())
        self.assertTrue(Loan.objects.filter(pk=manual.pk).exists())
        self.assertTrue(BankAccount.objects.filter(pk=self.account.pk).exists())
        self.assertTrue(FinancialTransaction.objects.filter(pk=self.banktx.pk).exists())
        remove.assert_not_called()

    def test_active_work_blocks_deletion(self):
        AsyncTask.objects.create(user=self.user, request_key='running', input_hash='hash', task_type='finance_sync', status='running')
        response = self.revoke()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'privacy_busy')
        self.assertTrue(Card.objects.filter(pk=self.card.pk).exists())

    def test_direct_running_sync_also_blocks_deletion(self):
        SyncRun.objects.create(user=self.user, connection=self.conn, product_code='card_sync', status='running')
        self.assertEqual(self.revoke().status_code, 409)
        self.assertTrue(Card.objects.filter(pk=self.card.pk).exists())

    @patch('users.api_privacy.codef_privacy.delete_registered_accounts')
    def test_provider_failure_leaves_local_data_for_retry_without_exposing_provider_message(self, remove):
        self.user.codef_connected_id_ciphertext = seal('synthetic-id')
        self.user.save(update_fields=['codef_connected_id_ciphertext'])
        remove.side_effect = ValueError('secret-raw-provider-response')
        response = self.revoke()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-raw', response.content.decode())
        self.assertTrue(Card.objects.filter(pk=self.card.pk).exists())
        self.assertTrue(Consent.objects.filter(user=self.user, product_scope='card', revoked_at=None).exists())
        remove.side_effect = None
        self.assertEqual(self.revoke().status_code, 200)
        remove.assert_called_with('synthetic-id', {'CD'})

    def test_withdraw_requires_app_password_and_deletes_full_graph_and_all_sessions(self):
        second = APIClient(); second.force_login(self.user)
        other_client = APIClient(); other_client.force_login(self.other)
        self.assertEqual(self.client.delete('/api/auth/me/', {'password': 'wrong', 'confirm': True}, format='json').status_code, 400)
        user_id = self.user.pk
        response = self.client.delete('/api/auth/me/', {'password': 'Synthetic123!', 'confirm': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertFalse(FinancialTransaction.objects.filter(user_id=user_id).exists())
        self.assertFalse(CardApprovalCoverage.objects.filter(run__user_id=user_id).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.other.pk).exists())
        self.assertEqual(second.get('/api/auth/me/').status_code, 403)
        self.assertEqual(other_client.get('/api/auth/me/').status_code, 200)
        self.assertFalse(any(row.get_decoded().get('_auth_user_id') == str(user_id) for row in Session.objects.all()))

    @patch('users.api_privacy.codef_privacy.delete_registered_accounts')
    def test_withdraw_provider_failure_preserves_account_and_sessions_for_retry(self, remove):
        self.user.codef_connected_id_ciphertext = seal('synthetic-id')
        self.user.save(update_fields=['codef_connected_id_ciphertext'])
        remove.side_effect = ValueError('failure')
        response = self.client.delete('/api/auth/me/', {'password': 'Synthetic123!', 'confirm': True}, format='json')
        self.assertEqual(response.status_code, 502)
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertEqual(self.client.get('/api/auth/me/').status_code, 200)
        remove.assert_called_with('synthetic-id', None)

    @patch('finance.codef._client')
    def test_withdraw_completes_when_provider_accounts_are_already_absent(self, client):
        import json
        self.user.codef_connected_id_ciphertext = seal('synthetic-id')
        self.user.save(update_fields=['codef_connected_id_ciphertext'])
        client.return_value.get_account_list.return_value = json.dumps({
            'result': {'code': 'CF-04014'}, 'data': {'accountList': []},
        })
        user_id = self.user.pk
        response = self.client.delete('/api/auth/me/', {'password': 'Synthetic123!', 'confirm': True}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertFalse(FinancialTransaction.objects.filter(user_id=user_id).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.other.pk).exists())
        client.return_value.delete_account.assert_not_called()

    def test_anonymous_cannot_read_revoke_or_delete(self):
        client = APIClient()
        for response in (client.get('/api/finance/consents/'), client.delete('/api/finance/consents/card/'), client.delete('/api/auth/me/')):
            self.assertEqual(response.status_code, 403)

    def test_my_summary_counts_saved_values_and_only_active_connections(self):
        RecurringSaving.objects.create(user=self.user, name='저축', amount=200000, status='confirmed', source_type='manual')
        RecurringSaving.objects.create(user=self.user, name='후보', amount=100000, status='suggested', source_type='detected')
        result = self.client.get('/api/my/')
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertEqual(body['salary']['takeHomeIncome'], 3000000)
        self.assertEqual(body['fixed'], {'count': 2, 'monthlyTotal': 11000})
        self.assertEqual(body['savings'], {'count': 1, 'monthlyTotal': 200000})
        self.assertEqual(body['protect'], {'livingBudget': 1000000, 'savingsFloor': None, 'cushionMonthly': None})
        self.assertEqual(body['connections'], {'bankCount': 1, 'accountCount': 1, 'cardCount': 1})
        self.conn.status='disconnected'; self.conn.disconnected_at=timezone.now(); self.conn.save()
        self.assertEqual(self.client.get('/api/my/').json()['connections']['cardCount'], 0)

    def test_empty_my_summary_has_null_unset_values_and_zero_actual_counts(self):
        self.client.force_login(self.other)
        body = self.client.get('/api/my/').json()
        self.assertIsNone(body['salary']['takeHomeIncome'])
        self.assertEqual(body['fixed'], {'count': 0, 'monthlyTotal': 0})
        self.assertIsNone(body['protect']['livingBudget'])

    @patch('users.api_my.timezone.localdate', return_value=date(2026, 11, 5))
    def test_my_summary_preserves_pending_payday_change(self, today):
        FinancialProfile.objects.filter(user=self.user).update(scheduled_payday=20, payday_changes_on=date(2026, 11, 20), payday_previous_start=date(2026, 10, 15))
        body = self.client.get('/api/my/').json()
        self.assertEqual(body['salary']['payday'], 15)
        self.assertEqual(body['salary']['pendingPaydayChange'], {'payday': 20, 'effectiveOn': '2026-11-20'})
