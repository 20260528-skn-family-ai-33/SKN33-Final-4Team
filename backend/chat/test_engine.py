"""새 챗봇 엔진(chat/engine.py, CHAT_ENGINE = "router") — 가짜 해석 · fixture DB 실제 분석 · Conversation.slots 저장. 네트워크 호출 없음
더미 설정 값(spending_fixture.service_settings)으로 켜서 chat.api.messages 경로로 시험한다 (MEMO 25절 1단계, 이전 spending_fixture/test_engine.py).
팀 기본 설정(엔진 legacy)의 동작은 chat/tests.py 가 그대로 확인한다.
실행: python manage.py test chat.test_engine --settings=spending_fixture.settings
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings

from chat.models import ChatMessage, Conversation

from spending_analysis import explain
from spending_fixture import seed
from spending_fixture.test_fixture import DESIGN, EXPECTED, fixture_db_only
from spending_fixture.test_service import NO_NETWORK, PASSWORD, SERVICE, login

from policies import agent as policy_agent

from . import engine

URL = "/api/chat/messages/"


class ServiceChatBase(TestCase):
    """로그인한 회원별 클라이언트 · 메시지 보내기 도우미 (테스트 없음)"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)
        from spending_fixture import set_login
        set_login.set_passwords(PASSWORD, DESIGN)

    def setUp(self):
        patcher = NO_NETWORK
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clients = {}

    def client_for(self, persona):
        if persona not in self.clients:
            client = Client(enforce_csrf_checks=True)
            login(client, DESIGN["personas"][persona]["synthetic_email"])
            self.clients[persona] = client
        return self.clients[persona]

    def send(self, persona, conversation_id=None, key=None, **body):
        client = self.client_for(persona)
        extra = {"HTTP_X_CSRFTOKEN": client.cookies["mc_fixture_csrftoken"].value}
        if key:
            extra["HTTP_IDEMPOTENCY_KEY"] = key
        return client.post(URL, json.dumps({"conversationId": conversation_id, **body}), content_type="application/json",
                           **extra)

    def bot(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["messages"][1]["content"]



@fixture_db_only
@SERVICE
class ServiceChatTest(ServiceChatBase):
    def test_fake_engine_first_screen_and_message_without_live_imports(self):
        import builtins
        import importlib
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name in ("fcntl", "msvcrt", "llm.live", "chat.llm.live", "ledger", "chat.llm.ledger"):
                raise AssertionError(f"fake 경로의 불필요한 import: {name}")
            return original(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded):
            importlib.reload(engine)
            self.assertEqual(self.client_for("A").get(URL).status_code, 200)
            self.assertEqual(self.send("A", text="이번 달 얼마 썼어?").status_code, 200)

    def test_first_question_follow_up_and_reload(self):
        total = EXPECTED["personas"]["A"]["current_month_known"]["total"]
        out = self.send("A", text="이번 달 얼마 썼어?")
        cid = out.json()["conversationId"]
        content = self.bot(out)
        self.assertEqual(content["lines"][0], f"이번 달(10월 1일~29일) 소비는 {total:,}원이에요.")
        self.assertTrue(content["text"].startswith(content["lines"][0]))  # 기존 text 칸도 채운다
        self.assertEqual(out.json()["route"], {"intent": "spending", "slots": {}, "fallback": False})
        follow = self.bot(self.send("A", cid, text="그럼 교통비는?"))  # 직전 조건(이번 달 · 쓴 돈)을 이어받는다
        self.assertEqual(follow["lines"][0], "이번 달(10월 1일~29일) 교통·차량에 90,000원 썼어요.")
        history = self.client_for("A").get(f"/api/chat/conversations/{cid}/").json()  # 새로고침 = 기존 대화 조회 API
        self.assertEqual([m["role"] for m in history["messages"]], ["user", "bot", "user", "bot"])
        self.assertEqual(history["messages"][3]["content"]["lines"], follow["lines"])
        talk = Conversation.objects.get(pk=cid)
        self.assertEqual((talk.slots["schema"], talk.slots["rev"], talk.active_agent), ("router_core/1", 2, "spending"))

    def test_engine_and_feature_buttons_come_from_server(self):
        """MEMO 25-9 ①: 엔진 종류 · 기능 버튼 목록을 첫 화면(GET) · 메시지 응답 · 대화 조회에 같은 값으로. 꺼짐(legacy)은 기존 2개"""
        router = {"engine": "router", "features": [{"value": "spending", "label": "소비 분석"},
                                                    {"value": "budget", "label": "예산 조정"}, {"value": "policy", "label": "청년 정책"}],
                  "testMode": True, "today": "2026-10-29"}  # 고정 기준일 → 화면이 '테스트 환경 · 기준일' 띠
        first = self.client_for("A").get(URL)
        self.assertEqual((first.status_code, first.json()), (200, router))
        out = self.send("A", text="이번 달 얼마 썼어?").json()
        self.assertEqual({k: out[k] for k in router}, router)
        history = self.client_for("A").get(f"/api/chat/conversations/{out['conversationId']}/").json()
        self.assertEqual({k: history[k] for k in router}, router)
        self.assertEqual(Conversation.objects.filter(user__email=DESIGN["personas"]["A"]["synthetic_email"]).count(), 1)  # GET 은 저장 없음
        with override_settings(CHAT_ENGINE="legacy"):
            self.assertEqual(self.client_for("A").get(URL).json(),
                             {"engine": "legacy", "features": [{"value": "spending", "label": "소비 분석"},
                                                               {"value": "budget", "label": "예산 조정"}],
                              "testMode": False, "today": None})
        with override_settings(CHAT_TODAY=None):  # 켜짐이어도 기준일이 오늘이면 띠 없음
            self.assertEqual({k: self.client_for("A").get(URL).json()[k] for k in ("testMode", "today")}, {"testMode": False, "today": None})

    def test_state_lives_only_in_the_database(self):
        """서버 재시작 = 메모리에 남은 것 없이 DB 의 slots 만으로 이어받는다 (요청마다 저장소를 slots 에서 새로 만든다)"""
        cid = self.send("A", text="식비 얼마 썼어?").json()["conversationId"]
        slots = Conversation.objects.get(pk=cid).slots
        self.assertEqual(json.loads(json.dumps(slots)), slots)  # JSON 그대로 저장 가능
        restored = engine.ConversationStore.load(slots)
        self.assertEqual(restored.get_analysis(engine.KEY)["request"]["categories"], ["FOOD"])
        with mock.patch.object(engine, "FakeReading", wraps=engine.FakeReading) as fresh:
            follow = self.bot(self.send("A", cid, text="그럼 교통비는?"))
        self.assertEqual(fresh.call_count, 1)  # 해석기도 요청마다 새로 — 이전 요청의 메모리를 쓰지 않는다
        self.assertIn("교통·차량", follow["lines"][0])

    def test_other_members_cannot_use_the_conversation(self):
        cid = self.send("A", text="이번 달 얼마 썼어?").json()["conversationId"]
        response = self.send("B", cid, text="그럼 교통비는?")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid"))
        self.assertEqual(self.client_for("B").get(f"/api/chat/conversations/{cid}/").status_code, 404)
        self.assertEqual(ChatMessage.objects.filter(conversation_id=cid).count(), 2)  # B 의 메시지는 남지 않는다

    def test_budget_starts_real_dialog_and_policy_asks_region(self):
        out = self.send("A", text="식비 예산 줄여줘")
        cid = out.json()["conversationId"]
        adjustment = self.bot(out)["budgetAdjustment"]
        self.assertEqual(adjustment["phase"], "comparison")
        self.assertIn("comparison", adjustment)
        self.assertFalse(adjustment["canApprove"])
        self.assertNotIn("proposal", adjustment)
        store = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots)
        self.assertIsNone((store.get_task(engine.KEY) or {}).get("task_ref"))  # 질문만으로 저장 작업을 만들지 않는다
        after = self.bot(self.send("A", cid, text="이번 달 얼마 썼어?"))
        self.assertEqual(after["agent"], "spending")
        self.assertEqual(after["notices"], ["진행 중이던 예산 조정은 잠시 멈춰 뒀어요. 다시 이어갈 때 상태를 확인할게요."])
        self.assertEqual(self.bot(self.send("A", cid, text="청년 정책 추천해줘"))["lines"], [policy_agent.ASK_REGION])
        self.assertEqual(self.bot(self.send("A", choice="spending"))["lines"], ["어떤 소비가 궁금하세요?"])

    def test_extended_content_and_actionable_budget_suggestion(self):
        content = self.bot(self.send("B", text="식비 예산 얼마 남았어?"))  # 민재: 초과
        self.assertIn("초과했어요", " ".join(content["lines"]))
        self.assertEqual(content["choices"], [{"value": "budget", "label": "예산 조정하기"}])
        self.assertIn("필요하면 예산 조정을 요청할 수 있어요.", content["notices"])
        overview = self.bot(self.send("A", text="소비 분석해줘"))
        self.assertEqual(overview["view"][0], {"kind": "head", "title": "10월 1일~29일 · 소비"})  # 카드 보기도 함께
        chart = overview["chart"]
        self.assertEqual(chart["rows"][0]["text"], "주거·통신 700,000원 (46.36%)")
        lead = self.bot(self.send("A", text="최근 6개월 식비 어때?"))
        self.assertEqual(lead["lead"], ["3개월까지만 볼 수 있어 3개월로 보여드려요."])
        self.assertTrue(lead["text"].startswith(lead["lead"][0]))
        failed = self.send("A", text="이번 주 커피 얼마나 마셨지?")  # 가짜 해석이 모르는 문장 = 라우터 실패
        self.assertEqual((self.bot(failed)["lines"], failed.json()["route"]["fallback"]),
                         (["지금은 이해하지 못했어요, 아래에서 골라 주세요."], True))

    def test_same_request_key_is_answered_once(self):
        first = self.send("A", key="fixture-key-0001", text="이번 달 얼마 썼어?")
        again = self.send("A", key="fixture-key-0001", text="이번 달 얼마 썼어?")
        self.assertEqual(first.json(), again.json())
        self.assertEqual(ChatMessage.objects.filter(conversation_id=first.json()["conversationId"]).count(), 2)

    def test_conversation_changed_meanwhile_is_not_saved(self):
        cid = self.send("A", text="이번 달 얼마 썼어?").json()["conversationId"]
        real = engine.answer

        def racing(*args, **kwargs):  # 이 요청이 답을 만드는 사이 같은 대화의 다른 메시지가 먼저 저장됨
            out = real(*args, **kwargs)
            talk = Conversation.objects.get(pk=cid)
            talk.slots = {**talk.slots, "rev": talk.slots["rev"] + 1}
            talk.save(update_fields=["slots"])
            return out
        with mock.patch.object(engine, "answer", side_effect=racing):
            response = self.send("A", cid, text="그럼 교통비는?")
        self.assertEqual((response.status_code, response.json()), (409, engine.BUSY))
        self.assertEqual(ChatMessage.objects.filter(conversation_id=cid).count(), 2)


