"""
finance 앱 API — 금융 동의 · 계좌 · 연결 (docs/api/finance-connect.md)
- 6. POST /api/finance/consents/          금융 조회 동의 저장
- 7. GET  /api/finance/connect/accounts/  계좌 후보 보기
- 8. PUT  /api/finance/connect/accounts/  계좌 선택 저장
- 9. GET  /api/finance/connections/       연결 목록 보기
모두 저장된 값만 읽고 쓴다. CODEF 를 새로 부르지 않는다 (10/22 O)
"""
import hashlib
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.idempotency import idempotent
from users.models import Consent

from .display import account_display, masked_tail, organization_name
from .inputs import body_dict, reject_unknown_fields
from .models import BankAccount, Connection

PURPOSE = "finance_inquiry"  # 금융 조회 동의 목적 코드 (명세 '확인 필요': 최종 코드는 팀 확인)
SCOPES = {choice.value for choice in Consent.ProductScope}  # bank · card · loan
# 동의 문서 버전 → 원문. 해시는 서버가 원문으로 만든다 (★2: 화면이 보내지 않음)
# 동의 문구 최종본이 정해지면(명세 '확인 필요') 아래 문장을 그 원문으로 바꾼다
CONSENT_DOCUMENTS = {"v1": "머니쿠션 금융 정보 조회 동의 v1 (최종 문구 확정 전 임시 원문)"}


class ConsentRequired(PermissionDenied):
    """유효한 은행 조회 동의가 없음 → 403 consent_required (화면은 동의 화면으로)"""
    default_code = "consent_required"
    default_detail = "계좌 조회 동의가 필요해요."


def has_bank_consent(user):
    """철회하지 않은 은행 조회 동의가 있는지"""
    return Consent.objects.filter(
        user=user, purpose=PURPOSE, product_scope=Consent.ProductScope.BANK, revoked_at__isnull=True,
    ).exists()


# ---------- 6. 금융 조회 동의 저장 ----------

def consent_item(consent):
    return {
        "consent_id": consent.consent_id,
        "purpose": consent.purpose,
        "product_scope": consent.product_scope,
        "document_version": consent.document_version,
        "granted_at": consent.granted_at,
    }


def _check_consent_rows(rows):
    """consents 목록 검사. 틀리면 400, 맞으면 (범위, 버전) 목록 (같은 줄이 두 번 오면 한 번만)"""
    if not isinstance(rows, list) or not rows:
        raise ValidationError({"consents": ["동의 항목을 하나 이상 보내 주세요."]})
    picked = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError({"consents": ["동의 항목 형식을 확인해 주세요."]})
        reject_unknown_fields(row, {"purpose", "product_scope", "document_version"})
        # 글자가 아닌 값([] · {} · 숫자 등)은 먼저 걸러 400으로 답한다 (목록·객체는 집합 검사에서 500이 났음, PR #41 리뷰)
        for field in ("purpose", "product_scope", "document_version"):
            if not isinstance(row.get(field), str):
                raise ValidationError({field: ["글자로 보내 주세요."]})
        if row.get("purpose") != PURPOSE:
            raise ValidationError({"purpose": ["알 수 없는 동의 목적이에요."]})
        if row.get("product_scope") not in SCOPES:
            raise ValidationError({"product_scope": ["알 수 없는 조회 범위예요."]})
        if row.get("document_version") not in CONSENT_DOCUMENTS:
            raise ValidationError({"document_version": ["지원하지 않는 동의 문서 버전이에요."]})
        key = (row["product_scope"], row["document_version"])
        if key not in picked:
            picked.append(key)
    return picked


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent()
def consents(request):
    """6. POST /api/finance/consents/ — 201 {"items": [...]}
    - 은행 동의는 필수. 이번에 보냈거나 이미 유효한 은행 동의가 있어야 한다 (카드 · 대출은 선택, 10/22 V)
    - 같은 범위 · 같은 문서 버전의 유효한 동의가 이미 있으면 새로 만들지 않고 그 기록을 돌려준다 (10/22 N⑥)"""
    data = body_dict(request)
    reject_unknown_fields(data, {"consents"})
    picked = _check_consent_rows(data.get("consents"))

    items = []
    with transaction.atomic():
        # 회원 행을 잠가 같은 회원의 동의 저장을 한 번에 하나씩 처리한다.
        # 요청 번호가 다른 같은 동의가 동시에 와도, 뒤의 요청은 앞의 저장이 끝난 뒤 기존 기록을 찾아 재사용한다 (PR #41 리뷰)
        list(get_user_model().objects.select_for_update().filter(pk=request.user.pk).values_list("pk", flat=True))
        if not any(scope == Consent.ProductScope.BANK for scope, _ in picked) and not has_bank_consent(request.user):
            raise ValidationError({"consents": ["계좌 조회 동의는 꼭 필요해요."]})
        for scope, version in picked:
            found = Consent.objects.filter(
                user=request.user, purpose=PURPOSE, product_scope=scope,
                document_version=version, revoked_at__isnull=True,
            ).order_by("granted_at").first()
            if found is None:
                found = Consent.objects.create(
                    user=request.user, purpose=PURPOSE, product_scope=scope, document_version=version,
                    document_hash=hashlib.sha256(CONSENT_DOCUMENTS[version].encode("utf-8")).hexdigest(),
                )
            items.append(consent_item(found))
    return Response({"items": items}, status=status.HTTP_201_CREATED)


