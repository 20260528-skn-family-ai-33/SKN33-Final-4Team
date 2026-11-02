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

from .periods import Period
from .result import CATEGORY_ORDER, percent
from .scope import ONLINE, SHORT_PERIODS, filters, name as scope, label as _name, narrowed, online

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
    "budget_daily": "남은 {days}일 동안 하루 {daily}씩 쓸 수 있어요.",
    "budget_category": "{name} {allocated} 중 {spent} 사용 · {rest}",
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
    # 범용 조회 (10번)
    "q_count": "{period} {who}결제는 {count}건이에요.",
    "q_avg": "{period} {who}결제는 건당 평균 {approx}{amount}이에요.",
    "q_daily": "{period} {who}하루 평균 {approx}{amount}을 썼어요(고정지출 포함, {days}일로 나눈 값).",
    "q_daily_living": "고정지출을 빼면 하루 평균 {approx}{amount}이에요.",
    "q_max": "{period} {who}가장 큰 지출은 {date} {merchant}에서 쓴 {amount}이에요.",
    "q_max_living": "고정지출을 빼면 가장 큰 지출은 {date} {merchant}에서 쓴 {amount}이에요.",
    "q_group_head": "{period} {who}{group}별로 보면 이래요.",
    "q_group_sum": "{name} {amount} ({count}건)",
    "q_group_count": "{name} {count}건 ({amount})",
    "q_none": "{period} {who}조건에 맞는 결제가 없어요.",
    "fixed_same_period": "같은 기간 고정지출 {amount}은 따로 뺐어요.",
}
GROUP_TITLE = {"category": "카테고리", "merchant": "가게", "weekday": "요일", "time_slot": "시간대", "month": "월"}
NOTICE = {
    "partial": "일부 내역이 빠져 있을 수 있어요. 현재 확인된 내역으로 계산했어요.",
    "previous_incomplete": "지난달 같은 기간의 내역이 충분하지 않아 비교하기 어려워요.",
    "current_incomplete": "이번 달 내역이 충분하지 않아 비교하기 어려워요.",
    "instead_of": "{word}만 따로 볼 수 없어 {name} 전체로 조회했어요.",  # 360px 폰에서도 한 줄 (카드 안내 칸)
    "merchant": "가게 이름에 '{word}'{particle} 들어간 결제만 모았어요.",
    "online": "{brands} 등 {group} 결제만 모았어요.",
    "online_fee": "앱 결제 금액이라 음식값과 배달비가 함께 들어 있어요.",
    "payment": "계좌 이체 · 자동이체는 빼고 카드 결제만 모았어요.",
    "payment_bank": "카드 결제는 빼고 계좌에서 나간 소비만 모았어요.",
    "no_time": "시각이 없는 거래 {count}건은 시간대 조회에서 뺐어요.",
    "clipped": "끝 날짜가 아직 지나지 않아 {until}까지만 봤어요.",
    "fixed_excluded": "고정지출 {amount}({names})은 빼고 봤어요.",
    "out_of_budget": "주거·통신, 여행·숙박은 예산 범위가 아니라 생활비 사용액에서 뺐어요.",
    "unclassified": "카테고리가 아직 정해지지 않은 거래 {count}건, 총 {amount}도 포함했어요.",
    "months_capped": "{n}개월까지만 볼 수 있어 {n}개월로 보여드려요.",  # 360px 폰에서도 한 줄 (기간은 카드 머리줄에)
    "retry": "불러오지 못한 항목은 잠시 후 다시 물어봐 주세요.",
}
CHART = {
    "title": "카테고리별 소비 보기",
    "basis": "{period} 전체 소비 {total} 기준 (카테고리 금액 ÷ 전체 소비)",
    "limited": "일부 내역 기준이에요. 빠진 내역이 있으면 금액과 비율이 달라질 수 있어요.",
    "zero": "이번 달 소비가 0원이라 나눌 항목이 없어요.",
    "none": "확인할 수 있는 내역이 없어요.",
}
WHAT = {1: "소비", 2: "소비", 3: "남은 예산", 4: "남은 예산", 5: "소비 분석", 6: "지난달 비교", 7: "지난달 비교", 8: "추이", 9: "추이",
        10: "소비"}


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
    if a == b:  # 하루짜리 기간
        return f"{a.month}월 {a.day}일"
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