@fixture_db_only
@SERVICE
class ServiceChoiceTest(ServiceChatBase):
    """3단계: 답변 선택지 {value, turn} · 지난 선택지 409 · 기능 버튼 3개 · 담당 표시 · 예산 고르기 폼 차례. LLM(가짜 해석) 호출 수도 센다"""

    def setUp(self):
        super().setUp()
        from chat.llm.fake import FakeReading
        self.llm = mock.patch.object(FakeReading, "__call__", autospec=True, side_effect=FakeReading.__call__)
        self.calls = self.llm.start()
        self.addCleanup(self.llm.stop)

    def pick(self, persona, cid, content, label, **override):
        value = next(c["value"] for c in content["choices"] if c["label"] == label)
        return self.send(persona, cid, choice={"value": value, "turn": content["turn"], **override})

    def test_period_choice_then_stale_choice_is_rejected(self):
        out = self.send("A", text="이번 달 3개월 식비")
        cid, asked = out.json()["conversationId"], self.bot(out)
        self.assertEqual(asked["choices"], [{"value": "this_month", "label": "이번 달"}, {"value": "recent", "label": "최근 3개월"}])
        self.calls.reset_mock()
        picked = self.pick("A", cid, asked, "최근 3개월")
        self.assertEqual(self.bot(picked)["lines"][0], "최근 3개월 식비 지출이에요.")
        self.assertEqual(self.calls.call_count, 0)  # 정형 선택은 LLM 없이
        self.assertEqual(picked.json()["messages"][0]["content"]["text"], "최근 3개월")  # 사용자 말풍선 = 고른 이름
        self.send("A", cid, text="이번 달 얼마 썼어?")
        before = (ChatMessage.objects.filter(conversation_id=cid).count(), Conversation.objects.get(pk=cid).slots)
        stale = self.pick("A", cid, asked, "이번 달")  # 지난 질문의 버튼을 강제로 보냄
        self.assertEqual((stale.status_code, stale.json()), (409, engine.STALE))
        self.assertEqual((ChatMessage.objects.filter(conversation_id=cid).count(), Conversation.objects.get(pk=cid).slots), before)
        last = self.bot(self.send("A", cid, text="이번 달 3개월 식비"))
        wrong = self.send("A", cid, choice={"value": "last_month", "turn": last["turn"]})  # 최신 turn 이어도 없는 선택지
        self.assertEqual(wrong.status_code, 409)

    def test_detail_fallback_accept_and_cancel(self):
        out = self.send("A", text="커피 얼마나 썼어?")
        cid, asked = out.json()["conversationId"], self.bot(out)
        self.assertEqual([c["label"] for c in asked["choices"]], ["식비 전체 보기", "취소"])
        done = self.bot(self.pick("A", cid, asked, "식비 전체 보기"))
        self.assertEqual(done["lead"], ["커피만 따로 볼 수 없어 식비 전체로 조회했어요."])
        self.assertEqual(done["lines"][0], "이번 달(10월 1일~29일) 식비 전체에 390,000원 썼어요.")
        self.assertTrue(done["result"])
        asked = self.bot(self.send("A", cid, text="커피 얼마나 썼어?"))
        self.assertEqual(self.bot(self.pick("A", cid, asked, "취소"))["lines"], ["방금 질문은 취소했어요."])

    def test_feature_buttons_switch_agent_and_budget_free_text_turn(self):
        out = self.send("A", choice="budget")
        cid, budget = out.json()["conversationId"], self.bot(out)
        self.assertEqual((out.json()["agent"], budget["agent"], budget["inputs"], budget["lines"]),
                         ("budget", "budget", [], ["어떤 예산을 조정하고 싶으세요? 금액을 몰라도 괜찮아요."]))
        self.calls.reset_mock()
        sent = self.bot(self.send("A", cid, text="식비를 5만원 늘리고 싶어요"))  # 화면 adjustSentence 문장 그대로
        self.assertEqual((sent["agent"], sent["inputs"], sent["budgetAdjustment"]["phase"]), ("budget", [], "funding_scope"))
        self.assertEqual(self.calls.call_count, 1)  # 담당 예산 해석만 호출하고 라우터 재해석은 하지 않는다
        self.calls.reset_mock()
        spending = self.bot(self.send("A", cid, choice="spending"))
        self.assertEqual((spending["agent"], spending["lines"]), ("spending", ["어떤 소비가 궁금하세요?"]))
        policy = self.send("A", cid, choice="policy")
        self.assertEqual((policy.json()["agent"], self.bot(policy)["lines"]), ("policy", [policy_agent.ASK_REGION]))
        self.assertEqual(policy.json()["messages"][0]["content"]["text"], "청년 정책")
        self.assertEqual(self.calls.call_count, 0)  # 기능 버튼은 라우터 해석 없이

    def test_common_router_choices_open_policy_without_reclassification(self):
        from chat.llm.fake import EXTRA_SAID

        menu = [{"value": "spending", "label": "소비 분석"}, {"value": "budget", "label": "예산 조정"},
                {"value": "policy", "label": "청년 정책"}]
        cases = (
            ("안녕", "조금 더 자세히 적어 주세요."),
            ("날씨 어때?", "머니쿠션은 소비 분석, 예산 조정, 청년 정책 추천을 도와드려요."),
            ("이번 주 커피 얼마나 마셨지?", "지금은 이해하지 못했어요, 아래에서 골라 주세요."),
        )
        with mock.patch.dict(EXTRA_SAID, {"안녕": {"intents": ["unclear"]}}):
            for text, line in cases:
                with self.subTest(text=text):
                    out = self.send("A", text=text)
                    asked = self.bot(out)
                    cid = out.json()["conversationId"]
                    self.assertEqual((asked["lines"], asked["choices"]), ([line], menu))
                    self.assertEqual(asked["choices"], out.json()["features"])
                    self.calls.reset_mock()
                    picked = self.pick("A", cid, asked, "청년 정책")
                    self.assertEqual((picked.json()["agent"], self.bot(picked)["lines"]),
                                     ("policy", [policy_agent.ASK_REGION]))
                    self.assertEqual(picked.json()["messages"][0]["content"]["text"], "청년 정책")
                    self.assertEqual(Conversation.objects.get(pk=cid).active_agent, "policy")
                    self.assertEqual(self.calls.call_count, 0)

    def test_policy_region_buttons_answer_save_and_spending_goes_back(self):
        """청년 정책 (7단계 연결): 지역 버튼 → 문장 → 추천 → [1번 저장]은 대화 저장과 함께 DB 에 → 소비 질문은 소비 담당으로"""
        from policies.models import SavedPolicy
        from policies.tests.test_search import add
        from spending_fixture.test_fixture import user
        add("20260000000000000001", plcyNm="청년 월세 지원")  # 화성시 정책 (가짜 임베딩이라 순위는 상관없다)
        out = self.send("A", choice="policy")
        cid, content = out.json()["conversationId"], self.bot(out)
        self.assertEqual(content["inputs"], [policy_agent.REGION_SELECT])  # 화면은 지역 드롭다운
        content = self.bot(self.send("A", cid, text="경기도 화성시 동탄구"))  # 드롭다운은 고른 이름을 문장으로
        self.assertEqual(content["lines"], [policy_agent.ASK_TOPIC])
        self.assertEqual(content["examples"], policy_agent.TOPIC_EXAMPLES)  # 화면은 질문 아래 예시 목록
        content = self.bot(self.send("A", cid, text="월세 지원 있어?"))
        self.assertEqual([s["title"] for s in content["sections"]], ["1. 청년 월세 지원"])  # 결과 카드 (제목 + 점 목록)
        self.assertEqual(content["sections"][0]["policyNo"], "20260000000000000001")
        self.assertEqual((content["agent"], content["result"], content["notices"][0]), ("policy", True, policy_agent.NOTICE))
        content = self.bot(self.pick("A", cid, content, "1번 저장"))
        self.assertEqual(content["lines"], [policy_agent.SAVED.format(name="청년 월세 지원")])
        self.assertNotIn("saves", content)
        self.assertEqual(list(SavedPolicy.objects.filter(user=user("A")).values_list("policy_id", flat=True)),
                         ["20260000000000000001"])
        self.assertFalse(SavedPolicy.objects.exclude(user=user("A")).exists())
        content = self.bot(self.pick("A", cid, content, "다음에도 동탄구로 찾기"))  # 지역 기억 → 엔진이 내 정보 지역에 저장
        from users.models import FinancialProfile
        self.assertEqual(FinancialProfile.objects.get(user=user("A")).region_code, "41597")
        content = self.bot(self.pick("A", cid, content, policy_agent.SAVED_LIST))
        self.assertEqual([s["title"] for s in content["sections"]], ["1. 청년 월세 지원"])
        again = self.bot(self.send("A", choice="policy"))  # 새 대화 — 지역을 묻지 않는다
        self.assertEqual(again["lines"], [policy_agent.ASK_TOPIC])
        spending = self.send("A", cid, text="이번 달 얼마 썼어?")  # 정책 담당이 not_mine → 라우터가 다시 고른다
        self.assertEqual(spending.json()["agent"], "spending")

    def test_past_policy_card_detail_choice_opens_that_policy_by_number(self):
        """지난 답변 카드의 [자세히 보기]만 지난 선택지를 받는다. 정책에는 이름이 아니라 번호가 간다 (64b0c4b 리뷰 1)"""
        cid, cards = self._policy_results()
        self.send("A", cid, text="이번 달 얼마 썼어?")  # 담당이 소비 분석으로 바뀐 뒤에도
        label = next(c["label"] for c in cards["choices"] if c["value"].startswith("detail:"))
        self.calls.reset_mock()
        out = self.pick("A", cid, cards, label)
        self.assertEqual(out.status_code, 200)
        self.assertEqual(out.json()["messages"][0]["content"]["text"], label)  # 말풍선은 이름
        detail = self.bot(out)
        self.assertEqual((out.json()["agent"], detail["sections"][0]["policyNo"]), ("policy", cards["sections"][0]["policyNo"]))
        self.assertEqual(self.calls.call_count, 0)  # 라우터 분류 없이
        stale = self.pick("A", cid, cards, policy_agent.CHANGE_REGION)  # 다른 지난 선택지는 그대로 막는다
        self.assertEqual(stale.status_code, 409)

    def _policy_results(self):
        """화성시 동탄구로 '월세 지원 있어?' 추천까지 → (대화 번호, 마지막 봇 content)"""
        from policies.tests.test_search import add
        add("20260000000000000001", plcyNm="청년 월세 지원")
        out = self.send("A", choice="policy")
        cid, content = out.json()["conversationId"], self.bot(out)
        content = self.bot(self.send("A", cid, text="경기도 화성시 동탄구"))  # 지역 드롭다운
        return cid, self.bot(self.send("A", cid, text="월세 지원 있어?"))

    def test_review_saving_a_policy_that_became_inactive_says_it_failed(self):
        """PR 97 리뷰 4: 추천 뒤 정책이 비활성이 되면 저장되지 않으므로 '저장했어요'가 아니라 실패를 안내하고 저장 표시도 남기지 않는다"""
        from policies.models import Policy, SavedPolicy
        cid, content = self._policy_results()
        Policy.objects.filter(pk="20260000000000000001").update(is_active=False)
        content = self.bot(self.pick("A", cid, content, "1번 저장"))
        self.assertEqual(content["lines"], [policy_agent.SAVE_FAILED.format(name="청년 월세 지원")])
        self.assertFalse(SavedPolicy.objects.exists())
        store = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots)
        self.assertEqual(store.get_agent_state(engine.KEY, "policy")["saved"], [])

    def test_policy_review_failure_is_a_retry_message_without_result_cards(self):
        cid, _ = self._policy_results()
        with mock.patch("policies.review.reviewer", return_value=mock.Mock(side_effect=TimeoutError)):
            response = self.send("A", cid, text="월세 지원 찾아줘")
        content = self.bot(response)
        self.assertEqual(content["lines"], [policy_agent.REVIEW_FAILED])
        self.assertEqual(content["sections"], [])
        self.assertEqual(content["choices"], [])
        self.assertFalse(content["result"])
        self.assertEqual(content["agent"], "policy")

    def test_policy_home_confirmation_accepts_typed_yes_through_chat_api(self):
        from policies import profiles
        from policies.tests.test_search import DONGTAN, add
        from spending_fixture.test_fixture import user

        profiles.remember(user("A"), DONGTAN)
        add("20260000000000000001", plcyNm="청년 월세 지원")
        cid = self.send("A", choice="policy").json()["conversationId"]
        content = self.bot(self.send("A", cid, text="월세 지원 있어?"))
        self.assertEqual([c["label"] for c in content["choices"]], ["예", "아니요"])
        with mock.patch("policies.reading.read", side_effect=AssertionError("확인 답변은 다시 읽지 않는다")):
            content = self.bot(self.send("A", cid, text="네"))
        self.assertEqual([s["title"] for s in content["sections"]], ["1. 청년 월세 지원"])

    def test_policy_missing_embedding_model_is_not_http_500_and_can_retry(self):
        import os
        from chat.llm.fake import FakeReading
        from policies import reading

        cid, _ = self._policy_results()
        before = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(engine.KEY, "policy")
        with self.settings(CHAT_LLM="live"), mock.patch.dict(os.environ), \
                mock.patch.object(engine, "reading", return_value=FakeReading()), \
                mock.patch("policies.reading.reader", return_value=("fake", reading.fake_complete)), \
                mock.patch("policies.planner.chooser", return_value=None):
            os.environ.pop("EMBEDDING_MODEL", None)
            content = self.bot(self.send("A", cid, text="월세 지원 찾아줘"))
        self.assertFalse(content["result"])
        self.assertFalse(content["sections"])
        self.assertTrue(content["lines"])
        after = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots).get_agent_state(engine.KEY, "policy")
        self.assertEqual(after, before)
        retried = self.bot(self.send("A", cid, text="월세 지원 찾아줘"))
        self.assertEqual([s["title"] for s in retried["sections"]], ["1. 청년 월세 지원"])

    def test_review_policy_resumes_after_visiting_spending(self):
        """PR 97 리뷰 1: 청년 정책 → 소비 분석 → 청년 정책 순서로 고르면 보류된 정책 상담을 이어서 한다 (빈 답 없음)"""
        cid, content = self._policy_results()
        spending = self.send("A", cid, text="이번 달 얼마 썼어?")
        self.assertEqual(spending.json()["agent"], "spending")
        back = self.send("A", cid, choice="policy")
        self.assertEqual((back.json()["agent"], self.bot(back)["lines"]), ("policy", [policy_agent.ASK_TOPIC]))
        content = self.bot(self.send("A", cid, text="월세 지원 있어?"))
        self.assertEqual([s["title"] for s in content["sections"]], ["1. 청년 월세 지원"])

    def test_policy_save_is_not_kept_when_conversation_changed_meanwhile(self):
        from policies.models import SavedPolicy
        from policies.tests.test_search import add
        add("20260000000000000001", plcyNm="청년 월세 지원")
        out = self.send("A", choice="policy")
        cid, content = out.json()["conversationId"], self.bot(out)
        content = self.bot(self.send("A", cid, text="경기도 화성시 동탄구"))  # 지역 드롭다운
        content = self.bot(self.send("A", cid, text="월세 지원 있어?"))
        real = engine.answer

        def answer_then_someone_else_saves(*args, **kwargs):  # 처리하는 사이 같은 대화의 다른 메시지가 먼저 저장됨
            result = real(*args, **kwargs)
            talk = Conversation.objects.get(pk=cid)
            talk.slots = {**talk.slots, "rev": talk.slots["rev"] + 1}
            talk.save(update_fields=["slots"])
            return result
        with mock.patch.object(engine, "answer", side_effect=answer_then_someone_else_saves):
            busy = self.pick("A", cid, content, "1번 저장")
        self.assertEqual(busy.status_code, 409)
        self.assertFalse(SavedPolicy.objects.exists())  # 409 면 저장도 안 됨

    def test_suspended_budget_resume_choice_without_llm(self):
        cid = self.send("A", choice="budget").json()["conversationId"]
        moved = self.bot(self.send("A", cid, choice="spending"))
        self.assertEqual(moved["notices"], ["진행 중이던 예산 조정은 잠시 멈춰 뒀어요. 다시 이어갈 때 상태를 확인할게요."])
        asked = self.bot(self.send("A", cid, choice="budget"))
        self.assertEqual([c["label"] for c in asked["choices"]], ["이어서 하기", "취소하고 새로 시작"])
        self.calls.reset_mock()
        resumed = self.bot(self.pick("A", cid, asked, "이어서 하기"))
        self.assertEqual((resumed["lines"], resumed["inputs"], self.calls.call_count),
                         (["어떤 예산을 조정하고 싶으세요? 금액을 몰라도 괜찮아요."], [], 0))

    def test_yes_no_choice_without_llm(self):
        out = self.send("A", text="최근 3개월 식비 예산 얼마나 남았어?")
        cid, asked = out.json()["conversationId"], self.bot(out)
        self.assertEqual(asked["choices"], [{"value": "true", "label": "네"}, {"value": "false", "label": "아니요"}])
        self.calls.reset_mock()
        done = self.bot(self.pick("A", cid, asked, "네"))
        self.assertIn("식비 예산 350,000원 중 60,000원을 썼고 290,000원 남았어요.", done["lines"])
        self.assertEqual(self.calls.call_count, 0)

    def test_bad_choice_bodies(self):
        cid = self.send("A", text="이번 달 3개월 식비").json()["conversationId"]
        for body in ({"choice": {"value": "recent", "turn": 1, "x": 1}}, {"choice": {"value": "recent", "turn": True}},
                     {"choice": {"value": 3, "turn": 1}}, {"choice": "unknown"}):
            with self.subTest(body=body):
                self.assertEqual(self.send("A", cid, **body).status_code, 400)
        self.assertEqual(self.send("A", None, choice={"value": "recent", "turn": 1}).status_code, 400)  # 새 대화에 답변 선택지 없음


