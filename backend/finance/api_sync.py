"""
POST /api/finance/sync/ — 금융 정보 다시 불러오기 (docs/api/finance-connect.md '금융 정보 다시 불러오기')
자동 불러오기 명령(finance_auto_sync)도 같은 실행(sync_connections)을 쓴다.

10/27 나은 결정
1. 대상: 기본 = 연결된 기관 전부 × 기관마다 동의한 범위 전부 (은행: 계좌 · 거래 + 대출 / 카드: 카드 · 청구서).
   connectionIds 로 특정 기관만. 동의 안 한 범위는 건너뜀 (실패로 안 셈)
2. 기간: 거래 = 마지막 성공 조회일 7일 전 ~ 오늘 / 카드 청구서 = 지난달 + 이번 달 / 잔액 · 카드 · 대출 = 지금 상태 통째로
3. 일부 실패: 성공한 기관만 저장, 실패는 기관별 결과에 이유 (새 연결 실패 안내 표와 같은 문구). 로그인 오류면 relogin
4. 카드번호 · 카드 비밀번호가 필요한 곳(현대카드)은 요청으로 받아 보내고 버림. 없으면 그 기관만 실패
5. 작업 방식: 202 {taskId, status} → GET /api/tasks/<id>/ 로 결과. 1차는 요청 안에서 끝까지 처리 (config/tasks.py)
6. 중복: 진행 중이면 그 taskId, 끝난 지 1분 안이면 마지막 결과 (Idempotency-Key 안 씀)
"""
import logging
import re
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config import tasks
from config.api import _camel_key as camel_key
from users.models import Consent

from . import codef, connected_id, institutions
from .api_new_connection import CARD_NO, CARD_PASSWORD, failure
from .codef_accounts import classify, missing_fields
from .inputs import body_dict, reject_unknown_fields
from .models import Connection, SyncRun
from .sync import sync_bank
from .sync_ranges import bill_months, refresh_start

logger = logging.getLogger(__name__)

TASK_TYPE = "finance_sync"
REUSE_WITHIN = timedelta(minutes=1)
MAX_CONNECTIONS = 50
PURPOSE = "finance_inquiry"
UNSELECTABLE = "선택할 수 없는 연결이에요."
CARD_FIELDS_NEEDED = "카드 비밀번호가 필요해요."


def loan_syncer():
    """대출 저장 (권세진 PR #50 finance/sync_loans.py). 아직 없으면 None → 대출은 '준비 중'으로 건너뜀"""
    try:
        from .sync_loans import sync_bank_loans
    except ImportError:
        return None
    return sync_bank_loans


def card_syncer():
    """카드 · 카드 승인 · 청구서 저장 (담당 확인 필요, finance/sync_cards.py 의 sync_card). 아직 없으면 None
    약속한 모양: sync_card(user, connected_id, org, start, end, bill_months, card_no=None, card_password=None)"""
    try:
        from .sync_cards import sync_card
    except ImportError:
        return None
    return sync_card


# ---------- 입력 검사 ----------

def _uuid(value):
    try:
        return uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None


