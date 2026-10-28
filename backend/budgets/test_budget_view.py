"""카테고리 예산 조회 · 자물쇠 · 승인 계약 테스트."""
import json
import uuid
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from finance.models import BankAccount, Card, Connection, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense, TransactionLink
from users.models import CategoryMinimum, FinancialProfile, IdempotencyRecord

from .models import Budget, BudgetCategorySetting, BudgetCycle, BudgetItem, SpendingAllocation
from .overview import LIVING_CODES, NAG
from .recommend import budget_input_hash


TODAY = date(2026, 10, 28)
URL = "/api/budgets/"


class BudgetViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="budget-view@example.com")
        cls.other = get_user_model().objects.create_user(email="other@example.com")
        cls.profile = FinancialProfile.objects.create(
            user=cls.user, housing_type="family", monthly_take_home_income=1_500_000, payday=25,
            living_budget=1_000_000, savings_floor=200_000, cushion_monthly=100_000, cushion_target=4_500_000,
        )
        cls.cycle = BudgetCycle.objects.create(user=cls.user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
        cls.expense = RecurringExpense.objects.create(user=cls.user, name="통신", amount=100_000,
            category_id="HOUSING_TELECOM", status="confirmed", source_type="manual")
        bank = Connection.objects.create(user=cls.user, business_type="bank", organization_code="0004", status="connected")
        cls.account = BankAccount.objects.create(user=cls.user, connection=bank, source_account_key="budget-test",
                                                account_type="demand", currency="KRW", is_selected=True, balance=1_000_000)
        cls.card = Card.objects.create(user=cls.user, origin="manual", name="카드")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        clock = patch("django.utils.timezone.localdate", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)

    def make_budget(self, status="validated", cycle=None, food=1_000_000, card_reserved=0):
        cycle = cycle or self.cycle
        budget = Budget.objects.create(user=self.user, cycle=cycle, status=status, funding_amount=1_500_000,
            version_no=(cycle.budgets.order_by("-version_no").values_list("version_no", flat=True).first() or 0) + 1,
            input_hash=budget_input_hash(self.user, cycle), input_snapshot={"fixed": {"card_reserved": card_reserved}},
            approved_at=timezone.now() if status == "approved" else None)
        BudgetItem.objects.bulk_create([
            BudgetItem(user=self.user, budget=budget, bucket="living", category_id=code, allocated_amount=food if code == "FOOD" else 0)
            for code in LIVING_CODES
        ] + [BudgetItem(user=self.user, budget=budget, bucket=bucket, allocated_amount=amount)
             for bucket, amount in (("fixed", 100_000), ("savings", 200_000), ("cushion", 100_000))])
        return budget

    def tx(self, amount, category="FOOD", **fields):
        data = dict(user=self.user, source_kind="manual", occurred_on=TODAY, amount=amount, direction="out",
                    transaction_type="spending", confirmed_category_id=category, confirmation_state="confirmed")
        data.update(fields)
        return FinancialTransaction.objects.create(**data)

    def lock(self, budget, body, code="FOOD"):
        return self.client.patch(f"{URL}{budget.pk}/categories/{code}/", json.dumps(body), content_type="application/json")

    def approve(self, budget, key=None, body=None):
        return self.client.post(f"{URL}{budget.pk}/approve/", json.dumps({} if body is None else body),
                                content_type="application/json", HTTP_IDEMPOTENCY_KEY=key or str(uuid.uuid4()))

    def get(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_no_budget_keeps_unclassified_count(self):
        self.tx(3_000, "UNCLASSIFIED")
        data = self.get()
        self.assertEqual(data["cycle"], {"startsOn": "2026-10-25", "endsBefore": "2026-11-25"})
        for key in ("budget", "split", "living", "overspend", "nag"):
            self.assertIsNone(data[key])
        self.assertEqual((data["categories"], data["unclassifiedCount"]), ([], 1))

    def test_no_payday_has_no_cycle_even_with_old_budget(self):
        self.make_budget()
        self.profile.payday = None
        self.profile.save()
        self.tx(3_000, "UNCLASSIFIED", occurred_on=date(2026, 9, 1))
        data = self.get()
        self.assertIsNone(data["cycle"])
        self.assertIsNone(data["budget"])
        self.assertEqual(data["unclassifiedCount"], 1)

    def test_latest_validated_precedes_approved_and_ignores_other_states(self):
        approved = self.make_budget("approved")
        self.assertEqual(self.get()["budget"]["budgetId"], str(approved.pk))
        self.make_budget()
        latest = self.make_budget()
        for status in ("draft", "expired", "superseded", "rejected"):
            self.make_budget(status)
        self.assertEqual(self.get()["budget"]["budgetId"], str(latest.pk))
        Budget.objects.filter(status="validated").update(status="expired")
        self.assertEqual(self.get()["budget"]["status"], "approved")

    def test_split_categories_minimums_locks_and_order(self):
        budget = self.make_budget()
        CategoryMinimum.objects.create(user=self.user, category_id="FOOD", min_amount=155_000)
        self.lock(budget, {"locked": True})
        data = self.get()
        self.assertEqual(data["split"], {"funding": 1_500_000, "fixed": 100_000, "saving": 200_000,
                                         "cushion": 100_000, "living": 1_000_000, "spare": 100_000})
        self.assertEqual([item["category"] for item in data["categories"]], list(LIVING_CODES))
        food = data["categories"][0]
        self.assertEqual(food, {"category": "FOOD", "categoryName": "식비", "allocated": 1_000_000, "spent": 0,
                               "minAmount": 155_000, "locked": True, "lockedAmount": 1_000_000, "reason": None})
        self.assertIsNone(data["categories"][1]["minAmount"])

    def test_spent_matches_transaction_items_with_fixed_excluded(self):
        budget = self.make_budget()
        self.tx(10_000, occurred_on=self.cycle.starts_on)
        card = self.tx(20_000, source_kind="card", card=self.card)
        bank = self.tx(20_000, source_kind="bank", account=self.account)
        TransactionLink.objects.create(user=self.user, from_transaction=card, to_transaction=bank,
                                       relation_type="SAME_PURCHASE", matched_amount=20_000, confirmation_state="confirmed")
        self.tx(5_000, "ETC")
        self.tx(7_000, "UNCLASSIFIED")
        self.tx(10_000, canceled_amount=3_000)
        self.tx(9_000, is_canceled=True)
        fixed = self.tx(30_000)
        SpendingAllocation.objects.create(user=self.user, transaction=fixed, cycle=self.cycle, budget=budget,
                                           funding_bucket="fixed", category_id="FOOD", amount=30_000)
        linked = self.tx(40_000)
        due = PaymentDue.objects.create(user=self.user, recurring=self.expense, obligation_type="recurring", occurrence_key="test-fixed",
            due_date=TODAY, confirmed_amount=40_000, paid_amount=40_000, payment_state="paid", obligation_state="active")
        PaymentMatch.objects.create(user=self.user, due=due, transaction=linked, matched_amount=40_000)
        for kind in ("card_payment", "transfer", "own_transfer", "saving", "loan_repayment", "income"):
            self.tx(50_000, transaction_type=kind)
        self.tx(100_000, occurred_on=date(2026, 10, 24))
        self.tx(100_000, occurred_on=self.cycle.ends_before)
        self.tx(100_000, user=self.other)
        transactions = self.client.get("/api/finance/transactions/?from=2026-10-25&to=2026-11-24").json()
        expected = sum(item["netAmount"] for item in transactions["items"]
                       if item["transactionType"] == "spending" and item["funding"] != "fixed")
        data = self.get()
        self.assertEqual(data["living"]["spent"], expected)
        self.assertEqual(expected, 49_000)
        self.assertEqual(transactions["summary"]["spentTotal"], 119_000)
        self.assertEqual(data["unclassifiedCount"], 1)
        categories = {item["category"]: item["spent"] for item in data["categories"]}
        self.assertNotIn("UNCLASSIFIED", categories)
        self.assertEqual((categories["FOOD"], categories["ETC"]), (37_000, 5_000))

    def test_fixed_unclassified_counts_for_review_but_not_living_spent(self):
        budget = self.make_budget()
        tx = self.tx(30_000, "UNCLASSIFIED")
        SpendingAllocation.objects.create(user=self.user, transaction=tx, cycle=self.cycle, budget=budget,
                                           funding_bucket="fixed", category_id="UNCLASSIFIED", amount=30_000)
        data = self.get()
        self.assertEqual(data["unclassifiedCount"], 1)
        self.assertEqual(data["living"]["spent"], 0)

    def test_overspend_only_uses_spare(self):
        self.make_budget()
        tx = self.tx(900_000)
        for spent, expected in ((900_000, (0, 0, 100_000, 0)), (1_050_000, (50_000, 50_000, 50_000, 0)),
                                (1_200_000, (200_000, 100_000, 0, 100_000))):
            tx.amount = spent
            tx.save()
            data = self.get()
            self.assertEqual(tuple(data["overspend"][key] for key in ("over", "coveredBySpare", "spareRemaining", "shortfall")), expected)
            self.assertEqual(data["living"]["remaining"], max(1_000_000 - spent, 0))
            self.assertEqual((data["split"]["saving"], data["split"]["cushion"]), (200_000, 100_000))

    def test_salary_confirmation_reuses_home_period(self):
        self.make_budget()
        self.assertFalse(self.get()["budget"]["salaryConfirmed"])
        self.tx(1_500_000, source_kind="bank", account=self.account, direction="in", transaction_type="income")
        self.assertTrue(self.get()["budget"]["salaryConfirmed"])

    def test_nag_lock_counts_exclude_protected_categories(self):
        self.make_budget()
        for code in ("HOUSING_TELECOM", "TRANSPORT_CAR"):
            BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id=code, change_count=3)
        self.assertIsNone(self.get()["nag"])
        setting = BudgetCategorySetting.objects.create(user=self.user, cycle=self.cycle, category_id="FOOD", change_count=2)
        self.assertIsNone(self.get()["nag"])
        setting.change_count = 3
        setting.save()
        self.assertEqual(self.get()["nag"], NAG)

    def test_nag_counts_historical_approvals(self):
        self.make_budget()
        for index in range(3):
            budget = self.make_budget("superseded")
            budget.approved_at = timezone.now()
            budget.save()
            self.assertEqual(self.get()["nag"], NAG if index == 2 else None)

    def test_lock_unlock_and_idempotent_relock(self):
        budget = self.make_budget()
        response = self.lock(budget, {"locked": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["lockedAmount"], response.json()["changeCount"]), (1_000_000, 1))
        self.assertEqual(self.lock(budget, {"locked": True}).json()["changeCount"], 1)
        data = self.lock(budget, {"locked": False}).json()
        self.assertEqual((data["locked"], data["lockedAmount"], data["changeCount"]), (False, None, 2))
        self.assertEqual(self.lock(budget, {"locked": False}).json()["changeCount"], 2)
        self.assertEqual(self.lock(budget, {"locked": True}).json()["nag"], NAG)
        budget.refresh_from_db()
        self.assertEqual(budget.status, "validated")

    def test_relock_from_another_budget_only_changes_amount(self):
        first = self.make_budget("approved")
        second = self.make_budget(food=700_000)
        self.lock(first, {"locked": True})
        data = self.lock(second, {"locked": True}).json()
        self.assertEqual((data["lockedAmount"], data["changeCount"]), (700_000, 1))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), ("approved", "validated"))

    def test_lock_404_ownership_and_category(self):
        budget = self.make_budget()
        for code in ("UNCLASSIFIED", "UNKNOWN"):
            self.assertEqual(self.lock(budget, {"locked": True}, code).status_code, 404)
        for pk in (uuid.uuid4(), budget.pk):
            if pk == budget.pk:
                self.client.force_authenticate(self.other)
            response = self.client.patch(f"{URL}{pk}/categories/FOOD/", {"locked": True}, format="json")
            self.assertEqual(response.status_code, 404)

    def test_lock_409_for_wrong_state_or_cycle(self):
        for status in ("draft", "expired", "superseded", "rejected"):
            response = self.lock(self.make_budget(status), {"locked": True})
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_ready"))
        old = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25), ends_before=date(2026, 10, 25))
        response = self.lock(self.make_budget(cycle=old), {"locked": True})
        self.assertEqual(response.json()["error"]["message"], "지난 예산은 바꿀 수 없어요.")
        self.assertFalse(BudgetCategorySetting.objects.exists())

    def test_lock_400_for_invalid_body(self):
        budget = self.make_budget()
        for body in (None, [], {}, {"locked": None}, {"locked": 1}, {"locked": "true"}, {"locked": True, "amount": 1}):
            response = self.lock(budget, body)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (400, "invalid"))
        self.assertFalse(BudgetCategorySetting.objects.exists())

    def test_approval_supersedes_previous_and_returns_approved(self):
        previous = self.make_budget("approved")
        chosen = self.make_budget()
        newer = self.make_budget()
        key = str(uuid.uuid4())
        response = self.approve(chosen, key)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["budget"]["budgetId"], str(chosen.pk))
        self.assertEqual(response.json()["budget"]["status"], "approved")
        for budget in (previous, chosen, newer):
            budget.refresh_from_db()
        self.assertEqual((previous.status, chosen.status, newer.status), ("superseded", "approved", "validated"))
        self.assertEqual(chosen.approval_key, key)
        self.assertIsNotNone(chosen.approved_at)

    def test_first_approval_completes_onboarding_without_profile_flag(self):
        chosen = self.make_budget()
        self.assertFalse(self.client.get("/api/auth/me/").json()["onboardingCompleted"])
        self.assertEqual(self.approve(chosen).status_code, 200)
        self.assertTrue(self.client.get("/api/auth/me/").json()["onboardingCompleted"])

    def test_already_approved_keeps_approval_time_and_key(self):
        chosen = self.make_budget()
        self.approve(chosen)
        chosen.refresh_from_db()
        original = (chosen.approved_at, chosen.approval_key)
        response = self.approve(chosen)
        self.assertEqual(response.status_code, 200)
        chosen.refresh_from_db()
        self.assertEqual((chosen.approved_at, chosen.approval_key), original)

    def assert_expired(self, chosen):
        response = self.approve(chosen)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json(), {"error": {"code": "expired", "message": "조건이 바뀌어서 다시 추천받아야 해요."}})
        chosen.refresh_from_db()
        self.assertEqual(chosen.status, "expired")
        self.assertIsNone(chosen.approved_at)
        self.assertFalse(IdempotencyRecord.objects.exists())

    def test_approval_expires_previous_cycle_and_persists_409(self):
        chosen = self.make_budget()
        with patch("django.utils.timezone.localdate", return_value=date(2026, 11, 25)):
            self.assert_expired(chosen)

    def test_approval_expires_changed_hash_and_persists_409(self):
        chosen = self.make_budget()
        self.profile.savings_floor += 1
        self.profile.save()
        self.assert_expired(chosen)

    def test_approval_expires_when_saved_living_is_cleared(self):
        chosen = self.make_budget()
        self.profile.living_budget = None
        self.profile.save()
        self.assert_expired(chosen)

    def test_approval_expires_when_cushion_became_required(self):
        # 리뷰 1: 잔액이 넉넉할 때 쿠션 0원으로 만든 초안 → 잔액이 줄어 쿠션이 필수가 되면 승인 안 됨
        self.account.balance = 5_000_000
        self.account.save()
        self.profile.cushion_monthly = 0
        self.profile.save()
        chosen = self.make_budget()
        self.account.balance = 1_000_000
        self.account.save()
        self.assertEqual(budget_input_hash(self.user, self.cycle), chosen.input_hash)  # 해시만으로는 못 잡는 경우
        self.assert_expired(chosen)

    def test_approval_with_zero_cushion_ok_while_balance_is_enough(self):
        self.account.balance = 5_000_000
        self.account.save()
        self.profile.cushion_monthly = 0
        self.profile.save()
        self.assertEqual(self.approve(self.make_budget()).status_code, 200)

    def test_past_cycle_approved_budget_is_not_ready_with_new_key(self):
        # 리뷰 2: 지난 주기 승인 예산을 새 요청 번호로 다시 보내도 이번 주기 초안이 성공으로 나오면 안 됨
        past_cycle = BudgetCycle.objects.create(user=self.user, starts_on=date(2026, 9, 25), ends_before=date(2026, 10, 25))
        past = self.make_budget("approved", cycle=past_cycle)
        draft = self.make_budget()
        response = self.approve(past)
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_ready"))
        self.assertEqual(response.json()["error"]["message"], "지난 예산은 승인할 수 없어요.")
        for budget in (past, draft):
            budget.refresh_from_db()
        self.assertEqual((past.status, draft.status), ("approved", "validated"))

    def test_approval_uses_card_amount_saved_in_draft(self):
        # 카드 선차감은 초안에 저장한 값을 쓴다: 지금 다시 계산하면 보류(None)여도 승인된다
        chosen = self.make_budget(card_reserved=0)
        pending = {"amount": None, "pending": [{"card_id": str(self.card.pk), "reason": "bill_history_incomplete"}]}
        with patch("budgets.protect.card_reserved", return_value=pending) as reserved:
            self.assertEqual(self.approve(chosen).status_code, 200)
        reserved.assert_not_called()

    def test_approval_expires_when_saved_card_amount_no_longer_fits(self):
        # 초안의 카드 선차감 때문에 지킬 돈 합계가 배분 기준을 넘으면 만료 (1,500,000 − 100,000 − 200,000 < 1,300,000)
        self.assert_expired(self.make_budget(card_reserved=200_000))

    def test_already_expired_is_409(self):
        self.assert_expired(self.make_budget("expired"))

    def test_superseded_rejected_and_draft_are_not_ready(self):
        for status in ("superseded", "rejected", "draft"):
            chosen = self.make_budget(status)
            response = self.approve(chosen)
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "not_ready"))
            self.assertEqual(response.json()["error"]["message"], "지난 예산은 승인할 수 없어요.")
            chosen.refresh_from_db()
            self.assertEqual(chosen.status, status)

    def test_lock_changes_do_not_expire_or_change_hash(self):
        chosen = self.make_budget()
        self.assertEqual(self.lock(chosen, {"locked": True}).status_code, 200)
        self.assertEqual(budget_input_hash(self.user, self.cycle), chosen.input_hash)
        self.assertEqual(self.approve(chosen).status_code, 200)

    def test_approval_same_request_key_replays_original_response(self):
        chosen = self.make_budget()
        key = str(uuid.uuid4())
        first = self.approve(chosen, key)
        self.tx(50_000)
        second = self.approve(chosen, key)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(IdempotencyRecord.objects.count(), 1)
        response = self.approve(chosen, key, {"extra": True})
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "idempotency_conflict"))

    def test_approval_404_and_required_key_and_body(self):
        chosen = self.make_budget()
        response = self.client.post(f"{URL}{chosen.pk}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.approve(chosen, body={"unknown": 1}).status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.approve(chosen).status_code, 404)
        self.assertEqual(self.client.post(f"{URL}{uuid.uuid4()}/approve/", {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code, 404)

    def test_all_endpoints_require_login(self):
        chosen = self.make_budget()
        self.client.force_authenticate(user=None)
        for response in (self.client.get(URL), self.lock(chosen, {"locked": True}), self.approve(chosen)):
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "not_authenticated"))
