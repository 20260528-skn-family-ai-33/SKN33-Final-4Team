from rest_framework import serializers

from finance.inputs import reject_unknown_fields


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    """true · false 만 받는다 ("true" · 1 같은 값은 400)"""
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid")
        return data


class SalarySerializer(serializers.Serializer):
    take_home_income = StrictIntegerField(min_value=1, max_value=9_999_999_999_999)
    payday = StrictIntegerField(min_value=1, max_value=31)
    # 11/12: 급여일 · 실수령액 둘 다의 적용 시점. paydayApplyFrom 은 예전 이름(같은 뜻)이라 계속 받는다
    apply_from = serializers.ChoiceField(choices=["current_cycle", "next_cycle"], required=False)
    payday_apply_from = serializers.ChoiceField(choices=["current_cycle", "next_cycle"], required=False)
    # 11/13 검수: 이번 주기를 받은 월급 기준(budget_cycle.income_basis)으로 짠 뒤 월급을 이번 주기부터 바꿀 때만 필요.
    # true = 이번 주기는 받은 월급 기준 그대로 · false = 이번 주기도 새 월급으로 (받은 월급 기준을 푼다)
    keep_income_basis = StrictBooleanField(required=False)

    def validate(self, attrs):
        new, old = attrs.pop("apply_from", None), attrs.pop("payday_apply_from", None)
        if new and old and new != old:
            raise serializers.ValidationError({"apply_from": ["applyFrom 과 paydayApplyFrom 이 달라요. 하나만 보내 주세요."]})
        attrs["apply_from"] = new or old
        # 오류를 보낸 칸 이름으로 돌려주려고 기억한다 (둘 다 안 보냈으면 새 이름)
        attrs["apply_key"] = "payday_apply_from" if old and not new else "apply_from"
        return attrs

    def to_internal_value(self, data):
        if isinstance(data, dict):
            reject_unknown_fields(data, self.fields)
        return super().to_internal_value(data)


class SalaryGapSerializer(serializers.Serializer):
    """월급 차이 안내 답 (11/13). apply = 이번 주기만 실제 들어온 월급으로 예산을 나눈다 · keep = 등록 실수령액 그대로"""
    answer = serializers.ChoiceField(choices=["apply", "keep"])
    salary_amount = StrictIntegerField(min_value=1, max_value=9_999_999_999_999)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            reject_unknown_fields(data, self.fields)
        return super().to_internal_value(data)
