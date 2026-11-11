import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from chat.router_core import MemoryStateStore
from policies import agent
from policies.llm import EmbedFailed
from policies.tests.test_search import DONGTAN, add, near, question

TODAY = "2026-11-07"
CID = "conversation"
PERSON = SimpleNamespace(financial_profile=SimpleNamespace(birth_date=date(1999, 3, 1), gender="male",
                                                           monthly_take_home_income=2_300_000))


def said(needs=(("월세 지원", ["월세"]),), place=None, vague=False, mine=True):
    return json.dumps({"needs": [{"query": q, "keywords": k} for q, k in needs], "place": place, "vague": vague,
                       "mine": mine}, ensure_ascii=False)


def titles(result):
    return [s["title"] for s in result["reply"].get("sections", [])]


def rows(result, number=0):
    return result["reply"]["sections"][number]["lines"]


class Talk:
    """한 대화: 같은 store 로 여러 번 부른다. 버튼은 이름(label)을 문장으로 보낸다 (라우터 어댑터와 같다)"""

    def __init__(self, user=PERSON, answer=None, plan=False, check=False):
        self.store, self.user = MemoryStateStore(), user
        self.complete = mock.Mock(return_value=answer or said())
        self.plan = plan  # False = 행동 고르기 LLM 없이 규칙 (6단계 고정 흐름)
        self.check = check  # False = 돌아보기 없이 검색 결과 그대로
        self.last = None

    def send(self, text=None, mode="continue"):
        request = {"agent": "policy", "mode": mode, "entry": "button" if text is None else "text", "text": text,
                   "work_ref": None, "input_id": "x", "conversation_id": CID, "today": TODAY, "as_of": None,
                   "context": {}}
        self.last = agent.handle(request, store=self.store, user=self.user, complete=self.complete, embed=question,
                                 plan=self.plan, check=self.check)
        return self.last

    @property
    def labels(self):
        return [c["label"] for c in self.last["reply"]["choices"]]

    @property
    def state(self):
        return self.store.get_agent_state(CID, "policy")

    def lives_in_dongtan(self):
        self.send(None, mode="start")
        for label in ("경기도", "화성시", "동탄구"):
            self.send(label)


class RegionTest(TestCase):
    def test_버튼으로_들어오면_사는_지역을_시도부터_묻는다(self):
        talk = Talk()
        result = talk.send(None, mode="start")
        self.assertEqual((result["status"], result["reply"]["lines"]), ("awaiting_input", [agent.ASK_REGION]))
        self.assertEqual(len(talk.labels), 16)
        self.assertIn("전남광주통합특별시", talk.labels)

    def test_시도_시_구_순서로_좁히고_무엇을_찾을지_말로_묻는다(self):
        talk = Talk()
        talk.send(None, mode="start")
        talk.send("경기도")
        self.assertEqual(talk.last["reply"]["lines"], ["경기도의 어디인가요?"])
        self.assertIn("화성시", talk.labels)
        talk.send("화성시")
        self.assertIn("동탄구", talk.labels)
        result = talk.send("동탄구")
        self.assertEqual((result["status"], result["reply"]["lines"], talk.state["region"]),
                         ("awaiting_input", [agent.ASK_TOPIC], DONGTAN))
        self.assertEqual(talk.labels, [])  # 분야 버튼 없음 (멘토 요청 ④)

    def test_버튼_대신_지명을_써도_고른다(self):
        talk = Talk()
        talk.send(None, mode="start")
        talk.send("화성 동탄")
        self.assertEqual(talk.state["region"], DONGTAN)

    def test_지역을_모를_때_받은_문장은_지역을_고른_뒤_찾는다(self):
        add("RENT", vector=near(1), plcyNm="청년 월세 지원")
        talk = Talk()
        talk.send("월세 지원 있어?", mode="start")
        self.assertEqual(talk.state["ask"]["text"], "월세 지원 있어?")
        for label in ("경기도", "화성시"):
            talk.send(label)
        result = talk.send("동탄구")
        self.assertEqual(result["status"], "replied")
        self.assertEqual(titles(result), ["1. 청년 월세 지원"])
        talk.complete.assert_called_once_with(agent.reading.PROMPT, "월세 지원 있어?")


