"""
POST /api/finance/connections/ — 금융기관 인증 · 새 연결 (docs/api/finance-connect.md '금융기관 인증 · 새 연결')

10/27 나은 결정
1. 아이디 · 비밀번호 로그인만 (공동인증서 · 간편인증 안 받음)
2. 추가 인증은 문자(SMS)만. CODEF 가 CF-03002 를 주면 sms_required + 인증 토큰을 돌려주고,
   화면이 같은 기관 정보 + 인증번호 + 토큰으로 한 번 더 보낸다 (2-way). 시간이 지나면 처음부터 다시 연결
3. 카드번호 · 카드 비밀번호는 저장하지 않고 매번 받는다. CODEF 에 보내고 바로 버린다 (DB · 로그 저장 금지)
4. 여러 기관을 한 번에 보내면 성공한 것만 저장. 하나라도 연결(또는 문자 인증 대기)되면 200 + 기관별 결과,
   전부 실패하면 실패 안내 기준으로 에러
5. CODEF 원래 코드는 로그에만 남기고 응답에는 넣지 않는다

순서 (기관마다 차례로)
  입력 검사 · 동의 확인 (CODEF 부르기 전) → [회원별 연결 잠금] CODEF 계정 등록 · 추가 · 갱신 → connectedId 암호화 저장
  · 연결 행 저장 → [잠금 풀고] 은행이면 계좌 · 거래 불러오기(sync_bank, PR #44 · #45 흐름)
"""
import logging
import re
from contextlib import contextmanager
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection as db_connection
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from config.api import _camel_key as camel_key
from users.models import Consent

from . import codef_accounts, connected_id, institutions
from .inputs import NOT_ALLOWED, body_dict, reject_unknown_fields
from .models import BankAccount, Connection
from .sync import sync_bank
from .sync_ranges import first_load_start

logger = logging.getLogger(__name__)

MAX_INSTITUTIONS = 10
FIELDS = {"business_type", "organization_code", "login_id", "password", "card_no", "card_password", "sms_auth"}
SMS_FIELDS = {"auth_token", "code"}
SMS_SALT = "finance.connections.sms"
SMS_SECONDS = 180          # 화면 문자 인증 팝업의 '3분 안에 입력'과 같게
LOCK_NAMESPACE = 0x4D43    # 회원별 CODEF 계정 등록 잠금(pg advisory lock)의 앞 번호 ('MC')

CARD_NO = re.compile(r"\d{14,19}")
CARD_PASSWORD = re.compile(r"\d{2}|\d{4}")
SMS_CODE = re.compile(r"\d{6}")


# ---------- 입력 검사 ----------

def _text(value, limit):
    return isinstance(value, str) and value.strip() != "" and len(value) <= limit


def _check_entry(entry, index, errors):
    """기관 한 줄 검사. 틀린 칸은 errors["institutions[0].login_id"] 처럼 모은다"""
    key = lambda field: f"institutions[{index}].{field}"
    if not isinstance(entry, dict):
        errors[f"institutions[{index}]"] = ["기관 정보 형식을 확인해 주세요."]
        return
    for field in entry:
        if field not in FIELDS:
            errors[key(field)] = [NOT_ALLOWED]

    business_type, code = entry.get("business_type"), entry.get("organization_code")
    # [] · {} 같은 값은 표 조회 · 중복 검사에서 500 이 나서 글자인지 먼저 본다 (PR #53 리뷰)
    if not isinstance(business_type, str) or business_type not in institutions.TABLE:
        errors[key("business_type")] = ["업권은 bank 또는 card 로 보내 주세요."]
        info = None
    else:
        info = institutions.find(business_type, code) if isinstance(code, str) else None
        if info is None:
            errors[key("organization_code")] = ["연결할 수 없는 기관이에요."]
        elif not info["id_login"]:
            errors[key("organization_code")] = ["이 기관은 공동인증서로만 연결돼서 아직 연결할 수 없어요."]

    if not _text(entry.get("login_id"), 100):
        errors[key("login_id")] = ["아이디를 입력해 주세요."]
    if not _text(entry.get("password"), 100):
        errors[key("password")] = ["비밀번호를 입력해 주세요."]

    card_values = {"card_no": entry.get("card_no"), "card_password": entry.get("card_password")}
    if business_type == institutions.BANK:
        for field, value in card_values.items():
            if field in entry:
                errors[key(field)] = [NOT_ALLOWED]
    elif business_type == institutions.CARD:
        needed = info["card_fields"] if info else ()
        if card_values["card_no"] is not None and not (isinstance(card_values["card_no"], str) and CARD_NO.fullmatch(card_values["card_no"])):
            errors[key("card_no")] = ["카드번호는 숫자만 14~19자리로 입력해 주세요."]
        elif card_values["card_no"] is None and "card_no" in needed:
            errors[key("card_no")] = ["카드번호를 입력해 주세요."]
        if card_values["card_password"] is not None and not (isinstance(card_values["card_password"], str) and CARD_PASSWORD.fullmatch(card_values["card_password"])):
            errors[key("card_password")] = ["카드 비밀번호는 숫자 2자리 또는 4자리로 입력해 주세요."]
        elif card_values["card_password"] is None and "card_password" in needed:
            errors[key("card_password")] = ["카드 비밀번호를 입력해 주세요."]

    sms = entry.get("sms_auth")
    if sms is not None:
        if not isinstance(sms, dict):
            errors[key("sms_auth")] = ["문자 인증 정보 형식을 확인해 주세요."]
            return
        # 점(.) 뒤 이름은 camelCase 로 바꿔지지 않아서 여기서 바로 화면 이름(smsAuth.code)으로 쓴다
        for field in sms:
            if field not in SMS_FIELDS:
                errors[key(f"smsAuth.{camel_key(field)}")] = [NOT_ALLOWED]
        if not _text(sms.get("auth_token"), 4000):
            errors[key("smsAuth.authToken")] = ["문자 인증을 처음부터 다시 해 주세요."]
        if not (isinstance(sms.get("code"), str) and SMS_CODE.fullmatch(sms["code"])):
            errors[key("smsAuth.code")] = ["인증번호 6자리를 입력해 주세요."]


