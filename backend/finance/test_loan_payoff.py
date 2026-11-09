"""R19 직접 등록 대출 정정 · 전체 완납 · 완납 되돌리기 (finance/api_loans.py)"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from budgets.models import Budget, BudgetCycle
from budgets.protect import fixed_breakdown
from users.models import Consent, FinancialProfile

from . import loan_payments
from .api_loans import sync_loan_due
from .models import (BankAccount, Connection, FinancialTransaction as Tx, Loan, PaymentDue, PaymentMatch)

# 실제 CODEF·LLM 호출 차단: 모듈을 불러올 때(테스트 DB 생성·시드 전) 시작한다
_blocks = [
    patch("finance.codef._request", side_effect=AssertionError("CODEF 호출 금지")),
    patch("chat.llm.openai_rest.ResponsesClient.__init__", side_effect=AssertionError("LLM 호출 금지")),
]
for _block in _blocks:
    _block.start()


def tearDownModule():
    for block in _blocks:
        block.stop()


TODAY = date(2026, 11, 12)  # 급여 주기 10/25 ~ 11/25


def loan_url(loan, tail=""):
    return f"/api/finance/loans/{loan.pk}/{tail}"


class Base:
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=TODAY)
        self.today = clock.start()
        self.addCleanup(clock.stop)
        self.user = get_user_model().objects.create_user(email="loan-payoff@example.invalid")
        FinancialProfile.objects.create(user=self.user, monthly_take_home_income=2_800_000, payday=25)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="pay",
                                                  account_type="demand")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get("/api/home/").status_code, 200)
        self.loan = self.make_loan(payment_day=20)  # 11/20 — 오늘 이후 회차
        self.future = PaymentDue.objects.get(loan=self.loan)

    def make_loan(self, origin="manual", **values):
        fields = {"lender_name": "한국장학재단", "product_name": "학자금", "manual_balance": 5_000_000,
                  "monthly_payment": 300_000, "payment_day": 10, **values}
        loan = Loan.objects.create(user=self.user, origin=origin, status="repaying", **fields)
        with transaction.atomic():
            sync_loan_due(loan)
        return loan

    def tx(self, amount=300_000, day=date(2026, 11, 10)):
        return Tx.objects.create(
            user=self.user, source_kind="bank", account=self.account, occurred_on=day, amount=amount,
            direction="out", source_description="자동이체", transaction_type="transfer",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
        )

    def link(self, due, tx, settlement="full"):
        response = self.client.post(f"/api/finance/loan-dues/{due.pk}/payments/",
                                    {"transactionId": str(tx.pk), "settlement": settlement}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def patch_loan(self, loan, body):
        return self.client.patch(loan_url(loan), body, format="json")

    def payoff(self, loan=None):
        return self.client.post(loan_url(loan or self.loan, "payoff/"), {}, format="json")

    def cancel(self, reported_at, loan=None):
        return self.client.post(loan_url(loan or self.loan, "payoff/cancel/"), {"reportedAt": reported_at}, format="json")

    def active_dues(self, loan=None):
        return list(PaymentDue.objects.filter(loan=loan or self.loan, obligation_state="active")
                    .order_by("due_date").values_list("pk", "due_date", "payment_state", "paid_amount"))


class ManualLoanEditTests(Base, TestCase):
    def test_edit_registration_fields(self):
        before = Tx.objects.count()
        body = {"lenderName": "국민은행", "productName": "전세자금", "manualBalance": 0, "manualRate": None,
                "maturityDate": "2030-02-28"}
        response = self.patch_loan(self.loan, body)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual({k: data[k] for k in body}, body)
        self.assertEqual(data["status"], "repaying")  # 잔액 0원 정정 ≠ 전체 완납
        self.assertIsNone(data["payoff"])
        self.assertEqual(Tx.objects.count(), before)  # 가짜 납부 거래 없음
        self.assertEqual(len(self.active_dues()), 1)

    def test_invalid_values_rejected_and_nothing_saved(self):
        cases = [
            {"manualBalance": -1}, {"manualBalance": 1_000_000_001}, {"manualBalance": "100"},
            {"manualBalance": None}, {"manualBalance": 1.5}, {"lenderName": ""}, {"lenderName": None},
            {"productName": "가" * 51}, {"manualRate": 31}, {"maturityDate": "2026-02-30"},
            {"maturityDate": "20261101"}, {"status": "repaid"}, {"origin": "provider"},
            {"lenderName": "새 이름", "manualBalance": -5},  # 일부만 틀려도 전체 미저장
        ]
        for body in cases:
            response = self.patch_loan(self.loan, body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(response.json()["error"]["code"], "invalid")
        self.loan.refresh_from_db()
        self.assertEqual((self.loan.lender_name, self.loan.manual_balance, self.loan.status),
                         ("한국장학재단", 5_000_000, "repaying"))

    def test_provider_loan_is_protected(self):
        Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="loan",
                               document_version="v1", document_hash="test")
        provider = self.make_loan(origin="provider", manual_balance=None)
        response = self.patch_loan(provider, {"lenderName": "바꿈", "manualBalance": 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()["error"]["fields"]), {"lenderName", "manualBalance"})
        self.assertEqual(self.patch_loan(provider, {"paymentDay": 15}).status_code, 200)  # 기존 보완 칸은 그대로
        for response in (self.payoff(provider), self.cancel("x", provider)):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "not_ready")
        provider.refresh_from_db()
        self.assertEqual((provider.status, provider.lender_name), ("repaying", "한국장학재단"))

    def test_other_users_loan_is_404(self):
        other = get_user_model().objects.create_user(email="loan-payoff-other@example.invalid")
        client = APIClient()
        client.force_authenticate(other)
        self.assertEqual(client.patch(loan_url(self.loan), {"lenderName": "x"}, format="json").status_code, 404)
        self.assertEqual(client.post(loan_url(self.loan, "payoff/"), {}, format="json").status_code, 404)
        self.assertEqual(client.post(loan_url(self.loan, "payoff/cancel/"), {"reportedAt": "x"},
                                     format="json").status_code, 404)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "repaying")

    def test_edit_keeps_due_ids_payments_and_matches(self):
        past = self.make_loan()  # 11/10 회차
        due = PaymentDue.objects.get(loan=past)
        self.link(due, self.tx(amount=250_000), "partial")
        match = PaymentMatch.objects.get(due=due)
        response = self.patch_loan(past, {"lenderName": "새 기관", "manualBalance": 100, "monthlyPayment": 200_000,
                                          "paymentDay": 15})
        self.assertEqual(response.status_code, 200, response.content)
        due.refresh_from_db()
        self.assertEqual((due.payment_state, due.paid_amount, due.confirmed_amount), ("partial", 250_000, 300_000))
        self.assertEqual(list(PaymentMatch.objects.filter(due=due)), [match])
        self.assertEqual(PaymentDue.objects.filter(loan=past).count(), 1)  # 납부일 바꿔도 같은 의무 중복 없음

    def test_full_due_payment_is_not_loan_payoff(self):
        past = self.make_loan()
        self.link(PaymentDue.objects.get(loan=past), self.tx(), "full")
        past.refresh_from_db()
        self.assertEqual((past.status, past.manual_balance), ("repaying", 5_000_000))


class PayoffTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        cycle = BudgetCycle.objects.get(user=self.user, status="active")
        self.approved = Budget.objects.create(user=self.user, cycle=cycle, version_no=1, status="approved",
                                              funding_amount=1_500_000)
        self.draft = Budget.objects.create(user=self.user, cycle=cycle, version_no=2, status="draft",
                                           funding_amount=1_500_000)

    def test_payoff_cancels_only_future_unprotected_dues(self):
        past = self.make_loan()  # 11/10 — 오늘 이전, 확인 필요
        past_due = PaymentDue.objects.get(loan=past)
        paid_ahead = self.make_loan(payment_day=22)
        ahead = PaymentDue.objects.get(loan=paid_ahead)
        self.link(ahead, self.tx(day=date(2026, 11, 11)), "partial")  # 미리 일부 낸 미래 회차
        other = self.make_loan(lender_name="다른은행", payment_day=21)
        approved = list(Budget.objects.filter(status="approved").values())

        for loan in (self.loan, past, paid_ahead):
            response = self.payoff(loan)
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()
            self.assertEqual((data["status"], data["manualBalance"]), ("repaid", 0))
            self.assertEqual(data["payoff"]["source"], "user_report")
            self.assertEqual(data["payoff"]["previousBalance"], 5_000_000)

        self.future.refresh_from_db()
        self.assertEqual(self.future.obligation_state, "canceled")
        self.assertEqual(self.active_dues(past), [(past_due.pk, date(2026, 11, 10), "needs_review", 0)])  # 가짜 납부 없음
        ahead.refresh_from_db()
        self.assertEqual((ahead.obligation_state, ahead.payment_state), ("active", "partial"))
        self.assertEqual(len(self.active_dues(other)), 1)  # 다른 대출은 그대로
        self.assertEqual(fixed_breakdown(self.user, None, card=False)["loan"], 300_000)  # 남은 대출만 보호
        self.assertEqual(list(Budget.objects.filter(status="approved").values()), approved)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "expired")
        self.assertEqual(Loan.objects.filter(pk__in=[self.loan.pk, past.pk, paid_ahead.pk]).count(), 3)  # 삭제 안 함
        names = [item["name"] for item in self.client.get("/api/home/").json()["mustPay"]]
        self.assertEqual(names, ["다른은행 학자금"])  # 11/9: 완납한 대출의 남은 회차(지난 · 일부 낸)는 꼭 나갈 돈에서 숨김 — 회차 자체는 위처럼 active 로 남음
        reported = self.client.get("/api/finance/loans/?status=repaid").json()["items"]
        reported = next(item["payoff"]["reportedAt"] for item in reported if item["loanId"] == str(past.pk))
        self.assertEqual(self.cancel(reported, past).status_code, 200)
        names = [item["name"] for item in self.client.get("/api/home/").json()["mustPay"]]
        self.assertEqual(sorted(names), ["다른은행 학자금", "한국장학재단 학자금"])  # 완납 취소하면 다시 보임

    def test_no_new_dues_after_payoff(self):
        self.payoff()
        with transaction.atomic():
            self.assertIsNone(sync_loan_due(self.loan))  # 오래된 객체로 불러도 잠금 안에서 다시 본다
        self.client.get("/api/home/")
        self.tx(amount=300_000, day=date(2026, 11, 12))
        with transaction.atomic():
            loan_payments.match(self.user)
        self.assertEqual(self.active_dues(), [])
        self.today.return_value = date(2026, 12, 21)  # 다음 주기 (11/25 ~ 12/25)
        self.client.get("/api/home/")
        self.assertEqual(self.active_dues(), [])
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "repaid")
        self.assertEqual(self.client.get("/api/finance/loans/").json()["items"], [])
        repaid = self.client.get("/api/finance/loans/?status=repaid").json()["items"]
        self.assertEqual([item["loanId"] for item in repaid], [str(self.loan.pk)])
        self.assertEqual(self.client.get("/api/finance/loans/?status=done").status_code, 400)

    def test_repeat_payoff_keeps_first_record(self):
        first = self.payoff().json()
        second = self.payoff().json()
        self.assertEqual(second["payoff"], first["payoff"])
        self.assertEqual(second["manualBalance"], 0)
        self.assertEqual(self.payoff().status_code, 200)
        self.assertEqual(self.client.post(loan_url(self.loan, "payoff/"), {"date": "2026-11-01"},
                                          format="json").status_code, 400)  # 소급 날짜 없음

    def test_stale_patch_after_payoff_is_404(self):
        self.payoff()
        self.assertEqual(self.patch_loan(self.loan, {"manualBalance": 10}).status_code, 404)
        self.loan.refresh_from_db()
        self.assertEqual((self.loan.status, self.loan.manual_balance), ("repaid", 0))

    def test_cancel_restores_without_duplicates(self):
        due_ids = set(PaymentDue.objects.filter(loan=self.loan).values_list("pk", flat=True))
        reported = self.payoff().json()["payoff"]["reportedAt"]
        response = self.cancel(reported)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual((data["status"], data["manualBalance"], data["payoff"]), ("repaying", 5_000_000, None))
        self.assertEqual(self.active_dues(), [(self.future.pk, date(2026, 11, 20), "unpaid", 0)])  # 같은 회차를 다시 살림
        self.assertEqual(set(PaymentDue.objects.filter(loan=self.loan).values_list("pk", flat=True)), due_ids)
        self.assertEqual(self.cancel(reported).status_code, 200)  # 반복 취소
        self.assertEqual(len(self.active_dues()), 1)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.field_states["payoff"]["reportedAt"], reported)  # 근거는 지우지 않음
        self.assertIn("undoneAt", self.loan.field_states["payoff"])

    def test_stale_cancel_does_not_undo_newer_payoff(self):
        old = self.payoff().json()["payoff"]["reportedAt"]
        self.cancel(old)
        newer = self.payoff().json()["payoff"]["reportedAt"]
        response = self.cancel(old)
        self.assertEqual(response.status_code, 409)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.status, "repaid")
        self.assertEqual(self.cancel(newer).status_code, 200)
        for body in ({}, {"reportedAt": 1}, {"reportedAt": newer, "extra": 1}):
            self.assertEqual(self.client.post(loan_url(self.loan, "payoff/cancel/"), body, format="json").status_code, 400)

    def test_cancel_does_not_create_previous_cycle_dues(self):
        reported = self.payoff().json()["payoff"]["reportedAt"]
        self.today.return_value = date(2026, 12, 21)
        self.client.get("/api/home/")
        self.cancel(reported)
        # 지난 주기 11/20 회차는 되살리지 않고, 이번 주기 12/20 회차만 기존 규칙으로 준비 (지났으면 확인 필요)
        self.assertEqual([row[1:3] for row in self.active_dues()], [(date(2026, 12, 20), "needs_review")])

    def test_idempotency_key_replays_same_response(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "11111111-1111-4111-8111-111111111111"}
        first = self.client.post(loan_url(self.loan, "payoff/"), {}, format="json", **headers)
        second = self.client.post(loan_url(self.loan, "payoff/"), {}, format="json", **headers)
        self.assertEqual(first.json(), second.json())

    def test_contract(self):
        from openapi_schema_validator import OAS30Validator, oas30_format_checker
        from config.openapi import build_contract
        contract = build_contract()

        def check(body, ref):
            validator = OAS30Validator({"$ref": f"#/components/schemas/{ref}", "components": contract["components"]},
                                       format_checker=oas30_format_checker)
            self.assertEqual([e.message for e in validator.iter_errors(body)], [], ref)

        check(self.patch_loan(self.loan, {"manualBalance": 0}).json(), "Loan")
        check({"lenderName": "a", "productName": "b", "manualBalance": 0, "manualRate": None, "maturityDate": None},
              "LoanUpdateRequest")
        repaid = self.payoff().json()
        check(repaid, "Loan")
        check(self.client.get("/api/finance/loans/?status=repaid").json(), "LoanList")
        check({"reportedAt": repaid["payoff"]["reportedAt"]}, "LoanPayoffCancelRequest")
        check(self.cancel(repaid["payoff"]["reportedAt"]).json(), "Loan")


@skipUnless(connection.vendor == "postgresql", "PostgreSQL 동시 요청 검사")
class PayoffConcurrencyTests(Base, TransactionTestCase):
    serialized_rollback = True

    def send(self, args):
        barrier, method, path, body = args
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
            barrier.wait(5)
            return getattr(client, method)(path, body, format="json").status_code
        finally:
            connections.close_all()

    def run_together(self, *requests):
        barrier = Barrier(len(requests))
        with ThreadPoolExecutor(len(requests)) as pool:
            return list(pool.map(self.send, [(barrier, *r) for r in requests]))

    def test_payoff_and_edit_together_leave_no_future_due(self):
        codes = self.run_together(("post", loan_url(self.loan, "payoff/"), {}),
                                  ("patch", loan_url(self.loan), {"monthlyPayment": 200_000, "paymentDay": 24}))
        self.assertEqual(codes[0], 200)
        self.assertIn(codes[1], (200, 404))  # 완납이 먼저면 오래된 화면의 수정은 404
        self.loan.refresh_from_db()
        self.assertEqual((self.loan.status, self.loan.manual_balance), ("repaid", 0))
        self.assertEqual(self.active_dues(), [])

    def test_double_payoff_and_double_cancel(self):
        self.assertEqual(self.run_together(*[("post", loan_url(self.loan, "payoff/"), {})] * 2), [200, 200])
        self.loan.refresh_from_db()
        report = self.loan.field_states["payoff"]
        self.assertEqual(report["previous"]["manualBalance"], 5_000_000)  # 두 번째가 0원을 '이전 값'으로 덮지 않음
        body = {"reportedAt": report["reportedAt"]}
        self.assertEqual(self.run_together(*[("post", loan_url(self.loan, "payoff/cancel/"), body)] * 2), [200, 200])
        self.loan.refresh_from_db()
        self.assertEqual((self.loan.status, self.loan.manual_balance), ("repaying", 5_000_000))
        self.assertEqual(len(self.active_dues()), 1)


class MonthlyDuesPayoffTests(Base, TestCase):
    """급여일을 바꾼 회원(uses_monthly_dues=True) — 회차가 '같은 달 하나' 키로 만들어지는 경우의 완납 · 취소"""

    def setUp(self):
        super().setUp()
        FinancialProfile.objects.filter(user=self.user).update(payday_revision=1)
        self.loan = self.make_loan(lender_name="월별은행", payment_day=20)  # 11/20, 월 키
        self.future = PaymentDue.objects.get(loan=self.loan)
        self.assertTrue(self.future.occurrence_key.endswith("month:2026-11"))

    def snapshot(self, loan=None):
        return list(PaymentDue.objects.filter(loan=loan or self.loan).order_by("due_date", "pk").values_list(
            "pk", "due_date", "occurrence_key", "obligation_state", "payment_state", "paid_amount", "confirmed_amount"))

    def round_trip(self, loan=None):
        loan = loan or self.loan
        reported = self.payoff(loan).json()["payoff"]["reportedAt"]
        response = self.cancel(reported, loan)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_no_new_dues_after_payoff(self):
        self.payoff()
        self.client.get("/api/home/")
        self.tx(amount=300_000, day=date(2026, 11, 12))
        with transaction.atomic():
            self.assertIsNone(sync_loan_due(self.loan))
            loan_payments.match(self.user)
        self.today.return_value = date(2026, 12, 21)  # 다음 주기 11/25~12/25 → 월 조각 11월 · 12월
        self.client.get("/api/home/")
        self.client.get("/api/home/")
        self.assertEqual(self.active_dues(), [])
        self.assertEqual(PaymentDue.objects.filter(loan=self.loan).count(), 1)  # 12월 회차도 안 생김

    def test_cancel_restores_same_month_due_and_repeats_are_stable(self):
        before = self.snapshot()
        data = self.round_trip()
        self.assertEqual((data["status"], data["manualBalance"]), ("repaying", 5_000_000))
        self.assertEqual(self.snapshot(), before)  # 같은 회차 ID · 키로 되살림, 중복 없음
        for _ in range(3):
            self.round_trip()
            self.client.get("/api/home/")
        self.assertEqual(self.snapshot(), before)
        self.payoff()
        self.payoff()  # 중복 완납
        self.future.refresh_from_db()
        self.assertEqual(self.future.obligation_state, "canceled")

    def test_payment_records_survive_payoff_and_cancel(self):
        past = self.make_loan(lender_name="월별은행", product_name="일부", payment_day=10)  # 11/10, 지난 회차
        due = PaymentDue.objects.get(loan=past)
        tx = self.tx(amount=120_000)
        self.link(due, tx, "partial")
        match = PaymentMatch.objects.get(due=due)
        tx.refresh_from_db()
        tx_before = (tx.transaction_type, tx.confirmation_state, tx.field_states)
        due_before = self.snapshot(past)
        self.assertEqual(due_before[0][3:6], ("active", "partial", 120_000))

        for _ in range(2):
            self.payoff(past)
            self.assertEqual(self.snapshot(past), due_before)  # 완납해도 일부 낸 회차는 그대로
            self.client.get("/api/home/")
            reported = self.client.get("/api/finance/loans/?status=repaid").json()["items"]
            reported = next(item for item in reported if item["loanId"] == str(past.pk))["payoff"]["reportedAt"]
            self.assertEqual(self.cancel(reported, past).status_code, 200)
            self.assertEqual(self.snapshot(past), due_before)  # 취소해도 다시 만들거나 초기화하지 않음
        self.assertEqual(list(PaymentMatch.objects.filter(due=due)), [match])
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmation_state, tx.field_states), tx_before)

    def test_cancel_in_next_cycle_prepares_only_this_month(self):
        reported = self.payoff().json()["payoff"]["reportedAt"]
        self.today.return_value = date(2026, 12, 21)
        self.client.get("/api/home/")
        self.cancel(reported)
        self.cancel(reported)
        self.client.get("/api/home/")
        rows = self.snapshot()
        self.assertEqual([(r[1], r[3]) for r in rows], [(date(2026, 11, 20), "canceled"), (date(2026, 12, 20), "active")])
        self.assertTrue(rows[1][2].endswith("month:2026-12"))  # 지난달 회차는 되살리지 않고 이번 달 하나만
