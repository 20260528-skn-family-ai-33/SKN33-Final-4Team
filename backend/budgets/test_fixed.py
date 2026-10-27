"""고정지출 API 회귀 테스트 (PR #43 리뷰). 실행: backend 폴더에서 python manage.py test budgets

- 본문 모양 검사: {} 가 아닌 본문([] · 42 · "hello")은 500 이 아니라 400 invalid
- 납부 예정: 직접 추가 · 후보 확정 때 이번 주기 1건, 수정하면 안 낸 회차만 맞춤 (낸 회차는 그대로)
- 근거 거래: 내 출금은 고를 수 있고, 비우면 납부 짝 · 상태도 다시 계산
- 동시 요청: 같은 고정지출을 동시에 바꿔도 상태가 덮어써지지 않음 (PostgreSQL 에서만 실행)
"""
import json
import threading
import time
from datetime import timedelta
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from finance.models import BankAccount, Card, Connection, FinancialTransaction, PaymentDue, PaymentMatch, RecurringExpense

from .models import BudgetCycle

URL = "/api/budgets/fixed/"


def send(client, method, url, body):
    """본문을 JSON 그대로 보내기 ([] · 42 · "hello" 도 그대로)"""
    return getattr(client, method)(url, data=json.dumps(body), content_type="application/json")


class FixedFixture:
    """회원 1명 + 입출금 계좌 2개 + 카드 1개 + 진행 중인 급여 주기(오늘 -10일 ~ +17일, 28일)"""

    def make_world(self, email="naeun@example.com"):
        self.today = timezone.localdate()
        self.user = get_user_model().objects.create_user(email=email, password="Example123!")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.account = BankAccount.objects.create(
            user=self.user, connection=bank, source_account_key=f"{email}-1", masked_number="****5678",
            account_type="demand", is_selected=True,
        )
        self.account2 = BankAccount.objects.create(
            user=self.user, connection=bank, source_account_key=f"{email}-2", masked_number="****1111",
            account_type="demand", is_selected=True,
        )
        card_conn = Connection.objects.create(user=self.user, business_type="card", organization_code="0301", status="connected")
        self.card = Card.objects.create(
            user=self.user, connection=card_conn, name="Deep Dream", masked_number="****1234",
            origin="provider", is_selected=True,
        )
        self.cycle = BudgetCycle.objects.create(
            user=self.user, starts_on=self.today - timedelta(days=10), ends_before=self.today + timedelta(days=18),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_expense(self, status="confirmed", **fields):
        data = dict(user=self.user, name="월세", amount=700000, payment_day=None, category_id="HOUSING_TELECOM",
                    status=status, source_type="manual" if status == "confirmed" else "detected")
        data.update(fields)
        return RecurringExpense.objects.create(**data)

    def make_tx(self, on, amount=700000, account=None, card=None, user=None, **fields):
        data = dict(
            user=user or self.user, occurred_on=on, amount=amount, direction="out",
            transaction_type="transfer" if card is None else "spending",
            confirmed_category_id="HOUSING_TELECOM", confirmation_state="confirmed",
        )
        if card is not None:
            data.update(source_kind="card", card=card)
        else:
            data.update(source_kind="bank", account=account or self.account)
        data.update(fields)
        return FinancialTransaction.objects.create(**data)

    def dues(self, expense, state="active"):
        return list(PaymentDue.objects.filter(recurring=expense, obligation_state=state))

    def paid_due(self, expense, tx):
        """이미 낸 회차 1건 (짝 있음)"""
        due = PaymentDue.objects.create(
            user=self.user, obligation_type="recurring", recurring=expense, occurrence_key=f"recurring:{expense.pk}:{self.cycle.starts_on}",
            due_date=tx.occurred_on, confirmed_amount=expense.amount, payment_account=expense.payment_account,
            source_type="manual", confirmation_state="confirmed", paid_amount=tx.amount, payment_state="paid",
        )
        PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=tx.amount)
        return due


