import csv
from pathlib import Path
import tempfile
import unittest

from scripts.relabel_mail_order_candidates import (
    FIELDS, classify, file_hash, prepare, relabel_row, run,
)


def candidate(name, items="의류/패션/잡화/뷰티", train="Y", label="패션·쇼핑"):
    row = dict.fromkeys(FIELDS, "")
    row.update({
        "상호": name, "상호_검색키": name, "취급품목": items,
        "라벨": label, "라벨_이전": label, "학습사용": train,
        "권장표본": "Y", "원본파일": "source.csv", "원본시작행": "2",
        "출처": "합성검증",
    })
    return row


def write_source(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class PolicyTests(unittest.TestCase):
    def test_no_default_fashion_for_unknown_name(self):
        self.assertEqual(classify("한빛상사", "의류/패션/잡화/뷰티")[0], "미분류")
        self.assertEqual(classify("한빛의류", "의류/패션/잡화/뷰티")[0], "패션·쇼핑")

    def test_confirmed_policy_examples(self):
        examples = [
            ("한빛안경", "의류/패션/잡화/뷰티", "의료·건강"),
            ("한빛가발", "의류/패션/잡화/뷰티", "뷰티·미용"),
            ("한빛문구", "컴퓨터/사무용품", "교육"),
            ("한빛성형외과", "건강/식품", "의료·건강"),
            ("한빛동물병원", "건강/식품", "의료·건강"),
            ("한빛펫용품", "의류/패션/잡화/뷰티", "기타"),
            ("한빛주류", "건강/식품", "식비"),
            ("한빛골프용품", "의류/패션/잡화/뷰티", "문화·여가"),
            ("한빛자전거", "자동차/자동차용품", "교통·차량"),
            ("한빛골프웨어", "의류/패션/잡화/뷰티", "패션·쇼핑"),
            ("한빛악기수리", "의류/패션/잡화/뷰티", "패션·쇼핑"),
            ("한빛사우나", "레저/여행/공연", "문화·여가"),
            ("한빛꽃집", "레저/여행/공연", "문화·여가"),
        ]
        for name, items, expected in examples:
            with self.subTest(name=name):
                self.assertEqual(classify(name, items)[0], expected)

    def test_multiple_evidence_does_not_use_first_match(self):
        self.assertEqual(classify("한빛안경가발", "의류/패션/잡화/뷰티")[:2], ("미분류", "근거충돌"))
        self.assertEqual(classify("주얼리스킨바디", "의류/패션/잡화/뷰티")[:2], ("미분류", "근거충돌"))

    def test_mismatch_with_registry_is_not_overridden(self):
        self.assertEqual(classify("한빛꽃집", "자동차/자동차용품")[:2], ("미분류", "상호품목불일치"))

    def test_general_merchants_and_platform_sellers_are_distinct(self):
        for name, items in [("쿠팡", "가전"), ("㈜ 다이소 강남점", "가전"),
                            ("한빛문구", "종합몰"), ("한빛상사", "가전 건강/식품")]:
            self.assertEqual(classify(name, items)[0], "미분류")
        row = candidate("한빛의류")
        row["인터넷도메인"] = "https://www.coupang.com/seller/example"
        self.assertEqual(relabel_row(row)["라벨"], "패션·쇼핑")

    def test_ambiguous_words_are_not_industries(self):
        for name, items in [("한빛플라워", "의류/패션/잡화/뷰티"),
                            ("한빛헬스", "건강/식품"), ("한빛갤럭시", "의류/패션/잡화/뷰티")]:
            self.assertEqual(classify(name, items)[0], "미분류")
        self.assertEqual(classify("한빛카펫", "가구/수납용품")[0], "생활용품")
        self.assertEqual(classify("플라워퍼퓸", "의류/패션/잡화/뷰티")[0], "뷰티·미용")

    def test_old_exclusion_stays_excluded_with_empty_training_label(self):
        row = relabel_row(candidate("한빛안경", train="N"))
        self.assertEqual(row["서비스분류"], "의료·건강")
        self.assertEqual((row["학습사용"], row["라벨"], row["권장표본"]), ("N", "", "N"))
        self.assertEqual(row["라벨_v2"], "패션·쇼핑")

    def test_other_and_unclassified_cannot_be_training_targets(self):
        for name in ["한빛전자담배", "한빛상사"]:
            row = relabel_row(candidate(name))
            self.assertEqual((row["학습사용"], row["라벨"], row["권장표본"]), ("N", "", "N"))

    def test_input_state_validation(self):
        with self.assertRaises(ValueError):
            relabel_row(candidate("한빛안경", train=""))


class PipelineTests(unittest.TestCase):
    def test_preservation_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder) / "source.csv", Path(folder) / "output.csv"
            rows = [candidate("㈜ 한빛안경"), candidate("한빛상사"), candidate("한빛가발", train="N")]
            write_source(source, rows)
            before = file_hash(source)
            report = run(source, output)
            self.assertEqual(report["rows"], 3)
            self.assertEqual(report["training"], 1)
            self.assertTrue(report["readback_verified"])
            self.assertEqual(file_hash(source), before)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                saved = list(csv.DictReader(stream))
            self.assertEqual([r["상호"] for r in saved], [r["상호"] for r in rows])
            for target in [source, output]:
                with self.assertRaises(FileExistsError):
                    run(source, target)

    def test_same_name_disagreement_is_excluded(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / "source.csv"
            write_source(source, [candidate("한빛", "가전"), candidate("㈜ 한빛", "자동차/자동차용품")])
            _, rows = prepare(source)
            self.assertTrue(all(r["분류상태"] == "동명상호충돌" and r["학습사용"] == "N" for r in rows))

    def test_dry_run_and_deterministic_sampling(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source = Path(folder) / "source.csv"
            write_source(source, [candidate("한빛안경"), candidate("한빛의류")])
            self.assertEqual(run(source), run(source))
            self.assertEqual(list(Path(folder).iterdir()), [source])

    def test_invalid_schema_fails_without_output(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output = Path(folder) / "source.csv", Path(folder) / "output.csv"
            source.write_text("상호,라벨\n한빛,식비\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                run(source, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
