"""Salary review retains confirmed plans and restores only its current-cycle proposal."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from chat.models import AsyncTask

from .models import Budget, BudgetCycle
from . import test_salary_redistribution as salary_fixtures


@override_settings(CHAT_TODAY='2026-11-05', BUDGET_DESIGN_EXTERNAL_ENABLED=False)
class SalaryReviewRegressionTest(TestCase):
    setUp = salary_fixtures.SalaryRedistributionTest.setUp
    handoff = salary_fixtures.SalaryRedistributionTest.handoff
    review = salary_fixtures.SalaryRedistributionTest.review
    post = salary_fixtures.SalaryRedistributionTest.post
    preview = salary_fixtures.SalaryRedistributionTest.preview
    approve = salary_fixtures.SalaryRedistributionTest.approve
    assert_conserved = salary_fixtures.SalaryRedistributionTest.assert_conserved

    def planned(self, amount=800000):
        return {'planned_spare': {'status': 'user_confirmed', 'scope': 'unbudgeted_only', 'amount': amount}}

    def test_confirmed_plans_survive_salary_change_preview_and_approval(self):
        state = self.handoff(state={'financial_overrides': self.planned()})
        review = self.review()
        self.assertEqual(review['remainingAmount'], 100000)
        shown = self.preview(review=review)
        proposal = shown['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['buckets']['SAVINGS'], 630000)
        self.assertGreaterEqual(proposal['buckets']['SPARE'], 800000)
        self.assertTrue(proposal['canApprove'])
        self.assert_conserved(proposal)
        draft = Budget.objects.get(pk=proposal['proposalId'])
        context = draft.input_snapshot['adjustment_context']
        self.assertEqual(context['diagnostic_inputs']['planned_spare'], self.planned()['planned_spare'])
        self.assertEqual(self.approve(proposal).status_code, 200)
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'approved')
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.slots['state']['agent:budget']['financial_overrides'], state['financial_overrides'])

    def test_planned_spending_is_reserved_from_actual_available_spare(self):
        self.snapshot['bucket_available']['SPARE'] = 250000
        self.handoff(amount=400000, state={'financial_overrides': self.planned()})
        shown = self.preview()
        proposal = shown['proposal']
        moved = 1400000 - proposal['buckets']['SPARE']
        self.assertLessEqual(moved, 450000)
        self.assertGreaterEqual(1250000 - moved, 800000)
        self.assertEqual(self.approve(proposal).status_code, 200)

    def test_request_shortfall_preserves_planned_expense_and_original_request(self):
        state = self.handoff(amount=700000, state={'financial_overrides': self.planned()})
        review = self.review()
        self.assertEqual(review['status'], 'needs_input')
        self.assertEqual(review['requestedAmount'], 700000)
        self.assertEqual(review['shortfallAmount'], 100000)
        self.assertEqual(Budget.objects.count(), 1)
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.slots['state']['agent:budget'], state)

    def test_new_planned_amount_expires_prior_preview_before_approval(self):
        self.handoff(state={'financial_overrides': self.planned()})
        proposal = self.preview()['proposal']
        self.talk.refresh_from_db()
        self.talk.slots['state']['agent:budget']['financial_overrides'] = self.planned(1000000)
        self.talk.save(update_fields=['slots'])
        self.assertEqual(self.approve(proposal).status_code, 409)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_confirmed_plan_from_a_different_period_requires_confirmation(self):
        self.handoff(state={'financial_overrides': self.planned()}, planning={'scope': 'next_cycle'})
        result = self.review()
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['reason'], 'planned_expense_period_changed')
        self.assertEqual(Budget.objects.count(), 1)

    def test_prior_cycle_high_version_does_not_hide_current_mypage_preview(self):
        proposal = self.preview(conversation=False)['proposal']
        current = Budget.objects.get(pk=proposal['proposalId'])
        previous_cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25),
            ends_before=date(2026, 10, 25), status='closed')
        proof = deepcopy(current.input_snapshot)
        proof['adjustment_context']['salary_review']['income_after'] = 3000000
        previous_task = AsyncTask.objects.create(user=self.user, task_type='budget_adjust', status='succeeded',
                                                request_key='previous-salary-review', input_hash=current.task.input_hash)
        Budget.objects.create(user=self.user, cycle=previous_cycle, version_no=10, status='approved',
                              task=previous_task, funding_amount=3000000, input_snapshot=proof)
        restored = self.review(conversation=False)
        self.assertEqual(restored['proposal']['proposalId'], proposal['proposalId'])

    def test_stale_higher_version_does_not_hide_valid_current_preview(self):
        proposal = self.preview(conversation=False)['proposal']
        current = Budget.objects.get(pk=proposal['proposalId'])
        stale = deepcopy(current.input_snapshot)
        stale['adjustment_context']['salary_review']['income_after'] = 3000000
        stale_task = AsyncTask.objects.create(user=self.user, task_type='budget_adjust', status='succeeded',
                                             request_key='stale-salary-review', input_hash=current.task.input_hash)
        Budget.objects.create(user=self.user, cycle=self.cycle, version_no=10, status='validated',
                              task=stale_task, base_budget=self.base, funding_amount=3000000, input_snapshot=stale)
        restored = self.review(conversation=False)
        self.assertEqual(restored['proposal']['proposalId'], proposal['proposalId'])

    def test_request_only_keeps_savings_and_cushion_and_skips_optional_allocation(self):
        from .salary_redistribution import preview
        self.handoff()
        review = self.review()
        with patch('budgets.salary_design.review_remaining', side_effect=AssertionError('No optional allocation')):
            result = preview(self.user, {'basis_hash': review['basisHash'], 'saving_percent': 0, 'request_only': True},
                             conversation=self.talk)
        proposal = result['proposal']
        self.assertEqual(proposal['allocations']['EDUCATION'], 550000)
        self.assertEqual(proposal['buckets'], {'SAVINGS': 600000, 'CUSHION_MONTHLY': 200000, 'SPARE': 900000})
        self.assertTrue(result['request_only'])
        restored = self.review()
        self.assertTrue(restored['requestOnly'])
        self.assertEqual(restored['proposal']['proposalId'], proposal['proposal_id'])