class FixedBodyTypeTest(FixedFixture, TestCase):
    """리뷰 4. 본문이 JSON 객체가 아니면 400 invalid (500 이 나면 안 됨)"""

    def setUp(self):
        self.make_world()
        self.expense = self.make_expense()

    def test_not_object_body_is_400(self):
        targets = [("post", URL), ("patch", f"{URL}{self.expense.pk}/"),
                   ("post", f"{URL}confirm/"), ("post", f"{URL}ignore/")]
        for method, url in targets:
            for body in ([], [1, 2], 42, "hello", "", None):
                with self.subTest(method=method, url=url, body=body):
                    r = send(self.client, method, url, body)
                    self.assertEqual(r.status_code, 400, r.content)
                    self.assertEqual(r.json()["error"]["code"], "invalid")
        self.assertEqual(RecurringExpense.objects.count(), 1)


class FixedStateTest(FixedFixture, TestCase):
    """한 번에 하나씩 보내도 상태 규칙이 지켜지는지"""

    def setUp(self):
        self.make_world()

    def test_confirm_then_ignore_is_400(self):
        e = self.make_expense(status="suggested")
        self.assertEqual(send(self.client, "post", f"{URL}confirm/", {"recurringIds": [str(e.pk)]}).status_code, 200)
        self.assertEqual(send(self.client, "post", f"{URL}ignore/", {"recurringIds": [str(e.pk)]}).status_code, 400)
        e.refresh_from_db()
        self.assertEqual(e.status, "confirmed")

    def test_patch_after_stop_is_404(self):
        e = self.make_expense()
        self.assertEqual(self.client.delete(f"{URL}{e.pk}/").status_code, 204)
        self.assertEqual(send(self.client, "patch", f"{URL}{e.pk}/", {"amount": 800000}).status_code, 404)
        e.refresh_from_db()
        self.assertEqual((e.status, e.amount), ("stopped", 700000))


