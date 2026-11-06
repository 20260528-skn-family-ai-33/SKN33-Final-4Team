"""거래 유형 판단 테스트 (pipeline.link_basics → finance/transfer_rules.py)."""
from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import pipeline
from .models import (BankAccount, Card, Connection, FinancialTransaction as Tx, Loan, PaymentDue, PaymentMatch,
                     RecurringExpense, TransactionLink)
from .tests import URL
from .transfer_rules import CARD_CANDIDATE, LOAN_CANDIDATE, OWN_REVIEW


class Fixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="rules@example.invalid")
        bank = Connection.objects.create(user=self.user, business_type="bank", organization_code="0004", status="connected")
        self.a = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="a")
        self.b = BankAccount.objects.create(user=self.user, connection=bank, source_account_key="b")
        self.card = Card.objects.create(user=self.user, origin="provider", issuer_code="0306")

    def bank(self, account, direction, amount=100_000, desc="이체", day=date(2026, 11, 3), user=None, **fields):
        return Tx.objects.create(
            user=user or self.user, source_kind="bank", account=account, occurred_on=day, amount=amount,
            direction=direction, source_description=desc,
            transaction_type="transfer" if direction == "out" else "income",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", **fields,
        )

    def approval(self, approval_no, day=date(2026, 11, 1), **fields):
        return Tx.objects.create(
            user=self.user, source_kind="card", card=self.card, occurred_on=day, amount=30_000, direction="out",
            approval_no=approval_no, transaction_type="spending",
            confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review", **fields,
        )

    def run_on(self, *txs):
        pipeline.link_basics(self.user, Tx.objects.filter(pk__in=[tx.pk for tx in txs]))
        for tx in txs:
            tx.refresh_from_db()


class OwnTransferSuggestionTest(Fixture):
    """R11: 상대 거래가 없어도 출금 적요가 가입 이름과 통째로 같으면 '내 계좌 이체 추정'만 표시 (유형은 transfer 그대로)"""

    def setUp(self):
        super().setUp()
        get_user_model().objects.filter(pk=self.user.pk).update(nickname=" 김 나은 ")

    def review(self, tx):
        tx.refresh_from_db()
        return tx.transaction_type, tx.field_states.get(OWN_REVIEW)

    def test_name_match_without_counterpart_is_only_suggested(self):
        out = self.bank(self.a, "out", desc="김나은")
        self.run_on(out)
        self.assertEqual(self.review(out), ("transfer", "suggested"))
        self.assertFalse(TransactionLink.objects.exists())
        self.assertEqual(Tx.objects.count(), 1)  # 상대 입금을 만들지 않음

    def test_spacing_is_normalized(self):
        out = self.bank(self.a, "out", desc="김나 은\t")
        self.run_on(out)
        self.assertEqual(self.review(out), ("transfer", "suggested"))

    def test_partial_or_empty_name_is_not_suggested(self):
        partial, other = self.bank(self.a, "out", desc="김나은상점"), self.bank(self.a, "out", desc="이하늘")
        income = self.bank(self.a, "in", desc="김나은")  # 입금은 이름 탐지 대상 아님
        self.run_on(partial, other, income)
        self.assertEqual([self.review(t)[1] for t in (partial, other, income)], [None, None, None])
        for empty in ("", None, "   "):
            with self.subTest(nickname=empty):
                get_user_model().objects.filter(pk=self.user.pk).update(nickname=empty)
                blank = self.bank(self.a, "out", desc=empty)
                self.run_on(blank)
                self.assertEqual(self.review(blank), ("transfer", None))

    def test_same_day_same_amount_alone_does_not_classify(self):
        out, income = self.bank(self.a, "out"), self.bank(self.b, "in")
        self.run_on(out, income)
        self.assertEqual((self.review(out), self.review(income)), (("transfer", None), ("income", None)))
        self.assertFalse(TransactionLink.objects.exists())

    def test_protected_withdrawals_are_not_suggested(self):
        canceled = self.bank(self.a, "out", desc="김나은", is_canceled=True)
        confirmed = self.bank(self.a, "out", desc="김나은")
        Tx.objects.filter(pk=confirmed.pk).update(confirmation_state="confirmed")
        paid = self.bank(self.a, "out", desc="김나은")
        due = PaymentDue.objects.create(
            user=self.user, obligation_type="manual", occurrence_key="k", due_date=date(2026, 11, 3),
            confirmed_amount=100_000, source_type="manual", confirmation_state="confirmed",
        )
        PaymentMatch.objects.create(user=self.user, due=due, transaction=paid, matched_amount=100_000)
        self.run_on(canceled, confirmed, paid)
        self.assertEqual([self.review(t)[1] for t in (canceled, confirmed, paid)], [None, None, None])

    def test_user_decision_survives_rerun_and_name_change(self):
        out = self.bank(self.a, "out", desc="김나은")
        self.run_on(out)
        Tx.objects.filter(pk=out.pk).update(field_states={OWN_REVIEW: "rejected"})
        self.run_on(out)
        get_user_model().objects.filter(pk=self.user.pk).update(nickname="다른이름")
        self.run_on(out)
        self.assertEqual(self.review(out), ("transfer", "rejected"))


