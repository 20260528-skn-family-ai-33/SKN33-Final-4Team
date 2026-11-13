from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch
import json
import unittest

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from . import test_adjustment_service as fixtures
from .adjustment_schedule import activate_due_settings, planning_context
from .adjustment_core import financial_conversation, interpretation
from .adjustment_core.funding import simulate_funding
from .models import Budget, BudgetAdjustmentSchedule, BudgetCategorySetting, BudgetCycle
from .test_adjustment_core import context, envelope, item, reading, Store, allocation


class FuturePlanningTest(unittest.TestCase):
    def setUp(self):
        self.base = context()
        self.base.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')

    def test_salary_decrease_and_requested_savings_cut_are_calculated_together(self):
        store, propose = Store(), Mock(return_value={'proposal_id': 'plan', 'task_ref': 'task'})
        parsed = reading(requests=[item('SAVINGS', 100000, 'down')], scope='next_cycle',
                         income_change={'amount': 500000, 'mode': 'by', 'direction': 'down'})
        result = financial_conversation.handle({'conversation_id': 'talk', 'text': '다음 달 월급 50만원 감소, 저축 10만원 감액'},
            store=store, complete=Mock(return_value=json.dumps(parsed)), get_context=lambda: deepcopy(self.base),
            simulate=simulate_funding, propose=propose, task_status=Mock(), cancel=Mock(), allocator=allocation)
        self.assertIn('proposal', result['reply'], result)
        calculated = propose.call_args.args[1]
        self.assertEqual(calculated['buckets']['SAVINGS'], 500000)
        self.assertEqual(calculated['buckets']['SPARE'], 0)
        self.assertEqual(propose.call_args.args[0]['monthly_income'], 2500000)

    def test_salary_shortfall_still_blocks_insufficient_or_protected_cuts(self):
        for cut, floor in ((50000, 300000), (100000, 550000)):
            with self.subTest(cut=cut, floor=floor):
                base = deepcopy(self.base)
                base['bucket_floors']['SAVINGS'] = floor
                planned = planning_context(base, {'scope': 'next_cycle',
                    'income_change': {'amount': 500000, 'mode': 'by', 'direction': 'down'}})
                result = simulate_funding(planned, envelope(item('SAVINGS', cut, 'down'), destinations=['SPARE']))
                self.assertEqual(result['status'], 'infeasible', result)

    def test_projection_uses_next_salary_not_current_spending_or_carryover(self):
        before = deepcopy(self.base)
        planned = planning_context(self.base, {'scope': 'next_cycle',
            'income_change': {'amount': 500000, 'mode': 'by', 'direction': 'up'}})
        self.assertEqual(planned['funding_amount'], 3500000)
        self.assertEqual(planned['buckets']['SPARE'], 900000)
        self.assertEqual(planned['spent'], {})
        self.assertEqual(self.base, before)
        result = simulate_funding(planned, {**envelope(item('EDUCATION', 500000), sources=['SPARE']), 'spare_minimum': 400000})
        self.assertEqual(result['allocations']['EDUCATION'], 550000)
        self.assertEqual(result['buckets']['SPARE'], 400000)

    def test_future_education_then_salary_increase_keeps_goal_and_makes_preview(self):
        store, propose = Store(), Mock(return_value={'proposal_id': 'plan', 'task_ref': 'task'})
        answers = [reading(requests=[item('EDUCATION', 500000)], scope='next_cycle'),
                   reading(scope='next_cycle', income_change={'amount': 500000, 'mode': 'by', 'direction': 'up'},
                           sources=['SPARE'], reference='previous')]
        complete = Mock(side_effect=[json.dumps(answer) for answer in answers])
        ports = dict(store=store, complete=complete, get_context=lambda: deepcopy(self.base),
                     simulate=simulate_funding, propose=propose, task_status=Mock(), cancel=Mock(), allocator=allocation)
        financial_conversation.handle({'conversation_id': 'talk', 'text': '다음달 교육비 50만원 더 나감'}, **ports)
        self.assertEqual(store.state['requests'], [item('EDUCATION', 500000)])
        result = financial_conversation.handle({'conversation_id': 'talk', 'text': '다음달 월급 50만원 늘어서 그것만 추가해'}, **ports)
        self.assertIn('proposal', result['reply'], result)
        args = propose.call_args
        self.assertEqual(args.kwargs['scope'], 'next_cycle')
        self.assertEqual(args.args[0]['monthly_income'], 3500000)
        self.assertEqual(args.args[1]['allocations']['EDUCATION'], 550000)
        self.assertEqual(args.args[1]['buckets']['SPARE'], 400000)

    def test_income_input_rejects_boolean_negative_and_unbounded_values(self):
        for amount in [True, -1, 10**11]:
            with self.subTest(amount=amount), self.assertRaises(interpretation.InterpretationFailed):
                interpretation.parse(reading(income_change={'amount': amount, 'mode': 'by', 'direction': 'up'}))
            with self.subTest(set_aside=amount), self.assertRaises(interpretation.InterpretationFailed):
                interpretation.parse(reading(set_aside_amount=amount))

    def test_unknown_trip_cost_does_not_generate_a_plan_or_cut_savings(self):
        store, propose = Store(), Mock()
        result = financial_conversation.handle({'conversation_id': 'talk', 'text': '다음달 여행이 있어'},
            store=store, complete=Mock(return_value=json.dumps(reading(requests=[item('TRAVEL_LODGING')], scope='next_cycle', one_off=True, action='compare'))),
            get_context=lambda: deepcopy(self.base), simulate=simulate_funding, propose=propose,
            task_status=Mock(), cancel=Mock(), allocator=allocation)
        self.assertEqual(store.state['pending']['kind'], 'amount', result)
        self.assertNotIn('comparison', result['reply'])
        propose.assert_not_called()

    def test_trip_cannot_automatically_exhaust_unallocated_money(self):
        store, propose = Store(), Mock()
        result = financial_conversation.handle({'conversation_id': 'talk', 'text': '다음 달 여행비 40만원은 여윳돈에서. 따로 모은 돈은 없어'},
            store=store, complete=Mock(return_value=json.dumps(reading(requests=[item('TRAVEL_LODGING', 400000)],
                scope='next_cycle', one_off=True, set_aside_amount=0, sources=['SPARE']))),
            get_context=lambda: deepcopy(self.base), simulate=simulate_funding, propose=propose,
            task_status=Mock(), cancel=Mock(), allocator=allocation)
        self.assertEqual(store.state['pending']['field'], 'spare_minimum', result)
        propose.assert_not_called()

    def test_one_off_trip_asks_prepared_money_and_only_funds_the_gap(self):
        store, propose = Store(), Mock(return_value={'proposal_id': 'plan', 'task_ref': 'task'})
        answers = [reading(requests=[item('TRAVEL_LODGING', 1000000)], scope='next_cycle', one_off=True),
                   reading(set_aside_amount=720000, sources=['SPARE'], reference='previous')]
        ports = dict(store=store, complete=Mock(side_effect=[json.dumps(a) for a in answers]),
                     get_context=lambda: deepcopy(self.base), simulate=simulate_funding, propose=propose,
                     task_status=Mock(), cancel=Mock(), allocator=allocation)
        first = financial_conversation.handle({'conversation_id': 'talk', 'text': '다음달 여행에 백만원 쓸 예정'}, **ports)
        self.assertEqual(store.state['pending']['kind'], 'prepared_funds', first)
        self.assertEqual(store.state['requests'][0]['category'], 'TRAVEL_LODGING')
        store.state['spare_minimum'] = 100000
        result = financial_conversation.handle({'conversation_id': 'talk', 'text': '따로 준비한 돈 70만원 있고 나머지는 여윳돈에서'}, **ports)
        self.assertIn('proposal', result['reply'], result)
        calculated = propose.call_args.args[1]
        self.assertEqual(calculated['allocations']['TRAVEL_LODGING'], 280000)
        self.assertEqual(calculated['buckets']['SAVINGS'], self.base['buckets']['SAVINGS'])
        self.assertEqual(calculated['buckets']['CUSHION_MONTHLY'], self.base['buckets']['CUSHION_MONTHLY'])
        self.assertTrue({'SAVINGS', 'CUSHION_MONTHLY'} <= set(store.state['excluded']))

    def test_switching_to_current_cycle_drops_future_salary_from_calculation(self):
        from .adjustment_core.conversation import handle_expanded
        store = Store()
        store.state = {'requests': [item('EDUCATION', 500000)], 'scope': 'next_cycle',
                       'planning': {'scope': 'next_cycle', 'income_change': {'amount': 500000, 'mode': 'by', 'direction': 'up'}}}
        planned = planning_context(self.base, store.state['planning'])
        discussion = Mock(return_value={'status': 'awaiting_input', 'reply': {}})
        handle_expanded({'conversation_id': 'talk', 'text': '이번 달로 바꿔줘'}, store=store,
                        complete=Mock(), get_context=lambda: planned, simulate=Mock(), propose=Mock(),
                        task_status=Mock(), cancel=Mock(), initial_data=reading(scope='cycle', reference='previous'),
                        discussion_handler=discussion)
        self.assertEqual(discussion.call_args.args[3], self.base)
        self.assertNotIn('planning', discussion.call_args.args[2])


