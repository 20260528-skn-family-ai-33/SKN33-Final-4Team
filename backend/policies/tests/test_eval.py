import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from policies import evaluate
from policies.models import Policy
from policies.regions import find_region
from policies.tests.test_search import DONGTAN, TODAY, add, near, question


def item(**changes):
    return {"id": "q01", "kind": "구체", "query": "질문", "region": DONGTAN, "keywords": [], "answers": [["A"]], **changes}


def file(*items, day=TODAY.isoformat()):
    return json.dumps({"date": day, "questions": list(items)})


def problems(text):
    try:
        evaluate.load(text)
    except evaluate.EvalFileError as error:
        return error.problems
    raise AssertionError("EvalFileError 가 나야 해요")


class RegionTest(SimpleTestCase):
    def test_코드나_이름_일부로_찾는다(self):
        self.assertEqual(find_region("41597"), "41597")
        self.assertEqual(find_region("동탄구"), "41597")

    def test_여러_곳이거나_없으면_멈춘다(self):
        for text in ("중구", "없는 동네"):
            with self.assertRaisesMessage(ValueError, "지역을 하나로 정할 수 없어요"):
                find_region(text)


class LoadTest(TestCase):
    def test_올바른_파일(self):
        add("A")
        add("B")
        today, [q] = evaluate.load(file(item(region="동탄구", keywords=[" 면접 ", ""], answers=[["A", "B"]])))
        self.assertEqual((today, q.region, q.keywords, q.answers), (TODAY, DONGTAN, ["면접"], [{"A", "B"}]))

    def test_형식_문제를_한꺼번에_알린다(self):
        add("A")
        found = problems(file(
            item(id="q01", kind="모름", typo=1),
            item(id="q02", region="중구"),
            item(id="q03", region="41000"),
            item(id="q04", answers=["A"]),
            item(id="q05", kind="없음", answers=[["A"]]),
            item(id="q06", query=" "),
        ))
        for text in ("q01: 모르는 칸 ['typo']", "q01: kind 는", "q02: 지역을 하나로 정할 수 없어요",
                     "q03: 시군구까지 고른 지역이 아니에요", "q04: answers 는", "q05: kind 가 없음이면", "q06: query"):
            self.assertTrue(any(p.startswith(text) for p in found), f"{text} 없음: {found}")

    def test_채점할_수_없는_정답_번호(self):
        add("SEOUL", zipCd="11680")
        add("CLOSED", aplyPrdSeCd="0057001", aplyYmd="20260101 ~ 20261106")
        Policy.objects.filter(pk=add("OFF").pk).update(is_active=False)
        found = problems(file(item(answers=[["SEOUL"], ["CLOSED"], ["OFF"], ["NONE"]])))
        self.assertEqual(found, ["q01: 정답 CLOSED → 이 지역 · 기준일 후보가 아님 (지역이 다르거나 마감)",
                                 "q01: 정답 NONE → 없는 번호", "q01: 정답 OFF → 비활성",
                                 "q01: 정답 SEOUL → 이 지역 · 기준일 후보가 아님 (지역이 다르거나 마감)"])

    def test_id_가_겹치면_알린다(self):
        add("A")
        self.assertEqual(problems(file(item(), item())), ["q01: id 가 겹쳐요"])

    def test_파일_전체_문제(self):
        self.assertIn("JSON 형식 오류", problems("{")[0])
        self.assertIn("date 칸", problems(file(item(), day="11/07"))[0])
        self.assertIn("questions 칸", problems(json.dumps({"date": "2026-11-07"}))[0])

    def test_이사_질문은_region_을_최종_검색_지역으로_본다(self):
        add("A")
        _, [q] = evaluate.load(file(item(kind="이사", query="부산으로 이사 가는데", region="동탄구")))
        self.assertEqual((q.kind, q.region), ("이사", DONGTAN))

    def test_정답을_비워_둔_질문은_정답_미정(self):
        _, [draft, nothing] = evaluate.load(file(item(id="q01", answers=[]), item(id="q02", kind="없음", answers=[])))
        self.assertEqual((draft.draft, nothing.draft), (True, False))


