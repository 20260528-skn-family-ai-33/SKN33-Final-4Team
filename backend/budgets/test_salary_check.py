"""R12 급여 인식: 후보 → 사용자 확인 → 같은 패턴 자동 인정. TODAY 10/21 · 급여일 25일 · 실수령액 250만
이번 회차(9/25) 구간 9/18 ~ 10/17, 다음 회차(10/25) 조기 구간 10/18 ~ 오늘"""
import threading
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from finance.models import BankAccount, Connection, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense, TransactionLink
from .models import Budget, BudgetCycle, SalaryDecision
from .salary import memo_key, salary_reviews
from .test_home import TODAY, HomeFixture

URL = "/api/budgets/salary/decisions/"
SEPT, OCT, NOV = date(2026, 9, 25), date(2026, 10, 25), date(2026, 11, 25)


class MemoKeyTest(SimpleTestCase):
    def same(self, a, b):
        return memo_key(a)[0] is not None and memo_key(a)[0] == memo_key(b)[0]

    def test_exact_after_space_and_case_cleanup(self):
        self.assertTrue(self.same("(주)머니쿠션", " (주) 머니쿠션 "))
        self.assertTrue(self.same("홍길동", "홍길동"))            # 사장님 이름도 기준이 된다
        self.assertTrue(self.same("MoneyCushion", "moneycushion"))
        self.assertFalse(self.same("홍길동", "홍길순"))
        self.assertFalse(self.same("머니쿠션", "머니쿠션 환급"))  # 부분 일치는 안 됨

    def test_month_and_year_change_only_in_explicit_format(self):
        self.assertTrue(self.same("11월 월급", "12월 월급"))
        self.assertTrue(self.same("2026년 12월 급여", "2027년 1월 급여"))
        self.assertFalse(self.same("11월 월급", "12월 급여"))          # 낱말이 다르면 다른 패턴
        self.assertFalse(self.same("11월 월급", "2026년 12월 월급"))   # 연도 유무가 다르면 다른 패턴
        self.assertFalse(self.same("머니쿠션11", "머니쿠션12"))        # 다른 숫자는 지우지 않는다
        self.assertFalse(self.same("13월 월급", "12월 월급"))

    def test_weak_memos_cannot_be_patterns(self):
        for memo in ("", None, "  ", "입금", "이체", "급여", "월급", "11월", "2026년 11월"):
            with self.subTest(memo=memo):
                key, reason = memo_key(memo)
                self.assertIsNone(key)
                self.assertTrue(reason)
        self.assertEqual(memo_key("머니쿠션"), (("exact", "머니쿠션"), None))


class SalaryCheckFixture(HomeFixture):
    def setUp(self):
        super().setUp()
        self.today = TODAY

    def tx(self, on, amount=2_500_000, memo="머니쿠션", **fields):
        return self.make_tx(on, amount, source_description=memo, **fields)

    def post(self, ids, decision="confirmed", use_pattern=None, today=None, client=None):
        body = {"transactionIds": [str(getattr(i, "pk", i)) for i in ids], "decision": decision}
        if use_pattern is not None:
            body["usePattern"] = use_pattern
        with patch("django.utils.timezone.localdate", return_value=today or self.today):
            return (client or self.client).post(URL, body, format="json")

    def period(self, today=None):
        return self.home(today or self.today)["period"]

    def state(self, today=None):
        return self.period(today)["salary_state"]

    def decision(self, tx):
        return SalaryDecision.objects.filter(transaction=tx).values_list("state", "pay_cycle_on").first()

    def learn(self, memo="머니쿠션", on=SEPT, amount=2_500_000):
        """9/25 회차를 사용자 확인 + 자동 인식 기준으로"""
        tx = self.tx(on, amount, memo)
        self.assertEqual(self.post([tx], use_pattern=True).status_code, 200)
        return tx