def parse_body(request):
    data = body_dict(request)
    reject_unknown_fields(data, {"institutions"})
    rows = data.get("institutions")
    if not isinstance(rows, list) or not rows:
        raise ValidationError({"institutions": ["연결할 기관을 하나 이상 보내 주세요."]})
    if len(rows) > MAX_INSTITUTIONS:
        raise ValidationError({"institutions": [f"한 번에 {MAX_INSTITUTIONS}곳까지 연결할 수 있어요."]})
    errors, seen = {}, set()
    for index, entry in enumerate(rows):
        _check_entry(entry, index, errors)
        if isinstance(entry, dict):
            pair = (entry.get("business_type"), entry.get("organization_code"))
            if not all(isinstance(value, str) for value in pair):
                continue  # 형식 오류는 위에서 이미 칸 오류로 넣었다
            if pair in seen:
                errors[f"institutions[{index}].organization_code"] = ["같은 기관이 두 번 들어 있어요."]
            seen.add(pair)
    if len(rows) > 1 and any(isinstance(e, dict) and e.get("sms_auth") is not None for e in rows):
        errors["non_field_errors"] = ["문자 인증은 기관 하나씩 보내 주세요."]
    if errors:
        raise ValidationError(errors)
    for entry in rows:
        entry["login_id"] = entry["login_id"].strip()
    return rows


def check_consents(user, rows):
    """은행은 은행 조회 동의, 카드사는 카드 조회 동의가 있어야 CODEF 를 부른다 (10/22 O · V)"""
    from .api_connect import PURPOSE, ConsentRequired  # api_connect 가 이 파일을 불러서 여기선 늦게 부른다

    for scope, message in ((institutions.BANK, "계좌 조회 동의가 필요해요."), (institutions.CARD, "카드 조회 동의가 필요해요.")):
        if any(row["business_type"] == scope for row in rows) and not Consent.objects.filter(
            user=user, purpose=PURPOSE, product_scope=scope, revoked_at__isnull=True,
        ).exists():
            raise ConsentRequired(message)


# ---------- 실패 안내 (10/27 결정 5) ----------

def failure(reason, name, missing=()):
    """실패 이유 → (HTTP 상태, code, 화면 문구, 칸 오류). missing = CODEF 가 빠졌다고 한 카드 칸"""
    if reason == "login":
        return 400, "invalid", "아이디 또는 비밀번호를 확인해 주세요.", None
    if reason == "locked":
        return 400, "invalid", f"{name} 홈페이지에서 직접 로그인해 잠금을 풀어 주세요.", None
    if reason == "card_fields":
        message = "카드번호와 카드 비밀번호를 확인해 주세요."
        # CODEF 가 빠진 칸을 알려 주면 그 칸만, 모르면 두 칸 다 표시
        return 400, "invalid", message, {field: [message] for field in (missing or ("card_no", "card_password"))}
    if reason == "sms_wrong":
        return 400, "invalid", "인증번호가 맞지 않아요.", None
    if reason == "sms_expired":
        return 400, "invalid", "인증 시간이 지났어요. 처음부터 다시 연결해 주세요.", None
    if reason == "sms_restart":
        return 400, "invalid", "문자 인증 정보가 맞지 않아요. 처음부터 다시 연결해 주세요.", None
    if reason == "unsupported_auth":
        return 400, "invalid", f"{name}에서 요구한 추가 인증은 아직 지원하지 않아요.", None
    # 기관 점검 · 응답 없음 · 알 수 없는 CODEF 오류 (API_RULES 10-22 O: 502 provider_unavailable)
    return 502, "provider_unavailable", f"{name} 연결이 잠시 어려워요. 잠시 후 다시 시도해 주세요.", None


