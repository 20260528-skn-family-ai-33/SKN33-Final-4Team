"""더미 서비스 실행 주소 (MEMO 22절 1 · 2단계) — config.urls 그대로 + 금융 연결 · 다시 불러오기만 503 + 챗봇 메시지는 새 라우터

웹 API 중 CODEF 를 실제로 부르는 것은 finance/sync/ 하나다. 나머지 막는 주소는 CODEF 연결 흐름(동의 · 계좌 · 카드 연결 ·
연결 추가 · 해제)이라 테스트 환경에서 쓸 수 없다. 연결 목록 조회(GET finance/connections/)는 읽기라 그대로 둔다.
주소 문자열은 config/api_urls.py 의 이름으로 찾아 그대로 쓴다 (팀이 주소를 바꿔도 따라간다, 이름이 없어지면 시작 때 멈춘다).
"""
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from config import api_urls
from config.urls import urlpatterns as service_urlpatterns

from . import service_chat

UNAVAILABLE = {"error": {"code": "unavailable", "message": "테스트 환경에서는 금융 연결 · 다시 불러오기를 쓸 수 없어요."}}
EVERY = None  # 모든 메서드
BLOCKED = {  # api_urls 이름 → 막을 메서드
    "finance-sync": EVERY,
    "finance-consents": EVERY,
    "finance-connect-accounts": EVERY,
    "card-candidates": EVERY,
    "finance-connections": ("POST",),
    "finance-connection": EVERY,
}


def _blocked(view, methods):
    @csrf_exempt  # 로그인 · CSRF 확인 전에 바로 503 (막힌 주소는 아무것도 처리하지 않는다)
    def wrapper(request, *args, **kwargs):
        if methods is EVERY or request.method in methods:
            return JsonResponse(UNAVAILABLE, status=503)
        return view(request, *args, **kwargs)
    return wrapper


def _blocked_patterns():
    found = {p.name: p for p in api_urls.urlpatterns if p.name in BLOCKED}
    missing = set(BLOCKED) - set(found)
    if missing:
        raise RuntimeError(f"config/api_urls.py 에 없는 주소 이름: {sorted(missing)} — service_urls.BLOCKED 를 맞춰 주세요")
    return [path(f"api/{p.pattern}", _blocked(p.callback, BLOCKED[name]), name=f"fixture-blocked-{name}")
            for name, p in found.items()]


def _chat_messages():
    """POST chat/messages/ → 새 라우터 뷰 (주소 문자열은 api_urls 그대로). 대화 조회(GET)는 기존 뷰 — 확장 content 도 그대로 돌려준다"""
    found = next((p for p in api_urls.urlpatterns if p.name == "chat-messages"), None)
    if found is None:
        raise RuntimeError("config/api_urls.py 에 chat-messages 주소가 없어요 — service_urls 를 맞춰 주세요")
    return path(f"api/{found.pattern}", service_chat.messages, name="fixture-chat-messages")


# 가로채는 주소가 먼저 (Django 는 처음 맞는 주소를 쓴다)
urlpatterns = [*_blocked_patterns(), _chat_messages(), *service_urlpatterns]
