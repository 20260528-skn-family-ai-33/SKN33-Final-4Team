"""수집 완전성 판정(spending_analysis.coverage) 검증. 기본 414건은 그대로, 변형은 별도 사용자로 테스트 안에서만 만든다(롤백)
실행: python manage.py test spending_fixture --settings=spending_fixture.settings
"""
from datetime import date, datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import OperationalError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from spending_analysis.dialog import MONTHS_MAX
from finance.models import BankAccount, Card, Connection, FinancialTransaction, SyncRun
from spending_analysis import periods
from spending_analysis.budget import approved_budget
from spending_analysis.coverage import coverage, gaps, merge
from spending_analysis.periods import Period, budget_period, recent_months, this_month
from users.models import User

from . import seed
from .test_fixture import DESIGN, fixture_db_only, user

d = date
P = Period
TODAY = d(2026, 10, 29)
KST = ZoneInfo("Asia/Seoul")


def missing(result):
    return {t["kind"]: t["missing"] for t in result["targets"]}


@fixture_db_only
class MergeTest(SimpleTestCase):
    def test_merge_overlap_and_adjacent(self):
        self.assertEqual(merge([P(d(2026, 10, 21), d(2026, 10, 30)), P(d(2026, 10, 1), d(2026, 10, 11)),
                                P(d(2026, 10, 8), d(2026, 10, 21))]), [P(d(2026, 10, 1), d(2026, 10, 30))])
        self.assertEqual(merge([P(d(2026, 10, 1), d(2026, 10, 11)), P(d(2026, 10, 12), d(2026, 10, 30))]),
                         [P(d(2026, 10, 1), d(2026, 10, 11)), P(d(2026, 10, 12), d(2026, 10, 30))])  # 10/11 하루 빔

    def test_gaps(self):
        oct_ = P(d(2026, 10, 1), d(2026, 10, 30))
        self.assertEqual(gaps(oct_, []), [oct_])
        self.assertEqual(gaps(oct_, [P(d(2026, 9, 1), d(2026, 11, 1))]), [])
        self.assertEqual(gaps(oct_, [P(d(2026, 10, 1), d(2026, 10, 11)), P(d(2026, 10, 12), d(2026, 10, 30))]),
                         [P(d(2026, 10, 11), d(2026, 10, 12))])
        self.assertEqual(gaps(oct_, [P(d(2026, 10, 20), d(2026, 10, 25))]),
                         [P(d(2026, 10, 1), d(2026, 10, 20)), P(d(2026, 10, 25), d(2026, 10, 30))])

    def test_router_and_periods_share_max_months(self):
        self.assertEqual(periods.MAX_MONTHS, MONTHS_MAX)


