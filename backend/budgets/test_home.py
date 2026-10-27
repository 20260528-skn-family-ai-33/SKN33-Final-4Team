"""홈 API 테스트 (docs/api/home.md). 실행: backend 폴더에서 python manage.py test budgets.test_home"""
import threading
import uuid
import time
from datetime import date, datetime, timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from finance.models import BankAccount, Card, Connection, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense
from users.models import FinancialProfile

from . import home as home_module
from .home import cycle_dates, home_data
from .models import BudgetCycle

TODAY = date(2026, 10, 21)  # 급여일 25일 → 이번 주기 9/25 ~ 10/24, 다음 월급날 10/25, 지난 주기 시작 8/25


class HomeFixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="home@example.com", password="Example123!")
        self.profile = FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        self.bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = self.make_account("k1", balance=2_000_000, masked_number="****5678", name="급여통장")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_account(self, key, connection=None, **fields):
        data = dict(user=self.user, connection=connection or self.bank, source_account_key=key,
                    account_type="demand", is_selected=True, fetched_at=timezone.now())
        data.update(fields)
        return BankAccount.objects.create(**data)

    def make_tx(self, on, amount, direction="in", account=None, **fields):
        data = dict(user=self.user, source_kind="bank", account=account or self.account, occurred_on=on,
                    amount=amount, direction=direction,
                    transaction_type="income" if direction == "in" else "transfer",
                    confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        data.update(fields)
        return FinancialTransaction.objects.create(**data)

    def make_expense(self, **fields):
        # 기본: 이번 주기(9/25~) 시작일에 등록한 고정지출 — 등록 뒤에 오는 출금일은 미납이면 알림,
        #       등록 전 지난 주기 회차는 출금이 있을 때만 생김
        data = dict(user=self.user, name="월세", amount=700_000, payment_day=10, category_id="HOUSING_TELECOM",
                    status="confirmed", source_type="manual", payment_account=self.account,
                    confirmed_at=timezone.make_aware(datetime(2026, 9, 25, 0, 0)))
        data.update(fields)
        return RecurringExpense.objects.create(**data)

    def home(self, today=TODAY):
        return home_data(self.user, today)


class CycleDatesTest(TestCase):
    """① 월급날 계산 (home.md 표)"""

    def test_cycle_dates(self):
        cases = [
            (25, date(2026, 10, 21), date(2026, 9, 25), date(2026, 10, 25)),
            (25, date(2026, 10, 25), date(2026, 10, 25), date(2026, 11, 25)),   # 월급날 당일이면 새 주기
            (31, date(2027, 2, 10), date(2027, 1, 31), date(2027, 2, 28)),      # 없는 날은 말일
            (31, date(2027, 2, 28), date(2027, 2, 28), date(2027, 3, 31)),
            (25, date(2027, 1, 3), date(2026, 12, 25), date(2027, 1, 25)),      # 해 넘김
            (1, date(2026, 3, 1), date(2026, 3, 1), date(2026, 4, 1)),
        ]
        for payday, today, start, next_payday in cases:
            with self.subTest(payday=payday, today=today):
                got_start, got_next, _ = cycle_dates(payday, today)
                self.assertEqual((got_start, got_next), (start, next_payday))


class HomePeriodTest(HomeFixture):
    def test_no_payday_gives_empty_home(self):
        self.profile.payday = None
        self.profile.save()
        data = self.home()
        self.assertIsNone(data["period"])
        self.assertEqual(data["must_pay"], [])
        self.assertIsNone(data["budget"])
        self.assertFalse(BudgetCycle.objects.exists())

    def test_period_and_cycle_saved_once(self):
        period = self.home()["period"]
        self.assertEqual(
            {k: period[k] for k in ("start_date", "end_date", "next_payday", "dday")},
            {"start_date": "2026-09-25", "end_date": "2026-10-24", "next_payday": "2026-10-25", "dday": 4},
        )
        self.home()
        cycle = BudgetCycle.objects.get(status="active")
        self.assertEqual((cycle.starts_on, cycle.ends_before, cycle.status), (date(2026, 9, 25), date(2026, 10, 25), "active"))
        # 실제 지난 주기(8/25~)도 끝난 주기로 남김
        self.assertEqual(list(BudgetCycle.objects.filter(status="closed").values_list("starts_on", flat=True)), [date(2026, 8, 25)])

    def test_new_payday_closes_old_cycle(self):
        self.home()
        self.home(date(2026, 10, 25))
        states = dict(BudgetCycle.objects.values_list("starts_on", "status"))
        self.assertEqual(states, {date(2026, 8, 25): "closed", date(2026, 9, 25): "closed", date(2026, 10, 25): "active"})

    def test_salary_states(self):
        self.assertEqual(self.home()["period"]["salary_state"], "waiting")

        self.make_tx(date(2026, 9, 30), 300_000)                     # 작은 입금은 월급 아님
        self.assertEqual(self.home()["period"]["salary_state"], "waiting")

        self.make_tx(date(2026, 9, 25), 2_500_000)
        period = self.home()["period"]
        self.assertEqual((period["salary_state"], period["salary_received_on"]), ("received", "2026-09-25"))
        self.assertEqual(BudgetCycle.objects.get(status="active").confirmed_income, 2_500_000)

    def test_unknown_without_income(self):
        self.profile.monthly_take_home_income = None
        self.profile.save()
        self.make_tx(date(2026, 9, 25), 2_500_000)
        self.assertEqual(self.home()["period"]["salary_state"], "unknown")

    def test_early_salary(self):
        # 명절 전 9/18에 9/25 월급이 미리 → 이번 주기 월급으로
        self.make_tx(date(2026, 9, 18), 2_500_000)
        self.assertEqual(self.home()["period"]["salary_received_on"], "2026-09-18")
        # 다음 달 월급이 10/19에 미리 → nextSalaryEarlyOn, 주기는 그대로
        self.make_tx(date(2026, 10, 19), 2_500_000)
        period = self.home()["period"]
        self.assertEqual((period["start_date"], period["next_salary_early_on"]), ("2026-09-25", "2026-10-19"))

    def test_late_salary_keeps_new_cycle(self):
        today = date(2026, 10, 27)   # 10/25 월급날이 지났는데 아직 안 들어옴
        period = self.home(today)["period"]
        self.assertEqual((period["start_date"], period["salary_state"]), ("2026-10-25", "waiting"))
        self.make_tx(date(2026, 10, 27), 2_500_000)
        self.assertEqual(self.home(today)["period"]["salary_received_on"], "2026-10-27")

    def test_own_transfer_and_unselected_account_are_not_salary(self):
        other = self.make_account("k2", balance=0)
        self.make_tx(date(2026, 9, 26), 2_500_000, direction="out", account=other)   # 내 다른 계좌에서 나감
        self.make_tx(date(2026, 9, 26), 2_500_000)                                     # 같은 날 같은 금액 입금
        unselected = self.make_account("k3", is_selected=False)
        self.make_tx(date(2026, 9, 27), 2_500_000, account=unselected)
        self.assertEqual(self.home()["period"]["salary_state"], "waiting")


class HomeSalaryEdgeTest(HomeFixture):
    def test_future_deposit_is_not_salary_yet(self):
        """미래 날짜로 저장된 입금은 그날이 오기 전까지 월급으로 보지 않음"""
        self.make_tx(date(2026, 10, 24), 2_500_000)
        self.assertEqual(self.home(date(2026, 10, 21))["period"]["salary_state"], "waiting")

    def test_canceled_outflow_does_not_hide_salary(self):
        """같은 날 같은 금액의 취소된 출금은 '내 계좌끼리 옮긴 돈'이 아님"""
        other = self.make_account("k2", balance=0)
        self.make_tx(date(2026, 9, 25), 2_500_000, direction="out", account=other, is_canceled=True)
        self.make_tx(date(2026, 9, 25), 2_500_000)
        self.assertEqual(self.home()["period"]["salary_state"], "received")

    def test_foreign_currency_deposit_is_not_salary(self):
        usd = self.make_account("usd", currency="USD")
        self.make_tx(date(2026, 9, 25), 2_500_000, account=usd)
        self.assertEqual(self.home()["period"]["salary_state"], "waiting")


class HomeBalanceTest(HomeFixture):
    def test_foreign_currency_account_is_excluded(self):
        self.make_account("usd", balance=1_000, currency="USD")
        balance = self.home()["balance"]
        self.assertEqual((balance["total"], balance["account_count"]), (2_000_000, 1))

    def test_balance_rules(self):
        old = timezone.make_aware(datetime(2026, 10, 21, 9, 30))
        self.account.fetched_at = old
        self.account.save()
        self.make_account("k2", balance=None)                                          # 잔액 모름
        relogin = Connection.objects.create(user=self.user, business_type="bank", organization_code="0088", status="relogin")
        self.make_account("k3", connection=relogin, balance=500_000)
        gone = Connection.objects.create(user=self.user, business_type="bank", organization_code="0020",
                                         status="disconnected", disconnected_at=timezone.now())
        self.make_account("k4", connection=gone, balance=9_000_000)                    # 해제 → 제외
        self.make_account("k5", balance=9_000_000, is_selected=False)                  # 관리 대상 아님 → 제외
        self.make_account("k6", balance=9_000_000, account_type="deposit")             # 예적금 → 제외

        balance = self.home()["balance"]
        self.assertEqual(balance, {
            "total": 2_500_000, "account_count": 3, "unknown_count": 1, "relogin_count": 1,
            "as_of": "2026-10-21T09:30:00+09:00",
        })

    def test_all_unknown_is_null(self):
        self.account.balance = None
        self.account.save()
        self.assertIsNone(self.home()["balance"]["total"])


class HomeCountsTest(HomeFixture):
    def test_pending_count_matches_transaction_list(self):
        self.make_tx(date(2026, 8, 1), 10_000, direction="out", transaction_type="spending")
        self.make_tx(date(2026, 10, 1), 10_000, direction="out", transaction_type="spending")
        self.make_tx(date(2026, 10, 2), 10_000, direction="out", transaction_type="spending", confirmed_category_id="FOOD")
        self.make_tx(date(2026, 10, 3), 10_000, direction="out")   # 이체는 세지 않음
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            listed = self.client.get("/api/finance/transactions/?category=UNCLASSIFIED").json()
        self.assertEqual(self.home()["pending_count"], 2)
        self.assertEqual(listed["summary"]["needsReviewCount"], 2)

    def test_needs_payday_count(self):
        self.make_expense(payment_day=None)
        self.make_expense(payment_day=None, status="suggested", source_type="detected")
        self.make_expense(payment_day=None, status="stopped")
        self.assertEqual(self.home()["needs_payday_count"], 1)


class HomeMustPayTest(HomeFixture):
    def test_future_due_is_created_once_and_listed(self):
        expense = self.make_expense(payment_day=24)       # 이번 주기 10/24
        data = self.home()
        self.home()
        self.assertEqual(PaymentDue.objects.filter(recurring=expense).count(), 1)
        self.assertEqual(data["must_pay"], [{
            "due_id": str(PaymentDue.objects.get().pk), "type": "recurring", "source_id": str(expense.pk),
            "name": "월세", "due_date": "2026-10-24", "amount": 700_000, "payment_state": "unpaid",
            "is_estimated": False,
            "payment_account": {"account_id": str(self.account.pk), "display_name": "국민 급여통장 · ****5678",
                                "status": "connected"},
        }])

    def test_card_and_no_payday_expenses_have_no_due(self):
        card = Card.objects.create(user=self.user, name="Deep Dream", masked_number="****1234", origin="manual")
        self.make_expense(payment_account=None, payment_card=card, payment_day=24)
        self.make_expense(payment_day=None)
        self.assertEqual(self.home()["must_pay"], [])

    def test_past_due_waits_three_days_then_needs_review(self):
        self.make_expense(payment_day=19)        # 10/19 출금일, 출금 없음
        self.assertEqual(self.home(date(2026, 10, 21))["must_pay"][0]["payment_state"], "unpaid")     # +2일: 출금 확인 중
        self.assertEqual(self.home(date(2026, 10, 22))["must_pay"][0]["payment_state"], "unpaid")     # +3일까지
        self.assertEqual(self.home(date(2026, 10, 23))["must_pay"][0]["payment_state"], "needs_review")

    def test_first_month_past_due_is_not_created_without_withdrawal(self):
        """등록한 첫 달: 출금일(10/19)이 등록일(10/21)보다 앞이면 출금이 없을 때 만들지 않음 → 다음 달부터"""
        self.make_expense(payment_day=19, confirmed_at=timezone.make_aware(datetime(2026, 10, 21, 9, 0)))
        self.assertEqual(self.home(date(2026, 10, 23))["must_pay"], [])
        self.assertFalse(PaymentDue.objects.exists())

    def test_first_month_past_due_with_withdrawal_is_paid(self):
        """등록한 첫 달이라도 맞는 출금이 있으면 "냄"으로 만들어 연결 (fixed.md)"""
        self.make_expense(payment_day=19, confirmed_at=timezone.make_aware(datetime(2026, 10, 21, 9, 0)))
        self.make_tx(date(2026, 10, 19), 700_000, direction="out")
        self.assertEqual(self.home()["must_pay"], [])
        self.assertEqual(PaymentDue.objects.get().payment_state, "paid")

    def test_missed_due_after_registration_is_shown(self):
        """등록한 뒤의 출금일인데 앱을 늦게 열었고 출금도 없음 → 만들어서 미납 확인 필요로 알림"""
        self.make_expense(payment_day=26, confirmed_at=timezone.make_aware(datetime(2026, 10, 1)))  # 10/1 등록, 새 주기(10/25~) 출금일 10/26
        items = self.home(date(2026, 10, 30))["must_pay"]
        self.assertEqual([(i["due_date"], i["payment_state"]) for i in items], [("2026-10-26", "needs_review")])

    def test_withdrawal_saved_after_due_was_created_is_matched(self):
        """10/17 홈에서 회차를 만든 뒤 10/19 출금이 들어오면, 다음에 홈을 열 때 자동 연결 → 빠짐"""
        self.make_expense(payment_day=19)
        self.assertEqual(len(self.home(date(2026, 10, 17))["must_pay"]), 1)
        self.make_tx(date(2026, 10, 19), 700_000, direction="out")
        self.assertEqual(self.home(date(2026, 10, 23))["must_pay"], [])
        self.assertEqual(PaymentDue.objects.get().payment_state, "paid")

    def test_two_matching_withdrawals_are_not_auto_matched(self):
        """맞는 출금이 2건이면 자동으로 고르지 않음 → 미납 확인 필요 ([고르기])"""
        self.make_expense(payment_day=19)
        self.home(date(2026, 10, 17))
        self.make_tx(date(2026, 10, 19), 700_000, direction="out")
        self.make_tx(date(2026, 10, 20), 700_000, direction="out")
        items = self.home(date(2026, 10, 23))["must_pay"]
        self.assertEqual([i["payment_state"] for i in items], ["needs_review"])

    def test_past_due_with_one_withdrawal_is_paid_and_hidden(self):
        self.make_expense(payment_day=19)
        self.make_tx(date(2026, 10, 19), 700_000, direction="out")
        self.assertEqual(self.home()["must_pay"], [])
        self.assertEqual(PaymentDue.objects.get().payment_state, "paid")

    def test_previous_cycle_unpaid_shown_until_next_payday(self):
        expense = self.make_expense(payment_day=20)
        PaymentDue.objects.create(
            user=self.user, obligation_type="recurring", recurring=expense, occurrence_key="old",
            due_date=date(2026, 9, 20), confirmed_amount=700_000, source_type="manual",
            confirmation_state="confirmed", payment_state="needs_review", payment_account=self.account,
        )
        PaymentDue.objects.create(
            user=self.user, obligation_type="recurring", recurring=expense, occurrence_key="older",
            due_date=date(2026, 8, 20), confirmed_amount=700_000, source_type="manual",
            confirmation_state="confirmed", payment_state="needs_review", payment_account=self.account,
        )
        dates = [item["due_date"] for item in self.home()["must_pay"]]
        self.assertEqual(dates, ["2026-09-20", "2026-10-20"])          # 두 주기 전 것은 빠짐, 날짜순
        dates = [item["due_date"] for item in self.home(date(2026, 10, 25))["must_pay"]]
        self.assertNotIn("2026-09-20", dates)                           # 다음 월급날부터 지난 주기 미납은 빠짐

    def test_partial_shows_remaining_and_sorting(self):
        rent = self.make_expense(payment_day=24)
        self.make_expense(name="통신비", amount=55_000, payment_day=24)
        self.make_expense(name="관리비", amount=150_000, payment_day=23)
        self.home()
        due = PaymentDue.objects.get(recurring=rent)
        tx = self.make_tx(date(2026, 10, 20), 200_000, direction="out")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=200_000)
        due.paid_amount, due.payment_state = 200_000, "partial"
        due.save()
        items = self.home()["must_pay"]
        self.assertEqual([(i["name"], i["amount"]) for i in items],
                         [("관리비", 150_000), ("월세", 500_000), ("통신비", 55_000)])

    def test_other_users_data_is_not_shown(self):
        other = get_user_model().objects.create_user(email="other@example.com", password="Example123!")
        FinancialProfile.objects.create(user=other, payday=25)
        RecurringExpense.objects.create(user=other, name="남의 월세", amount=100_000, payment_day=24,
                                        category_id="HOUSING_TELECOM", status="confirmed", source_type="manual")
        self.assertEqual(self.home()["must_pay"], [])


