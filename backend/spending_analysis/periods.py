"""소비 분석 기간 계산 — 기준 날짜를 인자로 받는 순수 함수 (DB · 현재 시각을 읽지 않는다)

모든 기간은 Period(start, end_before): start 이상 ~ end_before 미만. DB 의 BudgetCycle.ends_before 와 같은 경계.
API 응답의 {from, to}(포함)는 (start, last_day).
최근 N개월의 기본값 · 최대 3개월 · 안내는 라우터 책임이다. 여기서는 적용값을 검증만 하고 바꾸지 않는다.
"""
import calendar
from datetime import date, timedelta
from typing import NamedTuple

MAX_MONTHS = 3  # 라우터가 적용하는 최대값 (spending_analysis.dialog.MONTHS_MAX 와 같음)


class Period(NamedTuple):
    start: date
    end_before: date

    @property
    def last_day(self):
        return self.end_before - timedelta(days=1)


def _add_month(year, month, n):  # budgets.home.add_month 와 같은 계산. Django 를 import 하지 않으려고 따로 둔다
    month += n
    return year + (month - 1) // 12, (month - 1) % 12 + 1


def this_month(today):
    """이번 달 1일 ~ 기준 날짜"""
    return Period(today.replace(day=1), today + timedelta(days=1))


def last_month(today):
    """지난달 전체 (1일 ~ 말일, 완료된 달). 지난달 단독 조회용 (MEMO 23-11 결정)"""
    year, month = _add_month(today.year, today.month, -1)
    return Period(date(year, month, 1), today.replace(day=1))


def previous_month_same_days(today):
    """지난달 1일 ~ 같은 일자. 그 일자가 없으면 지난달 말일까지 (3/31 → 2/1~2/28, chat.md N①)"""
    year, month = _add_month(today.year, today.month, -1)
    last = min(today.day, calendar.monthrange(year, month)[1])
    return Period(date(year, month, 1), date(year, month, last) + timedelta(days=1))


def recent_months(today, months):
    """진행 중인 달을 뺀 완료된 months 개월, 오래된 달부터. months 는 라우터가 정한 적용값(1~3)"""
    if type(months) is not int or not 1 <= months <= MAX_MONTHS:
        raise ValueError(f"months 는 1~{MAX_MONTHS} 정수여야 한다: {months!r}")
    starts = [date(*_add_month(today.year, today.month, -n), 1) for n in range(months, -1, -1)]
    return [Period(a, b) for a, b in zip(starts, starts[1:])]


def recent_months_to_date(today, months):
    """이번 달을 포함한 months 개월, 오래된 달부터. 달마다 1일 ~ 기준일과 같은 일자(그 일자가 없으면 말일)
    → 진행 중인 이번 달과 과거 달을 같은 날짜 범위로 맞춘다 (3/31 기준 2월 = 2/1~2/28 · 29)"""
    if type(months) is not int or not 1 <= months <= MAX_MONTHS:
        raise ValueError(f"months 는 1~{MAX_MONTHS} 정수여야 한다: {months!r}")
    out = []
    for n in range(months - 1, -1, -1):
        year, month = _add_month(today.year, today.month, -n)
        last = min(today.day, calendar.monthrange(year, month)[1])
        out.append(Period(date(year, month, 1), date(year, month, last) + timedelta(days=1)))
    return out


def budget_period(cycle_start, cycle_end_before, today):
    """예산 잔액용: {"cycle": 주기 전체, "spent": 주기 시작일 ~ 기준 날짜(사용액 조회 범위)}
    주기는 DB 의 approved 예산 주기를 그대로 받는다. 기준 날짜가 주기 밖이면 ValueError"""
    if not cycle_start <= today < cycle_end_before:
        raise ValueError("기준 날짜가 예산 주기 밖이다")
    return {"cycle": Period(cycle_start, cycle_end_before), "spent": Period(cycle_start, today + timedelta(days=1))}


# ---------- 지정 기간 (범용 조회, 사용자 테스트 보완 10/30) ----------
# 볼 수 있는 범위 = 완료된 최근 MAX_MONTHS 개월의 첫날 ~ 기준일 (최근 3개월 + 이번 달). 밖이면 None — 다른 기간으로 바꾸지 않는다

def window(today):
    return recent_months(today, MAX_MONTHS)[0].start


