"""PR 97 재리뷰: 지역 저장·확인 답변·질문 교체·임베딩 설정 실패의 회귀 검사."""
import json
import os
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from policies import agent, llm, profiles
from policies.tests.test_agent import CID, Talk, said
from policies.tests.test_search import DONGTAN
from users.models import FinancialProfile


class RegionConversationTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="policy-review@example.test")
        FinancialProfile.objects.create(user=self.user, birth_date=date(1999, 3, 1), region_code=DONGTAN)
        patcher = mock.patch("policies.tools.find", return_value=([], {}))
        self.find = patcher.start()
        self.addCleanup(patcher.stop)

    def confirm(self):
        talk = Talk(user=self.user)
        talk.send("월세 지원 있어?")
        self.assertEqual(talk.state["ask"]["kind"], "confirm")
        return talk

    def assert_search(self, query, region=DONGTAN, keyword="월세"):
        needs, found_region, *_ = self.find.call_args.args
        self.assertEqual((needs[0].query, needs[0].keywords, found_region), (query, [keyword], region))

    def test_짧은_긍정은_원래_질문으로_검색하고_다시_읽지_않는다(self):
        for answer in ("예", "네", "응", "맞아", "맞아요", " 네! ", "네."):
            with self.subTest(answer=answer):
                talk = self.confirm()
                self.assertEqual(talk.send(answer)["status"], "replied")
                self.assert_search("월세 지원")
                talk.complete.assert_called_once()
                self.assertTrue(talk.state["confirmed"])

    def test_짧은_부정은_질문을_유지하고_다른_지역을_묻는다(self):
        for answer in ("아니요", "아니오", "아니", "아뇨", " 아니요! "):
            with self.subTest(answer=answer):
                talk = self.confirm()
                talk.send(answer)
                self.assertEqual(talk.state["ask"]["kind"], "region")
                self.assertEqual(talk.state["ask"]["text"], "월세 지원 있어?")
                talk.send("서울특별시")
                talk.send("종로구")
                self.assert_search("월세 지원", "11110")
                talk.complete.assert_called_once()

    def test_확인_중_새_정책_문장은_가입지역을_다시_확인한다(self):
        for question in ("아니, 취업 지원 찾아줘", "네, 그런데 취업 지원 찾아줘"):
            with self.subTest(question=question):
                talk = self.confirm()
                talk.complete.return_value = said(needs=(("취업 지원", ["취업"]),))
                talk.send(question)
                self.assertEqual(talk.state["ask"]["kind"], "confirm")
                self.assertEqual(talk.state["ask"]["text"], question)
                talk.send("네")
                self.assert_search("취업 지원", keyword="취업")
                self.assertEqual(talk.complete.call_count, 2)

    def test_이미_확인한_가입지역은_새_질문에서_다시_묻지_않는다(self):
        talk = self.confirm()
        talk.send("예")
        talk.complete.return_value = said(needs=(("취업 지원", ["취업"]),))
        self.assertEqual(talk.send("취업 지원 찾아줘")["status"], "replied")
        self.assert_search("취업 지원", keyword="취업")

    def test_지역_선택_중_새_질문은_원문과_키워드를_함께_바꾼다(self):
        FinancialProfile.objects.filter(user=self.user).update(region_code=None)
        talk = Talk(user=self.user)
        talk.send("월세 지원 있어?")
        talk.send("경기도")
        codes = talk.state["ask"]["codes"]
        talk.complete.return_value = said(needs=(("취업 지원", ["취업"]),))
        question = "아니, 취업 지원 찾아줘"
        talk.send(question)
        self.assertEqual(talk.state["ask"]["text"], question)
        self.assertEqual(talk.state["ask"]["codes"], codes)
        talk.send("화성시")
        talk.send("동탄구")
        self.assert_search("취업 지원", keyword="취업")
        self.assertEqual(talk.complete.call_count, 2)

    def test_가입지역_거절_후_새_질문은_가입지역_확인으로_돌아간다(self):
        talk = self.confirm()
        talk.send("아니요")
        talk.complete.return_value = said(needs=(("취업 지원", ["취업"]),))
        talk.send("취업 지원 찾아줘")
        self.assertEqual(talk.state["ask"]["kind"], "confirm")
        talk.send("예")
        self.assert_search("취업 지원", keyword="취업")

    def test_지역_선택_중_새_질문에_지역이_있으면_그곳으로_찾는다(self):
        talk = self.confirm()
        talk.send("아니요")
        talk.complete.return_value = said(needs=(("취업 지원", ["취업"]),), place="서울특별시 종로구")
        self.assertEqual(talk.send("서울 종로구 취업 지원 찾아줘")["status"], "replied")
        self.assert_search("취업 지원", "11110", "취업")

    def test_불명확한_짧은_답은_확인과_지역선택의_질문을_보존한다(self):
        for select_region in (False, True):
            with self.subTest(select_region=select_region):
                talk = self.confirm()
                if select_region:
                    talk.send("아니요")
                before = json.dumps(talk.state, sort_keys=True)
                talk.send("음")
                self.assertEqual(json.dumps(talk.state, sort_keys=True), before)
                talk.complete.assert_called_once()

    def test_확인_중_소비_질문은_상태를_보존하고_다른_담당으로_넘긴다(self):
        talk = self.confirm()
        before = json.dumps(talk.state, sort_keys=True)
        talk.complete.return_value = said(needs=(), mine=False)
        self.assertEqual(talk.send("이번 달 얼마 썼어?")["status"], "not_mine")
        self.assertEqual(json.dumps(talk.state, sort_keys=True), before)

    def test_맞춤추천_분야도_지역_확인을_거쳐_유지된다(self):
        talk = Talk(user=self.user, answer=said(needs=(), vague=True))
        with mock.patch("policies.planner.ask", return_value=("search", None, [], ["training"])):
            talk.plan = mock.Mock()
            talk.send("받을 수 있는 지원 있을까?")
        talk.send("네")
        self.assertEqual(self.find.call_args.args[0], [agent.FIELDS["training"][1]])


