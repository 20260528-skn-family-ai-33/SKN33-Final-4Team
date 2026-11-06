from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import relabel_store_industries as stores

RAW_FIELDS = [
    "상가업소번호", "상호명", "상호명_검색키", "상권업종소분류코드",
    "정제상태", "검토사유",
] + [f"원본열{i}" for i in range(41)]
FIELDS = RAW_FIELDS + list(stores.LABEL_FIELDS)
SUBSET = RAW_FIELDS[:13] + list(stores.LABEL_FIELDS)
INDEX = {name: i for i, name in enumerate(FIELDS)}


def row(code, category="", training="N", review=False):
    result = [""] * len(FIELDS)
    for key, value in {
        "상가업소번호": code + ("검토" if review else "정상"),
        "상호명": '검증상호, "원문"\n보존',
        "상권업종소분류코드": code,
        "정제상태": "검토필요" if review else "기본검증통과",
        "검토사유": "중복업종" if review else "",
        "소비카테고리": category, "학습대상여부": training,
        "라벨상태": "기존검토대상_제외" if review else ("운영정책_자동라벨" if training == "Y" else "업종판단보류_제외"),
        "라벨근거_제외사유": "원래 근거", "라벨규칙버전": "v3",
    }.items():
        result[INDEX[key]] = value
    return result


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(rows)


class TransformTests(unittest.TestCase):
    def test_only_five_label_columns_change(self):
        for code, old_cat, old_train, expected in [
            ("G20602", "", "N", "식비"), ("Q10208", "뷰티·미용", "Y", "의료·건강"),
        ]:
            before = row(code, old_cat, old_train)
            after = stores.transform(before, INDEX)
            self.assertEqual(after[:47], before[:47])
            self.assertEqual(after[INDEX["소비카테고리"]], expected)
            self.assertEqual(after[INDEX["학습대상여부"]], "Y")
            self.assertEqual(before[INDEX["소비카테고리"]], old_cat)

    def test_existing_reviews_and_other_industries_are_untouched(self):
        for before in [row("G20602", review=True), row("Q10208", review=True),
                       row("S20801", "문화·여가", "Y"), row("G22001")]:
            self.assertEqual(stores.transform(before, INDEX), before)

    def test_already_applied_target_is_preserved(self):
        before = row("G20602", "식비", "Y")
        self.assertEqual(stores.transform(before, INDEX), before)

    def test_unexpected_old_target_and_review_labels_fail(self):
        for before in [row("G20602", "교통·차량", "Y"),
                       row("Q10208", "의료·건강", "Y", review=True)]:
            with self.assertRaises(ValueError):
                stores.transform(before, INDEX)


class PipelineTests(unittest.TestCase):
    def fixture(self, root):
        source, output, clean, decision = root / "v3", root / "v32", root / "clean.csv", root / "decision.xlsx"
        source.mkdir()
        decision.write_bytes(b"decision-fixture")
        records = [
            row("G20602"), row("G20602", review=True), row("Q10208", "뷰티·미용", "Y"),
            row("Q10208", review=True), row("FOOD", "식비", "Y"), row("FASHION", "패션·쇼핑", "Y"),
        ]
        write_csv(clean, RAW_FIELDS, [r[:47] for r in records])
        write_csv(source / stores.ALL, FIELDS, records)
        for name, training in [(stores.TRAIN, "Y"), (stores.EXCLUDED, "N")]:
            write_csv(source / name, SUBSET, [
                [r[INDEX[f]] for f in SUBSET] for r in records if r[INDEX["학습대상여부"]] == training
            ])
        mapping = []
        for code, cat, count, review, eligible in [
            ("G20602", "(보류)", 2, 1, 0), ("Q10208", "뷰티·미용", 2, 1, 1),
            ("FOOD", "식비", 1, 0, 1), ("FASHION", "패션·쇼핑", 1, 0, 1),
        ]:
            mapping.append({
                "상권업종소분류코드": code, "소비카테고리": "" if cat == "(보류)" else cat,
                "결정카테고리": cat, "정제본건수": count, "검토대상건수": review,
                "학습대상예상건수": eligible, "학습처리": "학습 제외" if cat == "(보류)" else "ML 학습",
                "업종판정": "보류" if cat == "(보류)" else "라벨부여",
                "분류근거_보류사유": "기존 근거", "라벨규칙버전": "v3",
            })
        write_csv(source / stores.MAPPING, list(mapping[0]), [list(r.values()) for r in mapping])
        write_csv(source / stores.COUNTS, ["구분", "소비카테고리", "행수", "설명"], [
            ["학습대상", "식비", 1, ""], ["학습대상", "뷰티·미용", 1, ""],
            ["학습대상", "의료·건강", 0, ""], ["학습대상", "패션·쇼핑", 1, ""],
            ["학습제외", "", 2, "기존검토대상_제외"], ["학습제외", "", 1, "업종판단보류_제외"],
            ["전체", "", 6, ""],
        ])
        meta = {
            "source": str(clean), "source_sha256": stores.digest(clean), "rule_version": "v3",
            "counts": {"전체": 6, "학습대상": 3, "학습제외": 3},
            "categories": ["식비", "의료·건강", "뷰티·미용", "패션·쇼핑"],
            "category_counts": {"식비": 1, "뷰티·미용": 1, "패션·쇼핑": 1},
            "label_definition": "테스트용",
            "outputs": {name: {"sha256": stores.digest(source / name)}
                        for name in (stores.ALL, stores.TRAIN, stores.EXCLUDED, stores.MAPPING, stores.COUNTS)},
        }
        (source / stores.META).write_text(json.dumps(meta), encoding="utf-8")
        decisions = {"G20602": ("식비", "ML 학습"), "Q10208": ("의료·건강", "ML 학습"),
                     "FOOD": ("식비", "ML 학습"), "FASHION": ("패션·쇼핑", "ML 학습")}
        return source, output, decision, decisions, records

    def test_full_roundtrip_partitions_mapping_and_no_overwrite(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output, decision, decisions, records = self.fixture(Path(folder))
            before = {p: stores.digest(p) for p in source.iterdir()}
            with patch.object(stores, "INDUSTRY_COUNT", 4), patch.object(stores, "read_decisions", return_value=decisions):
                with redirect_stdout(io.StringIO()):
                    meta = stores.run(source, output, decision)
            self.assertEqual(meta["counts"], {"전체": 6, "학습대상": 4, "학습제외": 2})
            self.assertEqual(meta["changed_rows_by_code"], {"G20602": 1, "Q10208": 1})
            self.assertTrue(all(meta["checks"].values()))
            self.assertEqual(before, {p: stores.digest(p) for p in source.iterdir()})
            with (output / stores.ALL).open(encoding="utf-8-sig", newline="") as stream:
                saved = list(csv.reader(stream))[1:]
            for i in [1, 3, 4, 5]:
                self.assertEqual(saved[i], records[i])
            with self.assertRaises(ValueError):
                stores.run(source, output, decision)

    def test_unrelated_mapping_change_stops_before_outputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output, decision, decisions, _ = self.fixture(Path(folder))
            decisions["FOOD"] = ("패션·쇼핑", "ML 학습")
            with patch.object(stores, "INDUSTRY_COUNT", 4), patch.object(stores, "read_decisions", return_value=decisions):
                with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                    stores.run(source, output, decision)
            self.assertFalse(output.exists())

    def test_changed_input_hash_stops_before_outputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            source, output, decision, _, _ = self.fixture(Path(folder))
            with (source / stores.TRAIN).open("ab") as stream:
                stream.write(b"\n")
            with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                stores.run(source, output, decision)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
