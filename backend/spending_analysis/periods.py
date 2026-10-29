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
