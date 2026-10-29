"""'왜 늘었어?' — 직전 확정 분석의 거래 근거 (읽기 전용 · 금액 계산은 서버만)

- 근거는 직전 분석의 확정(evidence=confirmed) 항목 하나의 두 기간 · 카테고리로만 다시 조회해 만든다
  6 · 7번 = 이번 달 vs 지난달 같은 기간, 8 · 9번 = 가장 최근의 이웃한 두 달. 1~5번은 비교 기준이 없어 설명하지 않는다
- 집계는 소비 분석과 같은 규칙: listed_transactions(같은 구매 제외) → transaction_item(취소 차감) → 소비 거래만
- 다시 조회한 기간이 완전 수집이 아니거나 합계가 기록된 확정 수치와 다르면 근거로 쓰지 않는다 (자료가 바뀜)
- 계산하는 것: 금액 · 증감 · 건수 · 평균 결제액 · 가맹점별 증감 (전체 범위면 카테고리별 증감)
  [임시] 건수 = 순금액이 0 보다 큰 소비 거래 (전액 취소는 세지 않는다)
  [임시] 가맹점 = finance.merchant_rules.normalize_merchant (공백만 정리한 이름 — 지점이 다르면 다른 곳). MEMO 20번 질문
- 원인 · 동기 · 생활 습관은 추측하지 않는다. 답변은 '거래에서 확인한 변화'와 '이유는 단정할 수 없음'을 나눠 쓴다

문장: 기본은 서버 템플릿. 설명 LLM(complete)을 주면 근거 항목 이름과 키만 넘기고({F1} 자리표시), 돌아온 문장은
숫자 · 모르는 키 · 금지 표현이 없을 때만 서버 값을 끼워 쓴다. 하나라도 어긋나면 템플릿으로 돌아간다 (재시도 없음)
자리표시 바로 뒤 조사(을/를 · 은/는 · 이/가 · 과/와 · 으로/로)는 끼운 값의 받침에 맞춰 서버가 고친다
"""
import json
import re
from datetime import date, timedelta

from finance.merchant_rules import normalize_merchant

from . import result as R
from .analyze import _items
from .answer import NAMES, _plain, josa, scope, span, won
from .coverage import coverage
from .periods import Period

TOP = 3  # 가맹점 · 카테고리 증감은 변화 방향이 같은 큰 순서로 이만큼만
TEXT = {
    "head_up": "{name} 지출은 {previous}보다 {delta} 늘었어요. ↑ {rate}%",
    "head_down": "{name} 지출은 {previous}보다 {delta} 줄었어요. ↓ {rate}%",
    "head_from_zero": "{name} 지출은 {previous}엔 0원이었고 {current}에 {delta} 썼어요.",
    "head_same": "{name} 지출은 {previous}과 같아서 설명할 증감이 없어요.",
    "basis": "아래는 거래 내역에서 확인한 변화예요. 이것만으로 이유를 단정할 수는 없어요.",
    "count": "결제 건수는 {previous} {before}건, {current} {after}건이에요.",
    "average": "건당 평균 결제액은 {previous} {before}, {current} {after}이에요.",
    "merchants": "가맹점별로는 {items} {direction}어요.",
    "categories": "카테고리별로는 {items} {direction}어요.",
    "unnamed": "가맹점 이름이 없는 거래는 가맹점별 변화에서 뺐어요.",
    "instead_of": "{name} 기준이라 {word}만의 변화는 알 수 없어요.",
}
REFUSE = {
    "no_basis": "이 결과는 비교한 기간이 없어 무엇이 늘거나 줄었는지 설명할 수 없어요. 지난달 비교나 최근 추이를 본 뒤 물어봐 주세요.",
    "incomplete": "다시 확인해 보니 그 기간 내역이 다 모이지 않아 근거를 만들 수 없어요.",
    "changed": "그 사이 내역이 바뀌어 앞의 결과로는 설명할 수 없어요. 다시 조회한 뒤 물어봐 주세요.",
    "no_pair": "이웃한 두 달 내역이 모두 모이지 않아 비교 근거를 만들 수 없어요.",
}
# 설명 LLM 출력에서 막는 표현 — 숫자는 서버 값만, 소비 동기 · 습관 · 평가 추측 금지 (완전한 차단이 아니라 최소 검사)
BANNED = ("습관", "과소비", "아마", "것 같", "때문일", "탓", "충동", "낭비")
PROMPT = """너는 가계부 앱의 소비 증감 설명 문장을 쓴다. 입력 facts 는 서버가 계산한 근거 목록이다 [{key, label}].
규칙: 1~3문장. 숫자 · 금액 · 비율을 직접 쓰지 말고 반드시 {key} 자리표시로만 쓴다 (예: "{F2}").
facts 에 없는 사실을 더하지 않는다. 소비 이유 · 동기 · 생활 습관을 추측하거나 평가하지 않는다.
입력의 scope 가 실제 조회 범위다. requested(사용자가 원래 말한 세부 지출)가 있으면 근거는 scope 전체의 것이므로
requested 만의 변화나 원인처럼 쓰지 않는다 (requested 라는 말을 문장에 쓰지 않는다).
JSON 하나로만 답한다: {"sentences": ["...", "..."]}"""
# 자리표시 바로 뒤 조사 → 받침에 맞는 쌍 (뒤가 띄어쓰기 · 문장부호 · 끝일 때만 — '이에요' 같은 서술은 건드리지 않는다)
PARTICLES = {p: pair for pair in (("을", "를"), ("은", "는"), ("이", "가"), ("과", "와"), ("으로", "로")) for p in pair}
PLACED = re.compile(r"\{(F\d+)\}(으로|로|을|를|은|는|이|가|과|와)?(?=[\s.,!?]|$)|\{(F\d+)\}")


