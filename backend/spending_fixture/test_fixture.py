"""소비 분석 fixture 검증 (실행: python manage.py test spending_fixture --settings=spending_fixture.settings)

- FixtureRelationTest: 적재 도구가 만든 행 자체의 관계 · 건수 · 반복 실행 · 초기화 범위
- ExistingCalculationTest: 현재 서비스 함수(listed_transactions · transaction_item · spending_summary ·
  cycle_dates · budget_data) 결과를 data/expected.json(설계 행을 따로 산술한 고정 정답)과 비교
- MicroFixtureTest: 손으로 검산한 작은 자료
정답은 서비스 함수로 다시 만들지 않는다. 기간 자르기(날짜 필터)만 테스트가 하고, 합산은 전부 서비스 함수가 한다.
아직 없는 소비 분석 1~9번 · 승인 예산 읽기 · 수집 완전성 판정은 여기서 흉내 내지 않는다 (README 부족 목록).
"""
import copy
import unittest
from datetime import date, time
from unittest import mock

from django.conf import settings
from django.db import connection
from django.test import TestCase

from budgets.home import cycle_dates
from budgets.models import Budget, BudgetCycle, BudgetItem
from budgets.overview import budget_data
from finance.api import listed_transactions, transaction_item
from finance.models import (BankAccount, Card, Connection, FinancialTransaction, PaymentDue, PaymentMatch,
                            RecurringExpense, SyncRun, TransactionLink)
from finance.spending import spending_summary
from users.models import FinancialProfile, User

from . import seed

DESIGN = seed.load("design.json")
EXPECTED = seed.load("expected.json")
TODAY = date(2026, 10, 29)
MONTHS = {"7": (date(2026, 7, 1), date(2026, 8, 1)), "8": (date(2026, 8, 1), date(2026, 9, 1)),
          "9": (date(2026, 9, 1), date(2026, 10, 1)), "10": (date(2026, 10, 1), date(2026, 10, 30))}
SEPT_SAME_DATES = (date(2026, 9, 1), date(2026, 9, 30))  # 9/1~29
# 공통 설정의 전체 테스트(manage.py test)에서는 건너뛴다. fixture 전용 DB 설정에서만 돈다
fixture_db_only = unittest.skipUnless(settings.SETTINGS_MODULE == "spending_fixture.settings",
                                      "--settings=spending_fixture.settings 로만 실행")


def user(p):
    return User.objects.get(pk=seed.user_ids(DESIGN)[p])


def summary(u, start, end, living_only=False):
    rows = listed_transactions(u).filter(occurred_on__gte=start, occurred_on__lt=end)
    return spending_summary((transaction_item(tx) for tx in rows), living_only=living_only)


def known(u, start, end):
    """서비스 결과를 05 의 칸 이름으로 옮긴다 (합산은 하지 않음. 고정비 = 전체 − 생활비)"""
    total, living = summary(u, start, end), summary(u, start, end, living_only=True)
    return {"total": total["spent_total"], "fixed_amount": total["spent_total"] - living["spent_total"],
            "living_amount": living["spent_total"],
            "by_category": {k: v for k, v in total["by_category"].items() if v},  # 05 는 0원 카테고리를 적지 않는다
            "unclassified_amount": total["by_category"].get("UNCLASSIFIED", 0),
            "needs_review_count": total["needs_review_count"]}


def item(plan_id):
    tx = listed_transactions(FinancialTransaction.objects.get(pk=seed.uid("tx", plan_id)).user).filter(
        pk=seed.uid("tx", plan_id)).first()
    return tx and transaction_item(tx)


