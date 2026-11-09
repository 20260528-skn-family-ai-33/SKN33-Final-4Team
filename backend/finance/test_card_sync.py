import json
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import serializers
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from users.models import Consent

from . import codef
from .card_review import FOREIGN_PENDING_RUN, approval_blocker, foreign_pending_count
from .codef_cards import CardDataError, card_matches, month_range, normalize_approvals, normalize_card_bills, normalize_cards
from .models import (Card, CardApprovalCoverage, CardApprovalReview, CardBill, CardBillItem, Connection,
                     FinancialTransaction, SyncRun)
from .sync_cards import CardConnectionGone, CardConsentRequired, sync_card

CARD_NO = "9490981234560000"
CARD_PW = "4321"
ORG = "0302"
START, END = date(2026, 6, 1), date(2026, 10, 28)
MONTHS = ("202609", "202610")


def card(number=CARD_NO, **change):
    return {"resCardNo": number, "resSleepYN": "N", "resCardName": "M포인트카드", "resCardType": "본인신용",
            "resTrafficYN": "", "resImageLink": "", "resIssueDate": "", "resValidPeriod": "20290531",
            "resState": "정상", **change}


def charge(**change):
    return {"resFee": "0", "resMemberStoreName": "서울강남", "resUsedAmount": "17,100", "resPaymentType": "1",
            "resInstallmentMonth": "0", "resRoundNo": "", "resPaymentAmt": "", "resAfterPaymentBalance": "0",
            "resEarnPoint": "", "resUsedCard": "1791", "resPaymentPrincipal": "17100", "resApprovalNo": "",
            "resUsedDate": "20260912", **change}


def approval(number="949098******0000", **change):
    return {"resUsedDate": "20261020", "resUsedTime": "120117", "resCardNo": number, "resCardNo1": "",
            "resCardName": "", "resMemberStoreName": "온라인예매", "resUsedAmount": "22,000", "resPaymentType": "1",
            "resInstallmentMonth": "", "resAccountCurrency": "KRW", "resApprovalNo": "69331111",
            "resPaymentDueDate": "", "resHomeForeignType": "1", "resCancelYN": "0", "resCancelAmount": "",
            "resKRWAmt": "", "commStartDate": "20260728", "commEndDate": "20261028", **change}


def bill(due="20261025", items=None, **change):
    return {"resTotalAmount": "420,000", "resBillType": "0", "resPaymentAccount": "은행10020000*****",
            "resFullAmt": "360000", "resInstallmentAmt": "60000", "resWithdrawalDueDate": "",
            "resPaymentDueDate": due,
            "resChargeHistoryList": [charge()] if items is None else items, **change}


