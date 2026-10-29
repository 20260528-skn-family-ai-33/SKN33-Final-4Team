"""소비 분석용 수집 완전성 판정 — 읽기 전용 (쓰기 · 잠금 · 동기화 · CODEF 호출 없음)

판단 근거는 성공한 조회 기록(SyncRun)의 covered 범위뿐이다. 거래 유무 · 합계 0원으로 판단하지 않는다.
- 대상 = 집계 대상과 같게: listed_transactions 는 사용자의 모든 계좌 · 카드 거래를 담는다
  (is_selected · 연결 해제 · 연결 시점으로 거르지 않음) → 사용자의 모든 계좌 · 카드가 대상
- 계좌: bank_sync 성공(연결 단위 기록, account FK 가 있으면 그 계좌만). sync_bank 는 입출금(demand) 계좌의 거래만 받는다
- 카드: card_approval_list 성공(카드 FK). 청구서(card_bill_list) · 연결 단위 card_sync 기록은 승인내역 근거로 쓰지 않는다
- SyncRun.covered_to 는 포함(CODEF endDate, budgets.protect.covered_until 과 같은 해석) → Period(covered_from, covered_to + 1일)
- 실패 기록은 읽지 않는다: 나중 실패가 이미 확보한 성공 범위를 지우지 않고, 실패 요청 범위(requested)를 성공으로 쓰지 않는다
- complete 는 수집 범위가 다 있다는 뜻이지 최신이라는 뜻이 아니다 (최근 성공 시각은 evidence · latest_success_at)
"""
from datetime import timedelta

from django.db import DatabaseError

from finance.api import LIST_TYPES
from finance.models import BankAccount, Card, FinancialTransaction, SyncRun

from .periods import Period

BANK_RUN, CARD_RUN = "bank_sync", "card_approval_list"
DAY = timedelta(days=1)


def merge(periods):
    """겹치거나 바로 이어지는(앞 end_before == 뒤 start) 기간을 합친다"""
    out = []
    for p in sorted(periods):
        if out and p.start <= out[-1].end_before:
            out[-1] = Period(out[-1].start, max(out[-1].end_before, p.end_before))
        else:
            out.append(p)
    return out


def gaps(period, covered):
    """period 안에서 covered(merge 결과)가 덮지 못한 기간 목록"""
    missing, cursor = [], period.start
    for p in covered:
        if p.start >= period.end_before:
            break
        if p.start > cursor:
            missing.append(Period(cursor, p.start))
        cursor = max(cursor, p.end_before)
    if cursor < period.end_before:
        missing.append(Period(cursor, period.end_before))
    return missing


def _target(kind, target_id, runs, period):
    covered = merge(Period(r["covered_from"], r["covered_to"] + DAY) for r in runs)
    done = [r["completed_at"] for r in runs if r["completed_at"]]
    return {
        "kind": kind, "id": target_id,
        "covered": [Period(max(p.start, period.start), min(p.end_before, period.end_before)) for p in covered
                    if p.start < period.end_before and p.end_before > period.start],
        "missing": gaps(period, covered),
        "evidence": [{"sync_id": r["sync_id"], "covered": Period(r["covered_from"], r["covered_to"] + DAY),
                      "completed_at": r["completed_at"]} for r in sorted(runs, key=lambda r: r["covered_from"])],
        "latest_success_at": max(done) if done else None,
    }


def coverage(user, period):
    """→ {"state": "complete" | "partial" | "none" | "error", "period", "targets", "undetermined"}
    - targets: 계좌 · 카드마다 {"kind", "id", "covered", "missing", "evidence", "latest_success_at"}
    - undetermined: 수집 범위를 정할 규칙이 없는 원천 (complete 를 막는다)
    - complete = 대상이 1개 이상 · 모든 대상에 빈 날 없음 · undetermined 없음
    - none = 기간 안에 확보된 날이 하나도 없음(대상이 없을 때 포함) / partial = 그 밖
    - error = 이 판정의 DB 조회 실패 (기관 조회 실패 기록은 판정 근거가 아니라 그냥 '성공 없음')"""
    try:
        accounts = list(BankAccount.objects.filter(user=user).order_by("account_id")
                        .values("account_id", "connection_id", "account_type"))
        cards = list(Card.objects.filter(user=user).order_by("card_id").values("card_id", "origin"))
        runs = list(SyncRun.objects.filter(
            user=user, status="success", product_code__in=[BANK_RUN, CARD_RUN],
            covered_from__isnull=False, covered_to__isnull=False,
            covered_from__lt=period.end_before, covered_to__gte=period.start,
        ).values("sync_id", "product_code", "connection_id", "account_id", "card_id",
                 "covered_from", "covered_to", "completed_at"))
        other_sources = sorted(set(FinancialTransaction.objects.filter(
            user=user, transaction_type__in=LIST_TYPES, occurred_on__gte=period.start, occurred_on__lt=period.end_before,
        ).exclude(source_kind__in=["bank", "card"]).values_list("source_kind", flat=True)))
    except DatabaseError:
        return {"state": "error", "reason": "db_error", "period": period}

    targets, undetermined = [], []
    for a in accounts:
        if a["account_type"] != "demand":  # sync_bank 가 거래를 받지 않는 계좌 — 범위 정책 미정
            undetermined.append({"kind": "account", "id": a["account_id"], "reason": "not_demand_account"})
            continue
        used = [r for r in runs if r["product_code"] == BANK_RUN and (
            r["account_id"] == a["account_id"] or (r["account_id"] is None and r["connection_id"] == a["connection_id"]))]
        targets.append(_target("account", a["account_id"], used, period))
    for c in cards:
        if c["origin"] != "provider":  # 직접 등록 카드는 조회 기록이 생기지 않는다
            undetermined.append({"kind": "card", "id": c["card_id"], "reason": "manual_card"})
            continue
        used = [r for r in runs if r["product_code"] == CARD_RUN and r["card_id"] == c["card_id"]]
        targets.append(_target("card", c["card_id"], used, period))
    for kind in other_sources:  # 직접 입력 · 대출 원천 거래: 수집 범위를 정할 기록이 없다
        undetermined.append({"kind": f"{kind}_transactions", "id": None, "reason": "no_sync_record"})

    if not any(t["covered"] for t in targets):
        state = "none"
    elif not undetermined and all(not t["missing"] for t in targets):
        state = "complete"
    else:
        state = "partial"
    return {"state": state, "period": period, "targets": targets, "undetermined": undetermined}