class SchedulePersistenceTest(TestCase):
    setUp = fixtures.AdjustmentServiceTest.setUp
    adapter = fixtures.AdjustmentServiceTest.adapter
    flush = fixtures.AdjustmentServiceTest.flush
    approve = fixtures.AdjustmentServiceTest.approve

    def make_schedule(self):
        self.snapshot.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        adapter = self.adapter()
        planned = planning_context(adapter.get_context(), {'scope': 'next_cycle',
            'income_change': {'amount': 500000, 'mode': 'by', 'direction': 'up'}})
        result = adapter.simulate(planned, {**envelope(item('EDUCATION', 500000), sources=['SPARE']), 'spare_minimum': 400000})
        card = adapter.propose(planned, result, scope='next_cycle')
        self.flush(adapter)
        return card

    def test_next_cycle_reservation_responses_match_the_public_contract(self):
        from rest_framework.test import APIClient
        from openapi_schema_validator import OAS30Validator, oas30_format_checker
        from config.openapi import build_contract

        contract = build_contract()
        card = self.make_schedule()
        client = APIClient()
        client.force_authenticate(self.user)
        url = f"/api/budgets/proposals/{card['proposal_id']}/"
        with patch('django.utils.timezone.localdate', return_value=fixtures.TODAY):
            responses = [(url, 'get', client.get(url))]
            responses.append((url + 'approve/', 'post', client.post(url + 'approve/', {},
                format='json', HTTP_IDEMPOTENCY_KEY='schedule-contract')))
        for path, method, response in responses:
            with self.subTest(method=method):
                self.assertEqual(response.status_code, 200, response.content)
                payload = response.json()
                self.assertEqual((payload.get('adjustment') or payload)['scope'], 'next_cycle')
                template = path.replace(card['proposal_id'], '{id}')
                schema = contract['paths'][template][method]['responses']['200']['content']['application/json']['schema']
                validator = OAS30Validator({**schema, 'components': contract['components']},
                                          format_checker=oas30_format_checker)
                self.assertEqual([error.message for error in validator.iter_errors(payload)], [])

    def test_salary_decrease_cut_survives_preview_approval_and_activation(self):
        self.snapshot.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        adapter = self.adapter()
        planned = planning_context(adapter.get_context(), {'scope': 'next_cycle',
            'income_change': {'amount': 500000, 'mode': 'by', 'direction': 'down'}})
        result = adapter.simulate(planned, envelope(item('SAVINGS', 100000, 'down'), destinations=['SPARE']))
        card = adapter.propose(planned, result, scope='next_cycle')
        self.flush(adapter)
        self.approve(card['proposal_id'])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.savings_floor, 600000)
        spare_change = next(row for row in card['changes'] if row['category'] == 'SPARE')
        self.assertEqual((spare_change['before'], spare_change['after']), (400000, 0))
        with transaction.atomic():
            activate_due_settings(self.user, self.profile, date(2026, 11, 25))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.monthly_take_home_income, 2500000)
        self.assertEqual(self.profile.savings_floor, 500000)

    def test_one_off_education_reservation_can_generate_and_approve_due_budget(self):
        from rest_framework.test import APIClient
        from finance.models import RecurringExpense
        from chat.models import AsyncTask
        from .api import protect_json
        self.profile.housing_type = 'family'
        self.profile.save()
        RecurringExpense.objects.create(user=self.user, name='고정비', amount=800000,
            category_id='HOUSING_TELECOM', status='confirmed', source_type='manual')
        self.snapshot.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        self.snapshot['allocations']['FOOD'] += self.snapshot['allocations']['ETC']
        self.snapshot['allocations']['ETC'] = 0
        adapter = self.adapter()
        planned = planning_context(adapter.get_context(), {'scope': 'next_cycle', 'one_off': True})
        result = adapter.simulate(planned, {**envelope(item('EDUCATION', 100000), sources=['SPARE']), 'spare_minimum': 100000})
        card = adapter.propose(planned, result, scope='next_cycle')
        self.flush(adapter)
        self.approve(card['proposal_id'])
        client = APIClient()
        client.force_authenticate(self.user)
        with patch('django.utils.timezone.localdate', return_value=date(2026, 11, 25)), self.settings(BUDGET_DESIGN_ENABLED=False):
            response = client.post('/api/budgets/recommend/', {}, format='json', HTTP_IDEMPOTENCY_KEY='scheduled-recommend')
            self.assertEqual(response.status_code, 202, response.content)
            task = AsyncTask.objects.get(pk=response.json()['task']['taskId'])
            self.assertEqual(task.status, 'succeeded', task.result)
            draft = Budget.objects.get(pk=task.result['budget_id'])
            self.assertEqual(draft.items.get(category_id='EDUCATION', bucket='living').allocated_amount, 150000)
            self.assertEqual(draft.funding_amount - sum(draft.items.values_list('allocated_amount', flat=True)), 300000)
            response = client.post(f'/api/budgets/{draft.pk}/approve/', {}, format='json', HTTP_IDEMPOTENCY_KEY='scheduled-approve')
            self.assertEqual(response.status_code, 200, response.content)
            draft.refresh_from_db()
            self.assertEqual(draft.status, 'approved')
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.living_budget, 1000000)
            with transaction.atomic():
                state = protect_json(self.user, self.profile, date(2026, 12, 25))
            self.assertEqual(state['saved']['living_budget'], 1000000)
            self.assertEqual(state['saved']['spare'], 400000)

    def test_approval_is_idempotent_and_changes_no_current_budget_or_income(self):
        card = self.make_schedule()
        self.assertFalse(BudgetAdjustmentSchedule.objects.exists())
        saved = self.approve(card['proposal_id'])
        self.assertEqual(saved['effective_on'], '2026-11-25')
        self.assertEqual(saved['schedule_status'], 'scheduled')
        self.profile.refresh_from_db()
        self.base.refresh_from_db()
        self.assertEqual(self.profile.monthly_take_home_income, 3000000)
        self.assertEqual(self.base.status, 'approved')
        self.assertEqual(Budget.objects.filter(status='approved').count(), 1)
        self.assertEqual(self.approve(card['proposal_id']), saved)
        self.assertEqual(BudgetAdjustmentSchedule.objects.count(), 1)

    def test_only_due_settings_activate_once_and_feed_the_next_budget(self):
        card = self.make_schedule()
        self.approve(card['proposal_id'])
        with transaction.atomic():
            activate_due_settings(self.user, self.profile, date(2026, 11, 24))
            self.assertEqual(self.profile.monthly_take_home_income, 3000000)
            activate_due_settings(self.user, self.profile, date(2026, 11, 25))
            self.assertEqual(self.profile.monthly_take_home_income, 3500000)
            self.assertEqual(self.profile.budget_category_targets['EDUCATION'], 550000)
            self.assertEqual(self.profile.living_budget, 1500000)
            activate_due_settings(self.user, self.profile, date(2026, 11, 25))
        self.assertEqual(BudgetAdjustmentSchedule.objects.get().status, 'applied')
        from .recommend import budget_input_snapshot
        next_cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 11, 25), ends_before=date(2026, 12, 25))
        snapshot = budget_input_snapshot(self.user, next_cycle, profile=self.profile, today=date(2026, 11, 25), card=False)
        self.assertEqual(snapshot['take_home_income'], 3500000)
        self.assertEqual(snapshot['category_targets']['EDUCATION'], 550000)
        self.base.refresh_from_db()
        self.assertEqual(self.base.status, 'approved')

    def test_changed_settings_are_not_silently_overwritten_at_start_date(self):
        card = self.make_schedule()
        self.approve(card['proposal_id'])
        self.profile.monthly_take_home_income = 3200000
        self.profile.save()
        with transaction.atomic():
            activate_due_settings(self.user, self.profile, date(2026, 11, 25))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.monthly_take_home_income, 3200000)
        self.assertEqual(BudgetAdjustmentSchedule.objects.get().status, 'needs_review')

    def test_changed_protection_requires_review_instead_of_scheduled_override(self):
        card = self.make_schedule()
        self.approve(card['proposal_id'])
        BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id='EDUCATION',
                                             locked=True, locked_amount=50000)
        with transaction.atomic():
            activate_due_settings(self.user, self.profile, date(2026, 11, 25))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.monthly_take_home_income, 3000000)
        self.assertEqual(BudgetAdjustmentSchedule.objects.get().status, 'needs_review')

    def test_one_off_reserve_enters_only_next_cycle_without_changing_recurring_settings(self):
        from .recommend import budget_input_snapshot
        from .api import recommended_budget_lines
        self.snapshot.update(monthly_income=3000000, next_cycle_starts_on='2026-11-25')
        adapter = self.adapter()
        ctx = planning_context(adapter.get_context(), {'scope': 'next_cycle', 'one_off': True, 'reserve_category': 'TRAVEL_LODGING',
             'expense': {'category': 'TRAVEL_LODGING', 'total': 1000000, 'set_aside': 720000, 'additional': 280000}})
        result = adapter.simulate(ctx, {**envelope(item('TRAVEL_LODGING', 280000), sources=['SPARE']), 'spare_minimum': 100000})
        card = adapter.propose(ctx, result, scope='next_cycle')
        self.flush(adapter)
        saved = self.approve(card['proposal_id'])
        self.assertTrue(saved['one_off'])
        self.assertEqual(Budget.objects.get(pk=card['proposal_id']).items.get(bucket='reserve').allocated_amount, 280000)
        from .adjustment_context import _budget_amounts
        from .adjustment_service import _lines
        planned_budget = Budget.objects.get(pk=card['proposal_id'])
        allocations, totals, spare, _ = _budget_amounts(self.user, planned_budget)
        self.assertNotIn('TRAVEL_LODGING', allocations)
        self.assertEqual(spare, 120000)
        ordinary_result = {'allocations': allocations, 'buckets': {'SAVINGS': 600000, 'CUSHION_MONTHLY': 200000}}
        self.assertEqual(next(line for line in _lines(planned_budget, ordinary_result) if line['bucket'] == 'reserve')['allocated_amount'], 280000)
        with transaction.atomic():
            activate_due_settings(self.user, self.profile, date(2026, 11, 25))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.living_budget, 1000000)
        self.assertEqual(self.profile.savings_floor, 600000)
        self.assertEqual(self.profile.budget_category_targets, {})
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 11, 25), ends_before=date(2026, 12, 25))
        snap = budget_input_snapshot(self.user, cycle, profile=self.profile, today=date(2026, 11, 25), card=False)
        self.assertEqual(snap['fixed']['planned_reserve'], 280000)
        self.assertNotIn('TRAVEL_LODGING', snap['engine_input']['category_targets'])
        snap['fixed']['card_reserved'] = 0
        lines = recommended_budget_lines(snap, {'allocations': self.snapshot['allocations']})
        self.assertEqual(next(row for row in lines if row['bucket'] == 'reserve')['category_id'], 'TRAVEL_LODGING')
        later = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 12, 25), ends_before=date(2027, 1, 25))
        self.assertEqual(budget_input_snapshot(self.user, later, profile=self.profile, today=date(2026, 12, 25), card=False)['fixed']['planned_reserve'], 0)

    def test_new_reservation_replaces_previous_without_counting_as_current_budget_changes(self):
        from .overview import budget_nag
        from .protect import first_budget_cycle
        connected_at = timezone.now()
        first = self.make_schedule()
        self.approve(first['proposal_id'])
        second = self.make_schedule()
        self.approve(second['proposal_id'], key='replacement-reservation')
        self.assertEqual(BudgetAdjustmentSchedule.objects.get(proposal_id=first['proposal_id']).status, 'replaced')
        self.assertEqual(BudgetAdjustmentSchedule.objects.filter(status='scheduled').count(), 1)
        self.assertIsNone(budget_nag(self.user, self.cycle))
        next_cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 11, 25), ends_before=date(2026, 12, 25))
        self.assertTrue(first_budget_cycle(self.user, connected_at, next_cycle))
