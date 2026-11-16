import json
from datetime import date
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from .funds_overview import funds_overview
from .test_adjustment_core import context
from spending_analysis.reading import parse
from spending_analysis import agent, dialog
from chat.router_core import MemoryStateStore


class FundsOverviewTest(SimpleTestCase):
    def test_past_funds_query_keeps_its_kind_after_current_period_confirmation(self):
        store, execute = MemoryStateStore(), Mock(return_value=None)
        complete = Mock(return_value=json.dumps({
            'mine': True, 'target': 'budget_left', 'period': 'last_month', 'funds_overview': True,
        }))
        request = {'conversation_id': 'funds', 'text': '지난달 여윳돈 얼마 남았어?',
                   'mode': 'start', 'today': '2026-11-13'}
        with patch('budgets.funds_overview.funds_overview', return_value=['미배정 여윳돈: 40,000원']) as overview:
            first = agent.handle(request, store=store, complete=complete, user=object(), execute=execute)
            self.assertEqual(first['status'], 'awaiting_input')
            self.assertIn('현재 금액을 볼까요?', first['reply']['lines'][0])
            self.assertTrue(dialog.pending(store, 'funds')['request']['funds_overview'])
            overview.assert_not_called()
            second = agent.handle({**request, 'mode': 'continue', 'text': '네'},
                                  store=store, complete=complete, user=object(), execute=execute)
            overview.assert_called_once()
            self.assertEqual(second['status'], 'replied')
            self.assertIn('미배정 여윳돈: 40,000원', second['reply']['lines'])
            self.assertEqual(overview.call_args.args[1], date(2026, 11, 13))
        execute.assert_not_called()
        complete.assert_called_once()
        self.assertIsNone(dialog.pending(store, 'funds'))

    def test_separates_living_unallocated_accumulated_cushion_and_monthly_contribution(self):
        ctx = context()
        ctx['spending_status'] = 'complete'
        ctx['bucket_available']['SPARE'] = 98176
        with patch('budgets.funds_overview.get_adjustment_context', return_value=ctx), patch('budgets.funds_overview.cushion_data', return_value={'cushion': {'now': 900000, 'monthly': 200000}}):
            lines = funds_overview(object())
        self.assertIn('남은 생활비: 870,000원', lines[0])
        self.assertIn('미배정 여윳돈: 98,176원', lines[1])
        self.assertIn('쌓인 머니쿠션: 900,000원', lines[2])
        self.assertIn('적립 예산: 200,000원', lines[3])
        self.assertTrue(parse({'mine': True, 'target': 'budget_left', 'funds_overview': True}).funds_overview)
