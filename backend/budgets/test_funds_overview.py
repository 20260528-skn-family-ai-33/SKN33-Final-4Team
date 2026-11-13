from unittest.mock import patch
from django.test import SimpleTestCase
from .funds_overview import funds_overview
from .test_adjustment_core import context
from spending_analysis.reading import parse


class FundsOverviewTest(SimpleTestCase):
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
