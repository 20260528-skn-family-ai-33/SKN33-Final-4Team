"""동의 철회 시 범위별 원천 데이터와 파생 기록 정리. 호출자는 회원 잠금을 잡는다."""
from django.db.models import Q
from django.db.models.deletion import Collector, RestrictedError
from django.utils import timezone
from rest_framework.exceptions import APIException

from budgets.models import Budget, BudgetCycle, BudgetCategorySetting, BudgetItem, CushionEntry, ReservePlan, SpendingAllocation
from chat.models import AsyncTask, ChatMessage, Conversation
from finance.models import (BankAccount, Card, CardApprovalReview, CardBill, Connection, FinancialTransaction,
                            Loan, PaymentDue, PaymentMatch, RecurringExpense, RecurringSaving, SyncRun)
from .models import Consent, FinancialProfile, IdempotencyRecord


class PrivacyBusy(APIException):
    status_code = 409
    default_code = "privacy_busy"
    default_detail = "진행 중인 조회·분석이 있어요. 작업이 끝난 뒤 다시 시도해 주세요."


def check_idle(user):
    # 유효 기간이 지난 기록의 만료 처리는 호출 전에 config.tasks.expire_stale 이 따로 커밋한다 (R20)
    if (AsyncTask.objects.filter(user=user, status__in=["queued", "running"]).exists()
            or SyncRun.objects.filter(user=user, status="running").exists()):
        raise PrivacyBusy()


def delete_together(querysets):
    """같이 삭제할 데이터의 RESTRICT 참조를 모아 검증한다. 범위 밖 참조는 삭제를 중단한다."""
    collector = Collector(using="default")
    for rows in querysets:
        collector.collect(rows, fail_on_restricted=False)
    for model, instances in collector.data.items():
        collector.clear_restricted_objects_from_set(model, instances)
    for rows in collector.fast_deletes:
        collector.clear_restricted_objects_from_queryset(rows.model, rows)
    remaining = [obj for fields in collector.restricted_objects.values() for objects in fields.values() for obj in objects]
    if remaining:
        raise RestrictedError("삭제 범위 밖의 참조가 있어요.", remaining)
    collector.delete()


def consent_states(user):
    records = Consent.objects.filter(user=user, purpose="finance_inquiry").order_by("-granted_at", "-pk")
    rows = list(records)
    from .deletion import open_deletions, state  # deletion 이 이 파일을 불러서 여기선 늦게 부른다
    pending = {row.scope: state(row) for row in open_deletions(user.pk)}
    items = []
    for scope in ("bank", "card", "loan"):
        scoped = [row for row in rows if row.product_scope == scope]
        active = next((row for row in scoped if row.revoked_at is None), None)
        latest = active or (scoped[0] if scoped else None)
        items.append({
            "product_scope": scope, "required": scope == "bank",
            "status": "granted" if active else "revoked" if latest else "not_granted",
            "document_version": latest.document_version if latest else None,
            "granted_at": latest.granted_at if latest else None,
            "revoked_at": latest.revoked_at if latest else None,
            "deletion_status": pending.get(scope),  # null=삭제 없음 · 완료, processing · retry_required (R21)
        })
    return {"items": items}


def require_bank_not_revoked(user):
    rows = Consent.objects.filter(user=user, purpose="finance_inquiry", product_scope="bank")
    if rows.filter(revoked_at__isnull=False).exists() and not rows.filter(revoked_at__isnull=True).exists():
        from finance.api_connect import ConsentRequired
        raise ConsentRequired("계좌 조회 동의를 철회했어요. 다시 동의하고 연결한 뒤 예산을 설정해 주세요.")