class AnswerTest(TestCase):
    def test_추천은_DB_값과_찾은_말_확인할_것_안내를_담는다(self):
        add("RENT", vector=near(1), plcyNm="청년 월세 지원", plcySprtCn="월 20만 원", earnCndSeCd="0043002",
            earnEtcCn="중위소득 150% 이하", aplyUrlAddr="https://example.org/rent")
        talk = Talk()
        talk.lives_in_dongtan()
        result = talk.send("월세 지원 있어?")
        self.assertEqual(result["reply"]["lines"], ["경기도 화성시 동탄구 기준으로 찾은 청년 정책이에요.", agent.OTHER_TOPIC])
        self.assertEqual(titles(result), ["1. 청년 월세 지원"])
        self.assertEqual(rows(result), ["신청: 상시", "지원: 월 20만 원", "확인할 것: 소득(중위소득 150% 이하)",
                                        "관련: 월세", "링크: https://example.org/rent"])
        self.assertEqual(result["reply"]["notices"][0], agent.NOTICE)
        self.assertEqual(talk.state["last"], ["RENT"])
        self.assertEqual(talk.labels, ["1번 저장", agent.CHANGE_REGION])

    def test_자격_불충족과_같은_이름_재등록은_뺀다(self):
        add("OLD", vector=near(1), plcyNm="청년 월세 지원")
        add("NEW", vector=near(2), plcyNm="청년 월세 지원")
        add("WORKER", vector=near(3), plcyNm="재직자 월세", jobCd="0013001")
        add("YOUNG", vector=near(4), plcyNm="10대 월세", sprtTrgtMinAge="15", sprtTrgtMaxAge="19")
        talk = Talk()
        talk.lives_in_dongtan()
        talk.send("취업 준비 중인데 월세 지원 있어?")  # '취업 준비' → 미취업자, 월급 정보보다 앞선다
        self.assertEqual(talk.state["results"], ["OLD"])

    def test_세_개씩_보여_주고_더_보기(self):
        for rank in range(1, 6):
            add(f"P{rank}", vector=near(rank), plcyNm=f"정책 {rank}")
        talk = Talk()
        talk.lives_in_dongtan()
        talk.send("월세 지원 있어?")
        self.assertEqual(talk.state["last"], ["P1", "P2", "P3"])
        self.assertEqual(talk.labels[:4], ["1번 저장", "2번 저장", "3번 저장", agent.MORE])
        result = talk.send(agent.MORE)
        self.assertEqual((result["status"], talk.state["last"]), ("replied", ["P4", "P5"]))
        self.assertEqual(titles(result), ["4. 정책 4", "5. 정책 5"])
        self.assertNotIn(agent.MORE, talk.labels)
        self.assertEqual(talk.labels[:2], ["4번 저장", "5번 저장"])  # 번호는 이어서

    def test_찾지_못하면_replied_로_안내(self):
        add("BUSAN", vector=near(1), zipCd="26350")
        talk = Talk()
        talk.lives_in_dongtan()
        result = talk.send("월세 지원 있어?")
        self.assertEqual(result["status"], "replied")
        self.assertEqual(result["reply"]["lines"], [agent.NONE_FOUND.format(region="경기도 화성시 동탄구")])


