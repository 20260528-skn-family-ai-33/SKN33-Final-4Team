"""상권(상가) 정보 → 가게 이름 사전 (브랜드 · 업종 · 소비 카테고리). 한 번 돌려 만드는 가공 스크립트, 네트워크 없음

    .venv/bin/python -I scripts/merchant_dict/build.py <stores_all_categories.csv> <출력 .json(저장소 밖)>

- 입력: 정제된 상가정보 CSV (상호명 · 상호명_검색키 · 상권업종소분류명 · 소비카테고리 열). 원본은 크고 이용 조건이 있어 저장소에 넣지 않는다
- 이름 키: 검색키와 같은 규칙 key() — NFKC · 소문자 · 한글/영문/숫자만 ("㈜스타벅스 강남점" → "주스타벅스강남점")
- 브랜드로 남기는 이름: 같은 키의 매장이 MIN_STORES 개 이상이고, 가장 많은 업종이 MIN_SHARE 이상인 것
  (매장이 하나뿐인 개인 가게 · '아지트'처럼 업종이 갈리는 흔한 이름은 업종을 정할 수 없어 뺀다)
- 출력: {"version", "source", "rule", "brands": {키: {"name", "stores", "industry", "category"}}}
  category = 그 업종 매장의 소비카테고리 중 가장 많은 것 (없으면 null — 편의점 · 슈퍼처럼 정책상 미분류)
"""
import collections
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

MIN_STORES = 3
MIN_SHARE = 0.8
ROOT = Path(__file__).resolve().parents[2]


def key(name):
    """가게 이름 비교용 키 (상가정보 상호명_검색키와 같은 규칙)"""
    return re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", name or "").lower())


def build(path):
    stores = collections.Counter()
    names = collections.defaultdict(collections.Counter)
    industries = collections.defaultdict(collections.Counter)
    categories = collections.defaultdict(collections.Counter)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            k = row["상호명_검색키"] or key(row["상호명"])
            if len(k) < 2:
                continue
            stores[k] += 1
            names[k][row["상호명"]] += 1
            industries[k][row["상권업종소분류명"]] += 1
            categories[k][row["소비카테고리"] or None] += 1
    brands = {}
    for k, n in stores.items():
        if n < MIN_STORES:
            continue
        industry, count = industries[k].most_common(1)[0]
        if count / n < MIN_SHARE:
            continue
        brands[k] = {"name": names[k].most_common(1)[0][0], "stores": n, "industry": industry,
                     "category": categories[k].most_common(1)[0][0]}
    return brands, len(stores)


def main(argv):
    source, out = Path(argv[1]), Path(argv[2]).expanduser().resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("출력은 저장소 밖 경로여야 해요 (원본 이용 조건 · 크기)")
    brands, keys = build(source)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"version": 1, "source": source.name, "keys_seen": keys,
                               "rule": {"min_stores": MIN_STORES, "min_share": MIN_SHARE},
                               "brands": dict(sorted(brands.items()))}, ensure_ascii=False), encoding="utf-8")
    print(f"브랜드 {len(brands):,}개 / 이름 키 {keys:,}개 → {out}")


if __name__ == "__main__":
    main(sys.argv)
