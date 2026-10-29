"""CODEF 카드 응답 검증 (보유카드 card-list · 청구내역 billing-list · 승인내역 approval-list).
식별값·원문을 예외 메시지에 넣지 않는다.

칸 이름은 CODEF 개발가이드 '개인 보유카드' · '개인 청구내역' · '개인 승인내역' 출력부 기준 (2026-10-28 확인).
- 응답은 단건이면 객체, 여러 건이면 목록으로 온다. 빈 data({})는 0건이다.
- 미제공 값은 빈칸으로 온다. 빈칸을 0으로 바꾸지 않는다.
- 청구내역은 명세서 단위(카드번호 없이 상세마다 이용카드)라 카드사 통합 청구(issuer)로 저장한다 (10/28 나은 · cards.md).
"""
import hashlib
import re
from datetime import date, datetime

from . import codef

ITEM_TYPES = {"1": "one_time", "2": "installment"}   # 3 그외 · 4 단기카드대출 · 5 장기카드대출 → unknown
MONTH = re.compile(r"[0-9]{4}(?:0[1-9]|1[0-2])")
MAX_AMOUNT = 9223372036854775807
CANCELED, PARTIAL, DECLINED = "1", "2", "3"   # resCancelYN (0 정상)


class CardDataError(ValueError):
    def __init__(self, code="invalid_card_data"):
        self.code = code
        super().__init__("CODEF 카드 응답을 확인할 수 없어 저장하지 않았어요.")


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise CardDataError()
    return value.strip() or None


