"""CODEF 은행 조회 결과를 저장한다. 금융 원문은 출력하지 않는다."""
import hashlib

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from users.models import Consent

from . import codef
from .models import BankAccount, Connection, FinancialTransaction, SyncRun
from .pipeline import RETRY_STATE_KEY, process_new_transactions, retry_failed_transactions


class BankConsentRequired(PermissionError):
    """은행 조회 동의가 없거나 조회 중 철회됨 → 저장하지 않음 (다시 불러오기에서는 '동의 안 함'으로 건너뜀)"""


class BankConnectionGone(ValueError):
    """지정한 연결이 이미 해제됨 → 새 연결을 만들지 않고 멈춤 (다시 불러오기 PR #53 리뷰)"""


def _check_bank_consent(user, connection):
    # sync_loans._check_consent 와 같은 기준 (회원 전체 동의 또는 이 연결 동의)
    if not Consent.objects.filter(
        Q(connection__isnull=True) | Q(connection=connection),
        user=user, purpose="finance_inquiry", product_scope="bank", revoked_at__isnull=True,
        granted_at__lte=timezone.now(),
    ).exists():
        raise BankConsentRequired("계좌 조회 동의가 필요해요.")


def _mask_account_numbers(value, account_numbers):
    # 이름·거래 설명에 섞인 조회 계좌번호도 남기지 않는다.
    if isinstance(value, str):
        for number in account_numbers:
            value = value.replace(number, codef.mask(number))
        return value
    if isinstance(value, dict):
        return {key: _mask_account_numbers(item, account_numbers) for key, item in value.items()}
    return value