class FixedPaymentDueTest(FixedFixture, TestCase):
    """리뷰 2. 납부 예정 만들기 · 맞추기"""

    def setUp(self):
        self.make_world()
        self.future = self.today + timedelta(days=5)
        self.past = self.today - timedelta(days=5)

    def create(self, **body):
        data = {"name": "월세", "amount": 700000, "category": "HOUSING_TELECOM", **body}
        r = send(self.client, "post", URL, data)
        self.assertEqual(r.status_code, 201, r.content)
        return RecurringExpense.objects.get(pk=r.json()["recurringId"])

    def test_create_makes_this_cycle_due(self):
        e = self.create(paymentDay=self.future.day, paymentAccountId=str(self.account.pk))
        [due] = self.dues(e)
        self.assertEqual((due.due_date, due.confirmed_amount, due.payment_account_id, due.payment_state),
                         (self.future, 700000, self.account.pk, "unpaid"))
        self.assertEqual(due.occurrence_key, f"recurring:{e.pk}:{self.cycle.starts_on}")

    def test_create_unknown_method_also_makes_due(self):
        e = self.create(paymentDay=self.future.day)
        self.assertEqual(len(self.dues(e)), 1)

    def test_no_due_for_card_or_no_day_or_no_cycle(self):
        self.assertEqual(self.dues(self.create(paymentDay=self.future.day, paymentCardId=str(self.card.pk))), [])
        self.assertEqual(self.dues(self.create(paymentAccountId=str(self.account.pk))), [])
        self.cycle.delete()
        self.assertEqual(self.dues(self.create(paymentDay=self.future.day)), [])

    def test_past_day_without_withdrawal_makes_nothing(self):
        e = self.create(paymentDay=self.past.day, paymentAccountId=str(self.account.pk))
        self.assertEqual(self.dues(e), [])

    def test_past_day_with_one_withdrawal_is_paid(self):
        tx = self.make_tx(self.past + timedelta(days=1))
        e = self.create(paymentDay=self.past.day, paymentAccountId=str(self.account.pk))
        [due] = self.dues(e)
        self.assertEqual((due.payment_state, due.paid_amount), ("paid", 700000))
        self.assertTrue(PaymentMatch.objects.filter(due=due, transaction=tx).exists())
        self.assertEqual(e.evidence, [str(tx.pk)])

    def test_past_day_with_two_withdrawals_needs_review(self):
        self.make_tx(self.past)
        self.make_tx(self.past + timedelta(days=1))
        e = self.create(paymentDay=self.past.day, paymentAccountId=str(self.account.pk))
        [due] = self.dues(e)
        self.assertEqual(due.payment_state, "needs_review")
        self.assertFalse(PaymentMatch.objects.filter(due=due).exists())

    def test_confirm_candidate_makes_due(self):
        e = self.make_expense(status="suggested", payment_day=self.future.day, payment_account=self.account)
        r = send(self.client, "post", f"{URL}confirm/", {"recurringIds": [str(e.pk)]})
        self.assertEqual(r.status_code, 200, r.content)
        [due] = self.dues(e)
        self.assertEqual((due.due_date, due.payment_state), (self.future, "unpaid"))

    def test_update_moves_unpaid_due_date_and_account(self):
        e = self.create(paymentDay=self.future.day, paymentAccountId=str(self.account.pk))
        later = self.today + timedelta(days=7)
        r = send(self.client, "patch", f"{URL}{e.pk}/",
                 {"paymentDay": later.day, "paymentAccountId": str(self.account2.pk), "amount": 750000})
        self.assertEqual(r.status_code, 200, r.content)
        [due] = self.dues(e)
        self.assertEqual((due.due_date, due.payment_account_id, due.confirmed_amount), (later, self.account2.pk, 750000))

    def test_update_keeps_paid_due(self):
        e = self.make_expense(payment_day=self.past.day, payment_account=self.account)
        due = self.paid_due(e, self.make_tx(self.past))
        r = send(self.client, "patch", f"{URL}{e.pk}/",
                 {"paymentDay": self.future.day, "amount": 800000, "paymentAccountId": str(self.account2.pk)})
        self.assertEqual(r.status_code, 200, r.content)
        due.refresh_from_db()
        self.assertEqual((due.due_date, due.confirmed_amount, due.payment_account_id, due.payment_state),
                         (self.past, 700000, self.account.pk, "paid"))
        self.assertEqual(PaymentDue.objects.filter(recurring=e).count(), 1)   # 새 회차를 또 만들지 않음

    def test_update_keeps_partial_due(self):
        e = self.make_expense(payment_day=self.past.day, payment_account=self.account)
        due = self.paid_due(e, self.make_tx(self.past, amount=300000))
        PaymentDue.objects.filter(pk=due.pk).update(payment_state="partial", paid_amount=300000)
        send(self.client, "patch", f"{URL}{e.pk}/", {"paymentDay": self.future.day, "amount": 800000})
        due.refresh_from_db()
        self.assertEqual((due.due_date, due.confirmed_amount, due.payment_state), (self.past, 700000, "partial"))

    def test_day_set_later_makes_due(self):
        e = self.create(paymentAccountId=str(self.account.pk))
        self.assertEqual(self.dues(e), [])
        send(self.client, "patch", f"{URL}{e.pk}/", {"paymentDay": self.future.day})
        self.assertEqual(len(self.dues(e)), 1)

    def test_to_card_cancels_and_back_to_account_revives(self):
        e = self.create(paymentDay=self.future.day, paymentAccountId=str(self.account.pk))
        r = send(self.client, "patch", f"{URL}{e.pk}/", {"paymentAccountId": None, "paymentCardId": str(self.card.pk)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.dues(e), [])
        self.assertEqual(len(self.dues(e, "canceled")), 1)

        send(self.client, "patch", f"{URL}{e.pk}/", {"paymentCardId": None, "paymentAccountId": str(self.account2.pk)})
        [due] = self.dues(e)
        self.assertEqual((due.payment_account_id, due.canceled_at), (self.account2.pk, None))

    def test_clear_day_cancels_and_stop_cancels(self):
        e = self.create(paymentDay=self.future.day, paymentAccountId=str(self.account.pk))
        send(self.client, "patch", f"{URL}{e.pk}/", {"paymentDay": None})
        self.assertEqual(self.dues(e), [])

        e2 = self.create(paymentDay=self.future.day, paymentAccountId=str(self.account.pk))
        self.assertEqual(self.client.delete(f"{URL}{e2.pk}/").status_code, 204)
        self.assertEqual(self.dues(e2), [])


class FixedEvidenceTest(FixedFixture, TestCase):
    """리뷰 3. 근거 거래 고르기 · 바꾸기 · 비우기"""

    def setUp(self):
        self.make_world()
        self.past = self.today - timedelta(days=2)
        self.e = self.make_expense(payment_day=self.past.day, payment_account=self.account)

    def pick(self, tx, expense=None):
        expense = expense or self.e
        return send(self.client, "patch", f"{URL}{expense.pk}/", {"evidenceTransactionId": None if tx is None else str(tx.pk)})

    def test_pick_my_withdrawal_links_due(self):
        tx = self.make_tx(self.past)
        r = self.pick(tx)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["evidence"]["transactionId"], str(tx.pk))
        [due] = self.dues(self.e)
        self.assertEqual((due.payment_state, due.paid_amount), ("paid", 700000))
        self.assertTrue(PaymentMatch.objects.filter(due=due, transaction=tx).exists())

        # 같은 거래를 다시 보내도 그대로 성공 (짝이 두 개가 되지 않음)
        self.assertEqual(self.pick(tx).status_code, 200)
        self.assertEqual(PaymentMatch.objects.filter(due=due).count(), 1)

    def test_pick_card_payment(self):
        e = self.make_expense(name="OTT", amount=15000, payment_card=self.card, payment_day=self.past.day)
        tx = self.make_tx(self.past, amount=15000, card=self.card, merchant_name="구독 서비스")
        r = self.pick(tx, e)
        self.assertEqual(r.status_code, 200, r.content)
        e.refresh_from_db()
        self.assertEqual(e.evidence, [str(tx.pk)])
        self.assertEqual(self.dues(e), [])   # 카드는 납부 예정 없음

    def test_pick_invalid_is_400(self):
        other_user = get_user_model().objects.create_user(email="other@example.com", password="Example123!")
        other_conn = Connection.objects.create(user=other_user, business_type="bank", organization_code="0004", status="connected")
        other_account = BankAccount.objects.create(user=other_user, connection=other_conn, source_account_key="o-1",
                                                   account_type="demand", is_selected=True)
        used = self.make_tx(self.past)
        self.make_expense(name="관리비", evidence=[str(used.pk)])
        cases = {
            "남의 거래": self.make_tx(self.past, user=other_user, account=other_account),
            "다른 계좌": self.make_tx(self.past, account=self.account2),
            "입금": self.make_tx(self.past, direction="in"),
            "취소된 거래": self.make_tx(self.past, is_canceled=True),
            "다른 고정지출의 근거": used,
        }
        for label, tx in cases.items():
            with self.subTest(label):
                r = self.pick(tx)
                self.assertEqual(r.status_code, 400, r.content)
                self.assertEqual(r.json()["error"]["fields"]["evidenceTransactionId"], ["선택할 수 없는 거래예요."])

        unknown = self.make_expense(name="모름", payment_day=self.past.day)   # 결제 수단 모름
        self.assertEqual(self.pick(self.make_tx(self.past), unknown).status_code, 400)

    def test_clear_unlinks_payment_and_recalculates(self):
        tx = self.make_tx(self.past)
        self.pick(tx)
        r = self.pick(None)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json()["evidence"])
        [due] = self.dues(self.e)
        self.assertEqual((due.payment_state, due.paid_amount), ("needs_review", 0))   # 출금일 지남 → 확인 필요
        self.assertFalse(PaymentMatch.objects.filter(transaction=tx).exists())

    def test_clear_before_due_date_is_unpaid(self):
        future = self.today + timedelta(days=3)
        e = self.make_expense(name="통신비", amount=55000, payment_day=future.day, payment_account=self.account)
        tx = self.make_tx(self.today, amount=55000)
        self.pick(tx, e)
        self.pick(None, e)
        [due] = self.dues(e)
        self.assertEqual(due.payment_state, "unpaid")

    def test_change_replaces_this_cycle_link_only(self):
        last_cycle_tx = self.make_tx(self.cycle.starts_on - timedelta(days=20))
        old_due = PaymentDue.objects.create(
            user=self.user, obligation_type="recurring", recurring=self.e, occurrence_key="recurring:old",
            due_date=last_cycle_tx.occurred_on, confirmed_amount=700000, payment_account=self.account,
            source_type="manual", confirmation_state="confirmed", paid_amount=700000, payment_state="paid",
        )
        PaymentMatch.objects.create(user=self.user, due=old_due, transaction=last_cycle_tx, matched_amount=700000)
        RecurringExpense.objects.filter(pk=self.e.pk).update(evidence=[str(last_cycle_tx.pk)])

        first = self.make_tx(self.past)
        second = self.make_tx(self.past + timedelta(days=1), amount=710000)
        self.pick(first)
        r = self.pick(second)
        self.assertEqual(r.status_code, 200, r.content)

        [due] = PaymentDue.objects.filter(recurring=self.e, due_date__gte=self.cycle.starts_on)
        self.assertEqual(list(due.matches.values_list("transaction_id", flat=True)), [second.pk])
        self.assertEqual((due.payment_state, due.paid_amount), ("paid", 710000))
        # 지난 주기 기록은 그대로
        self.assertTrue(PaymentMatch.objects.filter(due=old_due, transaction=last_cycle_tx).exists())
        self.e.refresh_from_db()
        self.assertEqual(self.e.evidence, [str(last_cycle_tx.pk), str(second.pk)])

        # 비우면 이번 주기만 풀고 지난 주기 짝은 남김
        self.pick(None)
        self.assertTrue(PaymentMatch.objects.filter(due=old_due).exists())
        old_due.refresh_from_db()
        self.assertEqual(old_due.payment_state, "paid")


