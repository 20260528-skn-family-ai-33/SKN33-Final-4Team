"""chat API 테스트. 실행: backend 폴더에서 python manage.py test chat"""
from datetime import datetime, timezone
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import ChatMessage, Conversation


class ConversationTest(TestCase):
    """32. GET /api/chat/conversations/<id>/ (chat.md '이전 대화 보기')"""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="sejin@example.com", password="Example123!")
        cls.other = get_user_model().objects.create_user(email="other@example.com", password="Example123!")
        cls.talk = Conversation.objects.create(user=cls.user, active_agent="spending", slots={"month": "2026-10"})
        # 봇 답을 먼저 저장해도 시각 순서(오래된 것부터)로 나오는지 본다
        bot = ChatMessage.objects.create(user=cls.user, conversation=cls.talk, role="bot", content_type="card",
                                         content={"kind": "spending"})
        asked = ChatMessage.objects.create(user=cls.user, conversation=cls.talk, role="user", content_type="text",
                                           content={"text": "지난달보다 식비 얼마 썼어?", "choices": []})
        ChatMessage.objects.filter(pk=asked.pk).update(created_at=datetime(2026, 10, 21, 16, 19, 58, tzinfo=timezone.utc))
        ChatMessage.objects.filter(pk=bot.pk).update(created_at=datetime(2026, 10, 21, 16, 20, 0, tzinfo=timezone.utc))

    def url(self, talk):
        return f"/api/chat/conversations/{talk.conversation_id}/"

    def test_messages_oldest_first(self):
        self.client.force_login(self.user)
        body = self.client.get(self.url(self.talk)).json()
        self.assertEqual(body["conversationId"], str(self.talk.conversation_id))
        self.assertEqual(body["status"], "active")
        self.assertEqual([m["role"] for m in body["messages"]], ["user", "bot"])
        self.assertEqual(body["messages"][0]["content"], {"text": "지난달보다 식비 얼마 썼어?", "choices": []})
        self.assertEqual(set(body["messages"][0]), {"messageId", "role", "contentType", "content", "taskId", "createdAt"})
        self.assertNotIn("slots", body)  # 서버만 쓰는 대화 상태는 안 보냄
        self.assertNotIn("activeAgent", body)

    def test_other_users_conversation_is_404(self):
        self.client.force_login(self.other)
        res = self.client.get(self.url(self.talk))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"]["code"], "not_found")

    def test_login_required(self):
        self.assertEqual(self.client.get(self.url(self.talk)).status_code, 403)


class MessageTest(TestCase):
    """33. POST /api/chat/messages/ (chat.md '챗봇에 메시지 보내기') — 저장 · 선택지 · 라우터 실패 처리"""
    URL = "/api/chat/messages/"

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="sejin@example.com", password="Example123!")
        cls.other = get_user_model().objects.create_user(email="other@example.com", password="Example123!")

    def send(self, body, key=None):
        self.client.force_login(self.user)
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post(self.URL, data=json.dumps(body), content_type="application/json", **headers)

    def test_new_conversation_with_budget_choice(self):
        res = self.send({"conversationId": None, "choice": "budget"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["route"], {"intent": "budget", "slots": {}, "fallback": False})
        self.assertIsNone(body["task"])
        user_msg, bot_msg = body["messages"]
        self.assertEqual((user_msg["role"], user_msg["contentType"]), ("user", "text"))
        self.assertEqual(user_msg["content"], {"text": "예산 조정", "choices": []})
        self.assertEqual((bot_msg["role"], bot_msg["contentType"]), ("bot", "text"))
        self.assertIn("예산 화면", bot_msg["content"]["text"])
        # 저장된 대화를 32번으로 다시 보면 같은 두 메시지가 순서대로 나온다
        talk = Conversation.objects.get(pk=body["conversationId"])
        self.assertEqual(talk.user, self.user)
        again = self.client.get(f"/api/chat/conversations/{talk.pk}/").json()
        self.assertEqual([m["messageId"] for m in again["messages"]], [user_msg["messageId"], bot_msg["messageId"]])

    def test_text_without_router_is_fallback_unknown_and_keeps_state(self):
        talk = Conversation.objects.create(user=self.user, active_agent="spending", slots={"category": "FOOD"})
        res = self.send({"conversationId": str(talk.pk), "text": "  지난달보다 식비 얼마 썼어?  "})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["conversationId"], str(talk.pk))
        self.assertEqual(body["route"], {"intent": "unknown", "slots": {}, "fallback": True})
        self.assertEqual(body["messages"][0]["content"]["text"], "  지난달보다 식비 얼마 썼어?  ")
        bot = body["messages"][1]["content"]
        self.assertEqual(bot["text"], "지금은 이해하지 못했어요, 아래에서 골라 주세요.")
        self.assertEqual([c["value"] for c in bot["choices"]], ["spending", "budget"])
        talk.refresh_from_db()  # 라우터 실패 때 대화 상태를 바꾸지 않음
        self.assertEqual((talk.active_agent, talk.slots), ("spending", {"category": "FOOD"}))
        self.assertEqual(talk.messages.count(), 2)

    def test_spending_choice_temporary_text(self):
        body = self.send({"conversationId": None, "choice": "spending"}).json()
        self.assertEqual(body["route"]["intent"], "spending")
        self.assertEqual(body["messages"][1]["contentType"], "text")

    def test_same_request_key_saves_once(self):
        key = uuid.uuid4().hex
        first = self.send({"conversationId": None, "choice": "budget"}, key=key)
        second = self.send({"conversationId": None, "choice": "budget"}, key=key)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Conversation.objects.filter(user=self.user).count(), 1)
        self.assertEqual(ChatMessage.objects.filter(user=self.user).count(), 2)

    def test_bad_requests(self):
        archived = Conversation.objects.create(user=self.user, status="archived")
        others = Conversation.objects.create(user=self.other)
        cases = [
            ({"conversationId": None, "text": "안녕", "choice": "budget"}, "nonFieldErrors"),
            ({"conversationId": None}, "nonFieldErrors"),
            ({"text": "안녕"}, "conversationId"),
            ({"conversationId": None, "text": "안녕", "extra": 1}, "extra"),
            ({"conversationId": None, "text": "   "}, "text"),
            ({"conversationId": None, "text": "가" * 501}, "text"),
            ({"conversationId": None, "text": ["안녕"]}, "text"),
            ({"conversationId": None, "choice": "nope"}, "choice"),
            ({"conversationId": None, "choice": []}, "choice"),
            ({"conversationId": "not-a-uuid", "text": "안녕"}, "conversationId"),
            ({"conversationId": str(uuid.uuid4()), "text": "안녕"}, "conversationId"),
            ({"conversationId": str(others.pk), "text": "안녕"}, "conversationId"),
            ({"conversationId": str(archived.pk), "text": "안녕"}, "conversationId"),
        ]
        for body, field in cases:
            with self.subTest(body=body):
                res = self.send(body)
                self.assertEqual(res.status_code, 400)
                error = res.json()["error"]
                self.assertEqual(error["code"], "invalid")
                self.assertIn(field, error["fields"])
        self.assertEqual(self.send({"conversationId": None, "text": "가" * 500}).status_code, 200)
        self.assertEqual(ChatMessage.objects.filter(conversation__in=[archived, others]).count(), 0)

    def test_login_required(self):
        res = self.client.post(self.URL, data=json.dumps({"conversationId": None, "choice": "budget"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["error"]["code"], "not_authenticated")