# ---------- 문자 인증 토큰 ----------

def make_sms_token(user, business_type, code, mode, two_way):
    """CODEF 2-way 정보를 서명해서 화면에 맡긴다 (서버에 따로 저장하지 않음). 회원 · 기관이 다르면 쓸 수 없다"""
    return signing.dumps(
        {"u": str(user.pk), "b": business_type, "o": code, "m": mode, "t": two_way}, salt=SMS_SALT, compress=True,
    )


def read_sms_token(user, business_type, code, token):
    """토큰 → (mode, two_way) 또는 실패 이유(sms_expired · sms_restart)"""
    try:
        data = signing.loads(token, salt=SMS_SALT, max_age=SMS_SECONDS)
    except signing.SignatureExpired:
        return "sms_expired"
    except signing.BadSignature:
        return "sms_restart"
    if (data.get("u"), data.get("b"), data.get("o")) != (str(user.pk), business_type, code):
        return "sms_restart"
    if data.get("m") not in codef_accounts.PATHS or not isinstance(data.get("t"), dict):
        return "sms_restart"
    return data["m"], data["t"]


# ---------- CODEF 계정 등록 + 저장 ----------

@contextmanager
def codef_account_lock(user):
    """같은 회원의 CODEF 계정 등록을 한 번에 하나씩 (connectedId 가 두 개 생기지 않게).
    외부 호출 동안 회원 행을 잡지 않도록 행 잠금 대신 회원별 advisory lock 을 쓴다"""
    key = int.from_bytes(user.pk.bytes[:4], "big", signed=True)
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", [LOCK_NAMESPACE, key])
    try:
        yield
    finally:
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, key])


def pick_mode(user, business_type, code, current_id):
    """create(첫 연결) · add(기관 추가) · update(전에 붙인 기관 다시 로그인)
    CODEF 에서 기관 빼기는 아직 붙이지 않아서(10/27), 한 번 연결한 기관은 해제했어도 connectedId 에 남아 있다 → update"""
    if not current_id:
        return "create"
    if Connection.objects.filter(user=user, business_type=business_type, organization_code=code).exists():
        return "update"
    return "add"


def save_connection(user, business_type, code, new_connected_id):
    """connectedId(처음이면) 와 연결 행 저장. 잠금 순서는 회원 → 연결 (sync.py 와 같음)"""
    with transaction.atomic():
        locked = get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
        if not locked.codef_connected_id_ciphertext:
            locked.codef_connected_id_ciphertext = connected_id.seal(new_connected_id)
            locked.codef_connected_at = timezone.now()
            locked.save(update_fields=["codef_connected_id_ciphertext", "codef_connected_at"])
        reconnected = Connection.objects.filter(
            user=user, business_type=business_type, organization_code=code,
        ).exists()
        now = timezone.now()
        row = Connection.objects.select_for_update().filter(
            user=user, business_type=business_type, organization_code=code, disconnected_at__isnull=True,
        ).first()
        if row is None:
            row = Connection.objects.create(
                user=user, business_type=business_type, organization_code=code, auth_method="id_password",
                status=Connection.Status.CONNECTED, last_authenticated_at=now,
            )
        else:
            row.status, row.last_authenticated_at = Connection.Status.CONNECTED, now
            row.save(update_fields=["status", "last_authenticated_at"])
    return row, reconnected


