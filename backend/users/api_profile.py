from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from finance.inputs import body_dict
from .models import CategoryMinimum, CategoryPriority, FinancialProfile
from .profile_serializers import ProfileSerializer


PROFILE_FIELDS = ("birth_date", "gender", "housing_type", "has_car")


def profile_data(user):
    saved = FinancialProfile.objects.filter(user=user).first()
    data = {name: getattr(saved, name, None) for name in PROFILE_FIELDS}
    data["priorities"] = [
        {"category": row.category_id, "category_name": row.category.display_name, "rank": row.rank}
        for row in CategoryPriority.objects.filter(user=user).select_related("category").order_by("rank")
    ]
    data["category_minimums"] = [
        {"category": row.category_id, "category_name": row.category.display_name, "min_amount": row.min_amount}
        for row in CategoryMinimum.objects.filter(user=user).select_related("category").order_by("category__display_order", "category_id")
    ]
    return data


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def profile(request):
    with transaction.atomic():
        # 조회와 저장 모두 회원 잠금으로 목록 일관성을 지킨다.
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        if request.method == "PUT":
            serializer = ProfileSerializer(data=body_dict(request))
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            FinancialProfile.objects.update_or_create(
                user=user, defaults={name: data[name] for name in PROFILE_FIELDS},
            )
            CategoryPriority.objects.filter(user=user).delete()
            CategoryPriority.objects.bulk_create([
                CategoryPriority(user=user, category_id=row["category"], rank=row["rank"])
                for row in data["priorities"]
            ])
            CategoryMinimum.objects.filter(user=user).delete()
            CategoryMinimum.objects.bulk_create([
                CategoryMinimum(user=user, category_id=row["category"], min_amount=row["min_amount"])
                for row in data["category_minimums"]
            ])
        return Response(profile_data(user))
