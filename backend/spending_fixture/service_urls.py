"""더미 서비스 실행 주소 (MEMO 22절 1단계) — config.urls 그대로 + 금융 연결 · 다시 불러오기만 503
챗봇 메시지는 가로채지 않는다 — chat.api.messages 가 설정(CHAT_ENGINE = "router", service_settings)을 보고 새 엔진으로 답한다 (MEMO 25절 1단계)

웹 API 중 CODEF 를 실제로 부르는 것은 finance/sync/ 하나다. 나머지 막는 주소는 CODEF 연결 흐름(동의 · 계좌 · 카드 연결 ·
연결 추가 · 해제)이라 테스트 환경에서 쓸 수 없다. 연결 목록 조회(GET finance/connections/)는 읽기라 그대로 둔다.
주소 문자열은 config/api_urls.py 의 이름으로 찾아 그대로 쓴다 (팀이 주소를 바꿔도 따라간다, 이름이 없어지면 시작 때 멈춘다).
프로필 응답에서 지정된 알파 온보딩 계정의 준비된 금융 자료만 확인해 다음 단계를 안내한다.
기존 프로필 인증·검증·저장과 금융 API 차단은 유지한다.
"""
from functools import wraps

from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from config import api_urls
from config.urls import urlpatterns as service_urlpatterns
from users.api_profile import profile as service_profile

from .seed import uid

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


def alpha_finance_ready(user):
    """지정 알파 계정의 준비된 자료만 사용한다. 실제 금융 연결·선택 API는 열지 않는다."""
    if (not user.is_authenticated or user.pk != uid('alpha-persona-v1', 'onboarding', 'user')
            or user.email != 'alpha.onboarding@example.test'):
        return False
    from finance.models import BankAccount, Card, FinancialTransaction

    asset = lambda name: uid('alpha-persona-v1', 'onboarding', 'asset', name)
    return (BankAccount.objects.filter(user=user, pk__in=[asset('A'), asset('B')], is_selected=True,
                account_type='demand', currency='KRW', connection__user=user, connection__status='connected',
                connection__disconnected_at__isnull=True).count() == 2
            and Card.objects.filter(user=user, pk__in=[asset('D'), asset('E')], is_selected=True,
                connection__user=user, connection__status='connected', connection__disconnected_at__isnull=True).count() == 2
            and FinancialTransaction.objects.filter(user=user).exists())


@wraps(service_profile)
def alpha_profile(request, *args, **kwargs):
    response = service_profile(request, *args, **kwargs)  # 기존 인증·CSRF·검증·저장을 먼저 거친다.
    if response.status_code == 200:
        response.data['onboarding_next_step'] = 'salary' if alpha_finance_ready(request.user) else 'connect'
    return response


# 막는 주소가 먼저 (Django 는 처음 맞는 주소를 쓴다)
_profile_pattern = next(p for p in api_urls.urlpatterns if p.name == 'profile')
urlpatterns = [*_blocked_patterns(), path(f'api/{_profile_pattern.pattern}', alpha_profile,
                name='fixture-alpha-profile'), *service_urlpatterns]
