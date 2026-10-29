from rest_framework import serializers

from finance.inputs import reject_unknown_fields


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class SalarySerializer(serializers.Serializer):
    take_home_income = StrictIntegerField(min_value=1, max_value=9_999_999_999_999)
    payday = StrictIntegerField(min_value=1, max_value=31)
    payday_apply_from = serializers.ChoiceField(choices=["current_cycle", "next_cycle"], required=False)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            reject_unknown_fields(data, self.fields)
        return super().to_internal_value(data)
