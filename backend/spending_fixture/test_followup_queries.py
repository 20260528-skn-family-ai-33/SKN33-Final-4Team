"""비교 다음의 새 조회, 지원 묶음의 빈 결과, 자료 부족을 실제 API에서 검증한다."""
from unittest.mock import patch
from django.test import override_settings
from chat import engine
from chat.test_engine import ServiceChatBase
from chat.llm.openai_rest import CapReached
from .test_fixture import fixture_db_only
from .test_service import SERVICE


@fixture_db_only
@SERVICE
@override_settings(CHAT_EXPLAIN_PLAN=False)
class FollowupQueryTest(ServiceChatBase):
    def after_compare(self, text):
        cid = self.send("A", text="지난달보다 식비 더 썼어?").json()["conversationId"]
        return self.bot(self.send("A", cid, text=text))

    def test_delivery_group_without_transactions_is_supported(self):
        out = self.after_compare("배달에 얼마나 썼지?")
        self.assertTrue(out["result"])
        self.assertIn("이번 달(10월 1일~29일) 배달앱에 0원 썼어요.", out["lines"])
        self.assertIn("배달의민족", out["lead"][0])
        self.assertNotIn("식비 전체", out["text"])

    def test_new_lunch_count_does_not_inherit_comparison(self):
        out = self.after_compare("점심엔 몇 번 결제했어?")
        self.assertTrue(out["result"])
        total = next(b for b in out["view"] if b["kind"] == "total")
        self.assertEqual((total["value"], total["amount"]), ("12건", 180000))
        self.assertEqual(out["view"][0]["title"], "10월 1일~29일 · 식비 · 점심")

    def test_category_only_followup_preserves_comparison(self):
        out = self.after_compare("그럼 교통비는?")
        self.assertIn("지난달 같은 기간과 비교", out["view"][0]["title"])
        rows = next(b for b in out["view"] if b["kind"] == "rows")["rows"]
        self.assertEqual([r["amount"] for r in rows], [90000, 90000])

    def test_missing_delivery_data_is_not_zero(self):
        cid = self.send("C", text="지난달 식비 얼마 썼어?").json()["conversationId"]
        out = self.bot(self.send("C", cid, text="배달에 얼마나 썼지?"))
        self.assertIn("확인할 수 있는 내역이 없어요.", " ".join(out["lines"]))
        self.assertNotIn("0원", out["text"])

    def test_limit_on_first_router_call_is_not_misunderstanding(self):
        class Limited:
            explains = False
            def __call__(self, *args, **kwargs):
                raise CapReached()
        with patch.object(engine, "reading", return_value=Limited()):
            response = self.send("A", text="지난달 비교해줘")
        out = self.bot(response)
        self.assertIn("호출 상한", out["text"])
        self.assertNotIn("이해하지 못했어요", out["text"])
        self.assertEqual(out["choices"], [])
        self.assertTrue(response.json()["route"]["fallback"])
