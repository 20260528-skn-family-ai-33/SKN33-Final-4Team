"""
CODEF 데모 - 현대카드 승인내역 '가능한 전부' → CSV (분류 모델 평가 데이터용)
준비: .env 에 CODEF 키 + CODEF_CONNECTED_ID (test_codef.py 로 이미 만든 것)
실행: python test_card_transactions.py

- 오늘부터 90일씩 과거로 올라가며 조회 → 카드사가 더 이상 안 주면(에러/연속 빈 결과) 멈춤
- 최대 MAX_DAYS 까지만 (호출 수 ≈ MAX_DAYS / CHUNK_DAYS, 기본 최대 8회)
- 처음 실행하면 기존 connectedId 에 현대카드를 붙인다 (홈페이지 아이디·비번 1회 입력)
- 결과: data/raw/hyundai_card_approvals.csv  (+ 원본 data/raw/hyundai_card_raw.json)
  data/raw/ 는 gitignore 돼 있음. 깃·단톡방에 올리지 마!
"""
import csv
import json
import os
from datetime import date, timedelta
from getpass import getpass

from dotenv import load_dotenv
from easycodefpy import Codef, ServiceType, encrypt_rsa

load_dotenv()

codef = Codef()
codef.public_key = os.getenv("CODEF_PUBLIC_KEY")
codef.set_demo_client_info(os.getenv("CODEF_DEMO_ID"), os.getenv("CODEF_DEMO_SECRET"))
ST = ServiceType.DEMO

CARD = "0302"  # 현대카드
cid = os.getenv("CODEF_CONNECTED_ID")
if not cid:
    raise SystemExit(".env 에 CODEF_CONNECTED_ID 가 없어. test_codef.py 먼저 돌려줘.")

CHUNK_DAYS = 90    # 한 번에 조회할 기간
MAX_DAYS = 730     # 최대 2년 전까지만 시도 (호출 수 상한 = 730/90 ≈ 8회)
STOP_AFTER_EMPTY = 2  # 빈 결과가 연속 2번이면 더 과거엔 없다고 보고 멈춤

OUT_DIR = "data/raw"
CSV_PATH = f"{OUT_DIR}/hyundai_card_approvals.csv"
RAW_PATH = f"{OUT_DIR}/hyundai_card_raw.json"

calls = 0


def approvals(start, end):
    global calls
    calls += 1
    res = codef.request_product("/v1/kr/card/p/account/approval-list", ST, {
        "connectedId": cid,
        "organization": CARD,
        "startDate": start.strftime("%Y%m%d"),
        "endDate": end.strftime("%Y%m%d"),
        "orderBy": "0",              # 최신순
        "inquiryType": "1",          # 전체 카드
        "memberStoreInfoType": "1",  # 가맹점 정보 포함 (업종 받으려고)
    })
    data = json.loads(res)
    r = data.get("result", {})
    d = data.get("data") or []
    rows = d if isinstance(d, list) else ([d] if d else [])
    return r, rows


def add_card():
    """기존 connectedId 에 현대카드 붙이기. 비번은 암호화해서 보내고 버린다."""
    global calls
    login_id = input("현대카드 홈페이지 아이디: ").strip()
    pw = getpass("비밀번호 (입력해도 화면에 안 보임): ")
    calls += 1
    res = json.loads(codef.add_account(ST, {
        "connectedId": cid,
        "accountList": [{
            "countryCode": "KR",
            "businessType": "CD",   # 카드
            "clientType": "P",      # 개인
            "organization": CARD,
            "loginType": "1",       # 아이디/비번
            "id": login_id,
            "password": encrypt_rsa(pw, codef.public_key),
        }],
    }))
    del pw
    r = res.get("result", {})
    print("현대카드 추가:", r.get("code"), r.get("message"), r.get("extraMessage", ""))
    # CF-04000 같은 포괄 실패면 진짜 원인은 data.errorList 에 있음
    d = res.get("data") or {}
    for e in (d.get("errorList") or []) if isinstance(d, dict) else []:
        print("  └ 상세:", e.get("code"), e.get("message"), e.get("extraMessage", ""))
    return r.get("code") == "CF-00000"


