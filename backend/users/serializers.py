import re
from rest_framework import serializers
from .models import User, normalize_email

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)

    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
    )

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({
                "non_field_errors": ["JSON 객체로 보내주세요."]
            })

        errors = {}

        for name, value in data.items():
            if name not in self.fields:
                errors[name] = ["이 칸은 보낼 수 없어요."]

            elif not isinstance(value, str):
                errors[name] = ["문자열로 입력해주세요."]

        if errors:
            raise serializers.ValidationError(errors)

        return super().to_internal_value(data)

    def validate_email(self, value):
        return normalize_email(value)



class SignupConsentSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(
        choices=["terms", "privacy"],
    )

    document_version = serializers.ChoiceField(
        choices=["v1"],
    )

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({
                "non_field_errors": ["동의 항목은 JSON 객체로 보내 주세요."]
            })

        errors = {}

        for name, value in data.items():
            if name not in self.fields:
                errors[name] = ["이 칸은 보낼 수 없어요."]
            elif not isinstance(value, str):
                errors[name] = ["문자열로 입력해 주세요."]

        if errors:
            raise serializers.ValidationError(errors)

        return super().to_internal_value(data)


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)

    password = serializers.CharField(
        min_length=8,
        max_length=25,
        write_only=True,
        trim_whitespace=False,
    )

    nickname = serializers.CharField(
        min_length=1,
        max_length=100,
        trim_whitespace=True,
    )

    consents = SignupConsentSerializer(
        many=True,
        allow_empty=False,
    )

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({
                "non_field_errors": ["JSON 객체로 보내 주세요."]
            })

        errors = {}

        for name, value in data.items():
            if name not in self.fields:
                errors[name] = ["이 칸은 보낼 수 없어요."]
            elif name != "consents" and not isinstance(value, str):
                errors[name] = ["문자열로 입력해 주세요."]

        if errors:
            raise serializers.ValidationError(errors)

        return super().to_internal_value(data)

    def validate_email(self, value):
        email = normalize_email(value)

        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError(
                "이미 가입된 이메일이에요"
            )

        return email

    def validate_password(self, value):
        kinds = [
            bool(re.search(r"[A-Za-z]", value)),
            bool(re.search(r"[0-9]", value)),
            bool(re.search(r"[^A-Za-z0-9\s]", value)),
        ]

        if sum(kinds) < 2:
            raise serializers.ValidationError(
                "영문·숫자·특수문자 중 2가지 이상 포함해 주세요."
            )

        return value

    def validate_consents(self, value):
        purposes = {consent["purpose"] for consent in value}

        if not {"terms", "privacy"}.issubset(purposes):
            raise serializers.ValidationError(
                "서비스 이용약관과 개인정보 동의가 모두 필요해요."
            )

        return value