class HomeApiTest(HomeFixture):
    def test_login_required(self):
        response = APIClient().get("/api/home/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_response_keys_follow_contract(self):
        self.make_expense(payment_day=24)
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            body = self.client.get("/api/home/").json()
        self.assertEqual(set(body), {"period", "balance", "pendingCount", "needsPaydayCount", "mustPay", "budget"})
        self.assertEqual(set(body["period"]), {"startDate", "endDate", "nextPayday", "dday", "salaryState",
                                               "salaryReceivedOn", "nextSalaryEarlyOn"})
        self.assertEqual(set(body["balance"]), {"total", "accountCount", "unknownCount", "reloginCount", "asOf"})
        self.assertEqual(set(body["mustPay"][0]), {"dueId", "type", "sourceId", "name", "dueDate", "amount",
                                                   "paymentState", "isEstimated", "paymentAccount"})
        self.assertIsNone(body["budget"])


@skipUnless(connection.vendor == "postgresql", "행 잠금은 PostgreSQL 에서만 확인")
class HomeConcurrencyTest(TransactionTestCase):
    """홈 조회와 고정지출 수정(근거 거래 연결)이 동시에 와도 서로 기다리다 멈추지(교착) 않는지"""
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="home-lock@example.com", password="Example123!")
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="k1",
                                                  account_type="demand", is_selected=True, balance=1)
        self.expense = RecurringExpense.objects.create(
            user=self.user, name="월세", amount=700_000, payment_day=24, category_id="HOUSING_TELECOM",
            status="confirmed", source_type="manual", payment_account=self.account,
            confirmed_at=timezone.make_aware(datetime(2026, 9, 25)),
        )
        home_data(self.user, TODAY)   # 주기 · 회차 미리 만들기
        self.tx = FinancialTransaction.objects.create(
            user=self.user, source_kind="bank", account=self.account, occurred_on=date(2026, 10, 20),
            amount=700_000, direction="out", transaction_type="transfer",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
        )

    def test_home_and_fixed_evidence_link_do_not_deadlock(self):
        results = {}
        original = home_module.ensure_cycle

        def slow_ensure_cycle(*args, **kwargs):
            time.sleep(0.5)   # 회원을 잠근 채로 잠깐 멈춤 → 그 사이 고정지출 수정이 끼어듦
            return original(*args, **kwargs)

        def run_home():
            try:
                with patch.object(home_module, "ensure_cycle", slow_ensure_cycle):
                    results["home"] = home_data(self.user, TODAY)
            except Exception as exc:  # 교착이면 여기서 잡힘
                results["home"] = exc
            finally:
                connection.close()

        def run_patch():
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                with patch("django.utils.timezone.localdate", return_value=TODAY):
                    results["patch"] = client.patch(
                        f"/api/budgets/fixed/{self.expense.pk}/", {"evidenceTransactionId": str(self.tx.pk)}, format="json",
                    ).status_code
            finally:
                connection.close()

        t1 = threading.Thread(target=run_home)
        t2 = threading.Thread(target=run_patch)
        t1.start()
        time.sleep(0.1)
        t2.start()
        t1.join(15)
        t2.join(15)
        self.assertIsInstance(results.get("home"), dict, results.get("home"))
        self.assertEqual(results.get("patch"), 200)
        self.assertEqual(PaymentDue.objects.get(recurring=self.expense, due_date=date(2026, 10, 24)).payment_state, "paid")


