"""Update only G20602 and Q10208 in the existing store labels, preserving v3."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from datetime import datetime, timezone, timedelta
import hashlib
from itertools import zip_longest
import json
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1] / "local_workspace"
SOURCE = ROOT / "data/store_labels"
DECISION = ROOT / "output/data_research/category_policy/store_category_decisions_20261015.xlsx"
VERSION = "industry_mapping_v3.2_20261020"
INDUSTRY_COUNT = 247
ALL = "stores_all_categories.csv"
TRAIN = "stores_ml_training.csv"
EXCLUDED = "stores_excluded.csv"
MAPPING = "industry_category_mapping.csv"
COUNTS = "category_counts.csv"
META = "labeling_results.json"
TARGETS = {"G20602": "식비", "Q10208": "의료·건강"}
LABEL_FIELDS = ("소비카테고리", "학습대상여부", "라벨상태", "라벨근거_제외사유", "라벨규칙버전")
REASONS = {
    "G20602": "확정 서비스 정책: 주류 소매업=식비 (2026-10-15 결정, v3.2 반영)",
    "Q10208": "확정 서비스 정책 D12: 성형외과를 포함한 병의원=의료·건강 (2026-10-20)",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def read_decisions(path):
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = iter(book["업종결정표"].values)
        headers = next(rows)
        decisions = {}
        for values in rows:
            if values[0] is None:
                continue
            row = dict(zip(headers, values))
            code = row["소분류코드"]
            require(code not in decisions, "결정표 업종코드 중복")
            decisions[code] = (row["결정카테고리"], row["학습처리"])
    finally:
        book.close()
    require(len(decisions) == INDUSTRY_COUNT, "결정표 업종 수 불일치")
    for code, category in TARGETS.items():
        require(decisions.get(code) == (category, "ML 학습"), "결정표의 변경 대상 정책 불일치")
    return decisions


def update_mapping(rows, decisions):
    require(len(rows) == INDUSTRY_COUNT, "기존 매핑 업종 수 불일치")
    require({r["상권업종소분류코드"] for r in rows} == set(decisions), "매핑과 결정표 코드 불일치")
    result = []
    for old in rows:
        row = dict(old)
        code = row["상권업종소분류코드"]
        if code in TARGETS:
            require(row["결정카테고리"] in
                    (("(보류)", "식비") if code == "G20602" else ("뷰티·미용", "의료·건강")),
                    "예상하지 못한 기존 업종 라벨")
            if (row["결정카테고리"], row["학습처리"]) != decisions[code]:
                row.update({
                    "소비카테고리": TARGETS[code], "결정카테고리": TARGETS[code],
                    "학습처리": "ML 학습", "업종판정": "라벨부여",
                    "분류근거_보류사유": REASONS[code], "라벨규칙버전": VERSION,
                    "학습대상예상건수": str(int(row["정제본건수"]) - int(row["검토대상건수"])),
                })
        require((row["결정카테고리"], row["학습처리"]) == decisions[code],
                f"대상 외 업종 또는 미지원 변경: {code}")
        result.append(row)
    return result


def transform(row, index):
    require(len(row) == len(index), "전체 CSV 열 수 불일치")
    code = row[index["상권업종소분류코드"]]
    review = row[index["정제상태"]] != "기본검증통과" or bool(row[index["검토사유"]])
    if review:
        require(row[index["학습대상여부"]] == "N" and not row[index["소비카테고리"]],
                "검토대상에 기존 학습 라벨 존재")
        return row
    if code not in TARGETS:
        return row
    category = row[index["소비카테고리"]]
    training = row[index["학습대상여부"]]
    if category == TARGETS[code] and training == "Y":
        return row
    expected = ("", "N") if code == "G20602" else ("뷰티·미용", "Y")
    require((category, training) == expected, f"예상하지 못한 기존 행 라벨: {code}")
    result = list(row)
    for field, value in zip(LABEL_FIELDS, (TARGETS[code], "Y", "운영정책_자동라벨", REASONS[code], VERSION)):
        result[index[field]] = value
    return result


def csv_reader(stack, path):
    return csv.reader(stack.enter_context(path.open(encoding="utf-8-sig", newline="")))


def csv_writer(stack, path, fields):
    writer = csv.writer(stack.enter_context(path.open("x", encoding="utf-8-sig", newline="")))
    writer.writerow(fields)
    return writer


def verify_files(source, output, clean, fields, subset_fields, changed_expected):
    """Compare saved rows against original clean/v3 data and both partition files."""
    index = {name: i for i, name in enumerate(fields)}
    projection = [index[name] for name in subset_fields]
    clean_fields = fields[:-len(LABEL_FIELDS)]
    changed, counts = Counter(), Counter()
    with ExitStack() as stack:
        readers = {name: csv_reader(stack, output / name) for name in (ALL, TRAIN, EXCLUDED)}
        old = csv_reader(stack, source / ALL)
        raw = csv_reader(stack, clean)
        require(next(old) == next(readers[ALL]) == fields, "전체 파일 헤더 불일치")
        require(next(raw) == clean_fields, "정제본 원본 열 불일치")
        for name in (TRAIN, EXCLUDED):
            require(next(readers[name]) == subset_fields, "부분집합 파일 헤더 불일치")
        for number, (previous, current, original) in enumerate(zip_longest(old, readers[ALL], raw), 1):
            require(previous is not None and current is not None and original is not None, "전체 행 수 불일치")
            require(len(current) == len(fields), "저장된 전체 파일 열 수 불일치")
            require(previous[:len(clean_fields)] == current[:len(clean_fields)] == original,
                    "원본 값 또는 행 순서 변경")
            code = current[index["상권업종소분류코드"]]
            if previous != current:
                require(code in TARGETS, "245개 유지 업종에서 변경 발견")
                require(previous[index["정제상태"]] == "기본검증통과"
                        and not previous[index["검토사유"]], "검토대상 변경 발견")
                expected = {
                    "소비카테고리": TARGETS[code], "학습대상여부": "Y",
                    "라벨상태": "운영정책_자동라벨", "라벨근거_제외사유": REASONS[code],
                    "라벨규칙버전": VERSION,
                }
                require(all(current[index[k]] == v for k, v in expected.items()), "변경 대상 라벨 불일치")
                changed[code] += 1
            target = TRAIN if current[index["학습대상여부"]] == "Y" else EXCLUDED
            subset_row = next(readers[target], None)
            require(subset_row == [current[i] for i in projection], "학습/제외 부분집합 값·순서 불일치")
            counts[target] += 1
            if number % 500000 == 0:
                print(f"저장 재검증 {number:,}행", flush=True)
        require(next(readers[TRAIN], None) is None and next(readers[EXCLUDED], None) is None,
                "부분집합에 여분의 행 존재")
    require(changed == changed_expected, "저장 전후 변경 건수 불일치")
    return counts


def run(source, output, decision):
    require(not output.exists(), "기존 출력 디렉터리는 덮어쓰지 않습니다")
    old_meta = json.loads((source / META).read_text(encoding="utf-8"))
    clean = Path(old_meta["source"])
    before = {}
    print("입력 해시 및 결정표 확인", flush=True)
    for path, expected in [(clean, old_meta["source_sha256"])] + [
        (source / name, old_meta["outputs"][name]["sha256"])
        for name in (ALL, TRAIN, EXCLUDED, MAPPING, COUNTS)
    ]:
        before[path] = digest(path)
        require(before[path] == expected, f"기존 처리기록과 입력 해시 불일치: {path.name}")
    before[source / META] = digest(source / META)
    before[decision] = digest(decision)
    decisions = read_decisions(decision)
    map_fields, old_mapping = read_csv(source / MAPPING)
    mapping = update_mapping(old_mapping, decisions)
    with (source / ALL).open(encoding="utf-8-sig", newline="") as stream:
        fields = next(csv.reader(stream))
    with (source / TRAIN).open(encoding="utf-8-sig", newline="") as stream:
        subset_fields = next(csv.reader(stream))
    require(len(fields) == 52 and tuple(fields[-5:]) == LABEL_FIELDS, "지원하지 않는 전체 파일 형식")
    require(len(subset_fields) == 18, "지원하지 않는 부분집합 형식")
    index = {name: i for i, name in enumerate(fields)}
    projection = [index[name] for name in subset_fields]
    mapping_by_code = {r["상권업종소분류코드"]: r for r in mapping}
    output.mkdir()
    (output / META).write_text(json.dumps({"status": "처리중", "rule_version": VERSION},
                                       ensure_ascii=False), encoding="utf-8")
    counts, categories, statuses, changed = Counter(), Counter(), Counter(), Counter()
    industry_training, industry_total, industry_review = Counter(), Counter(), Counter()
    print("2개 업종 변경 및 전체·학습·제외 CSV 생성", flush=True)
    with ExitStack() as stack:
        reader = csv_reader(stack, source / ALL)
        next(reader)
        writers = {
            ALL: csv_writer(stack, output / ALL, fields),
            TRAIN: csv_writer(stack, output / TRAIN, subset_fields),
            EXCLUDED: csv_writer(stack, output / EXCLUDED, subset_fields),
        }
        for number, old in enumerate(reader, 1):
            row = transform(old, index)
            code = row[index["상권업종소분류코드"]]
            require(code in mapping_by_code, "미등록 업종코드")
            training, category = row[index["학습대상여부"]], row[index["소비카테고리"]]
            require(training in {"Y", "N"}, "학습대상 상태 오류")
            if training == "Y":
                require(category in old_meta["categories"], "10개 밖의 학습 라벨")
                require(category == mapping_by_code[code]["소비카테고리"], "행 라벨과 매핑 불일치")
                categories[category] += 1
                industry_training[code] += 1
            else:
                require(not category, "학습제외 행에 라벨 잔존")
            industry_total[code] += 1
            if row[index["정제상태"]] != "기본검증통과" or row[index["검토사유"]]:
                industry_review[code] += 1
            if old != row:
                changed[code] += 1
            counts[ALL] += 1
            target = TRAIN if training == "Y" else EXCLUDED
            counts[target] += 1
            statuses[row[index["라벨상태"]]] += 1
            writers[ALL].writerow(row)
            writers[target].writerow([row[i] for i in projection])
            if number % 500000 == 0:
                print(f"생성 {number:,}행", flush=True)
    require(counts[ALL] == old_meta["counts"]["전체"] == counts[TRAIN] + counts[EXCLUDED], "전체 건수 불일치")
    for row in mapping:
        code = row["상권업종소분류코드"]
        require(industry_training[code] == int(row["학습대상예상건수"]), "업종 학습 건수 불일치")
        require(industry_total[code] == int(row["정제본건수"]), "업종 전체 건수 불일치")
        require(industry_review[code] == int(row["검토대상건수"]), "검토대상 건수 불일치")
    expected_changes = Counter()
    for row in old_mapping:
        code = row["상권업종소분류코드"]
        if code in TARGETS and row["결정카테고리"] != TARGETS[code]:
            expected_changes[code] = int(row["정제본건수"]) - int(row["검토대상건수"])
    require(changed == expected_changes, "2개 업종 변경 예상 건수 불일치")
    expected_categories = Counter(old_meta["category_counts"])
    expected_categories["식비"] += changed["G20602"]
    expected_categories["뷰티·미용"] -= changed["Q10208"]
    expected_categories["의료·건강"] += changed["Q10208"]
    require(categories == expected_categories, "카테고리 증감 불일치")
    with (output / MAPPING).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=map_fields)
        writer.writeheader()
        writer.writerows(mapping)
    count_fields, count_rows = read_csv(source / COUNTS)
    for row in count_rows:
        if row["구분"] == "학습대상":
            row["행수"] = str(categories[row["소비카테고리"]])
        elif row["구분"] == "학습제외":
            row["행수"] = str(statuses[row["설명"]])
        else:
            row["행수"] = str(counts[ALL])
    with (output / COUNTS).open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=count_fields)
        writer.writeheader()
        writer.writerows(count_rows)
    verified = verify_files(source, output, clean, fields, subset_fields, changed)
    require(verified[TRAIN] == counts[TRAIN] and verified[EXCLUDED] == counts[EXCLUDED], "부분집합 건수 불일치")
    saved_map_fields, saved_mapping = read_csv(output / MAPPING)
    require(saved_map_fields == map_fields and saved_mapping == mapping, "매핑표 저장 불일치")
    require(read_csv(output / COUNTS) == (count_fields, count_rows), "건수표 저장 불일치")
    print("원본·기존 결과·결정표 해시 재확인", flush=True)
    require(all(digest(path) == value for path, value in before.items()), "작업 중 입력 파일 변경")
    outputs = {}
    for name, nrows, ncols in (
        (ALL, counts[ALL], 52), (TRAIN, counts[TRAIN], 18),
        (EXCLUDED, counts[EXCLUDED], 18), (MAPPING, len(mapping), len(map_fields)),
        (COUNTS, len(count_rows), len(count_fields)),
    ):
        path = output / name
        outputs[name] = {"rows": nrows, "columns": ncols, "bytes": path.stat().st_size, "sha256": digest(path)}
    meta = {
        "status": "검증완료", "created_at": datetime.now(timezone(timedelta(hours=9))).isoformat(),
        "rule_version": VERSION, "previous_rule_version": old_meta["rule_version"],
        "previous_directory": str(source), "source": str(clean), "source_sha256": before[clean],
        "decision_workbook": str(decision), "decision_sheet": "업종결정표", "decision_sha256": before[decision],
        "categories": old_meta["categories"], "industry_codes": len(mapping),
        "counts": {"전체": counts[ALL], "학습대상": counts[TRAIN], "학습제외": counts[EXCLUDED]},
        "category_counts": dict(categories), "label_status_counts": dict(statuses),
        "changed_rows_by_code": dict(changed), "changed_row_count": sum(changed.values()),
        "mapped_industries": sum(r["학습처리"] == "ML 학습" for r in mapping),
        "held_industries": sum(r["결정카테고리"] == "(보류)" for r in mapping),
        "other_industries": sum(r["결정카테고리"] == "기타" for r in mapping),
        "unclassified_industries": sum(r["결정카테고리"] == "미분류" for r in mapping),
        "brand_candidates": {"applied": False, "reference": str(source / "multi_category_brand_candidates.csv")},
        "label_definition": old_meta["label_definition"],
        "rules": ["G20602=식비, Q10208=의료·건강; 변경 행에만 v3.2 규칙 버전 부여",
                  "기존 검토대상 및 그 밖의 245개 업종 모든 열 보존",
                  "기존 v3는 원래 위치에 보존; 새 디렉터리만 작성",
                  "통신판매·가계동향·모델 학습·GitHub 변경 없음"],
        "checks": {
            "source_and_previous_outputs_hashes_unchanged": True, "decision_hash_unchanged": True,
            "original_47_columns_and_order_match_clean_source": True,
            "only_requested_industries_changed": True, "existing_review_rows_unchanged": True,
            "mapping_247_matches_decision": True, "all_rows_accounted_for": True,
            "partition_cells_and_order_verified": True, "category_deltas_verified": True,
            "industry_counts_verified": True, "mapping_and_counts_readback_verified": True,
        },
        "outputs": outputs,
    }
    (output / META).write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: meta[key] for key in ("status", "counts", "changed_rows_by_code", "checks")},
                     ensure_ascii=False, indent=2), flush=True)
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=SOURCE / "v3.2_20261020")
    parser.add_argument("--decision", type=Path, default=DECISION)
    args = parser.parse_args()
    run(args.source, args.output, args.decision)


if __name__ == "__main__":
    main()
