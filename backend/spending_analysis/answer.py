"""소비 분석 결과 → 답변 문장 (LLM 없음). 숫자는 서버 결과를 형식만 바꿔 넣고 새로 계산하지 않는다
- 템플릿은 유형 · 상황별 본문 하나씩 + 공통 안내. 카테고리 · 금액마다 따로 만들지 않는다
- 공통 안내는 여러 항목에서 같은 문장이 나오면 한 번만
- 기간: 달력월 "이번 달(10월 1일~29일)" · 비교 "지난달 같은 기간(9월 1일~29일)" · 추이 "7월"
  예산은 주기 전체와 사용액을 센 기간을 따로 적는다 "이번 예산 기간은 10월 25일~11월 24일이고, 10월 29일까지 쓴 금액으로 …"
- 공통 기간 · 기준은 답변 맨 앞에 한 번만(head), 지난달 비교는 카테고리마다 묶음(sections: {title, lines})으로
- 답을 바꾸는 제한(개월 상한 · 비교 불가 · 대체 범위)은 핵심 안내(lead)로 결과보다 먼저, 나머지 공통 안내는 notices
- 금액은 원 단위 그대로(쉼표만). chat.md 예시의 '만 원' 줄임 표기는 반올림이 들어가므로 쓰지 않는다(표기 정책 미정)
입력은 adapter.run 결과(analyze 원본 값) — 기록용 JSON 요약이 아니다.
"""
from importlib import import_module

from .result import CATEGORY_ORDER

NAMES = {code: name for code, name, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES}

