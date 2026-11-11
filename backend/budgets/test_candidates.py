"""고정지출 · 저축 후보 감지 (⑦, budgets/candidates.py) 테스트. 실행: backend 폴더에서 python manage.py test budgets.test_candidates
기준일 TODAY 10/21 — 8 · 9 · 10월 거래가 3달 연속"""
from datetime import date, datetime
from pathlib import Path
from runpy import run_path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from finance.merchant_rules import compare_label
from finance.models import (BankAccount, Card, Connection, FinancialTransaction, PaymentDue, PaymentMatch,
                            RecurringExpense, RecurringSaving, TransactionLink)
from finance.pipeline import process_new_transactions

from .candidates import detect_candidates

TODAY = date(2026, 10, 21)
MONTHS = (8, 9, 10)


class CandidateFixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="cand@example.com", password="Example123!", nickname="김나은")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="a", name="월급통장",
                                                  account_type="demand", is_selected=True)
        self.deposit = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="c", name="첫목돈적금",
                                                  account_type="deposit")
        self.card = Card.objects.create(user=self.user, name="신한", masked_number="****1234", origin="manual")

    def tx(self, on, amount, card=False, kind="spending", label="넷플릭스", **fields):
        data = dict(user=self.user, occurred_on=on, amount=amount, direction="out", transaction_type=kind,
                    confirmed_category_id="CULTURE_LEISURE" if kind == "spending" else "UNCLASSIFIED",
                    confirmation_state="confirmed")
        if card:
            data.update(source_kind="card", card=self.card, merchant_name=label)
        else:
            data.update(source_kind="bank", account=self.account, source_description=label)
        data.update(fields)
        return FinancialTransaction.objects.create(**data)

    def monthly(self, day=8, amount=13_500, months=MONTHS, **fields):
        return [self.tx(date(2026, m, day), amount, **fields) for m in months]

    def detect(self, today=TODAY):
        detect_candidates(self.user, today)

    def expenses(self):
        return list(RecurringExpense.objects.filter(user=self.user, status="suggested"))

    def savings(self):
        return list(RecurringSaving.objects.filter(user=self.user, status="suggested"))


class FixedCandidateTest(CandidateFixture):
    def test_three_months_make_one_candidate(self):
        txs = self.monthly(card=True)
        self.detect()
        [e] = self.expenses()
        self.assertEqual((e.name, e.amount, e.payment_day, e.payment_card_id, e.payment_account_id, e.category_id, e.source_type),
                         ("넷플릭스", 13_500, 8, self.card.pk, None, "CULTURE_LEISURE", "detected"))
        self.assertEqual(e.evidence, [str(t.pk) for t in txs])
        self.assertEqual(self.savings(), [])

    def test_two_months_is_not_enough(self):
        self.monthly(card=True, months=(9, 10))
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_day_outside_three_days(self):
        self.tx(date(2026, 8, 8), 13_500, card=True)
        self.tx(date(2026, 9, 15), 13_500, card=True)
        self.tx(date(2026, 10, 8), 13_500, card=True)
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_amount_outside_ten_percent(self):
        self.tx(date(2026, 8, 8), 10_000, card=True)
        self.monthly(card=True, months=(9, 10))
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_amount_within_ten_percent(self):
        self.tx(date(2026, 8, 8), 12_500, card=True)  # 13,500 의 ±10% 안
        self.monthly(card=True, months=(9, 10))
        self.detect()
        self.assertEqual(len(self.expenses()), 1)

    def test_two_in_one_month(self):
        self.monthly(card=True)
        self.tx(date(2026, 9, 20), 13_500, card=True)
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_last_payment_three_months_ago(self):
        self.monthly(card=True, months=(5, 6, 7))
        self.detect()  # 10/21 기준 마지막이 7월 → 해지한 구독으로 봄
        self.assertEqual(self.expenses(), [])

    def test_travel_lodging_is_not_fixed(self):
        self.monthly(day=24, amount=120_000, card=True, label="포포인츠 구로", confirmed_category_id="TRAVEL_LODGING")
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_transfer_to_other_person_is_fixed(self):
        self.monthly(day=15, amount=500_000, kind="transfer", label="홍길동")
        self.detect()
        [e] = self.expenses()
        self.assertEqual((e.name, e.payment_account_id, e.category_id), ("홍길동", self.account.pk, "ETC"))
        self.assertEqual(self.savings(), [])

    # 받는 곳 이름은 연월 표시를 빼고 비교 (11/10, 11/11 PR #106 리뷰). 후보 이름은 가장 최근 적요 그대로
    def test_year_month_prefix_is_same_group(self):
        for m in MONTHS:
            self.tx(date(2026, m, 10), 22_600, kind="transfer", label=f"26{m - 1:02d}국민건강")
        self.detect()
        [e] = self.expenses()
        self.assertEqual((e.name, e.amount), ("2609국민건강", 22_600))

    def test_month_suffix_is_same_group(self):
        for m in MONTHS:
            self.tx(date(2026, m, 5), 150_000, kind="transfer", label=f"관리비 {m}월")
        self.detect()
        [e] = self.expenses()
        self.assertEqual(e.name, "관리비 10월")

    def test_digits_only_same_number_is_same_group(self):
        self.monthly(day=21, amount=69_000, kind="transfer", label="01012345678")
        self.detect()
        [e] = self.expenses()
        self.assertEqual(e.name, "01012345678")

    def test_digits_only_different_numbers_are_not_grouped(self):
        # [한계] 적요가 숫자뿐인데 달마다 다르면 묶지 않는다 — 사용자가 직접 등록
        for m in MONTHS:
            self.tx(date(2026, m, 21), 69_000, kind="transfer", label=f"2026{m:02d}0001")
        self.detect()
        self.assertEqual(self.expenses(), [])

    # 번호만 다른 받는 곳(휴대폰 두 회선 · 계약 번호)은 따로 본다 (PR #106 리뷰 1)
    def two_lines(self):
        for m in MONTHS:
            self.tx(date(2026, m, 15), 55_000, kind="transfer", label="01011112222SKT")
            self.tx(date(2026, m, 15), 33_000, kind="transfer", label="01033334444SKT")

    def test_two_phone_lines_are_two_candidates(self):
        self.two_lines()
        self.detect()
        self.assertEqual(sorted((e.name, e.amount) for e in self.expenses()),
                         [("01011112222SKT", 55_000), ("01033334444SKT", 33_000)])

    def test_registered_line_does_not_block_other_line(self):
        RecurringExpense.objects.create(user=self.user, status="confirmed", source_type="manual", name="01011112222SKT",
                                        amount=55_000, payment_day=15, category_id="ETC", payment_account=self.account)
        self.two_lines()
        self.detect()
        self.assertEqual([e.name for e in self.expenses()], ["01033334444SKT"])


