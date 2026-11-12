from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from policies import search
from policies.llm import DIMENSIONS, EmbedFailed
from policies.models import Policy
from policies.normalize import policy_fields
from policies.tests.test_normalize import row

TODAY = date(2026, 11, 7)
DONGTAN = "41597"  # match: 동탄구 · 화성시 · 경기도
ALWAYS = {"aplyPrdSeCd": "0057002", "aplyYmd": ""}


def axis(index):
    vector = [0.0] * DIMENSIONS
    vector[index] = 1.0
    return vector


def near(rank):
    """질문 벡터(axis 0)에서 rank 가 클수록 먼 벡터"""
    vector = axis(0)
    vector[1] = float(rank)
    return vector


def question(texts):
    return [axis(0) for _ in texts]


def add(number, *, vector=None, **changes):
    fields = policy_fields(row(plcyNo=number, **{"plcyNm": f"정책 {number}", "zipCd": "41590", **ALWAYS, **changes}))
    return Policy.objects.create(**fields, embedding=near(0) if vector is None else vector)


def find(query="질문", region=DONGTAN, **options):
    return search.search(query, region, TODAY, embed=question, **options)


def numbers(hits):
    return [hit.policy.pk for hit in hits]


class CandidateTest(TestCase):
    def test_지역은_match_목록으로_거른다(self):
        add("CITY", zipCd="41590")
        add("PROVINCE", zipCd="41000")
        add("GU", zipCd="41597")
        add("SEOUL", zipCd="11680")
        self.assertEqual(sorted(numbers(find())), ["CITY", "GU", "PROVINCE"])

    def test_시군구까지_고르지_않은_지역과_없는_코드는_받지_않는다(self):
        for code in ("41000", "99999"):
            with self.assertRaises(ValueError):
                find(region=code)

    def test_마감된_정책과_비활성_정책은_뺀다(self):
        add("CLOSED", aplyPrdSeCd="0057003")
        add("PAST", aplyPrdSeCd="0057001", aplyYmd="20260101 ~ 20261106")
        add("LAST_DAY", aplyPrdSeCd="0057001", aplyYmd="20260101 ~ 20261107")
        Policy.objects.filter(pk=add("OFF").pk).update(is_active=False)
        self.assertEqual(numbers(find()), ["LAST_DAY"])

    def test_신청_상태(self):
        add("OPEN", aplyPrdSeCd="0057001", aplyYmd="20261101 ~ 20261130")
        add("ALWAYS")
        add("LATER", aplyPrdSeCd="0057001", aplyYmd="20261201 ~ 20261231")
        add("GAP", aplyPrdSeCd="0057001", aplyYmd="20261001 ~ 20261031\\N20261201 ~ 20261231")
        add("UNKNOWN", aplyPrdSeCd="0057001", aplyYmd="")
        states = {hit.policy.pk: hit.apply_state for hit in find()}
        self.assertEqual(states, {"OPEN": "open", "ALWAYS": "always", "LATER": "upcoming", "GAP": "upcoming", "UNKNOWN": "unknown"})

    def test_사업이_끝난_상시_정책은_빼지_않고_표시한다(self):
        add("ENDED", bizPrdEndYmd="20261106")
        add("TODAY", bizPrdEndYmd="20261107")
        add("NONE", bizPrdEndYmd="")
        add("BROKEN", bizPrdEndYmd="2026")
        ended = {hit.policy.pk: hit.business_ended for hit in find()}
        self.assertEqual(ended, {"ENDED": True, "TODAY": False, "NONE": False, "BROKEN": False})

    def test_전국_정책을_구분한다(self):
        add("WIDE", zipCd=",".join(["41590"] + [str(10000 + i) for i in range(search.NATIONWIDE_CODES)]))
        add("LOCAL")
        self.assertEqual({hit.policy.pk: hit.nationwide for hit in find()}, {"WIDE": True, "LOCAL": False})

    def test_같은_이름의_재등록_정책은_둘_다_남긴다(self):
        add("OLD", plcyNm="같은 이름")
        add("NEW", plcyNm="같은 이름")
        self.assertEqual(sorted(numbers(find())), ["NEW", "OLD"])