def connect_one(user, entry):
    """기관 하나 연결 → (결과 dict, 실패면 HTTP 상태 · 아니면 None)"""
    business_type, code = entry["business_type"], entry["organization_code"]
    name = institutions.name_of(business_type, code)
    base = {"business_type": business_type, "organization_code": code, "organization_name": name}

    def failed(reason, codef_code=None, missing=()):
        logger.warning(
            "기관 연결 실패 user=%s business_type=%s organization=%s reason=%s codef_code=%s",
            user.pk, business_type, code, reason, codef_code,
        )
        http, error_code, message, fields = failure(reason, name, missing)
        error = {"code": error_code, "message": message}
        if fields:
            error["fields"] = fields
        return {**base, "status": "failed", "error": error}, http

    sms = None
    if entry.get("sms_auth") is not None:
        token = read_sms_token(user, business_type, code, entry["sms_auth"]["auth_token"])
        if isinstance(token, str):
            return failed(token)
        mode, two_way = token
        sms = {"code": entry["sms_auth"]["code"], "two_way": two_way}

    item = codef_accounts.account_item(
        business_type, code, entry["login_id"], entry["password"], entry.get("card_no"), entry.get("card_password"),
    )
    with codef_account_lock(user):
        fresh = get_user_model().objects.get(pk=user.pk)
        try:
            current_id = connected_id.load(fresh)
        except connected_id.ConnectedIdUnreadable:
            logger.error("저장된 connectedId 를 풀 수 없어요 user=%s", user.pk)
            return failed("unavailable")
        if sms is None:
            mode = pick_mode(user, business_type, code, current_id)
        elif (mode == "create") == bool(current_id):
            # 문자 인증 사이에 다른 기관 연결로 connectedId 가 생겼거나 사라짐 → 처음부터 다시
            return failed("sms_restart")

        outcome = codef_accounts.register(mode, item, current_id, sms)
        retry = {("add", "already_registered"): "update", ("update", "not_registered"): "add"}.get((mode, outcome.reason))
        if sms is None and outcome.kind == "failed" and retry:
            mode = retry
            outcome = codef_accounts.register(mode, item, current_id)

        if outcome.kind == "sms":
            result = {
                **base, "status": "sms_required",
                "sms_auth": {
                    "auth_token": make_sms_token(user, business_type, code, mode, outcome.two_way),
                    "expires_at": timezone.localtime() + timedelta(seconds=SMS_SECONDS),
                    "phone_hint": None,
                },
            }
            if sms is not None:  # 인증번호를 보냈는데 CODEF 가 다시 문자 인증을 요구 = 인증번호가 틀림
                result["error"] = {"code": "invalid", "message": failure("sms_wrong", name)[2]}
            return result, None
        if outcome.kind == "failed":
            return failed(outcome.reason, outcome.codef_code, outcome.missing)
        row, reconnected = save_connection(user, business_type, code, outcome.connected_id)

    result = {**base, "status": "connected", "connection_id": row.connection_id, "reconnected": reconnected}
    if business_type == institutions.BANK:
        result.update(load_bank(user, current_id or outcome.connected_id, code, row))
    else:
        # 카드 거래 · 청구서 불러오기는 아직 없다 (카드 조회 흐름이 생기면 여기서 이어 부른다)
        result.update({"sync_status": "not_supported", "account_count": None})
    return result, None


def load_bank(user, cid, code, row):
    """연결 직후 계좌 · 거래 불러오기 (나은 sync_bank). 실패해도 연결은 그대로 두고 sync_status 로 알려 준다"""
    end = timezone.localdate()
    try:
        # 첫 연결: 4달 전 1일 ~ 오늘 (10/27). 방금 만든 연결에만 저장하고, 동의는 저장 직전에도 확인 (PR #53 리뷰)
        sync_bank(user, cid, code, first_load_start(end), end, connection_id=row.pk, require_consent=True)
        sync_status = "done"
    except Exception as exc:
        logger.warning(
            "연결 직후 불러오기 실패 user=%s organization=%s codef_code=%s",
            user.pk, code, getattr(exc, "code", None),
        )
        sync_status = "failed"
    return {"sync_status": sync_status, "account_count": BankAccount.objects.filter(connection=row).count()}


# ---------- view 본체 ----------

def create(request):
    rows = parse_body(request)
    check_consents(request.user, rows)
    results, statuses = [], []
    for entry in rows:
        result, http = connect_one(request.user, entry)
        results.append(result)
        statuses.append(http)
    if any(http is None for http in statuses):
        return Response({"results": results}, status=status.HTTP_200_OK)

    # 전부 실패 → 실패 안내 기준으로 에러 (사람이 고칠 수 있는 400 이 하나라도 있으면 400)
    http = 400 if 400 in statuses else 502
    if len(results) == 1:
        error = dict(results[0]["error"])
        if "fields" in error:
            error["fields"] = {f"institutions[0].{field}": messages for field, messages in error["fields"].items()}
    else:
        fields = {}
        for index, result in enumerate(results):
            fields[f"institutions[{index}]"] = [result["error"]["message"]]
            for field, messages in result["error"].get("fields", {}).items():
                fields[f"institutions[{index}].{field}"] = messages
        error = {
            "code": "invalid" if http == 400 else "provider_unavailable",
            "message": "연결하지 못했어요. 기관별 안내를 확인해 주세요.",
            "fields": fields,
        }
    return Response({"error": error}, status=http)