class SavingCandidateTest(CandidateFixture):
    def own_transfer(self, m):
        out = self.tx(date(2026, m, 2), 900_000, kind="own_transfer", label="적금이체")
        income = FinancialTransaction.objects.create(
            user=self.user, source_kind="bank", account=self.deposit, occurred_on=date(2026, m, 2), amount=900_000,
            direction="in", transaction_type="own_transfer", confirmed_category_id="UNCLASSIFIED", confirmation_state="confirmed")
        TransactionLink.objects.create(user=self.user, from_transaction=out, to_transaction=income, relation_type="OWN_TRANSFER",
                                       matched_amount=900_000, confirmation_state="confirmed")
        return out

    def test_own_transfer_is_saving_with_to_account(self):
        txs = [self.own_transfer(m) for m in MONTHS]
        self.detect()
        [s] = self.savings()
        self.assertEqual((s.name, s.amount, s.transfer_day, s.from_account_id, s.to_account_id),
                         ("첫목돈적금", 900_000, 2, self.account.pk, self.deposit.pk))
        self.assertEqual(s.evidence, [str(t.pk) for t in txs])
        self.assertEqual(self.expenses(), [])

    def test_unlinked_account_with_my_name_is_saving(self):
        self.monthly(day=5, amount=300_000, kind="transfer", label="김 나은")  # 띄어쓰기 무시
        self.detect()
        [s] = self.savings()
        self.assertEqual((s.name, s.to_account_id, s.transfer_day), ("김 나은", None, 5))

    def test_saving_word_is_saving(self):
        self.monthly(day=10, amount=200_000, kind="transfer", label="청년적금 납입")
        self.detect()
        self.assertEqual([s.name for s in self.savings()], ["청년적금 납입"])

    def test_saving_needs_same_amount(self):
        self.tx(date(2026, 8, 10), 210_000, kind="transfer", label="청년적금 납입")  # 저축은 ±10% 아닌 같은 금액
        self.monthly(day=10, amount=200_000, kind="transfer", label="청년적금 납입", months=(9, 10))
        self.detect()
        self.assertEqual(self.savings(), [])

    def ignore(self, saving):
        self.client.force_login(self.user)
        res = self.client.post("/api/budgets/savings/ignore/", {"savingIds": [str(saving.pk)]}, content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)

    def test_ignore_name_transfer_keeps_transfer(self):
        from finance.api import listed_transactions
        txs = self.monthly(day=5, amount=300_000, kind="transfer", label="김 나은")
        self.detect()
        [s] = self.savings()
        self.ignore(s)
        ids = [t.pk for t in txs]
        self.assertEqual(set(FinancialTransaction.objects.filter(pk__in=ids).values_list("transaction_type", flat=True)), {"transfer"})
        self.assertEqual(listed_transactions(self.user).filter(pk__in=ids).count(), 3)  # 거래 목록에 그대로 보임
        self.detect()
        self.assertEqual(RecurringSaving.objects.filter(user=self.user).count(), 1)  # [아니에요]한 묶음은 다시 안 생김

    def test_ignore_own_transfer_stays_own_transfer(self):
        txs = [self.own_transfer(m) for m in MONTHS]
        self.detect()
        [s] = self.savings()
        self.ignore(s)
        self.assertEqual(set(FinancialTransaction.objects.filter(pk__in=[t.pk for t in txs]).values_list("transaction_type", flat=True)),
                         {"own_transfer"})
        self.detect()
        self.assertEqual(RecurringSaving.objects.filter(user=self.user).count(), 1)

    def test_list_puts_deposit_first(self):
        self.monthly(day=5, amount=300_000, kind="transfer", label="김나은")      # 받는 계좌 모름
        for m in MONTHS:
            self.own_transfer(m)                                                    # 예적금으로
        self.detect()
        self.client.force_login(self.user)
        items = self.client.get("/api/budgets/savings/").json()["items"]
        self.assertEqual([i["name"] for i in items], ["첫목돈적금", "김나은"])


