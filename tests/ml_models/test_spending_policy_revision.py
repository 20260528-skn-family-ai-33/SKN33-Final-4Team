"""정책 보정 회귀 테스트 소스. 학습/실데이터 평가 없이 합성 자료만 사용한다."""
import csv
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from scripts.spending_classifier.common import NORMALIZATION_VERSION, sha256
from scripts.spending_classifier.correct_policy_data import correct, domain_matches, source_policy


def save_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class PolicyRevisionTests(unittest.TestCase):
    def test_source_identity_and_domain_boundaries(self):
        self.assertTrue(source_policy({"상호명": "벌툰 송도점", "상권업종소분류명": "카페"}, "store"))
        for name, industry in (("리디", "여성 의류 소매업"), ("미스터블루", "미용실"), ("벌툰", "미용실")):
            self.assertEqual(source_policy({"상호명": name, "상권업종소분류명": industry}, "store"), "")
        self.assertTrue(source_policy({"상호": "리디(주)", "인터넷도메인": "https://ridibooks.com"}, "mail"))
        self.assertFalse(domain_matches("ridibooks.com.example.org", "ridibooks.com"))
        self.assertFalse(domain_matches("https://ridibooks.com@other.org", "ridibooks.com"))
        self.assertTrue(domain_matches("https://www.ridibooks.com/books", "ridibooks.com"))

    def test_correction_preserves_splits_and_namesakes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"
            base.mkdir()
            rows = []
            for name, code, split in (("벌툰송도점", "FOOD", "train"), ("리디", "FASHION_SHOPPING", "valid"),
                                      ("교보문고", "EDUCATION", "test"), ("벌툰혼재점", "FOOD", "train")):
                rows.append(dict(name=name, category=code, original_count=2 if name == "벌툰혼재점" else 1,
                                 majority_count=1, source_mask=1, original_name=name,
                                 brand_candidates="", brand_candidate=False, split=split))
            save_csv(base / "prepared.csv", rows)
            save_csv(base / "split_ids.csv", [{"name": r["name"], "split": r["split"]} for r in rows])
            store, mail, labels = root / "store.csv", root / "mail.csv", root / "labels.csv"
            save_csv(store, [{"상호명": n, "상권업종소분류명": i, "학습대상여부": "Y"}
                             for n, i in (("벌툰송도점", "카페"), ("리디", "여성 의류 소매업"),
                                          ("교보문고", "서점"), ("벌툰혼재점", "카페"), ("벌툰혼재점", "미용실"))])
            save_csv(mail, [{"상호": "없음", "인터넷도메인": "", "학습사용": "N"}])
            save_csv(labels, [{"가맹점명": "리디(주)", "비소비여부": "N", "category": "EDUCATION",
                               "categoryName": "교육", "근거": "이전 기준"}])
            prep = {"status": "complete", "normalization_version": NORMALIZATION_VERSION,
                    "input_sha256": {str(p.resolve()): sha256(p) for p in (store, mail)},
                    "prepared_sha256": sha256(base / "prepared.csv"),
                    "split_ids_sha256": sha256(base / "split_ids.csv")}
            (base / "preparation.json").write_text(json.dumps(prep), encoding="utf-8")
            original = {p: sha256(p) for p in (base / "prepared.csv", store, mail, labels)}
            args = SimpleNamespace(base=base, output=root / "out", store=store, mail=mail, evaluation=labels)
            correct(args)
            with (args.output / "prepared.csv").open(encoding="utf-8-sig", newline="") as stream:
                revised = {r["name"]: r for r in csv.DictReader(stream)}
            self.assertEqual(revised["벌툰송도점"]["category"], "CULTURE_LEISURE")
            self.assertEqual(revised["리디"]["category"], "FASHION_SHOPPING")
            self.assertEqual(revised["교보문고"]["category"], "EDUCATION")
            self.assertEqual(revised["벌툰혼재점"]["category"], "FOOD")
            self.assertEqual(sha256(args.output / "split_ids.csv"), prep["split_ids_sha256"])
            self.assertEqual(set(revised), {r["name"] for r in rows})
            for row in rows:
                self.assertEqual(revised[row["name"]]["split"], row["split"])
            self.assertEqual({p: sha256(p) for p in original}, original)
            with (args.output / "evaluation_labels.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["category"], "CULTURE_LEISURE")
            with self.assertRaises(FileExistsError):
                correct(args)


if __name__ == "__main__":
    unittest.main()
