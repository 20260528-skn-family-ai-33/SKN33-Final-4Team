"""
회원 · 내 정보 · 동의 (ERD: app_user, financial_profile, consent)

- Django 기본 User 대신 이 모델을 쓴다. settings.AUTH_USER_MODEL = "users.User"
- 이메일로 로그인한다. 이메일은 전부 소문자로 바꿔서 저장한다.
- 비밀번호는 원문이 아니라 해시로 저장한다. 꼭 user.set_password()로 설정한다.
- password, last_login 칸은 AbstractBaseUser가, is_superuser 칸은 PermissionsMixin이 만들어 준다.
- 다른 모델에서 회원을 가리킬 때는 이 클래스를 import 하지 말고 settings.AUTH_USER_MODEL을 쓴다.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

모델 작성 규칙과 앱별 테이블 배치는 docs/DB_MODELS.md, on_delete 이유는 FK_삭제규칙.md
"""
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def normalize_email(email):
    """앞뒤 공백 제거 + 전체 소문자 (Django 기본은 @ 뒤쪽만 소문자로 바꿈)"""
    return (email or "").strip().lower()


class UserManager(BaseUserManager):
    """회원 만드는 도우미 (회원가입·createsuperuser가 사용)"""

    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("이메일은 필수예요")
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        user = self.model(email=normalize_email(email), **extra_fields)
        user.set_password(password)  # password=None이면 로그인 불가 비밀번호로 저장
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("관리자 계정은 is_staff·is_superuser가 True여야 해요")
        return self.create_user(email, password, **extra_fields)

    def get_by_natural_key(self, email):
        # 로그인할 때 대문자로 입력해도 찾을 수 있게
        return self.get(email=normalize_email(email))