def purge_scope(user, scope):
    accounts = BankAccount.objects.filter(user=user) if scope == "bank" else BankAccount.objects.none()
    cards = Card.objects.filter(user=user, origin="provider") if scope == "card" else Card.objects.none()
    loans = Loan.objects.filter(user=user, origin="provider") if scope == "loan" else Loan.objects.none()
    bills = CardBill.objects.filter(user=user) if scope == "card" else CardBill.objects.none()
    txs = FinancialTransaction.objects.filter(user=user, source_kind=scope)
    tx_ids = {str(pk) for pk in txs.values_list("pk", flat=True)}
    account_ids, card_ids = set(accounts.values_list("pk", flat=True)), set(cards.values_list("pk", flat=True))

    # 직접 작성한 금액·이름은 남기고 지워지는 조회 데이터의 연결·근거만 제거한다.
    # 조회 자료에서 자동 탐지한 항목은 금액·가맹점도 파생 개인정보이므로 함께 지운다.
    expense_ids, saving_ids = [], []
    for model, deleted in ((RecurringExpense, expense_ids), (RecurringSaving, saving_ids)):
        for row in model.objects.filter(user=user):
            evidence = row.evidence or []
            touched = bool(tx_ids.intersection(map(str, evidence)))
            if model is RecurringExpense:
                touched |= row.payment_account_id in account_ids or row.payment_card_id in card_ids
            else:
                touched |= row.from_account_id in account_ids or row.to_account_id in account_ids
            if touched and row.source_type == "detected":
                deleted.append(row.pk)
            elif touched:
                row.evidence = [value for value in evidence if str(value) not in tx_ids]
                row.save(update_fields=["evidence"])

    expenses = RecurringExpense.objects.filter(user=user, pk__in=expense_ids)
    savings = RecurringSaving.objects.filter(user=user, pk__in=saving_ids)
    dues = PaymentDue.objects.filter(user=user).filter(Q(bill__in=bills) | Q(loan__in=loans) | Q(recurring__in=expenses))
    matches = PaymentMatch.objects.filter(user=user).filter(Q(transaction__in=txs) | Q(due__in=dues))
    retained_due_ids = list(matches.exclude(due__in=dues).values_list("due_id", flat=True).distinct())
    sync_products = {
        "bank": ["bank_sync", "account_list", "transaction_list"],
        "card": ["card_sync", "card_bill_list", "card_approval_list"],
        "loan": ["loan_sync", "loan_transaction_list"],
    }[scope]
    runs = SyncRun.objects.filter(user=user).filter(
        Q(product_code__in=sync_products) | Q(account__in=accounts) | Q(card__in=cards) | Q(loan__in=loans),
    )
    # 예산 스냅샷·대화·작업·응답 캐시는 여러 범위가 섞여 있어 통째로 지우고 다시 계산한다.
    derived = [model.objects.filter(user=user) for model in (
        BudgetCategorySetting, BudgetItem, SpendingAllocation, CushionEntry, Budget, BudgetCycle, ReservePlan,
        ChatMessage, Conversation, AsyncTask, IdempotencyRecord,
    )]
    roots = [matches, dues, expenses, savings, txs, runs, accounts, cards, loans, bills, *derived]
    if scope == "card":
        roots.append(CardApprovalReview.objects.filter(user=user))
    delete_together(roots)
    from budgets.api import refresh_due_state
    for due in PaymentDue.objects.filter(user=user, pk__in=retained_due_ids):
        refresh_due_state(due, timezone.localdate())
    FinancialProfile.objects.filter(user=user).update(
        living_budget=None, savings_floor=None, cushion_monthly=None,
        monthly_disposable_income=None, disposable_income_basis=None,
    )


def disconnect_revoked_scope(user, scope):
    active = set(Consent.objects.filter(user=user, purpose="finance_inquiry", revoked_at__isnull=True)
                 .values_list("product_scope", flat=True))
    kinds = {"card"} if scope == "card" else {"loan"} if scope == "loan" else set()
    # 은행 인증은 계좌와 대출 조회가 공유한다. 둘 다 철회했을 때만 해제한다.
    if not active.intersection({"bank", "loan"}):
        kinds.add("bank")
    Connection.objects.filter(user=user, business_type__in=kinds, disconnected_at__isnull=True).update(
        status="disconnected", disconnected_at=timezone.now(),
    )