def fmt(r):
    return f'{r.get("code")} {r.get("message", "")} {r.get("extraMessage", "")}'.strip()


# ── 1) 90일씩 과거로 올라가며 조회 ──
today = date.today()
oldest = today - timedelta(days=MAX_DAYS)
end = today
all_rows, empty_streak, first = [], 0, True

while end > oldest:
    start = max(end - timedelta(days=CHUNK_DAYS - 1), oldest)
    r, rows = approvals(start, end)

    # 첫 구간부터 실패 = 현대카드가 아직 안 붙어 있을 가능성 → 붙이고 다시
    if first and r.get("code") != "CF-00000":
        print("조회 실패:", fmt(r))
        if input("\n현대카드를 아직 연결 안 했으면 지금 추가할까? (y/n): ").strip().lower() != "y":
            raise SystemExit
        if not add_card():
            raise SystemExit("\n추가 실패. 위 결과 코드를 알려줘.")
        r, rows = approvals(start, end)
        if r.get("code") != "CF-00000":
            raise SystemExit(f"재조회 실패: {fmt(r)}")
    first = False

    if r.get("code") != "CF-00000":
        print(f"{start} ~ {end}: 실패 ({fmt(r)}) → 카드사 조회 가능 기간 끝으로 보고 멈춤")
        break

    print(f"{start} ~ {end}: {len(rows)}건")
    all_rows.extend(rows)
    empty_streak = 0 if rows else empty_streak + 1
    if empty_streak >= STOP_AFTER_EMPTY:
        print("빈 구간이 연속이라 더 과거엔 없다고 보고 멈춤")
        break
    end = start - timedelta(days=1)

# ── 2) 중복 제거 (구간이 안 겹치지만 혹시 몰라서) ──
seen, uniq = set(), []
for row in all_rows:
    key = (row.get("resUsedDate"), row.get("resUsedTime"), row.get("resApprovalNo"),
           row.get("resUsedAmount"), row.get("resMemberStoreName"))
    if key not in seen:
        seen.add(key)
        uniq.append(row)

# ── 3) 저장 ──
os.makedirs(OUT_DIR, exist_ok=True)
with open(RAW_PATH, "w", encoding="utf-8") as f:
    json.dump(uniq, f, ensure_ascii=False, indent=2)

COLS = {  # CODEF 칸 → 우리 칸
    "resUsedDate": "date",
    "resUsedTime": "time",
    "resMemberStoreName": "merchant",
    "resUsedAmount": "amount",
    "resMemberStoreType": "store_type",   # 카드사 업종 → 초벌 라벨
    "resCancelYN": "cancel",
}
with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀에서 안 깨짐
    w = csv.writer(f)
    w.writerow(COLS.values())
    for row in uniq:
        w.writerow([row.get(k, "") for k in COLS])

dates = sorted(row.get("resUsedDate", "") for row in uniq if row.get("resUsedDate"))
print(f"\n총 {len(uniq)}건 ({dates[0] if dates else '-'} ~ {dates[-1] if dates else '-'}) → {CSV_PATH}")
print(f"이번 실행 API 호출: {calls}회")
empty = sum(1 for row in uniq if not row.get("resMemberStoreType"))
print(f"업종(store_type) 비어있는 건: {empty}/{len(uniq)}")
for row in uniq[:5]:
    print(row.get("resUsedDate"), "|", row.get("resMemberStoreName"), "|",
          row.get("resUsedAmount"), "|", row.get("resMemberStoreType", ""))
if uniq and not any(k in uniq[0] for k in COLS):
    print("\n⚠️ 칸 이름이 예상과 달라. hyundai_card_raw.json 열어서 실제 키 이름 알려줘.")