class OwnTransferAutoTest(Fixture):
    """R11 (11/5 최종): 양쪽 거래가 다 있으면 같은 금액 · 5분 이내 · 양쪽 적요 모두 가입 이름 · 양쪽 유일 → auto"""

    def setUp(self):
        super().setUp()
        get_user_model().objects.filter(pk=self.user.pk).update(nickname="김나은")

    def pair(self, out_at=time(9), in_at=time(9, 1), out_desc="김나은", in_desc="김나은", in_day=date(2026, 11, 3), **fields):
        out = self.bank(self.a, "out", desc=out_desc, occurred_time=out_at, **fields)
        income = self.bank(self.b, "in", desc=in_desc, occurred_time=in_at, day=in_day, **fields)
        return out, income

    def kinds(self, *txs):
        for tx in txs:
            tx.refresh_from_db()
        return [(tx.transaction_type, tx.field_states.get(OWN_REVIEW)) for tx in txs]

    def test_both_sides_matched_are_auto_classified_once(self):
        out, income = self.pair(out_desc=" 김 나은", in_desc="김나은 ")
        self.run_on(out, income)
        self.assertEqual(self.kinds(out, income), [("own_transfer", "auto")] * 2)
        link = TransactionLink.objects.get()
        self.assertEqual((link.from_transaction_id, link.to_transaction_id, link.relation_type), (out.pk, income.pk, "OWN_TRANSFER"))
        self.run_on(out, income)
        self.assertEqual(TransactionLink.objects.count(), 1)

    def test_exactly_five_minutes_and_midnight(self):
        edge = self.pair(in_at=time(9, 5))
        late = self.pair(out_at=time(23, 58), in_at=time(0, 2), in_day=date(2026, 11, 4), amount=55_555)
        self.run_on(*edge, *late)
        self.assertEqual(self.kinds(*edge, *late), [("own_transfer", "auto")] * 4)

    def test_not_classified_without_all_conditions(self):
        cases = {
            "5분 초과": dict(in_at=time(9, 5, 1)),
            "시각 없음": dict(in_at=None),
            "이름 다름": dict(in_desc="이하늘"),
            "부분 일치": dict(in_desc="김나은상점"),
            "흔한 적요가 서로 같음": dict(out_desc="이체", in_desc="이체"),
            "한쪽만 이름": dict(out_desc="이체", in_desc="김나은"),
        }
        for label, fields in cases.items():
            with self.subTest(label):
                Tx.objects.all().delete()
                out, income = self.pair(**fields)
                self.run_on(out, income)
                self.assertEqual(self.kinds(income), [("income", None)])
                self.assertFalse(TransactionLink.objects.exists())
                # 이름이 맞는 출금은 일반 이체 그대로 '추정'으로만 남는다
                self.assertEqual(self.kinds(out)[0], ("transfer", "suggested" if fields.get("out_desc", "김나은") == "김나은" else None))

    def test_empty_nickname_never_auto_classifies(self):
        get_user_model().objects.filter(pk=self.user.pk).update(nickname="")
        out, income = self.pair(out_desc="", in_desc="")
        self.run_on(out, income)
        self.assertEqual(self.kinds(out, income), [("transfer", None), ("income", None)])

    def test_ambiguous_candidates_are_not_classified(self):
        out, income = self.pair()
        twin = self.bank(self.b, "in", desc="김나은", occurred_time=time(9, 2))  # 5분 안 입금 후보 둘
        self.run_on(out, income, twin)
        self.assertEqual(self.kinds(out)[0], ("transfer", "suggested"))  # 이름만 보고 추정으로 남음
        Tx.objects.all().delete()
        out, income = self.pair()
        self.bank(self.a, "out", desc="김나은", occurred_time=time(9, 3))  # 출금 후보 둘 (상대 쪽 유일성)
        self.run_on(out, income)
        self.assertFalse(TransactionLink.objects.exists())

    def test_same_account_or_other_user_is_not_a_pair(self):
        out = self.bank(self.a, "out", desc="김나은", occurred_time=time(9))
        same = self.bank(self.a, "in", desc="김나은", occurred_time=time(9))
        other = get_user_model().objects.create_user(email="rules-x@example.invalid", nickname="김나은")
        conn = Connection.objects.create(user=other, business_type="bank", organization_code="0004", status="connected")
        theirs = self.bank(BankAccount.objects.create(user=other, connection=conn, source_account_key="x"), "in",
                           desc="김나은", occurred_time=time(9), user=other)
        self.run_on(out, same)
        self.assertEqual(self.kinds(same, theirs), [("income", None)] * 2)
        self.assertFalse(TransactionLink.objects.exists())

    def test_late_counterpart_upgrades_suggestion_in_any_order(self):
        for first in ("out", "in"):
            with self.subTest(first=first):
                Tx.objects.all().delete()
                out, income = self.pair()
                for group in ([out], [income]) if first == "out" else ([income], [out]):
                    self.run_on(*group)
                self.assertEqual(self.kinds(out, income), [("own_transfer", "auto")] * 2)

    def collect_three(self, order):
        """출금 1 · 입금 후보 2 를 순서대로 받은 최종 판단"""
        Tx.objects.all().delete()
        out, first = self.pair()
        second = self.bank(self.b, "in", desc="김나은", occurred_time=time(9, 3))
        for group in {"together": [[out, first, second]], "split": [[out, first], [second]],
                      "late_out": [[first, second], [out]], "one_by_one": [[first], [out], [second]]}[order]:
            self.run_on(*group)
        return self.kinds(out, first, second), TransactionLink.objects.count()

    def test_together_and_split_collection_end_the_same(self):
        expected = ([("transfer", "suggested"), ("income", None), ("income", None)], 0)
        for order in ("together", "split", "late_out", "one_by_one"):
            with self.subTest(order=order):
                self.assertEqual(self.collect_three(order), expected)

    def test_recheck_keeps_unique_pairs_and_user_decisions(self):
        out, income = self.pair()
        self.run_on(out, income)
        far = self.bank(self.b, "in", desc="김나은", occurred_time=time(9, 30))  # 5분 밖 — 영향 없음
        self.run_on(far)
        self.assertEqual(self.kinds(out, income), [("own_transfer", "auto")] * 2)
        Tx.objects.filter(pk=out.pk).update(field_states={OWN_REVIEW: "confirmed"}, confirmation_state="confirmed")
        twin = self.bank(self.b, "in", desc="김나은", occurred_time=time(9, 2))
        self.run_on(twin)
        self.assertEqual(self.kinds(out, income, twin), [("own_transfer", "confirmed"), ("own_transfer", "auto"), ("income", None)])
        self.assertEqual(TransactionLink.objects.count(), 1)

    def test_recheck_does_not_unlink_saving_or_payment_evidence(self):
        from .models import RecurringSaving
        from .transfer_rules import OWN_RECHECK
        for protect in ("saving_type", "saving_evidence", "payment"):
            with self.subTest(protect=protect):
                Tx.objects.all().delete()
                out, income = self.pair()
                self.run_on(out, income)
                if protect == "saving_type":
                    Tx.objects.filter(pk=out.pk).update(transaction_type="saving")
                elif protect == "saving_evidence":
                    RecurringSaving.objects.create(user=self.user, name="적금", amount=100_000, status="suggested",
                                                   source_type="detected", evidence=[str(out.pk)])
                else:
                    due = PaymentDue.objects.create(
                        user=self.user, obligation_type="manual", occurrence_key="k", due_date=date(2026, 11, 3),
                        confirmed_amount=100_000, source_type="manual", confirmation_state="confirmed",
                    )
                    PaymentMatch.objects.create(user=self.user, due=due, transaction=income, matched_amount=100_000)
                self.run_on(self.bank(self.b, "in", desc="김나은", occurred_time=time(9, 2)))
                out.refresh_from_db()
                self.assertTrue(TransactionLink.objects.filter(from_transaction=out).exists())
                self.assertIs(out.field_states.get(OWN_RECHECK), True)
                self.assertEqual(out.field_states[OWN_REVIEW], "auto")
                RecurringSaving.objects.all().delete()
                PaymentMatch.objects.all().delete()

    def test_user_decisions_and_protected_rows_are_left_alone(self):
        out, income = self.pair()
        Tx.objects.filter(pk=out.pk).update(field_states={OWN_REVIEW: "rejected"})
        self.run_on(out, income)
        self.assertEqual(self.kinds(out, income), [("transfer", "rejected"), ("income", None)])
        Tx.objects.all().delete()
        out, income = self.pair()
        Tx.objects.filter(pk=income.pk).update(confirmation_state="confirmed")
        self.run_on(out, income)
        self.assertFalse(TransactionLink.objects.exists())
        Tx.objects.all().delete()
        out, income = self.pair()
        due = PaymentDue.objects.create(
            user=self.user, obligation_type="manual", occurrence_key="k", due_date=date(2026, 11, 3),
            confirmed_amount=100_000, source_type="manual", confirmation_state="confirmed",
        )
        PaymentMatch.objects.create(user=self.user, due=due, transaction=out, matched_amount=100_000)
        self.run_on(out, income)
        self.assertEqual(self.kinds(out, income), [("transfer", None), ("income", None)])

    def test_unlinked_pair_is_not_linked_again(self):
        out, income = self.pair()
        self.run_on(out, income)
        self.client.force_login(self.user)
        res = self.client.patch(f"{URL}{out.pk}/", data='{"ownTransfer": false}', content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        self.run_on(out, income)
        self.assertEqual(self.kinds(out, income), [("transfer", "rejected"), ("income", "rejected")])
        self.assertFalse(TransactionLink.objects.exists())


class DescriptionRuleTest(Fixture):
    def test_card_issuer_name_marks_candidate_only(self):
        # R11 (11/06): 이름만으로는 card_payment 로 바꾸지 않는다 — 이체로 남아 목록에 보이고, 청구서에 연결될 때 바뀐다
        for desc, code in (("신한카드", "0306"), ("KB카드출금", "0301"), ("현대 카드", "0302"), ("bc카드 대금", "0305"), ("비씨카드", "0305")):
            with self.subTest(desc=desc):
                tx = self.bank(self.a, "out", desc=desc)
                self.run_on(tx)
                self.assertEqual((tx.transaction_type, tx.field_states.get(CARD_CANDIDATE)), ("transfer", code))

    def test_lender_name_marks_candidate_only_for_my_active_loans(self):
        Loan.objects.create(user=self.user, origin="manual", lender_name="한국장학재단")
        Loan.objects.create(user=self.user, origin="manual", lender_name="웰컴저축은행", status="repaid")
        repayment, repaid = self.bank(self.a, "out", desc="한국장학재단 상환"), self.bank(self.a, "out", desc="웰컴저축은행")
        self.run_on(repayment, repaid)
        self.assertEqual((repayment.transaction_type, repaid.transaction_type), ("transfer", "transfer"))
        self.assertIn(LOAN_CANDIDATE, repayment.field_states)
        self.assertNotIn(LOAN_CANDIDATE, repaid.field_states)

    def test_candidate_stays_listed_and_editable_until_really_matched(self):
        Loan.objects.create(user=self.user, origin="manual", lender_name="국민은행")
        tx = self.bank(self.a, "out", desc="국민은행 홍길동", day=timezone.localdate())
        self.run_on(tx)
        self.client.force_login(self.user)
        self.assertIn(str(tx.pk), [item["transactionId"] for item in self.client.get(URL).json()["items"]])
        self.assertEqual(self.client.get(f"{URL}{tx.pk}/").status_code, 200)
        fixed = self.client.patch(f"{URL}{tx.pk}/", data='{"category": "FOOD"}', content_type="application/json")
        self.assertEqual(fixed.status_code, 200)

    def test_friend_transfer_and_deposits_stay(self):
        friend, deposit = self.bank(self.a, "out", desc="이하늘"), self.bank(self.a, "in", desc="신한카드 캐시백", amount=500)
        self.run_on(friend, deposit)
        self.assertEqual(friend.transaction_type, "transfer")
        self.assertEqual(deposit.transaction_type, "income")

    def test_same_amount_deposit_does_not_override_description(self):
        out, income = self.bank(self.a, "out", desc="신한카드"), self.bank(self.b, "in")
        self.run_on(out, income)
        self.assertEqual((out.transaction_type, income.transaction_type), ("transfer", "income"))
        self.assertIn(CARD_CANDIDATE, out.field_states)


class CollectionOrderTest(Fixture):
    """같은 거래를 한 번에 받든 나눠 받든 결과가 같아야 한다"""

    def collect(self, desc, amount, order):
        out, income = self.bank(self.a, "out", amount=amount, desc=desc), self.bank(self.b, "in", amount=amount)
        for group in {"together": [[out, income]], "out_first": [[out], [income]], "in_first": [[income], [out]]}[order]:
            self.run_on(*group)
        out.refresh_from_db()
        income.refresh_from_db()
        return out.transaction_type, income.transaction_type, TransactionLink.objects.count()

    def test_day_and_amount_pair_is_never_linked_in_any_order(self):
        Loan.objects.create(user=self.user, origin="manual", lender_name="한국장학재단")
        amount = 100_000
        for desc, kind in (("신한카드", "transfer"), ("한국장학재단", "transfer"), ("이체", "transfer")):
            for order in ("together", "out_first", "in_first"):
                with self.subTest(desc=desc, order=order):
                    amount += 1
                    self.assertEqual(self.collect(desc, amount, order), (kind, "income", 0))

    def test_late_deposit_does_not_touch_settled_withdrawals(self):
        paid = self.bank(self.a, "out", desc="신한카드")
        self.run_on(paid)
        due = PaymentDue.objects.create(
            user=self.user, obligation_type="manual", occurrence_key="paid", due_date=date(2026, 11, 3),
            confirmed_amount=100_000, source_type="manual", confirmation_state="confirmed",
        )
        PaymentMatch.objects.create(user=self.user, due=due, transaction=paid, matched_amount=100_000)
        confirmed = self.bank(self.a, "out", desc="신한카드", amount=200_000)
        self.run_on(confirmed)
        Tx.objects.filter(pk=confirmed.pk).update(confirmation_state="confirmed")
        late = self.bank(self.b, "in"), self.bank(self.b, "in", amount=200_000)
        self.run_on(*late)
        paid.refresh_from_db()
        confirmed.refresh_from_db()
        self.assertEqual([tx.transaction_type for tx in (paid, confirmed, *late)],
                         ["transfer", "transfer", "income", "income"])  # 카드값 후보 이체 그대로 (청구서 없음)
        self.assertFalse(TransactionLink.objects.exists())


class SamePurchaseTest(Fixture):
    def setUp(self):
        super().setUp()
        Card.objects.filter(pk=self.card.pk).update(name="NH 체크")

    def buy(self, name="김밥집", amount=9_000):
        return Tx.objects.create(
            user=self.user, source_kind="card", card=self.card, occurred_on=date(2026, 11, 3), amount=amount, direction="out",
            merchant_name=name, transaction_type="spending", confirmed_category_id="FOOD", confirmation_state="suggested",
        )

    def links(self):
        return TransactionLink.objects.filter(relation_type="SAME_PURCHASE")

    def test_check_card_and_its_withdrawal_become_one_row(self):
        card, bank = self.buy(), self.bank(self.a, "out", 9_000, "김밥 집")
        friend = self.bank(self.a, "out", 9_000, "홍길동")
        self.run_on(card, bank, friend)
        link = self.links().get()
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (card, bank, 9_000))
        self.client.force_login(self.user)
        items = self.client.get(URL + "?from=2026-11-01&to=2026-11-30").json()
        self.assertEqual(sorted(i["transactionType"] for i in items["items"]), ["spending", "transfer"])  # 친구 송금은 남음
        self.assertEqual(items["summary"]["spentTotal"], 9_000)

    def test_links_whichever_side_is_saved_first(self):
        bank = self.bank(self.a, "out", 9_000, "김밥집")
        self.run_on(bank)
        self.assertFalse(self.links().exists())
        card = self.buy()
        self.run_on(card)
        self.run_on(card)
        self.assertEqual(self.links().count(), 1)

    def test_not_linked_when_only_amount_and_day_match_or_it_is_ambiguous(self):
        self.run_on(self.buy("다른가게"), self.bank(self.a, "out", 9_000, "김밥집"))
        self.assertFalse(self.links().exists())
        self.bank(self.a, "out", 9_000, "다른가게", occurred_time=time(1, 0))
        self.bank(self.a, "out", 9_000, "다른가게", occurred_time=time(2, 0))  # 출금 두 건이면 어느 쪽인지 모름
        self.run_on(self.buy("다른가게", 9_000))
        self.assertFalse(self.links().exists())

    def test_credit_card_and_settled_withdrawals_stay_apart(self):
        Card.objects.filter(pk=self.card.pk).update(name="NH 신용")
        self.run_on(self.buy(), self.bank(self.a, "out", 9_000, "김밥집"))
        self.assertFalse(self.links().exists())
        Card.objects.filter(pk=self.card.pk).update(name=None, card_type="체크/본인")
        self.run_on(self.buy("분식집", 8_000), self.bank(self.a, "out", 8_000, "분식집"))
        self.assertEqual(self.links().filter(matched_amount=8_000).count(), 1)  # 카드 종류 칸의 '체크'도 본다

    def test_late_same_withdrawal_unlinks_the_ambiguous_pair(self):
        # PR #87 리뷰: 나눠서 들어와도 한 번에 받을 때와 결과가 같아야 한다. 어느 출금이 카드 결제인지 모르면 잇지 않는다
        card, first = self.buy(), self.bank(self.a, "out", 9_000, "김밥집")
        self.run_on(card, first)
        self.assertEqual(self.links().count(), 1)
        late = self.bank(self.b, "out", 9_000, "김밥집")
        self.run_on(late)
        self.assertFalse(self.links().exists())
        self.client.force_login(self.user)
        rows = self.client.get(URL + "?from=2026-11-01&to=2026-11-30").json()["items"]
        self.assertEqual(sum(i["transactionType"] == "transfer" for i in rows), 2)  # 두 출금 모두 다시 보인다

    def test_same_purchase_withdrawal_is_not_reused_as_own_transfer(self):
        # PR #87 리뷰: 같은 날 · 같은 금액 입금이 다른 계좌에 있어도 한 출금에 두 관계가 붙지 않는다
        card, out = self.buy(), self.bank(self.a, "out", 9_000, "김밥집")
        income = self.bank(self.b, "in", 9_000, "입금")
        self.run_on(card, out, income)
        self.assertEqual(self.links().count(), 1)
        self.assertFalse(TransactionLink.objects.filter(relation_type="OWN_TRANSFER").exists())
        self.assertEqual((out.transaction_type, income.transaction_type), ("transfer", "income"))

    def test_withdrawal_the_user_confirmed_is_left_alone(self):
        bank = self.bank(self.a, "out", 7_000, "빵집")
        Tx.objects.filter(pk=bank.pk).update(confirmation_state="confirmed")
        self.run_on(self.buy("빵집", 7_000), bank)
        self.assertFalse(self.links().exists())