class FlowTest(TestCase):
    def test_막연하면_묻지_않고_취업_상태로_고른_분야로_맞춤_추천(self):
        """멘토 요청 ④: 분야 버튼 없이. LLM 이 분야를 못 짐작(가짜 모드)하면 코드가 취업 상태로 고른다 (PERSON = 월급 → 재직)"""
        add("RENT", vector=near(1), plcyNm="청년 월세 지원")
        talk = Talk(answer=said(needs=(), vague=True))
        talk.lives_in_dongtan()
        result = talk.send("받을 수 있는 돈 없어?")
        self.assertEqual(result["status"], "replied")
        self.assertEqual(result["reply"]["lines"][0], agent.AUTO_LEAD.format(fields="월세 · 주거, 저축 · 자산, 생활비"))
        self.assertEqual(titles(result), ["1. 청년 월세 지원"])
        self.assertNotIn(agent.OTHER_FIELD, talk.labels)

    def test_LLM_이_짐작한_분야로_맞춤_추천(self):
        add("LIVING", vector=near(1), plcyNm="청년 생활 지원금", plcyKywdNm="생활비")
        talk = Talk(answer=said(needs=(), vague=True), plan=mock.Mock(return_value=chose("search", fields=["living"])))
        talk.lives_in_dongtan()
        result = talk.send("나도 청년인데 받을 수 있는 돈 없어?")
        self.assertEqual(result["reply"]["lines"][0], agent.AUTO_LEAD.format(fields="생활비"))
        self.assertIn("관련: 생활비", rows(result))

    def test_예전_대화의_분야_버튼도_찾는다(self):
        """11-12 전에 시작한 대화 상태에 분야 질문이 남아 있어도 그 버튼 이름으로 찾는다"""
        add("RENT", vector=near(1), plcyNm="청년 월세 지원")
        talk = Talk()
        talk.lives_in_dongtan()
        talk.store.save_agent_state(CID, "policy", {**talk.state, "ask": {
            "kind": "field", "options": {"월세 · 주거": "월세 · 주거"}}})
        self.assertEqual(titles(talk.send("월세 · 주거")), ["1. 청년 월세 지원"])

    def test_문장_속_시군구는_묻지_않고_그_지역으로_찾고_사는_곳은_그대로(self):
        """멘토 요청: 질문에 지역이 있으면 그 지역으로 바로 찾는다. 사는 곳과 달라도 확인 버튼 없음 · 이번 검색에만"""
        add("BUSAN", vector=near(1), plcyNm="해운대 월세", zipCd="26350")
        talk = Talk(answer=said(place="부산 해운대구"))
        talk.lives_in_dongtan()
        result = talk.send("부산 해운대구로 이사 가는데 월세 지원 있어?")
        self.assertEqual((result["reply"]["lines"][0], titles(result)),
                         ("부산광역시 해운대구 기준으로 찾은 청년 정책이에요.", ["1. 해운대 월세"]))
        self.assertEqual(talk.state["region"], DONGTAN)  # 사는 곳은 그대로
        self.assertNotIn("다음에도 해운대구로 찾기", talk.labels)

    def test_넓은_지명은_그_안에서만_고르고_다시_읽지_않는다(self):
        add("BUSAN", vector=near(1), plcyNm="해운대 월세", zipCd="26350")
        talk = Talk(answer=said(place="부산"))
        talk.lives_in_dongtan()
        talk.send("부산 월세 지원 있어?")
        self.assertEqual(talk.last["reply"]["lines"], ["부산광역시의 어디인가요?"])
        self.assertIn("해운대구", talk.labels)
        result = talk.send("해운대구")
        self.assertEqual(titles(result), ["1. 해운대 월세"])
        self.assertEqual(talk.state["region"], DONGTAN)
        talk.complete.assert_called_once()  # 읽은 결과를 남겨 두고 다시 읽지 않는다

    def test_여러_곳인_지명은_후보만_묻는다(self):
        talk = Talk(answer=said(place="중구"))
        talk.lives_in_dongtan()
        talk.send("중구 월세 지원 있어?")
        self.assertEqual((talk.last["reply"]["lines"], len(talk.labels)), ([agent.ASK_WHICH_PLACE], 5))
        self.assertIn("서울특별시 중구", talk.labels)

    def test_사는_곳을_품는_지명은_묻지_않는다(self):
        add("HOME", vector=near(1))
        talk = Talk(answer=said(place="경기도"))
        talk.lives_in_dongtan()
        self.assertEqual(talk.send("경기도 월세 지원")["status"], "replied")
        self.assertEqual(talk.state["region_used"], DONGTAN)

    def test_사는_곳을_몰라도_문장_속_지역으로_바로_찾는다(self):
        """스크린샷 사례: 처음 문장에 '서울특별시 종로구' → 지역 질문 없이 종로구"""
        add("JONGNO", vector=near(1), plcyNm="종로 월세", zipCd="11110")
        talk = Talk(answer=said(place="서울특별시 종로구"))
        result = talk.send("서울특별시 종로구에서 월세 지원받을 수 있는지 찾아줘", mode="start")
        self.assertEqual((result["status"], result["reply"]["lines"][0], titles(result)),
                         ("replied", "서울특별시 종로구 기준으로 찾은 청년 정책이에요.", ["1. 종로 월세"]))
        self.assertIsNone(talk.state.get("region"))  # 사는 곳으로 기억하지 않는다
        talk.complete.return_value = said()  # 다음 문장에 지역이 없으면 그때 묻는다
        self.assertEqual(talk.send("면접 정장도 있어?")["reply"]["lines"], [agent.ASK_REGION])

    def test_다른_담당_문장은_not_mine_이고_상태를_바꾸지_않는다(self):
        talk = Talk(answer=said(needs=(), mine=False))
        talk.lives_in_dongtan()
        before = json.dumps(talk.state, sort_keys=True)
        self.assertEqual(talk.send("이번 달 배달비 얼마 썼어?"), {"status": "not_mine", "work_ref": None})
        self.assertEqual(json.dumps(talk.state, sort_keys=True), before)

    def test_취소_단어와_cancel_모드는_끝내고_상태를_지운다(self):
        for text, mode in (("그만", "continue"), (None, "cancel")):
            talk = Talk()
            talk.lives_in_dongtan()
            result = talk.send(text, mode=mode)
            self.assertEqual((result["status"], result["reply"]["lines"], talk.state), ("ended", [agent.ENDED], None))


class FailTest(TestCase):
    def test_생년월일이_없으면_내_정보로_안내하고_끝낸다(self):
        nobody = SimpleNamespace(financial_profile=SimpleNamespace(birth_date=None, gender=None, monthly_take_home_income=None))
        result = Talk(user=nobody).send(None, mode="start")
        self.assertEqual((result["status"], result["reply"]["lines"]), ("ended", [agent.NO_BIRTH]))

    def test_읽기_실패와_임베딩_실패는_failed_이고_상태는_그대로(self):
        talk = Talk()
        talk.lives_in_dongtan()
        before = json.dumps(talk.state, sort_keys=True)
        talk.complete.side_effect = Exception("network")
        self.assertEqual(talk.send("월세 지원")["status"], "failed")
        talk.complete.side_effect = None
        with mock.patch("policies.search.search", side_effect=EmbedFailed("HTTP 500")):
            result = talk.send("월세 지원")
        self.assertEqual((result["status"], result["error"]), ("failed", "embed:HTTP 500"))
        self.assertEqual(json.dumps(talk.state, sort_keys=True), before)

    def test_지역_질문에_엉뚱한_답이면_버튼을_다시_보인다(self):
        talk = Talk()
        talk.lives_in_dongtan()
        talk.send(None)  # 기능 버튼 → 사는 곳은 알아서 무엇을 찾을지 말로 묻는다
        self.assertEqual(talk.last["reply"]["lines"], [agent.ASK_TOPIC])
        talk.send("경기도")  # 분야 질문에 지명을 쓰면 새 문장으로 읽는다 → 이번엔 지역 질문을 직접 만든다
        talk.store.save_agent_state(CID, "policy", {**talk.state, "ask": {
            "kind": "region", "options": {"경기도": "41000"}, "codes": ["41000"], "text": None, "needs": None, "home": True}})
        result = talk.send("음")
        self.assertEqual((result["status"], result["reply"]["lines"]), ("awaiting_input", [agent.PICK_BUTTON]))
        self.assertEqual(talk.labels, ["경기도"])