@skipUnless(connection.vendor == "postgresql", "행 잠금(select_for_update)은 PostgreSQL 에서만 확인")
class FixedConcurrencyTest(FixedFixture, TransactionTestCase):
    """리뷰 1. 같은 고정지출에 요청 두 개가 동시에 와도 상태가 덮어써지지 않는지

    'slow' 스레드는 고정지출을 저장하기 직전에 잠깐 멈춘다 → 그 사이에 다른 요청이 끼어든다.
    잠금이 없으면 끼어든 요청이 옛 상태를 보고 진행해서 결과가 꼬인다.
    """
    serialized_rollback = True   # 테스트마다 표를 비우므로 마이그레이션의 카테고리 초기 데이터를 되살림

    def setUp(self):
        self.make_world()

    def run_together(self, first, second, gap=0.1, pause=0.5):
        results = {}
        original_save = RecurringExpense.save

        def slow_save(obj, *args, **kwargs):
            if threading.current_thread().name == "slow":
                time.sleep(pause)
            return original_save(obj, *args, **kwargs)

        def worker(key, request):
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                results[key] = request(client).status_code
            finally:
                connection.close()

        with mock.patch.object(RecurringExpense, "save", slow_save):
            t1 = threading.Thread(target=worker, args=("first", first), name="slow")
            t2 = threading.Thread(target=worker, args=("second", second), name="fast")
            t1.start()
            time.sleep(gap)
            t2.start()
            t1.join(10)
            t2.join(10)
        return results["first"], results["second"]

    def test_patch_and_delete_do_not_revive_stopped(self):
        """수정 중에 중지가 끼어들어도 → 최종 상태는 중지"""
        e = self.make_expense()
        codes = self.run_together(
            lambda c: send(c, "patch", f"{URL}{e.pk}/", {"amount": 800000}),
            lambda c: c.delete(f"{URL}{e.pk}/"),
        )
        self.assertEqual(codes, (200, 204))
        e.refresh_from_db()
        self.assertEqual((e.status, e.amount), ("stopped", 800000))

    def test_delete_and_patch_patch_sees_stopped(self):
        """중지 중에 수정이 끼어들면 → 수정은 404, 상태는 중지"""
        e = self.make_expense()
        codes = self.run_together(
            lambda c: c.delete(f"{URL}{e.pk}/"),
            lambda c: send(c, "patch", f"{URL}{e.pk}/", {"amount": 800000}),
        )
        self.assertEqual(codes, (204, 404))
        e.refresh_from_db()
        self.assertEqual((e.status, e.amount), ("stopped", 700000))

    def test_confirm_and_ignore_only_one_wins(self):
        """같은 후보에 확정 · 무시가 동시에 → 하나만 200, 나머지는 400"""
        e = self.make_expense(status="suggested")
        body = {"recurringIds": [str(e.pk)]}
        codes = self.run_together(
            lambda c: send(c, "post", f"{URL}confirm/", body),
            lambda c: send(c, "post", f"{URL}ignore/", body),
        )
        self.assertEqual(codes, (200, 400))
        e.refresh_from_db()
        self.assertEqual(e.status, "confirmed")

    def test_batch_reverse_order_no_deadlock(self):
        """[A, B] 확정과 [B, A] 무시가 동시에 → 잠금 순서가 같아서 멈추지 않고 하나만 성공"""
        a = self.make_expense(status="suggested", name="A")
        b = self.make_expense(status="suggested", name="B")
        codes = self.run_together(
            lambda c: send(c, "post", f"{URL}confirm/", {"recurringIds": [str(a.pk), str(b.pk)]}),
            lambda c: send(c, "post", f"{URL}ignore/", {"recurringIds": [str(b.pk), str(a.pk)]}),
        )
        self.assertEqual(codes, (200, 400))
        self.assertEqual(sorted(RecurringExpense.objects.values_list("status", flat=True)), ["confirmed", "confirmed"])