class CancellationTest(Fixture):
    def test_links_cancel_row_to_original_by_approval_number(self):
        original = self.approval("A1")
        cancel = self.approval("A1", day=date(2026, 11, 2), is_canceled=True, canceled_amount=30_000)
        self.run_on(cancel)
        link = TransactionLink.objects.get(relation_type="CANCELLATION")
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (original, cancel, 30_000))

    def test_original_saved_after_cancel_row_is_linked_once(self):
        cancel = self.approval("A1", day=date(2026, 11, 2), is_canceled=True, canceled_amount=30_000)
        self.run_on(cancel)
        self.assertFalse(TransactionLink.objects.exists())
        original = self.approval("A1")
        self.run_on(original)
        self.run_on(original, cancel)
        link = TransactionLink.objects.get(relation_type="CANCELLATION")
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (original, cancel, 30_000))

    def test_rerun_keeps_one_link(self):
        self.approval("A1")
        cancel = self.approval("A1", is_canceled=True)
        self.run_on(cancel)
        self.run_on(cancel)
        self.assertEqual(TransactionLink.objects.count(), 1)

    def cancel_row(self, amount=12_000, desc="택시", when=time(20, 0, 1), **fields):
        return self.bank(self.a, "out", amount, desc, occurred_time=when, is_canceled=True, canceled_amount=amount, **fields)

    def test_bank_cancel_row_cancels_its_original(self):
        # 선결제 뒤 환불이 음수 출금으로 옴. 한 쌍이 0원이 되고 뒤의 실제 결제는 남는다
        original = self.bank(self.a, "out", 12_000, "택시", occurred_time=time(20, 0, 0))
        cancel = self.cancel_row()
        real = self.bank(self.a, "out", 9_500, "택시", occurred_time=time(20, 5, 0))
        self.run_on(original, cancel, real)
        link = TransactionLink.objects.get(relation_type="CANCELLATION")
        self.assertEqual((link.from_transaction, link.to_transaction, link.matched_amount), (original, cancel, 12_000))
        self.assertEqual((original.is_canceled, original.canceled_amount), (True, 12_000))
        self.assertFalse(real.is_canceled)

    def test_bank_cancel_picks_nearest_earlier_not_later_rebooking(self):
        # 결제 → 취소 → 같은 금액 재결제: 취소 뒤의 재결제는 짝이 아니다
        first = self.bank(self.a, "out", 50_000, "예약", occurred_time=time(9, 0, 0))
        cancel = self.cancel_row(50_000, "예약", time(9, 30, 0))
        again = self.bank(self.a, "out", 50_000, "예약", occurred_time=time(9, 30, 30))
        self.run_on(first, cancel, again)
        self.assertEqual((first.is_canceled, again.is_canceled), (True, False))

    def test_bank_cancel_finds_original_in_the_same_second_or_saved_later(self):
        cancel = self.cancel_row(7_000, when=time(18, 0, 0))
        self.run_on(cancel)
        self.assertFalse(TransactionLink.objects.exists())
        original = self.bank(self.a, "out", 7_000, "택시", occurred_time=time(18, 0, 0))
        self.run_on(original)
        self.run_on(original)
        self.assertEqual(TransactionLink.objects.filter(relation_type="CANCELLATION").count(), 1)
        self.assertTrue(original.is_canceled)

    def test_bank_cancel_needs_same_account_description_amount(self):
        others = [self.bank(self.b, "out", 12_000, "택시", occurred_time=time(1, 0)),
                  self.bank(self.a, "out", 12_000, "편의점", occurred_time=time(1, 0)),
                  self.bank(self.a, "out", 9_000, "택시", occurred_time=time(1, 0))]
        self.run_on(*others, self.cancel_row())
        self.assertFalse(TransactionLink.objects.filter(relation_type="CANCELLATION").exists())
        self.assertFalse(any(tx.is_canceled for tx in others))

    def mark_paid(self, tx):
        due = PaymentDue.objects.create(user=self.user, obligation_type="recurring", occurrence_key="k", due_date=date(2026, 11, 3),
                                        confirmed_amount=12_000, paid_amount=12_000, payment_state="paid", obligation_state="active",
                                        recurring=RecurringExpense.objects.create(user=self.user, name="x", amount=12_000, category_id="FOOD",
                                                                                  status="confirmed", source_type="manual"))
        return PaymentMatch.objects.create(user=self.user, due=due, transaction=tx, matched_amount=12_000)

    def test_paid_due_withdrawal_is_not_canceled(self):
        original = self.bank(self.a, "out", 12_000, "택시", occurred_time=time(1, 0))
        self.mark_paid(original)
        self.run_on(original, self.cancel_row())
        self.assertFalse(original.is_canceled)

    def test_bank_cancel_holds_instead_of_canceling_an_older_payment(self):
        # 어제 정상 결제 → 오늘 납부에 붙은 결제 → 오늘 취소: 어제 결제를 대신 취소하지 않는다
        yesterday = self.bank(self.a, "out", 12_000, "택시", day=date(2026, 11, 2), occurred_time=time(20, 0, 0))
        today = self.bank(self.a, "out", 12_000, "택시", occurred_time=time(20, 0, 0))
        paid = self.mark_paid(today)
        cancel = self.cancel_row()
        self.run_on(yesterday, today, cancel)
        self.assertEqual((yesterday.is_canceled, today.is_canceled), (False, False))
        self.assertFalse(TransactionLink.objects.filter(relation_type="CANCELLATION").exists())
        # 납부 연결이 풀리면 다음 처리 때 오늘 결제에 이어진다
        paid.delete()
        self.run_on(yesterday, today, cancel)
        self.assertEqual((yesterday.is_canceled, today.is_canceled), (False, True))

    def test_no_link_without_separate_original(self):
        in_place = self.approval("A1", is_canceled=True)         # 카드사가 원거래 줄을 취소로 바꿔 준 경우
        no_number = self.approval(None, is_canceled=True)
        self.approval("A2")
        self.run_on(in_place, no_number)
        self.assertFalse(TransactionLink.objects.exists())
