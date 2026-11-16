"""Salary increases are allocated once, after the saved request, without touching approved money."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from chat.models import Conversation
from users.models import CategoryPriority
from .models import Budget, BudgetAdjustmentSchedule, BudgetCategorySetting
from .test_adjustment_core import item
from . import test_adjustment_service as fixtures
from .design_candidates import CATEGORY_NAMES
from .test_design_service import history


URL = '/api/budgets/salary-review/'


@override_settings(CHAT_TODAY='2026-11-05', BUDGET_DESIGN_EXTERNAL_ENABLED=False)
class SalaryRedistributionTest(TestCase):
    def setUp(self):
        fixtures.AdjustmentServiceTest.setUp(self)
        self.old_context = deepcopy(self.snapshot)
        self.snapshot.update(monthly_income=4000000, effective_income=4000000,
                             next_cycle_starts_on='2026-11-25')
        self.profile.monthly_take_home_income = 4000000
        self.profile.birth_date = date(1999, 1, 1)
        self.profile.gender = 'female'
        self.profile.has_car = False
        self.profile.save(update_fields=['monthly_take_home_income', 'birth_date', 'gender', 'has_car'])
        CategoryPriority.objects.bulk_create([
            CategoryPriority(user=self.user, category_id=code, rank=rank)
            for code, rank in self.snapshot['priorities'].items()
        ])
        self.history = self.enterContext(patch('budgets.design_service.history_for_user', return_value={}))
        self.predict = self.enterContext(patch('budgets.design_service.predict_ml',
            return_value={'ratios': dict.fromkeys(CATEGORY_NAMES.values(), .1),
                          'source': 'model', 'artifact_id': 'synthetic',
                          'model_checksum': 'a' * 64, 'used_fallback': False}))
        self.client = APIClient()
        self.client.force_login(self.user)
        self.counter = 0

    def handoff(self, *, category='EDUCATION', amount=500000, state=None, planning=None):
        requests = [item(category, amount)]
        value = {
            'requests': requests,
            'scope': 'next_cycle' if planning else 'cycle',
            'pending': {'kind': 'salary_handoff'},
            'salary_handoff': {
                'items': requests,
                'targets': {category: self.old_context['allocations'].get(category, 0) + amount},
                'context': deepcopy(self.old_context),
                'rebuild': True,
            },
            **(state or {}),
        }
        if planning:
            value['planning'] = planning
        self.talk.slots = {'state': {'agent:budget': value}}
        self.talk.save(update_fields=['slots'])
        return value

    def review(self, *, conversation=True):
        response = self.client.get(URL, {'conversationId': str(self.talk.pk)} if conversation else {})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def post(self, url, data, *, key=None):
        self.counter += 1
        return self.client.post(url, data, format='json',
                                HTTP_IDEMPOTENCY_KEY=key or f'salary-redistribution-{self.counter}')

    def preview(self, *, conversation=True, percent=30, review=None):
        review = review or self.review(conversation=conversation)
        response = self.post(URL, {
            'basisHash': review['basisHash'], 'savingPercent': percent,
            **({'conversationId': str(self.talk.pk)} if conversation else {}),
        })
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def approve(self, proposal, *, key=None):
        return self.post(f"/api/budgets/proposals/{proposal['proposalId']}/approve/",
                         {'keepForNextCycle': proposal.get('keepForNextCycle', False)}, key=key)

    def assert_conserved(self, proposal, income=4000000):
        self.assertEqual(800000 + sum(proposal['allocations'].values()) + sum(proposal['buckets'].values()), income)

    def test_saved_request_is_reserved_before_savings_and_other_allocation(self):
        self.handoff()
        before = list(self.base.items.values('bucket', 'category_id', 'allocated_amount'))
        review = self.review()
        self.assertEqual(review['status'], 'ready')
        self.assertEqual(review['increase'], 1000000)
        self.assertEqual(review['requestedAmount'], 500000)
        self.assertEqual(review['remainingAmount'], 500000)
        preview = self.preview(review=review)
        proposal = preview['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 750000)
        self.assertEqual(preview['summary']['allocationReview']['plan'], 'a')
        self.assertEqual(preview['summary']['allocationReview']['status'], 'ready', preview['summary'])
        self.assertEqual(proposal['allocations']['FOOD'], 400000)
        self.assertEqual(proposal['allocations']['FASHION_SHOPPING'], 200000)
        self.assertEqual(proposal['allocations']['ETC'], 50000)
        self.assertGreater(proposal['allocations']['HOUSEHOLD'], 50000)
        self.assertEqual(proposal['buckets']['CUSHION_MONTHLY'], 200000)
        self.assertGreaterEqual(proposal['buckets']['SPARE'], 400000)
        self.assert_conserved(proposal)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')
        self.assertEqual(list(self.base.items.values('bucket', 'category_id', 'allocated_amount')), before)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.living_budget, self.profile.savings_floor, self.profile.cushion_monthly),
                         (1000000, 600000, 200000))

    def test_b_history_only_tops_up_needed_categories_and_leaves_surplus(self):
        self.handoff()
        self.history.return_value = history()
        result = self.preview()
        proposal = result['proposal']
        self.assertEqual(result['summary']['allocationReview']['plan'], 'b')
        self.assertEqual(result['summary']['allocationReview']['status'], 'ready', result['summary'])
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['allocations']['FOOD'], 400000)
        self.assertEqual(proposal['allocations']['FASHION_SHOPPING'], 200000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 750000)
        self.assertEqual(proposal['buckets']['CUSHION_MONTHLY'], 200000)
        self.assertGreater(proposal['buckets']['SPARE'], 400000)
        self.assertGreater(result['summary']['allocationReview']['unallocatedAmount'], 0)
        self.assert_conserved(proposal)

    def test_mypage_and_chat_share_the_same_ab_review(self):
        for records, plan in (({}, 'a'), (history(), 'b')):
            with self.subTest(plan=plan):
                self.history.return_value = records
                self.talk.slots = {'state': {'agent:budget': {}}}
                self.talk.save(update_fields=['slots'])
                chat = self.preview()
                mypage = self.preview(conversation=False)
                self.assertEqual(chat['proposal']['allocations'], mypage['proposal']['allocations'])
                self.assertEqual(chat['proposal']['buckets'], mypage['proposal']['buckets'])
                self.assertEqual(chat['summary']['allocationReview']['plan'], plan)

    def test_b_can_approve_salary_increase_left_entirely_in_spare(self):
        self.history.return_value = history()
        self.snapshot['allocations'] = {code: (100000 if amount else 0)
                                       for code, amount in self.snapshot['allocations'].items()}
        self.snapshot['buckets']['SPARE'] = 500000
        self.snapshot['bucket_available']['SPARE'] = 500000
        self.snapshot['minimums'] = {}
        for code, amount in self.snapshot['allocations'].items():
            self.base.items.filter(category_id=code).update(allocated_amount=amount)
        result = self.preview(conversation=False, percent=0)
        proposal = result['proposal']
        self.assertEqual(result['summary']['allocationReview']['status'], 'ready', result['summary'])
        self.assertEqual(result['summary']['allocationReview']['allocatedAmount'], 0)
        self.assertEqual(proposal['allocations'], self.snapshot['allocations'])
        self.assertEqual(proposal['buckets']['SPARE'], 1500000)
        self.assertEqual(proposal['changes'], [{'category': 'SPARE', 'before': 500000,
                                               'after': 1500000, 'diff': 1000000, 'direct': False}])
        self.assert_conserved(proposal)
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 200, response.content)
        approved = Budget.objects.get(pk=proposal['proposalId'])
        self.assertEqual(approved.funding_amount, 4000000)
        self.snapshot.update(base_budget_id=str(approved.pk), funding_amount=4000000,
                             buckets=deepcopy(proposal['buckets']))
        self.assertEqual(self.review(conversation=False)['status'], 'no_increase')

    def test_failed_ab_review_keeps_saved_request_and_savings_without_guessing(self):
        self.handoff()
        self.predict.side_effect = ValueError('model unavailable')
        result = self.preview()
        proposal = result['proposal']
        self.assertEqual(proposal['allocations'], {**self.old_context['allocations'], 'EDUCATION': 550000})
        self.assertEqual(proposal['buckets'], {'SAVINGS': 750000, 'CUSHION_MONTHLY': 200000, 'SPARE': 750000})
        self.assertEqual(result['summary']['allocationReview']['unallocatedAmount'], 350000)
        self.assertIn('추가 배분 근거', result['summary']['allocationReview']['message'])
        self.assert_conserved(proposal)

    def test_previous_proportional_preview_requires_new_ab_review(self):
        self.handoff()
        proposal = self.preview()['proposal']
        budget = Budget.objects.get(pk=proposal['proposalId'])
        budget.input_snapshot['adjustment_context']['salary_review'].pop('policy')
        budget.save(update_fields=['input_snapshot'])
        self.assertFalse(self.review().get('proposal'))
        self.assertEqual(self.approve(proposal).status_code, 409)

    def test_mypage_without_request_reviews_increase_after_savings_choice(self):
        review = self.review(conversation=False)
        self.assertEqual(review['requestedAmount'], 0)
        self.assertEqual(review['remainingAmount'], 1000000)
        proposal = self.preview(conversation=False, review=review)['proposal']
        self.assertEqual(proposal['buckets']['SAVINGS'], 900000)
        self.assertGreater(proposal['allocations']['EDUCATION'], 50000)
        self.assert_conserved(proposal)

    def test_selected_example_donor_does_not_replace_the_original_salary_return_request(self):
        self.handoff(state={'plan_selected': True, 'sources': ['LIVING'],
                            'plan_request': {'items': [item('EDUCATION', 500000)], 'sources': []}})
        proposal = self.preview()['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 750000)
        self.assertTrue(all(row['from'] == 'SPARE' for row in proposal['transfers']))

    def test_explicit_source_before_or_after_example_selection_is_not_discarded(self):
        for selected, original in ((True, ['LIVING']), (False, [])):
            with self.subTest(selected=selected):
                self.handoff(state={'plan_selected': selected, 'sources': ['LIVING'],
                                    'plan_request': {'items': [item('EDUCATION', 500000)], 'sources': original}})
                result = self.review()
                self.assertEqual(result['status'], 'needs_input')
                self.assertEqual(result['reason'], 'explicit_source_requires_review')

    def test_zero_and_full_savings_choices_preserve_the_original_request(self):
        self.handoff()
        for percent, expected in ((0, 600000), (100, 1100000)):
            with self.subTest(percent=percent):
                proposal = self.preview(percent=percent)['proposal']
                self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
                self.assertEqual(proposal['buckets']['SAVINGS'], expected)
                self.assert_conserved(proposal)

    def test_repeated_preview_uses_approved_basis_not_the_previous_draft(self):
        self.handoff()
        first = self.preview()['proposal']
        second = self.preview()['proposal']
        self.assertEqual(first['allocations'], second['allocations'])
        self.assertEqual(first['buckets'], second['buckets'])
        self.assertEqual(self.review()['remainingAmount'], 500000)
        self.assertEqual(Budget.objects.filter(status='approved').count(), 1)

    def test_preview_advances_conversation_revision_and_preserves_other_state(self):
        state = self.handoff()
        self.talk.slots.update(schema=1, rev=7)
        self.talk.slots['state']['agent:spending'] = {'request': {'funds_overview': True}}
        self.talk.save(update_fields=['slots'])
        proposal = self.preview()['proposal']
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.slots['rev'], 8)
        self.assertEqual(self.talk.slots['schema'], 1)
        self.assertEqual(self.talk.slots['state']['agent:spending'], {'request': {'funds_overview': True}})
        budget_state = self.talk.slots['state']['agent:budget']
        self.assertEqual(budget_state['salary_handoff'], state['salary_handoff'])
        self.assertEqual(budget_state['salary_review_proposal']['proposal_id'], proposal['proposalId'])

    def test_already_reflected_part_of_request_is_not_added_twice(self):
        self.handoff()
        self.snapshot['allocations']['EDUCATION'] = 250000
        self.snapshot['buckets']['SPARE'] = 200000
        self.snapshot['bucket_available']['SPARE'] = 200000
        self.base.items.filter(category_id='EDUCATION').update(allocated_amount=250000)
        review = self.review()
        self.assertEqual(review['requestedAmount'], 300000)
        self.assertEqual(review['remainingAmount'], 700000)
        proposal = self.preview(review=review)['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 810000)
        self.assert_conserved(proposal)

    def test_approval_is_idempotent_and_the_approved_salary_increase_is_not_applied_again(self):
        self.handoff()
        proposal = self.preview()['proposal']
        response = self.approve(proposal, key='approve-salary-once')
        self.assertEqual(response.status_code, 200, response.content)
        again = self.approve(proposal, key='approve-salary-once')
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(response.json(), again.json())
        approved = Budget.objects.get(pk=proposal['proposalId'])
        self.assertEqual(approved.status, 'approved')
        self.assertEqual(approved.funding_amount, 4000000)
        self.assertEqual(approved.items.get(category_id='EDUCATION').allocated_amount, 550000)
        self.assertEqual(Budget.objects.filter(status='approved').count(), 1)
        self.snapshot.update(base_budget_id=str(approved.pk), funding_amount=4000000,
                             allocations=deepcopy(proposal['allocations']), buckets=deepcopy(proposal['buckets']))
        self.assertEqual(self.review()['status'], 'no_increase')

    def test_actual_income_basis_does_not_spend_an_unreceived_registered_salary_increase(self):
        self.handoff()
        self.snapshot['effective_income'] = 3000000
        review = self.review()
        self.assertEqual(review['status'], 'no_increase')
        self.assertEqual(Budget.objects.count(), 1)

    def test_real_context_reads_latest_approved_amounts_and_actual_cycle_income_basis(self):
        from .adjustment_context import get_adjustment_context

        self.context_reader.side_effect = get_adjustment_context
        self.base.items.filter(category_id='FOOD').update(allocated_amount=450000)
        self.base.items.filter(category_id='FASHION_SHOPPING').update(allocated_amount=150000)
        self.profile.budget_category_targets = {'FOOD': 400000}
        self.profile.save(update_fields=['budget_category_targets'])
        review = self.review(conversation=False)
        self.assertEqual(review['status'], 'ready')
        self.assertEqual(review['before']['FOOD'], 450000)
        self.assertEqual(review['before']['FASHION_SHOPPING'], 150000)
        self.assertEqual((review['incomeBefore'], review['incomeAfter']), (3000000, 4000000))
        self.cycle.income_basis = 3000000
        self.cycle.save(update_fields=['income_basis'])
        self.assertEqual(self.review(conversation=False)['status'], 'no_increase')

    def test_saving_salary_twice_uses_total_change_from_approved_budget(self):
        self.handoff()
        self.snapshot.update(monthly_income=4200000, effective_income=4200000)
        self.profile.monthly_take_home_income = 4200000
        self.profile.save(update_fields=['monthly_take_home_income'])
        review = self.review()
        self.assertEqual((review['increase'], review['remainingAmount']), (1200000, 700000))
        proposal = self.preview(review=review)['proposal']
        self.assertEqual(proposal['buckets']['SAVINGS'], 810000)
        self.assert_conserved(proposal, 4200000)

    def test_stale_basis_is_rejected_without_creating_a_proposal(self):
        self.handoff()
        review = self.review()
        self.snapshot['locks']['FOOD'] = 400000
        response = self.post(URL, {'conversationId': str(self.talk.pk), 'basisHash': review['basisHash'], 'savingPercent': 30})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(Budget.objects.count(), 1)

    def test_other_users_conversation_cannot_be_read_or_used(self):
        other = Conversation.objects.create(user=self.other)
        self.assertEqual(self.client.get(URL, {'conversationId': str(other.pk)}).status_code, 404)
        response = self.post(URL, {'conversationId': str(other.pk), 'basisHash': 'not-owned', 'savingPercent': 30})
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(Budget.objects.count(), 1)

    def test_review_and_preview_match_the_public_api_contract(self):
        from openapi_schema_validator import OAS30Validator, oas30_format_checker
        from config.openapi import build_contract

        self.handoff()
        contract = build_contract()
        results = {'get': self.review(), 'post': self.preview()}
        for method, result in results.items():
            with self.subTest(method=method):
                schema = contract['paths'][URL][method]['responses']['200']['content']['application/json']['schema']
                validator = OAS30Validator({**schema, 'components': contract['components']},
                                           format_checker=oas30_format_checker)
                self.assertEqual(list(validator.iter_errors(result)), [])

    def test_invalid_savings_percent_cannot_change_any_budget(self):
        self.handoff()
        review = self.review()
        for percent in (-1, 101, True, '30', None):
            with self.subTest(percent=percent):
                response = self.post(URL, {'conversationId': str(self.talk.pk), 'basisHash': review['basisHash'], 'savingPercent': percent})
                self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(Budget.objects.count(), 1)

    def test_locks_and_exclusions_remain_unchanged_during_residual_distribution(self):
        self.snapshot['locks']['FOOD'] = 400000
        self.handoff(state={'excluded': ['FASHION_SHOPPING', 'CUSHION_MONTHLY']})
        proposal = self.preview()['proposal']
        self.assertEqual(proposal['allocations']['FOOD'], 400000)
        self.assertEqual(proposal['allocations']['FASHION_SHOPPING'], 200000)
        self.assertEqual(proposal['buckets']['CUSHION_MONTHLY'], 200000)
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assert_conserved(proposal)

    def test_missing_current_spending_keeps_preview_but_blocks_approval(self):
        self.snapshot.update(spending_status='partial', budget_preview=True)
        for code in ('SPARE', 'CUSHION_MONTHLY'):
            self.snapshot['bucket_status'][code] = 'unknown'
            self.snapshot['bucket_available'].pop(code, None)
        self.handoff()
        proposal = self.preview()['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertGreaterEqual(proposal['buckets']['CUSHION_MONTHLY'], 200000)
        self.assertGreaterEqual(proposal['buckets']['SPARE'], 400000)
        self.assertFalse(proposal['canApprove'])
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 409, response.content)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_unknown_spare_reservations_block_approval_even_with_complete_spending(self):
        self.snapshot['bucket_status']['SPARE'] = 'unknown'
        self.snapshot['bucket_available'].pop('SPARE', None)
        self.snapshot['bucket_reasons'] = {'SPARE': 'reservation_unverified'}
        self.handoff()
        proposal = self.preview()['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertGreaterEqual(proposal['buckets']['SPARE'], 400000)
        self.assertFalse(proposal['canApprove'])
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 409, response.content)

    def test_existing_available_spare_satisfies_its_minimum_without_reducing_new_salary_capacity(self):
        self.handoff(state={'spare_minimum': 200000})
        self.snapshot['bucket_available']['SPARE'] = 250000
        proposal = self.preview(percent=100)['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 1100000)
        self.assertEqual(proposal['buckets']['SPARE'], 400000)
        self.assert_conserved(proposal)

    def test_next_cycle_savings_can_receive_all_residual_without_inflating_spare_protection(self):
        self.handoff(planning={'scope': 'next_cycle'})
        proposal = self.preview(percent=100)['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 1100000)
        self.assertEqual(proposal['buckets']['SPARE'], 400000)
        self.assert_conserved(proposal)

    def test_saved_unlock_consent_and_goal_release_survive_salary_review_and_approval(self):
        from .adjustment_core.agent import _lock_snapshot

        self.snapshot['locks']['EDUCATION'] = 50000
        self.snapshot['lock_revisions']['EDUCATION'] = 1
        self.snapshot['category_targets']['FOOD'] = 400000
        self.profile.budget_category_targets = {'FOOD': 400000}
        self.profile.save(update_fields=['budget_category_targets'])
        lock = BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id='EDUCATION',
                                                   locked=True, locked_amount=50000, revision_no=1)
        self.handoff(state={'unlock_categories': ['EDUCATION'],
                            'unlock_consents': _lock_snapshot(self.snapshot, ['EDUCATION']),
                            'clear_targets': ['FOOD']})
        proposal = self.preview()['proposal']
        self.assertEqual(proposal['unlockCategories'], ['EDUCATION'])
        self.assertEqual(proposal['targetChanges'], {'FOOD': None})
        lock.refresh_from_db()
        self.assertTrue(lock.locked)
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 200, response.content)
        lock.refresh_from_db()
        self.assertFalse(lock.locked)
        self.profile.refresh_from_db()
        self.assertNotIn('FOOD', self.profile.budget_category_targets)

    def test_increase_shortfall_preserves_request_and_does_not_use_old_reserves(self):
        state = self.handoff(amount=1200000)
        review = self.review()
        self.assertEqual(review['status'], 'needs_input')
        self.assertEqual(review['shortfallAmount'], 200000)
        self.assertNotIn('proposal', review)
        self.assertEqual(Budget.objects.count(), 1)
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.slots['state']['agent:budget'], state)

    def test_ongoing_request_and_goal_release_preserve_their_scope_after_redistribution(self):
        self.profile.budget_category_targets = {'FOOD': 400000}
        self.profile.save(update_fields=['budget_category_targets'])
        self.snapshot['category_targets']['FOOD'] = 400000
        self.handoff(state={'scope': 'ongoing', 'clear_targets': ['FOOD']})
        proposal = self.preview()['proposal']
        self.assertEqual(proposal['scope'], 'ongoing')
        self.assertTrue(proposal['keepForNextCycle'])
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.budget_category_targets.get('EDUCATION'), 550000)
        self.assertNotIn('FOOD', self.profile.budget_category_targets)
        self.assertEqual(self.profile.savings_floor, proposal['buckets']['SAVINGS'])
        self.assertEqual(self.profile.cushion_monthly, proposal['buckets']['CUSHION_MONTHLY'])
        self.assertEqual(self.profile.living_budget, sum(proposal['allocations'].values()))

    def test_next_cycle_request_does_not_apply_current_spending_or_change_current_budget(self):
        self.snapshot.update(spending_status='partial', budget_preview=True)
        self.snapshot['spent']['EDUCATION'] = 900000
        self.handoff(planning={'scope': 'next_cycle'})
        proposal = self.preview()['proposal']
        self.assertEqual(proposal['scope'], 'next_cycle')
        self.assertEqual(proposal['effectiveOn'], '2026-11-25')
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertTrue(proposal['canApprove'])
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(BudgetAdjustmentSchedule.objects.filter(status='scheduled').count(), 1)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')
        self.assertEqual(self.base.items.get(category_id='EDUCATION').allocated_amount, 50000)
        again = self.review()
        self.assertEqual(again['status'], 'no_increase')
        self.assertEqual(again['proposal']['state'], 'saved')
        self.assertEqual(BudgetAdjustmentSchedule.objects.filter(status='scheduled').count(), 1)

    def test_next_cycle_one_off_travel_stays_a_reserve_and_keeps_its_period(self):
        self.handoff(category='TRAVEL_LODGING', planning={
            'scope': 'next_cycle', 'reserve_category': 'TRAVEL_LODGING', 'one_off': True,
        })
        review = self.review()
        self.assertEqual(review['requestedAmount'], 500000)
        self.assertEqual(review['remainingAmount'], 500000)
        proposal = self.preview(review=review)['proposal']
        self.assertEqual(proposal['allocations']['TRAVEL_LODGING'], 500000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 750000)
        self.assertTrue(proposal['oneOff'])
        response = self.approve(proposal)
        self.assertEqual(response.status_code, 200, response.content)
        schedule = BudgetAdjustmentSchedule.objects.get(proposal_id=proposal['proposalId'])
        self.assertTrue(schedule.settings['one_off'])
        self.assertEqual(schedule.settings['reserve_amount'], 500000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')
        self.assertFalse(self.base.items.filter(category_id='TRAVEL_LODGING').exists())