LEAD = ("previous_incomplete", "current_incomplete", "instead_of", "months_capped", "merchant", "online")  # 결과보다 먼저 보일 핵심 안내


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
    if fig["unclassified_amount"] and not narrowed(item):
        out.notice("unclassified", count=fig["needs_review_count"], amount=won(fig["unclassified_amount"]))


def _current(out, result):
    item, block = result["item"], result["current"]
    label = f"이번 달({span(block['period'])})"
    _unclassified(out, block["observed"], item)
    if not _period_block(out, block, item, label):
        return
    fig = block["confirmed"]
    if narrowed(item):
        out.line("category", period=label, name=scope(item), amount=won(_amount(fig, item)))
        if item.get("category") and fig["category"]["share"] and not filters(item):  # 거른 조회면 분모가 달라 빼낸다
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
        if _daily(result):
            out.line("budget_daily", days=result["days_left"], daily=won(_daily(result)))
    rows = [TEXT["budget_category"].format(name=NAMES[c["category"]], allocated=won(c["allocated"]), spent=won(c["spent"]),
                                           rest=_rest(c)) for c in result.get("categories", [])]
    if rows:
        out.section("카테고리별 남은 예산", *rows)


def _daily(result):
    """남은 예산 ÷ 남은 일수 (원 미만 버림 — 넘지 않게). 초과 · 마지막 날이면 None"""
    done = result.get("confirmed")
    return done and done["remaining"] and result.get("days_left", 0) > 0 and done["remaining"] // result["days_left"]


def _rest(c):
    return f"{won(c['over'])} 초과" if c["over"] else f"{won(c['remaining'])} 남음"


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


def _q_period(result):
    """10번 기간 이름: 이번 달(10월 1일~29일) · 지난달(9월 1일~30일) · 최근 3개월(7월 1일~9월 30일)"""
    item, periods = result["item"], result["periods"]
    whole = Period(periods[0].start, periods[-1].end_before)
    kind = item["query"]["period"]
    if kind == "recent":
        head = f"이번 달을 포함한 최근 {len(periods)}개월" if item.get("include_current") else f"최근 {len(periods)}개월"
    elif kind == "range":
        return span(whole), whole
    elif kind == "month":
        head = f"{whole.start.month}월"
    else:
        head = {"this_month": "이번 달", "last_month": "지난달", "this_week": "이번 주", "last_week": "지난주"}[kind]
    return f"{head}({span(whole)})", whole


def _when(m):
    return f"{day(m['occurred_on'])}" + (f" {m['occurred_time']}" if m["occurred_time"] else "")


