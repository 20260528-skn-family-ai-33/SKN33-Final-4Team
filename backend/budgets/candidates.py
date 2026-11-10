"""고정지출 · 저축 후보 감지 (⑦, 11/4) — 거래 저장 뒤 처리(finance/pipeline.py detect_candidates)가 회원 잠금 안에서 부른다.

감지 조건 (transactions.md 결정기록 3번 · savings.md S-05): 같은 결제 수단 + 같은 받는 곳 · 한 달 1건 · 매달 같은 날 ±3일 ·
같은 금액(고정지출은 ±10%) · 3달 연속. 회원 전체 출금을 본다 (첫 연결 때 4달치가 한 번에 들어옴).
내 계좌 이체 · 카드값 · 대출 판단(finance/transfer_rules.py)은 결과만 읽고, 판단은 이 파일 안에서만 한다.
"""
import re
from collections import defaultdict

from django.db.models import Exists, OuterRef, Q

from finance.merchant_rules import normalize_merchant
from finance.models import (FinancialTransaction, PaymentMatch, RecurringExpense, RecurringSaving, SourceKind,
                            TransactionLink)
from finance.transfer_rules import LOAN_CANDIDATE, OWN_REVIEW

from .api import FIXED_TX_TYPES, day_in_month, id_set

MONTHS = 3      # 3달 연속
DAY_GAP = 3     # 매달 같은 날 ±3일
SAVING_WORDS = ("적금", "예금", "저축", "청약", "파킹", "납입")  # [임시] 적요에 있으면 저축 후보. 알파 뒤 실제 적요로 다시 정함


def detect_candidates(user, today):
    """반복 출금 묶음 → 고정지출 · 저축 후보(suggested · detected). 이미 있는 것과 같은 묶음은 상태와 상관없이 만들지 않음"""
    from users.models import User

    name = compact(User.objects.filter(pk=getattr(user, "pk", user)).values_list("nickname", flat=True).first() or "")
    taken = existing_keys(user)
    for key, txs in groups(user, today).items():
        if key in taken:
            continue
        own_to = key[2] if key[1] == "to" else None
        saving = own_to is not None or key[1] == "own" or is_saving_transfer(txs[-1], name)
        run = monthly_run(txs, today, exact=saving)
        if not run:
            continue
        latest = run[-1]
        if saving:
            make_saving(user, run, latest, own_to)
        else:
            make_expense(user, run, latest)
        taken.add(key)


# ---------- 반복 묶음 ----------

def label_of(tx):
    return normalize_merchant(tx.merchant_name or tx.source_description)


def compact(text):
    return re.sub(r"\s+", "", text or "")


def method_of(tx):
    return ("card", tx.card_id) if tx.source_kind == SourceKind.CARD else ("account", tx.account_id)


def groups(user, today):
    """같은 결제 수단 + 같은 받는 곳으로 묶은 출금 (오래된 순). 키 = (결제 수단, 종류, 받는 곳)
    내 계좌끼리 이체는 받는 계좌를 알면 그 계좌로, 모르면 적요로 묶음 (은행 거래에는 받는 계좌 번호가 없음)"""
    same_purchase = TransactionLink.objects.filter(
        Q(from_transaction=OuterRef("pk")) | Q(to_transaction=OuterRef("pk")),
        relation_type=TransactionLink.Relation.SAME_PURCHASE,
    )
    rows = (
        FinancialTransaction.objects.filter(
            user=user, direction="out", is_canceled=False, occurred_on__lte=today,
            transaction_type__in=[*FIXED_TX_TYPES, "own_transfer"],   # 카드값 · 대출 상환 · 저축 · 입금은 아님
        )
        .annotate(in_same_purchase=Exists(same_purchase))
        .exclude(source_kind=SourceKind.BANK, in_same_purchase=True)  # 체크카드의 계좌 쪽은 한 번만 셈
        .exclude(pk__in=PaymentMatch.objects.values("transaction"))   # 청구서 · 대출 · 고정지출 회차에 연결된 출금
        .order_by("occurred_on", "occurred_time", "created_at")
    )
    used = linked_evidence(user)  # 다른 고정지출 · 저축의 근거 거래
    own_to = dict(TransactionLink.objects.filter(
        user=user, relation_type=TransactionLink.Relation.OWN_TRANSFER,
    ).values_list("from_transaction_id", "to_transaction__account_id"))

    found = defaultdict(list)
    for tx in rows:
        if str(tx.pk) in used or (tx.field_states or {}).get(LOAN_CANDIDATE):
            continue
        if tx.transaction_type == "own_transfer" and own_to.get(tx.pk):
            found[(method_of(tx), "to", own_to[tx.pk])].append(tx)
            continue
        label = label_of(tx)
        if label:
            found[(method_of(tx), "own" if tx.transaction_type == "own_transfer" else "label", label)].append(tx)
    return found