class HomePaymentReviewTest(HomeFixture):
    """PR #46 리뷰: 늦게 낸 지난달 미납 · 연결 해제 후 자동 재연결 · 미래 날짜 출금"""

    def at(self, day):
        return patch("django.utils.timezone.localdate", return_value=day)

    def fixed_patch(self, expense, body, day):
        with self.at(day):
            return self.client.patch(f"/api/budgets/fixed/{expense.pk}/", body, format="json")

    def test_late_payment_links_to_previous_unpaid_due(self):
        """9/20 회차를 못 내고 10/2에 늦게 냄 → 후보에 보이고, 고르면 이번 달(10/20)이 아니라 9/20 회차에 연결"""
        expense = self.make_expense(payment_day=20, confirmed_at=timezone.make_aware(datetime(2026, 8, 1)))
        self.home(date(2026, 9, 21))                       # 지난 주기(8/25~9/24) 회차 9/20
        self.home(date(2026, 10, 1))                       # 이번 주기(9/25~10/24), 9/20 은 미납 확인 필요
        tx = self.make_tx(date(2026, 10, 2), 700_000, direction="out")

        with self.at(date(2026, 10, 2)):
            item = self.client.get("/api/budgets/fixed/").json()["items"][0]
        self.assertEqual([c["transactionId"] for c in item["evidenceSuggestion"]["candidates"]], [str(tx.pk)])

        self.assertEqual(self.fixed_patch(expense, {"evidenceTransactionId": str(tx.pk)}, date(2026, 10, 2)).status_code, 200)
        old = PaymentDue.objects.get(recurring=expense, due_date=date(2026, 9, 20))
        new = PaymentDue.objects.get(recurring=expense, due_date=date(2026, 10, 20))
        self.assertEqual((old.payment_state, new.payment_state), ("paid", "unpaid"))
        self.assertFalse(new.matches.exists())
        self.assertEqual([i["due_date"] for i in self.home(date(2026, 10, 2))["must_pay"]], ["2026-10-20"])

    def test_unlinked_withdrawal_is_not_auto_linked_again(self):
        """자동 연결된 출금을 사용자가 풀면, 홈을 다시 열어도 그 회차에 다시 붙지 않음"""
        expense = self.make_expense(payment_day=19)
        self.home(date(2026, 10, 17))
        self.make_tx(date(2026, 10, 19), 700_000, direction="out")
        self.home(date(2026, 10, 23))
        self.assertEqual(PaymentDue.objects.get().payment_state, "paid")

        self.assertEqual(self.fixed_patch(expense, {"evidenceTransactionId": None}, date(2026, 10, 23)).status_code, 200)
        items = self.home(date(2026, 10, 23))["must_pay"]
        self.assertEqual([i["payment_state"] for i in items], ["needs_review"])
        self.assertFalse(PaymentDue.objects.get().matches.exists())

    def test_future_dated_withdrawal_is_not_paid_yet(self):
        """오늘보다 뒤 날짜로 저장된 출금은 아직 안 나간 돈 → 자동 연결하지 않음, 그날이 되면 연결"""
        self.make_expense(payment_day=20)
        self.make_tx(date(2026, 10, 22), 700_000, direction="out")
        self.assertEqual([i["payment_state"] for i in self.home(date(2026, 10, 21))["must_pay"]], ["unpaid"])
        self.assertEqual(self.home(date(2026, 10, 22))["must_pay"], [])