def _query(out, result):
    """10번 범용 조회 문장. 부분 수집이면 관측값 + 안내, 시간대 조건에서 뺀 시각 없는 거래 수도 안내"""
    item, q = result["item"], result["item"]["query"]
    label, _ = _q_period(result)
    who = f"{scope(item)} " if narrowed(item) else ""
    fig = result["confirmed"] or result["observed"]
    if result["coverage"]["state"] != "complete":
        out.notice("partial")
    if result["no_time"]:
        out.notice("no_time", count=result["no_time"])
    if result.get("clipped"):  # 끝 날짜가 기준일 뒤 → 기준일까지로 잘랐다
        out.notice("clipped", until=day(result["periods"][-1].last_day))
    if not fig["count"] and not fig["sum"]:  # 수집 기록이 없으면 0원 · 결제 없음이 아니라 확인할 내역이 없다
        return out.line("no_data", period=label, name=scope(item) if narrowed(item) else "소비") \
            if result["coverage"]["state"] == "none" else out.line("q_none", period=label, who=who)
    if q.get("group_by"):
        out.line("q_group_head", period=label, who=who, group=GROUP_TITLE[q["group_by"]])
        key = "q_group_count" if q["measure"] == "count" else "q_group_sum"
        for g in fig["groups"]:
            out.lines.append(TEXT[key].format(name=g["name"], amount=won(g["sum"]), count=g["count"]))
        return
    m = q["measure"]
    if m == "sum":
        return out.line("category", period=label, name=scope(item), amount=won(fig["sum"])) if narrowed(item) \
            else out.line("total", period=label, amount=won(fig["sum"]))
    if m == "count":
        return out.line("q_count", period=label, who=who, count=fig["count"])
    if m == "avg":
        return out.line("q_avg", period=label, who=who, approx=approx(fig["avg"]["exact"]), amount=won(fig["avg"]["value"]))
    if m == "daily_avg":
        out.line("q_daily", period=label, who=who, approx=approx(fig["daily_avg"]["exact"]),
                 amount=won(fig["daily_avg"]["value"]), days=result["days"])
        if fig["fixed"]:
            out.line("q_daily_living", approx=approx(fig["daily_avg_living"]["exact"]), amount=won(fig["daily_avg_living"]["value"]))
        return
    big = fig["max"]
    out.line("q_max", period=label, who=who, date=_when(big), merchant=big["merchant_name"] or "이름 없는 곳",
             amount=won(big["net_amount"]))
    if fig.get("max_living"):
        other = fig["max_living"]
        out.line("q_max_living", date=_when(other), merchant=other["merchant_name"] or "이름 없는 곳", amount=won(other["net_amount"]))


BODY = {1: _current, 2: _current, 5: _current, 3: _budget, 4: _budget, 6: _compare, 7: _compare, 8: _trend, 9: _trend,
        10: _query}


# ---------- 카드 보기 (view) ----------
# 화면이 카드로 그릴 짧은 구조값. 숫자는 위 문장과 같은 서버 결과 그대로(새 계산 없음 — 막대 길이 비율만 percent 로).
# 기간 · 범위는 머리줄(head)에 한 번만 두고 문장에서는 뺀다. lines(문장)는 그대로 남아 옛 화면 · text 칸이 쓴다.
# 블록: {kind: head, title} · {kind: total, caption?, amount, note?} · {kind: rows, title?, rows: [{name, amount | value(글자), note?, ratio?, badge?}]} ·
#       total.direction · rows[].direction = up | down | same (늘었어요 · 줄었어요 글자 색, 글자로도 적혀 있음)
#       {kind: text, lines} · {kind: meter, title, ratio, over, lines} (생활비 예산 사용률 막대 — 기간은 title 에 따로). badge = {text, direction: up | down | same} — 색만이 아니라 글자(증가 · 감소 · 변화 없음)로도
VIEW = {
    "partial": "일부 내역 기준",
    "no_record": "수집 기록이 없어 기록된 거래만",
    "none": "확인할 수 있는 내역이 없어요.",
    "share": "전체 소비 {total} 중 {share}%",
    "top": "분류된 소비 중 가장 많이 쓴 곳은 {names}{copula}.",
    "top_amount": "{amount} 전체 소비의 {share}%예요.",
    "top_tied": "각각 {amount} 전체 소비의 {share}%예요.",
    "budget_note": "예산 {allocated} 중 {spent} 사용 · {until}까지",
    "meter_title": "이번 예산 기간({cycle}) · 생활비",
    "meter_used": "{allocated} 중 {spent} 사용 ({ratio}%)",  # 두 줄: 사용 / 남음 · 초과 + 날짜
    "meter_left": "{remaining} 남음 · {until}까지",
    "meter_over": "{over} 초과 · {until}까지",
    "up": "{delta} 증가 ({rate}%)",
    "down": "{delta} 감소 ({rate}%)",
    "same": "변화 없음",
    "from_zero": "{delta} 증가 (지난달 0원)",
    "more": "지난달보다 더 쓴 금액",
    "less": "지난달보다 덜 쓴 금액",
    "even": "지난달과 차이",
    "rate_up": "{rate}% 늘었어요",
    "rate_down": "{rate}% 줄었어요",
}


