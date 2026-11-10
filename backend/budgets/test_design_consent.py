"""동의 저장·철회와 예산 설계 전송 권한. 입력은 합성이며 모든 LLM HTTP는 모의 처리."""
from copy import deepcopy
from datetime import date
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from users import consent_documents as documents
from users.models import CategoryMinimum, Consent, FinancialProfile
from finance.models import FinancialTransaction
from . import design_consent as consent
from .models import Budget, BudgetCategorySetting, BudgetCycle
from .test_design_service_llm import ServiceLLMFixture

URL = '/api/budgets/ai-consent/'


def agreement():
    doc = consent.current_document()
    health = consent.current_document(consent.HEALTH_PURPOSE)
    return {'agreed': True, 'documentVersion': doc['version'], 'documentHash': documents.canonical_hash(doc),
            'healthAgreed': True, 'healthDocumentVersion': health['version'],
            'healthDocumentHash': documents.canonical_hash(health)}


@override_settings(BUDGET_DESIGN_ENABLED=True, BUDGET_DESIGN_EXTERNAL_ENABLED=True)
class DesignConsentTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email='synthetic-consent@example.test')
        self.other = get_user_model().objects.create_user(email='synthetic-other@example.test')
        self.client.force_login(self.user)

    def put(self, body=None):
        return self.client.put(URL, json.dumps(agreement() if body is None else body), content_type='application/json')

    def revoke(self, body=None):
        return self.client.delete(URL, json.dumps({'confirm': True} if body is None else body), content_type='application/json')

    def test_default_unchecked_and_exact_document_is_returned(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        shown = response.json()
        self.assertEqual(shown['status'], 'not_granted')
        self.assertTrue(shown['available'])
        self.assertIs(shown['document']['required'], False)
        self.assertEqual(shown['documentHash'], documents.canonical_hash(consent.current_document()))
        self.assertFalse(Consent.objects.exists())

    def test_disabled_service_rejects_new_consents_without_writing(self):
        for changes in ({'BUDGET_DESIGN_ENABLED': False}, {'BUDGET_DESIGN_EXTERNAL_ENABLED': False},
                        {'BUDGET_DESIGN_EXTERNAL_ENABLED': 'true'}):
            with self.subTest(changes=changes), override_settings(**changes):
                self.assertFalse(self.client.get(URL).json()['available'])
                response = self.put()
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['error']['code'], 'budget_ai_unavailable')
                self.assertFalse(Consent.objects.exists())

    def test_existing_consent_can_be_revoked_while_disabled(self):
        self.put()
        with override_settings(BUDGET_DESIGN_EXTERNAL_ENABLED=False):
            state = self.client.get(URL).json()
            self.assertEqual((state['available'], state['status']), (False, 'granted'))
            self.assertEqual(self.revoke().json()['status'], 'revoked')
            self.assertFalse(consent.has_current_consent(self.user))

    def test_explicit_grant_stores_shown_version_and_hash_once(self):
        for _ in range(2):
            self.assertEqual(self.put().json()['status'], 'granted')
        row = Consent.objects.get(user=self.user, purpose='budget_ai')
        self.assertEqual((row.purpose, row.product_scope, row.document_version, row.document_hash),
                         ('budget_ai', None, 'v3', agreement()['documentHash']))
        self.assertEqual(Consent.objects.filter(user=self.user).count(), 2)
        self.assertTrue(consent.has_current_consent(self.user))
        self.assertFalse(consent.has_current_consent(self.other))

    def test_finance_or_other_purpose_is_not_budget_ai_permission(self):
        Consent.objects.create(user=self.user, purpose='finance_inquiry', product_scope='bank',
                               document_version='v2', document_hash='synthetic-finance')
        self.assertFalse(consent.has_current_consent(self.user))

    def test_boolean_and_unknown_input_rejected_without_saving(self):
        for bad in ({}, {**agreement(), 'agreed': False}, {**agreement(), 'agreed': 1},
                    {**agreement(), 'allowExternal': True}, {**agreement(), 'userId': str(self.other.pk)},
                    {**agreement(), 'documentVersion': []}):
            with self.subTest(body=bad):
                self.assertEqual(self.put(bad).status_code, 400)
        self.assertFalse(Consent.objects.exists())

    def test_both_separate_agreements_are_required_and_saved_atomically(self):
        for bad in ({**agreement(), 'healthAgreed': False}, {**agreement(), 'healthAgreed': 1},
                    {k:v for k,v in agreement().items() if not k.startswith('health')},
                    {**agreement(), 'healthDocumentHash': 'synthetic-wrong'}):
            self.assertIn(self.put(bad).status_code, (400, 409))
            self.assertFalse(Consent.objects.exists())
        doc = consent.current_document()
        Consent.objects.create(user=self.user, purpose='budget_ai', document_version=doc['version'],
                               document_hash=documents.canonical_hash(doc))
        self.assertFalse(consent.has_current_consent(self.user))

    def test_prior_v1_cannot_authorize_new_country_and_health_conditions(self):
        Consent.objects.create(user=self.user, purpose='budget_ai', document_version='v1',
                               document_hash=documents.ARCHIVE[('budget_ai', None, 'v1')])
        self.assertFalse(consent.has_current_consent(self.user))
        self.assertEqual(self.client.get(URL).json()['status'], 'needs_renewal')

    def test_current_notice_names_transfer_details_and_archive_is_preserved(self):
        doc = consent.current_document()
        text = json.dumps(doc, ensure_ascii=False)
        for field in ('미국', 'OpenAI OpCo, LLC', 'privacy@openai.com', 'us.api.openai.com', '30일', '거부'):
            self.assertIn(field, text)
        self.assertIn(('budget_ai', None, 'v1'), documents.ARCHIVE)
        self.assertIn(('budget_ai', None, 'v2'), documents.ARCHIVE)

    def test_unknown_version_and_wrong_hash_rejected(self):
        self.assertEqual(self.put({**agreement(), 'documentVersion': 'unknown'}).status_code, 400)
        response = self.put({**agreement(), 'documentHash': 'synthetic-wrong'})
        self.assertEqual((response.status_code, response.json()['error']['code']), (409, 'document_outdated'))
        self.assertFalse(Consent.objects.exists())

    def test_new_version_requires_fresh_agreement(self):
        self.put()
        old = agreement()
        newer = {**consent.current_document(), 'version': 'synthetic-v3'}
        with patch.dict(documents.CURRENT, {('budget_ai', None): newer}), \
                patch.dict(documents.ARCHIVE, {('budget_ai', None, newer['version']): documents.canonical_hash(newer)}):
            self.assertFalse(consent.has_current_consent(self.user))
            self.assertEqual(self.client.get(URL).json()['status'], 'needs_renewal')
            self.assertEqual(self.put(old).status_code, 409)
            self.assertEqual(self.put().json()['status'], 'granted')
        self.assertEqual(Consent.objects.filter(user=self.user).count(), 4)
        self.assertIsNotNone(Consent.objects.get(user=self.user, purpose='budget_ai', document_version='v3').revoked_at)

    def test_revoke_and_regrant_keep_history_and_financial_settings(self):
        profile = FinancialProfile.objects.create(user=self.user, living_budget=1000000)
        minimum = CategoryMinimum.objects.create(user=self.user, category_id='FOOD', min_amount=150001)
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        locked = BudgetCategorySetting.objects.create(user=self.user, cycle=cycle, category_id='EDUCATION',
                                                      locked=True, locked_amount=12345)
        budget = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status='approved', funding_amount=2000000)
        financial = Consent.objects.create(user=self.user, purpose='finance_inquiry', product_scope='bank',
                                           document_version='v2', document_hash='synthetic-finance')
        tx = FinancialTransaction.objects.create(user=self.user, source_kind='manual', request_key='synthetic-consent-tx',
            occurred_on=date(2026, 10, 15), amount=20000, direction='out', transaction_type='spending',
            confirmed_category_id='FOOD', confirmation_state='confirmed')
        self.put()
        for _ in range(2):
            self.assertEqual(self.revoke().json()['status'], 'revoked')
            self.assertFalse(consent.has_current_consent(self.user))
        for row in (profile, minimum, locked, budget, financial, tx):
            before = deepcopy({f.attname: getattr(row, f.attname) for f in row._meta.concrete_fields})
            row.refresh_from_db()
            self.assertEqual({f.attname: getattr(row, f.attname) for f in row._meta.concrete_fields}, before)
        self.assertEqual(self.put().json()['status'], 'granted')
        self.assertEqual(Consent.objects.filter(user=self.user, purpose='budget_ai').count(), 2)

    def test_revoke_requires_confirmation(self):
        self.put()
        for bad in ({}, {'confirm': 1}, {'confirm': False}, {'confirm': True, 'scope': 'bank'}):
            self.assertEqual(self.revoke(bad).status_code, 400)
        self.assertTrue(consent.has_current_consent(self.user))

    def test_all_methods_require_authentication_and_writes_need_csrf(self):
        client = Client()
        for method in ('get', 'put', 'delete'):
            self.assertEqual(getattr(client, method)(URL).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for method in ('put', 'delete'):
            response = getattr(client, method)(URL, '{}', content_type='application/json')
            self.assertEqual(response.status_code, 403)


class ConsentedServiceLLMTest(ServiceLLMFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(override_settings(BUDGET_DESIGN_ENABLED=True, BUDGET_DESIGN_EXTERNAL_ENABLED=True))
        self.user = get_user_model().objects.create_user(email='synthetic-llm-consent@example.test')
        self.client.force_login(self.user)

    def grant(self):
        response = self.client.put(URL, json.dumps(agreement()), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_and_b_require_own_consent_then_stop_after_revocation(self):
        for plan in ('a', 'b'):
            with self.subTest(plan=plan):
                self.a_requests.clear()
                self.b_requests.clear()
                blocked = self.run_plan(plan, user=self.user, allow_external=True)
                self.assertEqual(blocked['_design']['llm']['reason'], 'consent_required')
                self.assertFalse(self.a_requests or self.b_requests)
                self.grant()
                allowed = self.run_plan(plan, user=self.user, allow_external=True)
                self.assertEqual(allowed['_design']['llm']['status'], 'completed')
                self.assertTrue(self.a_requests if plan == 'a' else self.b_requests)
                for body in self.a_requests + self.b_requests:
                    self.assertIs(body['store'], False)
                    raw = json.dumps(body)
                    for private in (self.user.email, str(self.user.pk), 'transaction_id', 'merchant_name', 'source_description'):
                        self.assertNotIn(private, raw)
                self.client.delete(URL, json.dumps({'confirm': True}), content_type='application/json')
                counts = (len(self.a_requests), len(self.b_requests))
                revoked = self.run_plan(plan, user=self.user, allow_external=True)
                self.assertEqual(revoked['_design']['llm']['reason'], 'consent_required')
                self.assertEqual((len(self.a_requests), len(self.b_requests)), counts)
                self.assertEqual(revoked['allocations'], blocked['allocations'])

    def test_record_tampering_or_another_users_consent_cannot_allow_external(self):
        self.grant()
        record = Consent.objects.get(user=self.user, purpose='budget_ai')
        record.document_hash = 'synthetic-invalid'
        record.save(update_fields=['document_hash'])
        other = get_user_model().objects.create_user(email='synthetic-granted@example.test')
        Consent.objects.create(user=other, purpose='budget_ai', document_version='v3', document_hash=agreement()['documentHash'])
        for plan in ('a', 'b'):
            result = self.run_plan(plan, user=self.user, allow_external=True)
            self.assertEqual(result['_design']['llm']['reason'], 'consent_required')
        self.assertFalse(self.a_requests or self.b_requests)

    def test_consent_does_not_switch_on_service_external_calls_or_langsmith(self):
        self.grant()
        with patch('budgets.design_tracing._start', side_effect=AssertionError('No LangSmith')):
            for plan in ('a', 'b'):
                result = self.run_plan(plan, user=self.user)
                self.assertEqual(result['_design']['llm']['reason'], 'external_disabled')
        self.assertFalse(self.a_requests or self.b_requests)
