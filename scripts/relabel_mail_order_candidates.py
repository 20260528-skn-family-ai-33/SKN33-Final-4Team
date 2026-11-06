"""Apply the 2026-10-20 service policy to the existing v2 mail-order candidates.

No external lookups, model calls, source overwrites or restoration of excluded rows.
Labels remain rule-based training candidates, not manually verified merchant facts.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import unicodedata
from pathlib import Path

CATEGORIES = (
    "식비", "주거·통신", "교통·차량", "생활용품", "패션·쇼핑",
    "의료·건강", "뷰티·미용", "교육", "문화·여가", "여행·숙박",
)
VERSION = "mail_order_policy_v3_20261020"
ROOT = Path(__file__).resolve().parents[1] / "local_workspace"
DEFAULT_SOURCE = ROOT / "data/ml_training_candidates/mail_order_training_candidates_v2_20261015.csv"
DEFAULT_OUTPUT = ROOT / "data/ml_training_candidates/mail_order_training_candidates_v3_20261020.csv"
FIELDS = (
    "출처", "통신판매번호", "상호", "상호_검색키", "취급품목", "라벨", "라벨_이전",
    "라벨근거", "라벨신뢰", "기준조정", "기준조정근거", "학습사용", "권장표본",
    "법인여부", "인터넷도메인", "신고일자", "원본파일", "원본시작행",
)
EDITED = {"라벨", "라벨근거", "라벨신뢰", "기준조정", "기준조정근거", "학습사용", "권장표본"}
EXTRA = ("라벨_v2", "학습사용_v2", "권장표본_v2", "서비스분류", "분류상태", "라벨규칙버전")
DIRECT_ITEMS = {
    "가전": "생활용품", "가구/수납용품": "생활용품",
    "컴퓨터/사무용품": "생활용품", "자동차/자동차용품": "교통·차량",
}
# Compound registry fields are not evidence that every merchant sells all listed items.
# They also cannot supply a default label when no narrower evidence exists.
ALLOWED = {
    **{k: {v} for k, v in DIRECT_ITEMS.items()},
    "컴퓨터/사무용품": {"생활용품", "교육"},
    "의류/패션/잡화/뷰티": {"패션·쇼핑", "뷰티·미용", "의료·건강", "문화·여가", "기타"},
    "건강/식품": {"식비", "의료·건강"},
    "교육/도서/완구/오락": {"교육", "문화·여가"},
    "레저/여행/공연": {"문화·여가", "여행·숙박"},
}
CAPS = {"패션·쇼핑": 30000, "뷰티·미용": 15000, "식비": 15000, "의료·건강": 15000}

# Specific phrases consume their matched text before generic evidence is evaluated:
# 운동복 is apparel, 자전거 is transport, and 악기수리 is the D14 exception.
SPECIAL = (
    ("D14 수리 예외", "패션·쇼핑", r"(?:시계|귀금속|악기)(?:전문)?수리"),
    ("D4 동물병원", "의료·건강", r"동물병원"),
    ("D13 운전학원", "교육", r"(?:자동차)?운전(?:전문)?학원"),
    ("D13 자전거", "교통·차량", r"(?:전기)?자전거|캠핑카"),
    ("운동복", "패션·쇼핑", r"골프웨어|스포츠웨어|운동복|요가복|등산복"),
    ("D8 휴대폰", "생활용품", r"휴대폰|핸드폰"),
)
RULES = (
    ("D10 안경", "의료·건강", r"안경|옵티컬|아이웨어|(?<![a-z])(?:optical|eyewear)(?![a-z])"),
    ("D12 병의원", "의료·건강", r"성형외과|피부과|병원|의원|약국"),
    ("D10 건강보조식품", "의료·건강", r"건강보조식품|건강기능식품|홍삼|비타민|유산균|영양제|프로틴"),
    ("D12 가발", "뷰티·미용", r"가발|(?<![가-힣])위그|(?<![a-z])wig(?![a-z])"),
    ("미용", "뷰티·미용", r"뷰티|코스메틱|화장품|메이크업|네일|헤어|향수|퍼퓸|미용실|피부관리|에스테틱|스킨|더마|cosmetic|beauty|makeup|perfume|(?<![a-z])(?:skin|derma|hair|nail)(?![a-z])"),
    ("D11 문구", "교육", r"문구|문방구|스테이셔너리|stationery"),
    ("D11 도서", "교육", r"도서출판|북스|(?<![가-힣])서점|(?<![a-z])books(?![a-z])"),
    ("D11 학원", "교육", r"독서실|스터디카페|음악학원|미술학원|입시학원|외국어학원"),
    ("D3 운동용품", "문화·여가", r"운동용품|스포츠용품|골프용품|골프채|낚시용품|낚시점|캠핑용품|헬스장|필라테스|요가원"),
    ("D12 목욕·마사지", "문화·여가", r"목욕탕|사우나|마사지|안마"),
    ("D15 꽃·사진", "문화·여가", r"꽃집|꽃가게|생화전문|사진관|사진촬영|기념품"),
    ("문화용품", "문화·여가", r"장난감|완구|악기|노래방|피시방"),
    ("D7 기기", "생활용품", r"가전|컴퓨터|노트북|프린터|사무기기|가구|퍼니처|퍼니쳐|매트리스|소파|침구|커튼|카펫|주방용품"),
    ("자동차", "교통·차량", r"자동차|타이어|렌터카|렌트카|차량정비|세차장"),
    ("D9 주거", "주거·통신", r"부동산중개|고시원|기숙사|인테리어|입주청소|가정청소"),
    ("숙박", "여행·숙박", r"여행사|여행상품|호텔|펜션|모텔|글램핑"),
    ("D14 세탁·수선", "기타", r"세탁소|빨래방|의류수선|옷수선|신발수선|가방수선|원단|섬유제품|실가게"),
    ("D4 반려동물용품", "기타", r"(?:반려동물|애완|애견|강아지|고양이|펫)(?:용품|사료|샵|숍)"),
    ("D5 담배·복권", "기타", r"담배|복권|베이프|(?<![a-z])vape(?![a-z])"),
    ("D6 경조사", "기타", r"예식장|장례식장|화장터|납골당|혼례용품"),
    ("패션", "패션·쇼핑", r"의류|패션|옷가게|여성복|남성복|아동복|유아복|한복|신발|구두|가방|액세서리|악세사리|주얼리|쥬얼리|귀금속|시계|란제리|속옷"),
    ("식품·주류", "식비", r"농장|농원|농산|농업|푸드|정육|축산|수산|한우|김치|반찬|베이커리|제과|커피|미곡|어묵|젓갈|참기름|밀키트|도시락|식당|양봉|청과|주류|양조장|와인|맥주|food|bakery|coffee"),
)
SPECIAL = tuple((key, cat, re.compile(pattern, re.I)) for key, cat, pattern in SPECIAL)
RULES = tuple((key, cat, re.compile(pattern, re.I)) for key, cat, pattern in RULES)
GENERAL_NAME = re.compile(r"^(?:쿠팡|11번가|지마켓|옥션|다이소|이마트|홈플러스|롯데마트|코스트코)(?:[가-힣0-9]+(?:점|지점))?$")
AMBIGUOUS = re.compile(
    r"플라워|flower|골프|스포츠|sport|캠핑|아웃도어|낚시|피트니스|"
    r"폰케이스|갤럭시|아이폰|옵티컬|도서|(?<!카)펫|애견|애완|반려|강아지|고양이|"
    r"(?<![a-z])pet(?![a-z])|바이오|웰니스|헬스|다이어트"
)


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"주식회사|\(주\)", "", value)
    return re.sub(r"[\W_]+", "", value)


def classify(name: str, items: str) -> tuple[str, str, str]:
    """Return service category/status, rule and evidence; never use the old label."""
    name = normalize(name)
    items = " ".join(unicodedata.normalize("NFKC", items).split())
    tokens = set(items.split())
    if "종합몰" in tokens or GENERAL_NAME.fullmatch(name):
        return "미분류", "다품목", "D1·D16 종합몰·다품목 가맹점"
    if len(tokens) > 1:
        return "미분류", "복수품목", "복수 신고품목은 단일 업종으로 확정하지 않음"
    if not name or items not in ALLOWED:
        return "미분류", "근거부족", "상호 또는 지원하는 신고품목 없음"

    hits = []
    remaining = name
    for key, cat, pattern in SPECIAL:
        if pattern.search(remaining):
            hits.append((key, cat))
            remaining = pattern.sub("", remaining)
    for key, cat, pattern in RULES:
        if pattern.search(remaining):
            hits.append((key, cat))
    categories = {cat for _, cat in hits}
    evidence = "; ".join(key for key, _ in hits)
    if len(categories) > 1:
        return "미분류", "근거충돌", evidence
    if categories:
        category = next(iter(categories))
        if category not in ALLOWED[items]:
            return "미분류", "상호품목불일치", evidence + " / 신고품목과 불일치"
        return category, "규칙후보" if category in CATEGORIES else "범위밖", evidence
    if AMBIGUOUS.search(name):
        return "미분류", "근거부족", "경계 키워드만으로 업종을 확정하지 않음"
    if items in DIRECT_ITEMS:
        return DIRECT_ITEMS[items], "품목후보", "신고품목 직접 연결(D7·D13)"
    return "미분류", "근거부족", "복합 신고품목에 기본 카테고리를 부여하지 않음"


def relabel_row(row: dict[str, str]) -> dict[str, str]:
    category, status, evidence = classify(row["상호"], row["취급품목"])
    if row["학습사용"] not in {"Y", "N"}:
        raise ValueError("학습사용은 Y 또는 N이어야 합니다")
    # Old exclusions may also encode unresolved source-quality problems.
    train = category in CATEGORIES and row["학습사용"] == "Y"
    reason = evidence
    if row["학습사용"] == "N":
        reason += "; 기존 학습 제외 유지"
    result = dict(row)
    result.update({
        "라벨_v2": row["라벨"], "학습사용_v2": row["학습사용"],
        "권장표본_v2": row["권장표본"], "서비스분류": category,
        "분류상태": status, "라벨규칙버전": VERSION,
        "라벨": category if train else "", "학습사용": "Y" if train else "N",
        "권장표본": "N", "라벨근거": reason,
        "라벨신뢰": "규칙추정" if train else "학습제외",
        "기준조정": VERSION, "기준조정근거": reason,
    })
    return result


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def prepare(source: Path):
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDS:
            raise ValueError("입력은 기존 v2 후보 CSV의 18개 열이어야 합니다")
        original = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in original):
        raise ValueError("열 수가 일치하지 않는 CSV 행")
    result = [relabel_row(row) for row in original]
    # Resolve all same-name disagreements before choosing training samples.
    by_name = collections.defaultdict(set)
    for row in result:
        by_name[normalize(row["상호"])].add(row["서비스분류"])
    for row in result:
        if len(by_name[normalize(row["상호"])]) > 1:
            row.update({
                "서비스분류": "미분류", "분류상태": "동명상호충돌", "라벨": "",
                "학습사용": "N", "라벨신뢰": "학습제외",
                "라벨근거": "동일 정규화 상호의 분류 결과 충돌",
                "기준조정근거": "동일 정규화 상호의 분류 결과 충돌",
            })
    groups = collections.defaultdict(list)
    for index, row in enumerate(result):
        if row["학습사용"] == "Y":
            groups[row["라벨"]].append(index)
    # Stable selection independent of runtime random libraries, retaining old caps.
    for category, indices in groups.items():
        indices.sort(key=lambda i: hashlib.sha256(
            ("42|" + "|".join(result[i][key] for key in
                             ("출처", "원본파일", "원본시작행", "상호_검색키"))).encode("utf-8")
        ).digest())
        for index in indices[:CAPS.get(category, len(indices))]:
            result[index]["권장표본"] = "Y"
    return original, result


def verify(original, result):
    if len(original) != len(result):
        raise ValueError("입출력 행 수 불일치")
    for old, new in zip(original, result):
        if any(old[key] != new[key] for key in FIELDS if key not in EDITED):
            raise ValueError("원본 식별·추적 정보 변경")
        if new["서비스분류"] not in (*CATEGORIES, "기타", "미분류"):
            raise ValueError("정의되지 않은 서비스분류")
        if new["학습사용"] == "Y":
            if old["학습사용"] != "Y" or new["라벨"] not in CATEGORIES or new["라벨"] != new["서비스분류"]:
                raise ValueError("학습 제외 복구 또는 학습 라벨 오류")
        elif new["라벨"] or new["권장표본"] != "N":
            raise ValueError("학습 제외 행에 라벨·표본 잔존")


def run(source: Path, output: Path | None = None):
    if output is not None and (source.resolve() == output.resolve() or output.exists()):
        raise FileExistsError("원본 또는 기존 출력 파일을 덮어쓰지 않습니다")
    before = file_hash(source)
    original, result = prepare(source)
    verify(original, result)
    summary = {
        "rule_version": VERSION, "source": str(source), "source_sha256": before,
        "rows": len(result),
        "previous_training": sum(r["학습사용"] == "Y" for r in original),
        "training": sum(r["학습사용"] == "Y" for r in result),
        "recommended_sample": sum(r["권장표본"] == "Y" for r in result),
        "service_categories": dict(collections.Counter(r["서비스분류"] for r in result)),
        "statuses": dict(collections.Counter(r["분류상태"] for r in result)),
        "training_categories": dict(collections.Counter(r["라벨"] for r in result if r["학습사용"] == "Y")),
        "changed_label_or_training": sum(
            (a["라벨"], a["학습사용"]) != (b["라벨"], b["학습사용"])
            for a, b in zip(original, result)
        ),
    }
    if file_hash(source) != before:
        raise ValueError("처리 중 입력 파일 변경")
    if output is not None:
        with output.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS + EXTRA)
            writer.writeheader()
            writer.writerows(result)
        with output.open(encoding="utf-8-sig", newline="") as stream:
            saved = list(csv.DictReader(stream))
        verify(original, saved)
        if saved != result or file_hash(source) != before:
            raise ValueError("저장 재검증 실패")
        summary.update(output=str(output), output_sha256=file_hash(output), readback_verified=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dry-run", action="store_true", help="집계만 출력하고 파일은 쓰지 않음")
    args = parser.parse_args()
    print(json.dumps(run(args.source, None if args.dry_run else args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
