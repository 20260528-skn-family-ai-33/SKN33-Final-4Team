"""A~D 비교용 고정 데이터 작성. 이 모듈은 학습하지 않는다."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from scripts.spending_classifier.common import CATEGORIES, CODES, NAME_TO_CODE, NORMALIZATION_VERSION, ROOT, normalize_name, sha256, write_json, work_path
from scripts.spending_classifier.evaluate_csv import DEFAULT_PREPARED, read_labels


def signature(name):
    return re.sub(r"\d+", "#", normalize_name(name))


def rank(value, seed):
    return hashlib.sha256(f"{seed}:{value}".encode("utf-8")).hexdigest()


def assign_partition(group, seed):
    value = int(rank(group, seed)[:8], 16) / 2**32
    return "train" if value < 0.7 else "calibration" if value < 0.85 else "validation"


def read_augmentation(path):
    """별도 파일의 확인된 라벨만 허용. 반복 거래 횟수로 행을 복제하지 않는다."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = {"가맹점명", "category", "group_id", "human_verified"}
    if not required.issubset(raw):
        raise ValueError(f"보강 CSV 필수 열: {sorted(required)}")
    for col in ["category", "group_id", "human_verified"]:
        raw[col] = raw[col].str.strip()
    raw["category"] = raw["category"].map(lambda value: NAME_TO_CODE.get(value, value))
    eligible = raw["human_verified"].eq("Y") & raw["category"].isin(CODES)
    if "비소비여부" in raw:
        eligible &= raw["비소비여부"].str.strip().eq("N")
    if "검토필요" in raw:
        eligible &= raw["검토필요"].str.strip().eq("N")
    frame = raw.loc[eligible].copy()
    frame["name"] = frame["가맹점명"].map(normalize_name)
    if frame.empty or frame["name"].eq("").any() or frame["group_id"].eq("").any():
        raise ValueError("보강 CSV에는 유효한 상호·10개 라벨·group_id·human_verified=Y 행이 필요합니다")
    return frame[["name", "category", "group_id"]].assign(source="augmentation"), {
        "input_rows": len(raw), "accepted_rows": len(frame), "excluded_rows": len(raw) - len(frame)}


def group_resolver(*frames):
    # 같은 정규화 이름/숫자 변형과 수동 group_id를 연결한다. 수동 묶음이 자동 묶음을 쪼개지 않는다.
    parents = {}

    def find(key):
        parents.setdefault(key, key)
        if parents[key] != key:
            parents[key] = find(parents[key])
        return parents[key]

    for frame in frames:
        if "group_id" not in frame:
            continue
        for name, manual in frame[["name", "group_id"]].itertuples(index=False, name=None):
            if str(manual).strip():
                left, right = find("name:" + signature(name)), find("manual:" + str(manual).strip())
                # 루트 선택도 입력 행 순서에 의존하지 않게 고정한다.
                parents[max(left, right)] = min(left, right)
    aliases = {key: find(key) for key in list(parents)}
    return lambda name: aliases.get("name:" + signature(name), "name:" + signature(name))


def sample_by_category(frame, cap, seed):
    return (frame.assign(_rank=frame["name"].map(lambda name: rank(name, seed)))
            .sort_values(["_rank", "name"]).groupby("category", sort=True).head(cap)
            .drop(columns="_rank").reset_index(drop=True))


def verify_splits(frames):
    groups = {key: set(frame["group_id"]) for key, frame in frames.items()}
    for i, left in enumerate(groups):
        for right in list(groups)[i + 1:]:
            if groups[left] & groups[right]:
                raise ValueError(f"그룹 누수: {left} / {right}")
    for split in ("train", "calibration", "validation"):
        labels = set(frames[split].loc[frames[split]["source"].eq("base"), "category"])
        if labels != set(CODES):
            raise ValueError(f"{split}의 기존 데이터에 10개 라벨이 모두 필요합니다. 표본 수를 늘리세요")


