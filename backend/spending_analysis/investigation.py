"""분석 질문의 대상과 지표 계약. 거래 없는 대상 이름 추측·부분 문자열 매칭은 하지 않는다."""
from . import scope
from .reading import CATEGORY_NAMES

METRICS = ("amount", "count", "average", "day_average")
TARGETS = {"scope": {"label": "현재 조회 범위", "by": "scope", "keys": ()}}
for key, (label, *_) in scope.SLOTS.items():
    TARGETS[f"slot:{key}"] = {"label": label, "by": "time_slot", "keys": (key,)}
for key, slots in scope.SLOT_GROUPS.items():
    TARGETS[f"slot:{key}"] = {"label": {"late_night": "야식 시간대", "after_work": "퇴근 후 시간대"}[key], "by": "time_slot", "keys": slots}
for key in (*scope.DAYS, *scope.DAY_GROUPS):
    TARGETS[f"day:{key}"] = {"label": {"weekday": "평일", "weekend": "주말"}.get(key) or f"{scope.DAY_NAMES[scope.DAYS.index(key)]}요일",
                            "by": "weekday", "keys": tuple(scope.expand_days([key]))}
for code, label in CATEGORY_NAMES.items():
    TARGETS[f"category:{code}"] = {"label": label, "by": "category", "keys": (code,)}
for group, spec in scope.ONLINE.items():
    for label in (group, *spec["brands"]):
        TARGETS[f"online:{label}"] = {"label": label, "by": "merchant", "keys": scope.online(label)[1]}
for key, label in (("new", "새로 간 곳"), ("regular", "두 기간 모두 간 곳"), ("gone", "이번엔 안 간 곳")):
    TARGETS[f"visit:{key}"] = {"label": label, "by": "visit", "keys": (key,)}
TARGETS["unavailable"] = {"label": "내역으로 식별할 수 없는 대상", "by": "unavailable", "keys": ()}

def _schema_branch(targets, missing_type):
    return {"type": "object", "properties": {
        "metric": {"type": "string", "enum": list(METRICS)},
        "target": {"type": "string", "enum": targets},
        "unavailable": {"type": missing_type}},
        "required": ["metric", "target", "unavailable"], "additionalProperties": False}


SCHEMA = {"anyOf": [_schema_branch([t for t in TARGETS if t != "unavailable"], "null"),
                    _schema_branch(["unavailable"], "string"), {"type": "null"}]}

INSTRUCTION = """hypothesis는 일반 증감 이유·구성 설명이면 null, 사용자가 별도로 짐작한 원인 후보가 있으면 {metric, target, unavailable}이다.
설명할 카테고리를 다시 말한 것은 원인 후보가 아니다. 조회 범위와 원인 후보를 중복하지 않는다.
metric은 amount(총액), count(결제 횟수), average(건당 평균), day_average(하루 평균) 중 하나다.
더 자주 구매·방문·약속했다는 빈도 가설은 count, 끼니값·가격이 올라 비싸졌다는 가설은 average다. 총지출이 늘었다는 amount와 구분한다.
많이 썼다는 금액 가설은 amount다. 대상 조회가 불가능해도 질문한 지표를 보존한다.
요일 소비 비교는 기간별 요일 수 차이를 보정하는 day_average를 기본으로, 사용자가 합계 자체를 요구하면 amount를 쓴다.
target은 제공한 대상 코드만 선택한다. 지표 이름을 대상으로 쓰지 않는다. 대상 없는 건당 금액 질문은 scope, 점심 평균은 slot:lunch, 주말 소비는 day:weekend이다.
관측 가능한 결제 대상이 있으면 그 코드와 지표를 선택한다. 가설의 인과·가격·약속 여부가 불확실하다는 이유만으로 대상을 unavailable로 바꾸지 않는다.
상품 가격·식사 인원·약속 횟수의 한계는 서버가 별도로 안내한다. 현재 범위의 결제 부담 변화는 scope의 average로 확인하되 상품 가격이라고 단정하지 않는다.
커피 등 구매 품목, 신규 개업 여부, 목록에 없는 대상은 target=unavailable로 원래 대상을 unavailable에 보존한다. 카페 결제를 커피 구매로 바꾸지 않는다.
그 밖에는 unavailable=null. 새로 간 가게(visit:new)와 새로 생긴 가게(unavailable)는 다르다."""


def parse(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"metric", "target", "unavailable"}:
        raise ValueError("hypothesis_shape")
    if raw["metric"] not in METRICS or raw["target"] not in TARGETS:
        raise ValueError("hypothesis_code")
    word = raw["unavailable"]
    if raw["target"] == "unavailable":
        if not isinstance(word, str) or not word.strip() or len(word) > 80:
            raise ValueError("hypothesis_unavailable")
    elif word is not None:
        raise ValueError("hypothesis_unavailable")
    if raw["target"].startswith("visit:") and raw["metric"] != "amount":
        raise ValueError("hypothesis_metric")
    if raw["metric"] == "day_average" and not raw["target"].startswith("day:"):
        raise ValueError("hypothesis_metric")
    return dict(raw)


def catalog():
    out = []
    for key, value in TARGETS.items():
        row = {"id": key, "label": value["label"]}
        if key.startswith("online:"):
            label = value["label"]
            row["aliases"] = list(scope.ONLINE[label]["words"]) if label in scope.ONLINE else list(scope.online(label)[1])
        out.append(row)
    return out


def internal(raw):
    value = parse(raw)
    if value is None:
        return None
    target = TARGETS[value["target"]]
    axis = value["metric"] if value["metric"] in ("count", "average") else target["by"]
    return {**value, "axis": axis, "subject": value["unavailable"] or target["label"]}


def compatible(item, target):
    spec = TARGETS[target]
    if spec["by"] == "category" and item.get("category") not in (None, spec["keys"][0]):
        return False
    if spec["by"] == "merchant" and item.get("merchant"):
        original = scope.online(item["merchant"])[1] or tuple(item.get("merchant_names") or (item["merchant"],))
        if not {scope.plain(k) for k in spec["keys"]} <= {scope.plain(k) for k in original}:
            return False
    # 상위 조회의 필터를 확대하지 않는다. 겹치는 범위만 근거로 쓸 수 있다.
    if spec["by"] == "time_slot" and item.get("time_slots") and not set(spec["keys"]) <= set(item["time_slots"]):
        return False
    if spec["by"] == "weekday" and item.get("weekdays") and not set(spec["keys"]) <= set(item["weekdays"]):
        return False
    return True


def resolve(value, item):
    """같은 카테고리를 다시 묻는 중복 대상을 현재 범위로 정규화한다. 전체 금액 반복은 원인 가설이 아니다."""
    if value and "target" in value:
        if item.get("category") and value["target"] == f"category:{item['category']}":
            value = internal({"metric": value["metric"], "target": "scope", "unavailable": None})
        if value["target"] == "scope" and value["metric"] == "amount":
            return None
    return value
