"""홈 API 테스트 (docs/api/home.md). 실행: backend 폴더에서 python manage.py test budgets.test_home"""
import threading
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
        # 기본: 9/1에 등록해 둔 고정지출 (등록한 뒤에 오는 출금일은 미납이면 알림)
        data = dict(user=self.user, name="월세", amount=700_000, payment_day=10, category_id="HOUSING_TELECOM",
                    status="confirmed", source_type="manual", payment_account=self.account,
                    confirmed_at=timezone.make_aware(datetime(2026, 9, 1, 12, 0)))
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
        cycle = BudgetCycle.objects.get()
        self.assertEqual((cycle.starts_on, cycle.ends_before, cycle.status), (date(2026, 9, 25), date(2026, 10, 25), "active"))

    def test_new_payday_closes_old_cycle(self):
        self.home()
        self.home(date(2026, 10, 25))
        states = dict(BudgetCycle.objects.values_list("starts_on", "status"))
        self.assertEqual(states, {date(2026, 9, 25): "closed", date(2026, 10, 25): "active"})

    def test_salary_states(self):
        self.assertEqual(self.home()["period"]["salary_state"], "waiting")

        self.make_tx(date(2026, 9, 30), 300_000)                     # 작은 입금은 월급 아님
        self.assertEqual(self.home()["period"]["salary_state"], "waiting")

        self.make_tx(date(2026, 9, 25), 2_500_000)
        period = self.home()["period"]
        self.assertEqual((period["salary_state"], period["salary_received_on"]), ("received", "2026-09-25"))
        self.assertEqual(BudgetCycle.objects.get().confirmed_income, 2_500_000)

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
        self.make_expense(payment_day=26)   # 9/1 등록, 새 주기(10/25~) 출금일 10/26
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
            confirmed_at=timezone.make_aware(datetime(2026, 9, 1)),
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
        self.assertEqual(PaymentDue.objects.get(recurring=self.expense).payment_state, "paid")


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
