"""실제 PostgreSQL 트랜잭션으로 같은 요청 재전송과 다른 메시지 경합을 구분한다. LLM 호출 없음."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from chat.api import messages
from chat import engine
from chat.models import ChatMessage, Conversation
from users.models import IdempotencyRecord


@skipUnless(connection.vendor == "postgresql", "행 잠금 경합은 PostgreSQL에서 검증")
@override_settings(CHAT_ENGINE="router", CHAT_LLM="fake")
class ConcurrentRetryTest(TransactionTestCase):
    serialized_rollback = True  # 다른 트랜잭션 테스트도 쓰는 마이그레이션 초기 데이터를 복원

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="retry@example.com", password="test")
        self.talk = Conversation.objects.create(user=self.user)

    def post(self, body, key):
        # 스레드별 연결 · 요청. 응답은 실제 renderer를 거쳐 UUID/시각까지 비교한다.
        try:
            request = APIRequestFactory().post("/api/chat/messages/", json.dumps(body),
                                              content_type="application/json", HTTP_IDEMPOTENCY_KEY=key)
            force_authenticate(request, user=self.user)
            response = messages(request)
            response.render()
            return response.status_code, json.loads(response.content)
        finally:
            connections.close_all()

    def race(self, bodies, keys):
        original = engine.answer
        ready = threading.Barrier(2, timeout=15)

        def answer(*args, **kwargs):
            result = original(*args, **kwargs)
            ready.wait()  # 두 요청 모두 같은 rev를 읽고 답을 만든 뒤 저장으로 진행
            return result

        with mock.patch.object(engine, "answer", side_effect=answer), ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(self.post, body, key) for body, key in zip(bodies, keys)]
            return [job.result(timeout=30) for job in jobs]

    def body(self, **changes):
        return {"conversationId": str(self.talk.pk), "choice": "policy", **changes}

    def test_same_key_and_body_return_identical_success(self):
        body = self.body()
        responses = self.race([body, body], ["same-key-001"] * 2)
        self.assertEqual([r[0] for r in responses], [200, 200])
        self.assertEqual(*responses)
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        self.talk.refresh_from_db()
        self.assertEqual(self.talk.slots["rev"], 1)
        self.assertEqual(self.post(body, "same-key-001"), responses[0])

    def test_different_messages_keep_conversation_conflict(self):
        responses = self.race([self.body(), self.body(choice="budget")], ["request-001", "request-002"])
        self.assertEqual(sorted(r[0] for r in responses), [200, 409])
        failed = next(r for status, r in responses if status == 409)
        self.assertEqual(failed["error"]["code"], "conversation_busy")
        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(IdempotencyRecord.objects.count(), 1)

    def test_same_key_with_different_body_is_key_conflict(self):
        responses = self.race([self.body(), self.body(choice="budget")], ["same-key-002"] * 2)
        self.assertEqual(sorted(r[0] for r in responses), [200, 409])
        failed = next(r for status, r in responses if status == 409)
        self.assertEqual(failed["error"]["code"], "idempotency_conflict")
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_first_message_retry_does_not_create_another_conversation(self):
        self.talk.delete()
        body = self.body(conversationId=None)
        responses = self.race([body, body], ["first-key-001"] * 2)
        self.assertEqual([r[0] for r in responses], [200, 200])
        self.assertEqual(*responses)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_same_message_with_different_keys_is_still_a_conflict(self):
        responses = self.race([self.body()] * 2, ["request-003", "request-004"])
        self.assertEqual(sorted(r[0] for r in responses), [200, 409])
        self.assertEqual(ChatMessage.objects.count(), 2)
