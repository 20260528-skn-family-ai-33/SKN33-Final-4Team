import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from policies import reading, search
from policies.reading import Need, ReadFailed, parse
from policies.regions import REGIONS, place_regions
from policies.tests.test_search import DONGTAN, TODAY, add, axis, numbers


def answer(needs=(), place=None, vague=False):
    return json.dumps({"needs": [{"query": q, "keywords": k} for q, k in needs], "place": place, "vague": vague},
                      ensure_ascii=False)


def names(text):
    return [REGIONS[code]["name"] for code in place_regions(text)]


class PlaceTest(SimpleTestCase):
    def test_줄여_부른_시도와_시군구(self):
        self.assertEqual(names("부산"), ["부산광역시"])
        self.assertEqual(names("광주"), ["전남광주통합특별시"])  # 광주광역시는 표에 따로 없다
        self.assertEqual(names("해운대구"), ["부산광역시 해운대구"])
        self.assertEqual(names("부산 해운대구"), ["부산광역시 해운대구"])
        self.assertEqual(names("수원"), ["경기도 수원시"])  # 아래 구까지 다 내놓지 않는다
        self.assertEqual(names("수원시 영통구"), ["경기도 수원시 영통구"])
        self.assertEqual(names("경기도 광주시"), ["경기도 광주시"])

    def test_여러_곳이면_모두_없으면_빈_목록(self):
        self.assertEqual(len(names("중구")), 5)
        self.assertEqual(names("서울 중구"), ["서울특별시 중구"])
        self.assertEqual(names("없는 동네"), [])
        self.assertEqual(place_regions(""), [])


class ParseTest(SimpleTestCase):
    def test_필요_키워드_지명(self):
        got = parse(answer([("월세 지원", ["월세", " 주거비 "])], place=" 부산 "))
        self.assertEqual(got.needs, [Need("월세 지원", ["월세", "주거비"])])
        self.assertEqual((got.place, got.vague, got.place_codes), ("부산", False, ["26000"]))

    def test_흔한_말_한_글자_겹침은_빼고_개수를_자른다(self):
        got = parse(answer([("a", ["청년", "지원", "집", "월세", "월세", "전세", "보증금", "임대", "중개"])] +
                           [(f"b{i}", []) for i in range(4)]))
        self.assertEqual(got.needs[0].keywords, ["월세", "전세", "보증금", "임대"])
        self.assertEqual(len(got.needs), reading.MAX_NEEDS)

    def test_붙여_만든_말은_뗀다(self):
        got = parse(answer([("a", ["월세지원", "구직활동지원금", "생활지원금", "면접비", "창업사업"])]))
        self.assertEqual(got.needs[0].keywords, ["월세", "구직활동", "면접비", "창업"])  # '생활' 은 너무 흔해 뺀다

    def test_막연한데_필요가_있으면_막연이_아니다(self):
        self.assertFalse(parse(answer([("생활비", [])], vague=True)).vague)
        got = parse(answer(vague=True, place=""))
        self.assertEqual((got.needs, got.vague, got.place), ([], True, None))

    def test_모양이_틀리면_ReadFailed(self):
        for text in ("{", "[]", json.dumps({"needs": []}), json.dumps({"needs": "x", "place": None, "vague": False}),
                     json.dumps({"needs": [], "place": 3, "vague": False})):
            with self.subTest(text=text), self.assertRaises(ReadFailed):
                parse(text)

    def test_빈_문장_필요는_조용히_뺀다(self):
        self.assertEqual(parse(answer([(" ", ["월세"]), ("월세", ["월세"])])).needs, [Need("월세", ["월세"])])