def _head(title):
    return {"kind": "head", "title": title}


def _text(lines):
    return [{"kind": "text", "lines": lines}] if lines else []


def _lines(body, result):
    """구조로 못 나타내는 경우(예산 없음 · 확정 안 됨 등)는 그 결과의 문장을 그대로"""
    out = _Out()
    body(out, result)
    return out.lines


def _seen(block, item):
    """한 기간 금액 → (금액 또는 None, 메모). 확정이 아니면 '일부 내역 기준' 등"""
    amount = _amount(block["observed"], item)
    if block["confirmed"] is not None:
        return amount, None
    if block["coverage"]["state"] == "none":
        return (amount, VIEW["no_record"]) if amount else (None, None)
    return amount, VIEW["partial"]


def _meter(result):
    """1 · 5번 단독 답 아래 생활비 예산 막대. 승인 예산 · 확정 사용액이 있을 때만 (없으면 아무것도 안 붙임)"""
    budget = result.get("living_budget") or {}
    done = budget.get("confirmed")
    ratio = done and percent(done["spent"], done["allocated"])
    if not ratio:
        return []
    values = dict(allocated=won(done["allocated"]), spent=won(done["spent"]), ratio=ratio["value"],
                  remaining=won(done["remaining"]), over=won(done["over"]), until=day(budget["spent_period"].last_day))
    return [{"kind": "meter", "title": VIEW["meter_title"].format(cycle=span(budget["cycle_period"])),
             "ratio": str(ratio["value"]), "over": bool(done["over"]),
             "lines": [VIEW["meter_used"].format(**values), VIEW["meter_over" if done["over"] else "meter_left"].format(**values)]}]


def _view_current(results, many):
    first = results[0]
    item = first["item"]
    head = _head(f"{span(first['current']['period'])} · {scope(item) if not many and narrowed(item) else '소비'}")
    if many:  # 여러 카테고리를 물었으면 (일부가 실패해도) 줄 목록
        rows = []
        for r in results:
            amount, note = _seen(r["current"], r["item"])
            fig = r["current"]["confirmed"]
            share = fig and r["item"].get("category") and not filters(r["item"]) and fig["category"]["share"]
            if share:  # 확정이면 전체 소비 대비 비중, 아니면 '일부 내역 기준' 등
                note = VIEW["share"].format(total=won(fig["total"]), share=share["value"])
            rows.append({"name": _name(r["item"]), "amount": amount, "note": note})
        return [head, {"kind": "rows", "rows": rows}]
    amount, note = _seen(first["current"], item)
    if amount is None:
        return [head, *_text([VIEW["none"]]), *_meter(first)]
    fig = first["current"]["confirmed"]
    if fig is None:
        return [head, {"kind": "total", "amount": amount, "note": note}, *_meter(first)]
    if narrowed(item):
        share = item.get("category") and not filters(item) and fig["category"]["share"]
        note = share and VIEW["share"].format(total=won(fig["total"]), share=share["value"])
        return [head, {"kind": "total", "amount": amount, "note": note or None}]
    lines = [TEXT["fixed"].format(fixed=won(fig["fixed_amount"]), living=won(fig["living_amount"]))] if fig["fixed_amount"] else []
    if first["type"] == 5 and fig["top_spending"]:
        top = fig["top_spending"]
        names = "·".join(NAMES[t["category"]] for t in top)
        money = won(top[0]["amount"]) + josa(won(top[0]["amount"]), ("으로", "로"))
        lines += [VIEW["top"].format(names=names, copula=josa(names, ("이에요", "예요"))),
                  VIEW["top_tied" if len(top) > 1 else "top_amount"].format(
                      amount=money, share=fig["shares"][top[0]["category"]]["value"])]
    return [head, {"kind": "total", "amount": amount, "note": None}, *_text(lines), *_meter(first)]


