"""조회 범위 한 곳 — 분석 항목이 '무엇을' 보는지: 카테고리 · 가게 이름 · 결제 수단 (DB 없음)

항목 dict 의 범위 칸: category(카테고리 코드) · instead_of(세부 지출 대신 카테고리 전체를 봄) · merchant(가게 이름) ·
merchant_names(같은 가게의 다른 표기) · payment(card = 카드 결제만 · bank = 계좌에서 나간 것만) ·
weekdays(요일 0=월 ~ 6=일) · time_slots(시간대 구간 코드) · amount_min · amount_max(한 건 순금액, 원). 이 칸들의 이름 · 검사 · 옮기기는 여기서만 한다.
거래를 실제로 거르는 건 analyze._items (FILTERS 칸), 화면 이름은 name. 상권 사전의 업종 같은 새 범위도 여기에 더한다.
"""
from .reading import CATEGORY_NAMES

FILTERS = ("payment", "merchant", "merchant_names", "weekdays", "time_slots", "amount_min", "amount_max")  # 카테고리 말고 거르는 칸
PAYMENTS = ("card", "bank")
# 시간대 6구간 (KST, MEMO 23-11 결정): 코드 → (이름, 시작 시, 끝 시(포함))
SLOTS = {"dawn": ("새벽", 0, 5), "morning": ("아침", 6, 10), "lunch": ("점심", 11, 13), "afternoon": ("오후", 14, 17),
         "evening": ("저녁", 18, 20), "night": ("밤", 21, 23)}
SLOT_GROUPS = {"late_night": ("night", "dawn"), "after_work": ("evening", "night")}  # 야식 · 퇴근 후
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = "월화수목금토일"
DAY_GROUPS = {"weekday": (0, 1, 2, 3, 4), "weekend": (5, 6)}

# 온라인 서비스 별칭 표 — 매장이 없어 상가 정보(가게 사전)에 없는 곳. 묶음 이름 → 부르는 말 · 브랜드별 내역 표기
# 표기 = 카드 · 계좌 내역에 찍힐 수 있는 이름 (이름에 들어 있으면 그 브랜드). 짧고 흔한 말(예: 지역명 '대구로')은 다른 가게와
# 헷갈려 넣지 않는다. '쿠팡'은 쇼핑이라 넣지 않는다 (쿠팡이츠만).
# [확인 필요] 운영사 이름(우아한형제들 · 위대한상상 · 쿠팡이츠서비스)이 실제 내역에 그렇게 찍히는지 — CODEF 실제 데이터로 확인 전
ONLINE = {
    "배달앱": {
        "words": ("배달", "배달앱", "배달어플", "배달음식", "배달비"),
        "brands": {
            "배달의민족": ("배달의민족", "배민", "우아한형제들"),
            "쿠팡이츠": ("쿠팡이츠",),
            "요기요": ("요기요", "위대한상상"),
            "땡겨요": ("땡겨요",),
            "배달특급": ("배달특급",),  # 경기도 공공배달앱
            "먹깨비": ("먹깨비",),  # 공공배달앱 (여러 지자체)
        },
    },
}


def slot_of(hhmm):
    """'HH:MM' → 시간대 코드 (시각 없음이면 None)"""
    if not hhmm:
        return None
    hour = int(hhmm[:2])
    return next(code for code, (_, start, end) in SLOTS.items() if start <= hour <= end)


def expand_slots(said):
    """해석의 시간대 말(야식 · 퇴근 후 묶음 포함) → 구간 코드 목록 (SLOTS 순서, 겹치면 한 번)"""
    codes = {c for s in said for c in SLOT_GROUPS.get(s, (s,))}
    return [c for c in SLOTS if c in codes]


def expand_days(said):
    """해석의 요일 말(weekday · weekend · mon~sun) → 요일 번호 목록 (0=월)"""
    days = {d for s in said for d in (DAY_GROUPS[s] if s in DAY_GROUPS else (DAYS.index(s),))}
    return sorted(days)


def _won(n):
    return f"{n:,}원"


def filter_label(item):
    """거르기 조건의 화면 이름: '주말' · '밤' · '10,000원 이상' (없으면 '')"""
    parts = []
    days = item.get("weekdays")
    if days:
        parts.append(next((n for n, g in (("평일", DAY_GROUPS["weekday"]), ("주말", DAY_GROUPS["weekend"])) if tuple(days) == g),
                          "·".join(DAY_NAMES[d] for d in days) + "요일"))
    if item.get("time_slots"):
        parts.append("·".join(SLOTS[c][0] for c in item["time_slots"]))
    low, high = item.get("amount_min"), item.get("amount_max")
    if low is not None or high is not None:
        parts.append(f"{_won(low)}~{_won(high)}" if low is not None and high is not None
                     else f"{_won(low)} 이상" if low is not None else f"{_won(high)} 이하")
    return " · ".join(parts)


SHORT_PERIODS = ("this_week", "last_week", "range")  # 달보다 짧은 기간: 한 달에 한 번 나가는 고정지출이 총액을 덮는다


