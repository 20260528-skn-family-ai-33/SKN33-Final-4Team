"""
회원 · 내 정보 · 소비 우선순위 · 카테고리 최소 금액 · 동의 (ERD: app_user, financial_profile, category_priority, category_minimum, consent)

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
from rest_framework.utils.encoders import JSONEncoder as DRFJSONEncoder
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q
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


class FinancialProfile(models.Model):
    """내 정보·예산 기준 (회원당 1개). PK가 곧 회원 ID"""

    class HousingType(models.TextChoices):  # 2026-10-27 온보딩: 자취 여부로 지킬 돈 비율을 정한다
        ALONE = "alone", "자취"
        FAMILY = "family", "본가 거주"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE,
        db_column="user_id", related_name="financial_profile",
    )
    birth_date = models.DateField("생년월일", null=True, blank=True)
    gender = models.CharField("성별", max_length=20, null=True, blank=True)
    loan_presence = models.CharField("대출 보유 응답", max_length=20, null=True, blank=True)
    has_car = models.BooleanField("자동차 보유", null=True, blank=True)  # NULL=미응답, false=미보유
    housing_type = models.CharField(
        "거주 형태", max_length=10, choices=HousingType.choices, null=True, blank=True,
    )  # NULL=미응답. 자취 55·30·5·10 / 본가 45·40·5·10 (docs/api/budgets.md P-02)
    monthly_take_home_income = models.BigIntegerField("월 실수령액", null=True, blank=True)
    monthly_disposable_income = models.BigIntegerField("모델용 처분가능소득", null=True, blank=True)
    disposable_income_basis = models.CharField("처분가능소득 근거", max_length=30, null=True, blank=True)
    payday = models.SmallIntegerField("매월 급여일", null=True, blank=True)  # 1~31
    payday_rule = models.CharField("급여일 보정 규칙", max_length=30, null=True, blank=True)
    budget_direction = models.CharField("예산 산정 방향", max_length=30, null=True, blank=True)
    # 지킬 돈 (지킬 돈 설정 화면 · 2026-10-21 A안): 생활비 · 저축 · 월 쿠션 적립금 세 칸 모두 사용자 설정값
    # 이 값으로 매 주기 예산(budget_item)을 나눈다. 실제 배정 결과는 budget_item 에 있다
    living_budget = models.BigIntegerField("월 생활비", null=True, blank=True)  # NULL=미설정. 카테고리 예산 합계의 기준
    savings_floor = models.BigIntegerField("월 저축 보호액", null=True, blank=True)
    cushion_enabled = models.BooleanField("쿠션 설정 여부", null=True, blank=True)
    cushion_target = models.BigIntegerField("쿠션 목표액", null=True, blank=True)
    cushion_monthly = models.BigIntegerField("월 쿠션 적립금", null=True, blank=True)  # 2026-10-21 보호 하한(cushion_floor) 대체
    cushion_use_policy = models.CharField("쿠션 사용 조건", max_length=50, null=True, blank=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        db_table = "financial_profile"
        verbose_name = verbose_name_plural = "내 정보"
        constraints = [
            models.CheckConstraint(
                condition=Q(payday__isnull=True) | Q(payday__gte=1, payday__lte=31),
                name="fp_payday_1_31",
            ),
            models.CheckConstraint(
                condition=Q(living_budget__isnull=True) | Q(living_budget__gte=0), name="fp_living_budget_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(cushion_monthly__isnull=True) | Q(cushion_monthly__gte=0), name="fp_cushion_monthly_gte_0",
            ),
            models.CheckConstraint(
                condition=Q(housing_type__isnull=True) | Q(housing_type__in=["alone", "family"]),
                name="fp_housing_type_valid",
            ),
        ]

    def __str__(self):
        return f"내 정보({self.user_id})"


class CategoryPriority(models.Model):
    """소비 우선순위. 회원이 고른 카테고리 수만큼 한 줄씩 (2026-10-20: 1~3위 필수, 4위부터는 원하는 만큼)

    - DB가 막는 것: 같은 순위 2개, 같은 카테고리를 두 순위에, 순위 0 이하
    - API에서 검사할 것: 1~3위 필수, 순위 건너뛰기 금지(1·2·4위처럼)
    - 순위를 바꿀 때는 한 줄씩 고치지 말고 그 회원의 순위를 전부 지운 뒤 새 목록을 다시 넣는다 (트랜잭션 하나로).
      한 줄씩 1위↔2위를 바꾸면 중간에 UNIQUE(회원, 순위)에 걸린다
    """

    category_priority_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="category_priorities")
    category = models.ForeignKey(
        "budgets.Category", on_delete=models.RESTRICT,
        db_column="category_code", related_name="+", verbose_name="카테고리",
    )
    rank = models.SmallIntegerField("순위")  # 1 = 가장 중요

    class Meta:
        db_table = "category_priority"
        verbose_name = verbose_name_plural = "소비 우선순위"
        constraints = [
            models.UniqueConstraint(fields=["user", "rank"], name="cp_user_rank_key"),
            models.UniqueConstraint(fields=["user", "category"], name="cp_user_category_key"),
            models.CheckConstraint(condition=Q(rank__gte=1), name="cp_rank_gte_1"),
        ]

    def __str__(self):
        return f"{self.rank}위 {self.category_id}"


class CategoryMinimum(models.Model):
    """카테고리 최소 금액. 회원이 설정한 카테고리만 한 줄씩 (2026-10-21, 온보딩에서 선택 입력)

    - 우선순위와 상관없이 예산 칸이 있는 카테고리(10개 + 기타) 어디든 걸 수 있다
    - 자물쇠와 다르다: 자물쇠 = 금액 그대로 고정 / 최소 금액 = 이 금액 밑으로만 안 내려감(올리는 건 가능)
    - 설정 안 한 카테고리는 줄이 없다 (0원으로 저장하지 않는다)
    - DB가 막는 것: 같은 카테고리 두 번, 1원 미만
    - 서비스 코드에서 검사할 것: 최소 금액 합계 ≤ 생활비 총액 (예산 만들 때 검사, 넘으면 부족액 안내 후 수정 받기)
    - 바꿀 때는 그 회원 것을 전부 지우고 새 목록을 다시 넣는다 (트랜잭션 하나로)
    """

    category_minimum_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="category_minimums")
    category = models.ForeignKey(
        "budgets.Category", on_delete=models.RESTRICT,
        db_column="category_code", related_name="+", verbose_name="카테고리",
    )
    min_amount = models.BigIntegerField("카테고리 최소 금액")  # 원 단위

    class Meta:
        db_table = "category_minimum"
        verbose_name = verbose_name_plural = "카테고리 최소 금액"
        constraints = [
            models.UniqueConstraint(fields=["user", "category"], name="cm_user_category_key"),
            models.CheckConstraint(condition=Q(min_amount__gte=1), name="cm_min_amount_gte_1"),
        ]

    def __str__(self):
        return f"{self.category_id} 최소 {self.min_amount}원"


class Consent(models.Model):
    """약관·개인정보·기관 조회 동의 기록. 철회해도 행은 남기고 revoked_at만 채운다"""

    class ProductScope(models.TextChoices):  # 10-22 결정 L. 회원가입 동의(terms · privacy)는 비움
        BANK = "bank", "은행"
        CARD = "card", "카드"
        LOAN = "loan", "대출"

    consent_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consents")
    connection = models.ForeignKey(
        "finance.Connection", null=True, blank=True, on_delete=models.SET_NULL, related_name="consents",
    )
    purpose = models.CharField("동의 목적", max_length=50)
    product_scope = models.CharField(
        "조회 허용 상품 범위", max_length=50, null=True, blank=True, choices=ProductScope.choices,
    )
    document_version = models.CharField("동의 문서 버전", max_length=50)
    document_hash = models.CharField("동의 원문 해시", max_length=128)
    granted_at = models.DateTimeField("동의 시각", default=timezone.now)
    revoked_at = models.DateTimeField("철회 시각", null=True, blank=True)

    class Meta:
        db_table = "consent"
        verbose_name = verbose_name_plural = "동의"
        constraints = [
            models.CheckConstraint(
                condition=Q(product_scope__isnull=True) | Q(product_scope__in=["bank", "card", "loan"]),
                name="consent_product_scope_valid",
            ),
        ]

    def __str__(self):
        return f"{self.purpose} ({self.document_version})"


class IdempotencyRecord(models.Model):
    """같은 저장 요청이 두 번 와도 한 번만 반영하기 위한 '주문 번호' 기록 (10-21 C-10)

    - 화면이 폼을 열 때 요청 번호(UUID)를 하나 만들어 Idempotency-Key 헤더로 보낸다.
    - 같은 회원 · 같은 번호가 또 오면 새로 처리하지 않고 처음 응답을 그대로 돌려준다.
    - 사용법과 규칙은 config/idempotency.py · docs/API_RULES.md '같은 요청을 두 번 보내면'
    """

    record_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="idempotency_records")
    key = models.CharField("요청 번호", max_length=64)
    method = models.CharField("요청 방식", max_length=10)
    path = models.CharField("요청 주소", max_length=200)
    request_hash = models.CharField("요청 내용 해시", max_length=64)  # 같은 번호에 다른 내용이 오면 막으려고
    response_status = models.SmallIntegerField("처음 응답 상태 코드")
    response_body = models.JSONField("처음 응답 내용", null=True, blank=True, encoder=DRFJSONEncoder)  # 응답과 똑같이 저장 (DRF 인코더: 시각의 마이크로초까지 유지)
    created_at = models.DateTimeField("처리 시각", auto_now_add=True)

    class Meta:
        db_table = "idempotency_record"
        verbose_name = verbose_name_plural = "요청 번호 기록"
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="idempotency_record_user_id_key_key"),
        ]

    def __str__(self):
        return f"{self.method} {self.path} ({self.key})"
