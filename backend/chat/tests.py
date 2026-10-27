"""chat API 테스트. 실행: backend 폴더에서 python manage.py test chat"""
from datetime import datetime, timezone

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