class FirstConfirmTest(SalaryCheckFixture):
    def test_first_single_candidate_needs_check_until_confirmed(self):
        tx = self.tx(SEPT)
        self.assertEqual(self.state(), "needs_check")
        self.assertNotIn(tx.pk, salary_reviews(self.user))
        res = self.post([tx], use_pattern=True)
        self.assertEqual(res.status_code, 200)
        item = res.json()["windows"][0]["items"][0]
        self.assertEqual((item["decision"], item["isPattern"], item["account"]["displayName"][-4:]), ("confirmed", True, "5678"))
        period = self.period()
        self.assertEqual((period["salary_state"], period["salary_source"], period["salary_amount"]), ("received", "user", 2_500_000))
        self.assertEqual(salary_reviews(self.user), {tx.pk: "confirmed"})

    def test_multiple_candidates_are_not_picked_automatically(self):
        self.tx(date(2026, 9, 24)), self.tx(SEPT, memo="환급")
        self.assertEqual((self.state(), self.period()["salary_check_count"]), ("needs_check", 2))
        self.assertEqual(SalaryDecision.objects.count(), 0)

    def test_small_salary_outside_candidates_can_be_picked(self):
        small = self.tx(SEPT, 900_000)
        self.assertEqual(self.state(), "waiting")  # 80% 미만이라 후보는 아님
        self.assertEqual(self.post([small]).status_code, 200)
        self.assertEqual((self.state(), self.period()["salary_amount"]), ("received", 900_000))

    def test_split_salary_manual_sum_and_repeat_is_same(self):
        a, b = self.tx(SEPT, 1_500_000), self.tx(date(2026, 9, 26), 1_000_000)
        self.assertEqual(self.state(), "waiting")  # 합계가 비슷해도 자동 조합하지 않음
        self.assertEqual(self.post([a, b], use_pattern=True).json()["error"]["fields"]["usePattern"][0],
                         "분할 급여는 자동 인식 기준으로 쓸 수 없어요")
        for _ in range(2):
            self.assertEqual(self.post([a, b]).status_code, 200)
        self.assertEqual(self.period()["salary_amount"], 2_500_000)
        self.assertEqual(SalaryDecision.objects.count(), 2)
        self.assertEqual(BudgetCycle.objects.get(starts_on=SEPT).confirmed_income, 2_500_000)
        self.assertEqual(self.post([a]).status_code, 200)  # 회차 판단을 목록으로 바꿈 → b 는 판단 없음
        self.assertEqual((self.period()["salary_amount"], self.decision(b)), (1_500_000, None))

    def test_weak_memo_manual_only_with_reason(self):
        for memo in ("", "입금", "11월"):
            with self.subTest(memo=memo):
                tx = self.tx(SEPT, memo=memo)
                res = self.post([tx], use_pattern=True)
                self.assertEqual(res.status_code, 400)
                self.assertIn("자동 인식 기준으로 쓸 수 없어요", res.json()["error"]["fields"]["usePattern"][0])
                self.assertEqual(self.post([tx]).status_code, 200)
                self.assertEqual(self.post([tx], "none").status_code, 200)

    def test_registered_income_and_approved_budget_kept(self):
        cycle = BudgetCycle.objects.create(user=self.user, starts_on=SEPT, ends_before=OCT)
        budget = Budget.objects.create(user=self.user, cycle=cycle, status="approved", version_no=1, funding_amount=2_500_000,
                                       approved_at=timezone.now(), engine_version="temp-fallback-1")
        self.post([self.tx(SEPT, 2_800_000)])
        self.profile.refresh_from_db(), budget.refresh_from_db()
        self.assertEqual((self.profile.monthly_take_home_income, budget.status, budget.funding_amount), (2_500_000, "approved", 2_500_000))


class AmountTolerantAutoTest(SalaryCheckFixture):
    """11/13: 식대 · 수당으로 매달 금액이 달라도 같은 계좌 · 같은 적요면 자동 인정 (기준 월급의 절반 이상만)"""
    NEXT = date(2026, 11, 21)

    def test_different_amount_same_pattern_is_auto(self):
        self.learn()
        nxt = self.tx(OCT, 2_620_000)
        period = self.period(self.NEXT)
        self.assertEqual((period["salary_state"], period["salary_source"], period["salary_amount"]), ("received", "auto", 2_620_000))
        self.assertEqual(self.decision(nxt), ("auto", OCT))

    def test_less_than_half_of_pattern_is_not_auto(self):
        self.learn()
        small = self.tx(OCT, 1_249_999)
        self.assertEqual(self.state(self.NEXT), "waiting")  # 80% 미만이라 후보도 아님
        self.assertIsNone(self.decision(small))
        exact_half = self.tx(date(2026, 10, 26), 1_250_000)
        self.assertEqual(self.state(self.NEXT), "received")
        self.assertEqual(self.decision(exact_half), ("auto", OCT))


class SplitConfirmTurnsOffPatternTest(SalaryCheckFixture):
    """11/13: 여러 건을 월급으로 확인하면 1건 기준을 끈다 (다음 달 일부만 자동 인정 → 잘못된 '덜 들어왔어요' 방지)"""
    NEXT = date(2026, 11, 21)

    def test_split_confirm_turns_off_old_pattern(self):
        self.learn()
        base = self.tx(OCT, 2_000_000)
        meal = self.tx(date(2026, 10, 26), 200_000, memo="식대")
        self.assertEqual(self.post([base, meal], today=self.NEXT).status_code, 200)
        self.assertFalse(SalaryDecision.objects.filter(user=self.user, is_pattern=True).exists())
        self.assertEqual(self.period(self.NEXT)["salary_amount"], 2_200_000)
        self.assertEqual(SalaryDecision.objects.filter(user=self.user, state="confirmed", pay_cycle_on=SEPT).count(), 1)  # 지난 기록은 그대로

    def test_single_confirm_without_pattern_keeps_old_pattern(self):
        self.learn()
        tx = self.tx(OCT, 2_500_000, memo="보너스")
        self.post([tx], today=self.NEXT)
        self.assertTrue(SalaryDecision.objects.filter(user=self.user, is_pattern=True).exists())


