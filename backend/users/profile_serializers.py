import re

from django.utils import timezone
from rest_framework import serializers

from budgets.models import Category
from finance.inputs import reject_unknown_fields
from .models import FinancialProfile


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            reject_unknown_fields(data, self.fields)
        return super().to_internal_value(data)


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class StrictBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            self.fail("invalid", input=data)
        return data


class BirthDateField(serializers.DateField):
    def to_internal_value(self, data):
        if not isinstance(data, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data):
            raise serializers.ValidationError("YYYY-MM-DD 형식으로 입력해 주세요.")
        value = super().to_internal_value(data)
        today = timezone.localdate()
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if value > today or age > 120:
            raise serializers.ValidationError("생년월일을 확인해 주세요.")
        return value


class CategoryCodeField(serializers.CharField):
    def __init__(self, *, priority=False, **kwargs):
        self.priority = priority
        super().__init__(trim_whitespace=False, max_length=50, **kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        code = super().to_internal_value(data)
        categories = Category.objects.filter(is_active=True, is_living_budget_category=True)
        if self.priority:
            categories = categories.filter(is_ml_label=True)
        if not categories.filter(pk=code).exists():
            raise serializers.ValidationError("선택할 수 없는 카테고리예요.")
        return code


class PrioritySerializer(StrictSerializer):
    category = CategoryCodeField(priority=True)
    rank = StrictIntegerField(min_value=1, max_value=10)


class MinimumSerializer(StrictSerializer):
    category = CategoryCodeField()
    min_amount = StrictIntegerField(min_value=1, max_value=9_999_999_999)


class ProfileSerializer(StrictSerializer):
    birth_date = BirthDateField()
    gender = serializers.ChoiceField(choices=["male", "female"])
    housing_type = serializers.ChoiceField(choices=FinancialProfile.HousingType.choices)
    has_car = StrictBooleanField()
    priorities = PrioritySerializer(many=True, min_length=3, max_length=10, allow_empty=False)
    category_minimums = MinimumSerializer(many=True, max_length=11)

    def validate_priorities(self, rows):
        if len({row["category"] for row in rows}) != len(rows):
            raise serializers.ValidationError("카테고리를 중복 선택할 수 없어요.")
        if sorted(row["rank"] for row in rows) != list(range(1, len(rows) + 1)):
            raise serializers.ValidationError("순위는 1부터 빠짐없이 지정해 주세요.")
        return rows

    def validate_category_minimums(self, rows):
        if len({row["category"] for row in rows}) != len(rows):
            raise serializers.ValidationError("카테고리를 중복 선택할 수 없어요.")
        return rows