@fixture_db_only
class PersonaCoverageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed.apply(DESIGN)

    def test_a_b_complete_for_completed_months_and_this_month(self):
        for p in "AB":
            for period in [*recent_months(TODAY, 3), this_month(TODAY)]:
                with self.subTest(persona=p, period=period):
                    got = coverage(user(p), period)
                    self.assertEqual(got["state"], "complete")
                    self.assertEqual(sorted(t["kind"] for t in got["targets"]), ["account", "card"])
                    self.assertTrue(all(t["evidence"] and t["latest_success_at"] for t in got["targets"]))

    def test_c_history_is_none_not_zero(self):
        for period in recent_months(TODAY, 3):
            with self.subTest(period=period):
                got = coverage(user("C"), period)
                self.assertEqual(got["state"], "none")
                self.assertEqual(missing(got), {"account": [period], "card": [period]})

    def test_c_october_partial(self):
        got = coverage(user("C"), this_month(TODAY))
        self.assertEqual(got["state"], "partial")
        self.assertEqual(missing(got), {
            "account": [P(d(2026, 10, 1), d(2026, 10, 20))],
            "card": [P(d(2026, 10, 1), d(2026, 10, 20)), P(d(2026, 10, 25), d(2026, 10, 30))]})

    def test_c_current_cycle_partial_by_card_gap(self):
        cycle = approved_budget(user("C"), TODAY)["cycle"]  # 승인 예산은 없지만 주기는 있다
        spent = budget_period(cycle["starts_on"], cycle["ends_before"], TODAY)["spent"]
        got = coverage(user("C"), spent)
        self.assertEqual(got["state"], "partial")
        self.assertEqual(missing(got), {"account": [], "card": [P(d(2026, 10, 25), d(2026, 10, 30))]})
        card = next(t for t in got["targets"] if t["kind"] == "card")
        self.assertEqual(card["latest_success_at"], datetime(2026, 10, 24, 23, tzinfo=KST))  # 실패가 덮지 않음

    def test_other_users_runs_do_not_mix(self):
        c_card = Card.objects.get(user=user("C"))
        a_conn = Connection.objects.get(user=user("A"), business_type="card")
        # 다른 사용자 이름으로 C 카드를 가리키는 성공 기록 — 쓰이면 안 된다
        SyncRun.objects.create(user=user("A"), connection=a_conn, card=c_card, product_code="card_approval_list",
                               status="success", covered_from=d(2026, 10, 1), covered_to=d(2026, 10, 29))
        self.assertEqual(coverage(user("C"), this_month(TODAY))["state"], "partial")
        self.assertEqual(missing(coverage(user("C"), this_month(TODAY)))["card"][-1], P(d(2026, 10, 25), d(2026, 10, 30)))

    def test_read_only(self):
        before = (sorted(SyncRun.objects.values_list("sync_id", "status", "covered_from", "covered_to")),
                  seed.db_summary(DESIGN))
        with CaptureQueriesContext(connection) as queries:
            for p in "ABC":
                for period in [*recent_months(TODAY, 3), this_month(TODAY)]:
                    coverage(user(p), period)
        self.assertEqual((sorted(SyncRun.objects.values_list("sync_id", "status", "covered_from", "covered_to")),
                          seed.db_summary(DESIGN)), before)
        sql = [q["sql"].upper() for q in queries]
        self.assertTrue(sql and all(s.startswith("SELECT") and "FOR UPDATE" not in s for s in sql))

    def test_db_error_is_not_none(self):
        with mock.patch.object(SyncRun.objects, "filter", side_effect=OperationalError("down")):
            got = coverage(user("A"), this_month(TODAY))
        self.assertEqual((got["state"], got["reason"]), ("error", "db_error"))


