"""어느 앱에도 속하지 않는 공통 API"""
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.decorators import api_view
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
