"""CODEF 대출 응답 검증. 식별값·원문을 예외 메시지에 넣지 않는다."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


class LoanDataError(ValueError):
    def __init__(self, code="invalid_loan_data"):
        self.code = code
        super().__init__("CODEF 대출 응답을 확인할 수 없어 저장하지 않았어요.")


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise LoanDataError()
    return value.strip() or None


def _amount(value):
    value = _text(value)
    if value is None:
        return None
    if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value):
        raise LoanDataError()
    number = int(value.replace(",", ""))
    if number > 9223372036854775807:
        raise LoanDataError()
    return number


def _rate(value):
    value = _text(value)
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        raise LoanDataError()
    try:
        number = Decimal(value)
        # Loan.reported_rate는 Decimal(8, 5). 조용히 반올림하지 않는다.
        if number > Decimal("999.99999") or number != number.quantize(Decimal("0.00001")):
            raise LoanDataError()
    except InvalidOperation:
        raise LoanDataError() from None
    return number


def _date(value):
    value = _text(value)
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{8}", value):
        raise LoanDataError()
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise LoanDataError() from None


def _name(value):
    value = _text(value)
    if value and len(value) > 100:
        raise LoanDataError()
    return value


def normalize_loan_accounts(data):
    # resLoan 누락은 정상적인 빈 목록([])과 구분한다.
    if not isinstance(data, dict) or not isinstance(data.get("resLoan"), list):
        raise LoanDataError()
    accounts, seen = [], set()
    for row in data["resLoan"]:
        if not isinstance(row, dict):
            raise LoanDataError()
        number = _text(row.get("resAccount"))
        if not number or number in seen:
            raise LoanDataError("ambiguous_loan_identity")
        # 실행번호별 상세 조회 계약이 확인되기 전에는 다른 실행을 합치지 않는다.
        if _text(row.get("resAccountLoanExecNo")):
            raise LoanDataError("unsupported_loan_execution")
        if _text(row.get("resAccountCurrency")) != "KRW":
            raise LoanDataError("unsupported_loan_currency")
        seen.add(number)
        accounts.append({
            "account_no": number, "display_number": _text(row.get("resAccountDisplay")),
            "product_name": _name(row.get("resAccountName")),
        })
    return accounts


def normalize_loan_details(data, account_no):
    if not isinstance(data, dict) or _text(data.get("resAccount")) != account_no:
        raise LoanDataError("loan_identity_mismatch")
    history = data.get("resTrHistoryList")
    if not isinstance(history, list) or any(not isinstance(row, dict) for row in history):
        raise LoanDataError()
    start, end = _date(data.get("commStartDate")), _date(data.get("commEndDate"))
    if (start is None) != (end is None) or (start and start > end):
        raise LoanDataError()
    return {
        "reported_principal": _amount(data.get("resPrincipal")),
        "reported_balance": _amount(data.get("resLoanBalance")),
        "reported_rate": _rate(data.get("resRate")),
        "maturity_date": _date(data.get("resAccountEndDate")),
        "reported_payment_date": _date(data.get("resDatePayment")),
        "product_name": _name(data.get("resLoanKind")),
        # resState 코드표 미확정. 빈 값이나 미확인 코드를 False로 바꾸지 않는다.
        "overdue": None,
        "unmapped_overdue": _text(data.get("resState")) is not None,
        "covered_from": start, "covered_to": end, "history_count": len(history),
    }
