"""
계좌 · 카드 표시 객체 (docs/API_RULES.md '계좌 · 카드 표시', 10-21 확정)

거래 · 연결 관리 · 고정지출 · 홈 등 계좌 · 카드가 나오는 모든 API가 같은 모양을 쓰도록 여기 한 곳에서 만든다.
  계좌: {"account_id", "display_name": "국민 급여통장 · ****5678", "status"}
  카드: {"card_id", "display_name": "신한 Deep Dream · ****4321", "status"}
"""
import re

from . import institutions
from .codef import BANKS  # 은행 기관 코드 → 짧은 이름 (예: "0004" → "국민")
from .models import Connection


def organization_name(connection):
    """기관 이름. 은행은 '국민은행', 카드사는 '현대카드'처럼 (institutions.py 표). 표에 없으면 기관 코드를 그대로 쓴다"""
    short = BANKS.get(connection.organization_code)
    if connection.business_type == Connection.BusinessType.BANK and short:
        return f"{short}은행"
    return institutions.name_of(connection.business_type, connection.organization_code)


def masked_tail(masked_number):
    """가린 번호를 '****1234' 모양으로 (숫자가 없으면 받은 값 그대로)"""
    tail = last4(masked_number)
    return f"****{tail}" if tail else masked_number


def last4(masked_number):
    """마스킹 번호에서 숫자만 골라 끝 4자리 (숫자가 없으면 빈 문자)"""
    digits = re.sub(r"\D", "", masked_number or "")
    return digits[-4:]


def _with_number(label, masked_number):
    tail = last4(masked_number)
    return f"{label} · ****{tail}" if tail else label


def account_display(account):
    """계좌: {은행 짧은 이름} {별칭 → 계좌명 → '입출금'} · ****{끝 4자리}. 계좌가 없으면 None"""
    if account is None:
        return None
    bank = BANKS.get(account.connection.organization_code, "")
    label = f"{bank} {account.alias or account.name or '입출금'}".strip()
    return {
        "account_id": account.account_id,
        "display_name": _with_number(label, account.masked_number),
        "status": account.connection.status,
    }


def card_display(card):
    """카드: {카드명 → 없으면 '카드'} · ****{끝 4자리}. 카드가 없으면 None
    규칙상 카드명이 없으면 '{카드사} 카드'인데, 카드사 코드 → 이름 표가 아직 없어서 '카드'만 쓴다 (cards.md 표 생기면 교체)
    직접 등록한 카드(연결 없음)는 끊길 연결이 없으므로 connected"""
    if card is None:
        return None
    status = card.connection.status if card.connection else Connection.Status.CONNECTED
    return {
        "card_id": card.card_id,
        "display_name": _with_number(card.name or "카드", card.masked_number),
        "status": status,
    }