TEXT = {
    "total": "{period} 소비는 {amount}이에요.",
    "fixed": "고정지출 {fixed}을 빼면 생활비는 {living}이에요.",
    "category": "{period} {name}에 {amount} 썼어요.",
    "share": "전체 소비 {total} 중 {share}%예요.",
    "top": "분류된 소비 중 가장 많이 쓴 곳은 {names}, {amount}이에요. 전체 소비 {total}의 {share}%예요.",
    "top_tied": "분류된 소비 중 가장 많이 쓴 곳은 {names}, 각각 {amount}이에요. 각각 전체 소비 {total}의 {share}%예요.",
    "observed": "{period} {name} 지출은 일부 내역 기준 {amount}이에요.",
    "no_data": "{period} {name} 지출은 확인할 수 있는 내역이 없어요.",
    "no_record": "{period} {name} 지출은 수집 기록이 없어 전체 금액을 알 수 없어요. 기록된 거래는 {amount}이에요.",
    "budget_head": "이번 예산 기간은 {cycle}이고, {until}까지 쓴 금액으로 계산했어요.",
    "budget_left": "{name} 예산 {allocated} 중 {spent}을 썼고 {remaining} 남았어요.",
    "budget_over": "{name} 예산 {allocated} 중 {spent}을 써서 {over} 초과했어요.",
    "spare_cover": "초과한 {covered}은 여윳돈으로 메울 수 있어요. 남는 여윳돈은 {spare}이에요.",
    "spare_short": "여윳돈으로 메워도 {shortfall}이 모자라요.",
    "budget_unconfirmed": "{name} 예산은 {allocated}이고 일부 내역 기준 사용액은 {spent}이에요. 남은 금액은 확정할 수 없어요.",
    "budget_no_line": "승인된 예산에 {name} 배정이 없어서 남은 금액을 알려드릴 수 없어요.",
    "spent_only": "{name} 사용액은 {spent}이에요.",
    "no_approved": "아직 승인된 예산이 없어서 {name} 남은 예산을 알려드릴 수 없어요.",
    "no_cycle": "예산 기간 정보가 없어서 {name} 남은 예산을 알려드릴 수 없어요.",
    "compare_head": "이번 달({current}) 소비",
    "compare_basis": "지난달 같은 기간({previous})과 비교했어요.",
    "compare_title": "{name} · {amount}",
    "compare_title_observed": "{name} · 일부 내역 기준 {amount}",
    "compare_title_none": "{name} · 확인할 수 있는 내역이 없어요",
    "compare_up": "지난달보다 {delta} 증가했어요. ↑ {rate}%",
    "compare_down": "지난달보다 {delta} 감소했어요. ↓ {rate}%",
    "compare_same": "지난달과 같아요.",
    "compare_from_zero": "지난달엔 0원이라 증감률 없이 금액으로 알려드려요. {delta} 증가했어요.",
    "trend_head": "최근 {n}개월 {name} 지출이에요.",
    "trend_head_to_date": "이번 달을 포함한 최근 {n}개월 {name} 지출이에요. 달마다 같은 날짜까지 맞춰 비교했어요.",
    "compare_reference": "참고로 이번 달({current}) 금액이에요.",
    "trend_month": "{month} {amount}",
    "trend_observed": "{month} {amount} (일부 내역 기준)",
    "trend_no_record": "{month} {amount} (수집 기록이 없어 기록된 거래만)",
    "trend_none": "{months}은 확인할 수 있는 내역이 없어요.",
    "trend_all_none": "최근 {n}개월 {name} 지출은 확인할 수 있는 내역이 없어요.",
    "trend_average": "{months} {count} 평균은 {approx}{amount}이에요.",
    "trend_up": "{month}에는 {previous}보다 {delta}, {approx}{rate}% 더 썼어요.",
    "trend_down": "{month}에는 {previous}보다 {delta}, {approx}{rate}% 덜 썼어요.",
    "trend_same": "{month}에는 {previous}과 같은 금액을 썼어요.",
    "trend_from_zero": "{previous}엔 0원이라 증감률 없이 금액으로 알려드려요. {month}에는 {delta} 더 썼어요.",
    "trend_excluded": "{months}은 내역이 다 모이지 않아 평균 · 증감 계산에서 뺐어요.",
    "trend_too_few": "내역이 다 모인 달이 두 달보다 적어 평균과 증감은 계산하지 않았어요.",
    "failed": "{name} {what} 결과를 불러오지 못했어요.",
}
NOTICE = {
    "partial": "일부 내역이 빠져 있을 수 있어요. 현재 확인된 내역으로 계산했어요.",
    "previous_incomplete": "지난달 같은 기간의 내역이 충분하지 않아 비교하기 어려워요.",
    "current_incomplete": "이번 달 내역이 충분하지 않아 비교하기 어려워요.",
    "instead_of": "{word}에 쓴 돈만 따로 구분할 수 없어 {name} 전체로 조회했어요.",
    "unclassified": "카테고리가 아직 정해지지 않은 거래 {count}건, 총 {amount}도 포함했어요.",
    "months_capped": "최근 {n}개월까지만 볼 수 있어요. 최근 {n}개월 기준으로 알려드릴게요.",
    "retry": "불러오지 못한 항목은 잠시 후 다시 물어봐 주세요.",
}
CHART = {
    "title": "카테고리별 소비 보기",
    "basis": "{period} 전체 소비 {total} 기준 (카테고리 금액 ÷ 전체 소비)",
    "limited": "일부 내역 기준이에요. 빠진 내역이 있으면 금액과 비율이 달라질 수 있어요.",
    "zero": "이번 달 소비가 0원이라 나눌 항목이 없어요.",
    "none": "확인할 수 있는 내역이 없어요.",
}
WHAT = {1: "소비", 2: "소비", 3: "남은 예산", 4: "남은 예산", 5: "소비 분석", 6: "지난달 비교", 7: "지난달 비교", 8: "추이", 9: "추이"}


def josa(word, pair):
    """받침에 맞는 조사: ("을", "를") · ("은", "는") · ("이", "가") · ("과", "와") · ("으로", "로"). 한글로 끝나지 않으면 둘 다 적는다"""
    last = word[-1:]
    if not "가" <= last <= "힣":
        return f"{pair[0]}({pair[1]})"
    final = (ord(last) - 0xAC00) % 28
    if pair[1] == "로":
        return "로" if final in (0, 8) else "으로"  # ㄹ 받침은 '로'
    return pair[0] if final else pair[1]


def won(n):
    return f"{n:,}원"


def span(p):
    """Period → '10월 1일~29일' (달이 다르면 '9월 25일~10월 24일')"""
    a, b = p.start, p.last_day
    return f"{a.month}월 {a.day}일~{b.day}일" if (a.year, a.month) == (b.year, b.month) else \
        f"{a.month}월 {a.day}일~{b.month}월 {b.day}일"