class SaveTest(TestCase):
    def setUp(self):
        for rank in range(1, 4):
            add(f"P{rank}", vector=near(rank), plcyNm=f"정책 {rank}")
        self.talk = Talk()
        self.talk.lives_in_dongtan()
        self.talk.send("월세 지원 있어?")

    def test_저장_버튼은_저장할_번호를_돌려주고_나머지_버튼은_그대로(self):
        result = self.talk.send("2번 저장")
        self.assertEqual((result["status"], result["save_policies"]), ("replied", ["P2"]))
        self.assertEqual(result["reply"]["lines"], [agent.SAVED.format(name="정책 2")])
        self.assertEqual(self.talk.labels, ["1번 저장", "3번 저장", agent.CHANGE_REGION])
        self.assertEqual(self.talk.state["saved"], ["P2"])
        self.talk.send("1번 저장")
        self.assertEqual(self.talk.labels, ["3번 저장", agent.CHANGE_REGION])

    def test_저장한_칸이_없는_예전_상태도_읽는다(self):
        old = {k: v for k, v in self.talk.state.items() if k != "saved"}  # 저장 기능 전에 시작한 대화
        self.talk.store.save_agent_state(CID, "policy", old)
        self.assertEqual(self.talk.send("1번 저장")["save_policies"], ["P1"])
        self.talk.send("월세 지원 있어?")  # 다시 찾으면 이미 저장한 정책은 저장 버튼을 빼고 보인다
        self.assertEqual(self.talk.labels[:2], ["2번 저장", "3번 저장"])

    def test_저장은_DB_에_쓰지_않는다(self):
        from policies.models import SavedPolicy
        self.talk.send("1번 저장")
        self.assertFalse(SavedPolicy.objects.exists())  # 엔진이 save_policies 를 보고 저장한다 (7단계)


class TidyTest(TestCase):
    def test_원문_글머리_기호를_나누고_길면_자른다(self):
        from policies.tools import tidy
        self.assertEqual(tidy("ㅁ대상: 만 19~34세 ㅇ (전세) 보증금 이하 □ 한도"), "대상: 만 19~34세 · (전세) 보증금 이하 · 한도")
        self.assertEqual(tidy("-스마트 구인·구직 -상담·취업알선"), "스마트 구인·구직 · 상담·취업알선")
        self.assertEqual(tidy("월 20만 원"), "월 20만 원")
        long = tidy("가나다 " * 40)
        self.assertTrue(long.endswith("…") and len(long) <= 81)


