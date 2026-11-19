from django.conf import settings
from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from budgets.models import Budget

from .consent_documents import consent_hash, current_items
from .models import Consent, User
from .serializers import (
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    SignupSerializer,
)


# 로그인 전에는 IP별, 로그인 후에는 회원별로 센다. 횟수는 settings 의 DEFAULT_THROTTLE_RATES
class LoginThrottle(UserRateThrottle):
    scope = "login"


class SignupThrottle(UserRateThrottle):
    scope = "signup"


class PasswordResetThrottle(UserRateThrottle):
    scope = "password_reset"


class WithdrawThrottle(UserRateThrottle):
    scope = "withdraw"

    def allow_request(self, request, view):
        # /me/ 는 GET(내 계정 확인)도 같은 함수라, 비밀번호를 받는 DELETE(탈퇴)만 센다
        return request.method != "DELETE" or super().allow_request(request, view)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def login(request):
    SessionAuthentication().enforce_csrf(request)

    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = authenticate(
        request=request,
        email=serializer.validated_data["email"],
        password=serializer.validated_data["password"],
    )

    if (
        user is None
        or not user.is_active
        or user.status == User.Status.WITHDRAWN
    ):
        raise ValidationError({
            "detail": "이메일 또는 비밀번호를 확인해 주세요."
        })

    onboarding_completed = Budget.objects.filter(
        user=user,
        approved_at__isnull=False,
    ).exists()

    django_login(request, user)

    return Response({
        "user_id": str(user.user_id),
        "email": user.email,
        "nickname": user.nickname,
        "onboarding_completed": onboarding_completed,
    })

# login
@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
@throttle_classes([WithdrawThrottle])
def me(request):
    if request.method == "DELETE":
        from .api_privacy import withdraw
        return withdraw(request)
    user = request.user

    onboarding_completed = Budget.objects.filter(
        user=user,
        approved_at__isnull=False,
    ).exists()

    return Response({
        "user_id": str(user.user_id),
        "email": user.email,
        "nickname": user.nickname,
        "onboarding_completed": onboarding_completed,
    })

# logout
@api_view(["POST"])
@permission_classes([AllowAny])
def logout(request):
    SessionAuthentication().enforce_csrf(request)

    data = request.data

    if not isinstance(data, dict):
        raise ValidationError({
            "non_field_errors": ["요청 본문은 비워 주세요."]
        })

    if data:
        raise ValidationError({
            name: ["이 칸은 보낼 수 없어요."]
            for name in data
        })

    django_logout(request)

    return Response({"ok": True})


# signup
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([SignupThrottle])
def signup(request):
    SessionAuthentication().enforce_csrf(request)

    if request.user.is_authenticated:
        raise ValidationError({
            "detail": "로그아웃한 뒤 회원가입해 주세요."
        })

    serializer = SignupSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    consent_records = {}

    for consent in data["consents"]:
        purpose = consent["purpose"]
        version = consent["document_version"]
        try:
            document_hash = consent_hash(purpose, None, version)
        except ValidationError:
            raise ValidationError({
                "consents": ["최신 약관을 확인하고 다시 동의해 주세요."]
            })

        consent_records[purpose] = {
            "purpose": purpose,
            "document_version": version,
            "document_hash": document_hash,
        }

    with transaction.atomic():
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=data["email"],
                    password=data["password"],
                    nickname=data["nickname"],
                )
        except IntegrityError:
            if User.objects.filter(email=data["email"]).exists():
                raise ValidationError({
                    "email": ["이미 가입된 이메일이에요"]
                })
            raise

        Consent.objects.bulk_create([
            Consent(user=user, **record)
            for record in consent_records.values()
        ])

    django_login(request, user)

    return Response({
        "user_id": str(user.user_id),
        "email": user.email,
        "nickname": user.nickname,
        "onboarding_completed": False,
    }, status=201)


def can_log_in(user):
    return user.is_active and user.status != User.Status.WITHDRAWN


RESET_LINK_INVALID = "링크가 만료됐거나 이미 사용됐어요. 비밀번호 찾기를 다시 해 주세요."


# 비밀번호 찾기 1단계: 재설정 링크를 메일로 보낸다.
# 가입 여부를 알려 주지 않으려고 없는 이메일·탈퇴 계정에도 똑같이 {"ok": true} 로 답한다
@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([PasswordResetThrottle])
def password_reset(request):
    SessionAuthentication().enforce_csrf(request)

    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = User.objects.filter(email=serializer.validated_data["email"]).first()

    if user is not None and can_log_in(user):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
        send_mail(
            "[머니쿠션] 비밀번호 재설정 안내",
            "아래 링크에서 새 비밀번호를 정해 주세요. 링크는 1시간 동안 한 번만 쓸 수 있어요.\n\n"
            f"{link}\n\n"
            "비밀번호 찾기를 요청하지 않았다면 이 메일을 무시해 주세요.",
            None,
            [user.email],
        )

    return Response({"ok": True})


# 비밀번호 찾기 2단계: 메일 링크의 uid·token 을 확인하고 새 비밀번호로 바꾼다.
# 토큰은 비밀번호 해시가 바뀌면 무효가 되어 한 번만 쓰이고, 다른 기기의 로그인 세션도 함께 끊긴다
@api_view(["POST"])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    SessionAuthentication().enforce_csrf(request)

    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(data["uid"])))
    except (TypeError, ValueError, OverflowError, DjangoValidationError, User.DoesNotExist):
        user = None

    if (
        user is None
        or not can_log_in(user)
        or not default_token_generator.check_token(user, data["token"])
    ):
        raise ValidationError({"detail": RESET_LINK_INVALID})

    user.set_password(data["password"])
    user.save(update_fields=["password"])

    return Response({"ok": True})


# 동의 문서 — 회원가입 전에도 읽어야 해서 로그인 없이 연다. 화면은 받은 document 를 그대로 그리고 version 만 돌려보낸다
@api_view(["GET"])
@permission_classes([AllowAny])
def consent_documents(request):
    return Response({"items": current_items()})
