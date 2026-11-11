"""Legacy reference inference runs between short transactions, without financial row locks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase

from .adjustment_context import _references
from .api import lock_user
from .models import Budget, BudgetCycle


class ReferenceLockingTest(TransactionTestCase):
    serialized_rollback = True

    def test_inference_releases_budget_row_and_concurrent_reader_does_not_repeat_it(self):
        user = get_user_model().objects.create_user(email="reference-lock@example.test")
        cycle = BudgetCycle.objects.create(user=user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        initial = Budget.objects.create(user=user, cycle=cycle, status="approved", version_no=1,
                                       input_snapshot={"engine_input": {"target": 950000}})
        forbidden = Mock(side_effect=AssertionError("reference inference must run only once"))

        def concurrent_reader():
            close_old_connections()
            try:
                with transaction.atomic():
                    lock_user(user)
                    Budget.objects.select_for_update(nowait=True).get(pk=initial.pk)
                return _references(Budget.objects.get(pk=initial.pk), {"FOOD": 400000}, forbidden, {})
            finally:
                connections["default"].close()

        def provider(inp):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(inp, {"target": 950000})
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(concurrent_reader).result(timeout=10)
            self.assertEqual(pending["ml_reference_status"], "unavailable")
            return {"ml_reference": {"FOOD": 380000}, "ml_reference_meta": {
                "source": "model", "model_id": "fixture", "model_checksum": "a" * 64, "target": 950000}}

        result = _references(initial, {"FOOD": 400000}, provider, {})
        self.assertEqual(result["ml_reference_status"], "ready")
        initial.refresh_from_db()
        self.assertTrue(initial.input_snapshot["ml_reference_backfill_attempted"])
        self.assertEqual(initial.input_snapshot["ml_reference"], {"FOOD": 380000})
        repeated = _references(initial, {"FOOD": 400000}, forbidden, {})
        self.assertEqual(repeated["ml_reference"], result["ml_reference"])
        forbidden.assert_not_called()