class LateDueFixture(HomeFixture):
    """지난달 회차 9/20(미납 확인 필요) + 이번 달 회차 10/20(미납)이 있는 상태"""

    def setUp(self):
        super().setUp()
        self.expense = self.make_expense(payment_day=20, confirmed_at=timezone.make_aware(datetime(2026, 8, 1)))
        self.home(date(2026, 9, 21))     # 지난 주기 회차 9/20 (출금 없음)
        self.home(date(2026, 10, 1))     # 이번 주기 회차 10/20 생성, 9/20 은 미납 확인 필요

    def at(self, day):
        return patch("django.utils.timezone.localdate", return_value=day)

    def pick(self, tx, day):
        body = {"evidenceTransactionId": str(tx.pk) if tx else None}
        with self.at(day):
            response = self.client.patch(f"/api/budgets/fixed/{self.expense.pk}/", body, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def candidates(self, day):
        with self.at(day):
            item = self.client.get("/api/budgets/fixed/").json()["items"][0]
        suggestion = item["evidenceSuggestion"]
        return [c["transactionId"] for c in suggestion["candidates"]] if suggestion else []

    def due(self, day):
        return PaymentDue.objects.get(recurring=self.expense, due_date=day)

    def states(self):
        return self.due(date(2026, 9, 20)).payment_state, self.due(date(2026, 10, 20)).payment_state


class HomeLateAndCurrentDueTest(LateDueFixture):
    """PR #46 2차 리뷰: 지난달 미납(9/20)과 이번 달(10/20) 회차를 섞어서 처리해도 회차별 기록이 섞이지 않는지"""

    def test_same_request_twice_keeps_one_payment(self):
        """1. 지난달 출금 연결을 똑같이 다시 보내도 이번 달까지 납부되지 않음"""
        late = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        self.pick(late, date(2026, 10, 2))
        self.pick(late, date(2026, 10, 2))
        self.assertEqual(self.states(), ("paid", "unpaid"))
        self.assertEqual(PaymentMatch.objects.filter(transaction=late).count(), 1)
        self.assertFalse(self.due(date(2026, 10, 20)).matches.exists())

    def test_linking_this_month_keeps_last_month_payment(self):
        """2. 지난달 미납을 해결한 뒤 이번 달 출금을 연결해도 지난달 납부 기록은 그대로"""
        late = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        self.pick(late, date(2026, 10, 2))
        now = self.make_tx(date(2026, 10, 20), 700_000, direction="out")
        self.pick(now, date(2026, 10, 21))
        self.assertEqual(self.states(), ("paid", "paid"))
        self.assertEqual(list(self.due(date(2026, 9, 20)).matches.values_list("transaction_id", flat=True)), [late.pk])
        self.assertEqual(list(self.due(date(2026, 10, 20)).matches.values_list("transaction_id", flat=True)), [now.pk])
        self.assertEqual(self.home(date(2026, 10, 21))["must_pay"], [])

    def test_candidate_near_this_month_goes_to_last_month(self):
        """3. 지난달 미납 후보로 보여 준 거래는 이번 달 납부일 근처여도 지난달 회차에 연결 (후보 = 저장 기준)"""
        tx = self.make_tx(date(2026, 10, 19), 700_000, direction="out")
        self.assertEqual(self.candidates(date(2026, 10, 19)), [str(tx.pk)])
        self.pick(tx, date(2026, 10, 19))
        self.assertEqual(self.states(), ("paid", "unpaid"))
        self.assertFalse(self.due(date(2026, 10, 20)).matches.exists())

    def test_clearing_this_month_keeps_last_month(self):
        """비우기는 이번 회차 연결만 풀고, 지난달 납부 기록은 그대로"""
        late = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        self.pick(late, date(2026, 10, 2))
        now = self.make_tx(date(2026, 10, 20), 700_000, direction="out")
        self.pick(now, date(2026, 10, 21))
        self.pick(None, date(2026, 10, 21))
        self.assertEqual(self.due(date(2026, 9, 20)).payment_state, "paid")
        self.assertFalse(self.due(date(2026, 10, 20)).matches.exists())
        self.assertEqual(PaymentMatch.objects.filter(transaction=late).count(), 1)

    def test_auto_link_this_month_while_last_month_unpaid(self):
        """지난달 미납이 남아 있어도, 이번 달 출금일 ±3일의 출금 1건은 이번 달 회차에 자동 연결"""
        now = self.make_tx(date(2026, 10, 20), 700_000, direction="out")
        items = self.home(date(2026, 10, 23))["must_pay"]
        self.assertEqual(self.states(), ("needs_review", "paid"))
        self.assertEqual([i["due_date"] for i in items], ["2026-09-20"])
        self.assertNotIn(str(now.pk), self.candidates(date(2026, 10, 23)))   # 이미 연결된 출금은 지난달 후보에서 빠짐

    def test_one_withdrawal_never_pays_two_dues(self):
        """어떤 순서로 보내도 출금 1건은 회차 1개만 갚음"""
        tx = self.make_tx(date(2026, 10, 20), 700_000, direction="out")
        self.pick(tx, date(2026, 10, 21))         # 지난달 미납이 있으니 지난달 회차로
        self.home(date(2026, 10, 23))             # 홈 자동 연결이 같은 출금을 이번 달에 또 붙이지 않음
        self.pick(tx, date(2026, 10, 23))
        self.assertEqual(PaymentMatch.objects.filter(transaction=tx).count(), 1)
        self.assertEqual(self.states(), ("paid", "needs_review"))


class FixedPickDueTest(LateDueFixture):
    """회차를 지정해서 근거 거래 바꾸기 · 비우기 (evidenceDueId) — 지난달 회차에 잘못 고른 출금 되돌리기"""

    def pick_due(self, tx, due_day, day):
        body = {"evidenceTransactionId": str(tx.pk) if tx else None, "evidenceDueId": str(self.due(due_day).pk)}
        with self.at(day):
            return self.client.patch(f"/api/budgets/fixed/{self.expense.pk}/", body, format="json")

    def item(self, day):
        with self.at(day):
            return self.client.get("/api/budgets/fixed/").json()["items"][0]

    def test_list_shows_recent_dues_and_suggestion_due(self):
        late = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        item = self.item(date(2026, 10, 2))
        self.assertEqual(item["evidenceSuggestion"]["dueId"], str(self.due(date(2026, 9, 20)).pk))
        self.assertEqual(item["evidenceSuggestion"]["dueDate"], "2026-09-20")
        self.pick(late, date(2026, 10, 2))
        dues = self.item(date(2026, 10, 2))["recentDues"]
        self.assertEqual([(d["dueDate"], d["paymentState"], [t["transactionId"] for t in d["transactions"]]) for d in dues],
                         [("2026-09-20", "paid", [str(late.pk)]), ("2026-10-20", "unpaid", [])])

    def test_undo_wrong_pick_on_last_month(self):
        """지난달 회차에 잘못 고른 출금을 비우면 그 회차만 풀리고, 이번 달 납부는 그대로"""
        wrong = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        self.pick(wrong, date(2026, 10, 2))
        now = self.make_tx(date(2026, 10, 20), 700_000, direction="out")
        self.pick(now, date(2026, 10, 21))
        self.assertEqual(self.states(), ("paid", "paid"))

        self.assertEqual(self.pick_due(None, date(2026, 9, 20), date(2026, 10, 21)).status_code, 200)
        self.assertEqual(self.states(), ("needs_review", "paid"))
        self.assertFalse(PaymentMatch.objects.filter(transaction=wrong).exists())
        self.home(date(2026, 10, 22))                                    # 홈을 열어도 다시 자동으로 붙지 않음
        self.assertFalse(PaymentMatch.objects.filter(transaction=wrong).exists())
        self.expense.refresh_from_db()
        self.assertNotIn(str(wrong.pk), self.expense.evidence)
        self.assertIn(str(now.pk), self.expense.evidence)

    def test_move_pick_from_last_month_to_this_month(self):
        """지난달로 들어간 출금을 이번 달 회차로 옮김"""
        tx = self.make_tx(date(2026, 10, 19), 700_000, direction="out")
        self.pick(tx, date(2026, 10, 19))                                # 지난달 미납이 있어 지난달로 들어감
        self.assertEqual(self.states(), ("paid", "unpaid"))
        self.assertEqual(self.pick_due(tx, date(2026, 10, 20), date(2026, 10, 21)).status_code, 200)
        self.assertEqual(self.states(), ("needs_review", "paid"))
        self.assertEqual(PaymentMatch.objects.filter(transaction=tx).count(), 1)

    def test_replace_pick_on_last_month(self):
        """지난달 회차의 출금을 다른 출금으로 바꿈 → 이전 출금은 풀리고 새 출금으로 납부"""
        first = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        second = self.make_tx(date(2026, 10, 3), 700_000, direction="out")
        self.pick(first, date(2026, 10, 3))
        self.assertEqual(self.pick_due(second, date(2026, 9, 20), date(2026, 10, 3)).status_code, 200)
        self.assertEqual(list(self.due(date(2026, 9, 20)).matches.values_list("transaction_id", flat=True)), [second.pk])
        self.assertEqual(self.states(), ("paid", "unpaid"))
        self.assertFalse(PaymentMatch.objects.filter(transaction=first).exists())

    def test_same_due_request_twice(self):
        tx = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        self.pick_due(tx, date(2026, 9, 20), date(2026, 10, 2))
        self.pick_due(tx, date(2026, 9, 20), date(2026, 10, 2))
        self.assertEqual(self.states(), ("paid", "unpaid"))
        self.assertEqual(PaymentMatch.objects.filter(transaction=tx).count(), 1)

    def test_invalid_due_is_400(self):
        tx = self.make_tx(date(2026, 10, 2), 700_000, direction="out")
        other = self.make_expense(name="관리비", amount=150_000, payment_day=20,
                                  confirmed_at=timezone.make_aware(datetime(2026, 8, 1)))
        self.home(date(2026, 10, 2))
        other_due = PaymentDue.objects.filter(recurring=other).first()
        old = PaymentDue.objects.create(
            user=self.user, obligation_type="recurring", recurring=self.expense, occurrence_key="too-old",
            due_date=date(2026, 7, 20), confirmed_amount=700_000, source_type="manual",
            confirmation_state="confirmed", payment_state="needs_review", payment_account=self.account,
        )
        url = f"/api/budgets/fixed/{self.expense.pk}/"
        cases = [
            {"evidenceDueId": str(self.due(date(2026, 9, 20)).pk)},                          # 근거 거래 없이
            {"evidenceTransactionId": str(tx.pk), "evidenceDueId": str(other_due.pk)},        # 다른 고정지출 회차
            {"evidenceTransactionId": str(tx.pk), "evidenceDueId": str(old.pk)},              # 지난 주기보다 오래됨
            {"evidenceTransactionId": str(tx.pk), "evidenceDueId": "not-a-uuid"},
        ]
        for body in cases:
            with self.subTest(body=body), self.at(date(2026, 10, 2)):
                r = self.client.patch(url, body, format="json")
                self.assertEqual(r.status_code, 400, r.content)
                self.assertIn("evidenceDueId", r.json()["error"]["fields"])
        self.assertFalse(PaymentMatch.objects.exists())


@skipUnless(connection.vendor == "postgresql", "행 잠금은 PostgreSQL 에서만 확인")
class FixedLinkLockTest(TransactionTestCase):
    """PR #46 3차 리뷰: 연결을 풀거나 자동 연결하는 동안 다른 요청이 거래에 저장한 표시(field_states)가 덮어써지지 않는지"""
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="lock-link@example.com", password="Example123!")
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="k1",
                                                  account_type="demand", is_selected=True, balance=1)
        self.expense = RecurringExpense.objects.create(
            user=self.user, name="월세", amount=700_000, payment_day=20, category_id="HOUSING_TELECOM",
            status="confirmed", source_type="manual", payment_account=self.account,
            confirmed_at=timezone.make_aware(datetime(2026, 8, 1)),
        )

    def make_tx(self, on):
        return FinancialTransaction.objects.create(
            user=self.user, source_kind="bank", account=self.account, occurred_on=on, amount=700_000,
            direction="out", transaction_type="transfer", confirmed_category_id="UNCLASSIFIED",
            confirmation_state="needs_review",
        )

    def other_writer(self, tx_id):
        """다른 처리: 같은 거래를 잠그고 표시를 하나 저장"""
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            t = FinancialTransaction.objects.select_for_update().get(pk=tx_id)
            t.field_states = {**(t.field_states or {}), "other_mark": True}
            t.save(update_fields=["field_states"])

    def run_with_writer(self, slow_target, slow_name, action, tx_id, before=False):
        """action 은 slow_target 의 slow_name 함수 앞(before) 또는 뒤에서 0.5초 멈추고, 그 사이 다른 처리가 같은 거래에 저장"""
        errors = []
        original = getattr(slow_target, slow_name)

        def slow(*args, **kwargs):
            is_slow = threading.current_thread().name == "slow"
            if is_slow and before:
                time.sleep(0.5)
            result = original(*args, **kwargs)
            if is_slow and not before:
                time.sleep(0.5)
            return result

        def run_action():
            try:
                action()
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        def run_writer():
            try:
                self.other_writer(tx_id)
            finally:
                connection.close()

        with patch.object(slow_target, slow_name, slow):
            t1 = threading.Thread(target=run_action, name="slow")
            t2 = threading.Thread(target=run_writer, name="fast")
            t1.start()
            time.sleep(0.1)
            t2.start()
            t1.join(15)
            t2.join(15)
        self.assertEqual(errors, [])

    def test_unlink_due_keeps_mark_saved_by_other_request(self):
        from . import api as budgets_api
        home_data(self.user, date(2026, 10, 21))                      # 이번 회차 10/20
        tx = self.make_tx(date(2026, 10, 20))
        home_data(self.user, date(2026, 10, 21))                      # 자동 연결
        due = PaymentDue.objects.get(recurring=self.expense, due_date=date(2026, 10, 20))
        self.assertTrue(due.matches.filter(transaction=tx).exists())

        def unlink():
            client = APIClient()
            client.force_authenticate(self.user)
            with patch("django.utils.timezone.localdate", return_value=date(2026, 10, 21)):
                r = client.patch(f"/api/budgets/fixed/{self.expense.pk}/",
                                 {"evidenceTransactionId": None, "evidenceDueId": str(due.pk)}, format="json")
            assert r.status_code == 200, r.content

        # 풀 거래를 읽은 뒤 · 저장하기 전에 멈춤 → 그 사이 다른 처리가 같은 거래에 저장
        self.run_with_writer(budgets_api, "unmark_fixed", unlink, tx.pk, before=True)
        tx.refresh_from_db()
        self.assertTrue(tx.field_states.get("other_mark"))                         # 다른 처리의 표시가 남음
        self.assertIn(due.occurrence_key, tx.field_states.get("skip_auto_match", []))  # 해제 표시도 남음

    def test_auto_link_keeps_mark_saved_by_other_request(self):
        from . import api as budgets_api
        home_data(self.user, date(2026, 10, 19))                      # 이번 회차 10/20 (출금 전)
        tx = self.make_tx(date(2026, 10, 20))

        self.run_with_writer(budgets_api, "matching_payments", lambda: home_data(self.user, date(2026, 10, 21)), tx.pk)
        tx.refresh_from_db()
        self.assertTrue(tx.field_states.get("other_mark"))                         # 다른 처리의 표시가 남음
        self.assertTrue(tx.field_states.get("converted_by_fixed"))                 # 자동 연결 표시도 남음
        self.assertEqual(PaymentDue.objects.get(recurring=self.expense, due_date=date(2026, 10, 20)).payment_state, "paid")


