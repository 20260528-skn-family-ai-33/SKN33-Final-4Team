"""예산 설계·예산 조정의 외부 AI 전송 동의. 목적별로 따로 받고 한쪽 동의로 다른 쪽을 허용하지 않는다.

- 문서 원문·해시는 users.consent_documents 한 곳에서 관리한다 (설계 v3 · 조정 v1, 각 건강 요약 동의 포함).
- 응답 계약은 설계 동의(budgets/ai-consent/)와 같다. 조정은 budgets/adjustment-ai-consent/.
"""
from django.conf import settings
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

PURPOSES = {"design": ("budget_ai", "budget_ai_health"),
            "adjustment": ("budget_adjust_ai", "budget_adjust_ai_health")}


class ConsentRequired(APIException):
    status_code = 403
    default_code = "budget_adjust_consent_required"
    default_detail = "예산 조정 AI 전송 동의를 먼저 확인해 주세요."


class ConsentUnavailable(APIException):
    status_code = 409
    default_code = "budget_ai_unavailable"
    default_detail = "현재 이 AI 기능이 꺼져 있어 새 동의를 받을 수 없어요. 기존 동의는 철회할 수 있어요."


def available(scope):
    if scope == "design":
        from .design_service import external_enabled
        return external_enabled()
    return getattr(settings, "CHAT_LLM", "fake") == "live"


def document(purpose):
    return documents.CURRENT[(purpose, None)]


def document_hash(doc):
    return documents.canonical_hash(doc)


def has_consent(user, scope="adjustment"):
    if not getattr(user, "is_authenticated", False):
        return False
    for purpose in PURPOSES[scope]:
        doc = document(purpose)
        if not Consent.objects.filter(user=user, purpose=purpose, product_scope__isnull=True, revoked_at__isnull=True,
                                      document_version=doc["version"], document_hash=document_hash(doc)).exists():
            return False
    return True


def require_consent(user):
    if not has_consent(user, "adjustment"):
        raise ConsentRequired()


def consent_state(user, scope):
    purpose, health_purpose = PURPOSES[scope]
    doc, health = document(purpose), document(health_purpose)
    rows = Consent.objects.filter(user=user, purpose__in=PURPOSES[scope], product_scope__isnull=True)
    latest = rows.order_by("-granted_at", "-pk").first()
    active = rows.filter(revoked_at__isnull=True).exists()
    granted = has_consent(user, scope)
    status = ("granted" if granted else "not_granted" if latest is None else
              "needs_renewal" if active else "revoked")
    return {"purpose": purpose, "available": available(scope), "status": status, "can_revoke": active,
            "document": doc, "document_version": doc["version"], "document_hash": document_hash(doc),
            "health_document": health, "health_document_version": health["version"],
            "health_document_hash": document_hash(health),
            "granted_at": latest.granted_at if latest else None,
            "revoked_at": latest.revoked_at if latest else None}


def consent_response(request, scope):
    if request.method == "GET":
        return Response(consent_state(request.user, scope))
    data = body_dict(request)
    with transaction.atomic():
        # 추천·조정 요청과 같은 회원 잠금: 이미 시작된 요청과 철회 기록의 순서를 보존한다.
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        rows = Consent.objects.filter(user=user, purpose__in=PURPOSES[scope], product_scope__isnull=True,
                                      revoked_at__isnull=True)
        if request.method == "DELETE":
            reject_unknown_fields(data, {"confirm"})
            if data.get("confirm") is not True:
                raise ValidationError({"confirm": ["AI 전송 동의 철회를 확인해 주세요."]})
            rows.update(revoked_at=timezone.now())
        else:
            if not available(scope):
                raise ConsentUnavailable()
            reject_unknown_fields(data, {"agreed", "document_version", "document_hash",
                                         "health_agreed", "health_document_version", "health_document_hash"})
            checked = []
            for purpose, prefix in zip(PURPOSES[scope], ("", "health_")):
                if data.get(prefix + "agreed") is not True:
                    raise ValidationError({prefix + "agreed": ["각 동의 내용을 확인한 뒤 직접 선택해 주세요."]})
                version = data.get(prefix + "document_version")
                if not isinstance(version, str):
                    raise ValidationError({prefix + "document_version": ["화면에 표시된 문서 버전이 필요해요."]})
                hashed = documents.consent_hash(purpose, None, version)
                if data.get(prefix + "document_hash") != hashed:
                    raise documents.DocumentOutdated()
                checked.append((purpose, version, hashed))
            if not has_consent(user, scope):
                rows.update(revoked_at=timezone.now())
                for purpose, version, hashed in checked:
                    Consent.objects.create(user=user, purpose=purpose, document_version=version, document_hash=hashed)
    return Response(consent_state(user, scope))


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def design_consent(request):
    return consent_response(request, "design")


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def adjustment_consent(request):
    return consent_response(request, "adjustment")