@fixture_db_only
class FixtureRelationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def snapshot(self):
        users = list(seed.user_ids(DESIGN).values())
        models = seed.models()
        state = {name: sorted(str(pk) for pk in (m.objects.filter(pk__in=users) if name == "User" else
                                                 m.objects.filter(user_id__in=users)).values_list("pk", flat=True))
                 for name, m in models.items() if name != "Category"}
        state["links"] = sorted(TransactionLink.objects.values_list("from_transaction_id", "to_transaction_id"))
        state["matches"] = sorted(PaymentMatch.objects.values_list("due_id", "transaction_id", "matched_amount"))
        state["sums"] = seed.db_summary(DESIGN)
        return state

    def test_counts_match_design(self):
        counts, _ = seed.db_summary(DESIGN)
        self.assertEqual(counts, DESIGN["expected_baseline_counts"])

    def test_reapply_is_identical(self):
        before = self.snapshot()
        seed.apply(DESIGN)
        self.assertEqual(self.snapshot(), before)

    def test_reset_touches_only_fixture_users(self):
        other = User.objects.create_user(email="not-fixture@example.test")
        seed.reset(DESIGN)
        self.assertTrue(User.objects.filter(pk=other.pk).exists())
        self.assertFalse(User.objects.filter(pk__in=seed.user_ids(DESIGN).values()).exists())

    def test_reset_refuses_foreign_user_with_fixture_email(self):
        seed.reset(DESIGN)
        User.objects.create_user(email=DESIGN["personas"]["A"]["synthetic_email"])
        with self.assertRaises(SystemExit):
            seed.reset(DESIGN)

    def test_guard_refuses_service_db(self):
        with mock.patch.dict(connection.settings_dict, NAME="money_cushion"), self.assertRaises(SystemExit):
            seed.apply(DESIGN)

    def test_plan_check_catches_cross_user_fk(self):
        rows = copy.deepcopy(seed.plan(DESIGN))
        tx = next(f for m, f, _ in rows if m == "FinancialTransaction" and f["source_kind"] == "card")
        tx["card_id"] = seed.uid("B", "card") if tx["user_id"] == seed.user_ids(DESIGN)["A"] else seed.uid("A", "card")
        self.assertIn(("사용자 간 FK 섞임 없음", False), seed.check(DESIGN, rows))

    def test_fks_stay_within_one_user(self):
        for tx in FinancialTransaction.objects.select_related("account", "card"):
            self.assertEqual((tx.account or tx.card).user_id, tx.user_id)
            self.assertEqual(tx.source_kind, "bank" if tx.account_id else "card")
            self.assertIsNone(tx.balance_after)
            self.assertIsNone(tx.source_detail)
        for card in Card.objects.select_related("payment_account", "connection"):
            self.assertEqual({card.payment_account.user_id, card.connection.user_id}, {card.user_id})
        for m in PaymentMatch.objects.select_related("due__recurring", "transaction"):
            self.assertEqual({m.due.user_id, m.transaction.user_id, m.due.recurring.user_id}, {m.user_id})
        for link in TransactionLink.objects.select_related("from_transaction", "to_transaction"):
            self.assertEqual({link.from_transaction.user_id, link.to_transaction.user_id}, {link.user_id})
        for bi in BudgetItem.objects.select_related("budget__cycle"):
            self.assertEqual({bi.budget.user_id, bi.budget.cycle.user_id}, {bi.user_id})
        self.assertFalse(BankAccount.objects.exclude(balance=None).exists())
        self.assertFalse(User.objects.exclude(codef_connected_id_ciphertext=None).exists())
        self.assertFalse(User.objects.filter(is_staff=True).exists())

    def test_budget_lines_and_versions(self):
        self.assertFalse(BudgetItem.objects.filter(category_id="UNCLASSIFIED").exists())
        self.assertEqual(BudgetItem.objects.filter(bucket="living", category_id="ETC").count(), 12)
        self.assertFalse(Budget.objects.exclude(task=None).exists())
        for cycle in BudgetCycle.objects.all():
            self.assertLessEqual(cycle.budgets.filter(status="approved").count(), 1)
        a_now = BudgetCycle.objects.get(user=user("A"), status="active").budgets.order_by("version_no")
        self.assertEqual([(b.version_no, b.status) for b in a_now], [(1, "approved"), (2, "validated")])
        self.assertEqual(a_now[1].base_budget_id, a_now[0].pk)
        c_budgets = Budget.objects.filter(user=user("C"))
        self.assertEqual([(b.status, b.approved_at) for b in c_budgets], [("draft", None)])
        for b in Budget.objects.filter(user=user("A")):  # 여윳돈은 저장하지 않고 funding − 항목 합계
            self.assertEqual(b.funding_amount - sum(b.items.values_list("allocated_amount", flat=True)), 200_000)

    def test_confirmed_income_only_with_salary_evidence(self):
        for p, first in (("A", date(2026, 6, 25)), ("B", date(2026, 6, 10))):
            cycles = BudgetCycle.objects.filter(user=user(p)).order_by("starts_on")
            self.assertEqual(cycles[0].starts_on, first)
            self.assertIsNone(cycles[0].confirmed_income)  # 6월 시작 보조 주기는 급여 근거 없음
            self.assertTrue(all(c.confirmed_income for c in cycles[1:]))

    def test_fixed_chain(self):
        fixed = [r["plan_id"] for r in DESIGN["transaction_design_rows"] if r["funding"] == "fixed"]
        self.assertEqual(len(fixed), 30)
        for plan_id in fixed:
            match = PaymentMatch.objects.select_related("due__recurring").get(transaction_id=seed.uid("tx", plan_id))
            self.assertEqual(match.due.obligation_type, "recurring")
            self.assertEqual(match.due.paid_amount, match.due.confirmed_amount)
            self.assertEqual(match.due.recurring.status, "confirmed")
            self.assertIsNone(match.due.recurring.payment_card_id)
        self.assertEqual(RecurringExpense.objects.count(), 9)
        self.assertEqual(PaymentDue.objects.count(), 30)

    def test_coverage_records(self):
        c = user("C")
        self.assertFalse(SyncRun.objects.filter(user=c, status="success", covered_from__lt=date(2026, 10, 20)).exists())
        self.assertFalse(FinancialTransaction.objects.filter(user=c, occurred_on__lt=date(2026, 10, 20)).exists())
        failed = SyncRun.objects.get(user=c, status="failed")
        self.assertEqual((failed.product_code, failed.covered_from, failed.covered_to), ("card_approval_list", None, None))
        self.assertEqual(Card.objects.get(user=c).fetched_at.date(), date(2026, 10, 24))  # 실패가 성공 시각을 덮지 않음
        for p in "AB":
            runs = SyncRun.objects.filter(user=user(p), status="success")
            self.assertEqual(runs.count(), 8)
            self.assertFalse(runs.filter(product_code="bank_sync").exclude(card=None).exists())
            self.assertEqual(runs.filter(product_code="card_approval_list", card__isnull=False).count(), 4)