def _amount(value):
    # 청구 총액 · 이용금액은 환불 · 취소로 음수가 올 수 있다.
    value = _text(value)
    if value is None:
        return None
    if not re.fullmatch(r"-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value):
        raise CardDataError()
    number = int(value.replace(",", ""))
    if abs(number) > MAX_AMOUNT:
        raise CardDataError()
    return number


def _count(value):
    """할부 개월 · 회차. 0이나 빈칸은 '없음'(None)"""
    value = _text(value)
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{1,3}", value):
        raise CardDataError()
    return int(value) or None


def _date(value):
    value = _text(value)
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{8}", value):
        raise CardDataError()
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise CardDataError() from None


def _short(value, size):
    """표시용 글자. 길면 자른다 (식별값이 아니라서 응답 전체를 버리지 않는다)"""
    value = _text(value)
    return value[:size] if value else None


def _code(value, size=20):
    """기관 원래 값 그대로 저장하는 칸. 칸 길이를 넘으면 해석하지 않고 비운다"""
    value = _text(value)
    return value if value and len(value) <= size else None


def _rows(data):
    if data == {}:
        return []
    rows = [data] if isinstance(data, dict) else data
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise CardDataError()
    return rows


def _key(*parts):
    return hashlib.sha256("|".join(str(part or "") for part in parts).encode()).hexdigest()


def normalize_cards(data):
    cards, seen = [], set()
    for row in _rows(data):
        number = _text(row.get("resCardNo"))
        number = re.sub(r"[\s-]", "", number) if number else None
        if not number or number in seen:
            raise CardDataError("ambiguous_card_identity")
        seen.add(number)
        name = _short(row.get("resCardName"), 100)
        cards.append({
            # 카드번호 원문은 키 · 마스킹 · 승인내역 대조에만 쓰고 저장하지 않는다
            "number": number,
            "source_card_key": _key(number),
            "masked_number": codef.mask(number),
            "name": name.replace(number, codef.mask(number)) if name else None,
            # 정규화 기준 미정 (cards.md cardType) → 기관 값 그대로
            "card_type": _code(row.get("resCardType")),
            "card_status": _code(row.get("resState")),
        })
    return cards


def _item(row):
    months, round_no = _count(row.get("resInstallmentMonth")), _count(row.get("resRoundNo"))
    if months and round_no and round_no > months:
        raise CardDataError()
    principal, fee = _amount(row.get("resPaymentPrincipal")), _amount(row.get("resFee"))
    billed = _amount(row.get("resPaymentAmt"))
    states = {}
    if billed is None and principal is not None and fee is not None:
        # CODEF 명세: 결제금액 = 결제금액_원금 + 수수료. 둘 중 하나라도 비면 계산하지 않는다
        billed = principal + fee
        states["billedAmount"] = {"source": "derived"}
    return {
        "item_type": ITEM_TYPES.get(_text(row.get("resPaymentType")), "unknown"),
        "merchant_name": _short(row.get("resMemberStoreName"), 200),
        "original_purchase_amount": _amount(row.get("resUsedAmount")),
        "billed_amount": billed,
        "installment_months": months,
        "installment_round": round_no,
        "purchased_on": _date(row.get("resUsedDate")),
        "field_states": states,
        "_identity": (
            row.get("resUsedDate"), row.get("resMemberStoreName"), row.get("resUsedAmount"),
            row.get("resPaymentType"), row.get("resInstallmentMonth"), row.get("resRoundNo"),
            row.get("resApprovalNo"), row.get("resPaymentPrincipal"), row.get("resFee"), row.get("resUsedCard"),
        ),
    }


def _items(value):
    """상세 목록이 없으면 None(모름). 빈 목록은 0건"""
    if value is None or value == "":
        return None
    items, counts = [], {}
    for row in _rows(value):
        item = _item(row)
        identity = item.pop("_identity")
        # 같은 날 같은 금액 같은 가맹점이 두 번 있을 수 있어서 순번을 붙인다
        counts[identity] = counts.get(identity, 0) + 1
        item["source_item_key"] = _key(*identity, counts[identity])
        items.append(item)
    return items


def items_state(total, items):
    """상세를 못 받았으면 unavailable. 모든 항목의 이번 회차 금액이 있고 합계가 총액과 같을 때만 complete"""
    if items is None or (not items and total != 0):
        return "unavailable"
    if all(item["billed_amount"] is not None for item in items) and sum(
        item["billed_amount"] for item in items
    ) == total:
        return "complete"
    return "partial"


def normalize_card_bills(data, month, org):
    """month: 요청한 청구년월 YYYYMM. 같은 응답 안에서 명세서 구분이 겹치면 고르지 않고 실패"""
    if not isinstance(month, str) or not MONTH.fullmatch(month):
        raise ValueError("청구년월은 YYYYMM 이어야 해요.")
    billing_month = datetime.strptime(month + "01", "%Y%m%d").date()
    bills, seen = [], set()
    for row in _rows(data):
        total = _amount(row.get("resTotalAmount"))
        if total is None:
            raise CardDataError("bill_total_missing")
        payment_due = _date(row.get("resPaymentDueDate"))
        bill_type = _text(row.get("resBillType"))
        # CODEF 청구서에는 고유 번호가 없다. 금액은 예상 → 확정으로 바뀌므로 키에 넣지 않는다
        key = _key(org, month, bill_type, payment_due)
        if key in seen:
            raise CardDataError("ambiguous_bill_identity")
        seen.add(key)
        items = _items(row.get("resChargeHistoryList"))
        bills.append({
            "statement_key": key,
            "billing_month": billing_month,
            "total_amount": total,
            "payment_due_date": payment_due,
            "withdrawal_due_date": _date(row.get("resWithdrawalDueDate")),
            # 명세서 구분은 선차감에서 카드론 명세서를 가르는 데 쓴다. 길어도 비우지 않고 잘라 둔다 (비우면 빈 값으로 읽힘)
            "bill_type": _short(row.get("resBillType"), 50),
            "items": items,
            "items_state": items_state(total, items),
        })
    return bills


def card_matches(card_number, shown):
    """승인내역 카드번호(1234********5678)가 보유카드 번호와 같은 카드일 수 있는지. 가린 자리(*)는 비교하지 않는다"""
    return len(card_number) == len(shown) and all(
        a == b or "*" in (a, b) for a, b in zip(card_number, shown)
    )


def _time(value):
    value = _text(value)
    if value is None or not re.fullmatch(r"[0-9]{6}", value):
        return None
    try:
        return datetime.strptime(value, "%H%M%S").time()
    except ValueError:
        return None


def normalize_approvals(data, start, end):
    """승인내역 → (거래 목록, 실제 조회 기간 (시작, 끝)).

    거래 dict 의 card_number 는 카드 대조용(저장 안 함). 금액을 확정할 수 없는 줄은 unknown_reason 을 단다:
    해외 원화금액 없음 · 부분취소 금액 없음 · 음수 이용금액(취소전표) — 저장하지 않고 이번 조회를 '거래 다 불러옴'으로 치지 않는다.
    """
    rows, covered_from, covered_to = [], start, end
    for row in _rows(data):
        # 카드사가 실제로 조회해 준 기간 (요청보다 짧을 수 있음. 예: 현대카드 최근 3개월)
        comm_start, comm_end = _date(row.get("commStartDate")), _date(row.get("commEndDate"))
        if comm_start:
            covered_from = max(covered_from, comm_start)
        if comm_end:
            covered_to = min(covered_to, comm_end)
        shown = _text(row.get("resCardNo"))
        used_on = _date(row.get("resUsedDate"))
        if not shown or used_on is None:
            raise CardDataError("invalid_approval")
        currency = _text(row.get("resAccountCurrency")) or "KRW"
        amount = _amount(row.get("resUsedAmount")) if currency == "KRW" else _amount(row.get("resKRWAmt"))
        status, cancel = _text(row.get("resCancelYN")) or "0", _amount(row.get("resCancelAmount"))
        unknown = None
        if amount is None:
            unknown = "amount_unknown"
        elif amount < 0:
            unknown = "negative_amount"
        elif status == PARTIAL and cancel is None:
            unknown = "cancel_amount_unknown"
        elif cancel is not None and not 0 <= cancel <= amount:
            raise CardDataError("invalid_approval")
        payment_type, months = _text(row.get("resPaymentType")), _count(row.get("resInstallmentMonth"))
        merchant = _short(row.get("resMemberStoreName"), 200)
        approval_no = _code(row.get("resApprovalNo"), 50)
        rows.append({
            "card_number": re.sub(r"[\s-]", "", shown),
            "unknown_reason": unknown,
            # 취소 상태 · 취소액은 나중에 바뀌므로 식별 키에 넣지 않는다
            "_identity": (used_on, row.get("resUsedTime"), approval_no, row.get("resUsedAmount"), merchant,
                          payment_type, row.get("resInstallmentMonth"), currency),
            "occurred_on": used_on,
            "occurred_time": _time(row.get("resUsedTime")),
            "amount": amount,
            "merchant_name": merchant,
            "source_description": merchant,
            "installment_months": 0 if payment_type == "1" else months,   # 모델: 일시불 0, 모르면 null
            "approval_no": approval_no,
            # 기존 거래 규칙(api.cancel_info): 취소면 취소액(없으면 전액), 거절도 쓰지 않은 돈이라 전액 취소로 둔다
            "is_canceled": status in (CANCELED, DECLINED),
            "canceled_amount": cancel if status in (CANCELED, PARTIAL) else (amount if status == DECLINED else None),
            "source_detail": {"cancelStatus": status, "currency": currency,
                              "homeForeign": _code(row.get("resHomeForeignType"), 5)},
        })
    if covered_from > covered_to:
        raise CardDataError("invalid_approval")
    counts = {}
    for row in rows:
        identity = row.pop("_identity")
        counts[identity] = counts.get(identity, 0) + 1
        row["identity"] = (*identity, counts[identity])
    return rows, (covered_from, covered_to)


def approval_source_key(card_key, identity):
    return _key(card_key, *identity)


def month_range(month, today):
    """청구서 조회 기록 기간: 그 달 1일 ~ 말일 (이번 달이면 오늘까지)"""
    first = datetime.strptime(month + "01", "%Y%m%d").date()
    nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return first, min(date.fromordinal(nxt.toordinal() - 1), today)
