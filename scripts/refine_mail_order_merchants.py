"""Non-destructive, streaming refinement of the collected merchant snapshot.

Standard library only. Category results are reviewable candidates, never labels.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import gzip
import ipaddress
import json
import re
import sqlite3
import sys
import unicodedata
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

CATEGORIES = (
    "식비", "주거·통신", "교통·차량", "생활용품", "패션·쇼핑",
    "의료·건강", "뷰티·미용", "교육", "문화·여가", "여행·숙박",
)
FIELDS = (
    "통신판매번호", "신고기관명", "상호", "사업자등록번호", "법인여부",
    "대표자명", "전화번호", "전자우편", "신고일자", "사업장소재지",
    "사업장소재지(도로명)", "업소상태", "신고기관 대표연락처", "판매방식",
    "취급품목", "인터넷도메인", "호스트서버소재지",
)
SOURCE_FIELDS = ("원본파일", "원본시작행", "원본끝행")
INPUT_FIELDS = FIELDS + ("구조처리",) + SOURCE_FIELDS
ACTIVE = {"정상영업", "영업재개"}
KNOWN_STATUS = ACTIVE | {"폐업처리", "휴업처리", "직권취소", "직권말소", "타시군구전입"}
MISSING = {"", "null", "none", "nan", "n/a"}
ITEM_RULES = {
    "가전": ("생활용품",),
    "가구/수납용품": ("생활용품",),
    "컴퓨터/사무용품": ("생활용품",),
    "자동차/자동차용품": ("교통·차량",),
    "의류/패션/잡화/뷰티": ("패션·쇼핑", "뷰티·미용"),
    "건강/식품": ("식비", "의료·건강"),
    "교육/도서/완구/오락": ("교육", "문화·여가"),
    "레저/여행/공연": ("문화·여가", "여행·숙박"),
}
GENERAL_ITEMS = {"종합몰", "기타", "상품권", "성인/성인용품"}
PLATFORM_ROOTS = (
    "naver.com", "coupang.com", "gmarket.co.kr", "auction.co.kr", "11st.co.kr",
    "interpark.com", "instagram.com", "facebook.com", "kakao.com", "tistory.com",
    "wixsite.com", "cafe24.com", "godomall.com", "google.com", "apple.com",
)
BASE = ("레코드ID",) + SOURCE_FIELDS + ("업소상태",)
FEATURE_FIELDS = BASE + (
    "상호_정규화", "상호_검색키", "취급품목_원문", "도메인후보_JSON", "호스트_JSON",
    "도메인근거", "카테고리후보_JSON", "분류상태", "분류근거", "검토사유", "학습정답여부",
)


def missing(value: str) -> bool:
    return value.strip().casefold() in MISSING


def normalize_name(value: str) -> tuple[str, str]:
    if missing(value):
        return "", ""
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    key = re.sub(r"^(?:\(주\)|주식회사)\s*|\s*(?:\(주\)|주식회사)$", "", name)
    key = re.sub(r"[^\w가-힣]", "", key.casefold())
    return name, key


def business_issue(value: str) -> str:
    value = value.strip()
    if missing(value):
        return "사업자번호_결측"
    if not re.fullmatch(r"(?:[0-9]{3}-[0-9]{2}-[0-9]{5}|[0-9]{10})", value):
        return "사업자번호_형식오류"
    if len(set(value.replace("-", ""))) == 1:
        return "사업자번호_반복숫자"
    return ""


@lru_cache(maxsize=50000)
def date_issue(value: str, as_of: dt.date) -> str:
    try:
        if not re.fullmatch(r"[0-9]{8}", value):
            raise ValueError
        date = dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return "신고일자_유효하지않음"
    if date.year < 1900:
        return "신고일자_1900년미만"
    if date > as_of:
        return "신고일자_기준일이후"
    return ""


@lru_cache(maxsize=50000)
def category_candidates(value: str) -> tuple[tuple[str, ...], str, str]:
    if missing(value):
        return (), "미분류", "취급품목_결측"
    tokens = set(value.split())
    categories = {c for token in tokens for c in ITEM_RULES.get(token, ())}
    unknown = tokens - ITEM_RULES.keys() - GENERAL_ITEMS
    vague = tokens & GENERAL_ITEMS
    result = tuple(c for c in CATEGORIES if c in categories)
    reasons = []
    if unknown:
        reasons.append("미등록품목_또는_타필드혼입")
    if vague:
        reasons.append("포괄품목포함")
    if len(result) > 1:
        reasons.append("복수카테고리")
    if not result:
        status = "미분류"
    elif len(result) == 1 and not unknown and not vague:
        status = "규칙기반_단일후보"
    else:
        status = "검토필요"
    return result, status, ";".join(reasons) or "취급품목_사전대응"


def parse_domains(value: str) -> tuple[list[str], list[str], list[str]]:
    if missing(value):
        return [], [], ["도메인_결측"]
    urls, hosts, reasons = set(), set(), set()
    text = unicodedata.normalize("NFKC", value).strip()
    for token in re.split(r"[\s,;|]+", text):
        token = token.strip("\"'()[]<>")
        if not token:
            continue
        try:
            if "\ufffd" in token or "\\" in token:
                raise ValueError
            parsed = urlsplit(token if "://" in token or token.startswith("//") else "//" + token)
            if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
                raise ValueError
            if parsed.username is not None or parsed.password is not None:
                raise ValueError
            host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
            if len(host) > 253 or "." not in host:
                raise ValueError
            if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError
            if host.rsplit(".", 1)[-1].isdigit():
                ipaddress.ip_address(host)
                reasons.add("도메인_IP주소")
            port = parsed.port
            netloc = host + (f":{port}" if port is not None else "")
            # Query parameters may identify the seller. Keep them; discard only fragments.
            url = urlunsplit((parsed.scheme.lower(), netloc, parsed.path, parsed.query, ""))
            urls.add(url)
            hosts.add(host)
            if any(host == root or host.endswith("." + root) for root in PLATFORM_ROOTS):
                reasons.add("공용플랫폼_호스트단독식별금지")
        except (ValueError, UnicodeError):
            reasons.add("도메인_미해석문자열")
    if len(urls) > 1:
        reasons.add("도메인_복수후보")
    if not urls:
        reasons.add("도메인_유효후보없음")
    return sorted(urls), sorted(hosts), sorted(reasons)


def enrich_domains(domain: str, items: str) -> tuple[list[str], list[str], str, list[str]]:
    urls, hosts, reasons = parse_domains(domain)
    if urls:
        return urls, hosts, "인터넷도메인", reasons
    # Only explicit URL strings in the wrong field can supply a traceable fallback.
    matches = re.findall(r"(?:https?://|www\.)[^\s,;|]+", items, flags=re.I)
    if matches:
        recovered, recovered_hosts, errors = parse_domains(" ".join(matches))
        if recovered:
            return recovered, recovered_hosts, "취급품목내_URL_검토필요", sorted(set(reasons + errors + ["품목내_URL후보추출"]))
    return urls, hosts, "없음", reasons


def hash_values(values) -> bytes:
    return hashlib.sha256(json.dumps(list(values), ensure_ascii=False, separators=(",", ":")).encode()).digest()


def row_keys(row):
    fp = hash_values(row[:17])
    reg = None if missing(row[0]) else hash_values((row[0].strip(), row[1].strip()))
    name = normalize_name(row[2])[1]
    active = row[11] in ACTIVE
    return fp, reg, hash_values((name,)) if active and name else None, hash_values((row[14],)) if active else None, int(active)


def read_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if tuple(next(reader)) != INPUT_FIELDS:
            raise ValueError("Unexpected domestic input schema")
        for sequence, row in enumerate(reader, 1):
            if len(row) != len(INPUT_FIELDS):
                raise ValueError(f"Invalid CSV column count at record {sequence}")
            yield sequence, row


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def writer(stack: ExitStack, directory: Path, name: str, fields):
    stream = stack.enter_context(gzip.open(directory / (name + ".gz"), "wt", encoding="utf-8-sig", newline="", compresslevel=1))
    result = csv.writer(stream)
    result.writerow(fields)
    return result


def verify_foreign_values(source: Path, structured: Path, wheel: Path, expected_wheel_hash: str):
    """Permit container-only drift only after checking every cell and row locator."""
    if file_hash(wheel) != expected_wheel_hash:
        raise ValueError("XLS reader wheel hash does not match the existing manifest")
    sys.path.insert(0, str(wheel))
    try:
        import xlrd
        book = xlrd.open_workbook(str(source))
        if book.nsheets != 1:
            raise ValueError("Foreign workbook sheet count changed")
        sheet = book.sheet_by_index(0)
        with structured.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            fields = reader.fieldnames[:10]
            if sheet.ncols != 10 or sheet.row_values(0) != fields:
                raise ValueError("Foreign workbook header changed")
            count = 0
            for count, row in enumerate(reader, 1):
                if row["원본시트"] != sheet.name or int(row["원본행번호"]) != count + 1:
                    raise ValueError("Foreign row reference changed")
                for column, field in enumerate(fields):
                    cell = sheet.cell(count, column)
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        value = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).strftime("%Y-%m-%d")
                    elif cell.ctype == xlrd.XL_CELL_NUMBER and cell.value.is_integer():
                        value = str(int(cell.value))
                    else:
                        value = str(cell.value)
                    if value != row[field]:
                        raise ValueError(f"Foreign source value changed at row {count + 1}, column {column + 1}")
            if count != sheet.nrows - 1:
                raise ValueError("Foreign source row count changed")
        return count
    finally:
        sys.path.remove(str(wheel))


def refine(root: Path, output: Path, as_of: dt.date, xls_reader_wheel: Path | None = None):
    source = root / "raw/mail_order_merchants"
    data = root / "data/mail_order_structured"
    if output.exists():
        raise FileExistsError("Output must be a new directory; existing data is never overwritten")
    if output == source or source in output.parents or output == data or data in output.parents:
        raise ValueError("Output cannot be inside an input directory")
    manifest_path = data / "structure_results.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    snapshots = {}
    baseline_differences = []
    for folder, entries in ((source, manifest["source_files"]), (data, manifest["files"])):
        for entry in entries:
            path = folder / entry["file"]
            digest = file_hash(path)
            if digest != entry["sha256"] or path.stat().st_size != entry["bytes"]:
                if path == source / "foreign_mail_order_merchants.xls" and xls_reader_wheel is not None:
                    checked_rows = verify_foreign_values(path, data / "foreign_mail_order_structured.csv", xls_reader_wheel, manifest["foreign_reader"]["wheel_sha256"])
                    baseline_differences.append({"file": str(path.relative_to(root)), "previous_sha256": entry["sha256"], "current_sha256": digest, "all_cell_values_match_structured_rows": checked_rows})
                else:
                    raise ValueError(f"Input differs from the structural manifest: {path.name}")
            snapshots[str(path.relative_to(root))] = digest
    snapshots[str(manifest_path.relative_to(root))] = file_hash(manifest_path)
    domestic = data / "domestic_mail_order_structured.csv"
    output.mkdir(parents=True)
    marker = output / "처리중.txt"
    marker.write_text("완료 보고서가 생성되고 이 파일이 제거될 때까지 사용하지 마세요.", encoding="utf-8")
    print("Input hashes verified; indexing record identities", flush=True)
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("CREATE TABLE records(seq INTEGER PRIMARY KEY,fp BLOB,reg BLOB,name BLOB,item BLOB,active INTEGER)")
    batch = []
    input_count = 0
    for sequence, row in read_rows(domestic):
        batch.append((sequence,) + row_keys(row))
        input_count += 1
        if len(batch) == 20000:
            con.executemany("INSERT INTO records VALUES(?,?,?,?,?,?)", batch)
            batch.clear()
        if sequence % 500000 == 0:
            print(f"Indexed {sequence:,} records", flush=True)
    if batch:
        con.executemany("INSERT INTO records VALUES(?,?,?,?,?,?)", batch)
    con.commit()
    duplicates = {fp: (first, n) for fp, first, n in con.execute("SELECT fp,MIN(seq),COUNT(*) FROM records GROUP BY fp HAVING COUNT(*)>1")}
    reg_collisions = {r[0] for r in con.execute("SELECT reg FROM records WHERE reg IS NOT NULL GROUP BY reg HAVING COUNT(DISTINCT fp)>1")}
    name_collisions = {r[0] for r in con.execute("SELECT name FROM records WHERE name IS NOT NULL GROUP BY name HAVING COUNT(DISTINCT item)>1")}
    con.close()
    print("Identity groups ready; writing partitions", flush=True)
    counts = collections.Counter()
    statuses = collections.Counter()
    reasons_count = collections.Counter()
    classification = collections.Counter()
    category_count = collections.Counter()
    item_counts = collections.Counter()
    with ExitStack() as stack:
        active_writer = writer(stack, output, "mail_order_active_candidates.csv", FEATURE_FIELDS)
        history_writer = writer(stack, output, "mail_order_inactive_history.csv", BASE + ("검토사유",))
        duplicate_writer = writer(stack, output, "mail_order_exact_duplicates.csv", BASE + ("대표레코드ID", "그룹행수"))
        review_writer = writer(stack, output, "mail_order_review.csv", BASE + ("배정파일", "검토사유"))
        for sequence, row in read_rows(domestic):
            recid = f"KR-{sequence:07d}"
            base = [recid, row[18], row[19], row[20], row[11]]
            statuses[row[11]] += 1
            fp, reg, namekey, _, is_active = row_keys(row)
            if fp in duplicates and sequence != duplicates[fp][0]:
                first, size = duplicates[fp]
                duplicate_writer.writerow(base + [f"KR-{first:07d}", size])
                counts["exact_duplicate_rows"] += 1
                continue
            name, searchkey = normalize_name(row[2])
            reasons = [v for v in (business_issue(row[3]), date_issue(row[8], as_of)) if v]
            if not name:
                reasons.append("상호_결측")
            if "\ufffd" in "".join(row[:17]):
                reasons.append("원본_깨진문자")
            if missing(row[0]):
                reasons.append("통신판매번호_결측")
            if reg in reg_collisions:
                reasons.append("신고기관번호_비동일행충돌")
            if namekey in name_collisions:
                reasons.append("동일검색상호_복수취급품목")
            if row[11] not in KNOWN_STATUS:
                reasons.append("영업상태_미확인")
            if is_active:
                urls, hosts, provenance, domain_reasons = enrich_domains(row[15], row[14])
                reasons.extend(domain_reasons)
                candidates, category_status, evidence = category_candidates(row[14])
                if category_status != "규칙기반_단일후보":
                    reasons.append("카테고리_수동검토")
                classification[category_status] += 1
                category_count.update(candidates)
                item_counts[row[14]] += 1
                if provenance == "취급품목내_URL_검토필요":
                    counts["domain_candidates_recovered_from_items"] += 1
                reasons = sorted(set(reasons))
                active_writer.writerow(base + [name, searchkey, row[14], json.dumps(urls, ensure_ascii=False), json.dumps(hosts, ensure_ascii=False), provenance, json.dumps(candidates, ensure_ascii=False), category_status, evidence, ";".join(reasons), "아니오"])
                counts["active_unique_rows"] += 1
                partition = "mail_order_active_candidates.csv.gz"
                if not reasons:
                    counts["active_no_review_flags"] += 1
            else:
                reasons = sorted(set(reasons))
                history_writer.writerow(base + [";".join(reasons)])
                counts["inactive_unique_rows"] += 1
                partition = "mail_order_inactive_history.csv.gz"
            if reasons:
                review_writer.writerow(base + [partition, ";".join(reasons)])
                counts["review_rows"] += 1
                reasons_count.update(reasons)
            if sequence % 500000 == 0:
                print(f"Partitioned {sequence:,} records", flush=True)
        mapping_writer = writer(stack, output, "product_category_mapping.csv", ("취급품목_원문", "활성고유행수", "카테고리후보_JSON", "분류상태", "분류근거"))
        for item, n in item_counts.most_common():
            candidates, state, reason = category_candidates(item)
            mapping_writer.writerow([item, n, json.dumps(candidates, ensure_ascii=False), state, reason])
        foreign_writer = writer(stack, output, "foreign_mail_order_review_candidates.csv", ("레코드ID", "원본파일", "원본시트", "원본행번호", "상호_정규화", "상호_검색키", "운영상태_원문", "카테고리후보_JSON", "검토사유", "학습정답여부"))
        with (data / "foreign_mail_order_structured.csv").open(encoding="utf-8-sig", newline="") as stream:
            for n, row in enumerate(csv.DictReader(stream), 1):
                name, key = normalize_name(row["법인명(상호)"])
                flags = ["국외상태코드미확인", "취급품목없음"]
                if missing(row["사업자번호"]):
                    flags.append("사업자번호_결측")
                foreign_writer.writerow([f"FOREIGN-{n:04d}", row["원본파일"], row["원본시트"], row["원본행번호"], name, key, row["운영상태"], "[]", ";".join(flags), "아니오"])
                counts["foreign_rows"] += 1
    # Output partition IDs are independently checked, not trusted from write counters.
    allocated = bytearray(input_count + 1)
    validated_counts = {}
    for filename, count_key in (("mail_order_active_candidates.csv", "active_unique_rows"), ("mail_order_inactive_history.csv", "inactive_unique_rows"), ("mail_order_exact_duplicates.csv", "exact_duplicate_rows")):
        total = 0
        with gzip.open(output / (filename + ".gz"), "rt", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("Output schema mismatch")
                seq = int(row["레코드ID"].removeprefix("KR-"))
                if not 1 <= seq <= input_count or allocated[seq]:
                    raise ValueError("Duplicate or out-of-range partition ID")
                allocated[seq] = 1
                if "카테고리후보_JSON" in row:
                    if set(json.loads(row["카테고리후보_JSON"])) - set(CATEGORIES):
                        raise ValueError("Unexpected consumption category")
                    if row["학습정답여부"] != "아니오":
                        raise ValueError("Unreviewed data marked as a training label")
                total += 1
        if total != counts[count_key]:
            raise ValueError("Output count mismatch")
        validated_counts[filename] = total
    if allocated[1:].count(0):
        raise ValueError("An input row was lost")
    with ExitStack() as stack:
        partition_readers = {}
        for filename in validated_counts:
            stream = stack.enter_context(gzip.open(output / (filename + ".gz"), "rt", encoding="utf-8-sig", newline=""))
            partition_readers[filename] = iter(csv.DictReader(stream, strict=True))
        for seq, source_row in read_rows(domestic):
            fp = hash_values(source_row[:17])
            if fp in duplicates and seq != duplicates[fp][0]:
                filename = "mail_order_exact_duplicates.csv"
            elif source_row[11] in ACTIVE:
                filename = "mail_order_active_candidates.csv"
            else:
                filename = "mail_order_inactive_history.csv"
            actual = next(partition_readers[filename])
            expected = [f"KR-{seq:07d}", *source_row[18:21], source_row[11]]
            if [actual[k] for k in BASE] != expected:
                raise ValueError("Source provenance or status changed")
            if filename == "mail_order_exact_duplicates.csv" and actual["대표레코드ID"] != f"KR-{duplicates[fp][0]:07d}":
                raise ValueError("Wrong duplicate representative")
            if filename == "mail_order_active_candidates.csv" and actual["취급품목_원문"] != source_row[14]:
                raise ValueError("Original item text changed")
        if any(next(reader, None) is not None for reader in partition_readers.values()):
            raise ValueError("Unexpected extra partition record")
    print("Partitions verified; rechecking original hashes", flush=True)
    for relative, digest in snapshots.items():
        if file_hash(root / relative) != digest:
            raise ValueError("An original or structured input changed during processing")
    report = {
        "as_of": as_of.isoformat(), "issue": "https://github.com/20260528-skn-family-ai-33/SKN33-Final-4Team/issues/5",
        "input_rows": input_count, "counts": dict(counts), "source_status_counts": dict(statuses),
        "classification": dict(classification), "category_candidate_counts_nonexclusive": dict(category_count),
        "review_reasons_nonexclusive": dict(reasons_count), "categories": CATEGORIES,
        "registration_collision_groups": len(reg_collisions), "active_name_item_collision_groups": len(name_collisions),
        "source_sha256": snapshots, "validated_partition_counts": validated_counts,
        "previous_manifest_differences": baseline_differences,
        "existing_structural_review": {"blocks": manifest["counts"]["domestic_review_blocks"], "index": str((data / "structure_review_index.csv").relative_to(root)), "raw": str((data / "structure_review_raw.jsonl").relative_to(root))},
        "checks": {"source_hashes_unchanged": True, "partition_ids_disjoint_and_complete": True, "source_references_and_statuses_verified": True, "category_vocabulary_valid": True, "training_labels_not_asserted": True},
        "limitations": ["카테고리는 검수 전 후보이며 정답 라벨이 아님", "외부 사업자 유효성·도메인 소유·접속·공식 최신 전체건수 미확인", "상호 검색키·공용 플랫폼 호스트는 고유 식별자가 아님", "형식 정상인 사업자번호의 체크섬·진위 미확인", "비활성이력과 중복은 삭제하지 않고 원본 행 색인으로 보존", "기존 구조검토 블록은 재파싱하거나 강제 복구하지 않음"],
        "outputs": {p.name: {"bytes": p.stat().st_size, "sha256": file_hash(p)} for p in output.glob("*.csv.gz")},
    }
    (output / "refinement_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    marker.unlink()
    print(json.dumps({"counts": counts, "classification": classification, "checks": report["checks"]}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "local_workspace")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--xls-reader-wheel", type=Path, help="Optional verified xlrd wheel for checking foreign XLS container drift")
    args = parser.parse_args()
    refine(args.root.resolve(), args.output.resolve(), args.as_of, args.xls_reader_wheel)


if __name__ == "__main__":
    main()
