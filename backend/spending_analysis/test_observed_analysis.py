"""Incomplete synchronization keeps observed analysis without inventing missing spending."""
from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from chat.router_core import MemoryStateStore
from . import agent, answer, tools
from .periods import Period
from .test_insight import decision, transaction


class ObservedAnalysisTest(SimpleTestCase):
    current = Period(date(2026, 11, 1), date(2026, 11, 18))
    previous = Period(date(2026, 10, 1), date(2026, 10, 18))

    def test_partial_and_unsynced_queries_keep_category_analysis(self):
        for state in ('partial', 'none'):
            with self.subTest(state=state), patch.object(tools, 'coverage', return_value={'state': state}), \
                    patch.object(tools, 'rows', return_value=[transaction(18000, day=15)]):
                block = tools.spend_query(object(), self.current, overview=True)
                self.assertIsNone(block['confirmed'])
                self.assertEqual(block['coverage']['state'], state)
                item = {'type': 5}
                response = answer.compose([{'ok': True, 'item': item, 'result': {'type': 5, 'item': item, 'current': block}}])
                self.assertTrue(any('식비' in text and '18,000원' in text for text in response['lines']))
                self.assertTrue(any('15일' in text for text in response['notices']))
                self.assertTrue(response['chart']['rows'])

    def test_comparison_keeps_both_observed_periods_without_claiming_change(self):
        with patch.object(tools, 'coverage', return_value={'state': 'partial'}), \
                patch.object(tools, 'rows', side_effect=[[transaction(18000)], [transaction(12000, month=10)]]):
            result = tools.period_compare(object(), self.current, self.previous)
        item = {'type': 6}
        result.update(type=6, item=item)
        response = answer.compose([{'ok': True, 'item': item, 'result': result}])
        self.assertFalse(result['comparison']['total']['available'])
        self.assertIsNone(result['comparison']['total']['delta'])
        rows = next(block['rows'] for block in response['view'] if block['kind'] == 'rows')
        self.assertEqual([row['amount'] for row in rows], [18000, 12000])
        self.assertTrue(all('badge' not in row for row in rows))

    def test_missing_current_period_is_not_zero_and_does_not_hide_previous(self):
        with patch.object(tools, 'coverage', return_value={'state': 'none'}), \
                patch.object(tools, 'rows', side_effect=[[], [transaction(12000, month=10)]]):
            result = tools.period_compare(object(), self.current, self.previous)
        item = {'type': 6}
        result.update(type=6, item=item)
        response = answer.compose([{'ok': True, 'item': item, 'result': result}])
        rows = next(block['rows'] for block in response['view'] if block['kind'] == 'rows')
        self.assertEqual([row['amount'] for row in rows], [None, 12000])
        self.assertNotRegex(' '.join(response['lines']), r'(?<![\d,])0원')

    def test_no_saved_transactions_remains_unknown_without_sync_evidence(self):
        with patch.object(tools, 'coverage', return_value={'state': 'partial'}), \
                patch.object(tools, 'rows', return_value=[]):
            block = tools.spend_query(object(), self.current, overview=True)
        item = {'type': 5}
        response = answer.compose([{'ok': True, 'item': item, 'result': {'type': 5, 'item': item, 'current': block}}])
        self.assertEqual(block['observed_count'], 0)
        self.assertIsNone(block['confirmed'])
        self.assertIn('확인할 수 있는 내역이 없어요', ' '.join(response['lines']))
        self.assertFalse(response['chart']['rows'])
        self.assertNotRegex(' '.join(response['lines']), r'(?<![\d,])0원')

    def test_non_spending_rows_do_not_confirm_zero_spending_in_incomplete_queries(self):
        for state in ('none', 'partial'):
            for kind in ('income', 'transfer'):
                with self.subTest(coverage=state, transaction_type=kind), \
                        patch.object(tools, 'coverage', return_value={'state': state}), \
                        patch.object(tools, 'rows', return_value=[{**transaction(3000000, day=17),
                                                                  'transaction_type': kind}]):
                    block = tools.spend_query(object(), self.current, overview=True)
                self.assertEqual(block['observed_count'], 0)
                self.assertIsNone(block['observed_through'])
                self.assertIsNone(block['confirmed'])
                item = {'type': 5}
                response = answer.compose([{'ok': True, 'item': item,
                    'result': {'type': 5, 'item': item, 'current': block}}])
                self.assertIn('확인할 수 있는 내역이 없어요', ' '.join(response['lines']))
                self.assertFalse(any(view.get('kind') == 'total' and view.get('amount') == 0
                                     for view in response.get('view', [])))

    def test_observation_date_and_count_use_only_spending_in_the_selected_category(self):
        records = [transaction(18000, day=10), transaction(25000, 'HOUSEHOLD', day=15),
                   {**transaction(3000000, day=16), 'transaction_type': 'income'},
                   {**transaction(100000, day=17), 'transaction_type': 'transfer'}]
        for category, count, through in ((None, 2, date(2026, 11, 15)),
                                         ('FOOD', 1, date(2026, 11, 10)), ('EDUCATION', 0, None)):
            with self.subTest(category=category), patch.object(tools, 'coverage', return_value={'state': 'partial'}), \
                    patch.object(tools, 'rows', return_value=records):
                block = tools.spend_query(object(), self.current, category)
            self.assertEqual((block['observed_count'], block['observed_through']), (count, through))
            self.assertEqual(block['observed']['total'], 43000)
            if category:
                self.assertEqual(block['observed']['category']['amount'], 18000 if category == 'FOOD' else 0)
            self.assertIsNone(block['confirmed'])

    def test_fully_refunded_spending_is_observed_zero_and_complete_empty_period_is_confirmed_zero(self):
        for coverage, records, count, confirmed in (
                ('partial', [transaction(0, day=15)], 1, False),
                ('complete', [], 0, True)):
            with self.subTest(coverage=coverage), patch.object(tools, 'coverage', return_value={'state': coverage}), \
                    patch.object(tools, 'rows', return_value=records):
                block = tools.spend_query(object(), self.current)
            self.assertEqual(block['observed_count'], count)
            self.assertEqual(block['observed_through'], date(2026, 11, 15) if records else None)
            self.assertEqual(block['observed']['total'], 0)
            self.assertEqual(block['confirmed'] is not None, confirmed)
            item = {'type': 1}
            response = answer.compose([{'ok': True, 'item': item,
                'result': {'type': 1, 'item': item, 'current': block}}])
            self.assertTrue(any(view.get('kind') == 'total' and view.get('amount') == 0
                                for view in response.get('view', [])))

    def test_database_failure_is_not_treated_as_missing_sync(self):
        with patch.object(tools, 'coverage', return_value={'state': 'error'}), \
                patch.object(tools, 'rows') as rows, self.assertRaises(tools.DbError):
            tools.spend_query(object(), self.current, overview=True)
        rows.assert_not_called()

    def test_additional_analysis_failure_does_not_replace_observed_answer(self):
        base = {'lines': ['확인된 식비는 18,000원이에요.'], 'notices': [],
                'view': [{'kind': 'total', 'amount': 18000, 'note': '일부 내역 기준'}]}
        with patch.object(agent.dialog, 'step', return_value=decision()), \
                patch.object(agent, '_outcome', return_value={'status': 'replied', 'reply': deepcopy(base)}), \
                patch.object(agent.insight, 'answer', return_value=(None, '추가 분석 자료를 불러오지 못했어요.')):
            response = agent.handle({'conversation_id': 'observed', 'text': '소비 분석해줘', 'mode': 'start'},
                                    store=MemoryStateStore(), complete=Mock(), user=object(), insight_complete=Mock())
        self.assertEqual(response['reply']['lines'], base['lines'])
        self.assertEqual(response['reply']['view'], base['view'])
        self.assertIn('추가 분석 자료를 불러오지 못했어요.', response['reply']['notices'])
