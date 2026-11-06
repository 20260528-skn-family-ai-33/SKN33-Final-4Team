"""교통 규칙 비교 회귀 테스트. 실제 모델·데이터·네트워크를 사용하지 않는다."""
import tempfile
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from scripts.spending_classifier.transport_rules import transport_rule
from scripts.spending_classifier.compare_transport_rules import (
    apply_rules, checked_threshold, measure, read_saved_predictions,
)
from scripts.spending_classifier.evaluate_csv import read_labels


class TransportRuleTests(unittest.TestCase):
    def test_months_and_known_payment_descriptions(self):
        for month in range(1, 13):
            self.assertEqual(transport_rule(f"{month}월 교통카드 이용", "N"), "monthly_transit_charge")
        for name in ("이동의즐거움_택시_9", "카카오페이(택시)", "철도승차권  발매",
                     "티머니 시외버스(모바일결제)", "시외버스 승차권", "전국고속버스운송사업조합"):
            self.assertTrue(transport_rule(name, "N"), name)

    def test_ambiguous_names_and_nonconsumption_are_not_transport_rules(self):
        for name in ("0월교통카드이용", "13월교통카드이용", "티머니", "하나카드_체크",
                     "티머니onda_가승인", "티머니onda_0", "티머니충전", "코레일유통(주)(주안역)",
                     "메가엠지씨커피 강릉시외버스터미", "시외버스 승차권 취소", "카카오페이",
                     "한국철도공사_", "카카오페이(택시) 환불"):
            self.assertEqual(transport_rule(name, "N"), "", name)
        for state in ("Y", "혼재", "확인필요"):
            self.assertEqual(transport_rule("전국고속버스운송사업조합", state), "")

    def test_rule_does_not_use_labels_or_invent_probability(self):
        frame = pd.DataFrame({"가맹점명": ["1월교통카드이용", "카페"], "non_consumption": ["N", "N"],
                              "normalized": ["1월교통카드이용", "카페"], "prediction": ["BEAUTY", "FOOD"],
                              "second_prediction": ["FOOD", "BEAUTY"], "confidence": [0.8, 0.4],
                              "accepted": [True, False], "label": ["FOOD", "FOOD"], "weight": [1, 1]})
        after = apply_rules(frame)
        self.assertEqual(after.loc[0, "prediction"], "TRANSPORT_CAR")
        self.assertTrue(np.isnan(after.loc[0, "confidence"]))
        self.assertEqual(after.loc[0, "ml_confidence"], 0.8)
        self.assertEqual(after.loc[1, "route"], "llm_candidate")
        self.assertEqual(frame.loc[0, "prediction"], "BEAUTY")
        changed = frame.assign(label="TRANSPORT_CAR")
        self.assertEqual(apply_rules(changed)["rule_id"].tolist(), after["rule_id"].tolist())
        metric = measure(after, 0.5, with_rules=True)
        self.assertEqual(metric["decision"]["rule_wrong_rows"], 1)
        self.assertEqual(metric["decision"]["auto_wrong_rows"], 1)
        self.assertEqual(metric["decision"]["llm_candidate_rows"], 1)
        self.assertNotIn("top2_accuracy", metric)
        self.assertIsNone(measure(after.iloc[:0], None)["decision"]["auto_accuracy"])

    def test_threshold_validation(self):
        for value in (float("nan"), float("inf"), -0.1, 1.1, True):
            with self.assertRaises(ValueError):
                checked_threshold(value)
        self.assertIsNone(checked_threshold(None))

    def test_saved_rows_and_fixed_threshold_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            saved_path = Path(directory) / "saved.csv"
            pd.DataFrame({"가맹점명": ["카페"], "등장횟수": [1], "비소비여부": ["N"],
                          "category": ["FOOD"], "categoryName": ["식비"], "확신": ["높음"],
                          "근거": ["테스트"], "검토필요": ["N"]}).to_csv(source, index=False)
            frame = read_labels(source)
            saved = frame.assign(prediction="FOOD", second_prediction="BEAUTY", confidence=0.8, accepted=True)
            saved.to_csv(saved_path, index=False)
            mapping = {key: key for key in ("prediction", "second_prediction", "confidence", "accepted")}
            self.assertTrue(read_saved_predictions(frame, saved_path, mapping, 0.5).loc[0, "accepted"])
            saved["accepted"] = False
            saved.to_csv(saved_path, index=False)
            with self.assertRaises(ValueError):
                read_saved_predictions(frame, saved_path, mapping, 0.5)
            saved["가맹점명"] = "다른이름"
            saved.to_csv(saved_path, index=False)
            with self.assertRaises(ValueError):
                read_saved_predictions(frame, saved_path, mapping, 0.5)


if __name__ == "__main__":
    unittest.main()