class FixedTransferConvertTest(FixedFixture, TestCase):
    """2차 리뷰 2. 월세 이체를 연결하면 소비 + 고정비로, 풀면 원래 이체로"""

    def setUp(self):
        self.make_world()
        self.past = self.today - timedelta(days=2)
        self.e = self.make_expense(payment_day=self.past.day, payment_account=self.account)

    def detail(self, tx):
        r = self.client.get(f"/api/finance/transactions/{tx.pk}/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_link_converts_transfer_and_unlink_restores(self):
        tx = self.make_tx(self.past, source_description="집주인", confirmed_category_id="UNCLASSIFIED",
                          confirmation_state="needs_review")
        r = send(self.client, "patch", f"{URL}{self.e.pk}/", {"evidenceTransactionId": str(tx.pk)})
        self.assertEqual(r.status_code, 200, r.content)

        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("spending", "HOUSING_TELECOM", "confirmed", "user"))
        self.assertEqual(tx.field_states["original_transaction_type"], "transfer")
        item = self.detail(tx)
        self.assertEqual((item["funding"], item["fixedExpenseId"]), ("fixed", str(self.e.pk)))   # 소비 · 고정비로 보임

        # 고정지출에 연결된 동안은 거래 목록에서 이체로 되돌릴 수 없음 (기존 규칙 AA⑤)
        self.assertEqual(send(self.client, "patch", f"/api/finance/transactions/{tx.pk}/", {"category": None}).status_code, 400)

        # 연결을 풀면 원래 이체로
        self.assertEqual(send(self.client, "patch", f"{URL}{self.e.pk}/", {"evidenceTransactionId": None}).status_code, 200)
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("transfer", "UNCLASSIFIED", "needs_review", None))
        self.assertNotIn("original_transaction_type", tx.field_states)
        self.assertNotIn("converted_by_fixed", tx.field_states)

    def test_auto_link_on_create_converts_too(self):
        tx = self.make_tx(self.past)
        r = send(self.client, "post", URL, {"name": "월세", "amount": 700000, "category": "HOUSING_TELECOM",
                                             "paymentDay": self.past.day, "paymentAccountId": str(self.account.pk)})
        self.assertEqual(r.status_code, 201, r.content)
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.classification_source), ("spending", "rule"))
        self.assertEqual(self.detail(tx)["funding"], "fixed")

    def test_user_converted_spending_stays_spending_after_unlink(self):
        """사용자가 거래 목록에서 직접 소비로 바꾼 거래는 연결을 풀어도 소비 그대로"""
        tx = self.make_tx(self.past, transaction_type="spending", confirmed_category_id="FOOD",
                          field_states={"original_transaction_type": "transfer"})
        send(self.client, "patch", f"{URL}{self.e.pk}/", {"evidenceTransactionId": str(tx.pk)})
        send(self.client, "patch", f"{URL}{self.e.pk}/", {"evidenceTransactionId": None})
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_type, tx.confirmed_category_id), ("spending", "FOOD"))
        # 원래 표시는 그대로 + 연결을 푼 회차 표시(그 회차에 자동으로 다시 붙지 않게)만 더해짐
        states = dict(tx.field_states)
        self.assertEqual(len(states.pop("skip_auto_match")), 1)
        self.assertEqual(states, {"original_transaction_type": "transfer"})


