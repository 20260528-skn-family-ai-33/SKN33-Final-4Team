"""개발용 로컬 채팅 (local_chat) — 가짜 해석 · 예산/정책 대역 · 격리 fixture 자동 테스트 DB. 실제 LLM · 네트워크 호출 없음
실행: python manage.py test spending_fixture.test_local_chat --settings=spending_fixture.settings
"""
import json
import os
import tempfile
from unittest import mock

from django.test import TestCase

from chat.interpretation.legacy import RoleFakeLLM
from spending_analysis import explain

from . import local_chat, seed
from .test_fixture import DESIGN, fixture_db_only

NO_NETWORK = mock.patch("urllib.request.urlopen", side_effect=AssertionError("네트워크 호출 금지"))


def text(out):
    sections = [t for s in out["bot"].get("sections", []) for t in [s["title"], *s["lines"]]]
    return " ".join(out["bot"].get("lead", []) + out["bot"]["lines"] + sections + out["bot"]["notices"] + out["dev"])


@fixture_db_only
class GuardTest(TestCase):
    def test_stops_without_fixture_users_or_with_other_settings(self):
        with self.assertRaises(SystemExit):
            local_chat.guard()  # 자동 테스트 DB 에 아직 fixture 회원이 없다
        seed.apply(DESIGN)
        local_chat.guard()
        with mock.patch("django.conf.settings.SETTINGS_MODULE", "config.settings"), self.assertRaises(SystemExit):
            local_chat.guard()

    def test_live_needs_explicit_switch_cap_and_ledger(self):
        seed.apply(DESIGN)
        for argv, env in ((["--live", "--max-calls", "5", "--ledger", "/tmp/x.json"], {}),
                          (["--live", "--ledger", "/tmp/x.json"], {"LOCAL_CHAT_LIVE": "1"}),
                          (["--live", "--max-calls", "5"], {"LOCAL_CHAT_LIVE": "1"})):
            with self.subTest(argv=argv), mock.patch.dict(os.environ, env), NO_NETWORK, self.assertRaises(SystemExit):
                local_chat.main(argv)


