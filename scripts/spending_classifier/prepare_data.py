"""v5: 허용행 선택 → 정규화 → 원본 빈도 다수결 → 중복 제거 → 고정 분할."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import itertools

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

from scripts.spending_classifier.common import (
    ROOT, CODES, NAME_TO_CODE, NORMALIZATION_VERSION, normalize_name, sha256, write_json, work_path,
)

STORE = ROOT / "data/store_labels/v3.2_20261020/stores_ml_training.csv"
MAIL = ROOT / "data/ml_training_candidates/mail_order_training_candidates_v3_20261020.csv"
BRANDS = ROOT / "data/store_labels/multi_category_brand_candidates.csv"


def assign_splits(names, seed=42):
    if len(set(names)) != len(names):
        raise ValueError("분할 입력은 정규화 이름당 한 행이어야 합니다")
    indices = np.arange(len(names))
    rest, test = next(GroupShuffleSplit(n_splits=1, test_size=0.1, random_state=seed)
                      .split(indices, groups=names))
    train_local, valid_local = next(GroupShuffleSplit(n_splits=1, test_size=1 / 9, random_state=seed)
                                    .split(rest, groups=np.asarray(names)[rest]))
    split = np.full(len(names), "train", dtype=object)
    split[test] = "test"
    split[rest[valid_local]] = "valid"
    assert len(rest[train_local]) + len(rest[valid_local]) + len(test) == len(names)
    return split


def prepare(store, mail, brands, output, ratio=0.8, seed=42):
    if not 0.5 < ratio <= 1:
        raise ValueError("충돌 유지 비율은 0.5 초과 1 이하여야 합니다")
    output = work_path(output)
    output.mkdir(parents=True, exist_ok=False)
    hashes = {str(p.resolve()): sha256(p) for p in (store, mail, brands)}
    write_json(output / "preparation.json", {"status": "running", "input_sha256": hashes})
    source_stats = {}
    # 원본 전체를 pandas에 동시에 올리지 않는다. 현재 PC의 여유 RAM이 작아
    # 이름·라벨별 빈도를 디스크에 집계하고, 최종 고유 이름만 분할한다.
    database = output / "aggregation.sqlite3"
    db = sqlite3.connect(database)
    db.execute("PRAGMA cache_size=-32768")
    db.execute("""CREATE TABLE counts (
        name TEXT, category TEXT, n INTEGER, source_mask INTEGER, original_name TEXT,
        PRIMARY KEY(name, category)) WITHOUT ROWID""")
    insert = """INSERT INTO counts VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(name,category) DO UPDATE SET n=n+1, source_mask=source_mask|excluded.source_mask"""
    for source, path in [("store", store), ("mail", mail)]:
        print(f"Reading {source}", flush=True)
        name_col, label_col, flag = (("상호명", "소비카테고리", "학습대상여부") if source == "store"
                                     else ("상호", "라벨", "학습사용"))
        totals = Counter()
        categories = Counter()
        batch = []
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                totals["input_rows"] += 1
                if row[flag] not in {"Y", "N"}:
                    raise ValueError("알 수 없는 학습 허용값")
                if row[flag] != "Y":
                    totals["excluded_flag_rows"] += 1
                    continue
                code = NAME_TO_CODE[row[label_col]]
                totals["eligible_rows"] += 1
                categories[code] += 1
                name = normalize_name(row[name_col])
                if not name:
                    totals["empty_normalized_rows"] += 1
                    continue
                batch.append((name, code, 1 if source == "store" else 2, row[name_col]))
                if len(batch) >= 10000:
                    db.executemany(insert, batch)
                    db.commit()
                    batch.clear()
        db.executemany(insert, batch)
        db.commit()
        source_stats[source] = dict(totals, category_counts=dict(categories))
    print("Aggregating original label frequencies", flush=True)
    with Path(brands).open(encoding="utf-8-sig", newline="") as stream:
        patterns = [(row["브랜드명"], re.compile(row["검색키_정규식"])) for row in csv.DictReader(stream)]
    columns = ["name", "category", "original_count", "majority_count", "source_mask", "original_name",
               "brand_candidates", "brand_candidate"]
    stats = Counter()
    with (output / "prepared_unsplit.csv").open("w", encoding="utf-8-sig", newline="") as dest, \
         (output / "label_conflicts.csv").open("w", encoding="utf-8-sig", newline="") as conflict_dest:
        writer = csv.DictWriter(dest, fieldnames=columns)
        writer.writeheader()
        conflict_writer = csv.DictWriter(conflict_dest, fieldnames=["name", "counts", "majority_ratio", "kept"])
        conflict_writer.writeheader()
        for name, grouped in itertools.groupby(db.execute("SELECT * FROM counts ORDER BY name,category"), key=lambda r: r[0]):
            rows = list(grouped)
            counts = {r[1]: r[2] for r in rows}
            total = sum(counts.values())
            winner = max(CODES, key=lambda code: counts.get(code, 0))
            majority = counts[winner]
            keep = majority / total >= ratio
            stats["normalized_unique_names"] += 1
            source_mask = 0
            for row in rows:
                source_mask |= row[3]
            stats["both_source_names"] += source_mask == 3
            if len(counts) > 1:
                stats["conflicting_names"] += 1
                stats["kept_conflicting_names"] += keep
                conflict_writer.writerow({"name": name, "counts": json.dumps(counts),
                                          "majority_ratio": majority / total, "kept": keep})
            if not keep:
                stats["excluded_conflicting_names"] += 1
                stats["excluded_conflicting_original_rows"] += total
                continue
            stats["deduplicated_names"] += 1
            matches = "|".join(label for label, pattern in patterns if pattern.search(name))
            writer.writerow(dict(zip(columns, [name, winner, total, majority, source_mask,
                                               rows[0][4], matches, bool(matches)])))
    db.close()
    database.unlink()
    prepared = pd.read_csv(output / "prepared_unsplit.csv", keep_default_na=False)
    # 고유 이름의 정렬 순서 ID는 이름과 1:1이므로 같은 그룹 분할을 만든다.
    prepared["split"] = assign_splits(np.arange(len(prepared)), seed)
    for split in ("train", "valid", "test"):
        if set(prepared.loc[prepared["split"] == split, "category"]) != set(CODES):
            raise ValueError(f"{split}: 일부 카테고리가 없습니다")
    prepared.to_csv(output / "prepared.csv", index=False, encoding="utf-8-sig")
    prepared[["name", "split"]].to_csv(output / "split_ids.csv", index=False, encoding="utf-8-sig")
    (output / "prepared_unsplit.csv").unlink()
    final_hashes = {path: sha256(path) for path in hashes}
    if final_hashes != hashes:
        raise RuntimeError("준비 중 원본 파일이 변경되었습니다")
    report = {"status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
              "normalization_version": NORMALIZATION_VERSION, "majority_ratio": ratio, "seed": seed,
              "input_sha256": hashes, "inputs_unchanged": True, "sources": source_stats, **stats,
              "brand_candidate_names": int(prepared["brand_candidate"].sum()),
              "brand_candidate_policy": "미확정 후보 목록을 실험용 표시로만 사용. 원본/서비스 정책 변경 없음",
              "split_counts": prepared["split"].value_counts().to_dict(),
              "split_categories": {s: dict(Counter(g["category"])) for s, g in prepared.groupby("split")},
              "split_brand_candidates": prepared.groupby("split")["brand_candidate"].sum().to_dict(),
              "prepared_sha256": sha256(output / "prepared.csv"),
              "split_ids_sha256": sha256(output / "split_ids.csv")}
    write_json(output / "preparation.json", report)
    print(json.dumps({k: v for k, v in report.items() if k in stats or k == "split_counts"}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument("--mail", type=Path, default=MAIL)
    parser.add_argument("--brands", type=Path, default=BRANDS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--majority-ratio", type=float, default=0.8)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    prepare(args.store, args.mail, args.brands, args.output, args.majority_ratio, args.seed)


if __name__ == "__main__":
    main()
