"""Actual HTTP preparation is outside idempotency atomic, including headerless requests and CAS conflicts."""
from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from users.models import IdempotencyRecord

from . import engine
from .models import ChatMessage, Conversation


@override_settings(CHAT_ENGINE="router", CHAT_LLM="fake")
class ChatPreparationTest(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="chat-preparation@example.test")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def post(self, *, cid=None, key="chat-preparation-key", choice="policy"):
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
        return self.client.post("/api/chat/messages/", {"conversationId": cid, "choice": choice}, format="json", **headers)

    def test_http_replay_does_not_repeat_preparation_and_headerless_prepare_is_also_outside_atomic(self):
        original = engine.answer
        states = []

        def observed(*args, **kwargs):
            states.append(connection.in_atomic_block)
            return original(*args, **kwargs)

        with patch.object(engine, "answer", side_effect=observed) as answer:
            first = self.post()
            repeat = self.post()
            headerless = self.post(key=None)
        self.assertEqual((first.status_code, repeat.status_code, headerless.status_code), (200, 200, 200))
        self.assertEqual(first.json(), repeat.json())
        self.assertEqual(states, [False, False])
        self.assertEqual(answer.call_count, 2)
        self.assertEqual(Conversation.objects.count(), 2)
        self.assertEqual(ChatMessage.objects.count(), 4)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_concurrent_revision_survives_rejected_save_without_messages_or_record(self):
        talk = Conversation.objects.create(user=self.user, slots={"schema": engine.SCHEMA, "rev": 1, "state": {}})
        original = engine.answer

        def another_turn_finishes(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            result = original(*args, **kwargs)
            changed = deepcopy(talk.slots)
            changed["rev"] = 2
            Conversation.objects.filter(pk=talk.pk).update(slots=changed)
            return result

        with patch.object(engine, "answer", side_effect=another_turn_finishes):
            response = self.post(cid=str(talk.pk))
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "conversation_busy"))
        talk.refresh_from_db()
        self.assertEqual(talk.slots["rev"], 2)
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_stale_choice_returns_before_preparing_an_answer(self):
        talk = Conversation.objects.create(user=self.user)
        with patch.object(engine, "answer", side_effect=AssertionError("stale choice must not call an agent")):
            response = self.post(cid=str(talk.pk), choice={"value": "old", "turn": 1})
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "stale_choice"))
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_get_and_legacy_post_do_not_prepare_router_turns(self):
        with patch.object(engine, "prepare", side_effect=AssertionError("router preparation is not used")):
            self.assertEqual(self.client.get("/api/chat/messages/").status_code, 200)
            with self.settings(CHAT_ENGINE="legacy"):
                response = self.post(choice="budget")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ChatMessage.objects.count(), 2)