def prepare(args):
    args.output = work_path(args.output)
    if args.output.exists():
        raise FileExistsError(f"기존 비교 폴더를 덮어쓰지 않습니다: {args.output}")
    source_paths = [args.prepared, args.evaluation] + ([args.augmentation] if args.augmentation else [])
    if args.groups:
        source_paths.append(args.groups)
    inputs = {str(path.resolve()): sha256(path) for path in source_paths}
    external_raw = pd.read_csv(args.evaluation, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    external = read_labels(args.evaluation)
    external["name"] = external["normalized"]
    external["group_id"] = external_raw["group_id"].str.strip() if "group_id" in external_raw else ""
    if args.scope == "final":
        eligible = external["group"].isin(["primary_10_categories", "confirmed_outside_10_categories"])
        if "human_verified" not in external_raw or not external_raw.loc[eligible, "human_verified"].str.strip().eq("Y").all():
            raise ValueError("최종 평가의 채점 대상에는 human_verified=Y가 필요합니다")
        if external.loc[eligible, "group_id"].eq("").any():
            raise ValueError("최종 평가 채점 대상에는 변형/브랜드를 묶는 group_id가 필요합니다")
    augmentation = pd.DataFrame(columns=["name", "category", "group_id", "source"])
    audit = {"input_rows": 0, "accepted_rows": 0, "excluded_rows": 0}
    if args.augmentation:
        augmentation, audit = read_augmentation(args.augmentation)
    manual = pd.DataFrame(columns=["name", "group_id"])
    if args.groups:
        manual = pd.read_csv(args.groups, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if not {"가맹점명", "group_id"}.issubset(manual):
            raise ValueError("--groups CSV 필수 열: 가맹점명,group_id")
        manual["name"] = manual["가맹점명"].map(normalize_name)
    resolver = group_resolver(external, augmentation, manual)
    external["group_id"] = external["name"].map(resolver)
    heldout = set(external.loc[external["name"].ne(""), "group_id"])
    prep_report_path = args.prepared.parent / "preparation.json"
    prep_report = json.loads(prep_report_path.read_text(encoding="utf-8"))
    if prep_report["prepared_sha256"] != inputs[str(args.prepared.resolve())]:
        raise ValueError("prepared.csv 해시가 전처리 기록과 다릅니다")
    if prep_report["normalization_version"] != NORMALIZATION_VERSION:
        raise ValueError("기존 데이터의 정규화 버전이 다릅니다")
    base = pd.read_csv(args.prepared, dtype=str, keep_default_na=False,
                       usecols=["name", "category", "split", "brand_candidate"])
    if not base["category"].isin(CODES).all() or not base["split"].isin(["train", "valid", "test"]).all():
        raise ValueError("기존 데이터의 라벨/분할 표기가 유효하지 않습니다")
    base["group_id"] = base["name"].map(resolver)
    external["old_data_overlap"] = external["group_id"].isin(set(base["group_id"]))
    if args.scope == "final" and external.loc[external["group"].isin(
            ["primary_10_categories", "confirmed_outside_10_categories"]), "old_data_overlap"].any():
        raise ValueError("최종 평가에 기존 학습·검증·테스트와 겹치는 이름/그룹이 있습니다")
    # 이전 검증/테스트는 비교 실험의 학습·보정·튜닝에 재사용하지 않는다.
    original_holdout = set(base.loc[base["split"].ne("train"), "group_id"])
    base = base.loc[base["split"].eq("train") & base["brand_candidate"].str.lower().isin(["false", "0"]),
                    ["name", "category", "group_id"]].assign(source="base")
    augmentation["group_id"] = augmentation["name"].map(resolver)
    combined = pd.concat([base, augmentation], ignore_index=True)
    excluded = combined["group_id"].isin(heldout | original_holdout)
    audit["rows_excluded_for_evaluation_overlap"] = int(excluded.sum())
    combined = combined.loc[~excluded].copy()
    conflicting = set(combined.groupby("name")["category"].nunique().loc[lambda count: count > 1].index)
    audit["conflicting_names_excluded"] = len(conflicting)
    # 동일 이름은 기존 데이터 1행을 우선해 B/D에 복제 가중치가 생기지 않게 한다.
    combined = combined.loc[~combined["name"].isin(conflicting)].drop_duplicates("name", keep="first")
    combined["partition"] = combined["group_id"].map(lambda value: assign_partition(value, args.seed))
    frames = {}
    for split, cap in (("train", args.cap), ("calibration", args.eval_cap), ("validation", args.eval_cap)):
        subset = combined.loc[combined["partition"].eq(split)]
        selected_base = sample_by_category(subset.loc[subset["source"].eq("base")], cap, args.seed)
        frames[split] = pd.concat([selected_base, subset.loc[subset["source"].eq("augmentation")]], ignore_index=True)
    frames["evaluation"] = external
    verify_splits(frames)
    if args.augmentation and not frames["train"]["source"].eq("augmentation").any():
        raise ValueError("중복·충돌 제외와 그룹 분할 후 학습용 보강 행이 없습니다. 보강 표현을 더 확보하세요")
    for path in source_paths:
        if sha256(path) != inputs[str(path.resolve())]:
            raise ValueError("준비 도중 입력 파일이 변경됐습니다")
    args.output.mkdir(parents=True)
    files = {}
    for split, frame in frames.items():
        path = args.output / f"{split}.jsonl"
        frame.to_json(path, orient="records", lines=True, force_ascii=False)
        files[path.name] = sha256(path)
    write_json(args.output / "manifest.json", {
        "scope": args.scope, "seed": args.seed, "categories": CATEGORIES,
        "normalization_version": NORMALIZATION_VERSION, "inputs": inputs, "files": files,
        "base_cap_per_category": args.cap, "eval_cap_per_category": args.eval_cap,
        "augmentation_audit": audit,
        "counts": {key: {"rows": len(value), "categories": value.get("category", pd.Series(dtype=str)).value_counts().to_dict(),
                         "sources": value.get("source", pd.Series(dtype=str)).value_counts().to_dict()}
                   for key, value in frames.items()},
        "grouping": "동일 정규화 이름·숫자 변형 + group_id 연결. 수동 지정하지 않은 브랜드/지점 변형은 완전 차단을 보장하지 않음",
        "scope_note": "개발 평가로 이미 본 데이터는 scope=final로 바꿔도 새 테스트가 되지 않음",
    })
    print(f"비교 데이터 준비 완료: {args.output.resolve()}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, default=DEFAULT_PREPARED)
    parser.add_argument("--evaluation", type=Path, default=ROOT / "data/spending_classifier/evaluation/transaction_labels_v1.csv")
    parser.add_argument("--augmentation", type=Path)
    parser.add_argument("--groups", type=Path, help="추가 브랜드/지점 묶음 CSV: 가맹점명,group_id")
    parser.add_argument("--scope", choices=["development", "final"], default="development")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cap", type=int, default=2000)
    parser.add_argument("--eval-cap", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if min(args.cap, args.eval_cap) < 1:
        parser.error("표본 상한은 1 이상이어야 합니다")
    prepare(args)


if __name__ == "__main__":
    main()