class ReadTest(SimpleTestCase):
    def test_호출기_오류는_종류만_남긴다(self):
        error = Exception("키가 들어 있을지 모르는 메시지")
        error.kind = "rate_limit"
        with self.assertRaisesMessage(ReadFailed, "rate_limit"):
            reading.read("질문", mock.Mock(side_effect=error))

    def test_지시문과_문장을_따로_넘긴다(self):
        complete = mock.Mock(return_value=answer([("월세", [])]))
        reading.read("월세 지원 있어?", complete)
        complete.assert_called_once_with(reading.PROMPT, "월세 지원 있어?")

    def test_가짜_읽기는_문장_그대로(self):
        self.assertEqual(reading.reader(False)[0], "fake")
        got = reading.read("월세 지원 있어?", reading.fake_complete)
        self.assertEqual(got.needs, [Need("월세 지원 있어?", [])])

    def test_막연하면_원래_문장으로_검색(self):
        self.assertEqual(reading.needs_for(parse(answer(vague=True)), "돈 없어?"), [Need("돈 없어?")])

    def test_실제_읽기는_키와_모델이_필요하다(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "", "LLM_MODEL": ""}), self.assertRaises(ReadFailed):
            reading.reader(True)

    def test_실제_읽기는_읽기_스키마로_부른다(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "k", "LLM_MODEL": "m"}):
            name, complete = reading.reader(True, max_calls=2)
        client = complete.__self__
        self.assertEqual((name, client.max_calls), ("m", 2))
        body = client.body(reading.PROMPT, "질문")
        self.assertEqual(body["text"]["format"]["name"], "policy_reading")
        self.assertEqual(body["text"]["format"]["schema"], reading.SCHEMA)


def two_axes(texts):
    """'월세' 문장은 axis 0, '취업' 문장은 axis 2 를 가리킨다"""
    return [axis(2) if "취업" in text else axis(0) for text in texts]


def toward(index, rank):
    vector = axis(index)
    vector[1] = float(rank)
    return vector


class SearchNeedsTest(TestCase):
    def setUp(self):
        for rank in range(1, 5):
            add(f"RENT{rank}", vector=toward(0, rank), plcyNm=f"월세 {rank}")
            add(f"JOB{rank}", vector=toward(2, rank), plcyNm=f"취업 {rank}")

    def needs(self, *needs):
        return search.search_needs(list(needs), DONGTAN, TODAY, embed=two_axes, limit=8)

    def test_필요_하나면_search_와_같다(self):
        need = Need("월세", ["취업"])
        self.assertEqual(numbers(self.needs(need)),
                         numbers(search.search("월세", DONGTAN, TODAY, keywords=["취업"], embed=two_axes, limit=8)))

    def test_두_필요는_번갈아_앞에_온다(self):
        self.assertEqual(numbers(self.needs(Need("월세"), Need("취업")))[:4], ["JOB1", "RENT1", "JOB2", "RENT2"])

    def test_두_필요에서_나오면_가까운_거리와_키워드를_모두_남긴다(self):
        hits = {hit.policy.pk: hit for hit in self.needs(Need("월세", ["월세"]), Need("취업", ["월세 1"]))}
        rent1 = hits["RENT1"]
        self.assertEqual(rent1.keywords, ["월세", "월세 1"])
        self.assertAlmostEqual(rent1.distance, 1 - 2 ** -0.5)  # 월세 필요 쪽 거리 (취업 쪽은 1.0)

    def test_필요를_한_번에_임베딩한다(self):
        embed = mock.Mock(side_effect=two_axes)
        search.search_needs([Need("월세"), Need("취업")], DONGTAN, TODAY, embed=embed)
        embed.assert_called_once_with(["월세", "취업"])


class SearchCommandTest(TestCase):
    def test_read_는_읽은_결과를_보여_주고_필요마다_검색한다(self):
        add("RENT", vector=toward(0, 1), plcyNm="월세 지원")
        complete = mock.Mock(return_value=answer([("월세 지원", ["월세"]), ("취업 지원", [])], place="부산"))
        output = StringIO()
        with mock.patch("policies.reading.reader", return_value=("test", complete)), \
                mock.patch("policies.management.commands.policy_search.embedder", return_value=("test", two_axes)):
            call_command("policy_search", "월세랑 취업", "--region", "동탄구", "--date", TODAY.isoformat(), "--read",
                         stdout=output)
        text = output.getvalue()
        for line in ("읽기 1: 월세 지원 [월세]", "읽기 2: 취업 지원 [키워드 없음]", "문장 속 지명: 부산 → 부산광역시", "| 월세 지원 [월세]"):
            self.assertIn(line, text)