class RunTest(TestCase):
    def setUp(self):
        for rank in range(1, 6):
            add(f"P{rank}", vector=near(rank), plcyNm=f"정책 {rank}")
        add("WORD", vector=near(9), plcyNm="면접 정장 대여")

    def run_file(self, *items, embed=question):
        today, questions = evaluate.load(file(*items))
        return evaluate.run(questions, today, embed)

    def test_묶음마다_처음_나온_순위(self):
        [result, _] = self.run_file(item(answers=[["P2"], ["WORD"], ["P4"]]))
        self.assertEqual(result.ranks, [2, 6, 4])
        self.assertEqual(result.first, 2)

    def test_재등록_묶음은_하나만_나와도_찾은_것(self):
        add("P2_NEW", vector=near(30))
        [result, _] = self.run_file(item(answers=[["P2_NEW", "P2"]]))
        self.assertEqual(result.ranks, [2])

    def test_키워드는_두_번째_방식에만_쓴다(self):
        vector_only, with_keywords = self.run_file(item(keywords=["면접"], answers=[["WORD"]]))
        self.assertEqual((vector_only.mode, vector_only.ranks), ("벡터만", [6]))
        self.assertEqual((with_keywords.mode, with_keywords.ranks), ("키워드", [1]))

    def test_후보_밖이면_None(self):
        today, questions = evaluate.load(file(item(answers=[["P5"]])))
        [result, _] = evaluate.run(questions, today, question, limit=3)
        self.assertEqual((result.ranks, result.first), ([None], None))

    def test_질문은_한_번만_임베딩하고_정답_미정은_뺀다(self):
        embed = mock.Mock(side_effect=question)
        results = self.run_file(item(id="q01", query="같은 질문", answers=[["P1"]]),
                                item(id="q02", query="같은 질문", answers=[["P2"]]),
                                item(id="q03", query="미정", answers=[]), embed=embed)
        embed.assert_called_once_with(["같은 질문"])
        self.assertEqual({r.question.id for r in results}, {"q01", "q02"})

    def test_자동은_읽은_키워드로_찾는다(self):
        complete = mock.Mock(return_value=json.dumps(
            {"needs": [{"query": "면접 정장", "keywords": ["면접"]}], "place": None, "vague": False}))
        today, questions = evaluate.load(file(item(answers=[["WORD"]])))
        results = evaluate.run(questions, today, question, complete=complete)
        auto = [r for r in results if r.mode == evaluate.AUTO][0]
        self.assertEqual((auto.ranks, auto.reading.needs[0].keywords, evaluate.modes(results)),
                         ([1], ["면접"], ["벡터만", "키워드", "자동"]))

    def test_읽기가_실패하면_원래_문장으로_찾고_남긴다(self):
        error = Exception()
        error.kind = "network"
        today, questions = evaluate.load(file(item(answers=[["P1"]])))
        results = evaluate.run(questions, today, question, complete=mock.Mock(side_effect=error))
        auto = [r for r in results if r.mode == evaluate.AUTO][0]
        self.assertEqual((auto.ranks, auto.reading, auto.read_error), ([1], None, "network"))

    def test_점수_계산(self):
        results = self.run_file(item(id="q01", answers=[["P1"]]), item(id="q02", answers=[["P4"], ["WORD"]]),
                                item(id="q03", kind="없음", answers=[]))
        s = evaluate.summary(results, "벡터만")
        self.assertEqual(s["count"], 2)  # 정답 없는 질문은 세지 않는다
        self.assertEqual((s["hit3"], s["hit10"], s["found"]), (0.5, 1.0, 1.0))
        self.assertAlmostEqual(s["mrr"], (1 + 1 / 4) / 2)
        self.assertEqual(s["groups"], 1.0)
        nothing = [r for r in results if r.question.id == "q03"][0]
        self.assertAlmostEqual(nothing.top_distance, 1 - 2 ** -0.5)  # 1위 P1 = near(1)


class CommandTest(TestCase):
    def setUp(self):
        add("P1", vector=near(1), plcyNm="면접 정장 대여")
        add("P2", vector=near(2), plcyNm="청년 월세 지원", plcyKywdNm="주거")
        add("P2_NEW", vector=near(3), plcyNm="청년 월세 지원", plcyKywdNm="주거")
        add("BODY", vector=near(4), plcyNm="생활 안정", plcySprtCn="월세 일부를 돕는다")
        add("SEOUL", zipCd="11680", plcyNm="서울 월세")

    def eval_command(self, items, *args):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "questions.json").write_text(file(*items), encoding="utf-8")
        output = StringIO()
        with mock.patch("policies.management.commands.policy_eval.embedder", return_value=("test", question)):
            call_command("policy_eval", "--file", str(folder / "questions.json"), *args, stdout=output)
        return output.getvalue()

    def test_채점_표와_점수(self):
        output = self.eval_command([item(id="q01", answers=[["P2"]]), item(id="q02", kind="없음", answers=[])])
        self.assertIn("q01   구체   2", output)
        self.assertIn("벡터만", output)
        self.assertIn("1위 거리", output)

    def test_auto_는_자동_칸과_읽은_결과를_보여_준다(self):
        complete = mock.Mock(return_value=json.dumps(
            {"needs": [{"query": "월세", "keywords": ["월세"]}], "place": "부산", "vague": False}))
        with mock.patch("policies.reading.reader", return_value=("test", complete)):
            output = self.eval_command([item(id="q01", keywords=["주거"], answers=[["P2"]])], "--auto")
        self.assertIn("자동", output)
        self.assertIn("q01   주거 | 월세 [월세] · 지명 부산 → 부산광역시", output)

    def test_check_는_임베딩을_부르지_않는다(self):
        with mock.patch("policies.evaluate.run") as run:
            output = self.eval_command([item(answers=[["P1"]]), item(id="q02", answers=[])], "--check")
        run.assert_not_called()
        self.assertIn("정답 미정이라 채점에서 빼요: q02", output)
        self.assertIn("문제 없음", output)

    def test_파일_문제는_목록으로_멈춘다(self):
        with self.assertRaisesMessage(CommandError, "q01: 정답 NONE → 없는 번호"):
            self.eval_command([item(answers=[["NONE"]])])

    def test_없는_id_만_고르면_멈춘다(self):
        with self.assertRaisesMessage(CommandError, "없는 id: q09"):
            self.eval_command([item(answers=[["P1"]])], "--only", "q09")

    def test_정답_후보_찾기는_검색_순위와_상관없이_단어로(self):
        output = StringIO()
        call_command("policy_find", "월세", "--region", "동탄구", "--date", TODAY.isoformat(), stdout=output)
        lines = output.getvalue().splitlines()
        self.assertEqual(lines[0], "경기도 화성시 동탄구 · 2026-11-07 · 후보 4건 중 3건")
        self.assertEqual(lines[1:], [
            "P2 상시 · 지역 · 같은 이름 2건 | 청년 월세 지원 [월세:이름]",
            "P2_NEW 상시 · 지역 · 같은 이름 2건 | 청년 월세 지원 [월세:이름]",
            "BODY 상시 · 지역 | 생활 안정 [월세:본문]",
        ])