def day(d):
    return f"{d.month}월 {d.day}일"


def months_label(periods):
    """[7월, 8월] → '7·8월' (해가 바뀌어도 월만 — 최근 3개월 안이라 겹치지 않는다)"""
    return "·".join(str(p.start.month) for p in periods) + "월"


COUNT = {2: "두 달", 3: "세 달"}


def approx(exact):
    """반올림한 값이면 '약 ' (나누어떨어지면 붙이지 않는다)"""
    return "" if exact["numerator"] % exact["denominator"] == 0 else "약 "


LEAD = ("previous_incomplete", "current_incomplete", "instead_of", "months_capped")  # 결과보다 먼저 보일 핵심 안내


def scope(item):
    """실제 조회 범위 이름. 세부 지출 대신 카테고리 전체를 본 항목은 '식비 전체'"""
    if item.get("instead_of"):
        return f"{NAMES[item['category']]} 전체"
    return NAMES[item["category"]] if item.get("category") else ("생활비" if item["type"] == 4 else "전체")


def _amount(fig, item):
    return fig["category"]["amount"] if item.get("category") else fig["total"]


def _plain(rate):
    return str(rate["value"]).lstrip("-")


class _Out:
    def __init__(self):
        self.lines, self.notices, self.sections, self.lead = [], [], [], []

    def line(self, key, **values):
        self.lines.append(TEXT[key].format(**values))

    def head(self, key, **values):
        """여러 항목에 공통인 기간 · 기준 문장은 한 번만"""
        text = TEXT[key].format(**values)
        if text not in self.lines:
            self.lines.append(text)

    def section(self, title, *lines):
        self.sections.append({"title": title, "lines": list(lines)})

    def notice(self, key, **values):
        """공통 안내는 한 번만. 핵심 안내(LEAD)는 lead 칸 — 화면이 문자열을 찾아 옮기지 않게 구조로 나눈다"""
        text, box = NOTICE[key].format(**values), self.lead if key in LEAD else self.notices
        if text not in box:
            box.append(text)


def _period_block(out, block, item, label):
    """관측 · 확정을 구분해 한 기간의 금액 문장. 확정이면 True"""
    name, state = scope(item), block["coverage"]["state"]
    seen = _amount(block["observed"], item)
    if block["confirmed"] is not None:
        return True
    if state == "none":
        out.line("no_record" if seen else "no_data", period=label, name=name, amount=won(seen))
    else:
        out.line("observed", period=label, name=name, amount=won(seen))
        out.notice("partial")
    return False


def _unclassified(out, fig, item):
    """'확인 필요' 금액은 전체 합계에 들어 있으므로 전체 범위 답변에만 안내한다"""
    if fig["unclassified_amount"] and not item.get("category"):
        out.notice("unclassified", count=fig["needs_review_count"], amount=won(fig["unclassified_amount"]))


def _current(out, result):
    item, block = result["item"], result["current"]
    label = f"이번 달({span(block['period'])})"
    _unclassified(out, block["observed"], item)
    if not _period_block(out, block, item, label):
        return
    fig = block["confirmed"]
    if item.get("category"):
        out.line("category", period=label, name=scope(item), amount=won(fig["category"]["amount"]))
        if fig["category"]["share"]:
            out.line("share", total=won(fig["total"]), share=fig["category"]["share"]["value"])
        return
    out.line("total", period=label, amount=won(fig["total"]))
    if fig["fixed_amount"]:
        out.line("fixed", fixed=won(fig["fixed_amount"]), living=won(fig["living_amount"]))
    if result["type"] == 5 and fig["top_spending"]:
        top = fig["top_spending"]  # 동률이면 모두 (같은 금액이라 비중도 같다). 비중 분모 = 고정지출 · 확인 필요를 포함한 전체 소비
        out.line("top_tied" if len(top) > 1 else "top", names="·".join(NAMES[t["category"]] for t in top),
                 amount=won(top[0]["amount"]), total=won(fig["total"]), share=fig["shares"][top[0]["category"]]["value"])


