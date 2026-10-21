"""어느 앱에도 속하지 않는 공통 API"""
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.decorators import api_view
from rest_framework import status
from rest_framework.response import Response


@api_view(["GET"])
@ensure_csrf_cookie
def csrf(request):
    """
    GET /api/csrf/ — csrftoken 쿠키를 받아 가는 곳
    React 는 저장·수정(POST·PATCH·DELETE) 전에 이 쿠키 값을 X-CSRFToken 헤더에 담아 보낸다.
    (React 쪽 utils/api.js 의 apiSend 가 알아서 한다)
    """
    return Response({"ok": True})


@api_view(["GET", "POST", "PUT", "PATCH", "DELETE"])
def not_found(request, *args, **kwargs):
    """
    /api/ 아래에서 맞는 주소가 없을 때 — HTML 404 페이지 대신 공통 JSON 에러로 답한다 (10-21 C-11)
    - 없는 주소, 주소 속 ID 형식이 틀림(<uuid:…> 자리에 abc), 남의·지워진 데이터 모두 404 not_found
    - 끝에 / 를 빼먹은 주소도 여기로 온다 → 안내 문구만 다르게
    api_urls.py 맨 끝에 둔다 (위에서 아무 주소도 안 맞을 때만 온다)
    """
    if not request.path.endswith("/"):
        message = "주소를 확인해 주세요. 주소 끝에 /가 필요해요."
    else:
        message = "찾을 수 없어요."
    return Response({"error": {"code": "not_found", "message": message}}, status=status.HTTP_404_NOT_FOUND)