def monthly_run(txs, today, exact):
    """가장 최근 거래부터 거꾸로 한 달 1건 · 같은 날 ±3일 · 같은 금액(±10%)이 이어진 거래 (3달 이상이면 오래된 순, 아니면 [])
    [임시] 가장 최근 거래가 이번 달이나 지난달이어야 함 (해지한 구독을 후보로 띄우지 않게)"""
    by_month = defaultdict(list)
    for tx in txs:
        by_month[(tx.occurred_on.year, tx.occurred_on.month)].append(tx)
    latest = txs[-1]
    year, month = latest.occurred_on.year, latest.occurred_on.month
    if (today.year * 12 + today.month) - (year * 12 + month) > 1:
        return []
    low, high = (latest.amount, latest.amount) if exact else ((latest.amount * 9 + 9) // 10, latest.amount * 11 // 10)
    run = []
    while len(by_month.get((year, month), [])) == 1:
        tx = by_month[(year, month)][0]
        if abs((tx.occurred_on - day_in_month(year, month, latest.occurred_on.day)).days) > DAY_GAP:
            break
        if not low <= tx.amount <= high:
            break
        run.append(tx)
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return run[::-1] if len(run) >= MONTHS else []


def is_saving_transfer(tx, name):
    """남에게 보낸 이체 중 저축으로 볼 것: 적요(띄어쓰기 무시)에 회원 이름 또는 저축 단어 (받는 계좌 종류로는 거르지 않음)
    사용자가 '내 계좌 이체가 아니에요'라고 한 이체는 이름을 근거로 쓰지 않는다 (R11). 저축 단어는 그대로 본다"""
    if tx.transaction_type != "transfer":
        return False
    text = compact(tx.source_description or tx.merchant_name)
    mine = name and name in text and (tx.field_states or {}).get(OWN_REVIEW) != "rejected"
    return bool(mine) or any(word in text for word in SAVING_WORDS)


# ---------- 이미 있는 것 ----------

def linked_evidence(user):
    used = set()
    for model in (RecurringExpense, RecurringSaving):
        for ids in model.objects.filter(user=user).values_list("evidence", flat=True):
            used |= id_set(ids)
    return used


def existing_keys(user):
    """이미 있는 고정지출 · 저축(상태 무관 — 후보 · 확정 · 중지 · 무시)의 (결제 수단, 종류, 받는 곳) — 같은 묶음을 다시 띄우지 않게
    이름 · 근거 거래의 받는 곳 · 저축의 받는 계좌로 만든다"""
    keys = set()
    evidence_ids = []
    for e in RecurringExpense.objects.filter(user=user):
        method = ("card", e.payment_card_id) if e.payment_card_id else ("account", e.payment_account_id)
        keys.add((method, "label", normalize_merchant(e.name)))
        evidence_ids += list(id_set(e.evidence))
    for s in RecurringSaving.objects.filter(user=user):
        method = ("account", s.from_account_id)
        label = normalize_merchant(s.name)
        keys |= {(method, "label", label), (method, "own", label)}
        if s.to_account_id:
            keys.add((method, "to", s.to_account_id))
        evidence_ids += list(id_set(s.evidence))
    for tx in FinancialTransaction.objects.filter(user=user, pk__in=evidence_ids):
        label = label_of(tx)
        keys |= {(method_of(tx), "label", label), (method_of(tx), "own", label)}
    own_to = TransactionLink.objects.filter(
        user=user, relation_type=TransactionLink.Relation.OWN_TRANSFER, from_transaction_id__in=evidence_ids,
    ).values_list("from_transaction__account_id", "to_transaction__account_id")
    keys |= {(("account", a), "to", b) for a, b in own_to if b}
    return keys


# ---------- 후보 만들기 ----------

def make_expense(user, run, latest):
    if latest.confirmed_category_id == "TRAVEL_LODGING":
        return  # [임시] 여행 · 숙박은 예산 밖 카테고리라 고정지출이 될 수 없음 (같은 호텔 정기 결제도 후보로 안 띄움)
    category = latest.confirmed_category_id
    if latest.transaction_type != "spending" or category in (None, "UNCLASSIFIED"):
        category = "ETC"  # [임시] 이체 · 확인 필요는 기타. 확정 전에 사용자가 고칠 수 있음
    if not 1_000 <= latest.amount <= 100_000_000:
        return
    RecurringExpense.objects.create(
        user=user, status="suggested", source_type="detected",
        name=(latest.merchant_name or latest.source_description or "고정지출")[:100], amount=latest.amount,
        payment_day=latest.occurred_on.day, category_id=category,
        payment_account_id=latest.account_id if latest.source_kind == SourceKind.BANK else None,
        payment_card_id=latest.card_id if latest.source_kind == SourceKind.CARD else None,
        evidence=[str(tx.pk) for tx in run],
    )


def make_saving(user, run, latest, to_account_id):
    from finance.models import BankAccount

    if to_account_id == latest.account_id or not 1_000 <= latest.amount <= 100_000_000:
        return
    to_account = BankAccount.objects.filter(pk=to_account_id).first() if to_account_id else None
    RecurringSaving.objects.create(
        user=user, status="suggested", source_type="detected",
        name=((to_account.name if to_account else None) or latest.source_description or latest.merchant_name or "저축")[:100],
        amount=latest.amount, transfer_day=latest.occurred_on.day,
        from_account_id=latest.account_id, to_account=to_account,
        evidence=[str(tx.pk) for tx in run],
    )