def _period(value):
    return Period(date.fromisoformat(value["from"]), date.fromisoformat(value["to"]) + timedelta(days=1))


def _basis(item):
    """확정 분석 항목 → (지금 기간, 비교 기간, 이름표, 기록된 금액) 또는 거절 사유"""
    facts, code = item["facts"], item.get("category")
    if item["type"] in (6, 7):
        amount = (lambda fig: fig["category"]["amount"] if code else fig["total"])  # noqa: E731
        return (_period(item["period"]), _period(item["compare_period"]), ("이번 달", "지난달 같은 기간"),
                (amount(facts["current"]), amount(facts["previous"])))
    if item["type"] in (8, 9):
        series = facts["series"]
        if len(series) < 2 or series[-1]["confirmed"] is None or series[-2]["confirmed"] is None:
            return "no_pair"
        b, a = series[-1], series[-2]
        # 이번 달 포함 추이는 달마다 같은 날짜까지라 실제 날짜 범위로 이름 붙인다
        name = span if item.get("include_current") else (lambda p: f"{p.start.month}월")  # noqa: E731
        names = (name(_period(b["period"])), name(_period(a["period"])))
        return _period(b["period"]), _period(a["period"]), names, (b["confirmed"], a["confirmed"])
    return "no_basis"


def _side(user, period, code):
    """한 기간 · 카테고리의 소비 거래 집계. 완전 수집이 아니면 None"""
    if coverage(user, period)["state"] != "complete":
        return None
    rows = [r for r in _items(user, period) if r["transaction_type"] == "spending" and (code is None or r["category"] == code)]
    total = sum(r["net_amount"] for r in rows)
    paid = [r for r in rows if r["net_amount"] > 0]
    merchants, categories = {}, {}
    for r in rows:
        name = normalize_merchant(r["merchant_name"])
        merchants[name] = merchants.get(name, 0) + r["net_amount"]
        categories[r["category"]] = categories.get(r["category"], 0) + r["net_amount"]
    return {"amount": total, "count": len(paid), "average": R.average(sum(r["net_amount"] for r in paid), len(paid)),
            "merchants": merchants, "categories": categories}


def _movers(now, before, sign):
    """증감 방향(sign)이 같은 항목을 큰 순서로 TOP 개. 같은 크기면 이름 순 (일관된 순서)"""
    deltas = {k: now.get(k, 0) - before.get(k, 0) for k in set(now) | set(before) if k is not None}
    picked = sorted(((k, d) for k, d in deltas.items() if d * sign > 0), key=lambda kd: (-abs(kd[1]), str(kd[0])))
    return [{"key": k, "delta": d} for k, d in picked[:TOP]]


def evidence(user, item):
    """확정 분석 항목 하나 → {"state": "ok", ...근거} 또는 {"state": 거절 사유}"""
    basis = _basis(item)
    if isinstance(basis, str):
        return {"state": basis}
    current, previous, labels, recorded = basis
    code = item.get("category")
    now, before = _side(user, current, code), _side(user, previous, code)
    if now is None or before is None:
        return {"state": "incomplete"}
    if (now["amount"], before["amount"]) != recorded:
        return {"state": "changed"}
    change = R.compare(now["amount"], before["amount"], True, True)
    sign = (change["delta"] > 0) - (change["delta"] < 0)
    return {"state": "ok", "type": item["type"], "category": code, "labels": labels,
            "current": {k: now[k] for k in ("amount", "count", "average")},
            "previous": {k: before[k] for k in ("amount", "count", "average")},
            "delta": change["delta"], "rate": change["rate"],
            "merchants": _movers(now["merchants"], before["merchants"], sign) if sign else [],
            "unnamed": None in now["merchants"] or None in before["merchants"],
            "categories": _movers(now["categories"], before["categories"], sign) if sign and code is None else []}


