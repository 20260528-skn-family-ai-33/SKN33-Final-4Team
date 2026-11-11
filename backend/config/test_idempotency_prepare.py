"""Preparation runs without the response transaction; financial writes and replay records stay atomic."""
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from chat.models import Conversation
from users.models import IdempotencyRecord

from .idempotency import idempotent


class PreparedIdempotencyTest(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="prepared-request@example.test")

    def request(self, key="prepared-request-key", body='{"value":1}'):
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
        request = RequestFactory().post("/prepared-test/", data=body, content_type="application/json", **headers)
        request.user = self.user
        return request

    def test_prepare_is_outside_atomic_and_replay_skips_preparation_and_write(self):
        states = []
        prepare = Mock(side_effect=lambda request: states.append(connection.in_atomic_block))

        @idempotent(prepare=prepare)
        def save(request):
            self.assertTrue(connection.in_atomic_block)
            talk = Conversation.objects.create(user=request.user)
            return Response({"conversation_id": str(talk.pk)})

        first = save(self.request())
        repeated = save(self.request())
        self.assertEqual(states, [False])
        self.assertEqual(first.data, repeated.data)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        conflict = save(self.request(body='{"value":2}'))
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(prepare.call_count, 1)

    def test_failed_record_write_rolls_back_business_write(self):
        prepare = Mock()

        @idempotent(prepare=prepare)
        def save(request):
            Conversation.objects.create(user=request.user)
            return Response({"saved": True})

        with patch.object(IdempotencyRecord.objects, "create", side_effect=RuntimeError("test rollback")):
            with self.assertRaisesRegex(RuntimeError, "test rollback"):
                save(self.request())
        prepare.assert_called_once()
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_rejected_write_rolls_back_and_does_not_record_response(self):
        @idempotent(prepare=lambda request: self.assertFalse(connection.in_atomic_block))
        def save(request):
            Conversation.objects.create(user=request.user)
            return Response({"error": {"code": "not_ready"}}, status=409)

        self.assertEqual(save(self.request()).status_code, 409)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_invalid_key_and_preparation_error_cannot_write(self):
        prepare = Mock(side_effect=ValidationError("test input"))
        view = Mock(return_value=Response({}))
        save = idempotent(prepare=prepare)(view)
        self.assertEqual(save(self.request(key="short")).status_code, 400)
        prepare.assert_not_called()
        with self.assertRaises(ValidationError):
            save(self.request())
        view.assert_not_called()
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_headerless_call_keeps_existing_direct_view_behavior(self):
        prepare = Mock()

        @idempotent(prepare=prepare)
        def save(request):
            self.assertFalse(connection.in_atomic_block)
            return Response({"saved": True})

        self.assertEqual(save(self.request(key=None)).status_code, 200)
        prepare.assert_not_called()
        self.assertFalse(IdempotencyRecord.objects.exists())