def excludes_fixed(item):
    """요일 · 시간대 조건 · 묶기(10번 group_by) · 달보다 짧은 기간이면 고정지출을 기본으로 뺀다 — 월세 같은 자동이체가 '화요일 · 아침'을 덮지 않게.
    '고정지출 포함해서'(include_fixed)면 포함. 총액 · 카테고리 · 금액 범위 · 평균 · 최대 조회는 지금처럼 포함 (10/30 결정)"""
    item = item or {}
    query = item.get("query") or {}
    return not item.get("include_fixed") and bool(item.get("weekdays") or item.get("time_slots") or query.get("group_by")
                                                  or query.get("period") in SHORT_PERIODS)


def plain(text):
    """가게 이름 비교용: 공백을 없애고 소문자로"""
    return "".join((text or "").split()).lower()


def online(word):
    """부른 말 → (묶음 이름, 표기 목록). 묶음을 부르면("배달") 그 묶음 브랜드 전부, 브랜드 · 표기를 부르면("배민") 그 브랜드만.
    별칭 표에 없으면 (None, ())"""
    said = plain(word)
    for group, spec in ONLINE.items():
        if said in (plain(w) for w in (group, *spec["words"])):
            return group, tuple(n for names in spec["brands"].values() for n in names)
        for brand, names in spec["brands"].items():
            if said in (plain(n) for n in (brand, *names)):
                return None, names
    return None, ()


def name(item):
    """실제 조회 범위 이름. 세부 지출 대신 카테고리 전체를 본 항목은 '식비 전체', 가게 이름 검색은 그 이름,
    카드 결제만은 '카드 결제' · '식비 카드 결제', 범위 없는 소비는 '전체' (예산 4번은 '생활비'), 요일 · 시간대 · 금액은 ' · 주말' 처럼 뒤에"""
    if item.get("merchant"):
        base = online(item["merchant"])[0] or item["merchant"]  # 묶음("배달")이면 '배달앱'
    elif item.get("instead_of"):
        base = f"{CATEGORY_NAMES[item['category']]} 전체"
    else:
        base = CATEGORY_NAMES[item["category"]] if item.get("category") else ("생활비" if item["type"] == 4 else "전체")
    if item.get("payment"):
        pay = {"card": "카드 결제", "bank": "계좌 결제"}[item["payment"]]
        base = pay if base == "전체" else f"{base} {pay}"
    extra = filter_label(item)  # 요일 · 시간대 · 금액: '식비 · 주말', 범위가 전체면 '주말'만
    return (extra if base == "전체" else f"{base} · {extra}") if extra else base


def label(item):
    """선택지 · 카드 안 이름: 범위 없는 소비 조회는 '전체 소비'"""
    return name(item) if narrowed(item) or item["type"] in (3, 4) else "전체 소비"


def narrowed(item):
    """카테고리 · 가게 · 결제 수단으로 좁힌 조회인지 (아니면 전체 소비 답변 — 고정지출 · 생활비 막대 · 전반 분석)"""
    return bool(item.get("category") or any(item.get(k) is not None and item.get(k) != [] for k in FILTERS))


def filters(item):
    """다른 항목(설명용 새 비교 등)에 그대로 옮길 거르기 칸"""
    return {k: item[k] for k in FILTERS if item.get(k) is not None and item.get(k) != []}


def check(item, kind):
    """범위 칸 검사 (잘못되면 ValueError). 카테고리 · instead_of 는 analyze.validate 가 유형과 함께 본다"""
    if item.get("payment") not in (None, *PAYMENTS) or (item.get("payment") and kind in (3, 4)):
        raise ValueError(f"payment 는 소비 조회의 'card' 만: {item!r}")
    merchant = item.get("merchant")
    if merchant is not None and (not isinstance(merchant, str) or not plain(merchant) or kind not in (1, 6, 8, 10)):
        raise ValueError(f"merchant 는 총액 · 비교 · 추이 · 범용 조회(1 · 6 · 8 · 10)의 가게 이름 문자열: {item!r}")
    days, slots = item.get("weekdays"), item.get("time_slots")
    if days is not None and (not isinstance(days, list) or not days or not all(type(d) is int and 0 <= d <= 6 for d in days)):
        raise ValueError(f"weekdays 는 0~6 정수 목록: {item!r}")
    if slots is not None and (not isinstance(slots, list) or not slots or not all(s in SLOTS for s in slots)):
        raise ValueError(f"time_slots 는 시간대 코드 목록: {item!r}")
    low, high = item.get("amount_min"), item.get("amount_max")
    if any(v is not None and (type(v) is not int or v < 0) for v in (low, high)) or (low is not None and high is not None and low > high):
        raise ValueError(f"금액 범위는 0 이상 정수, 최소 ≤ 최대: {item!r}")
    if (days or slots or low is not None or high is not None) and kind in (3, 4):
        raise ValueError(f"예산 잔액은 요일 · 시간대 · 금액으로 나누지 않는다: {item!r}")
    names = item.get("merchant_names")
    if names is not None and (merchant is None or not isinstance(names, list) or not all(isinstance(n, str) for n in names)):
        raise ValueError(f"merchant_names 는 가게 이름 검색의 다른 표기 목록: {item!r}")
