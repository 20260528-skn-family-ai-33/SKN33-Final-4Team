import uuid
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from budgets.models import BudgetCycle
from budgets.protect import card_reserved
from .models import Card, CardApprovalReview, CardApprovalCoverage, Connection, FinancialTransaction, SyncRun
from . import test_card_sync as fixtures
from .test_card_sync import approval, ORG, END


class CardReviewTest(TestCase):
    setUpTestData = classmethod(fixtures.CardSyncTest.setUpTestData.__func__)
    setUp = fixtures.CardSyncTest.setUp
    response = fixtures.CardSyncTest.response
    run_sync = fixtures.CardSyncTest.run_sync
    runs = fixtures.CardSyncTest.runs

    def prepare(self, extra=()):
        self.approvals = [approval("1111********9999", resCardName="M포인트카드"), *extra]
        result = self.run_sync()
        self.client.force_login(self.user)
        self.card = Card.objects.get(user=self.user)
        self.review = CardApprovalReview.objects.get(user=self.user)
        return result

    def resolve(self, row=None, card_id=None):
        return self.client.put(
            f"/api/finance/card-reviews/{(row or self.review).pk}/",
            {"cardId": str(card_id or self.card.pk)}, content_type="application/json",
        )

    def test_review_then_budget_resumes_and_resync_preserves_resolution(self):
        result = self.prepare()
        self.assertEqual((result["approvals_complete"], result["card_review_count"]), (False, 1))
        self.assertFalse(FinancialTransaction.objects.filter(user=self.user).exists())
        Card.objects.filter(pk=self.card.pk).update(
            is_selected=True, first_connected_at=timezone.make_aware(datetime(2026, 10, 1)),
        )
        cycle = BudgetCycle(user=self.user, starts_on=date(2026, 10, 15), ends_before=date(2026, 11, 15))
        self.assertEqual(card_reserved(self.user, cycle, END)["pending"][0]["reason"], "card_assignment_required")
        response = self.resolve()
        self.assertEqual(response.status_code, 200)
        tx_id = response.json()["transactionId"]
        self.assertEqual(card_reserved(self.user, cycle, END), {"amount": 398000, "pending": []})
        self.assertEqual(self.runs("card_approval_list"), [(self.card.pk, date(2026, 7, 28), END)])
        self.assertEqual(self.resolve().json()["transactionId"], tx_id)
        self.approvals[0]["resCancelYN"] = "2"
        self.approvals[0]["resCancelAmount"] = "2000"
        self.assertTrue(self.run_sync()["approvals_complete"])
        tx = FinancialTransaction.objects.get(user=self.user)
        self.assertEqual((str(tx.pk), tx.canceled_amount), (tx_id, 2000))
        # 제공자가 다음 조회에서 정상 카드번호로 고쳐도 기존 거래를 유지한다.
        self.approvals[0]["resCardNo"] = "949098******0000"
        self.run_sync()
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 1)

    def test_same_unknown_card_number_does_not_resolve_another_transaction(self):
        self.approvals = [approval("1111********9999"), approval("1111********9999", resApprovalNo="another")]
        self.run_sync()
        self.client.force_login(self.user)
        self.card = Card.objects.get(user=self.user)
        rows = list(CardApprovalReview.objects.filter(user=self.user))
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.resolve(rows[0]).status_code, 200)
        self.assertFalse(self.runs("card_approval_list"))
        self.assertEqual(self.run_sync()["card_review_count"], 1)
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.resolve(rows[1]).status_code, 200)
        self.assertTrue(self.runs("card_approval_list"))

    def test_reissue_hint_preserves_pending_review_and_user_assignment(self):
        self.prepare()
        # 나중에 발급일이 들어와 다른 카드가 재발급 후보가 되어도 기존 확인 요청을 건너뛰지 않는다.
        self.cards = [fixtures.card(resIssueDate="20260101"),
                      fixtures.card("9490981234561111", resIssueDate="20261021")]
        self.approvals.append(approval("949098******1111", resApprovalNo="new-card",
                                       resUsedDate="20261025", resUsedAmount="5000"))
        result = self.run_sync()
        self.assertFalse(result["approvals_complete"])
        self.assertEqual(result["card_review_count"], 1)
        self.assertFalse(FinancialTransaction.objects.filter(user=self.user, amount=22000).exists())
        self.assertEqual(self.resolve().status_code, 200)
        self.review.refresh_from_db()
        original_id = self.review.transaction_id
        self.approvals[0].update(resCancelYN="2", resCancelAmount="2000")
        self.assertTrue(self.run_sync()["approvals_complete"])
        tx = FinancialTransaction.objects.get(user=self.user, amount=22000)
        self.assertEqual((tx.pk, tx.card_id, tx.canceled_amount), (original_id, self.card.pk, 2000))
        self.review.refresh_from_db()
        self.assertEqual(self.review.resolved_card_id, self.card.pk)

    def test_unknown_amount_is_not_reviewable_or_promoted_by_other_resolution(self):
        self.prepare([approval(resCancelYN="2", resApprovalNo="unknown")])
        self.assertEqual(CardApprovalReview.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.resolve().status_code, 200)
        self.assertFalse(self.runs("card_approval_list"))
        self.assertTrue(CardApprovalCoverage.objects.get().has_unknown_amount)
        self.assertIsNone(SyncRun.objects.get(product_code="card_sync").covered_from)

    def test_later_unknown_amount_prevents_using_old_payload(self):
        self.prepare()
        self.approvals[0]["resCancelYN"] = "2"
        self.run_sync()
        self.assertEqual(self.resolve().status_code, 400)
        self.assertFalse(self.client.get('/api/finance/card-reviews/').json()['items'][0]['canResolve'])
        self.assertFalse(self.runs("card_approval_list"))

    def test_list_has_name_hint_without_source_identifiers_or_provider_calls(self):
        self.prepare()
        calls = self.request.call_count
        response = self.client.get("/api/finance/card-reviews/")
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertTrue(item["candidates"][0]["nameMatches"])
        self.assertEqual(self.request.call_count, calls)
        for secret in ("sourceKey", "identity", "approvalNo", "1111********9999", "payload"):
            self.assertNotIn(secret, response.content.decode())

    def test_auth_consent_owner_and_candidate_validation(self):
        self.prepare()
        self.client.logout()
        self.assertEqual(self.client.get('/api/finance/card-reviews/').status_code, 403)
        self.client.force_login(self.user)
        other = get_user_model().objects.create_user(email="other-card-review@example.com")
        conn = Connection.objects.create(user=other, business_type="card", organization_code=ORG, status="connected")
        foreign = Card.objects.create(user=other, connection=conn, issuer_code=ORG, origin="provider")
        self.assertEqual(self.resolve(card_id=foreign.pk).status_code, 400)
        self.assertEqual(self.resolve(card_id=uuid.uuid4()).status_code, 400)
        alien = CardApprovalReview.objects.create(
            user=other, issuer_code=ORG, source_key="alien", identity=[], payload={},
            started_at=timezone.now(), fetched_at=timezone.now(),
        )
        self.assertEqual(self.resolve(alien).status_code, 404)
        other_conn = Connection.objects.create(user=self.user, business_type="card", organization_code="0306", status="connected")
        other_card = Card.objects.create(user=self.user, connection=other_conn, issuer_code="0306", origin="provider")
        self.assertEqual(self.resolve(card_id=other_card.pk).status_code, 400)
        self.consent.revoked_at = timezone.now()
        self.consent.save()
        self.assertEqual(self.resolve().status_code, 403)

    def test_different_card_after_confirmation_conflicts_and_disconnect_blocks(self):
        self.prepare()
        self.assertEqual(self.resolve().status_code, 200)
        second = Card.objects.create(user=self.user, connection=self.connection, issuer_code=ORG, origin="provider")
        self.assertEqual(self.resolve(card_id=second.pk).status_code, 409)
        self.connection.status, self.connection.disconnected_at = "disconnected", timezone.now()
        self.connection.save()
        self.assertEqual(self.resolve().status_code, 400)

    def test_reconnection_keeps_review_and_resolved_transaction(self):
        self.prepare()
        self.resolve()
        self.connection.status, self.connection.disconnected_at = "disconnected", timezone.now()
        self.connection.save()
        self.connection = Connection.objects.create(user=self.user, business_type="card", organization_code=ORG, status="connected")
        self.run_sync()
        self.assertEqual(CardApprovalReview.objects.filter(user=self.user).count(), 1)
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 1)

    def test_existing_transaction_is_reused_when_user_assigns_it(self):
        self.run_sync()
        self.approvals = [approval("1111********9999")]
        self.run_sync()
        self.client.force_login(self.user)
        self.card = Card.objects.get(user=self.user)
        self.review = CardApprovalReview.objects.get(user=self.user)
        self.resolve()
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 1)

    def test_unknown_fields_and_invalid_id_are_rejected(self):
        self.prepare()
        url = f"/api/finance/card-reviews/{self.review.pk}/"
        for body in ({"cardId": str(self.card.pk), "amount": 0}, {"cardId": True}, {}):
            self.assertEqual(self.client.put(url, body, content_type="application/json").status_code, 400)

    def test_identical_approval_attributes_on_different_cards_do_not_share_confirmation(self):
        self.approvals = [approval("1111********9999"), approval("2222********8888")]
        self.run_sync()
        self.client.force_login(self.user)
        self.card = Card.objects.get(user=self.user)
        # 전체 응답의 첫 행. 다음 조회에서는 순서가 뒤집힌다.
        row = next(row for row in CardApprovalReview.objects.filter(user=self.user) if row.identity[-1] == 1)
        self.resolve(row)
        self.approvals.reverse()
        self.assertEqual(self.run_sync()["card_review_count"], 1)
        self.assertEqual(CardApprovalReview.objects.filter(user=self.user).count(), 2)
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 1)
        self.assertFalse(self.runs("card_approval_list"))
