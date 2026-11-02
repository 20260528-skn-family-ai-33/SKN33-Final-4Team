"""새 챗봇 엔진(chat/engine.py, CHAT_ENGINE = "router") — 가짜 해석 · fixture DB 실제 분석 · Conversation.slots 저장. 네트워크 호출 없음
더미 설정 값(spending_fixture.service_settings)으로 켜서 chat.api.messages 경로로 시험한다 (MEMO 25절 1단계, 이전 spending_fixture/test_engine.py).
팀 기본 설정(엔진 legacy)의 동작은 chat/tests.py 가 그대로 확인한다.
실행: python manage.py test chat.test_engine --settings=spending_fixture.settings
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings

from chat.models import ChatMessage, Conversation

from spending_fixture import seed
from spending_fixture.test_fixture import DESIGN, EXPECTED, fixture_db_only
from spending_fixture.test_service import NO_NETWORK, PASSWORD, SERVICE, login

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

    def test_budget_and_policy_are_not_ready_and_end(self):
        out = self.send("A", text="식비 예산 줄여줘")
        cid = out.json()["conversationId"]
        self.assertEqual(self.bot(out)["lines"], [engine.NOT_READY["budget"]])
        store = engine.ConversationStore.load(Conversation.objects.get(pk=cid).slots)
        self.assertIsNone(store.get_task(engine.KEY))  # 보류 작업이 남지 않는다
        after = self.bot(self.send("A", cid, text="이번 달 얼마 썼어?"))
        self.assertEqual(after["notices"], [])  # '예산 조정은 잠시 멈춰 뒀어요' 같은 안내가 없다
        self.assertEqual(self.bot(self.send("A", cid, text="청년 정책 추천해줘"))["lines"], [engine.NOT_READY["policy"]])
        self.assertEqual(self.bot(self.send("A", choice="spending"))["lines"], ["어떤 소비가 궁금하세요?"])

    def test_extended_content_and_hidden_budget_suggestion(self):
        content = self.bot(self.send("B", text="식비 예산 얼마 남았어?"))  # 민재: 초과
        self.assertIn("초과했어요", " ".join(content["lines"]))
        self.assertEqual((content["choices"], content["notices"]), ([], []))  # [예산 조정하기] 는 1차에서 숨김
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

    def test_feature_buttons_switch_agent_and_budget_form_turn(self):
        out = self.send("A", choice="budget")
        cid, budget = out.json()["conversationId"], self.bot(out)
        self.assertEqual((out.json()["agent"], budget["agent"], budget["inputs"], budget["lines"]),
                         ("budget", "budget", ["budget_form"], [engine.BUDGET_ASK]))
        self.calls.reset_mock()
        sent = self.bot(self.send("A", cid, text="식비를 5만원 늘리고 싶어요"))  # 화면 adjustSentence 문장 그대로
        self.assertEqual((sent["lines"], sent["agent"], sent["inputs"]), ([engine.NOT_READY["budget"]], "budget", []))
        self.assertEqual(self.calls.call_count, 0)  # 담당(예산)에게 원문 그대로 — 라우터 해석 · 금액 해석 없음
        spending = self.bot(self.send("A", cid, choice="spending"))
        self.assertEqual((spending["agent"], spending["lines"]), ("spending", ["어떤 소비가 궁금하세요?"]))
        policy = self.send("A", cid, choice="policy")
        self.assertEqual((policy.json()["agent"], self.bot(policy)["lines"]), ("policy", [engine.NOT_READY["policy"]]))
        self.assertEqual(policy.json()["messages"][0]["content"]["text"], "청년 정책")
        self.assertEqual(self.calls.call_count, 0)  # 기능 버튼은 라우터 해석 없이

    def test_suspended_budget_resume_choice_without_llm(self):
        cid = self.send("A", choice="budget").json()["conversationId"]
        moved = self.bot(self.send("A", cid, choice="spending"))
        self.assertEqual(moved["notices"], ["진행 중이던 예산 조정은 잠시 멈춰 뒀어요. 다시 이어갈 때 상태를 확인할게요."])
        asked = self.bot(self.send("A", cid, choice="budget"))
        self.assertEqual([c["label"] for c in asked["choices"]], ["이어서 하기", "취소하고 새로 시작"])
        self.calls.reset_mock()
        resumed = self.bot(self.pick("A", cid, asked, "이어서 하기"))
        self.assertEqual((resumed["lines"], resumed["inputs"], self.calls.call_count), ([engine.BUDGET_ASK], ["budget_form"], 0))

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


@fixture_db_only
@SERVICE
class ServiceLiveTest(ServiceChatBase):
    """CHAT_LLM = "live"(더미 실행은 FIXTURE_LIVE_LLM=1): 실제 LLM 자리(가짜 응답 opener) 하나를 서버 동안 같이 쓰고, 설명 LLM 도 붙는다. 상한 · 장부 없음"""

    def setUp(self):
        super().setUp()
        import os
        from chat.interpretation.legacy import RoleFakeLLM
        from spending_analysis import explain
        from spending_fixture import local_chat
        from chat.llm.live import LiveReading
        from spending_fixture.test_local_chat import FakeResponse
        reader, self.sent = RoleFakeLLM({**local_chat.SAID, **local_chat.EXTRA_SAID}), []

        def opener(request, timeout):
            body = json.loads(request.data)
            self.sent.append(body["text"]["format"]["name"])
            if body["instructions"] == explain.PROMPT:
                return FakeResponse(json.dumps({"sentences": ["건당 평균 결제액이 {F4}에서 {F5}로 바뀌었어요."]}, ensure_ascii=False))
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

    def test_live_reading_and_explain_without_cap(self):
        self.assertIs(engine.reading(), self.live)
        cid = self.send("A", text="지난달보다 식비 더 썼어?").json()["conversationId"]
        out = self.bot(self.send("A", cid, text="왜 식비가 늘었어?"))
        self.assertEqual(self.sent[-2:], ["spending_reading", "spending_explanation"])
        texts = [l for b in out["view"] if b["kind"] == "text" for l in b["lines"]]
        self.assertIn("건당 평균 결제액이 12,000원에서 15,000원으로 바뀌었어요.", texts)
        self.assertEqual(self.live.usage()["limit"], None)
        self.assertEqual(self.live.usage()["used"], len(self.sent))

    def test_cap_reached_answers_router_failure_and_keeps_state(self):
        """CHAT_LLM_MAX_CALLS · CHAT_LLM_LEDGER(MEMO 25-12): 상한에 닿으면 보내지 않고 라우터 실패 문구 + 선택지, 대화 상태는 그대로"""
        import os
        import tempfile
        ledger = os.path.join(tempfile.mkdtemp(), "ledger.json")
        with override_settings(CHAT_LLM_MAX_CALLS=2, CHAT_LLM_LEDGER=ledger), mock.patch.object(engine, "_live", None), \
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-real", "LLM_MODEL": "test-model"}):
            live = engine.reading()
            live.client.opener = self.opener
            cid = self.send("A", text="이번 달 얼마 썼어?").json()["conversationId"]  # 라우터 1 + 소비 1 = 상한 2
            before = Conversation.objects.get(pk=cid)
            out = self.send("A", cid, text="그럼 교통비는?").json()
        bot, after = out["messages"][1]["content"], Conversation.objects.get(pk=cid)
        self.assertEqual((out["route"]["fallback"], bot["lines"]), (True, ["지금은 이해하지 못했어요, 아래에서 골라 주세요."]))
        self.assertTrue(bot["choices"])
        self.assertEqual((after.slots["state"], after.active_agent), (before.slots["state"], before.active_agent))
        self.assertEqual((len(self.sent), live.usage()["blocked"]), (2, 1))  # 세 번째는 보내지 않았다
        with open(ledger) as f:
            self.assertEqual(json.load(f), {"limit": 2, "used": 2})

    def test_off_means_fake_reading(self):
        from chat.llm.fake import FakeReading
        with override_settings(CHAT_LLM="fake"):
            self.assertIsInstance(engine.reading(), FakeReading)