@fixture_db_only
class ExistingCalculationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_cycle_dates_pure_function(self):
        for p, (start, end) in {"A": ("2026-10-25", "2026-11-25"), "B": ("2026-10-10", "2026-11-10"),
                                "C": ("2026-10-20", "2026-11-20")}.items():
            got = cycle_dates(DESIGN["personas"][p]["payday"], TODAY)[:2]
            self.assertEqual(got, (date.fromisoformat(start), date.fromisoformat(end)))
        self.assertEqual(cycle_dates(25, date(2026, 10, 24))[0], date(2026, 9, 25))  # 시작 전날은 지난 주기
        self.assertEqual(cycle_dates(25, date(2026, 10, 25))[0], date(2026, 10, 25))  # 시작일 포함

    def test_monthly_known_sums(self):
        for p in "ABC":
            for month, (start, end) in MONTHS.items():
                with self.subTest(persona=p, month=month):
                    self.assertEqual(known(user(p), start, end), EXPECTED["personas"][p]["monthly_known_sums"][month])
            self.assertEqual(known(user(p), *MONTHS["10"]), EXPECTED["personas"][p]["current_month_known"])

    def test_same_dates_comparison_is_not_full_september(self):
        for p in "ABC":
            with self.subTest(persona=p):
                self.assertEqual(known(user(p), *SEPT_SAME_DATES), EXPECTED["personas"][p]["previous_same_dates_known"])
        b = known(user("B"), *SEPT_SAME_DATES)["by_category"]["FOOD"]
        self.assertEqual((b, known(user("B"), *MONTHS["9"])["by_category"]["FOOD"]), (406_000, 420_000))
        a = known(user("A"), *SEPT_SAME_DATES)["total"]
        self.assertEqual((a, known(user("A"), *MONTHS["9"])["total"]), (1_468_000, 1_480_000))

    def test_current_cycle_living(self):
        for p in "ABC":
            start, end, _ = cycle_dates(DESIGN["personas"][p]["payday"], TODAY)
            living = summary(user(p), start, end, living_only=True)
            want = EXPECTED["personas"][p]["current_cycle_living_known"]
            with self.subTest(persona=p):
                self.assertEqual(living["spent_total"], want["living_amount"])
                self.assertEqual({k: v for k, v in living["by_category"].items() if v}, want["by_category"])

    def test_a_food_month_vs_cycle(self):
        a = user("A")
        self.assertEqual(known(a, *MONTHS["10"])["by_category"]["FOOD"], 390_000)
        self.assertEqual(summary(a, date(2026, 10, 25), date(2026, 10, 30), living_only=True)["by_category"]["FOOD"], 60_000)

    def test_b_duplicate_and_cancels(self):
        self.assertEqual(item("B-0193")["net_amount"], 180_000)
        self.assertIsNone(item("B-0194"))  # 같은 구매의 계좌 출금은 목록에서 빠진다
        partial, full = item("B-0195"), item("B-0196")
        self.assertEqual((partial["cancel"], partial["net_amount"]), ({"state": "partial", "amount": 60_000}, 120_000))
        self.assertEqual((full["cancel"], full["net_amount"]), ({"state": "full", "amount": 100_000}, 0))
        self.assertEqual(known(user("B"), *MONTHS["10"])["by_category"]["FASHION_SHOPPING"], 300_000)

    def test_c_unclassified_spending_counts_non_spending_does_not(self):
        c = known(user("C"), *MONTHS["10"])
        self.assertEqual((c["unclassified_amount"], c["needs_review_count"], c["total"]), (50_000, 2, 390_000))
        for plan_id in ("C-0017", "C-0019"):  # 급여 · 남에게 보낸 이체: DB 는 UNCLASSIFIED 지만 목록 카테고리는 None
            self.assertEqual(FinancialTransaction.objects.get(pk=seed.uid("tx", plan_id)).confirmed_category_id, "UNCLASSIFIED")
            self.assertEqual((item(plan_id)["category"], item(plan_id)["funding"]), (None, None))

    def test_non_spending_never_in_totals(self):
        excluded = {"income", "saving", "transfer", "own_transfer", "card_payment"}
        rows = [r for r in DESIGN["transaction_design_rows"] if r["transaction_type"] in excluded]
        self.assertEqual({r["transaction_type"] for r in rows}, excluded)
        for r in rows:
            got = item(r["plan_id"])
            if got is not None:  # saving · own_transfer · card_payment 는 목록에도 없다
                self.assertEqual((got["transaction_type"], got["category"]), (r["transaction_type"], None))
        # 비소비 행이 섞여 있는 상태에서 월 합계가 05 정답과 같다는 것은 test_monthly_known_sums 가 확인한다

    def test_user_isolation(self):
        FinancialTransaction.objects.filter(user=user("B")).update(amount=9_999_000, canceled_amount=None)
        self.assertEqual(known(user("A"), *MONTHS["10"]), EXPECTED["personas"]["A"]["current_month_known"])
        self.assertEqual(known(user("C"), *MONTHS["10"]), EXPECTED["personas"]["C"]["current_month_known"])

    def test_b_budget_overspend_formula(self):
        data, want = budget_data(user("B"), TODAY), EXPECTED["personas"]["B"]["budget"]
        self.assertEqual((data["budget"]["version_no"], data["budget"]["status"]), (1, "approved"))
        self.assertEqual(data["living"], {"budget": want["living_budget"], "spent": want["living_spent"],
                                          "remaining": want["living_remaining"]})
        self.assertEqual(data["overspend"], {k: want[k] for k in ("over", "covered_by_spare", "spare_remaining", "shortfall")})
        food = next(c for c in data["categories"] if c["category"] == "FOOD")
        self.assertEqual((food["allocated"], food["spent"]), (want["food_budget"], want["food_spent"]))

    def test_a_living_remaining(self):
        data, want = budget_data(user("A"), TODAY), EXPECTED["personas"]["A"]["budget"]
        self.assertEqual(data["living"], {"budget": want["living_budget"], "spent": want["living_spent"],
                                          "remaining": want["living_remaining"]})  # v1 · v2 생활비 총액이 같아서 성립

    @unittest.expectedFailure  # 미구현: 승인 예산 읽기 전용 경로. budget_data 는 validated v2(식비 450,000)를 먼저 고른다
    def test_a_category_budget_uses_approved_v1(self):
        data = budget_data(user("A"), TODAY)
        food = next(c for c in data["categories"] if c["category"] == "FOOD")
        self.assertEqual((data["budget"]["version_no"], food["allocated"]),
                         (1, EXPECTED["personas"]["A"]["budget"]["food_budget"]))

    def test_c_draft_is_not_used_as_budget(self):
        data = budget_data(user("C"), TODAY)
        self.assertEqual((data["budget"], data["living"], data["overspend"]), (None, None, None))


