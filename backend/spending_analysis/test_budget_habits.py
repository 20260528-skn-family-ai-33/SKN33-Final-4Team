from datetime import date, datetime, timezone
from unittest import mock

from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from . import budget_habits
from budgets.models import Budget, BudgetItem
from spending_fixture import seed
from spending_fixture.test_fixture import DESIGN, fixture_db_only, user


TODAY = date(2026, 11, 16)


def version(number, effective=None):
    return {'budget_id': f'private-budget-{number}', 'status': 'superseded', 'version_no': number,
            'approved_at': datetime(2026, 11, number, tzinfo=timezone.utc),
            'validation_summary__adjustment__effective_on': effective}


def items(number, food=100000, savings=300000, **extra):
    return [(f'private-budget-{number}', 'living', 'FOOD', food),
            (f'private-budget-{number}', 'savings', None, savings),
            *[(f'private-budget-{number}', 'living', code, amount) for code, amount in extra.items()]]


class BudgetHabitsTest(SimpleTestCase):
    def setUp(self):
        self.user = object()
        self.cycle = {'cycle_id': 'private-cycle', 'starts_on': date(2026, 10, 25),
                      'ends_before': date(2026, 11, 25)}
        self.cycles = self.enterContext(mock.patch.object(budget_habits.BudgetCycle.objects, 'filter'))
        self.cycles.return_value.values.return_value.__getitem__.return_value = [self.cycle]
        self.budgets = self.enterContext(mock.patch.object(budget_habits.Budget.objects, 'filter'))
        self.versions = self.budgets.return_value.order_by.return_value.values
        self.rows = self.enterContext(mock.patch.object(budget_habits.BudgetItem.objects, 'filter'))

    def read(self, versions, rows):
        self.versions.return_value = versions
        self.rows.return_value.values_list.return_value = rows
        return budget_habits.for_user(self.user, TODAY)

    def test_initial_approval_is_not_a_change_even_if_version_number_is_high(self):
        data = self.read([version(8)], items(8))
        self.assertEqual((data['state'], data['approved_revision_count'], data['change_count']), ('ready', 0, 0))
        self.assertEqual(data['categories'], [])
        self.assertEqual(data['savings']['decreases'], 0)

    def test_repeated_increases_and_decrease_keep_gross_amounts_and_net_separate(self):
        data = self.read([version(n) for n in (1, 2, 3, 4)],
                         items(1) + items(2, 150000, 250000) + items(3, 180000, 220000)
                         + items(4, 160000, 240000))
        self.assertEqual(data['change_count'], 3)
        self.assertEqual(data['categories'], [{'code': 'FOOD', 'name': '식비', 'increases': 2, 'decreases': 1,
                                              'increase_amount': 80000, 'decrease_amount': 20000, 'net_change': 60000,
                                              'topups': 2}])
        self.assertEqual(data['savings'], {'increases': 1, 'decreases': 2, 'increase_amount': 20000,
                                          'decrease_amount': 80000, 'net_change': -60000})
        self.assertNotIn('private-', str(data))

    def test_moving_budget_between_living_items_is_not_a_top_up(self):
        # 식비 +3만 · 문화·여가 -3만 (생활비 합계 그대로) 두 번 → 늘림은 2번이지만 추가 증액(topups)은 0번
        data = self.read([version(n) for n in (1, 2, 3)],
                         items(1, CULTURE_LEISURE=90000) + items(2, 130000, CULTURE_LEISURE=60000)
                         + items(3, 160000, CULTURE_LEISURE=30000))
        food = next(row for row in data['categories'] if row['code'] == 'FOOD')
        self.assertEqual((food['increases'], food['topups']), (2, 0))
        self.assertEqual(data['period'], {'from': '2026-10-25', 'to': '2026-11-25', 'as_of': '2026-11-16'})

    def test_future_schedule_is_not_a_current_change_and_proposals_are_not_queried(self):
        data = self.read([version(1), version(2, '2026-11-25'), version(3)], items(1) + items(3, 120000))
        self.assertEqual(data['approved_revision_count'], 1)
        self.assertEqual(data['categories'][0]['increase_amount'], 20000)
        self.cycles.assert_called_once_with(user=self.user, status='active', starts_on__lte=TODAY,
                                           ends_before__gt=TODAY)
        self.budgets.assert_called_once_with(user=self.user, cycle_id='private-cycle',
                                            status__in=['approved', 'superseded'], approved_at__isnull=False,
                                            approved_at__date__lte=TODAY)
        self.rows.assert_called_once_with(user=self.user, budget_id__in=['private-budget-1', 'private-budget-3'])

    def test_unchanged_approval_does_not_count_as_an_allocation_change(self):
        data = self.read([version(1), version(2)], items(1) + items(2))
        self.assertEqual((data['approved_revision_count'], data['change_count']), (1, 0))

    def test_multiple_saving_lines_count_as_one_change_and_category_details_are_summed(self):
        rows = items(1) + items(2, 120000, 200000)
        rows += [('private-budget-2', 'savings', 'EDUCATION', 150000),
                 ('private-budget-2', 'living', 'FOOD', 10000),
                 ('private-budget-2', 'living', 'UNCLASSIFIED', 90000)]
        data = self.read([version(1), version(2)], rows)
        self.assertEqual(data['savings']['increase_amount'], 50000)
        self.assertEqual(data['savings']['increases'], 1)
        self.assertEqual(data['categories'][0]['increase_amount'], 30000)
        self.assertEqual(len(data['categories']), 1)

    def test_missing_history_is_unavailable_not_zero_changes(self):
        self.assertEqual(self.read([], []), {'state': 'unavailable', 'reason': 'no_approved_history'})
        self.assertEqual(self.read([version(1), version(2)], items(2)),
                         {'state': 'unavailable', 'reason': 'missing_allocations'})

    def test_ambiguous_cycle_and_database_failure_do_not_claim_stable_habits(self):
        for cycles, reason in (([], 'no_active_cycle'), ([self.cycle, self.cycle], 'overlapping_cycles')):
            self.cycles.return_value.values.return_value.__getitem__.return_value = cycles
            self.assertEqual(budget_habits.for_user(self.user, TODAY), {'state': 'unavailable', 'reason': reason})
        self.cycles.side_effect = DatabaseError('private connection details')
        self.assertEqual(budget_habits.for_user(self.user, TODAY), {'state': 'unavailable', 'reason': 'db_error'})