def parse_body(request):
    """→ (대상 연결 목록, {연결 ID: {"card_no", "card_password"}}). 틀리면 400 (작업을 만들지 않음)"""
    data = body_dict(request)
    reject_unknown_fields(data, {"connection_ids", "card_credentials"})
    active = list(Connection.objects.filter(user=request.user, disconnected_at__isnull=True).order_by("created_at"))
    by_id = {row.connection_id: row for row in active}

    raw_ids = data.get("connection_ids")
    if raw_ids is None:
        targets = active
    else:
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > MAX_CONNECTIONS:
            raise ValidationError({"connection_ids": [f"불러올 연결을 1~{MAX_CONNECTIONS}개 보내 주세요."]})
        ids = [_uuid(value) for value in raw_ids]
        if any(value not in by_id for value in ids) or len(set(ids)) != len(ids):
            raise ValidationError({"connection_ids": [UNSELECTABLE]})
        targets = [by_id[value] for value in ids]
    if not targets:
        raise ValidationError({"non_field_errors": ["연결된 기관이 없어요. 먼저 기관을 연결해 주세요."]})

    creds, errors = {}, {}
    rows = data.get("card_credentials")
    if rows is not None:
        if not isinstance(rows, list) or len(rows) > MAX_CONNECTIONS:
            raise ValidationError({"card_credentials": ["카드 정보 형식을 확인해 주세요."]})
        target_cards = {row.connection_id for row in targets if row.business_type == institutions.CARD}
        for index, row in enumerate(rows):
            # 키를 화면 이름(camelCase)으로 바로 쓴다: 'card_credentials[0].card_no' 는 자동 변환이 대문자를 잘못 넣어서
            key = lambda field: f"cardCredentials[{index}].{camel_key(field)}"
            if not isinstance(row, dict):
                errors[f"cardCredentials[{index}]"] = ["카드 정보 형식을 확인해 주세요."]
                continue
            for field in row:
                if field not in {"connection_id", "card_no", "card_password"}:
                    errors[key(field)] = ["이 칸은 보낼 수 없어요."]
            cid = _uuid(row.get("connection_id"))
            if cid not in target_cards or cid in creds:
                errors[key("connection_id")] = [UNSELECTABLE]
            if not (isinstance(row.get("card_no"), str) and CARD_NO.fullmatch(row["card_no"])):
                errors[key("card_no")] = ["카드번호는 숫자만 14~19자리로 입력해 주세요."]
            if not (isinstance(row.get("card_password"), str) and CARD_PASSWORD.fullmatch(row["card_password"])):
                errors[key("card_password")] = ["카드 비밀번호는 숫자 2자리 또는 4자리로 입력해 주세요."]
            creds[cid] = {"card_no": row.get("card_no"), "card_password": row.get("card_password")}
    if errors:
        raise ValidationError(errors)
    return targets, creds


# ---------- 실행 (수동 · 자동 공용) ----------

def needs_card_fields(connection):
    info = institutions.find(connection.business_type, connection.organization_code)
    return bool(info and info["card_fields"])


def last_success_day(connection, product_code):
    """마지막으로 성공한 조회가 덮은 마지막 날 (없으면 None → 첫 연결 기간)"""
    row = (
        SyncRun.objects.filter(connection=connection, product_code=product_code, status="success")
        .exclude(covered_to=None).order_by("-completed_at").values_list("covered_to", flat=True).first()
    )
    return row


def _error(reason, name, connection=None, missing=()):
    """실패 이유 → 기관별 결과의 error. 로그인 오류 · 잠김은 연결을 relogin 으로 바꾸고 code=relogin_required"""
    _http, code, message, fields = failure(reason, name, missing)
    if reason in ("login", "locked", "relogin"):
        code = "relogin_required"
        if reason == "relogin":
            message = f"{name} 다시 연결이 필요해요. 연결 관리에서 다시 연결해 주세요."
        if connection is not None:
            Connection.objects.filter(pk=connection.pk, disconnected_at__isnull=True).update(status=Connection.Status.RELOGIN)
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return error


def run_part(kind, connection, work):
    """범위 하나 실행 → (part, 실패면 (이유, 빠진 카드 칸) · 아니면 None)"""
    try:
        work()
    except PermissionError:  # 저장 직전 동의 철회 (sync_loans)
        return {"kind": kind, "status": "skipped", "reason": "no_consent"}, None
    except codef.CodefError as exc:
        reason = classify(exc.code, exc.message, exc.extra)
        missing = missing_fields(exc.extra, exc.message) if reason == "card_fields" else ()
        logger.warning("다시 불러오기 실패 connection=%s kind=%s reason=%s codef_code=%s",
                       connection.pk, kind, reason, exc.code)
        return {"kind": kind, "status": "failed"}, (reason, missing)
    except Exception as exc:
        connection.refresh_from_db(fields=["disconnected_at"])
        if connection.disconnected_at is not None:  # 조회 중 해제 → 저장하지 않고 버림 (#51)
            return {"kind": kind, "status": "skipped", "reason": "disconnected"}, None
        logger.warning("다시 불러오기 실패 connection=%s kind=%s error=%s", connection.pk, kind, type(exc).__name__)
        return {"kind": kind, "status": "failed"}, ("unavailable", ())
    return {"kind": kind, "status": "succeeded"}, None


