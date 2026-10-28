"""원천 자료를 보존하고 정책 보정본을 만든다. 학습/평가 실행은 하지 않는다."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from scripts.spending_classifier.common import (
    CATEGORIES, NORMALIZATION_VERSION, ROOT, normalize_name, sha256, write_json, work_path,
)

POLICY = "20261026_books_content_beoltoon_v1"
BASE = ROOT / "data/spending_classifier/prepared/baseline_svm"
STORE = ROOT / "data/store_labels/v3.2_20261020/stores_ml_training.csv"
MAIL = ROOT / "data/ml_training_candidates/mail_order_training_candidates_v3_20261020.csv"
TARGET = "CULTURE_LEISURE"
BEOLTOON_INDUSTRIES = {
    "카페", "만화방", "김밥/만두/분식", "기타 오락장", "그 외 기타 간이 음식점",
    "기타 개인/가정용품 대여업", "백반/한정식",
}
PLATFORM_DOMAINS = {
    "리디": "ridibooks.com", "레진": "lezhin.com", "레진코믹스": "lezhin.com",
    "레진엔터테인먼트": "lezhin.com", "봄툰": "bomtoon.com", "미스터블루": "mrblue.com",
}
# 실제 거래에서 확인된 법인 적요. 짧은 정규화 이름으로 매칭하지 않는다.
CONFIRMED_EVALUATION_NAMES = {"리디(주)"}


def candidate(name):
    return any(token in name for token in ("벌툰", "리디", "레진", "봄툰", "미스터블루"))


def domain_matches(value, expected):
    for token in re.split(r"[\s,;|]+", value.strip()):
        try:
            host = urlsplit(token if "://" in token else "https://" + token).hostname
        except ValueError:
            continue
        if host and (host.lower() == expected or host.lower().endswith("." + expected)):
            return True
    return False


def source_policy(row, source):
    if source == "store":
        name = normalize_name(row["상호명"])
        if name.startswith("벌툰") and row["상권업종소분류명"] in BEOLTOON_INDUSTRIES:
            return "벌툰 상호 접두어 + 만화카페 관련 업종 확인"
    else:
        name = normalize_name(row["상호"])
        domain = PLATFORM_DOMAINS.get(name)
        if domain and domain_matches(row["인터넷도메인"], domain):
            return "플랫폼 법인 상호 + 전용 도메인 확인"
    return ""


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return reader.fieldnames, list(reader)


def write_csv(path, fields, rows):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def correct(args):
    base, output = args.base.resolve(), work_path(args.output)
    if output.exists():
        raise FileExistsError(f"기존 경로를 덮어쓰지 않습니다: {output}")
    prep = json.loads((base / "preparation.json").read_text(encoding="utf-8"))
    if prep.get("status") != "complete" or prep.get("normalization_version") != NORMALIZATION_VERSION:
        raise ValueError("완료된 현행 전처리 자료가 필요합니다")
    if prep.get("policy_revision"):
        raise ValueError("중복 보정을 막기 위해 최초 prepared 자료를 지정하세요")
    hashes = {p: sha256(p) for p in (
        base / "prepared.csv", base / "split_ids.csv", args.store, args.mail, args.evaluation,
    )}
    for filename, key in (("prepared.csv", "prepared_sha256"), ("split_ids.csv", "split_ids_sha256")):
        if hashes[base / filename] != prep[key]:
            raise ValueError(f"원본 해시 불일치: {filename}")
    recorded = {str(Path(p).resolve()).casefold(): h for p, h in prep["input_sha256"].items()}
    for path in (args.store, args.mail):
        if recorded.get(str(path.resolve()).casefold()) != hashes[path]:
            raise ValueError(f"원래 전처리에 사용한 원천 CSV와 다릅니다: {path}")

    targets = {}
    with (base / "prepared.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if candidate(row["name"]):
                targets[row["name"]] = row
    evidence = defaultdict(Counter)
    reasons = defaultdict(set)
    industries = defaultdict(set)
    for source, path in (("store", args.store), ("mail", args.mail)):
        print(f"원천 상호·업종 확인: {source}", flush=True)
        field, flag = ("상호명", "학습대상여부") if source == "store" else ("상호", "학습사용")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row[flag] != "Y":
                    continue
                name = normalize_name(row[field])
                if name not in targets:
                    continue
                evidence[name]["total"] += 1
                reason = source_policy(row, source)
                if reason:
                    evidence[name]["confirmed"] += 1
                    reasons[name].add(reason)
                industries[name].add(row.get("상권업종소분류명", row.get("취급품목", "")))

    changes, review = {}, []
    for name, row in targets.items():
        counts = evidence[name]
        # 다수결로 대표 상호만 남은 경우에도 같은 이름의 원천 행 모두를 확인한다.
        confirmed = counts["total"] > 0 and counts["total"] == counts["confirmed"]
        confirmed &= counts["total"] == int(row["original_count"])
        reason = "; ".join(sorted(reasons[name])) if confirmed else "동명 상호/출처 불확실: 원래 라벨 유지"
        if confirmed and row["category"] != TARGET:
            changes[name] = reason
        review.append({"name": name, "original_name": row["original_name"],
                       "split": row["split"], "from_category": row["category"],
                       "to_category": TARGET if confirmed else row["category"],
                       "decision": "change" if name in changes else "keep",
                       "source_rows": counts["total"], "confirmed_rows": counts["confirmed"],
                       "industries": "; ".join(sorted(industries[name])), "reason": reason})

    eval_fields, eval_rows = read_csv(args.evaluation)
    required = {"가맹점명", "비소비여부", "category", "categoryName", "근거"}
    if not required.issubset(eval_fields or []):
        raise ValueError("평가 CSV 필수 열이 없습니다")
    eval_changes = []
    for index, row in enumerate(eval_rows, 2):
        name = normalize_name(row["가맹점명"])
        known_beoltoon = name in targets and evidence[name]["total"] > 0 and (
            evidence[name]["total"] == evidence[name]["confirmed"] == int(targets[name]["original_count"]))
        if row["비소비여부"] != "N" or not (
            row["가맹점명"].strip() in CONFIRMED_EVALUATION_NAMES or known_beoltoon
        ):
            continue
        if row["category"] != TARGET:
            eval_changes.append({"row": index, "from_category": row["category"], "to_category": TARGET})
            row["category"], row["categoryName"] = TARGET, CATEGORIES[TARGET]
            row["근거"] += f"; {POLICY}: 사용자 확정 콘텐츠/만화카페 정책"

    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "preparation.json", {"status": "running", "policy_revision": POLICY})
    split_counts, categories, changed_splits = Counter(), defaultdict(Counter), Counter()
    with (base / "prepared.csv").open(encoding="utf-8-sig", newline="") as src, \
            (output / "prepared.csv").open("x", encoding="utf-8-sig", newline="") as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=[*reader.fieldnames, "source_category", "label_policy"])
        writer.writeheader()
        for row in reader:
            row["source_category"] = row["category"]
            row["label_policy"] = ""
            if row["name"] in changes:
                row["category"], row["label_policy"] = TARGET, POLICY
                changed_splits[row["split"]] += 1
            split_counts[row["split"]] += 1
            categories[row["split"]][row["category"]] += 1
            writer.writerow(row)
    shutil.copyfile(base / "split_ids.csv", output / "split_ids.csv")
    write_csv(output / "policy_review.csv", list(review[0]) if review else ["name", "decision", "reason"], review)
    write_csv(output / "evaluation_labels.csv", eval_fields, eval_rows)
    if any(sha256(path) != digest for path, digest in hashes.items()):
        raise RuntimeError("처리 도중 입력 파일 변경: 미완료 결과를 학습에 사용하지 마세요")
    report = {
        "status": "complete", "normalization_version": NORMALIZATION_VERSION,
        "policy_revision": POLICY, "base_directory": str(base), "base_prepared_sha256": prep["prepared_sha256"],
        "input_sha256": {str(p.resolve()): h for p, h in hashes.items()},
        "inputs_unchanged": True, "split_counts": dict(split_counts),
        "split_categories": {k: dict(v) for k, v in categories.items()},
        "changed_by_split": dict(changed_splits), "changed_names": len(changes),
        "evaluation_changes": eval_changes, "evaluation_rows": len(eval_rows),
        "prepared_sha256": sha256(output / "prepared.csv"),
        "split_ids_sha256": sha256(output / "split_ids.csv"),
        "evaluation_sha256": sha256(output / "evaluation_labels.csv"),
        "policy_review_sha256": sha256(output / "policy_review.csv"),
        "evaluation_scope": "development; previously inspected data, not independent final evaluation",
        "frequency_note": "original_count/majority_count/source_mask는 원래 약지도 집계. 정책 보정 후 빈도가 아님",
        "limitations": "기존 제외 행 복구/새 표본 추가 없음. 불확실한 동명 상호는 유지. 평가 거래는 학습에 추가하지 않음",
    }
    write_json(output / "preparation.json", report)
    print(f"보정 완료: 상호 {len(changes)}개, 분할별 {dict(changed_splits)}, 평가 라벨 {len(eval_changes)}개", flush=True)
    print(f"보정본: {output}\n상호별 적용/유지 근거: policy_review.csv", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--store", type=Path, default=STORE)
    parser.add_argument("--mail", type=Path, default=MAIL)
    parser.add_argument("--evaluation", type=Path, default=ROOT / "data/spending_classifier/evaluation/transaction_labels_v1.csv")
    parser.add_argument("--output", type=Path, required=True)
    correct(parser.parse_args())


if __name__ == "__main__":
    main()