class MemberTest(TestCase):
    """회원(로그인한 진짜 회원)만: [저장한 정책 보기] · [다음에도 …로 찾기]"""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from users.models import FinancialProfile
        self.user = get_user_model().objects.create_user(email="member@example.com")
        FinancialProfile.objects.create(user=self.user, birth_date=date(1999, 3, 1), gender="male")
        for rank in range(1, 3):
            add(f"P{rank}", vector=near(rank), plcyNm=f"정책 {rank}")

    def talk(self):
        return Talk(user=self.user)

    def test_추천_뒤에_저장_목록과_지역_기억_버튼(self):
        talk = self.talk()
        talk.lives_in_dongtan()
        talk.send("월세 지원 있어?")
        self.assertEqual(talk.labels, ["1번 저장", "2번 저장", agent.SAVED_LIST, "다음에도 동탄구로 찾기",
                                       agent.CHANGE_REGION])

    def test_가입_지역은_대화에서_처음_한_번_확인하고_예면_그_지역으로(self):
        """멘토 요청 ②: 지역 없이 물으면 "가입 지역(…)으로 찾으시는 거 맞나요?" — 이 대화에서 처음 한 번만"""
        from policies import profiles
        profiles.remember(self.user, DONGTAN)  # 온보딩에서 고른 지역
        talk = self.talk()
        self.assertEqual(talk.send(None, mode="start")["reply"]["lines"], [agent.ASK_TOPIC])  # 지역을 묻지 않는다
        result = talk.send("월세 지원 있어?")
        self.assertEqual((result["reply"]["lines"], talk.labels),
                         ([agent.CONFIRM_HOME.format(name="경기도 화성시 동탄구")], [agent.YES, agent.NO]))
        result = talk.send(agent.YES)
        self.assertEqual(titles(result), ["1. 정책 1", "2. 정책 2"])
        talk.complete.assert_called_once()  # 확인 뒤 다시 읽지 않는다
        self.assertNotIn("다음에도 동탄구로 찾기", talk.labels)  # 이미 저장된 지역
        self.assertEqual(titles(talk.send("월세 지원 있어?"))[0], "1. 정책 1")  # 같은 대화 두 번째는 묻지 않는다
        self.assertEqual(self.talk().send("월세 지원 있어?")["reply"]["lines"][0],
                         agent.CONFIRM_HOME.format(name="경기도 화성시 동탄구"))  # 새 대화는 다시

    def test_아니요면_이번_검색_지역을_고르고_다음에도_버튼으로_가입_지역을_바꾼다(self):
        from policies import profiles
        from users.models import FinancialProfile
        profiles.remember(self.user, DONGTAN)
        add("JONGNO", vector=near(1), plcyNm="종로 월세", zipCd="11110")
        talk = self.talk()
        talk.send("월세 지원 있어?")
        self.assertEqual(talk.send(agent.NO)["reply"]["lines"], [agent.ASK_OTHER_REGION])
        talk.send("서울특별시")
        result = talk.send("종로구")
        self.assertEqual(titles(result), ["1. 종로 월세"])
        self.assertIn("다음에도 종로구로 찾기", talk.labels)
        result = talk.send("다음에도 종로구로 찾기")
        self.assertEqual((result["status"], result["save_profile"]), ("replied", {"region_code": "11110"}))
        self.assertEqual(FinancialProfile.objects.get(user=self.user).region_code, DONGTAN)  # 핸들러는 쓰지 않는다 — 엔진이
        profiles.remember(self.user, "11110")  # 엔진이 한 것처럼
        self.assertEqual(self.talk().send("월세 지원 있어?")["reply"]["lines"][0],
                         agent.CONFIRM_HOME.format(name="서울특별시 종로구"))

    def test_지역을_말하면_확인_없이_찾고_다음에도_버튼(self):
        from policies import profiles
        profiles.remember(self.user, DONGTAN)
        add("JONGNO", vector=near(1), plcyNm="종로 월세", zipCd="11110")
        talk = Talk(user=self.user, answer=said(place="서울특별시 종로구"))
        result = talk.send("서울특별시 종로구 월세 지원 있어?", mode="start")
        self.assertEqual(titles(result), ["1. 종로 월세"])
        self.assertIn("다음에도 종로구로 찾기", talk.labels)

    def test_가입_지역이_없는_회원은_지역을_고르고_다음에도_버튼(self):
        talk = self.talk()
        talk.lives_in_dongtan()
        talk.send("월세 지원 있어?")
        self.assertIn("다음에도 동탄구로 찾기", talk.labels)  # 저장된 지역이 없으니 저장을 권한다

    def test_저장한_정책_보기_버튼과_문장(self):
        from policies import saved
        talk = self.talk()
        talk.lives_in_dongtan()
        result = talk.send("저장한 정책 보여 줘")
        self.assertEqual(result["reply"]["lines"], [agent.NO_SAVED])
        saved.save(self.user, "P2")
        talk.send("월세 지원 있어?")
        result = talk.send(agent.SAVED_LIST)
        self.assertEqual((result["status"], result["reply"]["lines"], titles(result)),
                         ("replied", [agent.SAVED_HEAD], ["1. 정책 2"]))
        self.assertEqual(rows(result)[0], "신청: 상시")
        self.assertNotIn(agent.SAVED_LIST, talk.labels)

    def test_회원이_아니면_저장_목록을_못_본다(self):
        talk = Talk()
        talk.lives_in_dongtan()
        self.assertEqual(talk.send("저장한 정책")["reply"]["lines"], [agent.SAVED_NEED_LOGIN])


def chose(action, number=None, topics=(), fields=()):
    return json.dumps({"action": action, "number": number, "topics": list(topics), "fields": list(fields)})