class DuplicateAndExclusionTest(CandidateFixture):
    def test_pipeline_twice_one_candidate(self):
        txs = self.monthly(card=True)
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            process_new_transactions(self.user, [t.pk for t in txs])
            process_new_transactions(self.user, [t.pk for t in txs])
        self.assertEqual(len(self.expenses()), 1)

    def test_existing_in_any_state_blocks_same_group(self):
        for status in ("ignored", "confirmed", "stopped"):
            with self.subTest(status=status):
                RecurringExpense.objects.filter(user=self.user).delete()
                FinancialTransaction.objects.filter(user=self.user).delete()
                RecurringExpense.objects.create(user=self.user, name="넷플릭스", amount=13_500, category_id="CULTURE_LEISURE",
                                                status=status, source_type="detected", payment_card=self.card)
                self.monthly(card=True)
                self.detect()
                self.assertEqual(RecurringExpense.objects.filter(user=self.user).count(), 1)

    def test_renamed_existing_blocked_by_evidence_merchant(self):
        old = self.tx(date(2026, 6, 8), 13_500, card=True)
        RecurringExpense.objects.create(user=self.user, name="OTT 구독", amount=13_500, category_id="CULTURE_LEISURE",
                                        status="ignored", source_type="detected", payment_card=self.card, evidence=[str(old.pk)])
        self.monthly(card=True)
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_card_payment_is_not_candidate(self):
        self.monthly(day=14, amount=140_000, kind="card_payment", label="신한카드")
        self.detect()
        self.assertEqual((self.expenses(), self.savings()), ([], []))

    def test_linked_transaction_is_left_out(self):
        txs = self.monthly(day=14, amount=140_000, kind="transfer", label="홍길동")
        other = RecurringExpense.objects.create(user=self.user, name="월세", amount=140_000, category_id="HOUSING_TELECOM",
                                                status="confirmed", source_type="manual", payment_account=self.account)
        due = PaymentDue.objects.create(user=self.user, obligation_type="recurring", recurring=other, occurrence_key="r:9",
                                        due_date=date(2026, 9, 14), confirmed_amount=140_000, source_type="manual",
                                        confirmation_state="confirmed", payment_state="paid", paid_amount=140_000)
        PaymentMatch.objects.create(user=self.user, due=due, transaction=txs[1], matched_amount=140_000)
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_canceled_transaction_breaks_run(self):
        txs = self.monthly(card=True)
        FinancialTransaction.objects.filter(pk=txs[1].pk).update(is_canceled=True)
        self.detect()
        self.assertEqual(self.expenses(), [])

    def test_loan_candidate_is_left_out(self):
        self.monthly(day=20, amount=300_000, kind="transfer", label="캐피탈", field_states={"loan_repayment_candidate": True})
        self.detect()
        self.assertEqual(self.expenses(), [])


ROOT = Path(__file__).resolve().parents[2]
ALPHA = ROOT / "local_workspace/scripts/alpha_test/seed.py"


