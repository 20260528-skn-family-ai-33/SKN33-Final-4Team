"""
금융 연결 · 자산 · 거래 · 납부 (ERD v2)

테이블: connection, bank_account, card, loan, sync_run,
        financial_transaction, transaction_link,
        card_bill, card_bill_item, recurring_expense, payment_due, payment_match

- 작성 규칙(UUID PK, db_table, settings.AUTH_USER_MODEL)은 docs/DB_MODELS.md
- on_delete는 FK_삭제규칙.md 표 그대로 (이유도 거기에)
- 여기 있는 CHECK·UNIQUE는 "한 줄 안에서" 지킬 수 있는 규칙만이다.
  같은 회원인지, 여러 줄 합계, 동시 요청 같은 건 서비스 코드에서 검사해야 한다.
- "# 코드 미정" 표시 칸은 값 목록이 아직 안 정해진 칸이다 (docs/DB_MODELS.md 5번)
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

USER = settings.AUTH_USER_MODEL


def uuid_pk():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


def empty_dict():
    return {}


def empty_list():
    return []


# ===================== 기관 연결 · 자산 =====================

class Connection(models.Model):
    """은행·카드사 등 기관 연결 1건. 해제해도 행은 남기고 disconnected_at만 채운다"""

    connection_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="connections")
    business_type = models.CharField("업권", max_length=20)
    organization_code = models.CharField("CODEF 기관 코드", max_length=50)
    auth_method = models.CharField("인증 방식", max_length=50, null=True, blank=True)
    alias = models.CharField("연결 별칭", max_length=100, null=True, blank=True)
    status = models.CharField("연결 상태", max_length=30)  # 코드 미정
    last_authenticated_at = models.DateTimeField("최근 인증 시각", null=True, blank=True)
    disconnected_at = models.DateTimeField("연결 해제 시각", null=True, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "connection"
        verbose_name = verbose_name_plural = "기관 연결"
        constraints = [
            # 같은 회원·업권·기관에서 "연결 중"인 것은 1개만
            models.UniqueConstraint(
                fields=["user", "business_type", "organization_code"],
                condition=Q(disconnected_at__isnull=True),
                name="connection_one_active_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.business_type}:{self.organization_code}"


class Origin(models.TextChoices):
    PROVIDER = "provider", "기관 조회"
    MANUAL = "manual", "직접 등록"


class BankAccount(models.Model):
    account_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="bank_accounts")
    connection = models.ForeignKey(Connection, on_delete=models.RESTRICT, related_name="bank_accounts")
    source_account_ref_ciphertext = models.TextField("암호화된 기관 계좌 식별값", null=True, blank=True)
    source_account_key = models.CharField("계좌 중복 확인 키", max_length=128)
    masked_number = models.CharField("마스킹 계좌번호", max_length=50, null=True, blank=True)
    name = models.CharField("계좌명", max_length=100, null=True, blank=True)
    alias = models.CharField("별칭", max_length=100, null=True, blank=True)
    account_type = models.CharField("계좌 종류", max_length=30, null=True, blank=True)
    currency = models.CharField("통화", max_length=3, default="KRW")
    balance = models.BigIntegerField("잔액", null=True, blank=True)
    withdrawable_amount = models.BigIntegerField("출금 가능액", null=True, blank=True)
    is_selected = models.BooleanField("관리 대상 선택", default=False)
    budget_role = models.CharField("용도", max_length=20, null=True, blank=True)
    field_states = models.JSONField("필드별 제공 상태", default=empty_dict, blank=True)
    fetched_at = models.DateTimeField("최근 정상 조회 시각", null=True, blank=True)

    class Meta:
        db_table = "bank_account"
        verbose_name = verbose_name_plural = "계좌"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "source_account_key"],
                name="bank_account_connection_id_source_account_key_key",
            ),
        ]

    def __str__(self):
        return self.alias or self.name or self.masked_number or str(self.account_id)


class Card(models.Model):
    card_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="cards")
    connection = models.ForeignKey(
        Connection, null=True, blank=True, on_delete=models.SET_NULL, related_name="cards",
    )
    payment_account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="paying_cards",
    )
    issuer_code = models.CharField("카드사 코드", max_length=50, null=True, blank=True)
    name = models.CharField("카드명", max_length=100, null=True, blank=True)
    masked_number = models.CharField("마스킹 카드번호", max_length=50, null=True, blank=True)
    source_card_ref_ciphertext = models.TextField("암호화된 기관 카드 식별값", null=True, blank=True)
    source_card_key = models.CharField("카드 중복 확인 키", max_length=128, null=True, blank=True)
    card_type = models.CharField("신용/체크", max_length=20, null=True, blank=True)
    card_status = models.CharField("기관 제공 카드 상태", max_length=20, null=True, blank=True)
    origin = models.CharField("출처", max_length=20, choices=Origin.choices)
    is_selected = models.BooleanField("관리 대상 선택", default=False)
    fetched_at = models.DateTimeField("최근 정상 조회 시각", null=True, blank=True)

    class Meta:
        db_table = "card"
        verbose_name = verbose_name_plural = "카드"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "source_card_key"],
                name="card_connection_id_source_card_key_key",
            ),
            models.CheckConstraint(condition=Q(origin__in=Origin.values), name="card_origin_valid"),
        ]

    def __str__(self):
        return self.name or self.masked_number or str(self.card_id)


class Loan(models.Model):
    class Status(models.TextChoices):
        REPAYING = "repaying", "상환 중"
        REPAID = "repaid", "상환 완료"

    loan_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="loans")
    connection = models.ForeignKey(
        Connection, null=True, blank=True, on_delete=models.SET_NULL, related_name="loans",
    )
    linked_account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="linked_loans",
    )
    source_loan_ref_ciphertext = models.TextField("암호화된 기관 대출 식별값", null=True, blank=True)
    source_loan_key = models.CharField("대출 중복 확인 키", max_length=128, null=True, blank=True)
    origin = models.CharField("출처", max_length=20, choices=Origin.choices)
    status = models.CharField("상환 상태", max_length=20, choices=Status.choices, default=Status.REPAYING)
    lender_name = models.CharField("대출 기관명", max_length=100, null=True, blank=True)
    product_name = models.CharField("대출 상품명", max_length=100, null=True, blank=True)
    reported_principal = models.BigIntegerField("기관 조회 원금", null=True, blank=True)
    reported_balance = models.BigIntegerField("기관 조회 잔액", null=True, blank=True)
    reported_rate = models.DecimalField("기관 조회 연 이율", max_digits=8, decimal_places=5, null=True, blank=True)
    manual_balance = models.BigIntegerField("사용자 보완 잔액", null=True, blank=True)
    manual_rate = models.DecimalField("사용자 보완 연 이율", max_digits=8, decimal_places=5, null=True, blank=True)
    maturity_date = models.DateField("만기일", null=True, blank=True)
    reported_payment_date = models.DateField("기관 제공 납부일자", null=True, blank=True)
    repayment_method = models.CharField("상환 방식", max_length=50, null=True, blank=True)
    monthly_payment = models.BigIntegerField("월 상환액", null=True, blank=True)
    payment_day = models.SmallIntegerField("매월 납부일", null=True, blank=True)
    field_states = models.JSONField("항목별 출처·확정 상태", default=empty_dict, blank=True)
    confirmed_at = models.DateTimeField("사용자 확인 시각", null=True, blank=True)
    fetched_at = models.DateTimeField("기관 조회 시각", null=True, blank=True)

    class Meta:
        db_table = "loan"
        verbose_name = verbose_name_plural = "대출"
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "source_loan_key"],
                name="loan_connection_id_source_loan_key_key",
            ),
            models.CheckConstraint(condition=Q(status__in=["repaying", "repaid"]), name="loan_status_valid"),
            models.CheckConstraint(condition=Q(origin__in=Origin.values), name="loan_origin_valid"),
        ]

    def __str__(self):
        return self.product_name or self.lender_name or str(self.loan_id)


class SyncRun(models.Model):
    """기관 조회 1회 기록. connection_id는 '그때' 조회한 연결 (재연결해도 바꾸지 않음)"""

    sync_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="sync_runs")
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE, related_name="sync_runs")
    account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="sync_runs",
    )
    card = models.ForeignKey(Card, null=True, blank=True, on_delete=models.SET_NULL, related_name="sync_runs")
    loan = models.ForeignKey(Loan, null=True, blank=True, on_delete=models.SET_NULL, related_name="sync_runs")
    product_code = models.CharField("API 상품 코드", max_length=50)
    requested_from = models.DateField("요청 시작일", null=True, blank=True)
    requested_to = models.DateField("요청 종료일", null=True, blank=True)
    covered_from = models.DateField("확인된 조회 시작일", null=True, blank=True)
    covered_to = models.DateField("확인된 조회 종료일", null=True, blank=True)
    status = models.CharField("조회 결과", max_length=20)  # 코드 미정
    business_code = models.CharField("CODEF 결과 코드", max_length=50, null=True, blank=True)
    error_code = models.CharField("내부 실패 코드", max_length=50, null=True, blank=True)
    started_at = models.DateTimeField("조회 시작 시각", default=timezone.now)
    completed_at = models.DateTimeField("조회 종료 시각", null=True, blank=True)

    class Meta:
        db_table = "sync_run"
        verbose_name = verbose_name_plural = "조회 기록"
        constraints = [
            # 조회 대상(계좌·카드·대출)은 최대 1개
            models.CheckConstraint(
                condition=(
                    Q(card__isnull=True, loan__isnull=True)
                    | Q(account__isnull=True, loan__isnull=True)
                    | Q(account__isnull=True, card__isnull=True)
                ),
                name="sync_run_one_target_max",
            ),
        ]


# ===================== 거래 =====================

class SourceKind(models.TextChoices):
    BANK = "bank", "계좌"
    CARD = "card", "카드"
    LOAN = "loan", "대출"
    MANUAL = "manual", "직접 입력"


class FinancialTransaction(models.Model):
    transaction_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="transactions")
    source_kind = models.CharField("원천", max_length=20, choices=SourceKind.choices)
    account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.RESTRICT, related_name="transactions",
    )
    card = models.ForeignKey(Card, null=True, blank=True, on_delete=models.RESTRICT, related_name="transactions")
    loan = models.ForeignKey(Loan, null=True, blank=True, on_delete=models.RESTRICT, related_name="transactions")
    source_key = models.CharField("거래 식별 키", max_length=128, null=True, blank=True)
    request_key = models.CharField("중복 요청 키", max_length=128, null=True, blank=True)
    occurred_on = models.DateField("거래일")
    occurred_time = models.TimeField("거래 시각", null=True, blank=True)
    amount = models.BigIntegerField("원거래 금액")  # 방향은 direction, 금액은 0 이상
    direction = models.CharField("입출금 방향", max_length=3)  # 코드 미정
    source_description = models.CharField("대표 설명", max_length=500, null=True, blank=True)
    source_detail = models.JSONField("기관 제공 설명 원문", null=True, blank=True)
    merchant_name = models.CharField("가맹점명", max_length=200, null=True, blank=True)
    merchant_category = models.CharField("가맹점 업종", max_length=100, null=True, blank=True)
    installment_months = models.SmallIntegerField("할부 개월", null=True, blank=True)  # 일시불 0
    is_canceled = models.BooleanField("승인 취소 여부", default=False)
    canceled_amount = models.BigIntegerField("확인된 누적 취소액", null=True, blank=True)
    balance_after = models.BigIntegerField("거래 후 잔액", null=True, blank=True)
    reported_principal = models.BigIntegerField("원금 부분", null=True, blank=True)
    reported_interest = models.BigIntegerField("이자 부분", null=True, blank=True)
    approval_no = models.CharField("카드 승인번호", max_length=50, null=True, blank=True)
    transaction_type = models.CharField("거래 유형", max_length=30)  # 코드 미정
    suggested_category = models.ForeignKey(
        "budgets.Category", null=True, blank=True, on_delete=models.RESTRICT,
        db_column="suggested_category", related_name="+", verbose_name="모델 제안 카테고리",
    )
    suggestion_source = models.CharField("제안 출처", max_length=20, null=True, blank=True)
    suggestion_confidence = models.DecimalField(
        "제안 신뢰도", max_digits=5, decimal_places=4, null=True, blank=True,
    )
    # 확인 필요 거래의 후보 카테고리 (확신도 높은 순). 예: [{"category": "FOOD", "confidence": 0.41}, ...]
    # 1순위 후보는 suggested_category 와 같다. JSON 안의 코드는 FK 검사가 안 되므로 저장할 때 category 테이블에 있는지 확인한다
    suggestion_candidates = models.JSONField("후보 카테고리 목록", null=True, blank=True)
    suggestion_reason = models.CharField("확인 필요 이유", max_length=300, null=True, blank=True)
    confirmed_category = models.ForeignKey(
        "budgets.Category", on_delete=models.RESTRICT,
        db_column="confirmed_category", related_name="+", verbose_name="적용 카테고리",
    )
    category_detail = models.CharField("세부 분류", max_length=30, null=True, blank=True)  # subscription
    classification_source = models.CharField("분류 출처", max_length=20, null=True, blank=True)
    confirmation_state = models.CharField("확인 상태", max_length=20)  # 코드 미정
    field_states = models.JSONField("필드별 누락·근거", default=empty_dict, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        db_table = "financial_transaction"
        verbose_name = verbose_name_plural = "거래"
        indexes = [
            # 같은 가맹점의 '사용자가 확정한 최근 거래'를 빨리 찾기 (finance/merchant_rules.py)
            models.Index(fields=["user", "merchant_name", "-occurred_on"], name="ft_user_merchant_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "request_key"], name="financial_transaction_user_id_request_key_key",
            ),
            models.UniqueConstraint(
                fields=["account", "source_key"], name="financial_transaction_account_id_source_key_key",
            ),
            models.UniqueConstraint(
                fields=["card", "source_key"], name="financial_transaction_card_id_source_key_key",
            ),
            models.UniqueConstraint(
                fields=["loan", "source_key"], name="financial_transaction_loan_id_source_key_key",
            ),
            # 원천 종류와 원천 FK 짝: bank면 계좌만, card면 카드만, loan이면 대출만, manual이면 셋 다 비움
            models.CheckConstraint(
                condition=(
                    Q(source_kind="bank", account__isnull=False, card__isnull=True, loan__isnull=True)
                    | Q(source_kind="card", account__isnull=True, card__isnull=False, loan__isnull=True)
                    | Q(source_kind="loan", account__isnull=True, card__isnull=True, loan__isnull=False)
                    | Q(source_kind="manual", account__isnull=True, card__isnull=True, loan__isnull=True)
                ),
                name="ft_source_kind_matches_fk",
            ),
            models.CheckConstraint(condition=Q(amount__gte=0), name="ft_amount_gte_0"),
            models.CheckConstraint(
                condition=Q(canceled_amount__isnull=True)
                | Q(canceled_amount__gte=0, canceled_amount__lte=F("amount")),
                name="ft_canceled_amount_range",
            ),
            models.CheckConstraint(
                condition=Q(suggestion_confidence__isnull=True)
                | Q(suggestion_confidence__gte=0, suggestion_confidence__lte=1),
                name="ft_confidence_0_1",
            ),
        ]

    def __str__(self):
        return f"{self.occurred_on} {self.amount} {self.source_description or ''}".strip()


class TransactionLink(models.Model):
    class Relation(models.TextChoices):
        SAME_PURCHASE = "SAME_PURCHASE", "같은 구매"
        OWN_TRANSFER = "OWN_TRANSFER", "내 계좌 간 이체"
        CANCELLATION = "CANCELLATION", "취소·환불"
        LOAN_REPAYMENT = "LOAN_REPAYMENT", "대출 상환"

    link_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="transaction_links")
    from_transaction = models.ForeignKey(
        FinancialTransaction, on_delete=models.CASCADE, related_name="links_from",
    )
    to_transaction = models.ForeignKey(
        FinancialTransaction, on_delete=models.CASCADE, related_name="links_to",
    )
    relation_type = models.CharField("관계", max_length=30, choices=Relation.choices)
    matched_amount = models.BigIntegerField("연결 확인 금액")
    confirmation_state = models.CharField("확정 상태", max_length=20)  # 코드 미정
    confirmed_at = models.DateTimeField("확인 시각", null=True, blank=True)

    class Meta:
        db_table = "transaction_link"
        verbose_name = verbose_name_plural = "거래 연결"
        constraints = [
            models.UniqueConstraint(
                fields=["from_transaction", "to_transaction", "relation_type"],
                name="transaction_link_from_to_relation_key",
            ),
            # 자기 자신과 연결 금지 (A→B, B→A 역방향 중복은 서비스에서 막기)
            models.CheckConstraint(
                condition=~Q(from_transaction=F("to_transaction")), name="tl_not_self",
            ),
            models.CheckConstraint(
                condition=Q(relation_type__in=["SAME_PURCHASE", "OWN_TRANSFER", "CANCELLATION", "LOAN_REPAYMENT"]), name="tl_relation_valid",
            ),
        ]


# ===================== 카드 청구 · 고정지출 · 납부 =====================

class CardBill(models.Model):
    bill_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="card_bills")
    connection = models.ForeignKey(
        Connection, null=True, blank=True, on_delete=models.SET_NULL, related_name="card_bills",
    )
    card = models.ForeignKey(Card, null=True, blank=True, on_delete=models.SET_NULL, related_name="bills")
    payment_account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="card_bills",
    )
    issuer_code = models.CharField("카드사 코드", max_length=50)
    statement_key = models.CharField("청구서 식별 근거", max_length=128, null=True, blank=True)
    scope_type = models.CharField("범위(카드사 전체/단일 카드)", max_length=20)  # 코드 미정
    billing_month = models.DateField("청구 월")
    total_amount = models.BigIntegerField("청구 총액")
    payment_due_date = models.DateField("결제예정일", null=True, blank=True)
    withdrawal_due_date = models.DateField("출금예정일", null=True, blank=True)
    source_type = models.CharField("조회/직접 입력", max_length=20)  # 코드 미정
    confirmation_state = models.CharField("확정 상태", max_length=20)  # 코드 미정
    field_states = models.JSONField("필드별 제공 상태", default=empty_dict, blank=True)

    class Meta:
        db_table = "card_bill"
        verbose_name = verbose_name_plural = "카드 청구서"
        constraints = [
            # 재연결하면 connection_id가 바뀌므로, 재연결 시 기존 청구서 대조는 서비스에서 (계좌·카드 재연결 규칙과 같이)
            models.UniqueConstraint(
                fields=["connection", "statement_key"], name="card_bill_connection_id_statement_key_key",
            ),
        ]

    def __str__(self):
        return f"{self.issuer_code} {self.billing_month:%Y-%m}"


class CardBillItem(models.Model):
    class ItemType(models.TextChoices):
        ONE_TIME = "one_time", "일시불"
        INSTALLMENT = "installment", "할부"
        FEE = "fee", "수수료"
        INTEREST = "interest", "이자"
        ADJUSTMENT = "adjustment", "조정"
        UNKNOWN = "unknown", "미확인"

    bill_item_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="card_bill_items")
    bill = models.ForeignKey(CardBill, on_delete=models.CASCADE, related_name="items")
    card = models.ForeignKey(Card, null=True, blank=True, on_delete=models.SET_NULL, related_name="bill_items")
    source_item_key = models.CharField("원천 항목 키", max_length=128, null=True, blank=True)
    item_type = models.CharField("항목 종류", max_length=20, choices=ItemType.choices)
    merchant_name = models.CharField("가맹점·항목명", max_length=200, null=True, blank=True)
    original_purchase_amount = models.BigIntegerField("전체 구매 금액", null=True, blank=True)
    billed_amount = models.BigIntegerField("이번 회차 청구 금액", null=True, blank=True)  # 미제공 NULL, 확인된 0원은 0
    installment_months = models.SmallIntegerField("할부 총회차", null=True, blank=True)
    installment_round = models.SmallIntegerField("이번 회차", null=True, blank=True)
    purchased_on = models.DateField("원 구매일", null=True, blank=True)
    field_states = models.JSONField("필드별 상태", default=empty_dict, blank=True)
    fetched_at = models.DateTimeField("마지막 정상 조회 시각", null=True, blank=True)

    class Meta:
        db_table = "card_bill_item"
        verbose_name = verbose_name_plural = "카드 청구 항목"
        constraints = [
            models.UniqueConstraint(fields=["bill", "source_item_key"], name="card_bill_item_bill_source_key"),
            models.CheckConstraint(condition=Q(item_type__in=["one_time", "installment", "fee", "interest", "adjustment", "unknown"]), name="cbi_item_type_valid"),
            models.CheckConstraint(
                condition=Q(installment_months__isnull=True) | Q(installment_months__gt=0),
                name="cbi_months_positive",
            ),
            models.CheckConstraint(
                condition=Q(installment_round__isnull=True) | Q(installment_round__gt=0),
                name="cbi_round_positive",
            ),
            models.CheckConstraint(
                condition=Q(installment_round__isnull=True)
                | Q(installment_months__isnull=True)
                | Q(installment_round__lte=F("installment_months")),
                name="cbi_round_lte_months",
            ),
        ]


class RecurringExpense(models.Model):
    """고정지출. 화면에서 '삭제'해도 행은 지우지 않고 status를 중지로 바꾼다

    10-21 C-04: 고정지출 = 매달 금액·날짜가 정해져 피할 수 없는 돈. 매월만 받는다
    (1년에 한 번 = 비정기지출 준비금 BUD-011, 매주·매일 사는 것 = 생활비 카테고리 예산)
    상태 흐름: 자동 발견 → suggested ─맞아요→ confirmed ─그만 나가요→ stopped
                                    └아니에요→ ignored (같은 후보를 다시 묻지 않으려고 남김)
               직접 추가 → 바로 confirmed.  예산에는 confirmed 만 들어간다 (TXN-008)
    """

    class Status(models.TextChoices):
        SUGGESTED = "suggested", "후보"
        CONFIRMED = "confirmed", "확정"
        STOPPED = "stopped", "중지"
        IGNORED = "ignored", "무시"

    class SourceType(models.TextChoices):
        MANUAL = "manual", "직접 등록"
        DETECTED = "detected", "반복 탐지"   # 후보를 고쳐서 확정해도 detected 유지

    recurring_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="recurring_expenses")
    payment_account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="recurring_expenses",
    )
    payment_card = models.ForeignKey(
        Card, null=True, blank=True, on_delete=models.SET_NULL, related_name="recurring_expenses",
    )
    name = models.CharField("이름", max_length=100)
    # 10-21 C-03: 1,000원 ~ 1억 원 (0원·오타 방지). 합계가 월급보다 큰지는 예산 만들 때 안내(BUD-001)
    amount = models.BigIntegerField("기준 지출액")
    # 10-21 C-03: 비워도 됨(출금일 모름 → '출금일 확인 필요'). 그 달에 없는 날(2월 31일 등)은 그 달 말일 (BUD-004)
    payment_day = models.SmallIntegerField("매월 납부일", null=True, blank=True)
    category = models.ForeignKey(
        "budgets.Category", on_delete=models.RESTRICT, db_column="category_code",
        related_name="+", verbose_name="카테고리",
    )
    # category_detail(세부 분류) 칸은 10-21 삭제 — 화면에서 입력하지 않고 1차에 쓰는 곳이 없어서 (구독 모아보기가 생기면 다시 검토)
    status = models.CharField("상태", max_length=20, choices=Status.choices)
    source_type = models.CharField("등록 출처", max_length=20, choices=SourceType.choices)
    evidence = models.JSONField("확정 근거 거래 ID", default=empty_list, blank=True)
    confirmed_at = models.DateTimeField("사용자 확인 시각", null=True, blank=True)
    # 10-21: 후보를 '최근에 찾은 순'으로 보여 주려고 추가 (직접 등록은 등록 시각)
    created_at = models.DateTimeField("발견·등록 시각", auto_now_add=True)

    class Meta:
        db_table = "recurring_expense"
        verbose_name = verbose_name_plural = "고정지출"
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=1_000, amount__lte=100_000_000), name="re_amount_1000_100m",
            ),
            models.CheckConstraint(
                condition=Q(payment_day__isnull=True) | Q(payment_day__gte=1, payment_day__lte=31),
                name="re_payment_day_1_31",
            ),
            models.CheckConstraint(condition=Q(status__in=["suggested", "confirmed", "stopped", "ignored"]), name="re_status_valid"),
            models.CheckConstraint(condition=Q(source_type__in=["manual", "detected"]), name="re_source_type_valid"),
        ]

    def __str__(self):
        return self.name


class PaymentDue(models.Model):
    """납부 의무(월세·카드값·대출 상환 등) 1회차"""

    class ObligationType(models.TextChoices):
        CARD_BILL = "card_bill", "카드 청구"
        RECURRING = "recurring", "고정지출"
        LOAN = "loan", "대출"
        MANUAL = "manual", "직접 입력"

    class ObligationState(models.TextChoices):
        ACTIVE = "active", "유효"
        CANCELED = "canceled", "취소(납부 불필요)"

    class PaymentState(models.TextChoices):
        UNPAID = "unpaid", "미납"
        PARTIAL = "partial", "부분납부"
        PAID = "paid", "완납"
        NEEDS_REVIEW = "needs_review", "확인필요"

    due_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="payment_dues")
    obligation_type = models.CharField("의무 종류", max_length=20, choices=ObligationType.choices)
    # 청구서 1장 = 납부 의무 1개 (UNIQUE) → OneToOne
    bill = models.OneToOneField(
        CardBill, null=True, blank=True, on_delete=models.RESTRICT, related_name="payment_due",
    )
    recurring = models.ForeignKey(
        RecurringExpense, null=True, blank=True, on_delete=models.RESTRICT, related_name="payment_dues",
    )
    loan = models.ForeignKey(Loan, null=True, blank=True, on_delete=models.RESTRICT, related_name="payment_dues")
    reserve = models.ForeignKey(
        "budgets.ReservePlan", null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_dues",
    )
    payment_account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="payment_dues",
    )
    occurrence_key = models.CharField("회차 중복 방지 키", max_length=128)
    due_date = models.DateField("납부 예정일")
    confirmed_amount = models.BigIntegerField("확정 의무 금액")
    source_type = models.CharField("조회/직접/추정", max_length=20)  # 코드 미정
    confirmation_state = models.CharField("확인 상태", max_length=20)  # 코드 미정
    obligation_state = models.CharField(
        "의무 유효 상태", max_length=20, choices=ObligationState.choices, default=ObligationState.ACTIVE,
    )
    canceled_at = models.DateTimeField("의무 취소 확인 시각", null=True, blank=True)
    paid_amount = models.BigIntegerField("납부액 합계(캐시)", default=0)
    payment_state = models.CharField(
        "납부 현황", max_length=20, choices=PaymentState.choices, default=PaymentState.UNPAID,
    )
    matched_at = models.DateTimeField("최근 매칭 확인 시각", null=True, blank=True)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "payment_due"
        verbose_name = verbose_name_plural = "납부 의무"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "occurrence_key"], name="payment_due_user_id_occurrence_key_key",
            ),
            # 의무 종류와 원천 FK 짝: card_bill면 청구서만, recurring면 고정지출만, loan이면 대출만, manual이면 셋 다 비움
            models.CheckConstraint(
                condition=(
                    Q(obligation_type="card_bill", bill__isnull=False, recurring__isnull=True, loan__isnull=True)
                    | Q(obligation_type="recurring", bill__isnull=True, recurring__isnull=False, loan__isnull=True)
                    | Q(obligation_type="loan", bill__isnull=True, recurring__isnull=True, loan__isnull=False)
                    | Q(obligation_type="manual", bill__isnull=True, recurring__isnull=True, loan__isnull=True)
                ),
                name="pd_obligation_type_matches_fk",
            ),
            # active면 취소 시각 없음, canceled면 취소 시각 필수
            models.CheckConstraint(
                condition=Q(obligation_state="active", canceled_at__isnull=True)
                | Q(obligation_state="canceled", canceled_at__isnull=False),
                name="pd_obligation_state_canceled_at",
            ),
            models.CheckConstraint(
                condition=Q(payment_state__in=["unpaid", "partial", "paid", "needs_review"]), name="pd_payment_state_valid",
            ),
            models.CheckConstraint(condition=Q(paid_amount__gte=0), name="pd_paid_amount_gte_0"),
        ]

    def __str__(self):
        return f"{self.obligation_type} {self.due_date} {self.confirmed_amount}"


class PaymentMatch(models.Model):
    """납부 의무 ↔ 실제 납부 거래 (분할 납부면 여러 줄)"""

    match_id = uuid_pk()
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="payment_matches")
    due = models.ForeignKey(PaymentDue, on_delete=models.RESTRICT, related_name="matches")
    transaction = models.ForeignKey(
        FinancialTransaction, on_delete=models.RESTRICT, related_name="payment_matches",
    )
    matched_amount = models.BigIntegerField("인정 금액")
    matched_at = models.DateTimeField("매칭 확인 시각", default=timezone.now)
    created_at = models.DateTimeField("생성 시각", auto_now_add=True)

    class Meta:
        db_table = "payment_match"
        verbose_name = verbose_name_plural = "납부 매칭"
        constraints = [
            models.UniqueConstraint(
                fields=["due", "transaction"], name="payment_match_due_id_transaction_id_key",
            ),
            models.CheckConstraint(condition=Q(matched_amount__gt=0), name="pm_matched_amount_positive"),
        ]