class User(AbstractBaseUser, PermissionsMixin):
    class Status(models.TextChoices):
        ACTIVE = "active", "활성"
        DORMANT = "dormant", "휴면"
        WITHDRAWN = "withdrawn", "탈퇴"

    user_id = models.UUIDField("회원 ID", primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("이메일", max_length=255, unique=True)
    nickname = models.CharField("닉네임", max_length=100, null=True, blank=True)
    email_verified_at = models.DateTimeField("이메일 인증 시각", null=True, blank=True)
    password_changed_at = models.DateTimeField("비밀번호 변경 시각", null=True, blank=True)
    is_active = models.BooleanField("로그인 허용", default=True)
    is_staff = models.BooleanField("관리자 페이지 접근", default=False)
    status = models.CharField("회원 상태", max_length=20, choices=Status.choices, default=Status.ACTIVE)
    codef_connected_id_ciphertext = models.TextField("암호화된 CODEF connectedId", null=True, blank=True)
    codef_connected_at = models.DateTimeField("connectedId 발급 시각", null=True, blank=True)
    created_at = models.DateTimeField("가입 시각", auto_now_add=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"  # 로그인 아이디
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []  # createsuperuser가 이메일·비밀번호 말고 더 묻지 않음

    class Meta:
        db_table = "app_user"
        verbose_name = "회원"
        verbose_name_plural = "회원"

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # 관리자 화면 등 어느 경로로 저장해도 이메일은 소문자
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)


PRIORITY_FIELDS = [f"priority_{n}" for n in range(1, 7)]


def _priority_in_order():
    """n위가 있으면 n-1위도 있어야 한다 (1·2·4위처럼 건너뛰기 금지)"""
    q = Q()
    for prev, cur in zip(PRIORITY_FIELDS, PRIORITY_FIELDS[1:]):
        q &= Q(**{f"{cur}__isnull": True}) | Q(**{f"{prev}__isnull": False})
    return q


def _priority_distinct():
    """같은 카테고리를 두 순위에 넣지 않기 (빈칸끼리는 비교하지 않음)"""
    q = Q()
    for i, a in enumerate(PRIORITY_FIELDS):
        for b in PRIORITY_FIELDS[i + 1:]:
            q &= ~Q(**{a: F(b)})
    return q


class FinancialProfile(models.Model):
    """내 정보·예산 기준 (회원당 1개). PK가 곧 회원 ID"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE,
        db_column="user_id", related_name="financial_profile",
    )
    birth_date = models.DateField("생년월일", null=True, blank=True)
    gender = models.CharField("성별", max_length=20, null=True, blank=True)
    loan_presence = models.CharField("대출 보유 응답", max_length=20, null=True, blank=True)
    has_car = models.BooleanField("자동차 보유", null=True, blank=True)  # NULL=미응답, false=미보유
    monthly_take_home_income = models.BigIntegerField("월 실수령액", null=True, blank=True)
    monthly_disposable_income = models.BigIntegerField("모델용 처분가능소득", null=True, blank=True)
    disposable_income_basis = models.CharField("처분가능소득 근거", max_length=30, null=True, blank=True)
    payday = models.SmallIntegerField("매월 급여일", null=True, blank=True)  # 1~31
    payday_rule = models.CharField("급여일 보정 규칙", max_length=30, null=True, blank=True)
    budget_direction = models.CharField("예산 산정 방향", max_length=30, null=True, blank=True)
    priority_1 = models.ForeignKey(
        "budgets.Category", null=True, blank=True, on_delete=models.RESTRICT,
        db_column="priority_1", related_name="+", verbose_name="소비 우선순위 1위",
    )
    priority_2 = models.ForeignKey(
        "budgets.Category", null=True, blank=True, on_delete=models.RESTRICT,
        db_column="priority_2", related_name="+", verbose_name="소비 우선순위 2위",
    )
    priority_3 = models.ForeignKey(
        "budgets.Category", null=True, blank=True, on_delete=models.RESTRICT,
        db_column="priority_3", related_name="+", verbose_name="소비 우선순위 3위",
    )
    # 4~6위는 선택 입력 (1~3위 필수 여부는 API에서 검사. DB는 '순서대로 채웠는지'·'중복 없는지'만 막음)
    priority_4 = models.ForeignKey(
        "budgets.Category", null=True, blank=True, on_delete=models.RESTRICT,
        db_column="priority_4", related_name="+", verbose_name="소비 우선순위 4위(선택)",
    )
    priority_5 = models.ForeignKey(
        "budgets.Category", null=True, blank=True, on_delete=models.RESTRICT,
        db_column="priority_5", related_name="+", verbose_name="소비 우선순위 5위(선택)",
    )
    priority_6 = models.ForeignKey(
        "budgets.Category", null=True, blank=True, on_delete=models.RESTRICT,
        db_column="priority_6", related_name="+", verbose_name="소비 우선순위 6위(선택)",
    )
    living_floor = models.BigIntegerField("월 최소 생활비", null=True, blank=True)  # NULL=미설정, 0=보호액 없음
    savings_floor = models.BigIntegerField("월 저축 보호액", null=True, blank=True)
    cushion_enabled = models.BooleanField("쿠션 설정 여부", null=True, blank=True)
    cushion_target = models.BigIntegerField("쿠션 목표액", null=True, blank=True)
    cushion_floor = models.BigIntegerField("쿠션 보호 하한액", null=True, blank=True)
    cushion_use_policy = models.CharField("쿠션 사용 조건", max_length=50, null=True, blank=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        db_table = "financial_profile"
        verbose_name = verbose_name_plural = "내 정보"
        constraints = [
            models.CheckConstraint(
                condition=Q(living_floor__isnull=True) | Q(living_floor__gte=0),
                name="fp_living_floor_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(payday__isnull=True) | Q(payday__gte=1, payday__lte=31),
                name="fp_payday_1_31",
            ),
            models.CheckConstraint(condition=_priority_in_order(), name="fp_priority_in_order"),
            models.CheckConstraint(condition=_priority_distinct(), name="fp_priority_distinct"),
        ]

    def __str__(self):
        return f"내 정보({self.user_id})"


class Consent(models.Model):
    """약관·개인정보·기관 조회 동의 기록. 철회해도 행은 남기고 revoked_at만 채운다"""

    consent_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consents")
    connection = models.ForeignKey(
        "finance.Connection", null=True, blank=True, on_delete=models.SET_NULL, related_name="consents",
    )
    purpose = models.CharField("동의 목적", max_length=50)
    product_scope = models.CharField("조회 허용 상품 범위", max_length=50, null=True, blank=True)
    document_version = models.CharField("동의 문서 버전", max_length=50)
    document_hash = models.CharField("동의 원문 해시", max_length=128)
    granted_at = models.DateTimeField("동의 시각", default=timezone.now)
    revoked_at = models.DateTimeField("철회 시각", null=True, blank=True)

    class Meta:
        db_table = "consent"
        verbose_name = verbose_name_plural = "동의"

    def __str__(self):
        return f"{self.purpose} ({self.document_version})"
