"""R05: 급여일 변경 이력이 없어도 주기가 넘어가면 홈 조회만으로 대출 납부 회차가 준비된다"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from finance.api_loans import sync_loan_due
from finance.models import BankAccount, Connection, FinancialTransaction, Loan, PaymentDue, PaymentMatch
from users.models import FinancialProfile

# 실제 CODEF·LLM 호출 차단: 이 모듈 테스트가 도는 동안만 (setUpModule ~ tearDownModule)
_blocks = [
    patch("finance.codef._request", side_effect=AssertionError("CODEF 호출 금지")),
    patch("chat.llm.openai_rest.ResponsesClient.__init__", side_effect=AssertionError("LLM 호출 금지")),
]


def setUpModule():
    # 11/13: 모듈을 불러올 때 시작하면 전체 테스트(python manage.py test)에서 다른 앱(chat · policies)의
    # ResponsesClient 테스트까지 막혀 깨진다. 이 모듈 테스트가 돌 때만 막는다
    for block in _blocks:
        block.start()


def tearDownModule():
    for block in _blocks:
        block.stop()


class LoanDuesAcrossCyclesTests(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=date(2026, 10, 28))
        self.today = clock.start()
        self.addCleanup(clock.stop)
        self.user = get_user_model().objects.create_user(email="loan-cycle@example.com")
        self.profile = FinancialProfile.objects.create(user=self.user, monthly_take_home_income=2_800_000, payday=25)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def home(self, on=None):
        if on:
            self.today.return_value = on
        response = self.client.get("/api/home/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def register(self, **values):
        """대출 등록(loan_create 처럼 등록 시 이번 주기 회차를 만든다)"""
        fields = {"monthly_payment": 100_000, "payment_day": 20, "lender_name": "테스트", **values}
        loan = Loan.objects.create(user=self.user, origin="manual", status="repaying", **fields)
        with transaction.atomic():
            sync_loan_due(loan)
        return loan

    def dates(self, loan):
        return list(PaymentDue.objects.filter(loan=loan, obligation_state="active")
                    .order_by("due_date").values_list("due_date", flat=True))

    def pay(self, due, amount):
        bank, _ = Connection.objects.get_or_create(user=self.user, business_type="bank", organization_code="0004",
                                                   defaults={"status": "connected"})
        account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key=str(due.pk), account_type="demand")
        tx = FinancialTransaction.objects.create(
            user=self.user, account=account, source_kind="bank", occurred_on=due.due_date, amount=amount,
            direction="out", transaction_type="transfer", confirmed_category_id="UNCLASSIFIED",
            confirmation_state="needs_review",
        )
        match = PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=amount)
        due.paid_amount = amount
        due.payment_state = "paid" if amount >= due.confirmed_amount else "partial"
        due.save(update_fields=["paid_amount", "payment_state"])
        return match

    def test_consecutive_cycles_get_dues_from_home_only(self):
        self.home()
        loan = self.register()
        self.assertEqual(self.dates(loan), [date(2026, 11, 20)])
        body = self.home(date(2026, 11, 26))
        self.assertIn(("loan", "2026-12-20"), [(i["type"], i["dueDate"]) for i in body["mustPay"]])
        self.home(date(2026, 12, 27))
        self.home(date(2026, 12, 28))  # 같은 주기 반복 조회
        self.assertEqual(self.dates(loan), [date(2026, 11, 20), date(2026, 12, 20), date(2027, 1, 20)])
        self.assertEqual(PaymentDue.objects.filter(loan=loan).count(), 3)
        self.assertEqual(PaymentDue.objects.get(loan=loan, due_date=date(2027, 1, 20)).confirmed_amount, 100_000)
        self.assertEqual(FinancialProfile.objects.get(user=self.user).payday_revision, 0)

    def test_month_end_february_and_year_change(self):
        self.home(date(2026, 12, 26))
        loan = self.register(payment_day=31)
        self.home(date(2027, 1, 26))
        self.home(date(2027, 2, 26))
        self.assertEqual(self.dates(loan), [date(2026, 12, 31), date(2027, 1, 31), date(2027, 2, 28)])

    def test_paid_and_partial_dues_are_preserved(self):
        self.home()
        loan = self.register()
        first = PaymentDue.objects.get(loan=loan)
        first_match = self.pay(first, 100_000)
        self.home(date(2026, 11, 26))
        second = PaymentDue.objects.get(loan=loan, due_date=date(2026, 12, 20))
        second_match = self.pay(second, 40_000)
        snapshots = {due.pk: PaymentDue.objects.filter(pk=due.pk).values().get() for due in (first, second)}
        self.home(date(2026, 12, 21))
        self.home(date(2026, 12, 27))
        self.home(date(2026, 12, 27))
        for pk, snapshot in snapshots.items():
            self.assertEqual(PaymentDue.objects.filter(pk=pk).values().get(), snapshot)
        self.assertTrue(PaymentMatch.objects.filter(pk=first_match.pk, due=first).exists())
        self.assertTrue(PaymentMatch.objects.filter(pk=second_match.pk, due=second).exists())
        self.assertEqual(PaymentDue.objects.filter(loan=loan).count(), 3)

    def test_no_due_for_repaid_or_missing_values(self):
        self.home()
        repaid = self.register()
        repaid.status = "repaid"
        repaid.save(update_fields=["status"])
        no_amount = self.register(monthly_payment=None)
        no_day = self.register(payment_day=None)
        self.home(date(2026, 11, 26))
        self.assertEqual(PaymentDue.objects.filter(loan=repaid, due_date__gte=date(2026, 11, 25)).count(), 0)
        self.assertFalse(PaymentDue.objects.filter(loan__in=[no_amount, no_day], obligation_state="active").exists())

    def test_loan_without_any_due_skips_passed_date_but_gets_future_one(self):
        """등록 시점을 모르는 대출(회차 없음)에 이미 지난 납부일을 만들지 않는다"""
        self.home()
        passed = Loan.objects.create(user=self.user, origin="manual", status="repaying",
                                     monthly_payment=100_000, payment_day=26, lender_name="지난")
        future = Loan.objects.create(user=self.user, origin="manual", status="repaying",
                                     monthly_payment=100_000, payment_day=20, lender_name="다음")
        self.home()
        self.assertEqual(self.dates(passed), [])
        self.assertEqual(self.dates(future), [date(2026, 11, 20)])
        self.home(date(2026, 11, 26))
        self.assertEqual(self.dates(passed), [date(2026, 11, 26)])  # 새 주기의 오늘 이후 회차부터
        self.assertEqual(self.dates(future), [date(2026, 11, 20), date(2026, 12, 20)])

    def found(self, **values):
        """회차·연결 출금 없이 대출 정보만 있는 대출 (매월 26일 10만 원)"""
        fields = {"origin": "manual", "monthly_payment": 100_000, "payment_day": 26, "lender_name": "발견", **values}
        return Loan.objects.create(user=self.user, status="repaying", **fields)

    def first_seen(self, loan):
        loan.refresh_from_db()
        return loan.field_states.get("first_seen_on")

    def test_passed_day_is_skipped_only_in_first_seen_cycle(self):
        loan = self.found()
        for _ in range(2):  # 같은 날 반복 조회
            self.home()
            self.assertEqual(self.first_seen(loan), "2026-10-28")
            self.assertEqual(self.dates(loan), [])
        for on in (date(2026, 11, 28), date(2026, 11, 28), date(2026, 12, 28)):
            self.home(on)
        self.assertEqual(self.first_seen(loan), "2026-10-28")
        dues = PaymentDue.objects.filter(loan=loan).order_by("due_date")
        self.assertEqual([(d.due_date, d.payment_state, d.obligation_state, d.paid_amount) for d in dues], [
            (date(2026, 11, 26), "needs_review", "active", 0), (date(2026, 12, 26), "needs_review", "active", 0),
        ])

    def test_later_cycle_before_on_and_after_payment_day(self):
        loan = self.found()
        self.home()
        states = []
        for on in (date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 28)):
            self.home(on)
            states.append(PaymentDue.objects.filter(loan=loan).values_list("pk", "due_date", "payment_state").get())
        self.assertEqual(len({pk for pk, _, _ in states}), 1)
        self.assertEqual([(d, s) for _, d, s in states], [
            (date(2026, 11, 26), "unpaid"), (date(2026, 11, 26), "unpaid"), (date(2026, 11, 26), "needs_review"),
        ])

    def test_refetch_does_not_push_first_seen_later(self):
        from datetime import datetime
        from django.utils import timezone
        from finance.models import SyncRun

        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        loan = self.found(origin="provider", connection=bank, source_loan_key="k")

        def fetch(on):  # sync_loans 처럼 field_states 를 이어 쓰고 fetched_at · 조회 기록을 새로 남긴다
            at = timezone.make_aware(datetime(on.year, on.month, on.day, 9))
            SyncRun.objects.create(user=self.user, connection=bank, loan=loan, product_code="loan_transaction_list",
                                   status="success", started_at=at)
            loan.refresh_from_db()
            loan.field_states = {**loan.field_states, "codef_loan": {"history_count": 0}}
            loan.fetched_at = at
            loan.save(update_fields=["field_states", "fetched_at"])

        fetch(date(2026, 10, 27))
        self.home()  # 10/26 은 처음 조회(10/27)보다 앞섬 → 만들지 않음
        self.assertEqual(self.first_seen(loan), "2026-10-27")
        self.assertEqual(self.dates(loan), [])
        fetch(date(2026, 11, 28))
        self.home(date(2026, 11, 28))
        self.assertEqual(self.first_seen(loan), "2026-10-27")
        self.assertEqual(self.dates(loan), [date(2026, 11, 26)])

    def test_first_seen_uses_earliest_existing_due(self):
        """이미 회차가 있는 대출(배포 전 등록)은 첫 회차를 만든 날을 처음 확인한 날로 본다"""
        from datetime import datetime
        from django.utils import timezone

        self.home(date(2026, 10, 25))
        loan = self.register(payment_day=26)
        PaymentDue.objects.filter(loan=loan).update(created_at=timezone.make_aware(datetime(2026, 10, 25, 9)))
        self.home(date(2026, 10, 28))
        self.assertEqual(self.first_seen(loan), "2026-10-25")
        self.assertEqual(self.dates(loan), [date(2026, 10, 26)])

    def test_payday_changed_user_keeps_one_due_per_month(self):
        self.home()
        loan = self.register()
        self.profile.payday, self.profile.payday_revision = 10, 1
        self.profile.save(update_fields=["payday", "payday_revision"])
        self.home(date(2026, 11, 10))  # 11/10~12/10: 예전 주기 키 11/20 회차 재사용
        self.home(date(2026, 12, 11))
        self.home(date(2026, 12, 11))
        self.assertEqual(self.dates(loan), [date(2026, 11, 20), date(2026, 12, 20)])
        self.assertEqual(PaymentDue.objects.filter(loan=loan).count(), 2)
        self.assertTrue(PaymentDue.objects.get(due_date=date(2026, 11, 20)).occurrence_key.endswith("2026-10-25"))
        self.assertTrue(PaymentDue.objects.get(due_date=date(2026, 12, 20)).occurrence_key.endswith("month:2026-12"))


@skipUnless(connection.vendor == "postgresql", "PostgreSQL 동시 요청 검사")
class LoanDuesConcurrencyTests(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        clock = patch("django.utils.timezone.localdate", return_value=date(2026, 10, 28))
        self.today = clock.start()
        self.addCleanup(clock.stop)
        self.user = get_user_model().objects.create_user(email="loan-cycle-lock@example.com")
        FinancialProfile.objects.create(user=self.user, monthly_take_home_income=2_800_000, payday=25)

    def home(self, barrier):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
            barrier.wait(5)
            return client.get("/api/home/").status_code
        finally:
            connections.close_all()

    def test_concurrent_home_creates_one_due(self):
        loan = Loan.objects.create(user=self.user, origin="manual", status="repaying",
                                   monthly_payment=100_000, payment_day=20, lender_name="테스트")
        self.today.return_value = date(2026, 11, 26)
        barrier = Barrier(3)
        with ThreadPoolExecutor(max_workers=3) as pool:
            codes = list(pool.map(self.home, [barrier] * 3))
        self.assertEqual(codes, [200, 200, 200])
        self.assertEqual(list(PaymentDue.objects.filter(loan=loan).values_list("due_date", flat=True)),
                         [date(2026, 12, 20)])

    def run_homes(self):
        barrier = Barrier(3)
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(list(pool.map(self.home, [barrier] * 3)), [200, 200, 200])

    def test_concurrent_home_saves_first_seen_once_then_one_due(self):
        loan = Loan.objects.create(user=self.user, origin="manual", status="repaying",
                                   monthly_payment=100_000, payment_day=26, lender_name="발견")
        self.run_homes()
        loan.refresh_from_db()
        self.assertEqual(loan.field_states, {"first_seen_on": "2026-10-28"})
        self.assertFalse(PaymentDue.objects.filter(loan=loan).exists())
        self.today.return_value = date(2026, 11, 28)
        self.run_homes()
        loan.refresh_from_db()
        self.assertEqual(loan.field_states, {"first_seen_on": "2026-10-28"})
        self.assertEqual(list(PaymentDue.objects.filter(loan=loan).values_list("due_date", "payment_state")),
                         [(date(2026, 11, 26), "needs_review")])