@fixture_db_only
class LocalChatTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def setUp(self):
        self.app = local_chat.App(local_chat.FakeReading())
        patcher = NO_NETWORK
        patcher.start()
        self.addCleanup(patcher.stop)

    def say(self, persona="A", **body):
        return self.app.handle("/api/send", {"persona": persona, **body})

    def test_info_shows_fixture_date_mode_and_doubles(self):
        info = self.app.info()
        self.assertEqual((info["today"], info["mode"]), ("2026-10-29", "가짜 해석"))
        self.assertEqual({v["alias"] for v in info["personas"].values()}, {"윤서", "민재", "지우"})
        self.assertEqual(set(info["buttons"].values()), {"소비 분석하기", "예산 조정하기", "청년 정책 추천받기"})

    def test_analysis_follow_up_and_personas_are_separate(self):
        first = self.say(text="이번 달 얼마 썼어?")
        self.assertIn("이번 달(10월 1일~29일) 소비는", text(first))
        self.assertIn("교통·차량", text(self.say(text="그럼 교통비는?")))  # 직전 분석 이어받기
        other = self.say("B", text="그럼 교통비는?")  # 민재는 이어받을 분석이 없다
        self.assertIn("이어서 볼 이전 분석이 없어요.", text(other))
        self.app.handle("/api/new", {"persona": "A"})
        self.assertIn("이어서 볼 이전 분석이 없어요.", text(self.say(text="그럼 교통비는?")))  # 새 대화 = 새 상태

    def test_choice_click_answers_the_question(self):
        out = self.say(text="커피값 얼마 썼어?")
        food = next(c for c in out["bot"]["choices"] if c["value"] == "FOOD")
        self.assertIn("식비", text(self.say(choice=food)))

    def test_alias_is_kept_per_conversation_and_repick_by_click(self):
        out = self.say(text="커피값 얼마 썼어?")
        self.say(choice=next(c for c in out["bot"]["choices"] if c["value"] == "FOOD"))
        out = self.say(text="커피값 얼마 썼어?")
        self.assertIn("'커피값'은 이번 대화에서 정한 대로 식비 전체로 조회했어요.", text(out))
        repick = next(c for c in out["bot"]["choices"] if c["value"] == "repick")
        calls = len(self.app.llm.calls)
        out = self.say(choice=repick)  # 선택지 이름 그대로 → LLM 없이 다시 묻는다
        self.assertEqual((out["bot"]["lines"], len(self.app.llm.calls)), (["'커피값'을 어느 카테고리로 조회할까요?"], calls))
        self.app.handle("/api/new", {"persona": "A"})
        self.assertIn("'커피값'을 어느 카테고리로 조회할까요?", text(self.say(text="커피값 얼마 썼어?")))

    def test_budget_over_offers_adjustment_through_router_button(self):
        out = self.say("B", text="식비 예산 얼마 남았어?")
        self.assertIn("필요하면 예산 조정을 요청할 수 있어요.", out["bot"]["notices"])
        offer = next(c for c in out["bot"]["choices"] if c["label"] == "예산 조정하기")
        out = self.say("B", choice=offer)  # 기능 버튼과 같은 경로 — 승인 · 저장 없이 대역에 시작만 전달
        self.assertIn("[대역] 예산 조정 요청을 받았어요.", text(out))
        sent = self.app.sessions["B"].budget.last
        self.assertEqual((sent["mode"], [i["type"] for i in sent["context"]["shared"]["spending"]["items"]]), ("start", [3]))
        self.assertNotIn("예산 조정하기", [c["label"] for c in self.say(text="식비 예산 얼마 남았어?")["bot"]["choices"]])

    def test_stale_choice_is_rejected_without_changing_state(self):
        old = self.say(text="이번 달 3개월 식비")["bot"]["choices"]
        self.say(text="이번 달 얼마 썼어?")
        session = self.app.sessions["A"]
        before, calls = session.store.export(session.cid), len(self.app.llm.calls)
        out = self.say(choice=old[1])
        self.assertEqual(out["bot"]["notices"], [local_chat.STALE])
        self.assertEqual((session.store.export(session.cid), len(self.app.llm.calls)), (before, calls))

    def test_user_test_repros_through_router(self):
        out = self.say(text="데이트비는 식비로 볼게")  # 새 대화 — 라우터 → 소비. 범위 밖 안내가 아니다
        self.assertEqual(out["bot"]["lines"], ["이번 대화에서는 '데이트비'를 식비 전체로 조회할게요."])
        out = self.say(text="커피 얼마나 썼어?")
        self.assertEqual(out["bot"]["lines"], ["커피에 쓴 돈만 따로 구분해서 보여드리기는 아직 어려워요.", "대신 식비 전체를 확인할까요?"])
        self.assertEqual([c["label"] for c in out["bot"]["choices"]], ["식비 전체 보기", "취소"])
        out = self.say(choice=out["bot"]["choices"][0])
        self.assertEqual(out["bot"]["lead"], ["커피에 쓴 돈만 따로 구분할 수 없어 식비 전체로 조회했어요."])
        out = self.say(text="최근 6개월 식비 어때?")
        self.assertEqual(out["bot"]["lead"], ["최근 3개월까지만 볼 수 있어요. 최근 3개월 기준으로 알려드릴게요."])
        out = self.say(text="지난달 대비 최근 3개월로 비교해줘")
        self.assertEqual([c["label"] for c in out["bot"]["choices"]], ["완료된 3개월", "이번 달 포함 3개월"])
        out = self.say(choice=out["bot"]["choices"][1])
        self.assertIn("10월 1일~29일 1,510,000원", out["bot"]["lines"])

    def test_switch_to_doubles_is_marked_and_cancel_kinds_differ(self):
        out = self.say(text="식비 예산 줄여줘")
        self.assertIn("[대역] 예산 조정 요청을 받았어요.", text(out))
        self.assertIn("대역이 답했어요", text(out))
        out = self.say(text="이번 달 얼마 썼어?")  # 대역이 not_mine → 소비로 전환 · 예산 보류
        self.assertIn("진행 중이던 예산 조정은 잠시 멈춰 뒀어요.", text(out))
        self.say(text="최근 0개월 식비 알려줘")
        out = self.say(text="취소")  # 소비 질문 + 보류 예산 작업 → 무엇을 취소할지
        self.assertEqual([c["label"] for c in out["bot"]["choices"]], ["방금 질문", "예산 조정"])
        self.assertIn("방금 질문은 취소했어요.", text(self.say(choice=out["bot"]["choices"][0])))
        self.say(text="아까 예산 조정 이어서 하자")
        out = self.say(text="취소")  # 에이전트에 전달 → 대역이 서버 상태를 취소로 → 서버 확인
        self.assertIn("예산 조정 쪽에 취소 요청을 전달했어요.", text(out))
        self.assertIn("예산 조정 작업이 취소된 것을 확인했어요.", text(out))
        self.assertNotIn("방금 질문은 취소했어요.", text(out))

    def test_saving_task_is_not_said_to_stop(self):
        self.say(text="식비 예산 줄여줘")
        self.say(text="그렇게 해줘")
        out = self.say(text="이번 달 얼마 썼어?")
        self.assertIn("예산 조정은 저장이 진행 중이에요. 결과는 확인한 뒤 알려드릴게요.", text(out))
        self.assertNotIn("멈춰", text(out))

    def test_explain_shows_server_evidence_without_llm(self):
        self.say(text="지난달보다 식비 더 썼어?")
        calls = len(self.app.llm.calls)
        out = self.say(text="왜 식비가 늘었어?")
        self.assertEqual(out["bot"]["lines"][0], "식비 지출은 지난달 같은 기간보다 42,000원 늘었어요. ↑ 12.07%")
        self.assertIn("설명 LLM 은 쓰지 않고 템플릿 문장으로 답했어요.", text(out))
        self.assertNotIn("explain", out["bot"])
        self.assertEqual(len(self.app.llm.calls), calls + 1)  # 해석 1번뿐 — 설명 LLM 은 부르지 않는다

    def test_undecided_path_is_explained_not_coded(self):
        self.say(text="청년 정책 추천해줘")
        self.say(text="이번 달 얼마 썼어?")
        out = self.say(button="policy")
        self.assertIn("[검토 중] 보류된 청년 정책 추천이 있을 때", text(out))
        self.assertNotIn("policy_resume_undecided", str(out))
        out = self.say("B", text="청년 정책 추천해줘")
        self.assertIn("[대역]", text(out))
        self.assertIn("[검토 중] 청년 정책 추천 중 '취소'", text(self.say("B", text="취소")))

    def test_unknown_sentence_in_fake_mode_is_flagged(self):
        out = self.say(text="이번 주 커피 얼마나 마셨지?")
        self.assertEqual(out["bot"]["lines"], ["지금은 이해하지 못했어요, 아래에서 골라 주세요."])
        self.assertIn("[가짜 해석]", text(out))
        self.assertEqual(out["calls"], {"fake": len(self.app.llm.calls)})

    def test_bad_requests(self):
        for path, body in (("/api/send", {"persona": "Z", "text": "x"}), ("/api/send", {"persona": "A", "button": "x"}),
                           ("/api/nope", {"persona": "A"})):
            with self.subTest(path=path, body=body), self.assertRaises(KeyError):
                self.app.handle(path, body)