class HomeSkippedMonthTest(HomeFixture):
    """PR #46 4차 리뷰: 9월에 접속하고 10월을 건너뛰어 11월에 접속 — 11월 출금이 9월 미납으로 연결되면 안 됨
    (저장된 마지막 주기가 아니라 실제 지난 급여 주기 10/25~11/24 기준)"""

    def setUp(self):
        super().setUp()
        self.expense = self.make_expense(payment_day=20, confirmed_at=timezone.make_aware(datetime(2026, 8, 1)))
        self.home(date(2026, 9, 21))          # 9/20 회차 (출금 없음), 10월은 접속 안 함

    def at(self, day):
        return patch("django.utils.timezone.localdate", return_value=day)

    def due(self, day):
        return PaymentDue.objects.get(recurring=self.expense, due_date=day)

    def test_november_withdrawal_pays_november_due_automatically(self):
        tx = self.make_tx(date(2026, 11, 20), 700_000, direction="out")
        items = self.home(date(2026, 11, 26))["must_pay"]     # 이번 주기 11/25~, 실제 지난 주기 10/25~11/24
        self.assertEqual(self.due(date(2026, 11, 20)).payment_state, "paid")
        self.assertEqual(list(self.due(date(2026, 11, 20)).matches.values_list("transaction_id", flat=True)), [tx.pk])
        self.assertFalse(self.due(date(2026, 9, 20)).matches.exists())
        self.assertEqual([i["due_date"] for i in items], ["2026-12-20"])   # 9/20 은 두 주기 전이라 홈에 없음
        self.assertTrue(BudgetCycle.objects.filter(starts_on=date(2026, 10, 25), status="closed").exists())

    def test_picked_november_withdrawal_goes_to_november_due(self):
        """출금이 2건이라 자동 연결이 안 될 때, 고르면 9월이 아니라 지난 주기(11/20) 회차로"""
        self.make_tx(date(2026, 11, 19), 700_000, direction="out")
        picked = self.make_tx(date(2026, 11, 21), 700_000, direction="out")
        self.home(date(2026, 11, 26))
        self.assertEqual(self.due(date(2026, 11, 20)).payment_state, "needs_review")

        with self.at(date(2026, 11, 26)):
            item = self.client.get("/api/budgets/fixed/").json()["items"][0]
        self.assertEqual(item["evidenceSuggestion"]["dueDate"], "2026-11-20")
        self.assertEqual([d["dueDate"] for d in item["recentDues"]], ["2026-11-20", "2026-12-20"])

        with self.at(date(2026, 11, 26)):
            r = self.client.patch(f"/api/budgets/fixed/{self.expense.pk}/", {"evidenceTransactionId": str(picked.pk)}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.due(date(2026, 11, 20)).payment_state, "paid")
        self.assertFalse(self.due(date(2026, 9, 20)).matches.exists())

    def test_old_due_cannot_be_picked_by_id(self):
        """두 주기 전 회차(9/20)는 회차 지정으로도 고를 수 없음"""
        tx = self.make_tx(date(2026, 11, 21), 700_000, direction="out")
        self.home(date(2026, 11, 26))
        with self.at(date(2026, 11, 26)):
            r = self.client.patch(f"/api/budgets/fixed/{self.expense.pk}/",
                                  {"evidenceTransactionId": str(tx.pk), "evidenceDueId": str(self.due(date(2026, 9, 20)).pk)},
                                  format="json")
        self.assertEqual(r.status_code, 400, r.content)