def _view_budget(results, many):
    first = results[0]
    if "cycle_period" not in first:  # 예산 기간 없음
        return _text([line for r in results for line in _lines(_budget, r)])
    head = _head(f"{span(first['cycle_period'])} · 예산" if many else f"{span(first['cycle_period'])} · {scope(first['item'])} 예산")
    blocks, rows, other = [head], [], []
    for r in results:
        done = r.get("confirmed")
        if done is None:
            other += [line for line in _lines(_budget, r) if not line.startswith("이번 예산 기간은")]
            continue
        note = VIEW["budget_note"].format(allocated=won(done["allocated"]), spent=won(done["spent"]),
                                          until=day(r["spent_period"].last_day))
        if not many:
            blocks.append({"kind": "total", "caption": "초과한 금액" if done["over"] else "남은 금액",
                           "amount": done["over"] or done["remaining"], "note": note})
            spare = [TEXT["budget_daily"].format(days=r["days_left"], daily=won(_daily(r)))] if _daily(r) else []
            if done.get("covered_by_spare"):
                spare.append(TEXT["spare_cover"].format(covered=won(done["covered_by_spare"]), spare=won(done["spare_remaining"])))
            if done.get("shortfall"):
                spare.append(TEXT["spare_short"].format(shortfall=won(done["shortfall"])))
            blocks += _text(spare)
        else:
            rows.append({"name": scope(r["item"]), "amount": done["over"] or done["remaining"],
                         "note": ("초과 · " if done["over"] else "남음 · ") + note})
    if rows:
        blocks.append({"kind": "rows", "rows": rows})
    cats = [c for r in results for c in r.get("categories", [])]
    if cats:  # 카테고리별 남은 예산: 막대 = 쓴 비율(100% 넘으면 가득), 초과는 붉은 글자 + '초과' 글자
        blocks.append({"kind": "rows", "title": "카테고리별 남은 예산", "rows": [
            {"name": NAMES[c["category"]], "amount": c["over"] or c["remaining"],
             "note": ("초과 · " if c["over"] else "남음 · ") + f"예산 {won(c['allocated'])}",
             "ratio": str(min(percent(c["spent"], c["allocated"])["value"], 100)) if c["allocated"] else "100",
             **({"direction": "up"} if c["over"] else {})} for c in cats]})
    return blocks + _text(other)


def _badge(c):
    if c["rate"] is None:
        return {"text": VIEW["from_zero"].format(delta=won(c["delta"])), "direction": "up"}
    if c["delta"] > 0:
        return {"text": VIEW["up"].format(delta=won(c["delta"]), rate=_plain(c["rate"])), "direction": "up"}
    if c["delta"] < 0:
        return {"text": VIEW["down"].format(delta=won(-c["delta"]), rate=_plain(c["rate"])), "direction": "down"}
    return {"text": VIEW["same"], "direction": "same"}


def _view_one_compare(r):
    """한 항목 비교: 큰 금액 = 차이(더 · 덜 쓴 금액), 아래 두 줄 = 이번 달 · 지난달 금액 막대 (길이 = 큰 쪽 대비)"""
    c, item = r["comparison"]["category" if r["item"].get("category") else "total"], r["item"]
    now, before = _amount(r["current"]["confirmed"], item), _amount(r["previous"]["confirmed"], item)
    if c["rate"] is None:
        note = VIEW["from_zero"].format(delta=won(c["delta"]))
    else:
        note = c["delta"] and VIEW["rate_up" if c["delta"] > 0 else "rate_down"].format(rate=_plain(c["rate"])) or VIEW["same"]
    top = max(now, before)
    rows = [{"name": name, "amount": amount, "note": span(r[key]["period"]),
             "ratio": str(percent(amount, top)["value"]) if top > 0 else "0"}
            for name, key, amount in (("이번 달", "current", now), ("지난달", "previous", before))]
    caption = VIEW["more" if c["delta"] > 0 else "less" if c["delta"] < 0 else "even"]
    return [_head(f"{_name(item)} · 지난달 같은 기간과 비교"),
            {"kind": "total", "caption": caption, "amount": abs(c["delta"]), "note": note,
             "direction": "up" if c["delta"] > 0 else "down" if c["delta"] < 0 else "same"}, {"kind": "rows", "rows": rows}]