def facts(ev):
    """근거 → 문장 재료 [{key, label, text}]. text 는 서버가 만든 표기 그대로 (LLM 에는 label · key 만)"""
    cur, prev = ev["labels"]
    out = [("지출 증감", won(abs(ev["delta"]))), (f"{prev} 결제 건수", f"{ev['previous']['count']}건"),
           (f"{cur} 결제 건수", f"{ev['current']['count']}건")]
    if ev["previous"]["average"] and ev["current"]["average"]:
        out += [(f"{prev} 건당 평균 결제액", won(ev["previous"]["average"]["value"])),
                (f"{cur} 건당 평균 결제액", won(ev["current"]["average"]["value"]))]
    out += [(f"가맹점 '{m['key']}' 증감", won(abs(m["delta"]))) for m in ev["merchants"]]
    out += [(f"카테고리 '{NAMES[c['key']]}' 증감", won(abs(c["delta"]))) for c in ev["categories"]]
    return [{"key": f"F{i}", "label": label, "text": text} for i, (label, text) in enumerate(out, 1)]


def _head(ev, name):
    cur, prev = ev["labels"]
    if ev["rate"] is None:
        return TEXT["head_from_zero"].format(name=name, previous=prev, current=cur, delta=won(ev["delta"]))
    if ev["delta"] == 0:
        return TEXT["head_same"].format(name=name, previous=prev)
    key = "head_up" if ev["delta"] > 0 else "head_down"
    return TEXT[key].format(name=name, previous=prev, delta=won(abs(ev["delta"])), rate=_plain(ev["rate"]))


def _template(ev):
    cur, prev = ev["labels"]
    lines = [TEXT["count"].format(previous=prev, current=cur, before=ev["previous"]["count"], after=ev["current"]["count"])]
    if ev["previous"]["average"] and ev["current"]["average"]:
        lines.append(TEXT["average"].format(previous=prev, current=cur, before=won(ev["previous"]["average"]["value"]),
                                            after=won(ev["current"]["average"]["value"])))
    direction = "늘었" if ev["delta"] > 0 else "줄었"
    if ev["merchants"]:
        items = ", ".join(f"{m['key']} {won(abs(m['delta']))}" for m in ev["merchants"])
        lines.append(TEXT["merchants"].format(items=items, direction=direction))
    if ev["categories"]:
        items = ", ".join(f"{NAMES[c['key']]} {won(abs(c['delta']))}" for c in ev["categories"])
        lines.append(TEXT["categories"].format(items=items, direction=direction))
    return lines


def checked(raw, table):
    """설명 LLM 응답 → 서버 값을 끼운 문장 목록. 형식 · 숫자 · 모르는 키 · 금지 표현 · 자리표시 없는 문장이면 None"""
    try:
        sentences = json.loads(raw)["sentences"]
    except Exception:  # JSON 아님 · 칸 없음
        return None
    if not isinstance(sentences, list) or not 1 <= len(sentences) <= 3:
        return None
    out = []
    for s in sentences:
        if not isinstance(s, str) or re.search(r"[0-9%]", re.sub(r"\{F\d+\}", "", s)) or any(b in s for b in BANNED):
            return None
        keys = re.findall(r"\{(F\d+)\}", s)
        if not keys or any(k not in table for k in keys) or re.search(r"\{(?!F\d+\})", s):
            return None
        out.append(PLACED.sub(lambda m: _place(table[m.group(1) or m.group(3)], m.group(2)), s))
    return out


def _place(value, particle):
    return value + (josa(value, PARTICLES[particle]) if particle else "")


def answer(user, items, complete=None):
    """설명 요청의 확정 항목들 → {"lines", "notices", "explain": {llm, rejected, failed}} (항목마다 근거 → 문장)
    llm = 설명 LLM 을 쓰게 했는지, rejected = 응답을 검사에서 버림, failed = 호출 실패 · 상한 (둘 다 템플릿으로 답함, 개발 표시용)"""
    lines, rejected, failed = [], False, False
    for item in items:
        ev = evidence(user, item)
        name = scope(item)  # 세부 지출 대신 본 항목은 "식비 전체"
        if ev["state"] != "ok":
            lines.append(REFUSE[ev["state"]])
            continue
        lines.append(_head(ev, name))
        if ev["delta"] == 0:
            continue
        lines.append(TEXT["basis"])
        body = None
        if complete is not None:
            table = facts(ev)
            payload = {"scope": name, "requested": item.get("instead_of"), "direction": "up" if ev["delta"] > 0 else "down",
                       "facts": [{"key": f["key"], "label": f["label"]} for f in table]}
            try:
                raw = complete(PROMPT, json.dumps(payload, ensure_ascii=False))
            except Exception:  # 호출 오류 · 상한 — 종류를 모른다, 템플릿으로 (재시도 없음)
                failed = True
            else:
                body = checked(raw, {f["key"]: f["text"] for f in table})
                rejected = rejected or body is None
        lines += body or _template(ev)
        if item.get("instead_of"):
            lines.append(TEXT["instead_of"].format(name=name, word=item["instead_of"]))
        if ev["unnamed"] and ev["merchants"]:
            lines.append(TEXT["unnamed"])
    return {"lines": lines, "notices": [], "explain": {"llm": complete is not None, "rejected": rejected, "failed": failed}}
