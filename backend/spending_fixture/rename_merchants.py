"""더미 거래의 가게 이름("가상한끼점" 등)을 상가(상권)정보의 실제 상호로 바꾼다 — 한 번 돌리는 도구 (네트워크 · DB 없음)

    cd backend && ../.venv/bin/python -I spending_fixture/rename_merchants.py <stores_all_categories.csv>

- 바꾸는 것: data/design.json 의 merchant_name 중 '가상…점'(가게) 이름만. 금액 · 날짜 · 카테고리 · 건수 · 414행 · expected.json 은 그대로
- 고정지출 · 계좌 이체 이름(가상임대료 · 관리비 · 통신료 · 자동차보험 · 온라인수강료 · 결제대행)은 가게가 아니라 상가정보에 없다 →
  FIXED 표의 자연스러운 이름으로 (CSV 없이 돌리면 이것만: ../.venv/bin/python -I spending_fixture/rename_merchants.py)
- 고르는 법 (같은 입력이면 늘 같은 결과): 서울 · 기본검증통과 · 업종 라벨 확정(학습대상여부 Y, 정책상 미분류인 기타 업종은 제외) · 이름에 기호가 없는 상가 중 카테고리마다 정한 업종(INDUSTRIES)에서,
  상가업소번호의 sha256 순으로 회원마다 겹치지 않게 POOL 개씩 → 거래마다 plan_id 의 sha256 으로 그중 하나.
  같은 구매(카드 승인 + 계좌 출금) 쌍은 카드 쪽 이름을 같이 쓴다
- 테스트가 '가게 이름 검색 안 됨 → 카테고리 전체로 볼까요?'를 확인하는 말(커피 · 배달 · 택시)이 든 상호는 고르지 않는다
- 끝나면 seed.SHA256["design.json"] 을 새 값으로 바꿔야 한다 (출력에 나온다)
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

DESIGN = Path(__file__).resolve().parent / "data" / "design.json"
# 카테고리 → 상권업종소분류명 (서울에 흔한 업종 위주)
INDUSTRIES = {
    "FOOD": ("백반/한정식", "카페", "김밥/만두/분식", "빵/도넛", "치킨", "국/탕/찌개류", "중국집", "피자", "경양식", "일식 회/초밥"),
    "TRANSPORT_CAR": ("주유소", "자동차 세차장", "자동차 정비소"),
    "TRANSPORT_CAR_NO_CAR": ("자전거 소매업",),  # 차가 없는 회원 (대중교통 · 택시는 상가정보에 없다)
    "FASHION_SHOPPING": ("기타 의류 소매업", "여성 의류 소매업", "남성 의류 소매업", "신발 소매업", "가방 소매업"),
    "CULTURE_LEISURE": ("노래방", "PC방", "헬스장", "당구장"),
    "HOUSEHOLD": ("주방/가정용품 소매업", "가전제품 소매업", "침구류/커튼 소매업", "가구 소매업"),
    "EDUCATION": ("서점", "외국어학원", "독서실/스터디 카페", "문구/회화용품 소매업"),
    "BEAUTY": ("미용실", "네일숍", "화장품 소매업"),
    "MEDICAL_HEALTH": ("약국", "내과/소아과 의원", "치과의원"),
    "TRAVEL_LODGING": ("호텔/리조트",),
    "ETC": ("편의점", "세탁소"),
}
POOL = {"FOOD": 10}  # 회원마다 카테고리별 가게 수 (그 밖은 3)
AVOID = ("커피", "배달", "택시")
FIXED = {"가상임대료": "월세", "가상관리비": "관리비", "가상통신료": "통신요금", "가상자동차보험": "자동차보험료",
         "가상온라인수강료": "온라인 강의 수강료", "가상결제대행01": "간편결제 대행", "가상결제대행02": "온라인 결제 대행"}


def _order(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidates(path):
    """업종 → [상호(+ 지점)] (상가업소번호 sha256 순, 같은 상호는 한 번)"""
    wanted = {i for names in INDUSTRIES.values() for i in names}
    found = {i: [] for i in wanted}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            industry = r["상권업종소분류명"]
            if industry in wanted and r["시도명"] == "서울특별시" and r["정제상태"] == "기본검증통과" \
                    and (r["학습대상여부"] == "Y" or industry in INDUSTRIES["ETC"]) and not any(w in r["상호명"] for w in AVOID) \
                    and all(ch.isalnum() or ch in " &-·" for ch in r["상호명"] + r["지점명"]):
                found[industry].append((_order(r["상가업소번호"]), " ".join(filter(None, (r["상호명"].strip(), r["지점명"].strip())))))
    out = {}
    for industry, rows in found.items():
        seen, names = set(), []
        for _, name in sorted(rows):
            if name not in seen:
                seen.add(name)
                names.append(name)
        out[industry] = names
    return out


def pools(design, found):
    """(회원, 카테고리) → 가게 이름 목록. 업종을 돌아가며 하나씩, 회원끼리 겹치지 않게"""
    cursor = {i: 0 for i in found}
    out = {}
    for persona in sorted(design["personas"]):
        car = design["personas"][persona]["has_car"]
        for category, industries in INDUSTRIES.items():
            if category == "TRANSPORT_CAR_NO_CAR":
                continue
            if category == "TRANSPORT_CAR" and not car:
                industries = INDUSTRIES["TRANSPORT_CAR_NO_CAR"]
            names = []
            for n in range(POOL.get(category, 3)):
                industry = industries[n % len(industries)]
                names.append(found[industry][cursor[industry]])
                cursor[industry] += 1
            out[persona, category] = names
    return out


def rename(design, found):
    by_pool, rows = pools(design, found), design["transaction_design_rows"]
    by_id = {r["plan_id"]: r for r in rows}
    changed = 0
    for r in rows:
        name = r["merchant_name"]
        if not (name and name.startswith("가상") and name.endswith("점")) or r["same_purchase_with"]:
            continue
        names = by_pool[r["persona"], r["category"]]
        r["merchant_name"] = names[int(_order(r["plan_id"]), 16) % len(names)]
        changed += 1
    for r in rows:  # 같은 구매의 계좌 출금 쪽은 카드 쪽 이름 그대로
        if r["same_purchase_with"] and r["merchant_name"] and r["merchant_name"].startswith("가상") \
                and r["merchant_name"].endswith("점"):
            r["merchant_name"] = by_id[r["same_purchase_with"]]["merchant_name"]
            changed += 1
    return changed


def rename_fixed(design):
    changed = 0
    for r in design["transaction_design_rows"]:
        if r["merchant_name"] in FIXED:
            r["merchant_name"] = FIXED[r["merchant_name"]]
            changed += 1
    return changed


def main(argv):
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    changed = rename(design, candidates(Path(argv[1]).expanduser())) if len(argv) > 1 else 0
    changed += rename_fixed(design)
    for note in ("merchant_name: 가게 이름은 상가(상권)정보 서울 상호에서 골랐다 (rename_merchants.py). 금액 · 날짜 · 카테고리는 가상",
                 "merchant_name: 고정지출 · 결제대행 이름은 '가상' 없이 자연스러운 표시 이름 (rename_merchants.FIXED)"):
        if note not in design["notes"]:
            design["notes"].append(note)
    raw = json.dumps(design, ensure_ascii=False, indent=2) + "\n"
    DESIGN.write_text(raw, encoding="utf-8")
    print(f"바꾼 행 {changed}개 · design.json sha256 = {hashlib.sha256(raw.encode('utf-8')).hexdigest()}")


if __name__ == "__main__":
    main(sys.argv)
