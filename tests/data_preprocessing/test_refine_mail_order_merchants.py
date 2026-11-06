import csv
import gzip
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from scripts.refine_mail_order_merchants import (
    CATEGORIES, INPUT_FIELDS, business_issue, category_candidates, date_issue,
    enrich_domains, file_hash, normalize_name, parse_domains, refine,
)


class RuleTests(unittest.TestCase):
    def test_name_keys_preserve_original_meaning(self):
        self.assertEqual(normalize_name(" ㈜ 가상 상점 "), ("(주) 가상 상점", "가상상점"))
        self.assertEqual(normalize_name("null"), ("", ""))
        self.assertEqual(normalize_name("주상점")[1], "주상점")

    def test_business_format_and_placeholders(self):
        for value in ("123-45-67890", "1234567890"):
            self.assertEqual(business_issue(value), "")
        self.assertEqual(business_issue("000-00-00000"), "사업자번호_반복숫자")
        self.assertEqual(business_issue("123456789"), "사업자번호_형식오류")
        self.assertEqual(business_issue("null"), "사업자번호_결측")

    def test_calendar_boundaries(self):
        day = dt.date(2026, 10, 15)
        self.assertEqual(date_issue("20240229", day), "")
        self.assertEqual(date_issue("20230229", day), "신고일자_유효하지않음")
        self.assertEqual(date_issue("20261016", day), "신고일자_기준일이후")
        self.assertEqual(date_issue("00010101", day), "신고일자_1900년미만")
        self.assertEqual(date_issue("20261015", day), "")

    def test_ambiguous_categories_are_not_single_labels(self):
        self.assertEqual(category_candidates("가전 가구/수납용품")[0], ("생활용품",))
        self.assertEqual(category_candidates("가전")[1], "규칙기반_단일후보")
        self.assertEqual(category_candidates("가전 기타")[1], "검토필요")
        self.assertEqual(category_candidates("가전 종합몰")[1], "검토필요")
        self.assertEqual(category_candidates("가전 미등록")[1], "검토필요")
        self.assertEqual(category_candidates("종합몰 기타")[0], ())
        self.assertEqual(set(category_candidates("의류/패션/잡화/뷰티")[0]), {"패션·쇼핑", "뷰티·미용"})
        self.assertEqual(set(category_candidates("건강/식품")[0]), {"식비", "의료·건강"})
        self.assertEqual(len(CATEGORIES), 10)

    def test_url_parsing_does_not_merge_platform_shops(self):
        urls, hosts, flags = parse_domains("HTTPS://smartstore.naver.com/shop-a?shop=1#top https://smartstore.naver.com/shop-b")
        self.assertEqual(len(urls), 2)
        self.assertEqual(hosts, ["smartstore.naver.com"])
        self.assertIn("https://smartstore.naver.com/shop-a?shop=1", urls)
        self.assertIn("공용플랫폼_호스트단독식별금지", flags)
        self.assertIn("도메인_복수후보", flags)
        self.assertEqual(parse_domains("https://naver.com.example.org/shop")[2], [])

    def test_invalid_or_credential_urls(self):
        for value in ("name@example.com", "http://user:secret@example.com", "javascript:alert(1)", "http://example.com:bad", "http://-bad.example.com", "https://example.com\\other", "�.com"):
            self.assertEqual(parse_domains(value)[0], [])
        self.assertEqual(parse_domains("Example.COM/shop")[0], ["//example.com/shop"])

    def test_fallback_is_explicit_and_reviewable(self):
        urls, _, evidence, flags = enrich_domains("null", "기타 https://example.com/shop")
        self.assertEqual(urls, ["https://example.com/shop"])
        self.assertEqual(evidence, "취급품목내_URL_검토필요")
        self.assertIn("품목내_URL후보추출", flags)
        self.assertEqual(enrich_domains("null", "네이버 입점")[0], [])


class PipelineTests(unittest.TestCase):
    def test_partitions_review_traceability_and_preservation(self):
        with tempfile.TemporaryDirectory(prefix="merchant-test-", dir=Path(__file__).parent) as folder:
            root = Path(folder)
            src = root / "raw/mail_order_merchants"
            data = root / "data/mail_order_structured"
            src.mkdir(parents=True)
            data.mkdir(parents=True)
            source = src / "synthetic.csv"
            source.write_text("synthetic source only\n", encoding="utf-8")
            records = []
            for n in range(1, 9):
                row = [f"2020-test-{n}", "테스트기관", f"가상상점{n}", "123-45-67890", "법인", "", "", "", "20200101", "", "", "정상영업", "", "인터넷", "가전", "example.com/shop", "null", "기본구조확인", source.name, str(n + 1), str(n + 1)]
                records.append(row)
            records[1][:17] = records[0][:17]
            records[2][11] = "폐업처리"
            records[2][8] = "30000101"
            records[3][14:16] = ["종합몰", "null"]
            records[3][3] = "000-00-00000"
            records[4][14:16] = ["건강/식품 https://example.org/store", "null"]
            records[5][0] = records[0][0]
            records[6][2] = records[0][2]
            records[6][14] = "건강/식품"
            records[7][2] = ""
            records[7][8] = "20230229"
            domestic = data / "domestic_mail_order_structured.csv"
            with domestic.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(INPUT_FIELDS)
                w.writerows(records)
            foreign = data / "foreign_mail_order_structured.csv"
            with foreign.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["법인명(상호)", "사업자번호", "원본파일", "원본시트", "원본행번호", "운영상태"])
                w.writerow(["가상국외상점", "", "synthetic.xls", "Sheet1", "2", "01"])
            manifest = {"source_files": [{"file": source.name, "bytes": source.stat().st_size, "sha256": file_hash(source)}], "files": [{"file": p.name, "bytes": p.stat().st_size, "sha256": file_hash(p)} for p in (domestic, foreign)], "counts": {"domestic_review_blocks": 0}}
            (data / "structure_results.json").write_text(json.dumps(manifest), encoding="utf-8")
            out = root / "result"
            report = refine(root, out, dt.date(2026, 10, 15))
            self.assertEqual(report["input_rows"], 8)
            self.assertEqual(report["counts"]["exact_duplicate_rows"], 1)
            self.assertEqual(report["counts"]["active_unique_rows"], 6)
            self.assertEqual(report["counts"]["inactive_unique_rows"], 1)
            self.assertEqual(report["counts"]["foreign_rows"], 1)
            self.assertEqual(report["counts"]["domain_candidates_recovered_from_items"], 1)
            self.assertEqual(report["registration_collision_groups"], 1)
            self.assertTrue(all(report["checks"].values()))
            with gzip.open(out / "mail_order_active_candidates.csv.gz", "rt", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertNotIn("대표자명", reader.fieldnames)
                self.assertNotIn("전화번호", reader.fieldnames)
                self.assertTrue(all(r["학습정답여부"] == "아니오" for r in rows))
                self.assertIn("신고기관번호_비동일행충돌", rows[0]["검토사유"])
            self.assertFalse((out / "처리중.txt").exists())
            with self.assertRaises(FileExistsError):
                refine(root, out, dt.date(2026, 10, 15))
            source.write_text("changed input", encoding="utf-8")
            with self.assertRaises(ValueError):
                refine(root, root / "another", dt.date(2026, 10, 15))
            self.assertFalse((root / "another").exists())


if __name__ == "__main__":
    unittest.main()