def _view_compare(results, many):
    cur = results[0]["current"]
    available = all(r["comparison"]["category" if r["item"].get("category") else "total"]["available"] for r in results)
    if available and not many:
        return _view_one_compare(results[0])
    head = _head(f"{span(cur['period'])} · " + ("지난달 같은 기간과 비교" if available else "이번 달 금액"))
    rows = []
    for r in results:
        c = r["comparison"]["category" if r["item"].get("category") else "total"]
        amount, note = _seen(r["current"], r["item"])
        row = {"name": _name(r["item"]), "amount": amount, "note": note}
        if c["available"]:
            row["badge"] = _badge(c)
        rows.append(row)
    return [head, {"kind": "rows", "rows": rows}]


def _view_trend(results, many):
    blocks = []
    for n, r in enumerate(results):
        series, item = r["series"], r["item"]
        to_date = bool(item.get("include_current"))
        months = f"{series[0]['period'].start.month}월~{series[-1]['period'].start.month}월"
        if to_date:
            months += f" (매달 1일~{series[-1]['period'].last_day.day}일)"
        if n == 0:
            blocks.append(_head(f"{months} · {_name(item)}" if len(results) == 1 else months))
        shown = [(s, *_seen({"observed": {"total": s["observed"]}, "confirmed": s["confirmed"],
                             "coverage": {"state": s["state"]}}, {})) for s in series]
        shown = [(s, a, note) for s, a, note in shown if a is not None]
        top = max([a for _, a, _ in shown] or [0])
        rows = [{"name": span(s["period"]) if to_date else f"{s['period'].start.month}월", "amount": a, "note": note,
                 "ratio": str(percent(a, top)["value"]) if top > 0 else "0"} for s, a, note in shown]
        empty = [s["period"] for s in series if all(s is not x for x, _, _ in shown)]
        if not rows:
            blocks += _text([f"{_name(item)} · {VIEW['none']}" if len(results) > 1 else VIEW["none"]])
            continue
        blocks.append({"kind": "rows", "title": _name(item) if len(results) > 1 else None, "rows": rows})
        stats = _Out()
        if empty:
            stats.line("trend_none", months=months_label(empty))
        _stats(stats, r["stats"], len(series), to_date)
        blocks += _text(stats.lines)
    return blocks


QUERY_CAPTION = {"sum": "쓴 금액", "avg": "건당 평균", "daily_avg": "하루 평균", "max": "가장 큰 지출"}