@fixture_db_only
class BudgetHabitsDatabaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_only_applied_approved_changes_are_counted_and_other_users_stay_out(self):
        owner, other, today = user('A'), user('B'), seed.AS_OF.date()
        initial = Budget.objects.get(user=owner, cycle__status='active', status='approved')
        initial.approved_at = seed.AS_OF.replace(day=25, hour=9)
        initial.save(update_fields=['approved_at'])
        baseline = budget_habits.for_user(owner, today)
        self.assertEqual((baseline['state'], baseline['change_count']), ('ready', 0))
        self.assertTrue(Budget.objects.filter(user=owner, cycle=initial.cycle, status='validated').exists())
        initial.status = 'superseded'
        initial.save(update_fields=['status'])
        original_items = list(initial.items.values('bucket', 'category_id', 'category_detail', 'reserve_id', 'allocated_amount'))

        def revised(number, *, status='superseded', day=27, increase=50000, effective_on=None):
            version = Budget.objects.create(user=owner, cycle=initial.cycle, base_budget=initial,
                                            version_no=number, status=status, funding_amount=initial.funding_amount,
                                            approved_at=seed.AS_OF.replace(day=day, hour=9),
                                            validation_summary={'adjustment': {'effective_on': effective_on}})
            for row in original_items:
                line = dict(row)
                if line['bucket'] == 'living' and line['category_id'] == 'FOOD':
                    line['allocated_amount'] += increase
                elif line['bucket'] == 'savings':
                    line['allocated_amount'] -= increase
                BudgetItem.objects.create(user=owner, budget=version, **line)
            return version

        first = revised(3, day=26)
        latest = revised(4, status='approved', day=27, increase=100000)
        latest.base_budget = first
        latest.save(update_fields=['base_budget'])
        revised(5, day=28, increase=400000, effective_on='2026-11-25')
        revised(6, status='expired', day=28, increase=200000)
        revised(7, day=30, increase=300000)  # 기준일 이후 승인도 섞지 않는다.
        # 실제 저장 자료가 잘못 연결돼도 다른 회원의 항목과 예산은 합치지 않는다.
        BudgetItem.objects.create(user=other, budget=latest, bucket='living', category_id='FOOD',
                                  category_detail='stray-owner', allocated_amount=9999000)
        unrelated = Budget.objects.create(user=other, cycle=initial.cycle, version_no=8, status='superseded',
                                          approved_at=seed.AS_OF.replace(day=28))
        BudgetItem.objects.create(user=other, budget=unrelated, bucket='living', category_id='FOOD',
                                  allocated_amount=9999000)

        with CaptureQueriesContext(connection) as queries:
            result = budget_habits.for_user(owner, today)
        self.assertEqual(result['state'], 'ready')
        self.assertEqual((result['approved_revision_count'], result['change_count']), (2, 2))
        self.assertEqual(result['categories'], [{'code': 'FOOD', 'name': '식비', 'increases': 2, 'decreases': 0,
                                                'increase_amount': 100000, 'decrease_amount': 0, 'net_change': 100000,
                                            'topups': 2}])
        self.assertEqual(result['savings'], {'increases': 0, 'decreases': 2, 'increase_amount': 0,
                                            'decrease_amount': 100000, 'net_change': -100000})
        self.assertEqual(result['period'], {'from': '2026-10-25', 'to': '2026-11-25', 'as_of': '2026-10-29'})
        self.assertTrue(queries)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