class SalaryGapTest(SalaryCheckFixture):
    """11/13 월급 차이 안내: 받은 월급이 등록 실수령액(250만)과 1만원 이상 · 3% 이상 다르면 period.salary_gap (답은 apply · keep 둘)"""
    NEXT = date(2026, 11, 21)
    GAP_URL = "/api/budgets/salary/gap/"

    def answer(self, answer, amount, today=None):
        with patch("django.utils.timezone.localdate", return_value=today or self.NEXT):
            return self.client.post(self.GAP_URL, {"answer": answer, "salaryAmount": amount}, format="json")

    def test_small_difference_is_ignored(self):
        self.learn()
        for amount in (2_490_001, 2_430_000):  # 1만원 미만 · 3% 미만(7만원 = 2.8%)
            with self.subTest(amount=amount):
                tx = self.tx(OCT, amount)
                self.assertIsNone(self.period(self.NEXT)["salary_gap"])
                tx.delete()

    def test_less_and_more_salary_are_reported(self):
        self.learn()
        tx = self.tx(OCT, 2_200_000)
        gap = self.period(self.NEXT)["salary_gap"]
        self.assertEqual(gap, {"diff": -300_000, "received": 2_200_000, "registered": 2_500_000, "basis": None})
        tx.delete()
        self.tx(OCT, 2_800_000)
        self.assertEqual(self.period(self.NEXT)["salary_gap"]["diff"], 300_000)

    def test_apply_uses_actual_salary_for_this_cycle_only(self):
        from .api import protect_json
        self.profile.housing_type = "family"
        self.profile.save()
        self.learn()
        self.tx(OCT, 2_200_000)
        self.period(self.NEXT)
        res = self.answer("apply", 2_200_000)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"incomeBasis": 2_200_000, "salaryAmount": 2_200_000})
        cycle = BudgetCycle.objects.get(starts_on=OCT)
        self.assertEqual((cycle.income_basis, cycle.income_answered_for), (2_200_000, 2_200_000))
        self.assertIsNone(self.period(self.NEXT)["salary_gap"])  # 같은 금액으로는 다시 묻지 않는다
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.monthly_take_home_income, 2_500_000)  # 등록 실수령액은 그대로
        base = protect_json(self.user, self.profile, self.NEXT)["base"]
        self.assertEqual((base["take_home_income"], base["income_source"]), (2_200_000, "actual"))
        self.assertEqual(self.answer("apply", 2_200_000).status_code, 200)  # 같은 답을 다시 보내도 성공

    def test_cancelling_salary_confirmation_drops_actual_basis(self):
        """11/13: 받은 금액으로 다시 짠 뒤 월급 확인을 취소하면 등록 실수령액 기준으로 돌아간다 (지킬 돈이 0원 월급으로 막히지 않게)"""
        from .api import protect_json
        self.profile.housing_type = "family"
        self.profile.save()
        wrong = self.tx(OCT, 305_400, memo="중고거래")
        self.assertEqual(self.post([wrong], today=self.NEXT).status_code, 200)
        self.period(self.NEXT)
        self.assertEqual(self.answer("apply", 305_400).status_code, 200)
        self.assertEqual(BudgetCycle.objects.get(starts_on=OCT).income_basis, 305_400)
        res = self.post([wrong], decision="none", today=self.NEXT)  # 확인 취소
        self.assertEqual((res.status_code, res.json()["basisReset"]), (200, True))  # 화면이 '다시 짤까요?'를 묻는 근거
        cycle = BudgetCycle.objects.get(starts_on=OCT)
        self.assertEqual((cycle.income_basis, cycle.income_answered_for, cycle.confirmed_income), (None, None, None))
        base = protect_json(self.user, self.profile, self.NEXT)["base"]
        self.assertEqual((base["take_home_income"], base["income_source"]), (2_500_000, "registered"))

    def test_plain_cancel_without_actual_basis_does_not_ask(self):
        tx = self.tx(OCT, 2_500_000)
        self.post([tx], today=self.NEXT)
        res = self.post([tx], decision="none", today=self.NEXT)
        self.assertEqual((res.status_code, res.json()["basisReset"]), (200, False))

    def test_keep_only_stops_asking(self):
        self.learn()
        salary = self.tx(OCT, 2_800_000)
        self.period(self.NEXT)
        self.assertEqual(self.answer("keep", 2_800_000).json(), {"incomeBasis": None, "salaryAmount": 2_800_000})
        self.assertIsNone(self.period(self.NEXT)["salary_gap"])
        bonus = self.tx(date(2026, 10, 26), 100_000, memo="상여")  # 사용자가 상여까지 월급으로 확인 → 받은 금액이 바뀌면 다시 묻는다
        self.assertEqual(self.post([salary, bonus], today=self.NEXT).status_code, 200)
        self.assertEqual(self.period(self.NEXT)["salary_gap"]["received"], 2_900_000)

    def test_answer_validation(self):
        self.learn()
        self.tx(OCT, 2_200_000)
        self.period(self.NEXT)
        stale = self.answer("apply", 2_300_000)
        self.assertEqual(stale.status_code, 400)
        self.assertIn("salaryAmount", stale.json()["error"]["fields"])
        self.assertIsNone(BudgetCycle.objects.get(starts_on=OCT).income_basis)
        for body in ({"answer": "spare", "salaryAmount": 2_200_000}, {"answer": "apply"},
                     {"answer": "apply", "salaryAmount": "2200000"}, {"answer": "apply", "salaryAmount": 2_200_000, "userId": 1}):
            with self.subTest(body=body), patch("django.utils.timezone.localdate", return_value=self.NEXT):
                self.assertEqual(self.client.post(self.GAP_URL, body, format="json").status_code, 400)

    def test_apply_expires_drafts(self):
        self.learn()
        self.tx(OCT, 2_200_000)
        self.period(self.NEXT)
        cycle = BudgetCycle.objects.get(starts_on=OCT)
        draft = Budget.objects.create(user=self.user, cycle=cycle, status="validated", version_no=1, funding_amount=2_500_000)
        self.answer("apply", 2_200_000)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "expired")