class FakeResponse:
    """Responses API 가짜 응답 (네트워크 없음)"""
    def __init__(self, text):
        self.raw = json.dumps({"status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1},
                               "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})

    def read(self):
        return self.raw.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@fixture_db_only
@SERVICE
class ServiceLiveTest(ServiceChatBase):
    """CHAT_LLM = "live"(더미 실행은 FIXTURE_LIVE_LLM=1): 실제 LLM 자리(가짜 응답 opener) 하나를 서버 동안 같이 쓰고, 설명 LLM 도 붙는다. 상한 · 장부 없음"""

    def setUp(self):
        super().setUp()
        import os
        from chat.interpretation.legacy import RoleFakeLLM
        from spending_analysis import explain
        from chat.interpretation.legacy import SAID
        from chat.llm.fake import EXTRA_SAID
        from chat.llm.live import LiveReading
        reader, self.sent = RoleFakeLLM({**SAID, **EXTRA_SAID}), []

        def opener(request, timeout):
            body = json.loads(request.data)
            self.sent.append(body["text"]["format"]["name"])
            return FakeResponse(reader(body["instructions"], body["input"]))
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-real", "LLM_MODEL": "test-model"}):
            live = LiveReading(None, None)
        live.client.opener = self.opener = opener
        live_setting = override_settings(CHAT_LLM="live")
        live_setting.enable()
        self.addCleanup(live_setting.disable)
        patcher = mock.patch.object(engine, "_live", live)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.live = live
        # 자유 입력의 외부 라우팅 전 동의도 실제 API 경로로 준비한다. 합성 회원 A만 대상이다.
        client = self.client_for("A")
        path = "/api/budgets/adjustment-ai-consent/"
        state = client.get(path).json()
        response = client.put(path, json.dumps({"agreed": True, "healthAgreed": True,
            **{key: state[key] for key in ("documentVersion", "documentHash", "healthDocumentVersion", "healthDocumentHash")}}),
            content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["mc_fixture_csrftoken"].value)
        self.assertEqual(response.status_code, 200, response.content)

    def test_live_reading_and_explain_without_cap(self):
        self.assertIs(engine.reading(), self.live)
        first = self.send("A", text="지난달보다 식비 더 썼어?")
        self.assertEqual(first.status_code, 200, first.content)
        cid = first.json()["conversationId"]
        out = self.bot(self.send("A", cid, text="왜 식비가 늘었어?"))
        self.assertNotIn("spending_explanation", self.sent)  # 일반 '왜 늘었어?'에 설명 문장용 LLM 없음 (MEMO 31)
        self.assertEqual(out["lines"][0], "식비 지출은 지난달 같은 기간보다 42,000원 늘었어요. ↑ 12.07%")
        self.assertEqual(self.live.usage()["limit"], None)
        self.assertEqual(self.live.usage()["used"], len(self.sent))

    def test_cap_reached_answers_limit_and_keeps_state(self):
        """CHAT_LLM_MAX_CALLS · CHAT_LLM_LEDGER(MEMO 25-12): 상한에 닿으면 보내지 않고 상한 안내, 대화 상태는 그대로"""
        import os
        import tempfile
        ledger = os.path.join(tempfile.mkdtemp(), "ledger.json")
        with override_settings(CHAT_LLM_MAX_CALLS=2, CHAT_LLM_LEDGER=ledger), mock.patch.object(engine, "_live", None), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-real", "LLM_MODEL": "test-model"}):
            live = engine.reading()
            live.client.opener = self.opener
            first = self.send("A", text="이번 달 얼마 썼어?")
            self.assertEqual(first.status_code, 200, first.content)
            cid = first.json()["conversationId"]  # 라우터 1 + 소비 1 = 상한 2
            before = Conversation.objects.get(pk=cid)
            out = self.send("A", cid, text="그럼 교통비는?").json()
        bot, after = out["messages"][1]["content"], Conversation.objects.get(pk=cid)
        self.assertEqual((out["route"]["fallback"], bot["lines"]), (True, ["설정된 AI 호출 상한에 도달해 질문을 처리하지 못했어요. 호출 한도를 확인한 뒤 다시 시도해 주세요."]))
        self.assertFalse(bot["choices"])
        self.assertEqual((after.slots["state"], after.active_agent), (before.slots["state"], before.active_agent))
        self.assertEqual((len(self.sent), live.usage()["blocked"]), (2, 1))  # 세 번째는 보내지 않았다
        with open(ledger) as f:
            self.assertEqual(json.load(f), {"limit": 2, "used": 2})

    def test_off_means_fake_reading(self):
        from chat.llm.fake import FakeReading
        with override_settings(CHAT_LLM="fake"):
            self.assertIsInstance(engine.reading(), FakeReading)



@fixture_db_only
@SERVICE
class ExplainPlanTest(ServiceChatBase):
    """B 증감 설명(MEMO 26, CHAT_EXPLAIN_PLAN) — 가짜 계획 · 답 LLM(chat.llm.fake 의 정해 둔 출력)으로 끝까지. 꺼짐 = 지금 템플릿"""

    def ask(self, persona, question, first=None):
        cid = self.send(persona, text=first).json()["conversationId"] if first else None
        return self.send(persona, cid, text=question).json()["messages"][1]["content"]

    def test_dev_trace_shows_entry_stage(self):
        """26-20: 분석 과정에 B 전 단계(해석 칸 · dialog 갈림길 · 설명 요청 판정)도 — B 를 거치지 않은 답에도"""
        out = self.ask("A", "이번 달 얼마 썼어?")
        self.assertEqual(out["devTrace"]["entry"], {"reading": {"mine": True, "target": "spend", "period": "this_month"},
                                                    "decision": {"status": "ready", "reason": None, "asking": None},
                                                    "items": [{"type": 1}], "explain": False})

    def test_unsupported_product_and_supported_lunch_average_in_service(self):
        with override_settings(CHAT_EXPLAIN_PLAN=True):
            coffee = self.ask("A", "커피를 자주 사 먹어서 그래?", first="지난달보다 식비 더 썼어?")
            lunch = self.ask("A", "점심값이 비싸진 거야?", first="지난달보다 식비 더 썼어?")
        self.assertEqual(coffee["devTrace"]["support"]["status"], "insufficient")
        self.assertIn("커피에 해당하는 내역", coffee["lines"][0])
        self.assertEqual(coffee["devTrace"]["queries"], [])
        self.assertEqual(lunch["devTrace"]["support"]["status"], "observation")
        self.assertEqual(lunch["devTrace"]["queries"][0]["target"], "slot:lunch")
        self.assertIn("점심 건당 평균", lunch["lines"][0])
        self.assertTrue(any("상품 가격이 올랐는지는 판단할 수 없어요" in line for line in lunch["lines"]))
        self.assertIn("결제 건수", lunch["lines"][-1])
        self.assertFalse(any(line.startswith(("네,", "아니요,")) for line in coffee["lines"] + lunch["lines"]))

    def test_direct_past_period_explanation_keeps_its_dates(self):
        for question, prefix in (("지난달 식비는 왜 많이 나왔어?", "지난달(9월 1일~30일)"),
                                 ("지난주 식비는 왜 많이 나왔어?", "지난주(10월 19일~25일)")):
            with self.subTest(question=question):
                out = self.ask("A", question)
                self.assertTrue(out["lines"][0].startswith(prefix), out["lines"])

    def test_fresh_explanation_records_the_scope_for_followups(self):
        first = self.send("A", text="지난달 식비는 왜 많이 나왔어?").json()
        out = self.bot(self.send("A", first["conversationId"], text="그럼 교통비는?"))
        self.assertTrue(out["lines"][0].startswith("지난달(9월 1일~30일)"), out)
        first = self.send("A", text="왜 식비가 늘었어?").json()
        out = self.bot(self.send("A", first["conversationId"], text="그럼 교통비는?"))
        self.assertEqual(out["devTrace"]["entry"]["items"][0]["type"], 7)

    def test_off_is_the_current_template(self):
        out = self.ask("B", "왜 식비가 늘었어?")
        self.assertEqual(out["lines"][0], "식비 지출은 지난달 같은 기간보다 224,000원 늘었어요. ↑ 55.17%")
        self.assertNotIn("stop", out["devTrace"])  # B 를 거치지 않았다 (분석 과정엔 B 전 단계만)

    def test_general_why_does_not_go_to_b(self):
        """MEMO 31: 가설이 없는 일반 '왜 늘었어?'는 B 를 켜도 B 계획 · 답을 부르지 않는다 — 짧은 서버 설명"""
        with override_settings(CHAT_EXPLAIN_PLAN=True), mock.patch("chat.llm.fake.fake_plan_answer") as llm:
            out = self.ask("B", "왜 식비가 늘었어?")
        llm.assert_not_called()
        self.assertEqual(out["lines"][0], "식비 지출은 지난달 같은 기간보다 224,000원 늘었어요. ↑ 55.17%")
        self.assertNotIn("stop", out["devTrace"])

    def test_yunseo_plan_stops_with_no_query(self):
        with override_settings(CHAT_EXPLAIN_PLAN=True):
            out = self.ask("A", "건당 금액이 올라서야?", first="지난달보다 식비 더 썼어?")
        self.assertEqual(out["lines"][:2], ["건당 평균은 12,000원에서 15,000원으로 올랐어요.", "반면 결제 건수는 29건에서 26건으로 줄었어요."])
        self.assertNotIn("결제 건수도 늘었는지", out["lines"][-1])  # 이미 답한 지표를 다시 확인하라고 하지 않는다.
        self.assertEqual((out["devTrace"]["stop"], out["devTrace"]["queries"], out["devTrace"]["plan"]["reason"]),
                         (None, [], "첫 결과의 건당 평균 주장으로 답할 수 있다"))  # 멈춤은 서버가 아니라 계획 LLM 이 (26-12 ②)

    def test_b_card_shows_cited_numbers_as_rows(self):
        """B 답 카드: 답에 쓴 주장의 전 → 후 값은 비교 줄로, 큰 금액과 같은 범위 증감 문장 · 줄로 옮긴 문장은 카드 문장에서 뺀다.
        확인 범위 한계 · 다음 확인은 notices(작은 글씨). 금액이 든 카드 줄은 개발용 분석 과정에 남기지 않는다"""
        cases = {"건당 금액이 올라서야?": ("확인한 변화", [("건당 평균", "12,000원 → 15,000원", "+3,000원", "up"),
                                                ("결제 건수", "29건 → 26건", "−3건", "down")]),
                 "점심값이 비싸진 거야?": ("확인한 변화 (고정지출 제외)", [("점심 건당 평균", "12,000원 → 15,000원", "+3,000원", "up")])}
        for question, (title, rows) in cases.items():
            with self.subTest(question=question), override_settings(CHAT_EXPLAIN_PLAN=True):
                out = self.ask("A", question, first="지난달보다 식비 더 썼어?")
                kinds = [b["kind"] for b in out["view"]]
                self.assertEqual(kinds[:3], ["head", "total", "rows"])
                block = out["view"][2]
                self.assertEqual((block["title"], [(r["name"], r["value"], r["note"], r["direction"]) for r in block["rows"]]), (title, rows))
                said = [l for b in out["view"] if b["kind"] == "text" for l in b["lines"]]
                self.assertFalse(any("42,000원" in l or "12,000원에서" in l for l in said), said)
                self.assertTrue(out["notices"])  # 한계 · 다음 확인
                self.assertFalse(any(n in l for n in out["notices"] for l in said))
                self.assertNotIn("_rows", json.dumps(out["devTrace"]))
                self.assertTrue(any("12,000원에서 15,000원" in l for l in out["lines"]))  # 글자만 보는 화면엔 문장 그대로

    def test_jiwoo_server_stops_for_missing_data(self):
        with override_settings(CHAT_EXPLAIN_PLAN=True), mock.patch("chat.llm.fake.fake_plan_answer") as llm:
            out = self.ask("C", "배달 때문이야?", first="지난달보다 식비 더 썼어?")
        # 지난달 자료가 다 모이지 않은 비교 → 기존 경로가 B 전에 멈춘다 (evidence_incomplete)
        self.assertEqual(out["lines"], ["자료가 다 모이지 않아 이유를 설명해 드릴 수 없어요."])
        self.assertNotIn("stop", out["devTrace"])
        llm.assert_not_called()  # 계획 · 답 LLM 을 부르지 않는다

    def test_delivery_question_says_only_checked_range(self):
        with override_settings(CHAT_EXPLAIN_PLAN=True):
            out = self.ask("A", "배달 때문이야?", first="지난달보다 식비 더 썼어?")
        self.assertEqual(out["lines"][0], "확인한 배달앱 이름 범위에서는 두 기간 모두 결제가 없었어요.")
        self.assertEqual(out["devTrace"]["queries"], [{"by": "merchant", "focus": "배달앱", "metric": "amount", "target": "online:배달앱"}])

    def test_rejected_answer_falls_back_to_template(self):
        """가설 질문의 B 답이 검사에서 떨어지면 서버 설명 그대로 (다른 LLM 으로 우회하지 않는다)"""
        bad = {"배달 때문이야?": [("배달이 잦아서 {id}", {"axis": "scope"})]}
        with override_settings(CHAT_EXPLAIN_PLAN=True), mock.patch.dict("chat.llm.fake.ANSWER_SAID", bad):
            out = self.ask("A", "배달 때문이야?", first="지난달보다 식비 더 썼어?")
        self.assertEqual(out["lines"][0], "식비 지출은 지난달 같은 기간보다 42,000원 늘었어요. ↑ 12.07%")  # 서버 설명 그대로
        self.assertEqual(out["devTrace"]["fallback"], "rejected")
        self.assertIn("1:free_text", out["devTrace"]["check"])

    def test_timeout_falls_back_to_template(self):
        from spending_analysis import claims
        ticks = iter([0, 0, 1, 1, 1, 13])  # 시작 · 계획 한도 · 계획 뒤 · 답 차례 확인 · 답 한도 · 답 뒤(12초 넘음)
        with override_settings(CHAT_EXPLAIN_PLAN=True), mock.patch.object(claims, "clock", lambda: next(ticks)):
            out = self.ask("A", "배달 때문이야?", first="지난달보다 식비 더 썼어?")
        self.assertTrue(out["lines"][0].endswith("↑ 12.07%"))
        self.assertEqual(out["devTrace"]["fallback"], "timeout")
