"""
같은 저장 요청이 두 번 와도 한 번만 반영하기 (10-21 C-10) — '주문 번호'

화면: 폼을 열 때 요청 번호를 하나 만들고(newRequestKey), 저장할 때 Idempotency-Key 헤더로 보낸다.
서버: 저장 · 승인 API 에 @idempotent 를 붙인다.

    from config.idempotency import idempotent

    @api_view(["POST"])
    @idempotent()                 # 승인처럼 꼭 필요하면 @idempotent(required=True)
    def fixed_create(request): ...

동작 (같은 회원 기준)
- 처음 보는 번호        → 그대로 처리하고, 성공(2xx) 응답을 기록한다.
- 같은 번호 · 같은 내용  → 다시 처리하지 않고 기록해 둔 처음 응답을 돌려준다.
- 같은 번호 · 다른 내용  → 409 idempotency_conflict (번호를 재사용한 버그)
- 처리 결과가 실패(4xx·5xx)면 기록하지 않는다 → 입력을 고쳐 같은 번호로 다시 보내면 처음 요청처럼 처리
- 동시에 같은 번호가 두 번 오면: 두 번째 요청은 DB UNIQUE 에서 첫 번째가 끝나길 기다렸다가 처음 응답을 받는다
- 헤더가 없으면: required=False 면 그냥 처리, True 면 400
- 로그인 안 한 요청은 회원을 몰라서 번호 확인 없이 그냥 처리한다 (로그인 API 가 붙으면 자동으로 적용)
"""
import functools
import hashlib
import re

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.response import Response

HEADER = "Idempotency-Key"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _error(code, message, http_status):
    return Response({"error": {"code": code, "message": message}}, status=http_status)


def _request_hash(request):
    raw = request.method.encode() + b" " + request.path.encode() + b"\n" + (request.body or b"")
    return hashlib.sha256(raw).hexdigest()


def _replay(record):
    return Response(record.response_body, status=record.response_status)


def idempotent(required=False, *, commit_error_codes=()):
    """commit_error_codes는 승인 시 발견한 expired 상태 보존용. 실패 응답은 번호 기록에 남기지 않는다."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            from users.models import IdempotencyRecord  # 앱 로딩 순서 때문에 여기서 import

            key = request.headers.get(HEADER)
            user = getattr(request, "user", None)
            if not key:
                if required:
                    return _error("invalid", "요청 번호(Idempotency-Key)가 필요해요.", status.HTTP_400_BAD_REQUEST)
                return view(request, *args, **kwargs)
            if not KEY_PATTERN.match(key):
                return _error("invalid", "요청 번호 형식이 올바르지 않아요.", status.HTTP_400_BAD_REQUEST)
            if not (user and user.is_authenticated):
                return view(request, *args, **kwargs)

            request_hash = _request_hash(request)
            found = IdempotencyRecord.objects.filter(user=user, key=key).first()
            if found:
                if found.request_hash != request_hash:
                    return _error("idempotency_conflict", "같은 요청 번호로 다른 내용을 보낼 수 없어요.", status.HTTP_409_CONFLICT)
                return _replay(found)

            try:
                with transaction.atomic():
                    response = view(request, *args, **kwargs)
                    if 200 <= response.status_code < 300:
                        # UNIQUE(회원, 번호) — 같은 번호가 동시에 오면 여기서 막힌다 → 아래 except 로
                        IdempotencyRecord.objects.create(
                            user=user, key=key, method=request.method, path=request.path[:200],
                            request_hash=request_hash, response_status=response.status_code,
                            response_body=response.data,
                        )
                    elif not (response.status_code == 409
                              and isinstance(response.data, dict)
                              and response.data.get("error", {}).get("code") in commit_error_codes):
                        transaction.set_rollback(True)   # 실패한 요청은 기록하지 않고 저장한 것도 되돌림
                    return response
            except IntegrityError:
                found = IdempotencyRecord.objects.filter(user=user, key=key).first()
                if found is None:
                    raise
                if found.request_hash != request_hash:
                    return _error("idempotency_conflict", "같은 요청 번호로 다른 내용을 보낼 수 없어요.", status.HTTP_409_CONFLICT)
                return _replay(found)

        return wrapper

    return decorator