class SalaryChangeOverActualBasisTest(SalaryCheckFixture):
    """11/13 검수: 받은 월급(220만)으로 이번 주기를 다시 짠 뒤 월급 설정에서 실수령액을 바꿀 때.
    이번 주기부터면 둘 중 무엇을 쓸지 고른다(keepIncomeBasis) — 안 고르면 받은 월급 기준이 계속 이겨 화면끼리 달라진다"""
    NEXT, SALARY_URL = SalaryGapTest.NEXT, "/api/budgets/salary/"
    answer = SalaryGapTest.answer
    GAP_URL = SalaryGapTest.GAP_URL

    def setUp(self):
        super().setUp()
        self.profile.housing_type = "family"
        self.profile.save()
        self.learn()
        self.tx(OCT, 2_200_000)
        self.period(self.NEXT)
        self.assertEqual(self.answer("apply", 2_200_000).status_code, 200)

    def put(self, **body):
        with patch("django.utils.timezone.localdate", return_value=self.NEXT):
            return self.client.put(self.SALARY_URL, {"takeHomeIncome": 3_000_000, "payday": 25, **body}, format="json")

    def base(self):
        from .api import protect_json
        self.profile.refresh_from_db()
        base = protect_json(self.user, self.profile, self.NEXT)["base"]
        return base["take_home_income"], base["income_source"]

    def basis(self):
        return BudgetCycle.objects.get(starts_on=OCT).income_basis

    def test_get_shows_actual_basis(self):
        with patch("django.utils.timezone.localdate", return_value=self.NEXT):
            self.assertEqual(self.client.get(self.SALARY_URL).json()["incomeBasis"], 2_200_000)

    def test_this_cycle_change_needs_basis_choice_and_saves_nothing(self):
        res = self.put(applyFrom="current_cycle")
        self.assertEqual(res.status_code, 400)
        self.assertIn("keepIncomeBasis", res.json()["error"]["fields"])
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.monthly_take_home_income, self.basis()), (2_500_000, 2_200_000))
        for value in ("true", 1, None):
            with self.subTest(value=value):
                self.assertEqual(self.put(applyFrom="current_cycle", keepIncomeBasis=value).status_code, 400)

    def test_switch_to_new_salary_drops_actual_basis(self):
        res = self.put(applyFrom="current_cycle", keepIncomeBasis=False)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["takeHomeIncome"], res.json()["incomeBasis"]), (3_000_000, None))
        self.assertEqual(self.base(), (3_000_000, "registered"))
        self.assertIsNone(self.period(self.NEXT)["salary_gap"])  # 같은 받은 금액으로는 다시 묻지 않는다

    def test_keep_actual_basis_changes_registered_only(self):
        res = self.put(applyFrom="current_cycle", keepIncomeBasis=True)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["takeHomeIncome"], res.json()["incomeBasis"]), (3_000_000, 2_200_000))
        self.assertEqual(self.base(), (2_200_000, "actual"))

    def test_next_cycle_change_keeps_basis_without_choice(self):
        res = self.put(applyFrom="next_cycle")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.base(), (2_200_000, "actual"))

    def test_payday_change_this_cycle_cannot_keep_basis(self):
        # 11/13 재검수: 급여일도 이번 주기부터 바꾸면 주기를 새로 만든다 → 받은 월급 기준은 이어 갈 수 없어 새 월급으로만
        res = self.put(payday=10, applyFrom="current_cycle", keepIncomeBasis=True)
        self.assertEqual(res.status_code, 400)
        self.assertIn("keepIncomeBasis", res.json()["error"]["fields"])
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.monthly_take_home_income, self.profile.payday, self.basis()), (2_500_000, 25, 2_200_000))
        res = self.put(payday=10, applyFrom="current_cycle")  # 고르지 않아도 된다
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual((res.json()["payday"], res.json()["incomeBasis"]), (10, None))
        self.assertEqual(self.base(), (3_000_000, "registered"))

    def test_before_first_approval_also_needs_basis_choice(self):
        # 첫 예산 승인 전에는 applyFrom 없이 이번 주기로 바로 바꾼다 — 받은 월급 기준이 있으면 그래도 고른다
        res = self.put()
        self.assertEqual(res.status_code, 400)
        self.assertIn("keepIncomeBasis", res.json()["error"]["fields"])
        res = self.put(keepIncomeBasis=True)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.base(), (2_200_000, "actual"))

    def test_new_salary_same_as_received_needs_no_choice(self):
        res = self.put(takeHomeIncome=2_200_000, applyFrom="current_cycle")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertIsNone(self.basis())
        self.assertEqual(self.base(), (2_200_000, "registered"))