def _view_one_query(r):
    """10번 한 항목 카드: 머리줄 · 큰 금액(건수는 글자 값) · 묶음 줄(막대 = 가장 큰 묶음 대비, 요일 · 시간대 · 월은 정해진 순서)"""
    item, q = r["item"], r["item"]["query"]
    _, whole = _q_period(r)
    head = _head(f"{span(whole)} · {scope(item) if narrowed(item) else '소비'}")
    fig = r["confirmed"] or r["observed"]
    note = None if r["confirmed"] else VIEW["partial"]
    if not fig["count"] and not fig["sum"]:
        return [head, *_text([VIEW["none"] if r["coverage"]["state"] == "none" else TEXT["q_none"].format(period="", who="").strip()])]
    m = q["measure"]
    if m == "count":
        total = {"kind": "total", "caption": "결제 건수", "value": f"{fig['count']}건", "amount": fig["sum"],
                 "note": note or f"합계 {won(fig['sum'])}"}
    elif m == "max":
        big = fig["max"]
        total = {"kind": "total", "caption": QUERY_CAPTION[m] + (" (고정지출)" if big["funding"] == "fixed" else ""),
                 "amount": big["net_amount"],
                 "note": f"{_when(big)} · {big['merchant_name'] or '이름 없는 곳'} · {NAMES.get(big['category'], '')}"}
    else:
        value = fig[m] if m == "sum" else fig[m]["value"]
        caption = f"하루 평균 ({r['days']}일 · 고정지출 포함)" if m == "daily_avg" else QUERY_CAPTION[m]
        if m == "daily_avg" and fig["fixed"] and not note:
            note = f"고정지출 빼면 하루 {won(fig['daily_avg_living']['value'])}"
        total = {"kind": "total", "caption": caption, "amount": value, "note": note}
    blocks = [head, total]
    if _short_total(r) and r.get("fixed_excluded"):
        blocks += _text([TEXT["fixed_same_period"].format(amount=won(r["fixed_excluded"]))])
    if m == "max" and fig.get("max_living"):  # 가장 큰 1건이 고정지출이면 생활 소비 중 가장 큰 것을 한 줄 더
        other = fig["max_living"]
        blocks += _text([TEXT["q_max_living"].format(date=_when(other), merchant=other["merchant_name"] or "이름 없는 곳",
                                                     amount=won(other["net_amount"]))])
    if q.get("group_by"):
        by_count = m == "count"
        top = max((g["count"] if by_count else g["sum"]) for g in fig["groups"]) if fig["groups"] else 0
        blocks.append({"kind": "rows", "title": f"{GROUP_TITLE[q['group_by']]}별", "rows": [
            {"name": g["name"], "amount": g["sum"], **({"value": f"{g['count']}건"} if by_count else {}),
             "note": won(g["sum"]) if by_count else f"{g['count']}건",
             "ratio": str(percent(g["count"] if by_count else g["sum"], top)["value"]) if top > 0 else "0"}
            for g in fig["groups"]]})
    return blocks


def _view_query(results, many):
    if not many:
        return _view_one_query(results[0])
    rows = []  # 여러 카테고리 · 가게를 같은 조건으로: 줄마다 같은 계산값
    for r in results:
        fig, m = r["confirmed"] or r["observed"], r["item"]["query"]["measure"]
        value = {"sum": fig["sum"], "count": fig["sum"], "avg": (fig["avg"] or {}).get("value", 0),
                 "daily_avg": (fig["daily_avg"] or {}).get("value", 0), "max": (fig["max"] or {}).get("net_amount", 0)}[m]
        rows.append({"name": scope(r["item"]), "amount": value, **({"value": f"{fig['count']}건"} if m == "count" else {}),
                     "note": None if r["confirmed"] else VIEW["partial"]})
    _, whole = _q_period(results[0])
    title = "결제 건수" if results[0]["item"]["query"]["measure"] == "count" else QUERY_CAPTION[results[0]["item"]["query"]["measure"]]
    return [_head(f"{span(whole)} · {title}"), {"kind": "rows", "rows": rows}]


VIEWS = {10: _view_query, 1: _view_current, 2: _view_current, 5: _view_current, 3: _view_budget, 4: _view_budget,
         6: _view_compare, 7: _view_compare, 8: _view_trend, 9: _view_trend}


def view(results):
    """조회 결과 → 카드 블록 목록 (성공 항목이 없으면 None). 실패 항목은 문장으로 덧붙인다"""
    done = [r["result"] for r in results if r["ok"]]
    if not done:
        return None
    blocks = VIEWS[done[0]["type"]](done, len(results) > 1)
    failed = [TEXT["failed"].format(name=scope(r["item"]), what=WHAT[r["item"]["type"]]) for r in results if not r["ok"]]
    return blocks + _text(failed)


