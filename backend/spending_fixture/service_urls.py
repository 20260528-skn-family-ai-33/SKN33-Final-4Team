"""더미 서비스 실행 주소 (MEMO 22절 1단계) — config.urls 그대로 + 금융 연결 · 다시 불러오기만 503
챗봇 메시지는 가로채지 않는다 — chat.api.messages 가 설정(CHAT_ENGINE = "router", service_settings)을 보고 새 엔진으로 답한다 (MEMO 25절 1단계)

웹 API 중 CODEF 를 실제로 부르는 것은 finance/sync/ 하나다. 나머지 막는 주소는 CODEF 연결 흐름(동의 · 계좌 · 카드 연결 ·
연결 추가 · 해제)이라 테스트 환경에서 쓸 수 없다. 연결 목록 조회(GET finance/connections/)는 읽기라 그대로 둔다.
주소 문자열은 config/api_urls.py 의 이름으로 찾아 그대로 쓴다 (팀이 주소를 바꿔도 따라간다, 이름이 없어지면 시작 때 멈춘다).
프로필 응답에서 지정된 알파 온보딩 계정의 준비된 금융 자료만 확인해 다음 단계를 안내한다.
지정된 알파 계정의 저장된 계좌·카드 GET만 예외이며 금융 연결·선택 변경은 차단한다.
"""
from functools import wraps

from django.http import JsonResponse
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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


def _blocked(view, methods, read_view=None):
    @csrf_exempt  # 차단 요청은 로그인 · CSRF 확인 전에 503. 허용한 GET은 별도 인증을 거친다.
    def wrapper(request, *args, **kwargs):
        if request.method == 'GET' and read_view is not None:
            return read_view(request, *args, **kwargs)
        if methods is EVERY or request.method in methods:
            return JsonResponse(UNAVAILABLE, status=503)
        return view(request, *args, **kwargs)
    return wrapper


def _blocked_patterns():
    found = {p.name: p for p in api_urls.urlpatterns if p.name in BLOCKED}
    missing = set(BLOCKED) - set(found)
    if missing:
        raise RuntimeError(f"config/api_urls.py 에 없는 주소 이름: {sorted(missing)} — service_urls.BLOCKED 를 맞춰 주세요")
    return [path(f"api/{p.pattern}", _blocked(p.callback, BLOCKED[name], READ_ONLY.get(name)), name=f"fixture-blocked-{name}")
            for name, p in found.items()]


def alpha_persona(user):
    if user.is_authenticated:
        for persona in ('home', 'onboarding'):
            if user.pk == uid('alpha-persona-v1', persona, 'user') and user.email == f'alpha.{persona}@example.test':
                return persona
    return None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def alpha_accounts(request):
    if alpha_persona(request.user) is None:
        return Response(UNAVAILABLE, status=503)
    from finance.api_connect import ConsentRequired, account_item, has_bank_consent
    from finance.models import BankAccount

    if not has_bank_consent(request.user):
        raise ConsentRequired()
    # 저축 팝업의 받는 계좌에는 관리 대상이 아닌 적금 C도 필요하다.
    accounts = BankAccount.objects.filter(user=request.user, connection__user=request.user,
        connection__business_type='bank', connection__status__in=['connected', 'relogin'],
        connection__disconnected_at__isnull=True).select_related('connection').order_by('account_id')
    return Response({'items': [account_item(account) for account in accounts]})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def alpha_cards(request):
    if alpha_persona(request.user) is None:
        return Response(UNAVAILABLE, status=503)
    from finance.api_cards import candidate_cards, candidate_item, require_card_consent

    require_card_consent(request.user)
    cards = candidate_cards(request.user).filter(connection__disconnected_at__isnull=True)
    return Response({'items': [candidate_item(card) for card in cards]})


READ_ONLY = {'finance-connect-accounts': alpha_accounts, 'card-candidates': alpha_cards}


def alpha_finance_ready(user):
    """지정 알파 계정의 준비된 자료만 사용한다. 금융 연결·선택 변경 API는 열지 않는다."""
    if alpha_persona(user) != 'onboarding':
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