@fixture_db_only
class CoverageVariantTest(TestCase):
    """별도 사용자: 입출금 계좌 1 + 카드 1, 거래 · 조회 기록은 테스트마다 만든다"""
    OCT = P(d(2026, 10, 1), d(2026, 10, 30))

    def setUp(self):
        u = self.u = User.objects.create_user(email="fixture-coverage@example.test")
        self.bank_conn = Connection.objects.create(user=u, business_type="bank", organization_code="FIXTURE_BANK", status="connected")
        self.card_conn = Connection.objects.create(user=u, business_type="card", organization_code="FIXTURE_CARD", status="connected")
        self.account = BankAccount.objects.create(user=u, connection=self.bank_conn, source_account_key="fixture:cov-bank",
                                                  account_type="demand")
        self.card = Card.objects.create(user=u, connection=self.card_conn, payment_account=self.account, origin="provider",
                                        source_card_key="fixture:cov-card")

    def sync(self, kind, start, end, status="success", product=None, **extra):
        ok = status == "success"
        return SyncRun.objects.create(
            user=self.u, connection=self.bank_conn if kind == "bank" else self.card_conn,
            card=self.card if kind == "card" else None,
            product_code=product or ("bank_sync" if kind == "bank" else "card_approval_list"), status=status,
            requested_from=start, requested_to=end, covered_from=start if ok else None, covered_to=end if ok else None,
            completed_at=datetime.combine(end, time(23), KST), **extra)

    def both(self, start=d(2026, 10, 1), end=d(2026, 10, 29)):
        self.sync("bank", start, end)
        self.sync("card", start, end)

    def tx(self, day, kind="card"):
        return FinancialTransaction.objects.create(
            user=self.u, source_kind=kind, card=self.card if kind == "card" else None,
            account=self.account if kind == "bank" else None, source_key=f"fixture:cov:{day}:{kind}",
            occurred_on=day, amount=10_000, direction="out", transaction_type="spending",
            confirmed_category_id="FOOD", confirmation_state="confirmed")

    def test_no_transactions_but_success_is_complete(self):
        self.both()
        self.assertFalse(FinancialTransaction.objects.filter(user=self.u).exists())
        self.assertEqual(coverage(self.u, self.OCT)["state"], "complete")

    def test_transactions_without_success_are_not_complete(self):
        self.tx(d(2026, 10, 5))
        self.tx(d(2026, 10, 6), kind="bank")
        self.sync("card", d(2026, 10, 1), d(2026, 10, 29), status="failed")
        self.assertEqual(coverage(self.u, self.OCT)["state"], "none")

    def test_several_runs_merge_to_full_period(self):
        self.sync("bank", d(2026, 10, 1), d(2026, 10, 29))
        self.sync("card", d(2026, 10, 1), d(2026, 10, 10))
        self.sync("card", d(2026, 10, 8), d(2026, 10, 20))   # 겹침
        self.sync("card", d(2026, 10, 21), d(2026, 10, 29))  # 이어짐 (10/20 다음 날)
        got = coverage(self.u, self.OCT)
        self.assertEqual(got["state"], "complete")
        self.assertEqual(len(next(t for t in got["targets"] if t["kind"] == "card")["evidence"]), 3)

    def test_one_day_gap_is_partial(self):
        self.sync("bank", d(2026, 10, 1), d(2026, 10, 29))
        self.sync("card", d(2026, 10, 1), d(2026, 10, 10))
        self.sync("card", d(2026, 10, 12), d(2026, 10, 29))
        got = coverage(self.u, self.OCT)
        self.assertEqual((got["state"], missing(got)["card"]), ("partial", [P(d(2026, 10, 11), d(2026, 10, 12))]))

    def test_bank_complete_card_incomplete(self):
        self.sync("bank", d(2026, 10, 1), d(2026, 10, 29))
        self.sync("card", d(2026, 10, 1), d(2026, 10, 28))  # 마지막 날 하나 부족
        got = coverage(self.u, self.OCT)
        self.assertEqual((got["state"], missing(got)), ("partial", {"account": [], "card": [P(d(2026, 10, 29), d(2026, 10, 30))]}))

    def test_failed_and_other_products_never_count_as_card_approvals(self):
        self.sync("bank", d(2026, 10, 1), d(2026, 10, 29))
        failed = self.sync("card", d(2026, 10, 1), d(2026, 10, 29), status="failed")
        self.assertEqual((failed.covered_from, failed.covered_to), (None, None))  # 요청 범위만 있고 성공 범위는 비어 있음
        self.sync("card", d(2026, 10, 1), d(2026, 10, 29), product="card_bill_list")          # 청구서 성공
        SyncRun.objects.create(user=self.u, connection=self.card_conn, product_code="card_sync", status="success",
                               covered_from=d(2026, 10, 1), covered_to=d(2026, 10, 29))     # 연결 단위 기록
        got = coverage(self.u, self.OCT)
        self.assertEqual((got["state"], missing(got)["card"]), ("partial", [self.OCT]))

    def test_later_failure_keeps_secured_range(self):
        self.both()
        self.sync("card", d(2026, 10, 1), d(2026, 10, 29), status="failed")
        self.sync("bank", d(2026, 10, 1), d(2026, 10, 29), status="failed")
        self.assertEqual(coverage(self.u, self.OCT)["state"], "complete")

    def test_unselected_and_disconnected_are_still_targets(self):
        self.both()
        Card.objects.filter(pk=self.card.pk).update(is_selected=False)
        old_conn = Connection.objects.create(user=self.u, business_type="card", organization_code="OLD_CARD",
                                             status="disconnected", disconnected_at=datetime(2026, 9, 1, tzinfo=KST))
        old = Card.objects.create(user=self.u, connection=old_conn, origin="provider", source_card_key="fixture:old-card")
        got = coverage(self.u, self.OCT)
        self.assertEqual(got["state"], "partial")
        self.assertEqual(next(t for t in got["targets"] if t["id"] == old.pk)["missing"], [self.OCT])

    def test_no_targets_is_not_complete(self):
        empty = User.objects.create_user(email="fixture-empty@example.test")
        self.assertEqual(coverage(empty, self.OCT), {"state": "none", "period": self.OCT, "targets": [], "undetermined": []})

    def test_undetermined_sources_block_complete(self):
        self.both()
        FinancialTransaction.objects.create(
            user=self.u, source_kind="manual", occurred_on=d(2026, 10, 3), amount=5_000, direction="out",
            transaction_type="spending", confirmed_category_id="FOOD", confirmation_state="confirmed")
        got = coverage(self.u, self.OCT)
        self.assertEqual((got["state"], got["undetermined"]),
                         ("partial", [{"kind": "manual_transactions", "id": None, "reason": "no_sync_record"}]))
        Card.objects.create(user=self.u, origin="manual", name="직접 등록")
        self.assertIn("manual_card", [u["reason"] for u in coverage(self.u, self.OCT)["undetermined"]])
