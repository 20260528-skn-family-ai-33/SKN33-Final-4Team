"""더미 거래 시각 다양화 — design.json 의 occurred_time 만 현실적인 시각으로 바꾸는 한 번 돌리는 도구 (네트워크 · DB 없음)

    cd backend && ../.venv/bin/python -I spending_fixture/retime_transactions.py

- 바꾸는 것: occurred_time 만 (KST). 날짜 · 금액 · 카테고리 · 가게 · 414행 · expected.json 은 그대로
- 같은 입력이면 같은 결과: 시간대 · 시 · 분을 plan_id 의 sha256 으로 고른다
- 급여 · 이체 · 자동이체 · 고정지출 · 저축 · 카드대금은 오전의 일정한 시각 (고정지출은 가게(이름)마다 매달 같은 시각)
- 생활 소비는 카테고리별 시간대 가중치(SLOTS): 식비 = 점심 · 저녁 중심 + 일부 야식, 교통 = 출퇴근(주말은 낮),
  쇼핑 = 저녁 ~ 밤, 의료 = 진료 시간대 …
- 회원 경향 하나씩 (과장 없이): 민재(B) = 밤 외식 · 쇼핑 가중치 조금 높게, 윤서(A) = 퇴근 후 저녁 소액(15,000원 이하) 결제 조금 많게,
  지우(C) = 기본 분포
- 같은 구매 쌍: 계좌 출금은 카드 승인 1분 뒤. 취소는 원거래 행 안의 금액이라(따로 된 취소 행 없음) 맞출 시각이 없다
- 끝나면 seed.SHA256["design.json"] 을 새 값으로 바꿔야 한다 (출력에 나온다)
"""
import hashlib
import json
from datetime import date
from pathlib import Path

DESIGN = Path(__file__).resolve().parent / "data" / "design.json"
# 소비 아닌 거래 · 고정지출: 오전 일정 시각 (시, 분)
FIXED_TIME = {"income": (9, 0), "saving": (10, 0), "transfer": (11, 0), "own_transfer": (11, 30), "card_payment": (7, 30)}
RECURRING_HOUR = 8  # 자동이체 · 고정지출: 8시 + 가게마다 0 · 10 · 20 · 30 · 40분
# 카테고리 → [(시작 시, 끝 시(포함), 가중치)] 평일 기준
SLOTS = {
    "FOOD": [(11, 13, 45), (14, 16, 10), (18, 20, 35), (21, 23, 10)],
    "TRANSPORT_CAR": [(7, 9, 45), (10, 17, 15), (18, 20, 40)],
    "FASHION_SHOPPING": [(13, 17, 30), (18, 22, 70)],
    "CULTURE_LEISURE": [(13, 17, 40), (18, 22, 60)],
    "HOUSEHOLD": [(12, 17, 50), (18, 21, 50)],
    "EDUCATION": [(10, 17, 50), (19, 21, 50)],
    "BEAUTY": [(11, 19, 100)],
    "MEDICAL_HEALTH": [(9, 12, 50), (14, 18, 50)],
    "TRAVEL_LODGING": [(15, 21, 100)],
    "ETC": [(8, 9, 25), (12, 13, 25), (19, 23, 50)],
    "UNCLASSIFIED": [(10, 22, 100)],
}
WEEKEND = {"TRANSPORT_CAR": [(10, 20, 100)]}  # 주말은 출퇴근이 없다


def _hash(*parts):
    return int(hashlib.sha256(":".join(map(str, parts)).encode("utf-8")).hexdigest(), 16)


def _slots(r):
    weekend = date.fromisoformat(r["occurred_on"]).weekday() >= 5
    slots = [list(s) for s in (WEEKEND.get(r["category"]) if weekend else None) or SLOTS[r["category"]]]
    if r["persona"] == "B" and r["category"] in ("FOOD", "FASHION_SHOPPING"):  # 민재: 밤 외식 · 쇼핑 조금 더
        for s in slots:
            s[2] += 8 if s[1] >= 21 else 0
    if r["persona"] == "A" and r["amount_won"] <= 15_000:  # 윤서: 퇴근 후 저녁 소액 결제 조금 더
        for s in slots:
            s[2] += 15 if s[0] <= 19 <= s[1] else 0
    return slots


def pick(r):
    """한 행 → "HH:MM:SS" """
    kind = r["transaction_type"]
    if kind != "spending":
        hour, minute = FIXED_TIME[kind]
    elif r["funding"] == "fixed":
        hour, minute = RECURRING_HOUR, 10 * (_hash(r["persona"], r["merchant_name"]) % 5)
    else:
        slots, h = _slots(r), _hash(r["plan_id"])
        point = h % sum(s[2] for s in slots)
        for start, end, weight in slots:
            if point < weight:
                break
            point -= weight
        hour, minute = start + (h >> 16) % (end - start + 1), (h >> 32) % 60
    return f"{hour:02d}:{minute:02d}:00"


def retime(design):
    rows = design["transaction_design_rows"]
    by_id = {r["plan_id"]: r for r in rows}
    for r in rows:
        r["occurred_time"] = pick(r)
    for r in rows:  # 같은 구매의 계좌 출금 = 카드 승인 1분 뒤 (자정을 넘기지 않게)
        if r["same_purchase_with"]:
            hour, minute, _ = map(int, by_id[r["same_purchase_with"]]["occurred_time"].split(":"))
            total = min(hour * 60 + minute + 1, 23 * 60 + 59)
            r["occurred_time"] = f"{total // 60:02d}:{total % 60:02d}:00"


def main():
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    retime(design)
    note = "occurred_time: 거래 종류 · 카테고리별 현실적인 시각 (retime_transactions.py, plan_id 해시). 원래는 모두 12:00"
    if note not in design["notes"]:
        design["notes"].append(note)
    raw = json.dumps(design, ensure_ascii=False, indent=2) + "\n"
    DESIGN.write_text(raw, encoding="utf-8")
    print(f"design.json sha256 = {hashlib.sha256(raw.encode('utf-8')).hexdigest()}")


if __name__ == "__main__":
    main()