def chart(result):
    """5번 전반 분석의 카테고리별 막대 자료 (서버 금액 · 비율 그대로). 금액 내림차순, 동률은 카테고리 순서.
    확인 필요(UNCLASSIFIED)도 한 줄. 부분 수집이면 관측값 + 제한 표시. 예산 기간과 달라 예산 초과 표시는 넣지 않는다"""
    block = result["current"]
    fig, limited = block["confirmed"] or block["observed"], block["confirmed"] is None
    out = {"title": CHART["title"], "rows": [], "limited": CHART["limited"] if limited else None, "empty": None,
           "basis": CHART["basis"].format(period=f"이번 달({span(block['period'])})", total=won(fig["total"]))}
    if not fig["total"]:
        out["empty"] = CHART["none"] if block["coverage"]["state"] == "none" else CHART["zero"]
        return out
    for code in sorted(fig["by_category"], key=lambda c: (-fig["by_category"][c], CATEGORY_ORDER.index(c))):
        share = fig["shares"][code]["value"]
        out["rows"].append({"code": code, "name": NAMES[code], "amount": fig["by_category"][code], "share": str(share),
                            "text": f"{NAMES[code]} {won(fig['by_category'][code])} ({share}%)"})
    return out


def _budget(out, result):
    item, budget, name = result["item"], result["budget"], scope(result["item"])
    if budget["state"] == "no_cycle":
        return out.line("no_cycle", name=name)
    head = dict(cycle=span(result["cycle_period"]), until=day(result["spent_period"].last_day))
    if budget["state"] == "no_approved":
        out.line("no_approved", name=name)
        if result["coverage"]["state"] != "none":
            out.head("budget_head", **head)
            out.line("spent_only", name=name, spent=won(result["observed"]["spent"]))
            if result["coverage"]["state"] != "complete":
                out.notice("partial")
        return
    if budget["allocated"] is None:
        return out.line("budget_no_line", name=name)
    out.head("budget_head", **head)
    done = result["confirmed"]
    if done is None:
        out.line("budget_unconfirmed", name=name, allocated=won(budget["allocated"]),
                 spent=won(result["observed"]["spent"]))
        return out.notice("partial")
    values = dict(name=name, allocated=won(done["allocated"]), spent=won(done["spent"]))
    if done["over"]:
        out.line("budget_over", over=won(done["over"]), **values)
        if done.get("covered_by_spare"):
            out.line("spare_cover", covered=won(done["covered_by_spare"]), spare=won(done["spare_remaining"]))
        if done.get("shortfall"):
            out.line("spare_short", shortfall=won(done["shortfall"]))
    else:
        out.line("budget_left", remaining=won(done["remaining"]), **values)


def _compare(out, result):
    """공통 기간은 맨 앞에 한 번, 카테고리(전체)마다 '이름 · 금액' + 증감 문장 묶음. 증감은 화살표만이 아니라 문장으로"""
    item, cur, prev = result["item"], result["current"], result["previous"]
    name = scope(item)
    c = result["comparison"]["category" if item.get("category") else "total"]
    _unclassified(out, cur["observed"], item)
    seen = _amount(cur["observed"], item)
    if not c["available"]:  # 비교 못 함이 핵심(lead), 이번 달 금액은 참고. '비교했어요' 라고 쓰지 않는다
        out.head("compare_reference", current=span(cur["period"]))
        if cur["confirmed"] is not None:
            title = TEXT["compare_title"].format(name=name, amount=won(seen))
        elif cur["coverage"]["state"] == "none" and not seen:
            title = TEXT["compare_title_none"].format(name=name)
        else:
            title = TEXT["compare_title_observed"].format(name=name, amount=won(seen))
        out.section(title)
        out.notice("previous_incomplete" if c["previous_incomplete"] else "current_incomplete")
        return
    if c["rate"] is None:  # 이전 0원: 서버의 금액 차이만
        change = TEXT["compare_from_zero"].format(delta=won(c["delta"]))
    elif c["delta"] > 0:
        change = TEXT["compare_up"].format(delta=won(c["delta"]), rate=_plain(c["rate"]))
    elif c["delta"] < 0:
        change = TEXT["compare_down"].format(delta=won(-c["delta"]), rate=_plain(c["rate"]))
    else:
        change = TEXT["compare_same"]
    out.head("compare_head", current=span(cur["period"]))
    out.head("compare_basis", previous=span(prev["period"]))
    out.section(TEXT["compare_title"].format(name=name, amount=won(seen)), change)


