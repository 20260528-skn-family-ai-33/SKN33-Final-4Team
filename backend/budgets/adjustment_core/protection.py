"""자동 감액 보호금액. 서버가 스스로 줄일 때 이 금액 아래로는 내리지 않는다.

물가 자료가 준비되기 전까지는 사용자의 실제 소비 기록을 근거로 쓴다.
- 이번 주기: 불러온 지출 + 관측일 평균 × 남은 날
- 다음 주기 계획: 관측일 평균 × 30일(한 달 예상 소비)
관측한 소비 기록이 전혀 없으면 예산 속도로 대신한다:
- 이번 주기: 이미 쓴 돈 + 배정액 ÷ 30 × 남은 날 (남은 기간에 평소 예산만큼은 쓸 수 있게 남긴다)
남은 날도 모르면 None 이고, 이때는 자동으로 줄이지 않는다(현재 배정액 유지).
사용자가 항목과 금액을 직접 정한 감액은 이 보호금액이 아니라 이미 쓴 돈·최소금액·잠금만 지킨다.
"""
import math
from fractions import Fraction

from .diagnostics import day, resolve_history

UNIT = 5000
MONTH_DAYS = 30


def _up(amount):
    return -(-amount // UNIT) * UNIT


def daily_averages(context):
    """불러온 날짜의 카테고리별 평균. 누락 날짜·미관측 카테고리는 0원이 아니다."""
    info = context.get('diagnostic_inputs') or {}
    allocations = context.get('allocations') or {}
    try:
        if not info.get('as_of') or day(info['as_of']) > day(context.get('today')):
            return None
        samples, _ = resolve_history(info.get('history') or {}, info['as_of'], allocations)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    history = info['history']
    complete = history.get('complete_dates', history['dates'] if history['status'] == 'ready' else [])
    averages = {}
    for code in allocations:
        observed = [sample[code] for index, sample in enumerate(samples)
                    if history['dates'][index] in complete or history['samples'][index][code] > 0]
        if observed:
            averages[code] = Fraction(sum(observed), len(observed))
    return averages or None


def remaining_days(context):
    info = context.get('diagnostic_inputs') or {}
    days = info.get('cycle_remaining_days', info.get('remaining_days'))
    if type(days) is int and info.get('as_of') and context.get('today'):
        try:
            elapsed = (day(context['today']) - day(info['as_of'])).days
            if elapsed > 0:
                days = max(days - elapsed, 0)
        except (ValueError, TypeError):
            return None
    if type(days) is not int:
        days = _days_until_next_cycle(context)
    return days if type(days) is int and 0 <= days <= 365 else None


def _days_until_next_cycle(context):
    """진단 자료가 없을 때: 다음 주기 시작일까지 남은 날 (diagnostics 와 같은 셈: 오늘 제외 · 시작일 전날까지)"""
    from datetime import date
    try:
        return (date.fromisoformat(context['next_cycle_starts_on']) - date.fromisoformat(context['today'])).days - 1
    except (KeyError, TypeError, ValueError):
        return None


def has_history(context):
    """최근 소비 기록으로 하루 평균을 낼 수 있는지 (설명 문구용)"""
    return daily_averages(context) is not None


def usage_floors(context):
    """카테고리별 자동 감액 하한. 다음 주기 계획은 계산해 둔 값을 그대로 쓴다."""
    if 'usage_floors' in context:
        floors = context['usage_floors']
        return dict(floors) if isinstance(floors, dict) else None
    averages, days = daily_averages(context), remaining_days(context)
    if days is None:
        return None
    spent = context.get('spent') or {}
    if averages is None:
        # 기록이 부족하면 '남은 기간에도 평소 예산 속도로 쓸 돈'을 남긴다. 저축보다 생활비 안 이동이 먼저 되도록(정책 순서)
        averages = {code: Fraction(amount, MONTH_DAYS) for code, amount in (context.get('allocations') or {}).items()}
    return {code: _up(spent.get(code, 0) + math.ceil(average * days)) for code, average in averages.items()}


def next_cycle_floors(context):
    """다음 주기는 아직 쓴 돈이 없으니 한 달 평소 소비만 남긴다."""
    averages = daily_averages(context)
    if averages is None:
        return None
    return {code: _up(math.ceil(average * MONTH_DAYS)) for code, average in averages.items()}


def expected_need(context, category):
    """다음 급여일까지 이 항목에 더 필요해 보이는 돈 (최근 소비 속도 기준). 모르면 None."""
    averages, days = daily_averages(context), remaining_days(context)
    if averages is None or days is None or category not in averages:
        return None
    spent = (context.get('spent') or {}).get(category, 0)
    remaining = max(context['allocations'][category] - spent, 0)
    return max(math.ceil(averages[category] * days) - remaining, 0)