def this_week(today):
    """이번 주 월요일 ~ 기준일"""
    return Period(today - timedelta(days=today.weekday()), today + timedelta(days=1))


def last_week(today):
    """지난주 월 ~ 일"""
    monday = today - timedelta(days=today.weekday())
    return Period(monday - timedelta(days=7), monday)


def month_of(today, month):
    """특정 달(1~12) → 가장 가까운 지난(또는 이번) 그 달. 이번 달이면 1일 ~ 기준일. 볼 수 있는 범위 밖이면 None"""
    if type(month) is not int or not 1 <= month <= 12:
        return None
    year = today.year if month <= today.month else today.year - 1
    start = date(year, month, 1)
    if start < window(today):
        return None
    nxt = date(*_add_month(year, month, 1), 1)
    return Period(start, min(nxt, today + timedelta(days=1)))


def month_pair(today, month, compare_month):
    """달끼리 비교(11/10): 두 달(1~12, month 가 None 이면 이번 달) → (나중 달 기간, 앞선 달 기간, 같은 날짜까지 잘랐는지).
    나중 달이 진행 중인 이번 달이면 앞선 달도 1일 ~ 같은 일자까지만 본다(지난달 비교와 같은 규칙), 둘 다 끝난 달이면 한 달 전체.
    같은 달이거나 볼 수 있는 범위 밖이면 None"""
    a = this_month(today) if month is None else month_of(today, month)
    b = month_of(today, compare_month)
    if a is None or b is None or a.start == b.start:
        return None
    current, previous = (a, b) if a.start > b.start else (b, a)
    clipped = current.start == today.replace(day=1)
    if clipped:
        last = min(today.day, calendar.monthrange(previous.start.year, previous.start.month)[1])
        previous = Period(previous.start, previous.start.replace(day=last) + timedelta(days=1))
    return current, previous, clipped


def month_labels(today, current, previous, clipped):
    """달끼리 비교의 이름: {"current": "이번 달" | "9월", "previous": "8월", "previous_same": "8월 같은 기간" | "8월"}"""
    name = f"{previous.start.month}월"
    return {"current": "이번 달" if current.start == today.replace(day=1) else f"{current.start.month}월",
            "previous": name, "previous_same": f"{name} 같은 기간" if clipped else name}


def _day(today, said):
    """말한 날짜 'MM-DD' 또는 일만 'DD' → date. 생략한 월 · 연도는 기준일로 채운다 (해석은 말한 값만, 채우기는 여기).
    월을 말했는데 기준일보다 뒤인 다른 달이면 지난해(10월에 '12월 3일' = 작년 12월). 이번 달 · 일만 말한 날은 그대로(아직 안 온 날일 수 있다)"""
    try:
        parts = said.split("-")
        month, day_ = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (today.month, int(parts[0]))
        found = date(today.year, month, day_)
    except (AttributeError, ValueError, IndexError):
        return None
    if found > today and month != today.month:
        return found.replace(year=today.year - 1)
    return found


def resolve_range(today, start, end):
    """날짜 범위 → {"state", "period"}. state: ok · clipped(끝이 기준일 뒤라 기준일까지) · future(시작이 기준일 뒤) · out(볼 수 있는 범위 밖 ·
    뒤집힘 · 없는 날짜). 끝을 말하지 않았으면 기준일까지"""
    first = _day(today, start)
    last = _day(today, end) if end else today
    if first is None or last is None:
        return {"state": "out", "period": None}
    if first > today:
        return {"state": "future", "period": None}
    clipped = last > today
    last = min(last, today)
    if first > last or first < window(today):
        return {"state": "out", "period": None}
    return {"state": "clipped" if clipped else "ok", "period": Period(first, last + timedelta(days=1))}


def date_range(today, start, end):
    """날짜 범위 Period (끝은 기준일까지로 자른 것). 볼 수 없으면 None"""
    return resolve_range(today, start, end)["period"]


def month_before(today, said):
    """말한 날짜('MM-DD' · 'DD')의 한 달 전 'MM-DD' (아직 안 온 기간 → 지난달 같은 날짜 선택지). 일은 그 달 말일을 넘지 않게"""
    day_ = _day(today, said)
    if day_ is None:
        return None
    year, month = _add_month(day_.year, day_.month, -1)
    return f"{month:02d}-{min(day_.day, calendar.monthrange(year, month)[1]):02d}"