class PlannerTest(TestCase):
    """8단계 행동 고르기: 읽은 문장의 행동만 LLM 이 고르고, 번호 확인 · 상세 값은 코드가 한다"""

    def setUp(self):
        add("P1", vector=near(1), plcyNm="청년 월세 지원", plcySprtCn="월 20만 원", sbmsnDcmntCn="임대차계약서",
            operInstCdNm="서울시")
        add("P2", vector=near(2), plcyNm="청년 전월세 대출보증", aplyUrlAddr="https://example.org/loan")
        self.plan = mock.Mock(return_value=chose("search"))
        self.talk = Talk(plan=self.plan)
        self.talk.lives_in_dongtan()
        self.talk.send("월세 지원 있어?")

    def test_검색은_LLM_이_골라도_같은_추천(self):
        self.assertEqual(titles(self.talk.last), ["1. 청년 월세 지원", "2. 청년 전월세 대출보증"])
        message = json.loads(self.plan.call_args[0][1])
        self.assertEqual((message["문장"], message["직전 추천"]), ("월세 지원 있어?", "없음"))

    def test_번호로_물으면_그_정책의_원문_항목을_보인다(self):
        self.plan.return_value = chose("detail", 1, ["documents", "support", "apply"])
        result = self.talk.send("1번 서류랑 얼마 주는지 알려줘")
        self.assertEqual(json.loads(self.plan.call_args[0][1])["직전 추천"], ["1. 청년 월세 지원", "2. 청년 전월세 대출보증"])
        self.assertEqual((result["status"], result["reply"]["lines"], titles(result)),
                         ("replied", [agent.DETAIL_HEAD], ["청년 월세 지원"]))
        self.assertEqual(rows(result), ["지원 내용: 월 20만 원", "신청 방법: 공고 원문에 없어요. 서울시에 확인해 주세요.",
                                        "제출 서류: 임대차계약서", "링크: 정보 없음 (온통청년에서 정책명으로 찾아 주세요)",
                                        "운영 기관: 서울시"])
        self.assertIn("1번 저장", self.talk.labels)  # 추천 뒤 버튼은 그대로

    def test_어느_정책인지_모르면_직전_추천을_버튼으로_묻는다(self):
        self.plan.return_value = chose("detail", None, ["apply"])
        result = self.talk.send("그거 어떻게 신청해?")
        self.assertEqual((result["reply"]["lines"], self.talk.labels),
                         ([agent.WHICH], ["1. 청년 월세 지원", "2. 청년 전월세 대출보증"]))
        result = self.talk.send("2. 청년 전월세 대출보증")
        self.assertEqual(titles(result), ["청년 전월세 대출보증"])
        self.assertEqual(rows(result)[-1], "링크: https://example.org/loan")
        self.assertIn("1번 저장", self.talk.labels)  # 고른 뒤에도 추천 버튼이 돌아온다
        result = self.talk.send("그거 얼마 줘?")  # 방금 본 정책을 LLM 에게 알려 주고, 번호를 못 정하면 그 정책
        self.assertEqual(json.loads(self.plan.call_args[0][1])["방금 본 정책"], "2. 청년 전월세 대출보증")
        self.assertEqual(titles(result), ["청년 전월세 대출보증"])
        self.plan.return_value = chose("search")
        self.talk.send("월세 지원 있어?")  # 새로 찾으면 잊는다
        self.assertIsNone(self.talk.state["focus"])

    def test_추천_밖_번호는_쓰지_않는다(self):
        self.plan.return_value = chose("detail", 7, ["support"])
        self.assertEqual(self.talk.send("7번 알려줘")["reply"]["lines"], [agent.WHICH])

    def test_추천이_없으면_무엇을_찾을지_묻는다(self):
        talk = Talk(plan=mock.Mock(return_value=chose("detail", None, ["support"])))
        talk.lives_in_dongtan()
        result = talk.send("그거 얼마 줘?")
        self.assertEqual((result["reply"]["lines"], talk.labels), ([agent.NO_TARGET, agent.ASK_TOPIC], []))

    def test_LLM_이_실패하거나_모양이_틀리면_규칙으로(self):
        for broken in (RuntimeError("network"), None):
            self.plan.side_effect, self.plan.return_value = broken, '{"action": "fly"}'
            self.assertEqual(titles(self.talk.send("월세 지원 있어?"))[0], "1. 청년 월세 지원")

    def test_끝내기와_다른_담당(self):
        self.plan.return_value = chose("not_mine")
        self.assertEqual(self.talk.send("이번 달 얼마 썼어?")["status"], "not_mine")
        self.plan.return_value = chose("end")
        self.assertEqual(self.talk.send("이제 됐어 고마워")["status"], "ended")

    def test_파서가_모양이_틀린_답을_거절한다(self):
        from policies import planner
        from policies.reading import Reading
        read, shown = Reading(needs=[], place=None, vague=False), [(1, "청년 월세 지원")]
        for wrong in ('{"action": "detail", "number": "1", "topics": [], "fields": []}',  # 번호 자리에 글자
                      '{"action": "detail", "number": true, "topics": [], "fields": []}',  # 참/거짓
                      '{"action": "detail", "number": 1, "topics": ["money"], "fields": []}',  # 모르는 항목
                      '{"action": "search", "number": null, "topics": [], "fields": ["money"]}',  # 모르는 분야
                      '{"action": "ask_field", "number": null, "topics": [], "fields": []}',  # 없앤 행동 (11-12)
                      '{"action": "detail", "number": 1, "topics": [], "fields": [], "extra": 1}',  # 모르는 칸
                      '{"action": "detail", "number": 1, "topics": []}',  # 빠진 칸
                      '번호는 1번이에요'):  # JSON 이 아님
            self.assertIsNone(planner.ask("1번 알려줘", read, shown, lambda system, user, w=wrong: w), wrong)
        good = planner.ask("1번 알려줘", read, shown, lambda system, user: chose("detail", 1, ["support"]))
        self.assertEqual(good, ("detail", 1, ["support"], []))
        auto = planner.ask("돈 없어?", read, shown, lambda system, user: chose("search", fields=["living", "living", "job", "mind", "saving"]))
        self.assertEqual(auto, ("search", None, list(planner.DEFAULT_TOPICS), ["living", "job", "mind"]))  # 겹침 빼고 3개까지