@skipUnless(connection.vendor == "postgresql", "행 잠금은 PostgreSQL 에서만 확인")
class LockOrderTest(TransactionTestCase):
    """PR #46 4차 리뷰: 홈 자동 연결 · 납부 거래 교체가 거래 일괄 처리(재시도)와 동시에 돌아도 교착되지 않는지
    거래 ID 를 일부러 정해서, 홈 · 교체는 큰 ID → 작은 ID, 일괄 처리는 작은 ID → 큰 ID 순서로 거래를 잠그게 만든다"""
    serialized_rollback = True
    LOW = uuid.UUID("00000000-0000-4000-8000-000000000001")
    HIGH = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="lock-order@example.com", password="Example123!")
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="k1",
                                                  account_type="demand", is_selected=True, balance=1)

    def expense(self, pk, amount, day):
        return RecurringExpense.objects.create(
            recurring_id=pk, user=self.user, name=f"고정 {day}", amount=amount, payment_day=day,
            category_id="HOUSING_TELECOM", status="confirmed", source_type="manual", payment_account=self.account,
            confirmed_at=timezone.make_aware(datetime(2026, 9, 25)),
        )

    def tx(self, pk, amount, on):
        return FinancialTransaction.objects.create(
            transaction_id=pk, user=self.user, source_kind="bank", account=self.account, occurred_on=on,
            amount=amount, direction="out", transaction_type="transfer",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
        )

    def race(self, slow_target, slow_name, slow_action, fast_action, before=False):
        """slow_action 은 slow_name 함수 앞(before)/뒤에서 0.5초 멈추고, 0.1초 뒤 fast_action(일괄 처리)이 시작"""
        errors = []
        original = getattr(slow_target, slow_name)
        state = {"paused": False}

        def slow(*args, **kwargs):
            pause = threading.current_thread().name == "slow" and not state["paused"]
            if pause and before:
                state["paused"] = True
                time.sleep(0.5)
            result = original(*args, **kwargs)
            if pause and not before:
                state["paused"] = True
                time.sleep(0.5)
            return result

        def wrap(fn):
            def run():
                try:
                    fn()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    connection.close()
            return run

        with patch.object(slow_target, slow_name, slow):
            t1 = threading.Thread(target=wrap(slow_action), name="slow")
            t2 = threading.Thread(target=wrap(fast_action), name="fast")
            t1.start()
            time.sleep(0.1)
            t2.start()
            t1.join(20)
            t2.join(20)
        self.assertEqual(errors, [])

    def test_home_auto_link_and_batch_retry(self):
        from finance.pipeline import process_new_transactions
        from . import api as budgets_api
        first = self.expense(uuid.UUID(int=1), 700_000, 20)   # 고정지출 ID 순서로 처리 → 큰 ID 거래 먼저
        second = self.expense(uuid.UUID(int=2), 500_000, 21)
        home_data(self.user, date(2026, 10, 19))               # 10/20 · 10/21 회차 (출금 전)
        self.tx(self.HIGH, 700_000, date(2026, 10, 20))
        self.tx(self.LOW, 500_000, date(2026, 10, 21))

        self.race(
            budgets_api, "find_withdrawals",
            lambda: home_data(self.user, date(2026, 10, 23)),
            lambda: process_new_transactions(self.user, [self.LOW, self.HIGH]),
        )
        for e in (first, second):
            self.assertEqual(PaymentDue.objects.get(recurring=e).payment_state, "paid")

    def test_replace_payment_and_batch_retry(self):
        from finance.pipeline import process_new_transactions
        from . import api as budgets_api
        e = self.expense(uuid.UUID(int=1), 700_000, 20)
        home_data(self.user, date(2026, 10, 19))
        self.tx(self.LOW, 700_000, date(2026, 10, 20))
        home_data(self.user, date(2026, 10, 21))               # 작은 ID 거래가 자동 연결됨
        self.tx(self.HIGH, 700_000, date(2026, 10, 21))

        def replace():
            client = APIClient()
            client.force_authenticate(self.user)
            with patch("django.utils.timezone.localdate", return_value=date(2026, 10, 22)):
                r = client.patch(f"/api/budgets/fixed/{e.pk}/", {"evidenceTransactionId": str(self.HIGH)}, format="json")
            assert r.status_code == 200, r.content

        # 교체: 새 거래(큰 ID)를 잠근 뒤, 기존 연결(작은 ID)을 풀기 직전에 멈춤
        self.race(budgets_api, "unlink_current", replace,
                  lambda: process_new_transactions(self.user, [self.LOW, self.HIGH]), before=True)
        due = PaymentDue.objects.get(recurring=e)
        self.assertEqual(list(due.matches.values_list("transaction_id", flat=True)), [self.HIGH])
