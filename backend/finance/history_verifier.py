"""완료 월 거래 정합성의 읽기 전용 검증. 수집 완전성·예산 선택·허용 오차는 호출자 책임.

검증 범위와 근거의 한계: docs/finance_history_verifier.md.
비영 영향은 자동 허용하지 않는다. report 함수는 정책 검토용 근거를 별도로 제공한다.
"""
import re
from collections import Counter, defaultdict
from datetime import date

from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone

from .api import cancel_info
from .models import FinancialTransaction as Tx, TransactionLink

DIMENSIONS = ("duplicates", "cancellations", "refunds")
RANK = {"verified": 0, "unknown": 1, "unresolved": 2}


def _months(month_ids):
    if isinstance(month_ids, (str, bytes)):
        raise ValueError("month_ids는 YYYY-MM 문자열 3개의 목록이어야 합니다.")
    try:
        ids = list(month_ids)
        if len(ids) != 3 or any(not isinstance(m, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", m) for m in ids):
            raise ValueError
        starts = [date.fromisoformat(m + "-01") for m in ids]
        if len(set(ids)) != 3 or any(d >= timezone.localdate().replace(day=1) for d in starts):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("서로 다른 완료된 달력월 3개를 YYYY-MM 형식으로 전달해 주세요.") from None
    return ids


def verify_transaction_history(user, month_ids):
    """BUDGET_HISTORY_VERIFIER용: {월: {duplicates/cancellations/refunds: 상태}}.

    잘못된 월 입력은 ValueError. DB 조회 실패는 전 항목 unknown. DB/정책을 변경하지 않는다.
    """
    return {month: {key: item["status"] for key, item in result.items()}
            for month, result in transaction_history_report(user, month_ids).items()}


def transaction_history_report(user, month_ids):
    """상태와 사유별 영향 상한(원). None은 상한을 입증하지 못함; 상쇄·허용률 적용 없음.

    원거래월 밖의 취소/입금을 놓치지 않도록 회원의 저장된 전체 이력과 연결을 읽는다.
    원문·거래 ID를 결과나 로그에 노출하지 않는다.
    """
    months = _months(month_ids)
    report = {m: {k: {"status": "verified", "impact_upper_bound": 0, "reasons": []}
                  for k in DIMENSIONS} for m in months}

    def mark(affected, kind, status, reason, bound=None):
        if bound == 0:  # 건수가 아니라 소비 금액 영향으로 판단한다.
            return
        for month in set(affected) & report.keys():
            item = report[month][kind]
            item["status"] = max(item["status"], status, key=RANK.get)
            old = item["impact_upper_bound"]
            item["impact_upper_bound"] = None if old is None or bound is None else old + bound
            item["reasons"].append({"code": reason, "impact_upper_bound": bound})

    try:
        rows = list(Tx.objects.filter(user=user).order_by("occurred_on", "pk"))
        # 소유자가 잘못된 연결도 집계에서 제외 근거로 쓰일 수 있다. 상대 회원의 거래 내용은 읽지 않는다.
        links = list(TransactionLink.objects.filter(
            Q(from_transaction__user=user) | Q(to_transaction__user=user),
            relation_type__in=["SAME_PURCHASE", "CANCELLATION"],
        ).order_by("pk"))
    except DatabaseError:
        for kind in DIMENSIONS:
            mark(months, kind, "unknown", "db_error")
        return report
    by_id = {t.pk: t for t in rows}
    uid = getattr(user, "pk", user)
    same = [l for l in links if l.relation_type == "SAME_PURCHASE"]
    cancels = [l for l in links if l.relation_type == "CANCELLATION"]
    hidden = {pk for l in same for pk in (l.from_transaction_id, l.to_transaction_id)
              if pk in by_id and by_id[pk].source_kind == "bank"}

    def month(tx):
        return tx.occurred_on.strftime("%Y-%m")

    def preceding(tx):
        # 원거래를 모르는 환불은 입금월만 막으면 안 된다. 요청한 앞선 월도 미검증.
        return [m for m in months if m <= month(tx)]

    def net(tx):
        return tx.amount - cancel_info(tx)["amount"] if tx.transaction_type == "spending" else 0

    def spent(tx):
        return 0 if tx.pk in hidden else net(tx)

    def label(tx):
        name = tx.merchant_name if tx.source_kind == "card" else tx.source_description
        return "".join((name or "").split())

    # 같은 구매 연결은 실제로 계좌 행을 숨긴다. 소유권·방향·금액·일대일을 먼저 확인한다.
    same_degree = Counter(pk for l in same for pk in (l.from_transaction_id, l.to_transaction_id))
    valid_same = set()
    for link in same:
        pair = [by_id.get(link.from_transaction_id), by_id.get(link.to_transaction_id)]
        owned = [t for t in pair if t is not None]
        affected = [month(t) for t in owned]
        bank = next((t for t in owned if t.source_kind == "bank"), None)
        card = next((t for t in owned if t.source_kind == "card"), None)
        if (link.user_id != uid or len(owned) != 2 or bank is None or card is None
                or bank.direction != "out" or card.direction != "out"
                or card.transaction_type != "spending" or bank.amount != card.amount
                or link.matched_amount != card.amount or bank.occurred_on != card.occurred_on
                or any(same_degree[t.pk] != 1 for t in owned)):
            mark(affected, "duplicates", "unresolved", "invalid_same_purchase")
        elif link.confirmation_state != "confirmed":
            # 날짜·금액·가맹점은 추정 근거이지 동일 구매의 확정 증거가 아니다.
            mark(affected, "duplicates", "unknown", "suggested_same_purchase", net(bank))
        elif cancel_info(bank)["amount"] != cancel_info(card)["amount"]:
            mark(affected, "duplicates", "unresolved", "same_purchase_net_mismatch",
                 abs(cancel_info(bank)["amount"] - cancel_info(card)["amount"]))
        else:
            valid_same.add(frozenset(t.pk for t in owned))

    fingerprints, approvals, purchases = defaultdict(list), defaultdict(list), defaultdict(list)
    for tx in rows:
        if tx.direction == "out" and not tx.is_canceled:
            instrument = tx.account_id or tx.card_id or tx.loan_id
            fingerprints[(tx.source_kind, instrument, tx.occurred_on, tx.occurred_time,
                          tx.amount, label(tx), tx.balance_after)].append(tx)
            if tx.source_kind == "card" and tx.approval_no:
                approvals[(tx.card_id, tx.approval_no, tx.occurred_on)].append(tx)
            if label(tx) and tx.source_kind in ("bank", "card"):
                purchases[(tx.occurred_on, tx.amount, label(tx))].append(tx)
        if spent(tx) and not (tx.source_key or (tx.source_kind == "manual" and tx.request_key)):
            mark([month(tx)], "duplicates", "unknown", "missing_identity", spent(tx))
        pending = (tx.field_states or {}).get("after_save_pending", {})
        if "link_basics" in pending and tx.amount:
            for kind in DIMENSIONS:
                mark(preceding(tx) if tx.is_canceled or tx.direction == "in" else [month(tx)],
                     kind, "unknown", "link_processing_pending")

    for groups, reason in ((fingerprints, "duplicate_fingerprint"), (approvals, "duplicate_approval")):
        for group in groups.values():
            if len(group) > 1:
                mark([month(t) for t in group], "duplicates", "unknown", reason, sum(spent(t) for t in group))
    for group in purchases.values():
        for bank in (t for t in group if t.source_kind == "bank"):
            cards = [t for t in group if t.source_kind == "card"]
            if cards and spent(bank) and not any(frozenset((bank.pk, c.pk)) in valid_same for c in cards):
                mark([month(bank)], "duplicates", "unknown", "unlinked_cross_source_purchase", spent(bank))

    # 별도 환불/취소: 연결의 금액과 현재 집계의 차감을 대조한다. 연결만 있어도 verified가 되지는 않는다.
    incoming = Counter(l.to_transaction_id for l in cancels)
    outgoing = defaultdict(list)
    cancel_members = {pk for l in cancels for pk in (l.from_transaction_id, l.to_transaction_id)}
    for link in cancels:
        outgoing[link.from_transaction_id].append(link)
    for original_id, group in outgoing.items():
        original = by_id.get(original_id)
        events = [by_id.get(l.to_transaction_id) for l in group]
        owned = [t for t in [original, *events] if t is not None]
        affected = [month(t) for t in owned]
        if original is None:
            affected = months
        invalid = (original is None or any(e is None for e in events)
                   or any(l.user_id != uid or l.matched_amount < 0 for l in group))
        if not invalid:
            invalid = (original.direction != "out" or original.pk in incoming
                       or any(incoming[e.pk] != 1 or e.pk in outgoing or e.occurred_on < original.occurred_on
                              or l.matched_amount != (cancel_info(e)["amount"] if e.is_canceled or e.canceled_amount else e.amount)
                              or not (e.is_canceled or e.canceled_amount or e.direction == "in") for l, e in zip(group, events))
                       or sum(l.matched_amount for l in group) > original.amount)
        if invalid:
            for kind in ("cancellations", "refunds"):
                mark(affected, kind, "unresolved", "invalid_cancellation_link")
            continue
        if any(l.confirmation_state != "confirmed" for l in group):
            for kind in ("cancellations", "refunds"):
                mark(affected, kind, "unknown", "suggested_cancellation_link")
            # 추정 관계를 승인하지는 않지만, 그 관계와 소비액의 불일치는 검사한다.
            # 미반영이면 unresolved를 우선하고, 차이가 0이어도 unknown은 유지한다.
        total = sum(l.matched_amount for l in group)
        if original.pk in hidden and total:
            # 숨긴 계좌 원거래의 환불은 대표 카드 소비에도 영향을 줄 수 있다.
            # 원거래 취소 연결과 같은 구매 연결의 합성을 추정하여 검증 통과시키지 않는다.
            peers = [by_id[pk] for l in same if original.pk in (l.from_transaction_id, l.to_transaction_id)
                     for pk in (l.from_transaction_id, l.to_transaction_id) if pk in by_id]
            for kind in ("cancellations", "refunds"):
                mark([*affected, *(month(t) for t in peers)], kind, "unknown", "hidden_original_refund")
            continue
        # 원거래가 소비가 아니면 그 취소도 소비액에 반영할 필요가 없다.
        expected = original.amount - total if original.transaction_type == "spending" and original.pk not in hidden else 0
        # 월을 넘나드는 과부족을 상쇄하지 않는다. 환불 행을 양수 소비로 집계하면 안 된다.
        for tx, intended in [(original, expected), *((e, 0) for e in events)]:
            delta = abs(spent(tx) - intended)
            for kind in ("cancellations", "refunds"):
                mark([month(tx)], kind, "unresolved", "cancellation_not_reflected", delta)

    for tx in rows:
        detail = tx.source_detail if isinstance(tx.source_detail, dict) else {}
        status = detail.get("cancelStatus")
        if tx.source_kind == "card":
            bad_status = status not in (None, "0", "1", "2", "3")
            inconsistent = (status == "2" and (tx.canceled_amount is None or not 0 < tx.canceled_amount < tx.amount)
                            or status in ("1", "3") and (not tx.is_canceled or cancel_info(tx)["amount"] != tx.amount)
                            or status == "0" and (tx.is_canceled or bool(tx.canceled_amount)))
            if bad_status or inconsistent:
                for kind in ("cancellations", "refunds"):
                    mark([month(tx)], kind, "unknown", "cancel_metadata_conflict")
        if tx.pk in cancel_members:
            continue
        if tx.source_kind == "bank" and tx.is_canceled:
            for kind in ("cancellations", "refunds"):
                mark(preceding(tx), kind, "unknown", "unlinked_bank_cancellation", tx.amount)
        elif tx.is_canceled or tx.canceled_amount:
            # 저장 시 원승인 갱신임을 확인할 카드 상태/키가 없으면 별도 전표일 수도 있다.
            if not (tx.source_kind == "card" and tx.source_key and tx.approval_no and status in ("1", "2", "3")):
                for kind in ("cancellations", "refunds"):
                    mark(preceding(tx), kind, "unknown", "cancellation_origin_unknown", tx.amount)
            elif tx.approval_no and any(
                other.pk != tx.pk and other.card_id == tx.card_id and other.approval_no == tx.approval_no
                and other.occurred_on <= tx.occurred_on and not other.is_canceled for other in rows
            ):
                for kind in ("cancellations", "refunds"):
                    mark(preceding(tx), kind, "unknown", "unlinked_card_cancellation", tx.amount)
        if tx.direction == "in" and tx.amount:
            # income은 은행 입금의 기본 유형이며 '환불 아님'의 증거가 아니다. 미분류 비율과 무관하다.
            mark(preceding(tx), "refunds", "unknown", "unattributed_inflow", tx.amount + spent(tx))
    return report
