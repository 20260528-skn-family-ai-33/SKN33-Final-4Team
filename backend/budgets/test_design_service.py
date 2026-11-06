"""Synthetic service policies only; no external model/LLM calls."""
from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth import get_user_model
from finance.models import BankAccount, Connection, SyncRun
from users.models import CategoryMinimum, CategoryPriority

from . import design_service as service
from .design_candidates import CATEGORY_NAMES
from .models import Budget, BudgetCategorySetting, Bucket
from .test_design_candidates import inp, limits, priorities
from . import test_recommend as api_fixtures
from .recommend import EngineCheckFailed


def history(count=11):
    result = {}
    active = [c for c in CATEGORY_NAMES if c not in ('HOUSING_TELECOM', 'TRAVEL_LODGING')]
    for i, month in enumerate(('2026-08', '2026-09', '2026-10')):
        items = []
        for category in active:
            n = (4, 4, 3)[i] if category == 'FOOD' and count == 11 else 1
            items += [{'occurred_on': month+'-15', 'transaction_type': 'spending', 'funding': 'living',
                       'net_amount': 100000//n+(100000 % n if j == 0 else 0), 'category': category} for j in range(n)]
        result[month] = {'coverage_complete': True,
            'normalization_checks': dict.fromkeys(('duplicates', 'cancellations', 'refunds'), 'verified'), 'items': items}
    return result


def policy():
    return {'a': limits(inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'))),
            'b_cut_rates': dict.fromkeys(CATEGORY_NAMES, 25)}


class DesignServiceTest(SimpleTestCase):
    def setUp(self):
        self.snapshot = {'engine_input': inp(priorities=priorities('FOOD', 'EDUCATION', 'MEDICAL_HEALTH')), 'etc': 0}
        self.enterContext(override_settings(BUDGET_DESIGN_POLICY=policy()))
        self.predict = self.enterContext(patch.object(service, 'predict_ml', return_value={'ratios': dict.fromkeys(CATEGORY_NAMES.values(), .1)}))
        self.enterContext(patch('socket.socket', side_effect=AssertionError('No network')))

    def test_unset_policy_blocks_before_inference(self):
        with override_settings(BUDGET_DESIGN_POLICY=None), patch.dict('os.environ', {'BUDGET_DESIGN_POLICY': ''}):
            out = service.recommend(None, self.snapshot, 0, date(2026, 11, 5))
        self.assertEqual(out['status'], 'needs_policy')
        self.predict.assert_not_called()

    def test_missing_history_uses_a_and_preserves_original_settings(self):
        before = deepcopy(self.snapshot)
        with patch.object(service, 'history_for_user', return_value={}):
            out = service.recommend(None, self.snapshot, 0, date(2026, 11, 5))
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['selection']['plan'], 'a')
        self.assertEqual(service.validate_funding(self.snapshot, out, 0), 0)
        self.assertEqual(self.snapshot, before)

    def test_transport_floor_uses_history_even_when_a_is_selected(self):
        self.snapshot['engine_input']['has_vehicle'] = True
        records = history(count=3)
        for row in records.values():
            for item in row['items']:
                if item['category'] == 'TRANSPORT_CAR':
                    item['net_amount'] = 30000
        with patch.object(service, 'history_for_user', return_value=records), \
                patch.object(service, 'build_candidates', wraps=service.build_candidates) as build:
            out = service.recommend(None, self.snapshot, 0, date(2026, 11, 5))
        self.assertEqual(out['_design']['selection']['plan'], 'a')
        self.assertEqual(build.call_args.kwargs['reality_profile']['transport_history'], 30000)
        self.assertGreaterEqual(out['allocations']['TRANSPORT_CAR'], 30000)

    def test_b_funds_shortage_and_metadata_is_json_serializable(self):
        import json
        self.snapshot['engine_input']['target'] = 500000
        self.snapshot['engine_input']['minimums'] = dict.fromkeys(
            ('FOOD', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING'), 100000)
        with patch.object(service, 'history_for_user', return_value=history()):
            out = service.recommend(None, self.snapshot, 150000, date(2026, 11, 5))
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['selection']['plan'], 'b')
        self.assertEqual(out['_design']['spare_transfer'], 100000)
        self.assertEqual(sum(out['allocations'].values()), 600000)
        json.dumps(out['_design'], allow_nan=False)
        with self.assertRaises(EngineCheckFailed):
            service.validate_funding(self.snapshot, out, 99999)

    def test_b_needs_no_category_percentage_and_ml_failure_returns_no_budget(self):
        with patch.object(service, 'history_for_user', return_value=history()), override_settings(BUDGET_DESIGN_POLICY={'a': policy()['a']}):
            self.assertEqual(service.recommend(None, self.snapshot, 0, date(2026, 11, 5))['status'], 'proposed')
        self.predict.side_effect = ValueError('private model path')
        with patch.object(service, 'history_for_user', return_value={}):
            out = service.recommend(None, self.snapshot, 0, date(2026, 11, 5))
        self.assertEqual(out['status'], 'needs_data')
        self.assertNotIn('private', str(out))

    def test_initial_a_total_limit_is_twenty_percent_of_living_including_etc(self):
        snapshot = deepcopy(self.snapshot)
        snapshot['engine_input']['target'] = 900000
        snapshot['etc'] = 100000
        with patch.object(service, 'history_for_user', return_value={}), \
                patch.object(service, 'build_candidates', wraps=service.build_candidates) as build:
            out = service.recommend(None, snapshot, 0, date(2026, 11, 5))
        self.assertEqual(out['status'], 'proposed')
        resolved = build.call_args.kwargs['policy']
        self.assertEqual(resolved['total_limit'], 200000)
        self.assertTrue(all(v is None for v in resolved['increase_rates'].values()))
        self.assertTrue(all(v == 1000000 for v in resolved['decrease_limits'].values()))
        engine = service.build_candidates(snapshot['engine_input'], dict.fromkeys(CATEGORY_NAMES, 0.1),
                                          policy=resolved, reality_profile={'has_vehicle': False})
        self.assertEqual(sum(t['amount'] for t in engine['transfers']), 200000)


    def test_b_candidates_reach_final_amounts_and_metadata_without_changing_settings(self):
        records = history()
        for month, row in records.items():
            row['items'] += [{'occurred_on': month+'-15', 'transaction_type': 'spending',
                'funding': 'living', 'net_amount': 25000, 'category': 'UNCLASSIFIED',
                'suggestion_candidates': [{'category': 'FOOD', 'confidence': .8}],
                'suggestion_source': 'ml'} for _ in range(2)]
        self.snapshot['engine_input']['locks'] = {'EDUCATION': 100001}
        self.snapshot['engine_input']['minimums'] = {'FOOD': 120000}
        before = deepcopy((self.snapshot, records))
        with patch.object(service, 'history_for_user', return_value=records):
            out = service.recommend(None, self.snapshot, 200000, date(2026, 11, 6))
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['selection']['plan'], 'b')
        self.assertEqual(out['allocations']['FOOD'], 150000)
        self.assertEqual(out['allocations']['EDUCATION'], 100001)
        self.assertEqual(out['_design']['spare_transfer'], 0)
        self.assertEqual(out['_design']['candidate_adjustment']['status'], 'applied')
        self.assertEqual(service.validate_funding(self.snapshot, out, 200000), 0)
        self.assertEqual((self.snapshot, records), before)

    def test_private_history_snapshot_carries_candidates_and_hashed_merchant_only(self):
        candidates = [{'category': 'FOOD', 'confidence': .8}]
        tx = SimpleNamespace(merchant_name='synthetic private merchant',
                             suggestion_candidates=candidates, suggestion_source='ml')
        rows = Mock()
        rows.filter.return_value.order_by.return_value = [tx]
        row = {'transaction_id': 'synthetic-private-id', 'occurred_on': '2026-08-15',
               'transaction_type': 'spending', 'net_amount': 25000, 'category': 'UNCLASSIFIED', 'funding': 'living'}
        with patch('finance.api.listed_transactions', return_value=rows), \
                patch('finance.api.transaction_item', return_value=row), \
                patch('spending_analysis.coverage.coverage', return_value={'state': 'complete'}):
            data = service.history_for_user(None, date(2026, 11, 6))
        item = data['2026-08']['items'][0]
        self.assertEqual(item['suggestion_candidates'], candidates)
        self.assertEqual(item['suggestion_source'], 'ml')
        self.assertEqual(len(item['merchant_key']), 64)
        self.assertNotIn(tx.merchant_name, str(data))
        item['suggestion_candidates'][0]['confidence'] = .1
        self.assertEqual(candidates[0]['confidence'], .8)

    def test_b_service_reports_final_candidate_increase_without_cutting_beauty(self):
        records = history()
        values = {'FOOD': 130000, 'TRANSPORT_CAR': 107000, 'HOUSEHOLD': 31000,
                  'FASHION_SHOPPING': 90000, 'MEDICAL_HEALTH': 40000, 'BEAUTY': 47000}
        for index, (month, row) in enumerate(records.items()):
            count = (4, 4, 3)[index]
            row['items'] = [{'occurred_on': month+'-15', 'transaction_type': 'spending', 'funding': 'living',
                'net_amount': amount, 'category': c} for c, amount in values.items() if c != 'FOOD']
            row['items'] += [{'occurred_on': month+'-15', 'transaction_type': 'spending', 'funding': 'living',
                'net_amount': 130000//count + (130000 % count if i == 0 else 0), 'category': 'FOOD'} for i in range(count)]
            row['items'].append({'occurred_on': month+'-15', 'transaction_type': 'spending', 'funding': 'living',
                'net_amount': 30000, 'category': 'UNCLASSIFIED', 'suggestion_source': 'ml',
                'suggestion_candidates': [{'category': 'FOOD', 'confidence': .8}]})
        self.snapshot['engine_input'].update(target=480000, locks={'EDUCATION': 21345},
            priorities=priorities('HOUSEHOLD', 'FASHION_SHOPPING', 'MEDICAL_HEALTH', 'FOOD', 'CULTURE_LEISURE', 'TRANSPORT_CAR'))
        with patch.object(service, 'history_for_user', return_value=records):
            out = service.recommend(None, self.snapshot, 200000, date(2026, 11, 6))
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['selection']['plan'], 'b')
        self.assertEqual(out['allocations']['BEAUTY'], 50000)
        self.assertEqual(out['allocations']['TRANSPORT_CAR'], 110000)
        self.assertEqual(out['allocations']['FOOD'], 135000)
        self.assertEqual(out['_design']['candidate_adjustment']['corrections'][0]['amount'], 5000)
        self.assertEqual(service.validate_funding(self.snapshot, out, 200000), 0)

    def test_smaller_lock_keeps_b_available_without_spending_spare(self):
        records = history()
        before = deepcopy(records)
        self.snapshot['engine_input'].update(target=750000, locks={'EDUCATION': 10000})
        with patch.object(service, 'history_for_user', return_value=records):
            out = service.recommend(None, self.snapshot, 200000, date(2026, 11, 6))
        self.assertEqual(out['status'], 'proposed')
        self.assertEqual(out['_design']['selection']['plan'], 'b')
        self.assertEqual(out['allocations']['EDUCATION'], 10000)
        self.assertEqual(out['allocations']['FOOD'], 100000)
        self.assertEqual(out['unallocated'], 40000)
        self.assertEqual(service.validate_funding(self.snapshot, out, 200000), 0)
        self.assertEqual(records, before)


class DesignHistoryTest(TestCase):
    def test_collection_coverage_does_not_implicitly_verify_refunds(self):
        user = get_user_model().objects.create_user(email='synthetic-history@example.com')
        connection = Connection.objects.create(user=user, business_type='bank', organization_code='0004', status='connected')
        BankAccount.objects.create(user=user, connection=connection, source_account_key='synthetic', is_selected=True, account_type='demand')
        SyncRun.objects.create(user=user, connection=connection, product_code='bank_sync', status='success',
                               covered_from=date(2026, 8, 1), covered_to=date(2026, 10, 31))
        records = service.history_for_user(user, date(2026, 11, 5))
        self.assertTrue(all(r['coverage_complete'] for r in records.values()))
        self.assertTrue(all(r['normalization_checks']['refunds'] == 'unknown' for r in records.values()))
        with override_settings(BUDGET_HISTORY_VERIFIER=lambda u, months: {m: dict.fromkeys(('duplicates', 'cancellations', 'refunds'), 'verified') for m in months}):
            self.assertTrue(all(r['normalization_checks']['refunds'] == 'verified' for r in service.history_for_user(user, date(2026, 11, 5)).values()))

    def test_coverage_states_are_separate_from_normalization(self):
        user = get_user_model().objects.create_user(email='synthetic-coverage@example.com')
        with patch('spending_analysis.coverage.coverage', side_effect=[{'state': 'complete'},
                    {'state': 'partial'}, {'state': 'error'}]) as check:
            result = service.history_for_user(user, date(2026, 11, 6))
        self.assertEqual([result[m]['coverage_state'] for m in result], ['complete', 'partial', 'error'])
        self.assertEqual([result[m]['coverage_complete'] for m in result], [True, False, False])
        self.assertTrue(all(r['normalization_checks']['refunds'] == 'unknown' for r in result.values()))
        self.assertEqual([(c.args[1].start, c.args[1].end_before) for c in check.call_args_list],
            [(date(2026, 8, 1), date(2026, 9, 1)), (date(2026, 9, 1), date(2026, 10, 1)),
             (date(2026, 10, 1), date(2026, 11, 1))])

    def test_unselected_account_with_missing_coverage_cannot_enable_b(self):
        user = get_user_model().objects.create_user(email='synthetic-missing@example.com')
        connection = Connection.objects.create(user=user, business_type='bank', organization_code='0004', status='connected')
        covered_account = BankAccount.objects.create(user=user, connection=connection, source_account_key='covered',
                                                       is_selected=True, account_type='demand')
        BankAccount.objects.create(user=user, connection=connection, source_account_key='missing',
                                   is_selected=False, account_type='demand')
        SyncRun.objects.create(user=user, connection=connection, account=covered_account, product_code='bank_sync',
                               status='success', covered_from=date(2026, 8, 1), covered_to=date(2026, 10, 31))
        with override_settings(BUDGET_HISTORY_VERIFIER=lambda u, months: {m: dict.fromkeys(
                ('duplicates', 'cancellations', 'refunds'), 'verified') for m in months}):
            records = service.history_for_user(user, date(2026, 11, 6))
        self.assertTrue(all(r['coverage_state'] == 'partial' for r in records.values()))
        summary = service.summarize_history(records, as_of=date(2026, 11, 6))
        self.assertEqual(service.select_plan(summary)['plan'], 'a')


@override_settings(BUDGET_DESIGN_ENABLED=True)
class DesignAPITest(TestCase):
    setUpTestData = classmethod(api_fixtures.RecommendAPITest.setUpTestData.__func__)
    post = api_fixtures.RecommendAPITest.post
    task = api_fixtures.RecommendAPITest.task

    def setUp(self):
        api_fixtures.RecommendAPITest.setUp(self)
        self.enterContext(patch('budgets.api.timezone.localdate', return_value=date(2026, 11, 5)))
        CategoryPriority.objects.create(user=self.user, category_id='EDUCATION', rank=2)
        CategoryPriority.objects.create(user=self.user, category_id='MEDICAL_HEALTH', rank=3)
        self.enterContext(override_settings(BUDGET_DESIGN_POLICY=policy()))
        self.enterContext(patch.object(service, 'predict_ml', return_value={'ratios': dict.fromkeys(CATEGORY_NAMES.values(), .1)}))

    def test_missing_policy_returns_prepare_status_without_saving_budget(self):
        with override_settings(BUDGET_DESIGN_POLICY=None), patch.dict('os.environ', {'BUDGET_DESIGN_POLICY': ''}):
            task = self.task(self.post())
        self.assertEqual(task.result['outcome'], 'needs_policy')
        self.assertIsNone(task.result['budget_id'])
        self.assertFalse(Budget.objects.filter(user=self.user).exists())

    def test_a_api_saves_metadata_and_approval_is_explicit(self):
        with patch.object(service, 'history_for_user', return_value={}):
            task = self.task(self.post())
        budget = Budget.objects.get(pk=task.result['budget_id'])
        self.assertEqual(budget.status, 'validated')
        self.assertEqual(budget.validation_summary['design']['selection']['plan'], 'a')
        data = self.client.get('/api/budgets/').json()
        self.assertEqual(data['budget']['engineVersion'], service.ENGINE_VERSION)
        with patch.object(service, 'history_for_user', return_value={}):
            response = self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json', HTTP_IDEMPOTENCY_KEY='synthetic-a-approve')
        self.assertEqual(response.status_code, 200, response.content)

    def test_priority_notice_is_saved_and_returned_on_recommendation(self):
        notice = '식비(1순위): 평소 기준보다 25,000원 줄었어요.'
        real_recommend = service.recommend
        def recommended(*args, **kwargs):
            result = real_recommend(*args, **kwargs)
            result['_design']['explanations'] = [{'category': 'FOOD', 'text': notice}]
            return result
        with patch.object(service, 'history_for_user', return_value={}), \
                patch.object(service, 'recommend', side_effect=recommended):
            task = self.task(self.post())
        self.assertEqual(task.result['outcome'], 'proposed')
        result = self.client.get('/api/budgets/').json()
        food = next(c for c in result['categories'] if c['category'] == 'FOOD')
        self.assertEqual(food['reason'], notice)

    def test_saved_units_and_exact_user_lock_survive_approval(self):
        BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle,
            category_id='EDUCATION', locked=True, locked_amount=12345)
        CategoryMinimum.objects.create(user=self.user, category_id='ETC', min_amount=12345)
        with patch.object(service, 'history_for_user', return_value={}):
            task = self.task(self.post())
            self.assertEqual(task.result['outcome'], 'proposed')
            budget = Budget.objects.get(pk=task.result['budget_id'])
            lines = {r.category_id: r.allocated_amount for r in budget.items.filter(bucket=Bucket.LIVING)}
            self.assertEqual(lines['EDUCATION'], 12345)
            self.assertGreaterEqual(lines['ETC'], 12345)
            self.assertTrue(all(v % 5000 == 0 for c, v in lines.items() if c != 'EDUCATION'))
            response = self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json',
                                        HTTP_IDEMPOTENCY_KEY='synthetic-units-approve')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(budget.items.get(bucket=Bucket.LIVING, category_id='EDUCATION').allocated_amount, 12345)

    def test_b_funding_updates_profile_only_on_approval_and_preserves_total(self):
        self.profile.living_budget = 500000
        self.profile.save(update_fields=['living_budget'])
        for code in ('FOOD', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING'):
            CategoryMinimum.objects.create(user=self.user, category_id=code, min_amount=100000)
        with patch.object(service, 'history_for_user', return_value=history()):
            task = self.task(self.post())
            budget = Budget.objects.get(pk=task.result['budget_id'])
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.living_budget, 500000)
            data = self.client.get('/api/budgets/').json()
            self.assertEqual(data['split']['living'], 600000)
            self.assertEqual(data['split']['spare'], 620000)
            self.assertEqual(data['recommendation']['spareTransfer'], 100000)
            response = self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json', HTTP_IDEMPOTENCY_KEY='synthetic-b-approve')
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.living_budget, 600000)
        # Repeated approval may not transfer the money a second time.
        response = self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json', HTTP_IDEMPOTENCY_KEY='synthetic-b-approve-again')
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.living_budget, 600000)

    def test_changed_history_expires_draft_instead_of_saving_stale_result(self):
        with patch.object(service, 'history_for_user', return_value={}):
            task = self.task(self.post())
        budget = Budget.objects.get(pk=task.result['budget_id'])
        with patch.object(service, 'history_for_user', return_value=history()):
            response = self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json', HTTP_IDEMPOTENCY_KEY='synthetic-expired')
        self.assertEqual(response.status_code, 409, response.content)
        budget.refresh_from_db()
        self.assertEqual(budget.status, 'expired')

    def test_changed_policy_expires_draft_without_changing_profile(self):
        with patch.object(service, 'history_for_user', return_value={}):
            task = self.task(self.post())
            budget = Budget.objects.get(pk=task.result['budget_id'])
            changed = policy()
            changed['a']['total_limit'] += 1000  # Synthetic policy change, not a service default.
            with override_settings(BUDGET_DESIGN_POLICY=changed):
                response = self.client.post(f'/api/budgets/{budget.pk}/approve/', {}, format='json', HTTP_IDEMPOTENCY_KEY='synthetic-policy-change')
        self.assertEqual(response.status_code, 409, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.living_budget, 1120000)

    def test_unset_policy_does_not_expire_a_previous_draft(self):
        with patch.object(service, 'history_for_user', return_value={}):
            task = self.task(self.post())
        budget = Budget.objects.get(pk=task.result['budget_id'])
        with override_settings(BUDGET_DESIGN_POLICY=None), patch.dict('os.environ', {'BUDGET_DESIGN_POLICY': ''}):
            self.assertEqual(self.task(self.post()).result['outcome'], 'needs_policy')
        budget.refresh_from_db()
        self.assertEqual(budget.status, 'validated')
