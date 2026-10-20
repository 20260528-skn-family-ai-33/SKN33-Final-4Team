"""
카테고리 · 급여 주기 · 예산 · 지출 배분 · 준비금 · 쿠션 (ERD v2)

테이블: category, budget_cycle, budget, budget_item, budget_category_setting,
        spending_allocation, reserve_plan, cushion_entry

- 작성 규칙(UUID PK, db_table, settings.AUTH_USER_MODEL)은 docs/DB_MODELS.md
- on_delete는 FK_삭제규칙.md 표 그대로 (이유도 거기에)
- 여기 있는 CHECK·UNIQUE는 "한 줄 안에서" 지킬 수 있는 규칙만이다.
  같은 회원·같은 주기인지, 예산 합계, 승인 교체, 쿠션 잔액 같은 건 서비스 코드에서 트랜잭션으로 검사한다.
- "# 코드 미정" 표시 칸은 값 목록이 아직 안 정해진 칸이다 (docs/DB_MODELS.md 5번)
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

USER = settings.AUTH_USER_MODEL


def uuid_pk():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


class Category(models.Model):
    """소비 카테고리. 쓰던 카테고리는 지우지 않고 is_active=False로 숨긴다 (목록은 2026-10-20 확정)"""

    category_code = models.CharField("내부 코드", max_length=30, primary_key=True)
    display_name = models.CharField("표시 이름", max_length=30, unique=True)
    is_ml_label = models.BooleanField("ML 학습 정답 여부")
    is_living_budget_category = models.BooleanField("생활비 예산 모델 대상")
    display_order = models.SmallIntegerField("화면 정렬 순서")  # 순서 바꾸기 편하게 UNIQUE 안 둠 (2026-10-19 결정)
    is_active = models.BooleanField("사용 여부", default=True)

    class Meta:
        db_table = "category"
        verbose_name = verbose_name_plural = "카테고리"
        ordering = ["display_order"]

    def __str__(self):
        return f"{self.display_name}({self.category_code})"


class BudgetCycle(models.Model):
    """급여 주기. starts_on 이상 ~ ends_before 미만"""

    cycle_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="budget_cycles")
    starts_on = models.DateField("주기 시작일")
    ends_before = models.DateField("주기 종료 경계일")
    expected_payday = models.DateField("다음 급여 예정일", null=True, blank=True)
    confirmed_income = models.BigIntegerField("확인된 주기 수입", null=True, blank=True)
    approved_carryover = models.BigIntegerField("승인된 이월금", null=True, blank=True)
    status = models.CharField("진행 상태", max_length=20)  # 코드 미정
    revision_no = models.IntegerField("동시성 확인 번호", default=0)

    class Meta:
        db_table = "budget_cycle"
        verbose_name = verbose_name_plural = "급여 주기"
        constraints = [
            models.UniqueConstraint(fields=["user", "starts_on"], name="budget_cycle_user_id_starts_on_key"),
            models.CheckConstraint(condition=Q(starts_on__lt=models.F("ends_before")), name="bc_starts_before_ends"),
        ]

    def __str__(self):
        return f"{self.starts_on} ~ {self.ends_before}"


class BudgetCategorySetting(models.Model):
    """주기별 카테고리 자물쇠·변경 횟수"""

    setting_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="budget_category_settings")
    cycle = models.ForeignKey(BudgetCycle, on_delete=models.CASCADE, related_name="category_settings")
    category = models.ForeignKey(
        Category, on_delete=models.RESTRICT, db_column="category_code", related_name="+",
    )
    locked = models.BooleanField("자물쇠", default=False)
    change_count = models.IntegerField("변경 횟수", default=0)
    revision_no = models.IntegerField("동시 변경 확인 번호", default=0)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        db_table = "budget_category_setting"
        verbose_name = verbose_name_plural = "카테고리 설정"
        constraints = [
            models.UniqueConstraint(
                fields=["cycle", "category"], name="budget_category_setting_cycle_id_category_code_key",
            ),
        ]


class ReservePlan(models.Model):
    """준비금 계획 (예: 연말 경조사비)"""

    reserve_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="reserve_plans")
    name = models.CharField("용도", max_length=100)
    target_amount = models.BigIntegerField("목표 금액")
    confirmed_reserved_amount = models.BigIntegerField("확인된 기적립액", null=True, blank=True)
    due_date = models.DateField("준비 기한", null=True, blank=True)
    confirmation_state = models.CharField("확인 상태", max_length=20)  # 코드 미정
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        db_table = "reserve_plan"
        verbose_name = verbose_name_plural = "준비금 계획"

    def __str__(self):
        return self.name


class Budget(models.Model):
    """예산 1개 버전. 주기마다 현재 적용(approved)은 최대 1개"""

    class Status(models.TextChoices):
        DRAFT = "draft", "초안"
        VALIDATED = "validated", "검수 통과"
        APPROVED = "approved", "현재 적용"
        SUPERSEDED = "superseded", "이전 예산"
        REJECTED = "rejected", "거절"
        EXPIRED = "expired", "만료"

    budget_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="budgets")
    cycle = models.ForeignKey(BudgetCycle, on_delete=models.RESTRICT, related_name="budgets")
    base_budget = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="revisions",
    )
    task = models.ForeignKey(
        "chat.AsyncTask", null=True, blank=True, on_delete=models.SET_NULL, related_name="budgets",
    )
    version_no = models.IntegerField("주기 내 버전")
    status = models.CharField("상태", max_length=20, choices=Status.choices, default=Status.DRAFT)
    reason = models.CharField("변경 사유", max_length=200, null=True, blank=True)
    funding_amount = models.BigIntegerField("배분 대상 자금", null=True, blank=True)
    input_hash = models.CharField("계산 입력 해시", max_length=128, null=True, blank=True)
    input_snapshot = models.JSONField("승인 당시 계산 입력", null=True, blank=True)
    engine_version = models.CharField("계산 엔진 버전", max_length=50, null=True, blank=True)
    policy_version = models.CharField("계산 규칙 버전", max_length=50, null=True, blank=True)
    validation_summary = models.JSONField("검수 요약", null=True, blank=True)
    validated_at = models.DateTimeField("엔진 검수 시각", null=True, blank=True)
    approval_key = models.CharField("중복 승인 방지 키", max_length=128, null=True, blank=True)
    approved_at = models.DateTimeField("승인 시각", null=True, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "budget"
        verbose_name = verbose_name_plural = "예산"
        constraints = [
            models.UniqueConstraint(fields=["cycle", "version_no"], name="budget_cycle_id_version_no_key"),
            models.UniqueConstraint(fields=["user", "approval_key"], name="budget_user_id_approval_key_key"),
            # 주기마다 현재 적용 예산은 최대 1개
            models.UniqueConstraint(
                fields=["cycle"], condition=Q(status="approved"), name="budget_one_approved_per_cycle",
            ),
            models.CheckConstraint(condition=Q(status__in=["draft", "validated", "approved", "superseded", "rejected", "expired"]), name="budget_status_valid"),
            models.CheckConstraint(condition=~Q(base_budget=models.F("budget_id")), name="budget_base_not_self"),
        ]

    def __str__(self):
        return f"v{self.version_no} {self.status}"


class Bucket(models.TextChoices):
    LIVING = "living", "생활비"
    FIXED = "fixed", "고정비"
    SAVINGS = "savings", "저축"
    RESERVE = "reserve", "준비금"
    CUSHION = "cushion", "쿠션"


class BudgetItem(models.Model):
    """예산 항목. 여윳돈(free)은 저장하지 않고 funding_amount − 항목 합계로 계산"""

    item_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="budget_items")
    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="items")
    reserve = models.ForeignKey(
        ReservePlan, null=True, blank=True, on_delete=models.RESTRICT, related_name="budget_items",
    )
    bucket = models.CharField("구분", max_length=20, choices=Bucket.choices)
    category = models.ForeignKey(
        Category, null=True, blank=True, on_delete=models.RESTRICT,
        db_column="category_code", related_name="+",
    )
    category_detail = models.CharField("세부 분류", max_length=30, null=True, blank=True)
    allocated_amount = models.BigIntegerField("배정 금액")

    class Meta:
        db_table = "budget_item"
        verbose_name = verbose_name_plural = "예산 항목"
        constraints = [
            # 빈칸(NULL)끼리도 같은 값으로 보고 중복을 막는다 (PostgreSQL 15+)
            models.UniqueConstraint(
                fields=["budget", "bucket", "category", "category_detail", "reserve"],
                nulls_distinct=False,
                name="budget_item_unique_line",
            ),
            models.CheckConstraint(condition=Q(bucket__in=Bucket.values), name="bi_bucket_valid"),
            # bucket=reserve면 준비 계획 필수
            models.CheckConstraint(
                condition=~Q(bucket="reserve") | Q(reserve__isnull=False), name="bi_reserve_required",
            ),
        ]


class SpendingAllocation(models.Model):
    """소비 1건을 어느 재원에서 얼마 충당했는지. 새 예산 승인 때 복제하지 않는다"""

    class FundingBucket(models.TextChoices):
        LIVING = "living", "생활비"
        FIXED = "fixed", "고정비"
        FREE = "free", "여윳돈"
        CUSHION = "cushion", "쿠션"
        RESERVE = "reserve", "준비금"

    allocation_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="spending_allocations")
    transaction = models.ForeignKey(
        "finance.FinancialTransaction", on_delete=models.CASCADE, related_name="allocations",
    )
    cycle = models.ForeignKey(BudgetCycle, on_delete=models.RESTRICT, related_name="allocations")
    budget = models.ForeignKey(Budget, on_delete=models.RESTRICT, related_name="allocations")
    funding_bucket = models.CharField("재원", max_length=20, choices=FundingBucket.choices)
    reserve = models.ForeignKey(
        ReservePlan, null=True, blank=True, on_delete=models.RESTRICT, related_name="allocations",
    )
    category = models.ForeignKey(
        Category, on_delete=models.RESTRICT, db_column="category_code", related_name="+",
    )
    amount = models.BigIntegerField("충당액")
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "spending_allocation"
        verbose_name = verbose_name_plural = "지출 배분"
        constraints = [
            models.UniqueConstraint(
                fields=["transaction", "cycle", "funding_bucket", "reserve", "category"],
                nulls_distinct=False,
                name="spending_allocation_unique_line",
            ),
            models.CheckConstraint(
                condition=Q(funding_bucket__in=["living", "fixed", "free", "cushion", "reserve"]), name="sa_funding_bucket_valid",
            ),
            # funding_bucket=reserve면 준비 계획 필수
            models.CheckConstraint(
                condition=~Q(funding_bucket="reserve") | Q(reserve__isnull=False), name="sa_reserve_required",
            ),
        ]


class CushionEntry(models.Model):
    """쿠션 증감 기록. 현재 확보액 = 이 표 amount 합계. 지우지 않고 정정은 새 줄로"""

    class EntryType(models.TextChoices):
        INITIAL = "initial", "초기 확보액"
        ALLOCATION = "allocation", "예산 적립"
        USE = "use", "사용"
        REFUND = "refund", "환급"
        ADJUSTMENT = "adjustment", "정정"

    entry_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="cushion_entries")
    cycle = models.ForeignKey(
        BudgetCycle, null=True, blank=True, on_delete=models.RESTRICT, related_name="cushion_entries",
    )
    budget = models.ForeignKey(
        Budget, null=True, blank=True, on_delete=models.RESTRICT, related_name="cushion_entries",
    )
    allocation = models.ForeignKey(
        SpendingAllocation, null=True, blank=True, on_delete=models.RESTRICT, related_name="cushion_entries",
    )
    reference_entry = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.RESTRICT, related_name="follow_ups",
    )
    entry_type = models.CharField("종류", max_length=20, choices=EntryType.choices)
    amount = models.BigIntegerField("증감액")  # 증가 +, 감소 −
    request_key = models.CharField("중복 방지 키", max_length=128)
    occurred_at = models.DateTimeField("확정 시각")
    note = models.CharField("사유", max_length=200, null=True, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "cushion_entry"
        verbose_name = verbose_name_plural = "쿠션 증감"
        constraints = [
            models.UniqueConstraint(fields=["user", "request_key"], name="cushion_entry_user_request_key"),
            # 초기 확보액은 회원당 한 번
            models.UniqueConstraint(
                fields=["user"], condition=Q(entry_type="initial"), name="cushion_one_initial_per_user",
            ),
            # 지출 배분 1건당 쿠션 사용 기록 한 번
            models.UniqueConstraint(
                fields=["allocation"], condition=Q(entry_type="use"), name="cushion_one_use_per_allocation",
            ),
            # 종류별 부호: initial ≥0, use <0, refund >0, allocation·adjustment ≠0
            models.CheckConstraint(
                condition=(
                    Q(entry_type="initial", amount__gte=0)
                    | Q(entry_type="use", amount__lt=0)
                    | Q(entry_type="refund", amount__gt=0)
                    | (Q(entry_type__in=["allocation", "adjustment"]) & ~Q(amount=0))
                ),
                name="cushion_amount_sign_by_type",
            ),
            # 종류별 필수 연결: initial은 연결 없음, use·refund는 배분 필수, allocation은 주기·예산 필수
            models.CheckConstraint(
                condition=(
                    Q(entry_type="initial", cycle__isnull=True, budget__isnull=True,
                      allocation__isnull=True, reference_entry__isnull=True)
                    | Q(entry_type__in=["use", "refund"], allocation__isnull=False)
                    | Q(entry_type="allocation", cycle__isnull=False, budget__isnull=False)
                    | Q(entry_type="adjustment")
                ),
                name="cushion_fk_by_type",
            ),
            models.CheckConstraint(
                condition=~Q(reference_entry=models.F("entry_id")), name="cushion_ref_not_self",
            ),
        ]
