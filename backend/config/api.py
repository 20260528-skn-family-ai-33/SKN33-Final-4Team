"""
API 공통 도구 (settings.REST_FRAMEWORK 에서 쓴다)

1. 이름 바꾸기: 파이썬은 snake_case(living_used), 자바스크립트는 camelCase(livingUsed)
   - 보낼 때 CamelJSONRenderer 가, 받을 때 SnakeJSONParser 가 알아서 바꾼다.
   - 그래서 view 에서는 파이썬 이름 그대로 dict 를 돌려주면 된다.
2. 에러 모양 통일: 어떤 에러든 {"error": {"code", "message", "fields"}} 로 보낸다.
"""
import logging
import re

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


# ----- 이름 바꾸기 -----
def _camel_key(key):
    head, *rest = key.split("_")
    return head + "".join(word.title() for word in rest)


def _snake_key(key):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _convert(data, fn):
    if isinstance(data, dict):
        return {fn(k) if isinstance(k, str) else k: _convert(v, fn) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_convert(v, fn) for v in data]
    return data


def camel(data):
    """{"living_used": 1} → {"livingUsed": 1} (안쪽까지 전부)"""
    return _convert(data, _camel_key)


def snake(data):
    """{"livingUsed": 1} → {"living_used": 1} (안쪽까지 전부)"""
    return _convert(data, _snake_key)


class CamelJSONRenderer(JSONRenderer):
    """응답 JSON 의 키를 camelCase 로 바꿔서 보낸다"""
    def render(self, data, accepted_media_type=None, renderer_context=None):
        return super().render(camel(data), accepted_media_type, renderer_context)


class SnakeJSONParser(JSONParser):
    """요청 JSON 의 키를 snake_case 로 바꿔서 view 에 넘긴다"""
    def parse(self, stream, media_type=None, parser_context=None):
        return snake(super().parse(stream, media_type, parser_context))


# ----- 에러 모양 통일 -----
def exception_handler(exc, context):
    """
    모든 API 에러를 한 모양으로:
      {"error": {"code": "invalid", "message": "입력값을 확인해 주세요.", "fields": {"email": ["..."]}}}
    - code: 프로그램이 보는 이름 (not_authenticated, not_found, invalid, ...)
    - message: 화면에 그대로 보여 줄 문장
    - fields: 칸별 에러 (입력값 검사에서만)
    """
    response = drf_exception_handler(exc, context)

    if response is None:
        # DRF 가 모르는 에러 = 서버 버그. 개발 중엔 장고 에러 화면을 그대로 보여 준다
        if settings.DEBUG:
            return None
        logger.exception("API 처리 중 오류", exc_info=exc)
        return Response(
            {"error": {"code": "server_error", "message": "잠시 후 다시 시도해 주세요."}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    data = response.data
    # Django 의 Http404(get_object_or_404) · PermissionDenied 는 default_code 가 없어서 'error' 가 되던 것을 바로잡음 (10-21)
    if isinstance(exc, Http404):
        code = "not_found"
    elif isinstance(exc, DjangoPermissionDenied):
        code = "permission_denied"
    else:
        code = getattr(exc, "default_code", "error")
    error = {"code": code}
    if isinstance(data, dict) and set(data) == {"detail"}:
        error["message"] = str(data["detail"])
        if error["code"] == "not_found":
            # get_object_or_404 · NotFound 도 /api/ 맨 끝 줄(api_common.not_found)과 같은 문구로 (10-21 C-11)
            error["message"] = "찾을 수 없어요."
    else:
        # 입력값 검사 에러: {"email": ["필수 항목입니다."]} 같은 칸별 목록
        error["message"] = "입력값을 확인해 주세요."
        error["fields"] = data if isinstance(data, dict) else {"non_field_errors": data}
    response.data = {"error": error}
    return response