def sync_bank(user, connected_id, org, start, end, *, connection_id=None, require_consent=False):
    """은행 계좌 · 거래 저장.
    - connection_id: 이 연결에만 저장한다. 해제됐으면 새 연결을 만들지 않고 BankConnectionGone (다시 불러오기 · 새 연결 API)
      없으면 기존처럼 활성 연결을 찾거나 만든다 (개발용 codef_sync 명령)
    - require_consent: 외부 조회 전과 저장 트랜잭션 안에서 은행 조회 동의를 확인한다 (조회 중 철회되면 저장 안 함)"""
    if not connected_id:
        raise ValueError("CODEF 연결 ID가 필요해요.")
    if start > end:
        raise ValueError("조회 시작일은 종료일보다 늦을 수 없어요.")

    # 실패 기록의 연결 FK는 계좌·거래 롤백 뒤에도 유지한다.
    with transaction.atomic():
        if connection_id is None:
            connection, _ = Connection.objects.get_or_create(
                user=user, business_type="bank", organization_code=org,
                disconnected_at=None, defaults={"status": "connected"},
            )
        else:
            connection = Connection.objects.filter(
                pk=connection_id, user=user, business_type="bank", organization_code=org,
            ).first()
            if connection is None or connection.disconnected_at is not None:
                raise BankConnectionGone("연결이 해제되어 불러오지 않았어요.")
        if require_consent:
            _check_bank_consent(user, connection)
        run = SyncRun.objects.create(
            user=user, connection=connection, product_code="bank_sync",
            requested_from=start, requested_to=end, status="running",
        )

    try:
        # 외부 조회를 모두 마친 뒤 저장 트랜잭션을 연다.
        accounts = codef.list_accounts(connected_id, org)
        account_numbers = {account["account_no"] for account in accounts}
        if any(not isinstance(number, str) or not number.strip() for number in account_numbers):
            raise ValueError("조회된 계좌 식별값이 올바르지 않아요.")
        account_numbers = sorted(account_numbers, key=len, reverse=True)
        fetched_accounts = []
        for data in accounts:
            rows = (
                codef.list_transactions(connected_id, org, data["account_no"], start, end)
                if data["kind_code"] == "11" else []
            )
            fetched_accounts.append((data, rows))

        with transaction.atomic():
            # 홈 · 고정지출 · 거래 처리와 같은 순서: 회원 → 연결 → 계좌 → 거래.
            # 외부 조회 중에는 잠그지 않고, 저장할 때 연결·계좌보다 먼저 잠근다.
            get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk).first()
            # 같은 연결의 저장은 한 번에 하나씩 처리한다.
            connection = Connection.objects.select_for_update().get(pk=connection.pk)
            if connection.disconnected_at is not None:
                raise ValueError("조회 중 연결이 해제되어 저장하지 않았어요.")
            if require_consent:  # 조회 중 동의를 철회했으면 저장하지 않는다 (회원 행을 잠근 뒤라 동의 저장과 순서가 맞음)
                _check_bank_consent(user, connection)
            new_ids = []
            existing_count = 0
            saved_accounts = []
            for data, rows in fetched_accounts:
                number = data["account_no"]
                key = hashlib.sha256(number.encode()).hexdigest()
                try:
                    # 같은 회원·은행의 이전 계좌를 옮겨 ID와 납부 연결을 보존한다.
                    account = BankAccount.objects.select_for_update(of=("self",)).filter(
                        Q(connection=connection) | Q(connection__disconnected_at__isnull=False),
                        user=user, source_account_key=key, connection__user=user,
                        connection__business_type="bank", connection__organization_code=org,
                    ).get()
                except BankAccount.DoesNotExist:
                    account = BankAccount.objects.create(user=user, connection=connection, source_account_key=key)
                account.connection = connection
                account.source_account_ref_ciphertext = None
                account.masked_number = codef.mask(number)
                account.name = _mask_account_numbers(data["name"], account_numbers)
                account.balance = data["balance"]
                # 11 외의 예금 코드 매핑은 팀 확인 뒤 추가한다.
                account.account_type = "demand" if data["kind_code"] == "11" else "other"
                account.field_states = {**account.field_states, "codef_deposit_code": data["kind_code"]}
                account.fetched_at = timezone.now()
                account.save(update_fields=[
                    "connection",
                    "source_account_ref_ciphertext", "masked_number", "name", "balance",
                    "account_type", "field_states", "fetched_at",
                ])
                saved_accounts.append((account, rows))

            # 조회 기간 밖의 실패도 재시도하되 이번에 생긴 거래는 제외한다.
            retry_ids = list(FinancialTransaction.objects.filter(
                user=user, source_kind="bank", account__connection=connection,
                field_states__has_key=RETRY_STATE_KEY,
            ).values_list("pk", flat=True))

            for account, rows in saved_accounts:
                for row in rows:
                    if not row.get("source_key"):
                        raise ValueError("조회된 거래 식별값이 비어 있어요.")
                    defaults = {
                        **row, "user": user, "source_kind": "bank",
                        # 기본 유형은 매칭 입구에서 다시 판단한다.
                        "transaction_type": "transfer" if row["direction"] == "out" else "income",
                        "confirmation_state": "needs_review", "confirmed_category_id": "UNCLASSIFIED",
                    }
                    for field in ("source_description", "source_detail"):
                        defaults[field] = _mask_account_numbers(row.get(field), account_numbers)
                    tx, created = FinancialTransaction.objects.get_or_create(
                        account=account, source_key=row["source_key"], defaults=defaults,
                    )
                    if created:
                        new_ids.append(tx.pk)
                    else:
                        existing_count += 1

            retry_failed_transactions(user, retry_ids)
            process_new_transactions(user, new_ids)
            run.status = "success"
            run.business_code = codef.SUCCESS
            run.covered_from, run.covered_to = start, end
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "business_code", "covered_from", "covered_to", "completed_at"])
    except Exception as exc:
        # 금융 정보가 섞일 수 있는 메시지 대신 오류 코드만 남긴다.
        SyncRun.objects.filter(pk=run.pk).update(
            status="failed", completed_at=timezone.now(),
            business_code=exc.code if isinstance(exc, codef.CodefError) else None,
            error_code="provider_error" if isinstance(exc, codef.CodefError) else "sync_error",
        )
        raise

    return {
        "account_count": len(accounts),
        "new_transaction_count": len(new_ids),
        "existing_transaction_count": existing_count,
    }