def kept(*numbers):
    return json.dumps({"keep": list(numbers)})


class ReviewTest(TestCase):
    """8단계 돌아보기: 검색 뒤 LLM 이 질문과 맞는 후보만 남긴다. 3개를 억지로 채우지 않는다"""

    def setUp(self):
        add("FEE", vector=near(1), plcyNm="청년 국가기술자격 응시료 지원", plcySprtCn="국가기술자격 응시료 지원")
        add("BUILD", vector=near(2), plcyNm="건설기술인 진입장벽 완화", plcySprtCn="건설 재직자 교육")
        add("ARMY", vector=near(3), plcyNm="장병 자격증 응시료", plcySprtCn="현역 장병 응시료")
        add("FEE2", vector=near(4), plcyNm="자격증 취득 지원", plcySprtCn="자격증 시험 응시료")
        self.check = mock.Mock(return_value=kept(1, 4))
        self.talk = Talk(check=self.check)
        self.talk.lives_in_dongtan()

    def test_맞는_것만_검색_순서대로_보인다(self):
        result = self.talk.send("자격증 시험비가 너무 비싸")
        self.assertEqual(titles(result), ["1. 청년 국가기술자격 응시료 지원", "2. 자격증 취득 지원"])
        self.assertEqual(self.talk.state["results"], ["FEE", "FEE2"])
        message = json.loads(self.check.call_args[0][1])
        self.assertEqual(message["문장"], "자격증 시험비가 너무 비싸")
        self.assertEqual(message["후보"][1], "2. 건설기술인 진입장벽 완화 — 건설 재직자 교육")

    def test_맞는_게_없으면_찾지_못했다고_답한다(self):
        self.check.return_value = kept()
        result = self.talk.send("자격증 시험비가 너무 비싸")
        self.assertEqual((result["status"], titles(result)), ("replied", []))
        self.assertEqual(result["reply"]["lines"], [agent.NONE_FOUND.format(region="경기도 화성시 동탄구")])

    def test_후보_밖_번호는_무시하고_실패하면_거르지_않는다(self):
        self.check.return_value = kept(1, 99)
        self.assertEqual(titles(self.talk.send("자격증 시험비")), ["1. 청년 국가기술자격 응시료 지원"])
        for broken in ('{"keep": ["1"]}', "1번만요"):
            self.check.return_value = broken
            self.assertEqual(len(titles(self.talk.send("자격증 시험비"))), 3)  # 걸러지지 않은 검색 결과 그대로

    def test_맞춤_추천은_원문과_고른_분야를_함께_본다(self):
        self.talk.complete.return_value = said(needs=(), vague=True)
        self.talk.send("받을 수 있는 거 없나")
        self.assertEqual(json.loads(self.check.call_args[0][1])["문장"],
                         "받을 수 있는 거 없나 (고른 분야: 월세 · 주거, 저축 · 자산, 생활비)")


class ButtonOrderTest(TestCase):
    """대화 상태는 DB jsonb 에 저장되고 jsonb 는 객체 키 순서를 바꾼다 → 버튼 순서는 상태에서 다시 만든다"""

    def test_상세_저장_뒤에도_버튼_순서가_그대로(self):
        for rank in range(1, 3):
            add(f"P{rank}", vector=near(rank), plcyNm=f"정책 {rank}")
        talk = Talk(plan=mock.Mock(return_value=chose("search")))
        keep = talk.store.save_agent_state

        def like_jsonb(cid, agent_name, state):  # 키를 정렬해 저장 (jsonb 처럼 순서가 바뀐다)
            keep(cid, agent_name, None if state is None else json.loads(json.dumps(state, sort_keys=True)))
        talk.store.save_agent_state = like_jsonb
        talk.lives_in_dongtan()
        talk.send("월세 지원 있어?")
        order = ["1번 저장", "2번 저장", agent.CHANGE_REGION]
        self.assertEqual(talk.labels, order)
        talk.plan.return_value = chose("detail", 1, ["support"])
        talk.send("1번 얼마 줘?")
        self.assertEqual(talk.labels, order)
        talk.send("2번 저장")
        self.assertEqual(talk.labels, ["1번 저장", agent.CHANGE_REGION])