class AutoRecognizeTest(SalaryCheckFixture):
    NEXT = date(2026, 11, 21)  # 10/25 회차 (구간 10/18 ~ 11/17)

    def test_same_pattern_next_cycle_is_auto(self):
        self.learn()
        nxt = self.tx(OCT)
        period = self.period(self.NEXT)
        self.assertEqual((period["salary_state"], period["salary_source"]), ("received", "auto"))
        self.assertEqual(self.decision(nxt), ("auto", OCT))
        self.assertEqual(BudgetCycle.objects.get(starts_on=OCT).confirmed_income, 2_500_000)
        self.assertEqual(salary_reviews(self.user)[nxt.pk], "auto")
        self.assertFalse(SalaryDecision.objects.get(transaction=nxt).is_pattern)  # 자동 인정은 기준을 바꾸지 않음

    def test_month_and_year_memo_patterns(self):
        self.learn("9월 월급")
        self.tx(OCT, memo="10월 월급")
        self.assertEqual(self.state(self.NEXT), "received")

    def test_year_change_with_year_memo(self):
        self.today = date(2026, 12, 28)
        self.learn("2026년 12월 급여", on=date(2026, 12, 25))
        jan = self.tx(date(2027, 1, 25), memo="2027년 1월 급여")
        self.assertEqual(self.state(date(2027, 1, 26)), "received")
        self.assertEqual(self.decision(jan), ("auto", date(2027, 1, 25)))

    def test_changed_memo_amount_account_need_check(self):
        self.learn()
        other = self.make_account("k9", masked_number="****0000")
        # 11/13: 금액은 '기준의 절반 이상'이면 같은 패턴 (금액 조건은 아래 AmountTolerantAutoTest)
        for label, kwargs in (("문구", {"memo": "10월 월급"}), ("계좌", {"account": other})):
            with self.subTest(label):
                tx = self.tx(OCT, **kwargs)
                self.assertEqual(self.state(self.NEXT), "needs_check")
                tx.delete()

    def test_unconfirmed_big_refund_not_auto(self):
        self.learn()
        self.tx(date(2026, 10, 30), 5_000_000, memo="국세환급")
        self.assertEqual(self.state(self.NEXT), "needs_check")

    def test_late_same_pattern_keeps_deposit_closest_to_payday(self):
        """11/13: 같은 회사 경비가 말일에 따로 들어와도 월급날에 가장 가까운 입금만 월급"""
        self.learn()
        first = self.tx(OCT)
        self.assertEqual(self.state(self.NEXT), "received")
        late = self.tx(date(2026, 11, 1))  # 늦게 들어온 같은 패턴 입금 (경비 등)
        self.assertEqual(self.state(self.NEXT), "received")
        self.assertEqual((self.decision(first), self.decision(late)), (("auto", OCT), None))
        self.assertEqual(BudgetCycle.objects.get(starts_on=OCT).confirmed_income, 2_500_000)

    def test_two_matches_same_distance_need_check_and_undo_auto(self):
        self.learn()
        first = self.tx(date(2026, 10, 23))
        self.assertEqual(self.state(self.NEXT), "received")
        self.tx(date(2026, 10, 27))  # 월급날(10/25)에서 같은 거리 → 어느 쪽인지 모름
        self.assertEqual(self.state(self.NEXT), "needs_check")
        self.assertEqual(self.decision(first), ("recheck", OCT))  # 다른 후보로 갈아 붙이지 않고 재확인 대상으로
        self.assertIsNone(BudgetCycle.objects.get(starts_on=OCT).confirmed_income)

    def test_user_confirmed_cycle_gets_no_auto_addition(self):
        self.learn()
        manual = self.tx(date(2026, 10, 24), 300_000, memo="보너스")
        self.post([manual], today=self.NEXT)
        self.tx(OCT)
        self.state(self.NEXT)
        self.assertEqual(self.period(self.NEXT)["salary_amount"], 300_000)

    def test_early_salary_auto_goes_to_next_cycle(self):
        self.learn()
        early = self.tx(date(2026, 10, 19))
        period = self.period()
        self.assertEqual((period["start_date"], period["next_salary_early_on"]), ("2026-09-25", "2026-10-19"))
        self.assertEqual(self.decision(early), ("auto", OCT))
        self.assertEqual(BudgetCycle.objects.get(starts_on=SEPT).confirmed_income, 2_500_000)  # 이번 주기 금액은 그대로

    def test_month_end_payday(self):
        self.profile.payday = 31
        self.profile.save()
        self.today = date(2026, 10, 31)
        self.learn(on=date(2026, 10, 31))
        nov = self.tx(date(2026, 11, 30))
        self.assertEqual(self.state(date(2026, 11, 30)), "received")
        self.assertEqual(self.decision(nov), ("auto", date(2026, 11, 30)))

    def test_scheduled_payday_change(self):
        self.profile.scheduled_payday, self.profile.payday_changes_on = 10, date(2026, 11, 10)
        self.profile.payday_previous_start, self.profile.payday_revision = SEPT, 1
        self.profile.save()
        self.learn()  # 9/25 ~ 11/9 전환 주기
        nov = self.tx(date(2026, 11, 10))
        self.assertEqual(self.state(date(2026, 11, 12)), "received")
        self.assertEqual(self.decision(nov), ("auto", date(2026, 11, 10)))