def sync_connection(user, connection, cid, scopes, cred, today):
    """기관 하나 → 기관별 결과 dict (JSON 그대로 저장되는 값만)"""
    name = institutions.name_of(connection.business_type, connection.organization_code)
    item = {
        "connection_id": str(connection.connection_id), "business_type": connection.business_type,
        "organization_code": connection.organization_code, "organization_name": name,
        "status": "skipped", "parts": [], "new_transaction_count": None, "error": None,
    }

    def failed(reason, missing=()):
        if isinstance(reason, tuple):  # run_part 가 준 (이유, 빠진 카드 칸)
            reason, missing = reason
        item["status"], item["error"] = "failed", _error(reason, name, connection, missing)
        return item

    if connection.status == Connection.Status.RELOGIN or cid is None:
        return failed("relogin")

    org = connection.organization_code
    reason = None
    if connection.business_type == institutions.BANK:
        start = refresh_start(today, last_success_day(connection, "bank_sync"))
        if institutions.BANK not in scopes:
            item["parts"].append({"kind": "accounts", "status": "skipped", "reason": "no_consent"})
        else:
            counts = {}
            part, reason = run_part("accounts", connection, lambda: counts.update(sync_bank(user, cid, org, start, today)))
            item["parts"].append(part)
            item["new_transaction_count"] = counts.get("new_transaction_count")
        loans = loan_syncer()
        if "loan" not in scopes:
            item["parts"].append({"kind": "loans", "status": "skipped", "reason": "no_consent"})
        elif loans is None:
            item["parts"].append({"kind": "loans", "status": "skipped", "reason": "not_supported"})
        elif reason is not None:
            item["parts"].append({"kind": "loans", "status": "skipped", "reason": "not_run"})
        else:
            part, reason = run_part("loans", connection, lambda: loans(user, cid, org, start, today))
            item["parts"].append(part)
    elif connection.business_type == institutions.CARD:
        if institutions.CARD not in scopes:
            item["parts"].append({"kind": "cards", "status": "skipped", "reason": "no_consent"})
        elif needs_card_fields(connection) and not cred:
            item["status"] = "failed"
            item["error"] = {"code": "invalid", "message": CARD_FIELDS_NEEDED,
                             "fields": {"card_no": [CARD_FIELDS_NEEDED], "card_password": [CARD_FIELDS_NEEDED]}}
            return item
        else:
            cards = card_syncer()
            if cards is None:
                item["parts"].append({"kind": "cards", "status": "skipped", "reason": "not_supported"})
            else:
                start = refresh_start(today, last_success_day(connection, "card_sync"))
                cred = cred or {}
                part, reason = run_part("cards", connection, lambda: cards(
                    user, cid, org, start, today, bill_months(today),
                    card_no=cred.get("card_no"), card_password=cred.get("card_password"),
                ))
                item["parts"].append(part)
    else:
        item["parts"].append({"kind": connection.business_type, "status": "skipped", "reason": "not_supported"})

    if reason is not None:
        return failed(reason)
    if any(part["status"] == "succeeded" for part in item["parts"]):
        item["status"] = "succeeded"
    return item


def sync_connections(user, connections, creds=None, today=None):
    """연결 여러 개를 차례로 불러오고 작업 결과(result)를 만든다. 성공한 기관만 저장된다 (기관마다 따로 저장)"""
    creds, today = creds or {}, today or timezone.localdate()
    try:
        cid = connected_id.load(user)
    except connected_id.ConnectedIdUnreadable:
        logger.error("저장된 connectedId 를 풀 수 없어요 user=%s", user.pk)
        cid = None
    scopes = set(Consent.objects.filter(
        user=user, purpose=PURPOSE, revoked_at__isnull=True,
    ).values_list("product_scope", flat=True))
    items = [sync_connection(user, row, cid, scopes, creds.get(row.connection_id), today) for row in connections]
    summary = {"total": len(items)}
    for key in ("succeeded", "failed", "skipped"):
        summary[key] = sum(1 for item in items if item["status"] == key)
    return {"summary": summary, "items": items}


def target_hash(connections):
    return tasks.input_hash({"connections": sorted(str(row.connection_id) for row in connections)})


# ---------- view ----------

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def sync(request):
    """202 {"task_id", "status"} — 진행 중이면 그 작업, 1분 안에 끝난 같은 대상이면 마지막 작업을 그대로 준다"""
    targets, creds = parse_body(request)
    # 카드 값을 새로 보냈으면(앞에서 '카드 비밀번호가 필요해요'를 받고 다시 누름) 1분 안이어도 다시 부른다
    task, created = tasks.start_or_reuse(
        request.user, TASK_TYPE, target_hash(targets), reuse_within=None if creds else REUSE_WITHIN,
    )
    if created:
        user = request.user
        tasks.run(task, lambda: sync_connections(user, targets, creds))
    return Response({"task_id": task.task_id, "status": task.status}, status=status.HTTP_202_ACCEPTED)
