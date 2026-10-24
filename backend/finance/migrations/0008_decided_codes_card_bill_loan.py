# 2026-10-24: 정해진 코드를 DB에 반영 (H-07 · 결정 Z · AA① · P · Y)
#  - 연결 · 계좌 · 거래 코드 choices + CHECK, 연결 해제 상태 ↔ 해제 시각
#  - 카드 청구서: 범위 · 확정 상태를 비울 수 있게(모르면 null), 상세 항목 상태 · 조회 시각 칸 추가
#  - 대출: 연체 여부 칸 추가, 상환 방식 코드 · 월 상환액 · 납부일 · 직접 등록 잔액 · 금리 범위
# 로컬 DB에 규칙에 안 맞는 테스트 행이 있으면 실패한다 → 그 행을 지우고 다시

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("budgets", "0004_budget_cycle_status"),
        ("finance", "0007_recurring_saving"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="cardbill",
            name="fetched_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="최근 정상 조회 시각"
            ),
        ),
        migrations.AddField(
            model_name="cardbill",
            name="items_state",
            field=models.CharField(
                blank=True,
                choices=[
                    ("complete", "항목 전체 확인"),
                    ("partial", "일부만 확인"),
                    ("unavailable", "항목 내역 없음"),
                ],
                max_length=20,
                null=True,
                verbose_name="상세 항목 확보 상태",
            ),
        ),
        migrations.AddField(
            model_name="loan",
            name="overdue",
            field=models.BooleanField(blank=True, null=True, verbose_name="연체 여부"),
        ),
        migrations.AlterField(
            model_name="bankaccount",
            name="account_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("demand", "입출금"),
                    ("deposit", "예적금"),
                    ("other", "기타"),
                ],
                max_length=30,
                null=True,
                verbose_name="계좌 종류",
            ),
        ),
        migrations.AlterField(
            model_name="cardbill",
            name="confirmation_state",
            field=models.CharField(
                blank=True,
                choices=[
                    ("estimated", "예상 청구"),
                    ("confirmed", "확정 청구"),
                    ("unknown", "확정 여부 확인 불가"),
                ],
                max_length=20,
                null=True,
                verbose_name="확정 상태",
            ),
        ),
        migrations.AlterField(
            model_name="cardbill",
            name="scope_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("issuer", "카드사 통합 청구"),
                    ("card", "카드 한 장"),
                    ("unknown", "범위 확인 불가"),
                ],
                max_length=20,
                null=True,
                verbose_name="범위(카드사 전체/단일 카드)",
            ),
        ),
        migrations.AlterField(
            model_name="connection",
            name="business_type",
            field=models.CharField(
                choices=[("bank", "은행"), ("card", "카드사"), ("loan", "대출 기관")],
                max_length=20,
                verbose_name="업권",
            ),
        ),
        migrations.AlterField(
            model_name="connection",
            name="status",
            field=models.CharField(
                choices=[
                    ("connected", "연결됨"),
                    ("relogin", "다시 로그인 필요"),
                    ("disconnected", "연결 해제"),
                ],
                max_length=30,
                verbose_name="연결 상태",
            ),
        ),
        migrations.AlterField(
            model_name="financialtransaction",
            name="classification_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("user", "사용자"),
                    ("rule", "규칙"),
                    ("ml", "ML"),
                    ("llm", "LLM"),
                ],
                max_length=20,
                null=True,
                verbose_name="분류 출처",
            ),
        ),
        migrations.AlterField(
            model_name="financialtransaction",
            name="confirmation_state",
            field=models.CharField(
                choices=[
                    ("needs_review", "확인 필요"),
                    ("suggested", "규칙·ML이 정함"),
                    ("confirmed", "사용자 확정"),
                ],
                max_length=20,
                verbose_name="확인 상태",
            ),
        ),
        migrations.AlterField(
            model_name="financialtransaction",
            name="direction",
            field=models.CharField(
                choices=[("in", "입금"), ("out", "출금")],
                max_length=3,
                verbose_name="입출금 방향",
            ),
        ),
        migrations.AlterField(
            model_name="financialtransaction",
            name="transaction_type",
            field=models.CharField(
                choices=[
                    ("spending", "소비"),
                    ("transfer", "남에게 보낸 이체"),
                    ("own_transfer", "내 계좌 간 이체"),
                    ("card_payment", "카드값"),
                    ("saving", "저축"),
                    ("loan_repayment", "대출 상환"),
                    ("income", "입금"),
                ],
                max_length=30,
                verbose_name="거래 유형",
            ),
        ),
        migrations.AlterField(
            model_name="loan",
            name="repayment_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("equal_payment", "원리금균등"),
                    ("equal_principal", "원금균등"),
                    ("bullet", "만기일시"),
                    ("unknown", "잘 모르겠어요"),
                ],
                max_length=50,
                null=True,
                verbose_name="상환 방식",
            ),
        ),
        migrations.AddConstraint(
            model_name="bankaccount",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("account_type__isnull", True),
                    ("account_type__in", ["demand", "deposit", "other"]),
                    _connector="OR",
                ),
                name="ba_account_type_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="cardbill",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("scope_type__isnull", True),
                    ("scope_type__in", ["issuer", "card", "unknown"]),
                    _connector="OR",
                ),
                name="cb_scope_type_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="cardbill",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("confirmation_state__isnull", True),
                    ("confirmation_state__in", ["estimated", "confirmed", "unknown"]),
                    _connector="OR",
                ),
                name="cb_confirmation_state_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="cardbill",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("items_state__isnull", True),
                    ("items_state__in", ["complete", "partial", "unavailable"]),
                    _connector="OR",
                ),
                name="cb_items_state_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="connection",
            constraint=models.CheckConstraint(
                condition=models.Q(("business_type__in", ["bank", "card", "loan"])),
                name="conn_business_type_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="connection",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("disconnected_at__isnull", False), ("status", "disconnected")
                    ),
                    models.Q(
                        ("disconnected_at__isnull", True),
                        ("status__in", ["connected", "relogin"]),
                    ),
                    _connector="OR",
                ),
                name="conn_status_disconnected_at",
            ),
        ),
        migrations.AddConstraint(
            model_name="financialtransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(("direction__in", ["in", "out"])),
                name="ft_direction_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="financialtransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "transaction_type__in",
                        [
                            "spending",
                            "transfer",
                            "own_transfer",
                            "card_payment",
                            "saving",
                            "loan_repayment",
                            "income",
                        ],
                    )
                ),
                name="ft_transaction_type_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="financialtransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "confirmation_state__in",
                        ["needs_review", "suggested", "confirmed"],
                    )
                ),
                name="ft_confirmation_state_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="financialtransaction",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("classification_source__isnull", True),
                    ("classification_source__in", ["user", "rule", "ml", "llm"]),
                    _connector="OR",
                ),
                name="ft_classification_source_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("repayment_method__isnull", True),
                    (
                        "repayment_method__in",
                        ["equal_payment", "equal_principal", "bullet", "unknown"],
                    ),
                    _connector="OR",
                ),
                name="loan_repayment_method_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("monthly_payment__isnull", True),
                    models.Q(
                        ("monthly_payment__gte", 1), ("monthly_payment__lte", 100000000)
                    ),
                    _connector="OR",
                ),
                name="loan_monthly_payment_1_100m",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("payment_day__isnull", True),
                    models.Q(("payment_day__gte", 1), ("payment_day__lte", 31)),
                    _connector="OR",
                ),
                name="loan_payment_day_1_31",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("manual_balance__isnull", True),
                    models.Q(
                        ("manual_balance__gte", 1), ("manual_balance__lte", 1000000000)
                    ),
                    _connector="OR",
                ),
                name="loan_manual_balance_1_1b",
            ),
        ),
        migrations.AddConstraint(
            model_name="loan",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("manual_rate__isnull", True),
                    models.Q(("manual_rate__gte", 0), ("manual_rate__lte", 30)),
                    _connector="OR",
                ),
                name="loan_manual_rate_0_30",
            ),
        ),
    ]