@fixture_db_only
class MicroFixtureTest(TestCase):
    """02 문서 4번: 페르소나 자료와 다른 초기화 범위의 손계산 자료 (A 의 급여일 25일 재사용)"""

    @classmethod
    def setUpTestData(cls):
        u = cls.user = User.objects.create_user(email="fixture-micro@example.test")
        FinancialProfile.objects.create(user=u, payday=25, monthly_take_home_income=300_000)
        bank_conn = Connection.objects.create(user=u, business_type="bank", organization_code="FIXTURE_BANK", status="connected")
        card_conn = Connection.objects.create(user=u, business_type="card", organization_code="FIXTURE_CARD", status="connected")
        bank = BankAccount.objects.create(user=u, connection=bank_conn, source_account_key="fixture:micro-bank", is_selected=True)
        card = Card.objects.create(user=u, connection=card_conn, payment_account=bank, origin="provider", source_card_key="fixture:micro-card")
        cycle = BudgetCycle.objects.create(user=u, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25), status="active")
        budget = Budget.objects.create(user=u, cycle=cycle, version_no=1, status="approved", funding_amount=300_000,
                                       engine_version=seed.ENGINE)
        for code, amount in (("FOOD", 50_000), ("ETC", 50_000)):
            BudgetItem.objects.create(user=u, budget=budget, bucket="living", category_id=code, allocated_amount=amount)
        BudgetItem.objects.create(user=u, budget=budget, bucket="fixed", category_id="HOUSING_TELECOM", allocated_amount=70_000)

        def tx(row, day, **extra):
            source = row.get("source_kind", "card")
            return FinancialTransaction.objects.create(
                user=u, source_kind=source, account=bank if source == "bank" else None, card=card if source == "card" else None,
                source_key=f"fixture:micro:{row['name']}", occurred_on=day, occurred_time=time(12), amount=row["amount"],
                direction=extra.pop("direction", "out"), transaction_type=extra.pop("transaction_type", "spending"),
                canceled_amount=row.get("canceled_amount"), is_canceled=row.get("canceled_amount") == row["amount"],
                confirmed_category_id=row["category"], merchant_name=row["name"],
                confirmation_state="needs_review" if row["category"] == "UNCLASSIFIED" else "confirmed", **extra)

        made = {r["name"]: tx(r, date(2026, 10, 26)) for r in DESIGN["micro_fixture_rows"]}
        TransactionLink.objects.create(user=u, from_transaction=made["food_card"], to_transaction=made["food_bank_duplicate"],
                                       relation_type="SAME_PURCHASE", matched_amount=24_000, confirmation_state="confirmed")
        rent = RecurringExpense.objects.create(user=u, payment_account=bank, name="micro rent", amount=70_000, payment_day=26,
                                               category_id="HOUSING_TELECOM", status="confirmed", source_type="manual")
        due = PaymentDue.objects.create(user=u, obligation_type="recurring", recurring=rent, payment_account=bank,
                                        occurrence_key=f"recurring:{rent.pk}:2026-10-25", due_date=date(2026, 10, 26),
                                        confirmed_amount=70_000, source_type="manual", confirmation_state="confirmed",
                                        paid_amount=70_000, payment_state="paid")
        PaymentMatch.objects.create(user=u, due=due, transaction=made["fixed_housing"], matched_amount=70_000)
        # 비소비 행: 넣어도 소비 합계가 변하지 않아야 한다
        for kind in ("income", "saving", "transfer", "own_transfer", "card_payment"):
            tx({"name": kind, "amount": 1_000_000, "category": "UNCLASSIFIED", "source_kind": "bank"}, date(2026, 10, 27),
               transaction_type=kind, direction="in" if kind == "income" else "out")

    def test_micro_answers(self):
        want = EXPECTED["micro_fixture_expected"]
        got = known(self.user, date(2026, 10, 25), date(2026, 11, 25))
        self.assertEqual((got["total"], got["fixed_amount"], got["living_amount"], got["by_category"]["FOOD"],
                          got["unclassified_amount"]),
                         (want["total"], want["fixed_amount"], want["living_spent"], want["food_net"],
                          want["unclassified_amount"]))
        data = budget_data(self.user, TODAY)
        self.assertEqual(data["living"], {"budget": want["living_budget"], "spent": want["living_spent"],
                                          "remaining": want["living_remaining"]})
        food = next(c for c in data["categories"] if c["category"] == "FOOD")
        self.assertEqual((food["allocated"], food["spent"]), (want["food_budget"], want["food_net"]))