class CorrectionTest(SalaryCheckFixture):
    NEXT = AutoRecognizeTest.NEXT

    def test_reject_sticks_through_reprocessing_and_can_be_undone(self):
        tx = self.tx(SEPT)
        self.assertEqual(self.post([tx], "rejected").status_code, 200)
        self.assertEqual(self.state(), "waiting")
        FinancialTransaction.objects.filter(pk=tx.pk).update(source_description="머니쿠션")  # 재수집으로 같은 거래가 갱신됨
        self.assertEqual((self.state(), self.decision(tx)), ("waiting", ("rejected", None)))
        self.post([tx], "none")
        self.assertEqual(self.state(), "needs_check")

    def test_rejected_matching_tx_is_not_auto(self):
        self.learn()
        nxt = self.tx(OCT)
        self.state(self.NEXT)
        self.assertEqual(self.post([nxt], "rejected", today=self.NEXT).status_code, 200)
        self.assertEqual((self.state(self.NEXT), self.decision(nxt)), ("waiting", ("rejected", None)))
        self.assertIsNone(BudgetCycle.objects.get(starts_on=OCT).confirmed_income)
        self.assertNotIn(nxt.pk, salary_reviews(self.user))

    def test_release_pattern_keeps_history(self):
        base = self.learn()
        self.client.delete("/api/budgets/salary/pattern/")
        self.tx(OCT)
        self.assertEqual(self.state(self.NEXT), "needs_check")
        self.assertEqual(self.decision(base), ("confirmed", SEPT))

    def test_change_pattern_does_not_rewrite_history(self):
        base = self.learn()
        new = self.tx(OCT, memo="10월 월급")
        self.assertEqual(self.state(self.NEXT), "needs_check")  # 회사명 → 월급 문구: 같은 출처로 추론하지 않음
        self.post([new], use_pattern=True, today=self.NEXT)
        self.assertEqual(SalaryDecision.objects.get(is_pattern=True).transaction, new)
        self.assertEqual(self.decision(base), ("confirmed", SEPT))

    def test_canceled_source_removes_salary(self):
        tx = self.learn()
        FinancialTransaction.objects.filter(pk=tx.pk).update(is_canceled=True)
        self.assertEqual(self.state(), "waiting")
        self.assertIsNone(BudgetCycle.objects.get(starts_on=SEPT).confirmed_income)
        self.assertEqual(salary_reviews(self.user), {})

    def test_stale_screen_and_protected_links_are_revalidated(self):
        own = self.tx(SEPT)
        mirror = self.make_tx(SEPT, 2_500_000, direction="out", account=self.make_account("k2"))
        TransactionLink.objects.create(user=self.user, from_transaction=mirror, to_transaction=own, relation_type="OWN_TRANSFER",
                                       matched_amount=2_500_000, confirmation_state="confirmed")
        paid = self.tx(date(2026, 9, 26))
        expense = RecurringExpense.objects.create(user=self.user, name="환급", amount=2_500_000, payment_day=26, category_id="HOUSING_TELECOM",
                                                  status="confirmed", source_type="manual", payment_account=self.account)
        due = PaymentDue.objects.create(user=self.user, recurring=expense, obligation_type="recurring", occurrence_key="x",
                                        due_date=date(2026, 9, 26), confirmed_amount=2_500_000, source_type="manual")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=paid, matched_amount=2_500_000)
        canceled = self.tx(date(2026, 9, 27), is_canceled=True)
        typed = self.tx(date(2026, 9, 28), transaction_type="own_transfer")
        for tx in (own, paid, canceled, typed):
            with self.subTest(tx=tx.occurred_on):
                self.assertEqual(self.post([tx]).status_code, 400)
        self.assertEqual(SalaryDecision.objects.count(), 0)

    def test_window_and_ownership_checks(self):
        old = self.tx(date(2026, 8, 25))
        self.assertEqual(self.post([old]).status_code, 400)                          # 지금 확인할 구간 밖
        self.assertEqual(self.post([self.tx(SEPT), self.tx(date(2026, 10, 19))]).status_code, 400)  # 두 회차를 섞음
        stranger = get_user_model().objects.create_user(email="other@example.com", password="Example123!")
        other_client = APIClient()
        other_client.force_authenticate(stranger)
        theirs = self.tx(SEPT)
        for decision in ("confirmed", "rejected", "none"):
            res = self.post([theirs], decision, client=other_client)
            self.assertIn(res.status_code, (400,))
        self.assertIsNone(self.decision(theirs))
        self.assertEqual(self.post([theirs], "bogus").status_code, 400)
        res = self.client.post(URL, {"transactionIds": [str(theirs.pk)], "decision": "none", "extra": 1}, format="json")
        self.assertEqual(res.status_code, 400)
        anon = APIClient()
        self.assertEqual(anon.get("/api/budgets/salary/deposits/").status_code, 403)

    def test_deposits_list_shows_candidates_and_pattern(self):
        self.learn()
        small = self.tx(date(2026, 10, 1), 50_000, memo="")
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            body = self.client.get("/api/budgets/salary/deposits/").json()
        items = {i["transactionId"]: i for i in body["windows"][0]["items"]}
        self.assertEqual((items[str(small.pk)]["isCandidate"], items[str(small.pk)]["patternUnusableReason"] is not None), (False, True))
        self.assertEqual(body["pattern"]["description"], "머니쿠션")


