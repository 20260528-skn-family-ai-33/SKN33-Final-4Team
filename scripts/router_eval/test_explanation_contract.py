"""평가기가 전체 통계로 부분 질문에 답한 결과를 성공으로 세지 않는지 검사한다."""
import unittest

from explain_eval import score


class SemanticScoreTest(unittest.TestCase):
    case = {"stop": None, "question": "부분 대상의 횟수 가설", "hypothesis": {"axis": "count", "subject": "부분 대상"},
            "plan": {"must": [], "must_not": [], "zero": "allowed"},
            "answer": {"must": [], "any": [], "support": "insufficient"}}

    def test_correct_axis_does_not_compensate_for_wrong_subject(self):
        trace = {"stop": None, "plan": {"queries": []}, "hypothesis": {"axis": "count", "subject": None},
                 "support": {"status": "observation"}, "fallback": None, "check": [],
                 "cited": [{"axis": "count", "kind": "change"}]}
        result = score(self.case, trace, [])
        self.assertFalse(result["semantic"])
        self.assertFalse(result["answer"])

    def test_honest_insufficiency_is_a_successful_answer(self):
        trace = {"stop": None, "plan": {"queries": []}, "hypothesis": self.case["hypothesis"],
                 "support": {"status": "insufficient"}, "fallback": None, "check": [], "cited": []}
        result = score(self.case, trace, [])
        self.assertTrue(result["semantic"])
        self.assertTrue(result["answer"])

    def test_failed_plan_is_not_a_successful_zero_query_plan(self):
        trace = {"stop": None, "plan": None, "fallback": "plan_failed"}
        self.assertFalse(score(self.case, trace, [])["plan"])

    def test_query_rejected_by_server_is_not_a_successful_plan(self):
        query = {"by": "category", "focus": None}
        trace = {"stop": None, "plan": {"queries": [query]}, "dropped": [query]}
        self.assertFalse(score(self.case, trace, [])["plan"])