class AlphaOnboardingTest(TestCase):
    """알파 온보딩 계정(고정지출 · 저축 설정 없음) — 시드처럼 은행 거래를 transfer · income 으로 넣고
    실제 첫 연결처럼 파이프라인 전체(① 유형 판단 → … → ⑦ 감지)를 돌린다 (seed.run_onboarding_pipeline, 11/4)"""

    @classmethod
    def setUpTestData(cls):
        seed = run_path(str(ALPHA))
        as_of = datetime(2026, 11, 5, 12, tzinfo=ZoneInfo("Asia/Seoul"))
        rows, *_ = seed["plan"](as_of)
        models = {m.__name__: m for app in ("users", "finance", "budgets") for m in apps.get_app_config(app).get_models()}
        for name, pk, fields in rows:
            models[name](pk=pk, **fields).save(force_insert=True)
        seed["run_onboarding_pipeline"](models, as_of)
        cls.user = models["User"].objects.get(pk=seed["uid"]("onboarding", "user"))
        cls.uid = seed["uid"]

    def test_fixed_candidates(self):
        names = sorted(RecurringExpense.objects.filter(user=self.user, status="suggested").values_list("name", flat=True))
        print("\n[페르소나 고정지출 후보]", names)
        for expected in ("월세", "관리비", "전기·가스·수도 합산", "휴대폰 요금"):
            self.assertIn(expected, names)
        self.assertTrue(any("넷플릭스" in n for n in names) and any("멜론" in n for n in names), names)
        self.assertIn("지인 단기 대여", names)                            # 지인에게 보낸 이체 반복은 그대로 후보
        self.assertFalse(any("포포인츠" in n for n in names), names)      # 호텔(여행·숙박)은 빠짐

    def test_saving_candidates_deposit_first(self):
        self.client.force_login(self.user)
        items = self.client.get("/api/budgets/savings/").json()["items"]
        print("\n[페르소나 저축 후보]", [(i["name"], i["amount"], i["transferDay"]) for i in items])
        to_ids = [str(s.to_account_id) for s in RecurringSaving.objects.filter(user=self.user)]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["name"], "합성 첫목돈적금")
        self.assertIn(str(self.uid("onboarding", "asset", "B")), to_ids)


class ReviewFlowTest(CandidateFixture):
    """PR #80 리뷰: 감지한 후보를 실제 API 순서로 확정 · 거절"""

    def test_account_candidate_confirm_links_this_cycle_withdrawal(self):
        from users.models import FinancialProfile
        from .home import home_data
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        rents = self.monthly(day=10, amount=700_000, kind="transfer", label="월세")
        self.detect()
        [e] = self.expenses()
        self.client.force_login(self.user)
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            res = self.client.post("/api/budgets/fixed/confirm/", {"recurringIds": [str(e.pk)]}, content_type="application/json")
            self.assertEqual(res.status_code, 200, res.content)
            must_pay = home_data(self.user, TODAY)["must_pay"]
            item = next(i for i in self.client.get("/api/budgets/fixed/").json()["items"] if i["recurringId"] == str(e.pk))
        october = rents[-1]
        self.assertTrue(PaymentMatch.objects.filter(transaction=october, due__recurring=e).exists())
        self.assertEqual(PaymentDue.objects.get(recurring=e, due_date=date(2026, 10, 10)).payment_state, "paid")
        self.assertEqual(item["paidThisCycle"]["transactionId"], str(october.pk))
        self.assertIn("paid", [d["paymentState"] for d in item["recentDues"]])
        self.assertNotIn(str(e.pk), [m["sourceId"] for m in must_pay])

    def test_ignore_saving_keeps_user_spending(self):
        from finance.api import listed_transactions
        txs = self.monthly(day=5, amount=300_000, kind="transfer", label="김나은")
        self.detect()
        [s] = self.savings()
        self.client.force_login(self.user)
        res = self.client.patch(f"/api/finance/transactions/{txs[0].pk}/", {"category": "FOOD"}, content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)  # 사용자가 소비(식비)로 분류
        res = self.client.post("/api/budgets/savings/ignore/", {"savingIds": [str(s.pk)]}, content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        mine = FinancialTransaction.objects.get(pk=txs[0].pk)
        self.assertEqual((mine.transaction_type, mine.classification_source, mine.confirmed_category_id), ("spending", "user", "FOOD"))
        self.assertTrue(listed_transactions(self.user).filter(pk=mine.pk).exists())
        self.assertEqual(set(FinancialTransaction.objects.filter(pk__in=[t.pk for t in txs[1:]]).values_list("transaction_type", flat=True)),
                         {"transfer"})


class CompareLabelTest(SimpleTestCase):
    """받는 곳 비교 이름 (finance/merchant_rules.compare_label): 연월 표시만 빼고 다른 숫자는 남긴다"""

    def test_year_month_removed_other_numbers_kept(self):
        cases = {
            "2606국민건강": "국민건강", "202606국민건강": "국민건강", "국민건강 2606": "국민건강",
            "관리비 10월": "관리비", "2026년 11월 관리비": "관리비",
            "01011112222SKT": "01011112222SKT", "KT인터넷 12345678": "KT인터넷 12345678",
            "카페 1204": "카페 1204", "2580마트": "2580마트", "2606": "2606", "넷플릭스": "넷플릭스",
            "  ": None, None: None,
        }
        for name, expected in cases.items():
            with self.subTest(name):
                self.assertEqual(compare_label(name), expected)
