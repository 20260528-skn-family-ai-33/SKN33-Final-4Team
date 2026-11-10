"""금융 조회 동의 철회와 회원 탈퇴. 실제 작업은 명시적 확인 요청에서만 실행한다."""
from django.contrib.auth import get_user_model, logout
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config import tasks
from finance.api_new_connection import codef_account_lock
from finance.inputs import body_dict, reject_unknown_fields
from finance.models import Connection
from . import deletion
from .models import Consent, PrivacyDeletion
from .privacy import check_idle, consent_states, disconnect_revoked_scope, purge_scope


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def revoke_consent(request, scope):
    if scope not in {"bank", "card", "loan"}:
        raise Http404
    data = body_dict(request)
    reject_unknown_fields(data, {"confirm"})
    if data.get("confirm") is not True:
        raise ValidationError({"confirm": ["해당 금융 데이터 삭제를 확인해 주세요."]})
    with codef_account_lock(request.user):
        # 유효 기간이 지난 작업 · 조회 기록부터 만료 처리 (따로 커밋 → 아래에서 409 가 나도 남는다, R20)
        tasks.expire_stale(request.user)
        return _revoke(request, scope)


def _revoke(request, scope):
    # 접수 · 로컬 정리를 짧은 트랜잭션으로 커밋하고, 외부 삭제 · 완료 기록은 deletion.run 이 트랜잭션 밖에서 이어 간다 (R21)
    with transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        list(Connection.objects.select_for_update().filter(user=user).order_by("pk"))
        row = deletion.open_deletions(user.pk).filter(scope=scope).first()
        rows = Consent.objects.filter(user=user, purpose="finance_inquiry", product_scope=scope, revoked_at__isnull=True)
        if row is None and rows.exists():
            check_idle(user)
            row = PrivacyDeletion.objects.create(user_id=user.pk, scope=scope, provider_kinds=deletion.provider_kinds(user, scope))
            rows.update(revoked_at=timezone.now())  # 접수 순간부터 이 범위의 조회 · 저장 · 재연결을 막는다
            # 외부 삭제가 남아도 이 범위 자료가 조회 · 집계 · 분석에 쓰이지 않게 바로 지운다 (재시도에는 connectedId · 업권 기록만 필요)
            purge_scope(user, scope)
            disconnect_revoked_scope(user, scope)
    if row is not None:
        row = deletion.run(row)
    done = deletion.state(row) is None
    return Response(consent_states(request.user), status=200 if done else 202)


def withdraw(request):
    data = body_dict(request)
    reject_unknown_fields(data, {"password", "confirm"})
    if data.get("confirm") is not True:
        raise ValidationError({"confirm": ["회원 탈퇴와 전체 데이터 삭제를 확인해 주세요."]})
    password = data.get("password")
    if not isinstance(password, str) or not password:
        raise ValidationError({"password": ["머니쿠션 로그인 비밀번호를 입력해 주세요."]})
    with codef_account_lock(request.user):
        tasks.expire_stale(request.user)  # 철회와 같음 (R20)
        row = deletion.run(_accept_withdraw(request, password))
    logout(request)
    shown = deletion.state(row)
    if shown is None:
        return Response({"ok": True})
    # 접수됐지만 외부 인증 또는 자료 삭제가 남음 → 완료라고 하지 않는다. 로그인은 막혔고 운영 재처리 명령이 이어 간다
    return Response({"ok": False, "status": shown}, status=202)


def _accept_withdraw(request, password):
    with transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        if not user.check_password(password):
            raise ValidationError({"password": ["비밀번호를 확인해 주세요."]})
        row = deletion.open_deletions(user.pk).filter(scope="all").first()
        if row is None:
            check_idle(user)
            row = PrivacyDeletion.objects.create(user_id=user.pk, scope="all", provider_kinds=None)
            # 접수 뒤에는 로그인 · 일반 이용 · 자동 동기화(status=active 만)를 막는다
            user.is_active, user.status = False, get_user_model().Status.WITHDRAWN
            user.save(update_fields=["is_active", "status", "updated_at"])
    return row
