"""원화 금액이 없는 외화 승인(codef_cards.FOREIGN_PENDING)의 원화 금액을 저장된 다른 자료에서 찾는다 (11/09).

11/07 NH: USD 승인 줄은 resKRWAmt 가 끝내 비어 있었지만, 같은 결제가
  · 청구서 상세의 '전체 구매 금액'(original_purchase_amount)에 원화로 있고
  · 체크카드라 같은 날 계좌 출금으로도 원화가 빠져 있었다.
같은 결제라는 근거가 확실할 때만 이 두 곳의 원화 금액을 쓴다. 금액을 짐작하거나 환율로 만들지 않는다.
- 근거: 같은 날 · 가맹점 이름이 같고(공백 · 대소문자 무시. 한쪽이 잘렸으면 짧은 쪽 6자 이상일 때 앞부분 일치) 후보가 한 건.
  같은 날 같은 이름의 외화 승인이 여럿이면 어느 것의 금액인지 몰라 쓰지 않는다. 청구서와 출금이 둘 다 있으면 금액이 같아야 한다.
- 다른 거래의 짝은 후보에서 뺀다: 금액을 아는 승인(이번 응답 · 저장된 거래)과 날짜 · 금액 · 이름이 맞는 항목 · 출금,
  이미 같은 구매 · 내 계좌 이체로 이은 출금, 납부에 붙은 출금, 사용자가 확정한 출금, 다른 외화 승인에 이미 쓴 항목 · 출금.
- 계좌 출금은 체크카드(transfer_rules.link_same_purchases 와 같은 판단)만 본다. 신용카드는 결제 때 출금이 없다.
- 정상 승인만 채운다. 취소 · 부분취소 · 거절은 취소 금액도 외화로 올 수 있어 원화 순액을 정할 수 없다.
청구서는 같은 조회에서 먼저 저장되므로(sync_cards) 이번에 받은 청구서도 본다. 계좌 출금은 은행 연결로 이미 저장된 것만 본다.
11/09 NH 실데이터(USD 7건): 0달러 승인 4건(codef_cards 에서 0원) · 이름으로 채움 3건(환율 1,372 · 1,520 · 1,520원/달러).
"""
from dataclasses import dataclass

from django.db.models import Exists, OuterRef, Q

from .codef_cards import FOREIGN_PENDING
from .models import CardBillItem, FinancialTransaction as Tx, PaymentMatch, TransactionLink
from .transfer_rules import squeeze

MIN_PREFIX = 6                                # 잘린 이름을 같은 가게로 볼 최소 글자 수 (공백 뺀 길이)
BILL, BANK = "card_bill", "bank_withdrawal"   # 거래 field_states.amount.source 값
COUNTED_ITEMS = ("one_time", "installment")


@dataclass
class Won:
    amount: int
    sources: list
    withdrawal: Tx | None = None


def same_store(a, b):
    a, b = squeeze(a), squeeze(b)
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return short == long_ or (len(short) >= MIN_PREFIX and long_.startswith(short))


def is_check_card(card):
    return "체크" in (card.name or "") or "체크" in (card.card_type or "")


class WonFinder:
    """save_approvals 한 번(카드사 한 곳의 응답 하나)의 외화 승인을 한꺼번에 본다. 같은 항목 · 출금을 두 승인에 쓰지 않는다.

    resolve(row) → (카드, 이미 저장된 거래): 카드를 하나로 못 정하면 (None, None). 저장된 거래가 있으면 그 거래를 그대로 이어 간다
    (그 출금은 이미 이 거래의 짝이라 다시 찾으면 안 된다)."""

    def __init__(self, user, issuer_code, rows, resolve):
        self.user, self.issuer_code, self.result, self.taken = user, issuer_code, {}, set()
        pending = [row for row in rows if row["unknown_reason"] == FOREIGN_PENDING]
        if not pending:
            return
        self.known = [(row["occurred_on"], row["amount"], row["merchant_name"]) for row in rows if row["amount"] is not None]
        rest = []
        for row in pending:
            card, saved = resolve(row)
            if saved is not None:
                self.result[row["identity"]] = saved
                self.known.append((saved.occurred_on, saved.amount, saved.merchant_name))
            elif card is not None:
                rest.append((row, card))
        for row, card in rest:
            won = self._find(row, card, pending)
            if won is not None:
                self.result[row["identity"]] = won

    def won(self, identity):
        """이미 저장된 거래 · Won · None(못 찾음)"""
        return self.result.get(identity)

    def _paired(self, day, amount, name):
        """이 날 · 금액 · 이름이 금액을 아는 다른 승인의 짝인지"""
        if any(d == day and a == amount and same_store(n, name) for d, a, n in self.known):
            return True
        return any(same_store(tx.merchant_name, name) for tx in Tx.objects.filter(
            user=self.user, source_kind="card", card__issuer_code=self.issuer_code, occurred_on=day, amount=amount,
        ))

    def _items(self, day, name):
        items = [
            item for item in CardBillItem.objects.filter(
                user=self.user, bill__issuer_code=self.issuer_code, purchased_on=day,
                item_type__in=COUNTED_ITEMS, original_purchase_amount__gt=0,
            )
            if same_store(item.merchant_name, name) and ("bill", item.pk) not in self.taken
            and not self._paired(day, item.original_purchase_amount, item.merchant_name)
        ]
        # 할부는 달마다 청구서에 다시 나온다 → 청구서마다 한 건 · 전체 구매 금액이 모두 같을 때만 한 결제로 본다
        bills = [item.bill_id for item in items]
        if len(bills) != len(set(bills)) or len({item.original_purchase_amount for item in items}) > 1:
            return []
        return items

    def _withdrawal(self, day, name):
        links = TransactionLink.objects.filter(
            Q(from_transaction=OuterRef("pk")) | Q(to_transaction=OuterRef("pk")),
            relation_type__in=[TransactionLink.Relation.SAME_PURCHASE, TransactionLink.Relation.OWN_TRANSFER],
        )
        found = [
            tx for tx in Tx.objects.filter(
                user=self.user, source_kind="bank", direction="out", transaction_type=Tx.TransactionType.TRANSFER,
                is_canceled=False, occurred_on=day,
            ).exclude(confirmation_state=Tx.ConfirmationState.CONFIRMED).exclude(
                Exists(PaymentMatch.objects.filter(transaction=OuterRef("pk"))),
            ).exclude(Exists(links))
            if same_store(tx.source_description, name) and ("bank", tx.pk) not in self.taken
            and not self._paired(day, tx.amount, tx.source_description)
        ]
        return found[0] if len(found) == 1 else None

    def _find(self, row, card, pending):
        day, name = row["occurred_on"], row["merchant_name"]
        if (row.get("source_detail") or {}).get("cancelStatus") != "0" or not squeeze(name):
            return None
        if sum(1 for other in pending if other["occurred_on"] == day and same_store(other["merchant_name"], name)) != 1:
            return None
        items = self._items(day, name)
        withdrawal = self._withdrawal(day, name) if is_check_card(card) else None
        amounts = {items[0].original_purchase_amount if items else None, withdrawal.amount if withdrawal else None} - {None}
        if len(amounts) != 1:
            return None
        self.taken.update(("bill", item.pk) for item in items)
        if withdrawal is not None:
            self.taken.add(("bank", withdrawal.pk))
        return Won(amounts.pop(), [BILL] * bool(items) + [BANK] * bool(withdrawal), withdrawal)
