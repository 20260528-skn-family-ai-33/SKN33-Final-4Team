"""
CODEF 불러오기 기간 (10/27 나은 결정)

- 첫 연결: 4달 전 1일 ~ 오늘
- 다시 불러오기 · 자동 불러오기: 마지막으로 성공한 조회일 7일 전 ~ 오늘 (겹친 거래는 sync.py 가 중복 없이 처리)
- 카드 청구서: 지난달 + 이번 달 (CODEF billing-list 는 월 단위 YYYYMM)
"""
from datetime import date, timedelta

FIRST_LOAD_MONTHS = 4
OVERLAP_DAYS = 7


def months_back(day, months):
    """day 가 속한 달에서 months 달 전의 1일"""
    index = day.year * 12 + (day.month - 1) - months
    return date(index // 12, index % 12 + 1, 1)


def first_load_start(today):
    """첫 연결 시작일: 4달 전 1일"""
    return months_back(today, FIRST_LOAD_MONTHS)


def refresh_start(today, last_success):
    """다시 불러오기 시작일: 마지막 성공 조회일 7일 전. 성공한 적이 없으면 첫 연결과 같게"""
    if last_success is None:
        return first_load_start(today)
    return min(last_success - timedelta(days=OVERLAP_DAYS), today)


def bill_months(today):
    """카드 청구서 조회 달: (지난달 YYYYMM, 이번 달 YYYYMM)"""
    return months_back(today, 1).strftime("%Y%m"), today.strftime("%Y%m")