class ConcurrentConfirmTest(TransactionTestCase):
    """같은 확인을 동시에 두 번 → 판단 한 줄, 금액 한 번"""
    serialized_rollback = True  # 카테고리 시드를 다른 테스트에 남긴다

    def test_concurrent_confirm(self):
        from users.models import FinancialProfile
        user = get_user_model().objects.create_user(email="race@example.com", password="Example123!")
        FinancialProfile.objects.create(user=user, payday=25, monthly_take_home_income=2_500_000)
        bank = Connection.objects.create(user=user, business_type="bank", organization_code="0004", status="connected")
        account = BankAccount.objects.create(user=user, connection=bank, source_account_key="k", account_type="demand",
                                             is_selected=True, fetched_at=timezone.now())
        tx = FinancialTransaction.objects.create(user=user, source_kind="bank", account=account, occurred_on=SEPT, amount=2_500_000,
                                                 direction="in", transaction_type="income", source_description="머니쿠션",
                                                 confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        BudgetCycle.objects.create(user=user, starts_on=SEPT, ends_before=OCT)
        codes = []

        def run():
            try:
                client = APIClient()
                client.force_authenticate(user)
                codes.append(client.post(URL, {"transactionIds": [str(tx.pk)], "decision": "confirmed", "usePattern": True},
                                         format="json").status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(2)]
        with patch("django.utils.timezone.localdate", return_value=TODAY):  # 스레드마다 patch 하면 서로 되돌려서 바깥에서 한 번
            [t.start() for t in threads], [t.join() for t in threads]
        self.assertEqual(codes, [200, 200])
        self.assertEqual(SalaryDecision.objects.filter(user=user).count(), 1)
        self.assertEqual(BudgetCycle.objects.get(user=user, starts_on=SEPT).confirmed_income, 2_500_000)


class R12FollowUpFixture(SalaryCheckFixture):
    """보완: R11 자동 분류 · 계좌 선택 해제 · 자동 인정 교체"""
    NEXT = date(2026, 11, 21)  # 10/25 회차 (구간 10/18 ~ 11/17)

    def own_pair(self, memo="김나은"):
        """확인할 입금(k1) + 다른 계좌에서 같은 금액 · 1분 차이 출금 — R11 조건"""
        from datetime import time
        get_user_model().objects.filter(pk=self.user.pk).update(nickname="김나은")
        income = self.tx(SEPT, memo=memo, occurred_time=time(9, 0))
        out = self.make_tx(SEPT, 2_500_000, direction="out", account=self.make_account("k2"),
                           source_description=memo, occurred_time=time(9, 1))
        return income, out

    def run_r11(self, out):
        from finance import transfer_rules
        transfer_rules.apply(self.user, FinancialTransaction.objects.filter(pk=out.pk))


class ConfirmedSalaryVsOwnTransferTest(R12FollowUpFixture):
    def test_user_confirmed_salary_is_not_turned_into_own_transfer(self):
        income, out = self.own_pair()
        self.assertEqual(self.post([income]).status_code, 200)
        self.run_r11(out)  # 확인 뒤 다른 계좌 출금이 수집됨
        income.refresh_from_db(), out.refresh_from_db()
        self.assertEqual((income.transaction_type, out.transaction_type), ("income", "transfer"))
        self.assertFalse(TransactionLink.objects.filter(relation_type="OWN_TRANSFER").exists())
        self.assertEqual(salary_reviews(self.user), {income.pk: "confirmed"})
        self.assertEqual(self.period()["salary_amount"], 2_500_000)

    def test_without_confirmation_r11_still_links(self):
        income, out = self.own_pair()
        self.run_r11(out)
        income.refresh_from_db()
        self.assertEqual(income.transaction_type, "own_transfer")  # R11 탐지 기준은 그대로


class DeselectedAccountTest(R12FollowUpFixture):
    def test_confirmed_salary_survives_deselect(self):
        tx = self.tx(SEPT)
        self.assertEqual(self.post([tx]).status_code, 200)
        BankAccount.objects.filter(pk=self.account.pk).update(is_selected=False)
        period = self.period()
        self.assertEqual((period["salary_state"], period["salary_amount"], period["salary_received_on"]),
                         ("received", 2_500_000, "2026-09-25"))
        self.assertEqual(BudgetCycle.objects.get(starts_on=SEPT).confirmed_income, 2_500_000)
        self.assertEqual(salary_reviews(self.user), {tx.pk: "confirmed"})
        with patch("django.utils.timezone.localdate", return_value=TODAY):
            row = self.client.get(f"/api/finance/transactions/{tx.pk}/").json()
            listed = self.client.get("/api/finance/transactions/?from=2026-09-01").json()["items"]
            deposits = self.client.get("/api/budgets/salary/deposits/").json()
        self.assertEqual((row["isSalary"], row["salaryReview"]), (True, "confirmed"))
        self.assertTrue(next(i for i in listed if i["transactionId"] == str(tx.pk))["isSalary"])
        self.assertEqual(deposits["windows"][0]["items"][0]["decision"], "confirmed")  # 팝업에서도 보이고 취소 가능
        self.assertEqual(self.post([tx], "none").status_code, 200)
        self.assertEqual(self.state(), "waiting")

    def test_deselected_account_gives_no_new_candidate_or_auto(self):
        self.learn()
        BankAccount.objects.filter(pk=self.account.pk).update(is_selected=False)
        nxt = self.tx(OCT)
        self.assertEqual(self.state(self.NEXT), "waiting")
        self.assertIsNone(self.decision(nxt))
        self.assertEqual(self.post([nxt], today=self.NEXT).status_code, 400)  # 새 확인도 관리 대상 계좌만


class NoSilentAutoSwapTest(R12FollowUpFixture):
    def test_canceled_auto_is_not_replaced(self):
        self.learn()
        a = self.tx(OCT)
        self.state(self.NEXT)
        self.assertEqual(self.decision(a), ("auto", OCT))
        FinancialTransaction.objects.filter(pk=a.pk).update(is_canceled=True)
        b = self.tx(date(2026, 11, 1))
        for _ in range(3):  # 홈 · 팝업 반복 조회
            period = self.period(self.NEXT)
            with patch("django.utils.timezone.localdate", return_value=self.NEXT):
                self.client.get("/api/budgets/salary/deposits/")
        self.assertIsNone(self.decision(b))
        self.assertEqual(self.decision(a), ("recheck", OCT))
        self.assertEqual((period["salary_state"], period["salary_amount"]), ("needs_check", None))
        self.assertIsNone(BudgetCycle.objects.get(starts_on=OCT).confirmed_income)
        self.assertEqual(self.post([b], today=self.NEXT).status_code, 200)  # 직접 확인은 정상
        period = self.period(self.NEXT)
        self.assertEqual((period["salary_state"], period["salary_source"], period["salary_amount"]), ("received", "user", 2_500_000))
        self.assertIsNone(self.decision(a))

    def test_ambiguous_then_single_again_is_not_auto(self):
        self.learn()
        a = self.tx(date(2026, 10, 23))
        self.state(self.NEXT)
        b = self.tx(date(2026, 10, 27))  # 월급날에서 같은 거리 → 모호
        self.assertEqual(self.state(self.NEXT), "needs_check")
        FinancialTransaction.objects.filter(pk=a.pk).update(is_canceled=True)  # 다시 한 건만 남아도
        for _ in range(2):
            self.assertEqual(self.state(self.NEXT), "needs_check")
        self.assertIsNone(self.decision(b))
        self.assertEqual(self.period(self.NEXT)["salary_check_count"], 1)

    def test_other_cycle_first_auto_unaffected(self):
        self.learn()
        a = self.tx(OCT)
        self.state(self.NEXT)
        FinancialTransaction.objects.filter(pk=a.pk).update(is_canceled=True)
        self.state(self.NEXT)
        nov = self.tx(NOV)
        self.assertEqual(self.state(date(2026, 12, 21)), "received")
        self.assertEqual(self.decision(nov), ("auto", NOV))


class ConfirmVsOwnTransferRaceTest(TransactionTestCase):
    """급여 확인과 R11 자동 연결이 겹쳐도 둘 다 확정되지 않는다 (한쪽이 트랜잭션을 연 채 기다리게 해서 순서를 고정)"""
    serialized_rollback = True

    def setUp(self):
        from datetime import time
        from users.models import FinancialProfile
        self.user = get_user_model().objects.create_user(email="race2@example.com", password="Example123!", nickname="김나은")
        FinancialProfile.objects.create(user=self.user, payday=25, monthly_take_home_income=2_500_000)
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        accounts = [BankAccount.objects.create(user=self.user, connection=bank, source_account_key=k, account_type="demand",
                                               is_selected=True, fetched_at=timezone.now()) for k in ("k1", "k2")]
        common = dict(user=self.user, source_kind="bank", occurred_on=SEPT, amount=2_500_000, source_description="김나은",
                      confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        self.income = FinancialTransaction.objects.create(account=accounts[0], direction="in", transaction_type="income",
                                                          occurred_time=time(9, 0), **common)
        self.out = FinancialTransaction.objects.create(account=accounts[1], direction="out", transaction_type="transfer",
                                                       occurred_time=time(9, 1), **common)

    def confirm(self, codes):
        try:
            client = APIClient()
            client.force_authenticate(self.user)
            codes.append(client.post(URL, {"transactionIds": [str(self.income.pk)], "decision": "confirmed"}, format="json").status_code)
        finally:
            connection.close()

    def r11(self):
        from finance import transfer_rules
        try:
            transfer_rules.link_own_transfers(self.user, FinancialTransaction.objects.filter(pk=self.out.pk))
        finally:
            connection.close()

    def both_confirmed(self):
        self.income.refresh_from_db()
        return self.income.transaction_type == "own_transfer" and SalaryDecision.objects.filter(
            transaction=self.income, state="confirmed").exists()

    def hold(self, target, name, started):
        """target 의 name 을 감싸서, 그 안(트랜잭션 진행 중)에서 신호를 보내고 잠시 기다리게 한다"""
        import time
        original = getattr(target, name)

        def slow(*args, **kwargs):
            started.set()
            time.sleep(0.5)
            return original(*args, **kwargs)
        return patch.object(target, name, slow)

    def test_confirm_first(self):
        from . import api_salary
        started, codes = threading.Event(), []
        with patch("django.utils.timezone.localdate", return_value=TODAY), self.hold(api_salary, "store_income", started):
            a = threading.Thread(target=self.confirm, args=(codes,))
            a.start()
            started.wait(5)
            b = threading.Thread(target=self.r11)
            b.start()
            a.join(), b.join()
        self.assertEqual(codes, [200])
        self.assertFalse(self.both_confirmed())
        self.out.refresh_from_db()
        self.assertEqual(self.out.transaction_type, "transfer")  # 출금만 반쪽으로 바뀌지 않음

    def test_r11_first(self):
        from finance import transfer_rules
        started, codes = threading.Event(), []
        with patch("django.utils.timezone.localdate", return_value=TODAY), self.hold(transfer_rules, "link", started):
            b = threading.Thread(target=self.r11)
            b.start()
            started.wait(5)
            a = threading.Thread(target=self.confirm, args=(codes,))
            a.start()
            a.join(), b.join()
        self.assertEqual(codes, [400])
        self.assertFalse(self.both_confirmed())