class FixedEvidenceSuggestionTest(FixedFixture, TestCase):
    """2차 리뷰 3. 목록의 evidenceSuggestion — 고를 수 있는 후보 내려주기"""

    def setUp(self):
        self.make_world()
        self.due_day = self.today - timedelta(days=1)

    def suggestion(self, expense):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200, r.content)
        return next(i for i in r.json()["items"] if i["recurringId"] == str(expense.pk))["evidenceSuggestion"]

    def test_account_two_withdrawals_gives_candidates(self):
        e = self.make_expense(payment_day=self.due_day.day, payment_account=self.account)
        near = self.make_tx(self.due_day, amount=705000, source_description="집주인")
        far = self.make_tx(self.due_day - timedelta(days=2), amount=700000, source_description="집주인")
        self.make_tx(self.due_day, account=self.account2)                       # 다른 계좌 X
        self.make_tx(self.due_day, amount=500000)                               # 금액 차이 큼 X
        self.make_tx(self.due_day - timedelta(days=5))                          # 날짜 멀음 X
        s = self.suggestion(e)
        self.assertEqual(s["count"], 2)
        self.assertEqual([c["transactionId"] for c in s["candidates"]], [str(near.pk), str(far.pk)])   # 날짜 가까운 순
        self.assertEqual(s["candidates"][0], {"transactionId": str(near.pk), "merchantName": "집주인",
                                              "date": self.due_day.isoformat(), "amount": 705000})

        # 고르면 → 연결되고 제안은 사라짐
        send(self.client, "patch", f"{URL}{e.pk}/", {"evidenceTransactionId": str(near.pk)})
        self.assertIsNone(self.suggestion(e))

    def test_account_one_withdrawal_is_not_asked(self):
        e = self.make_expense(payment_day=self.due_day.day, payment_account=self.account)
        self.make_tx(self.due_day)
        self.assertIsNone(self.suggestion(e))

    def test_late_payment_on_needs_review_due(self):
        """출금일 +3일이 지난 회차는 늦게 낸 출금 1건도 고를 수 있게"""
        due_day = self.today - timedelta(days=6)
        e = self.make_expense(payment_day=due_day.day, payment_account=self.account)
        late = self.make_tx(self.today - timedelta(days=1))
        s = self.suggestion(e)
        self.assertEqual((s["count"], s["candidates"][0]["transactionId"]), (1, str(late.pk)))

    def test_card_without_evidence_one_payment(self):
        e = self.make_expense(name="OTT", amount=15000, payment_day=self.due_day.day, payment_card=self.card)
        tx = self.make_tx(self.due_day, amount=15000, card=self.card, merchant_name="구독 서비스")
        s = self.suggestion(e)
        self.assertEqual((s["count"], s["candidates"][0]["merchantName"]), (1, "구독 서비스"))
        RecurringExpense.objects.filter(pk=e.pk).update(evidence=[str(tx.pk)])
        self.assertIsNone(self.suggestion(e))   # 근거 거래가 있으면 묻지 않음

    def test_excludes_other_expense_and_caps_at_five(self):
        e = self.make_expense(payment_day=self.due_day.day, payment_account=self.account)
        taken = self.make_tx(self.due_day)
        self.make_expense(name="관리비", evidence=[str(taken.pk)])
        for _ in range(6):
            self.make_tx(self.due_day)
        s = self.suggestion(e)
        self.assertEqual((s["count"], len(s["candidates"])), (6, 5))
        self.assertNotIn(str(taken.pk), [c["transactionId"] for c in s["candidates"]])

    def test_no_suggestion_without_day_or_for_candidates(self):
        no_day = self.make_expense(payment_account=self.account)
        candidate = self.make_expense(status="suggested", payment_day=self.due_day.day, payment_account=self.account)
        self.make_tx(self.due_day)
        self.make_tx(self.due_day)
        self.assertIsNone(self.suggestion(no_day))
        self.assertIsNone(self.suggestion(candidate))


