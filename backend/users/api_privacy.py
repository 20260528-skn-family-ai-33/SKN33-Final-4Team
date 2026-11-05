"""금융 조회 동의 철회와 회원 탈퇴. 실제 작업은 명시적 확인 요청에서만 실행한다."""
from django.contrib.auth import get_user_model, logout
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from finance import connected_id, codef_privacy
from finance.api_new_connection import codef_account_lock
from finance.inputs import body_dict, reject_unknown_fields
from finance.models import Connection
from .models import Consent
from .privacy import check_idle, consent_states, disconnect_revoked_scope, purge_scope


class ProviderDeleteFailed(APIException):
    status_code = 502
    default_code = "provider_unavailable"
    default_detail = "금융기관 인증 연결 삭제를 완료하지 못했어요. 잠시 후 다시 시도해 주세요."


def remove_provider_accounts(user, scope=None):
    active = set(Consent.objects.filter(user=user, purpose="finance_inquiry", revoked_at__isnull=True)
                 .exclude(product_scope=scope).values_list("product_scope", flat=True))
    if scope in {"bank", "loan"} and active.intersection({"bank", "loan"}):
        return  # 은행 인증은 다른 유효한 조회 동의에서도 사용한다.
    try:
        cid = connected_id.load(user)
        if cid:
            kinds = None if scope is None else {"CD"} if scope == "card" else {"BK"}
            codef_privacy.delete_registered_accounts(cid, kinds)
    except Exception:
        # 외부 예외의 원문에는 인증·기관 데이터가 있을 수 있어 응답과 로그에 옮기지 않는다.
        raise ProviderDeleteFailed() from None


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def revoke_consent(request, scope):
    if scope not in {"bank", "card", "loan"}:
        raise Http404
    data = body_dict(request)
    reject_unknown_fields(data, {"confirm"})
    if data.get("confirm") is not True:
        raise ValidationError({"confirm": ["해당 금융 데이터 삭제를 확인해 주세요."]})
    with codef_account_lock(request.user), transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        list(Connection.objects.select_for_update().filter(user=user).order_by("pk"))
        rows = Consent.objects.filter(user=user, purpose="finance_inquiry", product_scope=scope, revoked_at__isnull=True)
        if rows.exists():
            check_idle(user)
            remove_provider_accounts(user, scope)
            rows.update(revoked_at=timezone.now())
            purge_scope(user, scope)
            disconnect_revoked_scope(user, scope)
        return Response(consent_states(user))


def withdraw(request):
    data = body_dict(request)
    reject_unknown_fields(data, {"password", "confirm"})
    if data.get("confirm") is not True:
        raise ValidationError({"confirm": ["회원 탈퇴와 전체 데이터 삭제를 확인해 주세요."]})
    password = data.get("password")
    if not isinstance(password, str) or not password:
        raise ValidationError({"password": ["머니쿠션 로그인 비밀번호를 입력해 주세요."]})
    with codef_account_lock(request.user), transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        if not user.check_password(password):
            raise ValidationError({"password": ["비밀번호를 확인해 주세요."]})
        check_idle(user)
        list(Connection.objects.select_for_update().filter(user=user).order_by("pk"))
        remove_provider_accounts(user)
        user_id = str(user.pk)
        # 모든 회원 소유 모델이 CASCADE로 모이므로 그 안의 RESTRICT 참조도 함께 삭제된다.
        user.delete()
        for session in Session.objects.all().iterator():
            if session.get_decoded().get("_auth_user_id") == user_id:
                session.delete()
    logout(request)
    return Response({"ok": True})