class FakeResponse:
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
class LiveExplainTest(TestCase):
    """실제 LLM 모드의 호출 경로 그대로(ResponsesClient · 역할별 스키마 · 장부) — 응답만 가짜 API. 네트워크 없음"""

    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def setUp(self):
        self.sent, self.explain_reply = [], {"sentences": ["건당 평균 결제액이 {F4}에서 {F5}로 바뀌었어요."]}
        self.ledger = os.path.join(tempfile.mkdtemp(), "ledger.json")
        reader = RoleFakeLLM({**local_chat.SAID, **local_chat.EXTRA_SAID})

        def opener(request, timeout):
            body = json.loads(request.data)
            self.sent.append(body["text"]["format"]["name"])
            if body["instructions"] == explain.PROMPT:
                return FakeResponse(json.dumps(self.explain_reply, ensure_ascii=False))
            return FakeResponse(reader(body["instructions"], body["input"]))
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-real"}):
            self.llm = local_chat.LiveReading(8, self.ledger, "test-model")
        self.llm.client.opener = opener
        self.app = local_chat.App(self.llm)
        patcher = NO_NETWORK
        patcher.start()
        self.addCleanup(patcher.stop)

    def say(self, said):
        return self.app.handle("/api/send", {"persona": "A", "text": said})

    def test_explain_uses_llm_sentences_with_server_numbers_and_counts_calls(self):
        self.say("지난달보다 식비 더 썼어?")
        before = len(self.sent)
        out = self.say("왜 식비가 늘었어?")
        self.assertEqual(self.sent[before:], ["spending_reading", "spending_explanation"])  # 해석 1 + 설명 1, 설명 전용 스키마
        self.assertEqual(out["bot"]["lines"][2], "건당 평균 결제액이 12,000원에서 15,000원으로 바뀌었어요.")
        self.assertIn("근거 문장은 설명 LLM 이 쓰고 숫자는 서버 값을 끼웠어요.", text(out))
        self.assertEqual(out["calls"]["used"], len(self.sent))
        with open(self.ledger) as f:
            self.assertEqual(json.load(f)["used"], len(self.sent))  # 설명 호출도 같은 장부에
        self.assertTrue(self.app.info()["explains"])

    def test_rejected_or_capped_explain_falls_back_to_template(self):
        self.say("지난달보다 식비 더 썼어?")
        self.explain_reply = {"sentences": ["식비가 42,000원 늘었어요."]}  # 숫자 직접
        out = self.say("왜 식비가 늘었어?")
        self.assertEqual(out["bot"]["lines"][2], "결제 건수는 지난달 같은 기간 29건, 이번 달 26건이에요.")
        self.assertIn("검사(숫자 · 없는 근거 · 금지 표현)에서 걸러져", text(out))
        self.llm.client.max_calls = len(self.llm.client.attempts) + 1  # 해석까지만 남기고 설명은 상한
        out = self.say("왜 식비가 늘었어?")
        self.assertIn("설명 LLM 호출이 실패하거나 상한에 걸려", text(out))
        self.assertEqual(out["bot"]["lines"][2], "결제 건수는 지난달 같은 기간 29건, 이번 달 26건이에요.")