@skipUnless(connection.vendor == "postgresql", "행 잠금(select_for_update)은 PostgreSQL 에서만 확인")
class FixedAutoLinkConcurrencyTest(FixedFixture, TransactionTestCase):
    """2차 리뷰 1. 고정지출 두 개를 동시에 등록해도 같은 출금 하나가 두 번 '냄'이 되지 않음

    'slow' 스레드는 맞는 출금을 찾은 직후(잠그기 전) 잠깐 멈춘다 → 그 사이 다른 요청이 같은 출금을 연결하고 끝냄.
    잠근 뒤 다시 확인하지 않으면 slow 쪽도 같은 출금을 연결해 버린다.
    """
    serialized_rollback = True

    def setUp(self):
        self.make_world()
        self.past = self.today - timedelta(days=5)

    def test_same_withdrawal_is_linked_once(self):
        from budgets import api

        tx = self.make_tx(self.past)
        original = api.matching_payments

        def slow_matching(*args, **kwargs):
            found = original(*args, **kwargs)
            if threading.current_thread().name == "slow":
                time.sleep(0.5)
            return found

        body = lambda name: {"name": name, "amount": 700000, "category": "HOUSING_TELECOM",  # noqa: E731
                             "paymentDay": self.past.day, "paymentAccountId": str(self.account.pk)}
        results = {}

        def worker(key, name):
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                results[key] = send(client, "post", URL, body(name)).status_code
            finally:
                connection.close()

        with mock.patch.object(api, "matching_payments", slow_matching):
            t1 = threading.Thread(target=worker, args=("first", "월세 A"), name="slow")
            t2 = threading.Thread(target=worker, args=("second", "월세 B"), name="fast")
            t1.start()
            time.sleep(0.1)
            t2.start()
            t1.join(10)
            t2.join(10)

        self.assertEqual((results["first"], results["second"]), (201, 201))
        self.assertEqual(PaymentMatch.objects.filter(transaction=tx).count(), 1)
        self.assertEqual(PaymentDue.objects.filter(payment_state="paid").count(), 1)
        linked = [e.name for e in RecurringExpense.objects.all() if str(tx.pk) in (e.evidence or [])]
        self.assertEqual(len(linked), 1)