class ReviewTest97(TestCase):
    """PR 97 리뷰 (나은 님) 재현: 2 마감 정책 상세 · 3 저장 목록 뒤 후속 질문 · 5 취업 상태 유지 · 6 지역 질문 중 다른 담당"""

    def test_2_마감된_정책_상세와_더_보기는_마감으로(self):
        add("OLD", vector=near(1), plcyNm="지난 월세 지원", aplyPrdSeCd="0057001", aplyYmd="20261101 ~ 20261130")
        talk = Talk(plan=mock.Mock(return_value=chose("search")))
        talk.lives_in_dongtan()
        talk.send("월세 지원 있어?")  # 11-07 기준 신청 예정
        talk.plan.return_value = chose("detail", 1, ["period"])
        request_day = "2026-12-05"  # 마감 뒤에 같은 대화에서 다시 묻는다
        with mock.patch.object(agent, "_today", return_value=date.fromisoformat(request_day)):
            result = talk.send("1번 아직 신청돼?")
            self.assertEqual(rows(result)[0], "신청: 마감 (2026-11-30)")
            from policies.models import Policy
            Policy.objects.filter(pk="OLD").update(apply_type="closed")  # 데이터 갱신으로 마감 처리된 경우 (KeyError 였다)
            self.assertEqual(rows(talk.send("1번 아직 신청돼?"))[0], "신청: 마감 (2026-11-30)")

    def test_3_저장_목록을_본_뒤의_그거는_저장_목록의_정책(self):
        from django.contrib.auth import get_user_model
        from policies import saved
        from users.models import FinancialProfile
        member = get_user_model().objects.create_user(email="review3@example.com")
        FinancialProfile.objects.create(user=member, birth_date=date(1999, 3, 1))
        add("RENT", vector=near(1), plcyNm="청년 월세 지원")
        add("JOB", vector=near(9), plcyNm="청년 취업 지원")
        saved.save(member, "JOB")
        talk = Talk(user=member, plan=mock.Mock(return_value=chose("search")))
        talk.lives_in_dongtan()
        talk.send("월세 지원 있어?")
        talk.plan.return_value = chose("detail", 1, ["support"])
        self.assertEqual(titles(talk.send("1번 얼마 줘?")), ["청년 월세 지원"])
        self.assertEqual(titles(talk.send(agent.SAVED_LIST)), ["1. 청년 취업 지원"])
        talk.plan.return_value = chose("detail", None, ["support"])
        result = talk.send("그거 얼마 줘?")
        self.assertEqual(json.loads(talk.plan.call_args[0][1])["직전 추천"], ["1. 청년 취업 지원"])
        self.assertEqual(titles(result), ["청년 취업 지원"])  # 하나뿐이라 그 정책 (예전 월세 정책이 아니다)
        self.assertNotIn("1번 저장", talk.labels)  # 이미 저장한 정책

    def test_5_대화에서_말한_취업_준비는_뒤의_질문에도_쓴다(self):
        add("JOBLESS", vector=near(1), plcyNm="미취업 청년 구직 지원", jobCd="0013003")
        talk = Talk(answer=said(needs=(), vague=True))  # PERSON 은 월급 정보가 있다 → 그냥 두면 재직자
        talk.lives_in_dongtan()
        result = talk.send("취업 준비 중인데 뭐 받을 거 없어?")  # 맞춤 추천 분야도 미취업 기본값
        self.assertEqual(result["reply"]["lines"][0], agent.AUTO_LEAD.format(fields="취업 · 일자리, 생활비, 교육 · 직업훈련"))
        self.assertEqual(titles(result), ["1. 미취업 청년 구직 지원"])
        talk.complete.return_value = said()
        self.assertEqual(titles(talk.send("월세 지원도 있어?")), ["1. 미취업 청년 구직 지원"])  # 문장에 없어도 미취업

    def test_7_취업_상태를_고쳐_말하면_바꾼다(self):
        """나은 님 재리뷰 (931579a): '무직' 뒤에 '회사 다녀'로 정정하면 재직자로 다시 판단"""
        add("JOBLESS", vector=near(1), plcyNm="미취업 청년 지원", jobCd="0013003")
        add("WORKER", vector=near(2), plcyNm="재직 청년 지원", jobCd="0013001")
        talk = Talk(user=SimpleNamespace(financial_profile=SimpleNamespace(
            birth_date=date(1999, 3, 1), gender="male", monthly_take_home_income=None)))  # 월급 정보 없음
        talk.lives_in_dongtan()
        self.assertEqual(titles(talk.send("지금 무직인데 지원 있어?")), ["1. 미취업 청년 지원"])
        result = talk.send("아까 잘못 말했어. 지금 회사 다니고 월급 받고 있어. 재직자 지원 찾아줘")
        self.assertEqual(titles(result), ["1. 재직 청년 지원"])
        self.assertEqual(talk.state["job"], "employed")
        talk.store.save_agent_state(CID, "policy", {**{k: v for k, v in talk.state.items() if k != "job"}, "jobless": True})
        self.assertEqual(titles(talk.send("월세 지원 있어?")), ["1. 미취업 청년 지원"])  # 예전 상태(jobless: True)도 읽는다

    def test_6_지역을_묻는_중에도_소비_질문은_다른_담당으로(self):
        talk = Talk()
        talk.send(None, mode="start")
        self.assertEqual(talk.last["reply"]["lines"], [agent.ASK_REGION])
        talk.complete.return_value = said(needs=(), mine=False)
        self.assertEqual(talk.send("이번 달 얼마 썼어?")["status"], "not_mine")
        talk.complete.return_value = said()
        result = talk.send("월세 지원 있어?")  # 정책 문장이면 그대로 지역을 다시 묻는다 (문장은 기억)
        self.assertEqual((result["reply"]["lines"], talk.state["ask"]["text"]), ([agent.ASK_REGION], "월세 지원 있어?"))