class CardNormalizationTest(SimpleTestCase):
    def test_single_object_list_and_empty_response(self):
        self.assertEqual(len(normalize_cards(card())), 1)
        self.assertEqual(len(normalize_cards([card(), card("9490981234561111")])), 2)
        self.assertEqual(normalize_cards({}), [])
        for data in [None, "x", [None], [card(), "x"]]:
            with self.subTest(data=data), self.assertRaises(CardDataError):
                normalize_cards(data)

    def test_card_number_is_masked_and_not_kept(self):
        result = normalize_cards(card(resCardName=f"카드 {CARD_NO}"))[0]
        self.assertEqual(result["masked_number"], "****0000")
        # 원문 번호는 승인내역 대조용으로만 들고 있고(number), 저장 칸에는 없다
        self.assertNotIn(CARD_NO, json.dumps({k: v for k, v in result.items() if k != "number"}))
        # 하이픈 · 공백 표시는 같은 카드
        same = normalize_cards(card("9490-9812-3456-0000"))[0]
        self.assertEqual(same["source_card_key"], result["source_card_key"])

    def test_missing_or_duplicate_card_number_is_rejected(self):
        for rows in [[card("")], [card(None)], [card(), card("9490 9812 3456 0000")]]:
            with self.subTest(rows=rows), self.assertRaises(CardDataError) as error:
                normalize_cards(rows)
            self.assertEqual(error.exception.code, "ambiguous_card_identity")
            self.assertNotIn(CARD_NO, str(error.exception))

    def test_long_provider_codes_are_not_cut(self):
        result = normalize_cards(card(resCardType="가" * 21, resState=""))[0]
        self.assertIsNone(result["card_type"])
        self.assertIsNone(result["card_status"])

    def test_bill_amounts_dates_and_empty_values(self):
        result = normalize_card_bills(bill(resWithdrawalDueDate="20261026"), "202610", ORG)[0]
        self.assertEqual(result["billing_month"], date(2026, 10, 1))
        self.assertEqual(result["total_amount"], 420000)
        self.assertEqual(result["payment_due_date"], date(2026, 10, 25))
        self.assertEqual(result["withdrawal_due_date"], date(2026, 10, 26))
        self.assertIsNone(normalize_card_bills(bill(), "202610", ORG)[0]["withdrawal_due_date"])
        self.assertEqual(normalize_card_bills(bill(resTotalAmount="-1,200"), "202610", ORG)[0]["total_amount"], -1200)
        self.assertEqual(normalize_card_bills(bill(resTotalAmount="0"), "202610", ORG)[0]["total_amount"], 0)

    def test_bill_without_total_or_with_bad_values_is_rejected(self):
        for change in [{"resTotalAmount": ""}, {"resTotalAmount": "1.5"}, {"resPaymentDueDate": "2026-10-25"},
                       {"resPaymentDueDate": "20260231"}, {"resChargeHistoryList": [charge(resUsedAmount="abc")]},
                       {"resChargeHistoryList": [charge(resInstallmentMonth="3", resRoundNo="4")]}]:
            with self.subTest(change=change), self.assertRaises(CardDataError):
                normalize_card_bills(bill(**change), "202610", ORG)

    def test_month_must_be_yyyymm(self):
        for month in ["20261001", "2026-10", "202613", 202610]:
            with self.subTest(month=month), self.assertRaises(ValueError):
                normalize_card_bills(bill(), month, ORG)

    def test_same_statement_twice_is_ambiguous(self):
        with self.assertRaises(CardDataError) as error:
            normalize_card_bills([bill(), bill()], "202610", ORG)
        self.assertEqual(error.exception.code, "ambiguous_bill_identity")
        # 명세서 구분이 다르면 다른 청구서 (예: 신한 카드론 별도 명세서)
        self.assertEqual(len(normalize_card_bills([bill(), bill(resBillType="1")], "202610", ORG)), 2)

    def test_statement_key_ignores_amount_changes(self):
        first = normalize_card_bills(bill(), "202610", ORG)[0]["statement_key"]
        later = normalize_card_bills(bill(resTotalAmount="430000"), "202610", ORG)[0]["statement_key"]
        self.assertEqual(first, later)
        self.assertNotEqual(first, normalize_card_bills(bill(), "202609", ORG)[0]["statement_key"])

    def test_items(self):
        items = normalize_card_bills(bill(items=[
            charge(),
            charge(resPaymentType="2", resInstallmentMonth="3", resRoundNo="1", resUsedAmount="180000",
                   resPaymentPrincipal="60000", resFee="1200", resPaymentAmt="61200"),
            charge(resPaymentType="5", resPaymentPrincipal="", resUsedDate=""),
        ]), "202610", ORG)[0]["items"]
        one, installment, other = items
        self.assertEqual((one["item_type"], one["original_purchase_amount"], one["billed_amount"]), ("one_time", 17100, 17100))
        self.assertEqual(one["field_states"], {"billedAmount": {"source": "derived"}})
        self.assertIsNone(one["installment_months"])
        self.assertEqual((installment["item_type"], installment["installment_months"], installment["installment_round"],
                          installment["billed_amount"]), ("installment", 3, 1, 61200))
        self.assertEqual(installment["field_states"], {})
        # 결제금액 · 원금이 없으면 0으로 채우지 않는다
        self.assertEqual((other["item_type"], other["billed_amount"], other["purchased_on"]), ("unknown", None, None))

    def test_missing_items_differs_from_zero_items(self):
        self.assertIsNone(normalize_card_bills(bill(resChargeHistoryList=""), "202610", ORG)[0]["items"])
        self.assertIsNone(normalize_card_bills({k: v for k, v in bill().items() if k != "resChargeHistoryList"},
                                               "202610", ORG)[0]["items"])
        self.assertEqual(normalize_card_bills(bill(items=[]), "202610", ORG)[0]["items"], [])

    def test_identical_items_get_different_keys(self):
        items = normalize_card_bills(bill(items=[charge(), charge()]), "202610", ORG)[0]["items"]
        self.assertEqual(len({item["source_item_key"] for item in items}), 2)

    def test_items_state(self):
        def state(**change):
            return normalize_card_bills(bill(**change), "202610", ORG)[0]["items_state"]

        self.assertEqual(state(resChargeHistoryList=""), "unavailable")
        self.assertEqual(state(items=[]), "unavailable")
        self.assertEqual(state(items=[], resTotalAmount="0"), "complete")
        self.assertEqual(state(resTotalAmount="17100"), "complete")
        self.assertEqual(state(), "partial")   # 상세 합계 17,100 ≠ 총액 420,000
        self.assertEqual(state(resTotalAmount="17100", items=[charge(resPaymentPrincipal="")]), "partial")

    def test_month_range(self):
        self.assertEqual(month_range("202609", date(2026, 10, 28)), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(month_range("202610", date(2026, 10, 28)), (date(2026, 10, 1), date(2026, 10, 28)))
        self.assertEqual(month_range("202612", date(2027, 1, 5)), (date(2026, 12, 1), date(2026, 12, 31)))


class ApprovalNormalizationTest(SimpleTestCase):
    def test_masked_number_matches_only_same_digits(self):
        self.assertTrue(card_matches(CARD_NO, "949098******0000"))
        self.assertTrue(card_matches("949098******0000", "9490********0000"))
        self.assertFalse(card_matches(CARD_NO, "949098******1111"))
        self.assertFalse(card_matches(CARD_NO, "949098******00000"))

    def test_whole_won_amount_with_zero_decimals(self):
        # 해외 가맹점 원화 결제는 12345.00 처럼 온다. 이 한 줄 때문에 조회 전체가 저장되지 않았다
        self.assertEqual(normalize_approvals(approval(resUsedAmount="12345.00"), START, END)[0][0]["amount"], 12345)
        self.assertEqual(normalize_card_bills(bill(resTotalAmount="-1,200.00"), "202610", ORG)[0]["total_amount"], -1200)
        with self.assertRaises(CardDataError):  # 소수부가 있는 원화는 어떤 값인지 몰라 받지 않는다
            normalize_approvals(approval(resUsedAmount="12345.67"), START, END)

    def test_spending_row_and_actual_period(self):
        rows, covered = normalize_approvals(approval(), START, END)
        row = rows[0]
        self.assertEqual(covered, (date(2026, 7, 28), END))   # 카드사가 준 기간이 요청보다 짧으면 그만큼만
        self.assertEqual((row["occurred_on"], row["amount"], row["merchant_name"]), (date(2026, 10, 20), 22000, "온라인예매"))
        self.assertEqual((row["installment_months"], row["approval_no"], row["unknown_reason"]), (0, "69331111", None))
        self.assertEqual((row["is_canceled"], row["canceled_amount"]), (False, None))
        self.assertEqual(normalize_approvals({}, START, END), ([], (START, END)))

    def test_cancel_follows_existing_rule(self):
        cases = [
            ({"resCancelYN": "1"}, (True, None)),                            # 취소액 없음 → 전액 (cancel_info)
            ({"resCancelYN": "1", "resCancelAmount": "22000"}, (True, 22000)),
            ({"resCancelYN": "2", "resCancelAmount": "2,000"}, (False, 2000)),
            ({"resCancelYN": "3"}, (True, 22000)),                           # 거절은 쓰지 않은 돈
        ]
        for change, expected in cases:
            with self.subTest(change=change):
                row = normalize_approvals(approval(**change), START, END)[0][0]
                self.assertEqual((row["is_canceled"], row["canceled_amount"]), expected)
                self.assertIsNone(row["unknown_reason"])

    def test_amount_that_cannot_be_trusted_is_marked(self):
        cases = [
            ({"resCancelYN": "2"}, "cancel_amount_unknown"),                 # 신한: 부분취소 금액 미제공
            ({"resUsedAmount": "-22000"}, "negative_amount"),                # KB 취소전표매입
            ({"resUsedAmount": ""}, "amount_unknown"),
            ({"resAccountCurrency": "USD", "resUsedAmount": "15.00"}, "foreign_amount_pending"),
        ]
        for change, reason in cases:
            with self.subTest(change=change):
                self.assertEqual(normalize_approvals(approval(**change), START, END)[0][0]["unknown_reason"], reason)
        foreign = normalize_approvals(approval(resAccountCurrency="USD", resUsedAmount="15.00", resKRWAmt="20,100"),
                                      START, END)[0][0]
        self.assertEqual((foreign["amount"], foreign["unknown_reason"]), (20100, None))

    def test_bad_rows_are_rejected(self):
        for change in [{"resUsedDate": ""}, {"resCardNo": ""}, {"resCancelYN": "2", "resCancelAmount": "30000"},
                       {"commStartDate": "20261101", "commEndDate": "20261028"}]:
            with self.subTest(change=change), self.assertRaises(CardDataError):
                normalize_approvals(approval(**change), START, END)

    def test_identity_ignores_cancel_status(self):
        first = normalize_approvals(approval(), START, END)[0][0]["identity"]
        canceled = normalize_approvals(approval(resCancelYN="1", resCancelAmount="22000"), START, END)[0][0]["identity"]
        self.assertEqual(first, canceled)
        twice = normalize_approvals([approval(), approval()], START, END)[0]
        self.assertNotEqual(twice[0]["identity"], twice[1]["identity"])


@override_settings(CODEF={"ENV": "demo", "CONNECTED_ID": "test-id", "PUBLIC_KEY": "test-key"})
class ApprovalStoreTypeTest(SimpleTestCase):
    def test_store_type_is_kept_out_of_identity(self):
        with_type = normalize_approvals(approval(resMemberStoreType="약국", resMemberStoreAddr="주소"), START, END)[0][0]
        without = normalize_approvals(approval(), START, END)[0][0]
        self.assertEqual(with_type["merchant_category"], "약국")
        self.assertIsNone(without["merchant_category"])
        self.assertEqual(with_type["identity"], without["identity"])
        self.assertNotIn("주소", str(with_type))  # 주소는 저장하지 않는다

    def test_retry_without_store_info_only_for_other_errors(self):
        calls = []

        def fake(path, params):
            calls.append(params["memberStoreInfoType"])
            if params["memberStoreInfoType"] == "1":
                raise codef.CodefError(code, "실패")
            return approval()

        for code, retried in (("CF-99999", True), ("CF-12100", False), ("CF-03002", False), ("CF-12401", False)):
            calls.clear()
            with self.subTest(code), patch("finance.codef._request", side_effect=fake):
                if retried:
                    self.assertEqual(len(codef.list_card_approvals("cid", "0304", START, END)[0]), 1)
                    self.assertEqual(calls, ["1", "0"])
                else:
                    with self.assertRaises(codef.CodefError):
                        codef.list_card_approvals("cid", "0304", START, END)
                    self.assertEqual(calls, ["1"])


class CardRequestTest(SimpleTestCase):
    def setUp(self):
        encrypt = patch("finance.codef.encrypt_rsa", side_effect=lambda value, key: f"enc({len(value)})")
        encrypt.start()
        self.addCleanup(encrypt.stop)

    def test_card_values_go_only_into_request(self):
        with patch("finance.codef._request", return_value=card()) as request:
            codef.list_cards("cid", ORG, CARD_NO, CARD_PW)
        path, params = request.call_args.args
        self.assertEqual(path, codef.PATH_CARD_LIST)
        self.assertEqual(params["cardNo"], CARD_NO)
        self.assertEqual(params["cardPassword"], "enc(4)")
        with patch("finance.codef._request", return_value=bill()) as request:
            codef.list_card_bills("cid", ORG, "202610")
        path, params = request.call_args.args
        self.assertEqual((path, params["startDate"]), (codef.PATH_CARD_BILLS, "202610"))
        self.assertNotIn("cardNo", params)
        self.assertNotIn("cardPassword", params)

    def test_approvals_use_login_card_number(self):
        with patch("finance.codef._request", return_value=approval()) as request:
            codef.list_card_approvals("cid", ORG, START, END, CARD_NO, CARD_PW)
        path, params = request.call_args.args
        self.assertEqual(path, codef.PATH_CARD_APPROVALS)
        self.assertEqual((params["startDate"], params["endDate"], params["inquiryType"]), ("20260601", "20261028", "1"))
        # cardNo 는 '카드별 조회' 대상 카드라 인증용은 loginCardNo 로 보낸다
        self.assertEqual((params["loginCardNo"], params["cardPassword"]), (CARD_NO, "enc(4)"))
        self.assertNotIn("cardNo", params)

    def test_day_unit_month_is_not_sent(self):
        with patch("finance.codef._request") as request, self.assertRaises(ValueError):
            codef.list_card_bills("cid", ORG, "20261001")
        request.assert_not_called()


@override_settings(CODEF={"ENV": "demo", "CONNECTED_ID": "test-id", "PUBLIC_KEY": "test-key"})
class CardSyncTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email=f"card-sync-{uuid.uuid4().hex}@example.com")
        cls.connection = Connection.objects.create(
            user=cls.user, business_type="card", organization_code=ORG, status="connected",
        )
        cls.consent = Consent.objects.create(
            user=cls.user, purpose="finance_inquiry", product_scope="card",
            document_version="test", document_hash="test-only",
        )

    def setUp(self):
        self.cards = [card()]
        self.bills = {"202609": bill(due="20260925"), "202610": bill()}
        self.approvals = [approval()]
        request = patch("finance.codef._request", side_effect=self.response)
        self.request = request.start()
        self.addCleanup(request.stop)
        encrypt = patch("finance.codef.encrypt_rsa", side_effect=lambda value, key: "encrypted")
        encrypt.start()
        self.addCleanup(encrypt.stop)
        # 저장 뒤 분류는 test_classifier 에서 본다. 모델 파일이 없는 PC 에서도 같은 결과가 나오게 '판단 못 함'으로 고정
        classify = patch("finance.classifier.predict", side_effect=lambda names: [[] for _ in names])
        classify.start()
        self.addCleanup(classify.stop)

    def response(self, path, params):
        if path == codef.PATH_CARD_LIST:
            return deepcopy(self.cards)
        if path == codef.PATH_CARD_APPROVALS:
            return deepcopy(self.approvals)
        return deepcopy(self.bills[params["startDate"]])

    def runs(self, product_code):
        return sorted(SyncRun.objects.filter(user=self.user, product_code=product_code, status="success")
                      .values_list("card_id", "covered_from", "covered_to"))

    def run_sync(self, **kwargs):
        kwargs.setdefault("connection_id", self.connection.pk)
        return sync_card(self.user, "cid", ORG, START, END, MONTHS, **kwargs)

    def test_store_type_fills_saved_transaction_once(self):
        self.run_sync()
        tx = FinancialTransaction.objects.get(user=self.user)
        self.assertIsNone(tx.merchant_category)
        self.approvals = [approval(resMemberStoreType="약국")]
        self.run_sync()
        tx.refresh_from_db()
        self.assertEqual(tx.merchant_category, "약국")
        self.approvals = [approval(resMemberStoreType="편의점")]
        self.run_sync()
        tx.refresh_from_db()
        self.assertEqual(tx.merchant_category, "약국")  # 있는 값은 바꾸지 않는다

    def foreign_row(self, **change):
        return approval(resApprovalNo="2", resAccountCurrency="USD", resUsedAmount="15.00", **change)

    def test_foreign_row_without_won_amount_does_not_block_coverage(self):
        # 11/07 NH: USD 줄은 원화 금액이 끝내 비어 있어 다시 불러와도 지킬 돈 화면을 벗어나지 못했다
        self.approvals = [approval(), self.foreign_row()]
        result = self.run_sync()
        self.assertTrue(result["approvals_complete"])
        self.assertEqual(result["foreign_pending_count"], 1)
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 1)  # 금액을 만들어 저장하지 않는다
        self.assertIsNone(approval_blocker(self.user, ORG))
        self.assertEqual(len(self.runs("card_approval_list")), 1)
        self.assertEqual(self.runs(FOREIGN_PENDING_RUN), [])  # 조회 범위 계산에 들어가지 않는다
        self.assertEqual(foreign_pending_count(self.user, ORG, START, END), 1)

    def test_repeat_sync_counts_a_foreign_row_once_and_clears_it_when_won_amount_arrives(self):
        self.approvals = [approval(), self.foreign_row()]
        self.run_sync()
        self.run_sync()
        self.assertEqual(foreign_pending_count(self.user, ORG, START, END), 1)
        self.approvals = [approval(), self.foreign_row(resKRWAmt="20,100")]
        self.assertEqual(self.run_sync()["foreign_pending_count"], 0)
        self.assertEqual(foreign_pending_count(self.user, ORG, START, END), 0)
        self.assertTrue(FinancialTransaction.objects.filter(user=self.user, amount=20100).exists())

    def test_other_untrusted_amounts_still_block_coverage(self):
        for change in ({"resUsedAmount": ""}, {"resUsedAmount": "-22000"}, {"resCancelYN": "2"}):
            with self.subTest(change=change):
                CardApprovalCoverage.objects.all().delete()
                self.approvals = [approval(resApprovalNo="3", **change)]
                result = self.run_sync()
                self.assertFalse(result["approvals_complete"])
                self.assertEqual(result["foreign_pending_count"], 0)
                self.assertEqual(approval_blocker(self.user, ORG), "spending_history_incomplete")

    def test_foreign_row_with_user_review_keeps_blocking_when_won_amount_disappears(self):
        # 사용자가 확인할 승인의 금액이 사라지면 이전 금액으로 확인하지 못하게 그대로 막는다
        self.approvals = [self.foreign_row(number="949098******9999", resKRWAmt="20,100")]
        self.run_sync()
        self.approvals = [self.foreign_row(number="949098******9999")]
        result = self.run_sync()
        self.assertFalse(result["approvals_complete"])
        self.assertEqual(result["foreign_pending_count"], 0)
        self.assertFalse(CardApprovalReview.objects.get(user=self.user).data_valid)

    def test_saves_cards_bills_and_items(self):
        result = self.run_sync(card_no=CARD_NO, card_password=CARD_PW)
        self.assertEqual((result["card_count"], result["created_card_count"]), (1, 1))
        self.assertEqual((result["bill_count"], result["created_bill_count"]), (2, 2))
        saved = Card.objects.get(user=self.user)
        self.assertEqual((saved.connection, saved.issuer_code, saved.origin), (self.connection, ORG, "provider"))
        self.assertEqual((saved.masked_number, saved.card_type, saved.card_status), ("****0000", "본인신용", "정상"))
        self.assertIsNotNone(saved.first_connected_at)
        self.assertFalse(saved.is_selected)
        october = CardBill.objects.get(user=self.user, billing_month=date(2026, 10, 1))
        self.assertEqual((october.total_amount, october.payment_due_date), (420000, date(2026, 10, 25)))
        self.assertEqual((october.issuer_code, october.source_type), (ORG, "provider"))
        # 명세서 단위라 카드사 통합 청구. 카드별로 나누지 않고, 확정 여부는 CODEF 가 안 줘서 비움
        self.assertEqual((october.card, october.scope_type, october.confirmation_state, october.items_state),
                         (None, "issuer", None, "partial"))
        self.assertEqual(october.items.get().billed_amount, 17100)
        tx = FinancialTransaction.objects.get(user=self.user)
        self.assertEqual((tx.card, tx.source_kind, tx.direction, tx.transaction_type), (saved, "card", "out", "spending"))
        self.assertEqual((tx.amount, tx.occurred_on, tx.approval_no, tx.confirmation_state),
                         (22000, date(2026, 10, 20), "69331111", "needs_review"))
        run = SyncRun.objects.get(user=self.user, product_code="card_sync")
        self.assertEqual((run.status, run.requested_from, run.requested_to), ("success", START, END))
        self.assertEqual((run.covered_from, run.covered_to), (date(2026, 7, 28), END))
        # 선차감(P-12)이 읽는 카드별 조회 기록
        self.assertEqual(self.runs("card_bill_list"), [
            (saved.pk, date(2026, 9, 1), date(2026, 9, 30)), (saved.pk, date(2026, 10, 1), END),
        ])
        self.assertEqual(self.runs("card_approval_list"), [(saved.pk, date(2026, 7, 28), END)])

    def test_refresh_keeps_ids_selection_and_first_connection(self):
        self.run_sync()
        saved = Card.objects.get(user=self.user)
        first_at = saved.first_connected_at
        Card.objects.filter(pk=saved.pk).update(is_selected=True)
        item = CardBillItem.objects.get(bill__billing_month=date(2026, 10, 1))
        self.cards = [card(resState="분실")]
        self.bills["202610"] = bill(resTotalAmount="430000", items=[charge(), charge(resMemberStoreName="편의점")])
        result = self.run_sync()
        self.assertEqual((result["created_card_count"], result["updated_card_count"]), (0, 1))
        self.assertEqual((result["created_bill_count"], result["updated_bill_count"]), (0, 2))
        saved.refresh_from_db()
        self.assertEqual((saved.card_status, saved.is_selected, saved.first_connected_at), ("분실", True, first_at))
        october = CardBill.objects.get(user=self.user, billing_month=date(2026, 10, 1))
        self.assertEqual(october.total_amount, 430000)
        self.assertEqual(october.items.count(), 2)
        self.assertTrue(october.items.filter(pk=item.pk).exists())
        # 이번 응답에 없는 항목은 지운다
        self.bills["202610"] = bill(items=[charge(resMemberStoreName="편의점")])
        self.run_sync()
        self.assertFalse(CardBillItem.objects.filter(pk=item.pk).exists())

    def test_missing_items_keep_saved_items(self):
        self.run_sync()
        self.bills["202610"] = bill(resChargeHistoryList="")
        self.run_sync()
        self.assertEqual(CardBillItem.objects.filter(bill__billing_month=date(2026, 10, 1)).count(), 1)

    def test_reconnect_moves_card_and_keeps_first_connection(self):
        self.run_sync()
        saved = Card.objects.get(user=self.user)
        Connection.objects.filter(pk=self.connection.pk).update(status="disconnected", disconnected_at=timezone.now())
        again = Connection.objects.create(user=self.user, business_type="card", organization_code=ORG, status="connected")
        self.run_sync(connection_id=again.pk)
        moved = Card.objects.get(user=self.user)
        self.assertEqual((moved.pk, moved.connection_id, moved.first_connected_at),
                         (saved.pk, again.pk, saved.first_connected_at))
        self.assertEqual(CardBill.objects.filter(user=self.user).count(), 2)
        self.assertFalse(CardBill.objects.filter(user=self.user).exclude(connection=again).exists())

    def test_other_issuer_and_other_user_are_not_reused(self):
        other_user = get_user_model().objects.create_user(email=f"card-other-{uuid.uuid4().hex}@example.com")
        for user, org in [(other_user, ORG), (self.user, "0306")]:
            conn = Connection.objects.create(user=user, business_type="card", organization_code=org,
                                             status="disconnected", disconnected_at=timezone.now())
            Card.objects.create(user=user, connection=conn, origin="provider",
                                source_card_key=normalize_cards(card())[0]["source_card_key"])
        self.run_sync()
        self.assertEqual(Card.objects.filter(user=self.user, connection=self.connection).count(), 1)
        self.assertEqual(Card.objects.filter(connection__disconnected_at__isnull=False).count(), 2)

    def test_disconnected_connection_is_not_recreated(self):
        Connection.objects.filter(pk=self.connection.pk).update(status="disconnected", disconnected_at=timezone.now())
        with self.assertRaises(CardConnectionGone):
            self.run_sync()
        self.request.assert_not_called()
        self.assertFalse(Connection.objects.filter(user=self.user, disconnected_at__isnull=True).exists())

    def test_consent_is_checked_before_fetch_and_before_save(self):
        Consent.objects.filter(pk=self.consent.pk).update(revoked_at=timezone.now())
        with self.assertRaises(CardConsentRequired):
            self.run_sync()
        self.request.assert_not_called()
        Consent.objects.filter(pk=self.consent.pk).update(revoked_at=None)

        def revoke_while_fetching(path, params):
            Consent.objects.filter(pk=self.consent.pk).update(revoked_at=timezone.now())
            return self.response(path, params)

        self.request.side_effect = revoke_while_fetching
        with self.assertRaises(PermissionError):
            self.run_sync()
        self.assertFalse(Card.objects.filter(user=self.user).exists())
        self.assertEqual(SyncRun.objects.get(user=self.user, product_code="card_sync", status="failed").error_code,
                         "consent_required")

    def test_provider_error_saves_nothing(self):
        def fail_second_month(path, params):
            if params.get("startDate") == "202610":
                raise codef.CodefError("CF-13001", "입력 형식 오류", CARD_NO)
            return self.response(path, params)

        self.request.side_effect = fail_second_month
        with self.assertRaises(codef.CodefError):
            self.run_sync(card_no=CARD_NO, card_password=CARD_PW)
        self.assertFalse(Card.objects.filter(user=self.user).exists())
        self.assertFalse(CardBill.objects.filter(user=self.user).exists())
        run = SyncRun.objects.get(user=self.user, product_code="card_sync")
        self.assertEqual((run.status, run.business_code, run.error_code), ("failed", "CF-13001", "provider_error"))

    def test_bad_response_saves_nothing(self):
        self.bills["202610"] = bill(resTotalAmount="")
        with self.assertRaises(CardDataError):
            self.run_sync()
        self.assertFalse(Card.objects.filter(user=self.user).exists())
        self.assertEqual(SyncRun.objects.get(user=self.user, product_code="card_sync").error_code, "bill_total_missing")

    def test_card_values_are_not_stored(self):
        self.cards = [card(resCardName=f"카드 {CARD_NO}")]
        self.run_sync(card_no=CARD_NO, card_password=CARD_PW)
        dump = serializers.serialize("json", [
            *Card.objects.all(), *CardBill.objects.all(), *CardBillItem.objects.all(), *SyncRun.objects.all(),
        ])
        self.assertNotIn(CARD_NO, dump)
        self.assertNotIn(CARD_PW, dump)

    def test_older_run_does_not_overwrite_newer_save(self):
        self.run_sync()
        saved = Card.objects.get(user=self.user)
        bill_row = CardBill.objects.get(user=self.user, billing_month=date(2026, 10, 1))
        future = timezone.now() + timedelta(minutes=5)
        SyncRun.objects.filter(card=saved).update(started_at=future)
        CardBill.objects.filter(pk=bill_row.pk).update(field_states={"codefStartedAt": future.isoformat()})
        self.cards = [card(resState="분실")]
        self.bills["202610"] = bill(resTotalAmount="1")
        result = self.run_sync()
        self.assertEqual((result["skipped_card_count"], result["skipped_bill_count"]), (1, 1))
        saved.refresh_from_db()
        bill_row.refresh_from_db()
        self.assertEqual((saved.card_status, bill_row.total_amount), ("정상", 420000))

    def test_refresh_updates_cancel_only(self):
        self.run_sync()
        tx = FinancialTransaction.objects.get(user=self.user)
        FinancialTransaction.objects.filter(pk=tx.pk).update(confirmed_category_id="UNCLASSIFIED", confirmation_state="confirmed")
        self.approvals = [approval(resCancelYN="2", resCancelAmount="2000")]
        result = self.run_sync()
        self.assertEqual((result["new_transaction_count"], result["existing_transaction_count"]), (0, 1))
        tx.refresh_from_db()
        self.assertEqual((tx.is_canceled, tx.canceled_amount, tx.confirmation_state), (False, 2000, "confirmed"))

    def test_unattributable_approval_leaves_spending_incomplete(self):
        self.cards = [card(), card("9490981234561111", resCardName="다른 카드")]
        for rows in ([approval(), approval("123456******9999")],              # 어느 카드인지 모름
                     [approval(), approval("949098**********")],              # 두 카드 다 될 수 있음
                     [approval(), approval(resCancelYN="2")]):                # 부분취소 금액 모름
            with self.subTest(rows=rows):
                self.approvals = rows
                result = self.run_sync()
                self.assertFalse(result["approvals_complete"])
                self.assertEqual(self.runs("card_approval_list"), [])
                # 확실한 줄은 저장하고, 청구서 기록은 그대로 남긴다
                self.assertTrue(FinancialTransaction.objects.filter(user=self.user, amount=22000).exists())
                self.assertEqual(len(self.runs("card_bill_list")) % 4, 0)
                run = SyncRun.objects.filter(user=self.user, product_code="card_sync").latest("started_at")
                self.assertEqual((run.covered_from, run.covered_to), (None, None))

    def test_rows_before_the_reissue_date_go_to_the_only_used_card(self):
        # 재발급 전 카드는 목록에 없고 승인내역 번호가 짧게 온다. 발급일보다 앞선 거래만 재발급 전 거래로 본다
        self.cards = [card(resIssueDate="20261001")]
        self.approvals = [approval(),
                          approval("123", resApprovalNo="2", resUsedAmount="5000", resUsedDate="20260912"),
                          approval("123", resApprovalNo="3", resUsedAmount="7000", resUsedDate="20261015")]
        result = self.run_sync()
        self.assertEqual(sorted(FinancialTransaction.objects.filter(user=self.user).values_list("amount", flat=True)), [5000, 22000])
        self.assertFalse(result["approvals_complete"])  # 발급일 뒤인데 번호가 안 맞는 줄은 저장하지 않는다
        self.run_sync()
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 2)  # 다시 불러와도 그대로

    def test_card_issued_before_the_row_is_not_its_successor(self):
        # 거래일 전에 이미 있던 카드는 번호가 다르니 그 거래의 카드가 아니다. 뒤에 발급된 카드에 붙인다
        self.cards = [card(resIssueDate="20261001"), card("9490981234561111", resCardName="이전 카드", resIssueDate="20260101")]
        self.approvals = [approval(), approval("123", resApprovalNo="2", resUsedAmount="5000", resUsedDate="20260912")]
        self.run_sync()
        self.assertEqual(FinancialTransaction.objects.get(user=self.user, amount=5000).card.masked_number, "****0000")

    def test_reissue_after_saving_does_not_duplicate_the_old_rows(self):
        # PR #86 리뷰: 재발급 전 카드로 저장 → 재발급 → 다시 조회해도 같은 승인을 또 만들지 않는다
        old_row = approval("949098******2222", resApprovalNo="2", resUsedAmount="5000", resUsedDate="20260912")
        self.cards, self.approvals = [card("9490981234562222")], [old_row]
        self.run_sync()
        self.cards = [card(resIssueDate="20261001")]
        self.approvals = [approval(), old_row]
        self.run_sync()
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user, amount=5000).count(), 1)
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user).count(), 2)
        old_row["resCancelYN"] = "1"   # 재발급 전 거래의 취소도 그 거래에 반영된다
        self.run_sync()
        self.assertTrue(FinancialTransaction.objects.get(user=self.user, amount=5000).is_canceled)

    def test_old_card_still_listed_does_not_duplicate_the_saved_row(self):
        # PR #86 재리뷰: 예전 카드가 보유 목록에 남아 있어도 같은 승인을 찾아 재사용하고, 취소도 그 거래에 반영한다
        old_card = card("9490981234562222", resIssueDate="20260101")
        old_row = approval("949098******2222", resApprovalNo="2", resUsedAmount="5000", resUsedDate="20260927")
        self.cards, self.approvals = [old_card], [old_row]
        self.run_sync()
        self.cards = [old_card, card(resIssueDate="20261001")]
        short_row = {**old_row, "resCardNo": "123"}
        self.approvals = [approval(), short_row]
        result = self.run_sync()
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user, amount=5000).count(), 1)
        self.assertTrue(result["approvals_complete"])
        short_row["resCancelYN"] = "1"
        self.run_sync()
        self.assertTrue(FinancialTransaction.objects.get(user=self.user, amount=5000).is_canceled)

    def test_same_approval_saved_on_two_cards_is_left_alone(self):
        # 같은 승인이 두 카드에 있으면 어느 거래인지 몰라 재사용도 새로 저장도 하지 않는다
        first, second = card("9490981234562222", resIssueDate="20260101"), card("9490981234563333", resIssueDate="20260101")
        rows = [approval(f"949098******{tail}", resApprovalNo="2", resUsedAmount="5000", resUsedDate="20260927") for tail in ("2222", "3333")]
        self.cards, self.approvals = [first, second], [rows[0]]
        self.run_sync()
        self.approvals = [rows[1]]
        self.run_sync()
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user, amount=5000).count(), 2)
        self.cards = [first, second, card(resIssueDate="20261001")]
        self.approvals = [approval(), {**rows[0], "resCardNo": "123"}]
        result = self.run_sync()
        self.assertEqual(FinancialTransaction.objects.filter(user=self.user, amount=5000).count(), 2)
        self.assertFalse(result["approvals_complete"])

    def test_unknown_number_is_not_guessed_when_two_cards_could_be_the_successor(self):
        # PR #86 리뷰: 같은 날 발급된 두 장이면 번호가 맞은 카드가 한 장뿐이어도 고르지 않는다
        self.cards = [card(resIssueDate="20261001"), card("9490981234561111", resCardName="다른 카드", resIssueDate="20261001")]
        self.approvals = [approval(), approval("123", resApprovalNo="3", resUsedAmount="7000", resUsedDate="20260912")]
        result = self.run_sync()
        self.assertFalse(FinancialTransaction.objects.filter(user=self.user, amount=7000).exists())
        self.assertFalse(result["approvals_complete"])
        self.cards = [card()]  # 발급일을 안 주는 카드사
        self.approvals = [approval(), approval("123", resApprovalNo="4", resUsedAmount="8000", resUsedDate="20260912")]
        self.run_sync()
        self.assertFalse(FinancialTransaction.objects.filter(user=self.user, amount=8000).exists())

    def test_approval_goes_to_matching_card(self):
        self.cards = [card(), card("9490981234561111", resCardName="다른 카드")]
        self.approvals = [approval(), approval("949098******1111", resApprovalNo="2", resUsedAmount="5000")]
        self.run_sync()
        other = Card.objects.get(user=self.user, masked_number="****1111")
        self.assertEqual(FinancialTransaction.objects.get(card=other).amount, 5000)
        self.assertEqual(len(self.runs("card_approval_list")), 2)

    def test_deduction_reads_synced_data(self):
        """카드사 통합 청구서로 카드사 단위 선차감을 계산한다 (10/29 나은 · P-12)"""
        from types import SimpleNamespace

        from .card_deduction import get_issuer_first_bill_deduction

        self.bills["202610"] = bill(due="20261105", resBillType="")
        self.run_sync()
        saved = Card.objects.get(user=self.user)
        Card.objects.filter(pk=saved.pk).update(first_connected_at=timezone.make_aware(datetime(2026, 10, 1)))
        cycle = SimpleNamespace(user_id=self.user.pk, starts_on=date(2026, 10, 10), ends_before=date(2026, 11, 10))
        result = get_issuer_first_bill_deduction(self.user, ORG, cycle, card_ids=[saved.pk], as_of=END,
                                                 bill_history_complete=True, spending_history_complete=True)
        # 11/06: 청구서 상세에는 9/12 구매(주기 전)만 있어 10/20 승인 22,000 은 이 청구서에 든 지출이라는 근거가 없음 → 빼지 않는다
        # (예전: 420,000 − 22,000 = 398,000). 상세 합계가 총액보다 작아 빠진 항목은 모름으로 표시
        self.assertEqual((result.amount, result.reason, result.cycle_card_spending, result.items_incomplete),
                         (420_000, None, 0, True))

    def test_next_month_bill_is_saved_without_coverage_record(self):
        self.bills["202611"] = bill(due="20261105")
        sync_card(self.user, "cid", ORG, START, END, MONTHS + ("202611",), connection_id=self.connection.pk)
        self.assertTrue(CardBill.objects.filter(user=self.user, billing_month=date(2026, 11, 1)).exists())
        saved = Card.objects.get(user=self.user)
        # 아직 오지 않은 11월 날짜는 조회했다고 남기지 않는다
        self.assertEqual(self.runs("card_bill_list"), [
            (saved.pk, date(2026, 9, 1), date(2026, 9, 30)), (saved.pk, date(2026, 10, 1), END),
        ])

    def test_long_bill_type_is_kept_for_card_loan_check(self):
        self.bills["202610"] = bill(resBillType="장기카드대출(카드론) 이용대금 명세서 - 별도 청구분")
        self.run_sync()
        october = CardBill.objects.get(user=self.user, billing_month=date(2026, 10, 1))
        self.assertTrue(october.field_states["codefBillType"].startswith("장기카드대출(카드론)"))

    def test_refresh_api_finds_card_syncer(self):
        from .api_sync import card_syncer

        self.assertIs(card_syncer(), sync_card)
