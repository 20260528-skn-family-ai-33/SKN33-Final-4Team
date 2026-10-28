"""관리자 화면(/admin)에 회원 등록. 기본 UserAdmin은 username을 찾기 때문에 이메일 기준으로 바꿔 쓴다."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import CategoryMinimum, CategoryPriority, Consent, FinancialProfile, IdempotencyRecord, User


class UserAddForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("email", "nickname")
        field_classes = {}


class UserEditForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"
        field_classes = {}


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserEditForm
    add_form = UserAddForm

    ordering = ("-created_at",)
    list_display = ("email", "nickname", "status", "is_active", "is_staff", "created_at")
    list_filter = ("status", "is_active", "is_staff")
    search_fields = ("email", "nickname")
    readonly_fields = ("user_id", "last_login", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("user_id", "email", "password")}),
        ("내 정보", {"fields": ("nickname", "status", "email_verified_at", "password_changed_at")}),
        ("CODEF", {"fields": ("codef_connected_id_ciphertext", "codef_connected_at")}),
        ("권한", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("시각", {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "nickname", "usable_password", "password1", "password2")}),
    )


@admin.register(FinancialProfile)
class FinancialProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "payday", "monthly_take_home_income", "has_car", "housing_type", "budget_direction", "living_budget", "savings_floor", "cushion_monthly", "updated_at")


@admin.register(CategoryPriority)
class CategoryPriorityAdmin(admin.ModelAdmin):
    list_display = ("user", "rank", "category")


@admin.register(CategoryMinimum)
class CategoryMinimumAdmin(admin.ModelAdmin):
    list_display = ("user", "category", "min_amount")


@admin.register(Consent)
class ConsentAdmin(admin.ModelAdmin):
    list_display = ("user", "purpose", "document_version", "granted_at", "revoked_at")


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    """요청 번호 기록 (같은 저장 요청 두 번 막기, 10-21) — 확인용이라 읽기만"""

    list_display = ("created_at", "user", "method", "path", "response_status", "key")
    list_filter = ("method", "response_status")
    search_fields = ("key", "path")
    readonly_fields = [f.name for f in IdempotencyRecord._meta.fields]
