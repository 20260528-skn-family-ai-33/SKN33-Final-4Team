"""초과 답변에서 예산 조정으로 넘길 카테고리 고르기 (answer.over_target, 11/11). DB · LLM 없음"""
from django.test import SimpleTestCase

from .answer import over_target
from .result import budget_numbers


def one(category, allocated, spent, ok=True):
    """3번(카테고리 남은 예산) 결과 한 건"""
    return {"item": {"type": 3, "category": category}, "ok": ok,
            **({"result": {"type": 3, "confirmed": budget_numbers(allocated, spent)}} if ok else {"error": "DatabaseError"})}


def living(rows, confirmed=(1000000, 900000)):
    """4번(생활비 전체) 결과 한 건 — 카테고리별 줄 포함"""
    return {"item": {"type": 4, "category": None}, "ok": True, "result": {
        "type": 4, "confirmed": budget_numbers(*confirmed),
        "categories": [{"category": c, **budget_numbers(a, s)} for c, a, s in rows]}}


class OverTargetTest(SimpleTestCase):
    def test_single_category_over(self):
        self.assertEqual(over_target([one("FOOD", 300000, 312000)]), {"category": "FOOD", "over_amount": 12000})

    def test_not_over_or_unconfirmed_or_failed_gives_none(self):
        self.assertIsNone(over_target([one("FOOD", 300000, 300000)]))
        self.assertIsNone(over_target([one("FOOD", 300000, 100000)]))
        self.assertIsNone(over_target([{"item": {"type": 3, "category": "FOOD"}, "ok": True,
                                        "result": {"type": 3, "confirmed": None}}]))
        self.assertIsNone(over_target([one("FOOD", 300000, 312000, ok=False)]))
        self.assertIsNone(over_target([]))

    def test_largest_over_wins_across_category_and_living_answers(self):
        results = [one("FOOD", 300000, 312000),
                   living([("FOOD", 300000, 312000), ("BEAUTY", 50000, 90000), ("EDUCATION", 50000, 10000)])]
        self.assertEqual(over_target(results), {"category": "BEAUTY", "over_amount": 40000})

    def test_zero_budget_etc_and_out_of_budget_categories_are_skipped(self):
        results = [living([("ETC", 50000, 90000), ("CULTURE_LEISURE", 0, 30000), ("HOUSING_TELECOM", 100000, 700000),
                           ("TRAVEL_LODGING", 100000, 200000), ("UNCLASSIFIED", 10000, 20000)])]
        self.assertIsNone(over_target(results))
        self.assertEqual(over_target([*results, one("TRANSPORT_CAR", 100000, 101000)]),
                         {"category": "TRANSPORT_CAR", "over_amount": 1000})

    def test_other_answer_types_are_ignored(self):
        spending = {"item": {"type": 1, "category": "FOOD"}, "ok": True,
                    "result": {"type": 1, "confirmed": {"over": 99999, "allocated": 1}, "categories": [
                        {"category": "FOOD", "over": 99999, "allocated": 1}]}}
        self.assertIsNone(over_target([spending]))