class RegionProfileTest(TestCase):
    def test_지역만_저장하면_기존_프로필의_예산수정시각을_유지한다(self):
        user = get_user_model().objects.create_user(email="policy-profile@example.test")
        profile = FinancialProfile.objects.create(user=user, living_budget=1000000)
        before = profile.updated_at
        profiles.remember(user, DONGTAN)
        profile.refresh_from_db()
        self.assertEqual((profile.region_code, profile.living_budget, profile.updated_at), (DONGTAN, 1000000, before))

    def test_프로필이_없으면_생성하고_반복_저장은_중복을_만들지_않는다(self):
        user = get_user_model().objects.create_user(email="policy-new-profile@example.test")
        profiles.remember(user, DONGTAN)
        profiles.remember(user, DONGTAN)
        self.assertEqual(FinancialProfile.objects.filter(user=user).count(), 1)
        self.assertEqual(profiles.remembered_region(user), DONGTAN)


class EmbeddingConfigTest(SimpleTestCase):
    def test_모델_누락과_빈값은_네트워크_호출_전_EmbedFailed(self):
        for value in (None, "", "  "):
            for call in (lambda: llm.embedder(live=True), lambda: llm.live_embed(["월세"])):
                with self.subTest(value=value, call=call), mock.patch.dict(os.environ, {}, clear=True), \
                        mock.patch("urllib.request.urlopen") as network:
                    os.environ["OPENAI_API_KEY"] = "test-only"
                    if value is not None:
                        os.environ["EMBEDDING_MODEL"] = value
                    with self.assertRaisesRegex(llm.EmbedFailed, "EMBEDDING_MODEL"):
                        call()
                    network.assert_not_called()

    def test_API키_누락과_빈값도_EmbedFailed(self):
        for value in (None, "", "  "):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"EMBEDDING_MODEL": "test-model"}, clear=True), \
                    mock.patch("urllib.request.urlopen") as network:
                if value is not None:
                    os.environ["OPENAI_API_KEY"] = value
                with self.assertRaisesRegex(llm.EmbedFailed, "OPENAI_API_KEY"):
                    llm.live_embed(["월세"])
                network.assert_not_called()

    def test_정상_설정으로_지정한_모델을_요청한다(self):
        with mock.patch.dict(os.environ, {"EMBEDDING_MODEL": "test-model", "OPENAI_API_KEY": "test-only"}), \
                mock.patch("urllib.request.urlopen") as network:
            network.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"data": [{"index": 0, "embedding": [0.25]}]}).encode()
            model, embed = llm.embedder(live=True)
            self.assertEqual((model, embed(["월세"])), ("test-model", [[0.25]]))
            self.assertEqual(json.loads(network.call_args.args[0].data)["model"], "test-model")

    def test_fake는_설정없이_같은_질문에_같은_벡터를_만든다(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            name, embed = llm.embedder(live=False)
            self.assertEqual(name, "fake")
            self.assertEqual(embed(["월세"]), embed(["월세"]))
            self.assertEqual(len(embed(["월세"])[0]), llm.DIMENSIONS)


class EmbeddingFailureConversationTest(TestCase):
    @override_settings(CHAT_LLM="live")
    def test_모델_누락은_실패_응답이고_대화_상태를_보존한다(self):
        talk = Talk()
        talk.lives_in_dongtan()
        before = json.dumps(talk.state, sort_keys=True)
        request = {"conversation_id": CID, "today": "2026-11-07", "text": "월세 지원 있어?"}
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("urllib.request.urlopen") as network:
            result = agent.handle(request, store=talk.store, user=talk.user, complete=talk.complete, plan=False, check=False)
        self.assertEqual(result["status"], "failed")
        self.assertIn("EMBEDDING_MODEL", result["error"])
        self.assertEqual(json.dumps(talk.state, sort_keys=True), before)
        network.assert_not_called()