def compose(results, notices=()):
    """adapter.run 결과(+ 라우터 Decision.notices) → {"lines": 본문, "notices": 공통 안내(중복 없음)}"""
    out = _Out()
    for r in results:
        if r["item"].get("instead_of"):
            out.notice("instead_of", word=r["item"]["instead_of"], name=NAMES[r["item"]["category"]])
        if r["item"].get("merchant") and online(r["item"]["merchant"])[0]:  # 배달앱 묶음: 어느 앱을 모았는지
            group = online(r["item"]["merchant"])[0]
            out.notice("online", brands=" · ".join(list(ONLINE[group]["brands"])[:3]), group=group)
            out.notice("online_fee")
        elif r["item"].get("merchant"):
            out.notice("merchant", word=r["item"]["merchant"], particle=josa(r["item"]["merchant"], ("이", "가")))
        if r["item"].get("payment"):
            out.notice("payment_bank" if r["item"]["payment"] == "bank" else "payment")
        if not r["ok"]:
            out.line("failed", name=scope(r["item"]), what=WHAT[r["item"]["type"]])
            out.notice("retry")
            continue
        BODY[r["result"]["type"]](out, r["result"])
    # 생활비(4번 · 생활비 막대)에 예산 범위 밖 지출이 실제로 있었을 때만 짧게
    if any((r["result"].get("excluded_spent") or (r["result"].get("living_budget") or {}).get("excluded_spent"))
           for r in results if r["ok"]):
        out.notice("out_of_budget")
    excluded = sum(_fixed_excluded(r["result"]) for r in results if r["ok"])
    if excluded:  # 요일 · 시간대 · 묶기 · 짧은 기간 조회에서 기본으로 뺀 고정지출 → 금액(항목 이름) + [고정지출 포함해서 보기]
        names = " · ".join(dict.fromkeys(n for r in results if r["ok"] for n in _fixed_names(r["result"])))
        if all(_short_total(r["result"]) for r in results if r["ok"]):
            out.line("fixed_same_period", amount=won(excluded))  # 짧은 기간 총액: 본문 아래 따로 한 줄 (금액만)
        else:
            out.notice("fixed_excluded", amount=won(excluded), names=names)
    if "months_capped" in notices:
        applied = next((r["item"]["months_applied"] for r in results if r["item"].get("months_applied")), None)
        if applied:
            out.notice("months_capped", n=applied)
    reply = {"lines": out.lines, "notices": out.notices}
    cards = view(results)
    if cards:
        reply["view"] = cards
    if out.lead:
        reply["lead"] = out.lead
    if out.sections:
        reply["sections"] = out.sections
    done = [r["result"] for r in results if r["ok"]]
    if any(r["type"] == 5 for r in done):
        reply["chart"] = chart(next(r for r in done if r["type"] == 5))
    if any((r.get("confirmed") or {}).get("over") for r in done if r["type"] in (3, 4)):
        reply["suggest"] = ["budget_adjust"]  # 초과 뒤 예산 조정 제안 (문구 · 선택지는 라우터 안내가 붙인다)
    if excluded:
        reply["suggest"] = [*reply.get("suggest", []), "include_fixed"]
    return reply


def _short_total(result):
    """달보다 짧은 기간의 총액(묶기 · 요일 · 시간대 없음) — 뺀 고정지출을 본문 아래 '같은 기간 고정지출'로 따로 보인다"""
    item = result["item"]
    return result["type"] == 10 and item["query"]["period"] in SHORT_PERIODS and not item["query"].get("group_by") \
        and not item.get("weekdays") and not item.get("time_slots")


def _fixed_names(result):
    if result["type"] == 10:
        return result.get("fixed_names") or []
    blocks = [result.get("current") or {}] + result.get("months", [])
    return [n for b in blocks for n in b.get("fixed_names", [])]


def _fixed_excluded(result):
    """기본으로 뺀 고정지출 금액 (10번 · 조건 붙은 1 · 2 · 6~9번의 기간 블록). 없으면 0"""
    if result["type"] == 10:
        return result.get("fixed_excluded") or 0
    if result["type"] in (8, 9):  # 추이: 달마다 뺀 금액의 합
        return sum(m.get("fixed_excluded", 0) for m in result.get("months", []))
    return (result.get("current") or {}).get("fixed_excluded", 0)  # 이번 달 기준 (비교의 지난달 쪽도 같은 규칙으로 뺐다)
