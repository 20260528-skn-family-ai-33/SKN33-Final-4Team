"""은행의 대출 스냅샷을 저장한다. 상환 거래 매칭·납부 생성은 별도 작업이다."""
import hashlib
import re

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from users.models import Consent

from . import codef
from .codef_loans import LoanDataError
from .models import Connection, Loan, Origin, SyncRun


def _check_consent(user, connection):
    if not Consent.objects.filter(
        Q(connection__isnull=True) | Q(connection=connection),
        user=user, purpose="finance_inquiry", product_scope="loan", revoked_at__isnull=True,
        granted_at__lte=timezone.now(),
    ).exists():
        raise PermissionError("대출 조회 동의가 필요해요.")


def _safe_name(value, accounts):
    if not value:
        return None
    # 상품명에 계좌번호(하이픈/공백 표시 포함)가 들어와도 원문은 저장하지 않는다.
    for account in accounts:
        number = account["account_no"]
        value = value.replace(number, codef.mask(number))
        display = account.get("display_number")
        if display:
            value = value.replace(display, codef.mask(number))
        if number.isdigit():
            value = re.sub(r"[\s-]*".join(map(re.escape, number)), codef.mask(number), value)
    return value


def sync_bank_loans(user, connected_id, org, start, end):
    """신뢰된 서버 호출 전용. connected_id는 호출자가 해당 회원의 연결에서 얻어야 한다.

    계좌별 조회를 모두 성공한 뒤 한 트랜잭션으로 저장한다. 같은 연결·계좌는
    갱신하며 수동 보완값/대출 ID/납부 연결은 보존한다. HTTP GET에서 호출하지 않는다.
    """
    if not connected_id or start > end:
        raise ValueError("연결 ID와 조회 기간을 확인해 주세요.")
    connection = Connection.objects.get(
        user=user, business_type="bank", organization_code=org,
        disconnected_at=None, status="connected",
    )
    _check_consent(user, connection)
    run = SyncRun.objects.create(
        user=user, connection=connection, product_code="loan_sync", status="running",
        requested_from=start, requested_to=end,
    )
    try:
        accounts = codef.list_loan_accounts(connected_id, org)
        snapshots = [
            (account, codef.get_loan_details(connected_id, org, account["account_no"], start, end))
            for account in accounts
        ]
        fetched_at = timezone.now()
        created_count = updated_count = skipped_count = 0
        with transaction.atomic():
            connection = Connection.objects.select_for_update().get(pk=connection.pk, user=user)
            if connection.disconnected_at is not None or connection.status != "connected":
                raise ValueError("조회 중 연결 상태가 바뀌어 저장하지 않았어요.")
            _check_consent(user, connection)
            for account, data in snapshots:
                key = hashlib.sha256(account["account_no"].encode()).hexdigest()
                matches = Loan.objects.select_for_update(of=("self",)).filter(
                    Q(connection=connection) | Q(connection__disconnected_at__isnull=False),
                    user=user, connection__user=user, connection__business_type="bank",
                    connection__organization_code=org, source_loan_key=key,
                )
                try:
                    loan = matches.get()
                    if loan.origin != Origin.PROVIDER:
                        raise LoanDataError("loan_origin_conflict")
                    # 늦게 끝난 이전 조회가 더 최근 스냅샷을 덮어쓰지 않는다.
                    if loan.fetched_at and loan.fetched_at > run.started_at:
                        skipped_count += 1
                        continue
                    updated_count += 1
                except Loan.DoesNotExist:
                    loan = Loan(user=user, connection=connection, source_loan_key=key, origin=Origin.PROVIDER)
                    created_count += 1
                loan.connection = connection
                loan.lender_name = codef.BANKS.get(org) or org
                loan.product_name = _safe_name(account["product_name"] or data["product_name"], accounts)
                provider_fields = {
                    "reported_principal": "reportedPrincipal", "reported_balance": "reportedBalance",
                    "reported_rate": "reportedRate", "maturity_date": "maturityDate",
                    "reported_payment_date": "reportedPaymentDate", "overdue": "overdue",
                }
                states = dict(loan.field_states or {})
                for field, json_field in provider_fields.items():
                    value = data[field]
                    previous = getattr(loan, field)
                    setattr(loan, field, value)
                    # 동일한 기관 값의 확인 기록만 유지. 값이 바뀌면 확인을 재사용하지 않는다.
                    if value is None or value != previous or json_field not in states:
                        states[json_field] = {
                            "source": "provider" if value is not None else None,
                            "confirmationStatus": "unconfirmed" if value is not None else "unknown",
                            "confirmedAt": None,
                        }
                states["codef_loan"] = {
                    "history_count": data["history_count"],
                    "unmapped_overdue": data["unmapped_overdue"],
                    "covered_from": data["covered_from"].isoformat() if data["covered_from"] else None,
                    "covered_to": data["covered_to"].isoformat() if data["covered_to"] else None,
                }
                loan.field_states, loan.fetched_at = states, fetched_at
                if loan._state.adding:
                    loan.save(force_insert=True)
                else:
                    loan.save(update_fields=[
                        "connection", "lender_name", "product_name", "field_states", "fetched_at",
                        *provider_fields,
                    ])
                # 요청 기간과 별도로 각 대출의 실제 응답 기간을 기록한다.
                SyncRun.objects.create(
                    user=user, connection=connection, loan=loan, product_code="loan_transaction_list",
                    requested_from=start, requested_to=end,
                    covered_from=data["covered_from"], covered_to=data["covered_to"],
                    status="success", business_code=codef.SUCCESS,
                    started_at=run.started_at, completed_at=fetched_at,
                )
            run.status, run.business_code, run.completed_at = "success", codef.SUCCESS, timezone.now()
            run.save(update_fields=["status", "business_code", "completed_at"])
        return {"loan_count": len(accounts), "created_count": created_count,
                "updated_count": updated_count, "skipped_count": skipped_count}
    except Exception as exc:
        # 기관 오류의 message/extra에는 개인정보가 섞일 수 있어 저장하지 않는다.
        business_code = None
        if isinstance(exc, codef.CodefError) and re.fullmatch(r"CF-[0-9]{5}", str(exc.code)):
            business_code = exc.code
        error_code = exc.code if isinstance(exc, LoanDataError) else "loan_sync_failed"
        SyncRun.objects.filter(pk=run.pk).update(
            status="failed", business_code=business_code, error_code=error_code, completed_at=timezone.now(),
        )
        raise
