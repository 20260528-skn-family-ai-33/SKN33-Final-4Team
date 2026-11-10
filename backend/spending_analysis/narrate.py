"""6 · 7번(지난달 비교) 결과 → 서버 사실 문장 표 (추가 판단 LLM 에 보낼 관찰 결과, MEMO 31)

- 숫자가 든 문장은 서버만 만든다 (answer 템플릿 그대로). LLM 에는 문장 종류 · 대상 · 방향 · 숫자를 가린 문장만(for_llm) 보낸다
- 예전 답변 조합(LLM 이 이 문장을 골라 카드 아래에 잇던 것, 11/10)은 MEMO 31 에서 없앴다 — 답은 서버 카드 · 문장만
"""
import re

from .answer import TEXT, _amount, change_sentence, relabel, span, won
from .scope import filter_label, name as scope

TYPES = (6, 7)
SAID = {
    "previous": "지난달 같은 기간({period})에는 {name}에 {amount} 썼어요.",
    "previous_total": "지난달 같은 기간({period}) 소비는 {amount}이었어요.",
}


def _comparison(res):
    return res["comparison"]["category" if res["item"].get("category") else "total"]


def applies(results):
    """사실 표를 만들 수 있는지: 모든 항목이 6 · 7번이고 모두 조회에 성공했으며 비교할 수 있을 때만 (아니면 답 문장 한 줄씩)"""
    return bool(results) and all(r["ok"] and r["result"]["type"] in TYPES and _comparison(r["result"])["available"]
                                 for r in results)


def _now(res, name):
    """이번 달 금액 문장 — 확정 · 일부 내역 · 내역 없음 (answer 템플릿 그대로)"""
    item, cur = res["item"], res["current"]
    period, seen = f"이번 달({span(cur['period'])})", _amount(cur["observed"], item)
    if cur["confirmed"] is None and cur["coverage"]["state"] == "none" and not seen:
        return TEXT["no_data"].format(period=period, name=name)
    if cur["confirmed"] is None:
        return TEXT["observed"].format(period=period, name=name, amount=won(seen))
    if name == "전체":
        return TEXT["total"].format(period=period, amount=won(seen))
    return TEXT["category"].format(period=period, name=name, amount=won(seen))


def said_name(item):
    """문장에 넣는 범위 이름. 카드 제목은 '식비 · 저녁'이지만 문장에서는 '저녁 식비' ('주말 · 저녁'이 같이 있으면 '주말 저녁 식비')"""
    name, extra = scope(item), filter_label(item)
    if extra and name.endswith(" · " + extra):
        return f"{extra.replace(' · ', ' ')} {name[:-len(' · ' + extra)]}"
    return name


def facts(results):
    """조회 결과 → 서버 사실 문장 [{id, kind, subject, direction, must, text}] (항목 순서대로)"""
    out = []

    def add(kind, subject, text, direction=None, must=False):
        out.append({"id": f"F{len(out) + 1}", "kind": kind, "subject": subject, "direction": direction, "must": must,
                    "text": relabel(text, res)})  # 달끼리 비교면 '지난달' → '9월'

    res = None

    for r in results:  # applies() 를 통과한 결과 = 모두 비교 가능
        res = r["result"]
        item, name = res["item"], said_name(res["item"])
        c = _comparison(res)
        subject = "전체 소비는" if name == "전체" else f"{name} 지출은"
        direction = "up" if c["delta"] > 0 else "down" if c["delta"] < 0 else "same"
        # 증감률(↑ 12.07%)은 카드의 큰 숫자 · 배지에 이미 있어 문장에서는 뺀다 (11/10 사용자 확인). 금액 · 방향은 그대로
        change = re.sub(r"\s*[↑↓] [0-9.]+%$", "", change_sentence(res))
        add("change", name, f"{subject} {change}", direction, must=True)
        add("this_month", name, _now(res, name))
        before, period = _amount(res["previous"]["confirmed"], item), span(res["previous"]["period"])
        add("last_month", name, SAID["previous_total"].format(period=period, amount=won(before)) if name == "전체"
            else SAID["previous"].format(period=period, name=name, amount=won(before)))
    return out


def for_llm(table):
    """LLM 에 보낼 모양 — 금액 · 날짜 없이. preview = 문장에서 숫자를 □로 가린 것 (대상 이름이 문장에 이미 있다는 걸 보이려고)"""
    return [{**{k: f[k] for k in ("id", "kind", "subject", "direction", "must")},
             "preview": re.sub(r"[0-9][0-9,.]*", "□", f["text"])} for f in table]
