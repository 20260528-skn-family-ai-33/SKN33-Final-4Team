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

    def __init__(self, user=PERSON, answer=None):
        self.store, self.user = MemoryStateStore(), user
        self.complete = mock.Mock(return_value=answer or said())
        self.last = None

    def send(self, text=None, mode="continue"):
        request = {"agent": "policy", "mode": mode, "entry": "button" if text is None else "text", "text": text,
                   "work_ref": None, "input_id": "x", "conversation_id": CID, "today": TODAY, "as_of": None,
                   "context": {}}
        self.last = agent.handle(request, store=self.store, user=self.user, complete=self.complete, embed=question)
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

    def test_시도_시_구_순서로_좁히고_분야를_묻는다(self):
        talk = Talk()
        talk.send(None, mode="start")
        talk.send("경기도")
        self.assertEqual(talk.last["reply"]["lines"], ["경기도의 어디인가요?"])
        self.assertIn("화성시", talk.labels)
        talk.send("화성시")
        self.assertIn("동탄구", talk.labels)
        result = talk.send("동탄구")
        self.assertEqual((result["reply"]["lines"], talk.state["region"]), ([agent.ASK_FIELD], DONGTAN))
        self.assertEqual(talk.labels, list(agent.FIELDS))

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
        self.assertEqual(result["reply"]["lines"], ["경기도 화성시 동탄구 기준으로 찾은 청년 정책이에요."])
        self.assertEqual(titles(result), ["1. 청년 월세 지원"])
        self.assertEqual(rows(result), ["신청: 상시", "지원: 월 20만 원", "확인할 것: 소득(중위소득 150% 이하)",
                                        "관련: 월세", "링크: https://example.org/rent"])
        self.assertEqual(result["reply"]["notices"][0], agent.NOTICE)
        self.assertEqual(talk.state["last"], ["RENT"])
        self.assertEqual(talk.labels, ["1번 저장", agent.OTHER_FIELD, agent.CHANGE_REGION])

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
    def test_막연하면_분야를_묻고_고른_분야로_찾는다(self):
        add("RENT", vector=near(1), plcyNm="청년 월세 지원")
        talk = Talk(answer=said(needs=(), vague=True))
        talk.lives_in_dongtan()
        result = talk.send("받을 수 있는 돈 없어?")
        self.assertEqual((result["status"], result["reply"]["lines"]), ("awaiting_input", [agent.ASK_FIELD]))
        result = talk.send("월세 · 주거")
        self.assertEqual(result["status"], "replied")
        self.assertIn("관련: 월세", rows(result))

    def test_다른_지역을_말하면_확인하고_그_지역은_이번만_쓴다(self):
        add("BUSAN", vector=near(1), plcyNm="해운대 월세", zipCd="26350")
        talk = Talk(answer=said(place="부산"))
        talk.lives_in_dongtan()
        result = talk.send("부산으로 이사 가는데 월세 지원 있어?")
        self.assertIn("부산광역시", result["reply"]["lines"][0])
        self.assertEqual(talk.labels, ["부산 기준으로 보기", "사는 곳(동탄구) 기준으로 보기"])
        talk.send("부산 기준으로 보기")
        self.assertEqual(talk.last["reply"]["lines"], ["부산광역시의 어디인가요?"])
        result = talk.send("해운대구")
        self.assertEqual(titles(result), ["1. 해운대 월세"])
        self.assertEqual(talk.state["region"], DONGTAN)  # 사는 곳은 그대로
        talk.complete.assert_called_once()  # 읽은 결과를 남겨 두고 다시 읽지 않는다

    def test_사는_곳_기준을_고르면_사는_곳으로_찾는다(self):
        add("HOME", vector=near(1), plcyNm="동탄 월세")
        talk = Talk(answer=said(place="부산"))
        talk.lives_in_dongtan()
        talk.send("부산 월세 지원 있어?")
        result = talk.send("사는 곳(동탄구) 기준으로 보기")
        self.assertEqual(titles(result), ["1. 동탄 월세"])

    def test_사는_곳을_품는_지명은_묻지_않는다(self):
        add("HOME", vector=near(1))
        talk = Talk(answer=said(place="경기도"))
        talk.lives_in_dongtan()
        self.assertEqual(talk.send("경기도 월세 지원")["status"], "replied")

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
        talk.send(None)  # 분야 질문 중 기능 버튼 → 사는 곳은 알아서 분야를 묻는다
        self.assertEqual(talk.last["reply"]["lines"], [agent.ASK_FIELD])
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
        self.assertEqual(self.talk.labels, ["1번 저장", "3번 저장", agent.OTHER_FIELD, agent.CHANGE_REGION])
        self.assertEqual(self.talk.state["saved"], ["P2"])
        self.talk.send("1번 저장")
        self.assertEqual(self.talk.labels, ["3번 저장", agent.OTHER_FIELD, agent.CHANGE_REGION])

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
                                       agent.OTHER_FIELD, agent.CHANGE_REGION])

    def test_지역_기억은_save_profile_로_돌려주고_다음_대화는_지역을_묻지_않는다(self):
        from policies import profiles
        from policies.models import PolicyProfile
        talk = self.talk()
        talk.lives_in_dongtan()
        talk.send("월세 지원 있어?")
        result = talk.send("다음에도 동탄구로 찾기")
        self.assertEqual((result["status"], result["save_profile"]), ("replied", {"region_code": DONGTAN}))
        self.assertNotIn("다음에도 동탄구로 찾기", talk.labels)
        self.assertFalse(PolicyProfile.objects.exists())  # 핸들러는 쓰지 않는다 — 엔진이 저장
        profiles.remember(self.user, DONGTAN)  # 엔진이 한 것처럼
        fresh = self.talk()
        result = fresh.send(None, mode="start")
        self.assertEqual(result["reply"]["lines"], [agent.ASK_FIELD])  # 지역을 묻지 않는다
        result = fresh.send("월세 지원 있어?")
        self.assertIn(agent.REMEMBERED_NOTE.format(name="동탄구"), result["reply"]["lines"])
        self.assertNotIn("다음에도 동탄구로 찾기", fresh.labels)  # 이미 저장한 지역
        fresh.send(agent.CHANGE_REGION)
        self.assertEqual(fresh.last["reply"]["lines"], [agent.ASK_REGION])  # 바꿀 수 있다

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
                         ("replied", [agent.SAVED_HEAD], ["정책 2"]))
        self.assertEqual(rows(result)[0], "신청: 상시")
        self.assertNotIn(agent.SAVED_LIST, talk.labels)

    def test_회원이_아니면_저장_목록을_못_본다(self):
        talk = Talk()
        talk.lives_in_dongtan()
        self.assertEqual(talk.send("저장한 정책")["reply"]["lines"], [agent.SAVED_NEED_LOGIN])
