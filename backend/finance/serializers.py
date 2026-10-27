import re

from rest_framework import serializers

from .models import Loan


class LoanPatchSerializer(serializers.Serializer):
    monthly_payment = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        max_value=100_000_000,
    )

    payment_day = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        max_value=31,
    )

    repayment_method = serializers.ChoiceField(
        choices=Loan.RepaymentMethod.choices,
        required=False,
        allow_null=True,
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
                continue

            if value is None:
                continue

            if name in ("monthly_payment", "payment_day"):
                if type(value) is not int:
                    errors[name] = ["정수로 입력해 주세요."]

            elif name == "repayment_method":
                if not isinstance(value, str):
                    errors[name] = ["문자열로 입력해 주세요."]

        if errors:
            raise serializers.ValidationError(errors)

        return super().to_internal_value(data)


class LoanCreateSerializer(LoanPatchSerializer):
    """직접 등록은 보완 세 칸에 기관명·상품명·잔액·금리·만기일을 더 받는다."""

    lender_name = serializers.CharField(max_length=50)
    product_name = serializers.CharField(max_length=50)
    manual_balance = serializers.IntegerField(min_value=1, max_value=1_000_000_000)
    manual_rate = serializers.DecimalField(
        max_digits=8,
        decimal_places=5,
        min_value=0,
        max_value=30,
        required=False,
        allow_null=True,
        coerce_to_string=False,
    )
    maturity_date = serializers.DateField(required=False, allow_null=True)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            errors = {}
            for name, value in data.items():
                if value is None:
                    continue
                if name in ("lender_name", "product_name") and not isinstance(value, str):
                    errors[name] = ["문자열로 입력해 주세요."]
                elif name == "manual_balance" and type(value) is not int:
                    errors[name] = ["정수로 입력해 주세요."]
                elif name == "manual_rate" and type(value) not in (int, float):
                    errors[name] = ["숫자로 입력해 주세요."]
                elif name == "maturity_date" and (
                    not isinstance(value, str)
                    or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
                ):
                    errors[name] = ["날짜는 YYYY-MM-DD 형식으로 입력해 주세요."]
            if errors:
                raise serializers.ValidationError(errors)
        return super().to_internal_value(data)