# ---------- 7 · 8. 계좌 후보 보기 · 계좌 선택 저장 ----------

def candidate_accounts(user):
    """계좌 후보 = 이 회원의 해제되지 않은 은행 연결에서 가져온 입출금 계좌 (10/22 V · 결정기록 M)"""
    return (
        BankAccount.objects.filter(
            user=user,
            connection__business_type=Connection.BusinessType.BANK,
            connection__disconnected_at__isnull=True,
            account_type=BankAccount.AccountType.DEMAND,
        )
        .select_related("connection")
        .order_by("connection__created_at", "masked_number", "account_id")
    )


def account_item(account):
    shown = account_display(account)
    return {
        "account_id": account.account_id,
        "connection_id": account.connection_id,
        "display_name": shown["display_name"],
        "organization_name": organization_name(account.connection),
        "masked_number": masked_tail(account.masked_number),
        "name": account.name,
        "status": shown["status"],  # 계좌 상태 = 연결 상태 (H-07)
        "balance": account.balance,
        "is_selected": account.is_selected,
        "fetched_at": account.fetched_at,
    }


def _selected_ids(data, candidates):
    """selectedAccountIds 검사. 없는 번호 · 남의 계좌 · 고를 수 없는 계좌 · 중복은 모두 같은 문구 (어느 번호인지 안 알림)"""
    raw = data.get("selected_account_ids")
    if not isinstance(raw, list) or not raw:
        raise ValidationError({"selected_account_ids": ["계좌를 하나 이상 골라 주세요."]})
    allowed = {account.account_id for account in candidates}
    picked = set()
    for value in raw:
        try:
            account_id = uuid.UUID(str(value))
        except ValueError:
            account_id = None
        if account_id is None or account_id not in allowed or account_id in picked:
            raise ValidationError({"selected_account_ids": ["선택할 수 없는 계좌예요."]})
        picked.add(account_id)
    return picked


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def connect_accounts(request):
    """7. GET /api/finance/connect/accounts/ — {"items": [...]} 입출금 계좌 후보
    8. PUT /api/finance/connect/accounts/ {"selectedAccountIds": [...]} — 고른 계좌만 선택, 나머지 후보는 선택 해제
       하나라도 틀리면 아무것도 바꾸지 않는다. 응답은 7과 같은 모양"""
    if not has_bank_consent(request.user):
        raise ConsentRequired()

    if request.method == "PUT":
        data = body_dict(request)
        reject_unknown_fields(data, {"selected_account_ids"})
        candidates = list(candidate_accounts(request.user))
        picked = _selected_ids(data, candidates)
        with transaction.atomic():
            ids = [account.account_id for account in candidates]
            BankAccount.objects.filter(account_id__in=ids).exclude(account_id__in=picked).update(is_selected=False)
            BankAccount.objects.filter(account_id__in=picked).update(is_selected=True)

    return Response({"items": [account_item(account) for account in candidate_accounts(request.user)]})


# ---------- 9. 연결 목록 보기 ----------

def connection_item(connection):
    account_ids = [account.account_id for account in connection.bank_accounts.all()]
    card_ids = [card.card_id for card in connection.cards.all()]
    return {
        "connection_id": connection.connection_id,
        "business_type": connection.business_type,
        "organization_name": organization_name(connection),
        "alias": connection.alias,
        "status": connection.status,
        "connected_at": connection.created_at,
        "last_authenticated_at": connection.last_authenticated_at,
        "account_count": len(account_ids),
        "card_count": len(card_ids),
        "account_ids": account_ids,
        "card_ids": card_ids,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def connections(request):
    """9. GET /api/finance/connections/ — 해제하지 않은 기관 연결 목록 (disconnected 는 안 나옴, ★3)
    connectedId · CODEF 키 · 인증 정보는 넣지 않는다"""
    rows = (
        Connection.objects.filter(user=request.user, disconnected_at__isnull=True)
        .prefetch_related("bank_accounts", "cards")
        .order_by("created_at")
    )
    return Response({"items": [connection_item(connection) for connection in rows]})