def _trend(out, result):
    """금액이 있는 달(확정 · 일부 내역)은 달마다, 확인할 내역이 없는 달은 한 문장으로 묶는다 (0원으로 쓰지 않는다)"""
    item, series = result["item"], result["series"]
    empty = [s["period"] for s in series if s["confirmed"] is None and s["state"] != "partial" and not s["observed"]]
    if len(empty) == len(series):
        return out.line("trend_all_none", n=len(series), name=scope(item))
    to_date = bool(item.get("include_current"))
    out.line("trend_head_to_date" if to_date else "trend_head", n=len(series), name=scope(item))
    for s in series:
        month = span(s["period"]) if to_date else f"{s['period'].start.month}월"  # 포함이면 실제 날짜 범위
        if s["confirmed"] is not None:
            out.line("trend_month", month=month, amount=won(s["confirmed"]))
        elif s["state"] == "partial":
            out.line("trend_observed", month=month, amount=won(s["observed"]))
        elif s["observed"]:
            out.line("trend_no_record", month=month, amount=won(s["observed"]))
    if empty:
        out.line("trend_none", months=months_label(empty))
    _stats(out, result["stats"], len(series), to_date)


def _stats(out, stats, n, to_date=False):
    """완전 수집 월만으로 계산한 서버 값 그대로. 평가(과소비 등)는 붙이지 않는다"""
    if n < 2:
        return
    if stats["average"] is None and not stats["changes"]:
        return out.line("trend_too_few")
    if stats["average"] is not None:
        avg = stats["average"]
        out.line("trend_average", months=months_label(stats["used"]) + (" 같은 기간" if to_date else ""),
                 count=COUNT[len(stats["used"])],
                 approx=approx(avg["exact"]), amount=won(avg["value"]))
    for c in stats["changes"]:
        same = " 같은 기간" if to_date else ""
        names = dict(month=f"{c['current'].start.month}월", previous=f"{c['previous'].start.month}월{same}")
        if c["rate"] is None:
            out.line("trend_from_zero", delta=won(c["delta"]), **names)
        elif c["delta"]:
            out.line("trend_up" if c["delta"] > 0 else "trend_down", delta=won(abs(c["delta"])),
                     approx=approx(c["rate"]["exact"]), rate=_plain(c["rate"]), **names)
        else:
            out.line("trend_same", **names)
    if stats["excluded"]:
        out.line("trend_excluded", months=months_label([e["period"] for e in stats["excluded"]]))


BODY = {1: _current, 2: _current, 5: _current, 3: _budget, 4: _budget, 6: _compare, 7: _compare, 8: _trend, 9: _trend}


def compose(results, notices=()):
    """adapter.run 결과(+ 라우터 Decision.notices) → {"lines": 본문, "notices": 공통 안내(중복 없음)}"""
    out = _Out()
    for r in results:
        if r["item"].get("instead_of"):
            out.notice("instead_of", word=r["item"]["instead_of"], name=NAMES[r["item"]["category"]])
        if not r["ok"]:
            out.line("failed", name=scope(r["item"]), what=WHAT[r["item"]["type"]])
            out.notice("retry")
            continue
        BODY[r["result"]["type"]](out, r["result"])
    if "months_capped" in notices:
        applied = next((r["item"]["months_applied"] for r in results if r["item"].get("months_applied")), None)
        if applied:
            out.notice("months_capped", n=applied)
    reply = {"lines": out.lines, "notices": out.notices}
    if out.lead:
        reply["lead"] = out.lead
    if out.sections:
        reply["sections"] = out.sections
    done = [r["result"] for r in results if r["ok"]]
    if any(r["type"] == 5 for r in done):
        reply["chart"] = chart(next(r for r in done if r["type"] == 5))
    if any((r.get("confirmed") or {}).get("over") for r in done if r["type"] in (3, 4)):
        reply["suggest"] = ["budget_adjust"]  # 초과 뒤 예산 조정 제안 (문구 · 선택지는 라우터 안내가 붙인다)
    return reply
