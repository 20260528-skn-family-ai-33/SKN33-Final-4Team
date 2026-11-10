"""예산 설계 전용 외부 전송 동의. 금융 동의·예산·원자료는 변경하지 않는다."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from finance.inputs import body_dict, reject_unknown_fields
from users import consent_documents as documents
from users.models import Consent

PURPOSE = 'budget_ai'
HEALTH_PURPOSE = 'budget_ai_health'
PURPOSES = (PURPOSE, HEALTH_PURPOSE)


class ConsentUnavailable(APIException):
    status_code = 409
    default_code = 'budget_ai_unavailable'
    default_detail = '외부 AI 예산 추천이 비활성 상태라 새 동의를 받을 수 없어요.'


def current_document(purpose=PURPOSE):
    return documents.CURRENT[(purpose, None)]


def has_current_consent(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    for purpose in PURPOSES:
        doc = current_document(purpose)
        if not Consent.objects.filter(user=user, purpose=purpose, product_scope__isnull=True,
                revoked_at__isnull=True, document_version=doc['version'],
                document_hash=documents.canonical_hash(doc)).exists():
            return False
    return True


def consent_state(user):
    from .design_service import external_enabled

    doc = current_document()
    health = current_document(HEALTH_PURPOSE)
    rows = Consent.objects.filter(user=user, purpose__in=PURPOSES, product_scope__isnull=True)
    latest = rows.order_by('-granted_at', '-pk').first()
    granted = has_current_consent(user)
    status = ('granted' if granted else 'not_granted' if latest is None else
              'revoked' if not rows.filter(revoked_at__isnull=True).exists() else 'needs_renewal')
    return {'purpose': PURPOSE, 'available': external_enabled(), 'status': status, 'document': doc,
            'document_version': doc['version'], 'document_hash': documents.canonical_hash(doc),
            'health_document': health, 'health_document_version': health['version'],
            'health_document_hash': documents.canonical_hash(health),
            'granted_at': latest.granted_at if latest else None,
            'revoked_at': latest.revoked_at if latest else None}


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def ai_consent(request):
    if request.method == 'GET':
        return Response(consent_state(request.user))
    data = body_dict(request)
    with transaction.atomic():
        # 추천 API와 같은 회원 잠금: 이미 시작된 요청과 철회 기록의 순서를 보존한다.
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        rows = Consent.objects.filter(user=user, purpose__in=PURPOSES, product_scope__isnull=True,
                                      revoked_at__isnull=True)
        if request.method == 'DELETE':
            reject_unknown_fields(data, {'confirm'})
            if data.get('confirm') is not True:
                raise ValidationError({'confirm': ['예산 AI 전송 동의 철회를 확인해 주세요.']})
            rows.update(revoked_at=timezone.now())
        else:
            from .design_service import external_enabled
            if not external_enabled():
                raise ConsentUnavailable()
            reject_unknown_fields(data, {'agreed', 'document_version', 'document_hash',
                'health_agreed', 'health_document_version', 'health_document_hash'})
            checked = []
            for purpose, prefix in ((PURPOSE, ''), (HEALTH_PURPOSE, 'health_')):
                if data.get(prefix+'agreed') is not True:
                    raise ValidationError({prefix+'agreed': ['각 동의 내용을 확인한 뒤 직접 선택해 주세요.']})
                version = data.get(prefix+'document_version')
                if not isinstance(version, str):
                    raise ValidationError({prefix+'document_version': ['화면에 표시된 문서 버전이 필요해요.']})
                document_hash = documents.consent_hash(purpose, None, version)
                if data.get(prefix+'document_hash') != document_hash:
                    raise documents.DocumentOutdated()
                checked.append((purpose, version, document_hash))
            if not has_current_consent(user):
                rows.update(revoked_at=timezone.now())
                for purpose, version, document_hash in checked:
                    Consent.objects.create(user=user, purpose=purpose, document_version=version,
                                           document_hash=document_hash)
    return Response(consent_state(user))
