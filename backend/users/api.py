from django.contrib.auth import authenticate, login as django_login, logout as django_logout
from django.db import IntegrityError, transaction

from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from budgets.models import Budget

from .consent_documents import consent_hash, current_items
from .models import Consent, User
from .serializers import LoginSerializer, SignupSerializer

@api_view(["POST"])
@permission_classes([AllowAny])
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


# 동의 문서 — 회원가입 전에도 읽어야 해서 로그인 없이 연다. 화면은 받은 document 를 그대로 그리고 version 만 돌려보낸다
@api_view(["GET"])
@permission_classes([AllowAny])
def consent_documents(request):
    return Response({"items": current_items()})