class RankTest(TestCase):
    def test_키워드가_없으면_벡터_거리_순이다(self):
        add("FAR", vector=near(3))
        add("NEAR", vector=near(1))
        hits = find()
        self.assertEqual(numbers(hits), ["NEAR", "FAR"])
        self.assertLess(hits[0].distance, hits[1].distance)
        self.assertEqual(hits[0].keywords, [])

    def test_키워드가_벡터_순위를_뒤집는다(self):
        add("NEAR", vector=near(1))
        add("HIT", vector=near(3), plcySprtCn="시험 응시료 지원")
        hits = find(keywords=["응시료"])
        self.assertEqual(numbers(hits), ["HIT", "NEAR"])
        self.assertEqual(hits[0].keywords, ["응시료"])

    def test_이름_키워드칸_일치가_본문_일치보다_키워드_순위가_높다(self):
        add("BODY", plcySprtCn="면접 준비 지원")
        add("WORD", plcyKywdNm="면접")
        add("NAME", plcyNm="면접 수당")
        add("OTHER", plcySprtCn="면접 준비 지원")
        add("MISS")
        ranks = search._keyword_ranks(Policy.objects.all(), "면접")
        self.assertEqual(ranks, {"WORD": 1, "NAME": 1, "BODY": 3, "OTHER": 3})

    def test_키워드를_여럿_찾을수록_앞선다(self):
        add("ONE", vector=near(1), plcySprtCn="면접 지원")
        add("TWO", vector=near(2), plcySprtCn="면접 교통비 지원")
        hits = find(keywords=["면접", "교통비"])
        self.assertEqual(numbers(hits), ["TWO", "ONE"])
        self.assertEqual(hits[0].keywords, ["면접", "교통비"])

    def test_빈_키워드와_겹치는_키워드는_무시한다(self):
        add("HIT", plcySprtCn="면접 지원")
        self.assertEqual(find(keywords=["", " ", "면접", " 면접 "])[0].keywords, ["면접"])

    def test_임베딩_없는_정책은_키워드로만_찾는다(self):
        policy = add("TODO", plcySprtCn="면접 지원")
        Policy.objects.filter(pk=policy.pk).update(embedding=None)
        self.assertEqual(find(), [])
        hits = find(keywords=["면접"])
        self.assertEqual((numbers(hits), hits[0].distance), (["TODO"], None))

    def test_개수를_제한한다(self):
        for index in range(3):
            add(f"P{index}", vector=near(index + 1))
        self.assertEqual(numbers(find(limit=2)), ["P0", "P1"])

    def test_후보가_없으면_빈_목록이다(self):
        self.assertEqual(find(), [])

    def test_임베딩_실패는_추천_없음으로_바꾸지_않는다(self):
        add("P")
        with self.assertRaises(EmbedFailed):
            search.search("질문", DONGTAN, TODAY, embed=mock.Mock(side_effect=EmbedFailed("HTTP 500")))


class CommandTest(TestCase):
    def run_command(self, *args):
        out = StringIO()
        with mock.patch("policies.management.commands.policy_search.embedder", return_value=("fake", question)):
            call_command("policy_search", "질문", *args, "--date", "2026-11-07", stdout=out)
        return out.getvalue()

    def test_지역_이름으로_찾아_결과를_보여_준다(self):
        add("P", bizPrdEndYmd="20261106", plcySprtCn="면접 지원")
        output = self.run_command("--region", "동탄구", "--keyword", "면접")
        self.assertIn("경기도 화성시 동탄구 · 2026-11-07 · 후보 1건", output)
        self.assertIn("상시 · 지역 · 사업 종료 | 정책 P [면접]", output)

    def test_결과가_없다고_알려_준다(self):
        self.assertIn("맞는 정책 없음", self.run_command("--region", DONGTAN))

    def test_지역을_하나로_정할_수_없으면_멈춘다(self):
        with self.assertRaisesMessage(CommandError, "지역을 하나로 정할 수 없어요"):
            self.run_command("--region", "중구")

    def test_시군구까지_고르지_않은_지역이면_멈춘다(self):
        with self.assertRaisesMessage(CommandError, "검색하지 못했어요"):
            self.run_command("--region", "41000")
