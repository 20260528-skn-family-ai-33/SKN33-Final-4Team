import os
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from . import classifier
from .models import FinancialTransaction as Tx


def fake_predict(names):
    table = {
        "루덴치과의원": [("MEDICAL_HEALTH", 0.99), ("FOOD", 0.005), ("BEAUTY", 0.003)],
        "청담": [("FOOD", 0.44), ("BEAUTY", 0.40), ("HOUSEHOLD", 0.05)],
    }
    return [table.get(name, []) for name in names]


@patch("finance.classifier.predict", side_effect=fake_predict)
class ClassifyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email="classifier@example.com")

    def setUp(self):
        # 5단계(외부 API)는 JudgeTest 에서 본다. 여기서는 '쓸 수 없음'(None)이 기본
        judge = patch("finance.classifier.judge", return_value=None)
        self.judge = judge.start()
        self.addCleanup(judge.stop)
        # 동명 충돌 목록은 로컬 파일(label_conflicts.csv)이 있으면 결과가 달라지니 빈 목록이 기본. 필요한 테스트만 바꿔 넣는다
        conflicts = patch("finance.classifier.conflict_names", return_value=frozenset())
        conflicts.start()
        self.addCleanup(conflicts.stop)

    def tx(self, merchant, **fields):
        data = dict(user=self.user, source_kind="manual", occurred_on="2026-10-29", amount=10000, direction="out",
                    transaction_type="spending", merchant_name=merchant,
                    confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        data.update(fields)
        return Tx.objects.create(**data)

    def run_classify(self, *txs):
        classifier.classify(self.user, Tx.objects.filter(pk__in=[t.pk for t in txs]))
        return [Tx.objects.get(pk=t.pk) for t in txs]

    def test_confident_ml_is_suggested(self, predict):
        (tx,) = self.run_classify(self.tx("루덴치과의원"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("MEDICAL_HEALTH", "suggested", "ml"))
        self.assertEqual((tx.suggested_category_id, tx.suggestion_confidence), ("MEDICAL_HEALTH", Decimal("0.9900")))
        self.assertIsNone(tx.suggestion_candidates)

    def test_unsure_ml_needs_review_with_candidates(self, predict):
        (tx,) = self.run_classify(self.tx("청담"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("UNCLASSIFIED", "needs_review", None))
        self.assertEqual((tx.suggested_category_id, tx.suggestion_source), ("FOOD", "ml"))
        self.assertEqual([c["category"] for c in tx.suggestion_candidates], ["FOOD", "BEAUTY", "HOUSEHOLD"])
        self.assertEqual(tx.suggestion_reason, classifier.REVIEW_REASON)

    def test_user_memory_comes_before_ml(self, predict):
        self.tx("청담", confirmed_category_id="BEAUTY", confirmation_state="confirmed", classification_source="user",
                field_states={"apply_to_future": True})
        (tx,) = self.run_classify(self.tx("청담"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("BEAUTY", "suggested", "rule"))
        predict.assert_called_once_with([])

    def test_memory_without_apply_to_future_is_not_reused(self, predict):
        # 10/30: 사용자가 '앞으로도 적용'을 고르지 않았으면 다음 거래는 원래대로 SVM
        self.tx("청담", confirmed_category_id="BEAUTY", confirmation_state="confirmed", classification_source="user")
        (tx,) = self.run_classify(self.tx("청담"))
        self.assertEqual((tx.confirmed_category_id, tx.suggestion_source), ("UNCLASSIFIED", "ml"))
        predict.assert_called_once_with(["청담"])

    def test_multi_item_never_reuses_memory_or_calls_ml(self, predict):
        self.tx("쿠팡", confirmed_category_id="FOOD", confirmation_state="confirmed", classification_source="user")
        remembered, fresh = self.run_classify(self.tx("쿠팡"), self.tx("현대백화점 판교점"))
        for tx in (remembered, fresh):
            self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                             ("UNCLASSIFIED", "needs_review", None))
            self.assertEqual(tx.suggestion_reason, classifier.MULTI_ITEM_REASON)
        # 과거 선택은 후보로만
        self.assertEqual((remembered.suggested_category_id, remembered.suggestion_candidates),
                         ("FOOD", [{"category": "FOOD", "confidence": None}]))
        self.assertEqual((fresh.suggested_category_id, fresh.suggestion_candidates), (None, None))
        predict.assert_called_once_with([])

    def test_decided_retail_ignores_user_choice(self, predict):
        # 11/05: 편의점·마트는 항상 식비, 다이소는 생활용품. 사용자가 고친 값은 그 거래에만 쓴다
        self.tx("GS25 역삼점", confirmed_category_id="HOUSEHOLD", confirmation_state="confirmed", classification_source="user")
        store, mart, daiso = self.run_classify(self.tx("GS25 역삼점"), self.tx("웰빙마트"), self.tx("(주)아성다이소"))
        self.assertEqual([(t.confirmed_category_id, t.confirmation_state, t.classification_source, t.suggestion_reason)
                          for t in (store, mart, daiso)],
                         [("FOOD", "suggested", "rule", None), ("FOOD", "suggested", "rule", None),
                          ("HOUSEHOLD", "suggested", "rule", None)])
        predict.assert_called_once_with([])

    def test_future_choice_beats_retail_and_multi_item(self, predict):
        # 11/06: 사용자가 '앞으로도 적용'을 고르면 다품목도 그 선택. 페이 충전은 표시가 남아 있어도 묻는다
        chosen = {"confirmed_category_id": "HOUSEHOLD", "confirmation_state": "confirmed", "classification_source": "user",
                  "field_states": {"apply_to_future": True}}
        for name in ("GS25 역삼점", "쿠팡", "네이버페이충전"):
            self.tx(name, **chosen)
        store, mall, charge = self.run_classify(self.tx("GS25 역삼점"), self.tx("쿠팡"), self.tx("네이버페이충전"))
        for tx in (store, mall):
            self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source), ("HOUSEHOLD", "suggested", "rule"))
        self.assertEqual((charge.confirmed_category_id, charge.suggestion_reason), ("UNCLASSIFIED", classifier.PAY_CHARGE_REASON))
        predict.assert_called_once_with([])

    def test_name_conflict_is_not_bypassed_by_searched_retail(self, predict):
        # PR #92 리뷰: 동명 충돌 이름은 저장된 검색 업종이 마트여도 사용자가 고르기 전까지 확인 필요
        searched = {"classifier": {"industry": "슈퍼마켓", "multi_item": True, "name_conflict": True}}
        chosen = {"confirmed_category_id": "HOUSEHOLD", "confirmation_state": "confirmed", "classification_source": "user"}
        with patch("finance.classifier.conflict_names", return_value=frozenset({"청담"})):
            (again,) = self.run_classify(self.tx("청담", field_states=searched))  # 선택하지 않은 기존 거래 재분류
            self.assertEqual((again.confirmed_category_id, again.confirmation_state, again.suggestion_reason),
                             ("UNCLASSIFIED", "needs_review", classifier.CONFLICT_REASON))

            self.tx("청담", field_states=searched, **chosen)  # '앞으로도 적용' 없이 고름 → 새 거래는 후보만
            (fresh,) = self.run_classify(self.tx("청담"))
            self.assertEqual((fresh.confirmed_category_id, fresh.suggestion_reason, fresh.suggested_category_id),
                             ("UNCLASSIFIED", classifier.CONFLICT_REASON, "HOUSEHOLD"))

            self.tx("청담", field_states={**searched, "apply_to_future": True}, **chosen)  # 체크함 → 고른 카테고리
            (remembered,) = self.run_classify(self.tx("청담"))
            self.assertEqual((remembered.confirmed_category_id, remembered.classification_source), ("HOUSEHOLD", "rule"))
        predict.assert_called_with([])

    def test_saved_name_conflict_works_without_conflict_file(self, predict):
        # PR #92 재리뷰: 충돌 목록 파일이 없는 환경에서도 거래에 저장된 판단으로 막는다
        searched = {"classifier": {"industry": "슈퍼마켓", "multi_item": True, "name_conflict": True}}
        with patch("finance.classifier.conflict_names", return_value=frozenset()):
            old, fresh = self.run_classify(self.tx("청담", field_states=searched), self.tx("청담"))
            for tx in (old, fresh):
                self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                                 ("UNCLASSIFIED", "needs_review", classifier.CONFLICT_REASON))
            self.tx("청담", confirmed_category_id="HOUSEHOLD", confirmation_state="confirmed", classification_source="user",
                    field_states={**searched, "apply_to_future": True})
            (chosen,) = self.run_classify(self.tx("청담"))
            self.assertEqual((chosen.confirmed_category_id, chosen.classification_source), ("HOUSEHOLD", "rule"))
            # 충돌 표시가 없는 마트는 파일이 없어도 그대로 식비
            self.tx("행복상회", field_states={"classifier": {"industry": "슈퍼마켓", "multi_item": True}})
            (mart,) = self.run_classify(self.tx("행복상회"))
            self.assertEqual(mart.confirmed_category_id, "FOOD")

    def test_mixed_searched_industries_keep_asking(self, predict):
        # PR #94 리뷰: 복수 업종으로 보류한 판단은 재분류와 다음 거래에도 유지. '앞으로도 적용'을 고르면 그 카테고리
        predict.side_effect = lambda names: [[("BEAUTY", 0.99)] for _ in names]
        for industry, extra in (("슈퍼마켓, 백화점", {}), ("슈퍼마켓, 미용실", {}), ("슈퍼마켓", {"evidence_conflict": True})):
            with self.subTest(industry):
                Tx.objects.all().delete()
                searched = {"classifier": {"industry": industry, "multi_item": True, **extra}}
                again, fresh = self.run_classify(self.tx("행복상회", field_states=searched), self.tx("행복상회"))
                for tx in (again, fresh):
                    self.assertEqual((tx.confirmed_category_id, tx.confirmation_state), ("UNCLASSIFIED", "needs_review"))
                self.tx("행복상회", confirmed_category_id="HOUSEHOLD", confirmation_state="confirmed", classification_source="user",
                        field_states={**searched, "apply_to_future": True})
                (chosen,) = self.run_classify(self.tx("행복상회"))
                self.assertEqual(chosen.confirmed_category_id, "HOUSEHOLD")
        predict.assert_called_with([])

    def card_tx(self, merchant, industry, **fields):
        return self.tx(merchant, merchant_category=industry, **fields)

    def test_card_type_decides_before_svm_and_search(self, predict):
        # 11/06: 카드사가 기록한 업종이 가장 강한 근거. 같은 업종은 한 번만 묻는다
        answer = ("MEDICAL_HEALTH", 0.97, {}, "jev-test")
        with patch("finance.classifier.ask_jev", return_value=answer) as ask, \
                patch.dict(os.environ, {"TYPESAFE_API_KEY": "k", "JEV_MODEL": "m"}):
            first, second = self.run_classify(self.card_tx("새봄약국", "약국"), self.card_tx("행복약국", "약국"))
        ask.assert_called_once_with("카드사 업종: 약국")
        for tx in (first, second):
            self.assertEqual((tx.confirmed_category_id, tx.classification_source), ("MEDICAL_HEALTH", "llm"))
            self.assertEqual(tx.field_states["classifier"]["step"], "card_type")
        predict.assert_called_once_with([])
        self.judge.assert_not_called()

    def test_card_type_resolves_name_conflict(self, predict):
        answer = ("FOOD", 0.97, {}, "jev-test")
        with patch("finance.classifier.conflict_names", return_value=frozenset({"우리집"})), \
                patch("finance.classifier.ask_jev", return_value=answer) as ask, patch.dict(os.environ, {"TYPESAFE_API_KEY": "k", "JEV_MODEL": "m"}):
            (tx,) = self.run_classify(self.card_tx("우리집", "일반음식점 기타"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.merchant_category), ("FOOD", "suggested", "일반음식점 기타"))
        ask.assert_called_once_with("카드사 업종: 일반음식점")

    def test_each_card_type_decides_its_own_transaction(self, predict):
        # PR #96 리뷰: 같은 이름이어도 이번 거래의 카드 업종으로 판단하고, 카드 업종은 다음 거래에 넘기지 않는다
        answer = ("MEDICAL_HEALTH", 0.97, {}, "jev-test")
        searched = {"classifier": {"industry": "슈퍼마켓", "multi_item": True}}
        with patch("finance.classifier.ask_jev", return_value=answer) as ask, patch.dict(os.environ, {"TYPESAFE_API_KEY": "k", "JEV_MODEL": "m"}):
            (store,) = self.run_classify(self.card_tx("행복상회", "편의점"))
            (pharmacy,) = self.run_classify(self.card_tx("행복상회", "약국"))
            (store_again,) = self.run_classify(self.card_tx("행복상회", "편의점"))
            self.tx("좋은상회", field_states=searched)  # 지난 검색 기록보다 이번 카드 업종이 먼저
            (over_search,) = self.run_classify(self.card_tx("좋은상회", "약국"))
        self.assertEqual([t.confirmed_category_id for t in (store, pharmacy, store_again, over_search)], ["FOOD", "MEDICAL_HEALTH", "FOOD", "MEDICAL_HEALTH"])
        self.assertEqual(ask.call_count, 2)
        self.assertNotIn("multi_item", store.field_states["classifier"])
        (no_type,) = self.run_classify(self.tx("행복상회"))  # 업종 없는 거래에 카드 판단이 번지지 않는다
        self.assertEqual(no_type.classification_source, None)
        predict.assert_called_with(["행복상회"])

    def test_card_type_beats_saved_name_conflict(self, predict):
        searched = {"classifier": {"industry": "슈퍼마켓", "multi_item": True, "name_conflict": True}}
        self.tx("청담", field_states=searched)
        with patch("finance.classifier.ask_jev", return_value=("BEAUTY", 0.97, {}, "m")), patch.dict(os.environ, {"TYPESAFE_API_KEY": "k", "JEV_MODEL": "m"}):
            (tx,) = self.run_classify(self.card_tx("청담", "미용실"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state), ("BEAUTY", "suggested"))

    def test_remembered_choice_needs_the_same_card_type(self, predict):
        # 11/06: 이름이 같아도 업종이 분명히 다르면 다른 가게라서 이번 거래의 업종으로 분류한다
        self.card_tx("행복상회", "편의점", confirmed_category_id="HOUSEHOLD", confirmation_state="confirmed", classification_source="user",
                     field_states={"apply_to_future": True})
        answer = ("MEDICAL_HEALTH", 0.97, {}, "jev-test")
        with patch("finance.classifier.ask_jev", return_value=answer), patch.dict(os.environ, {"TYPESAFE_API_KEY": "k", "JEV_MODEL": "m"}):
            same, online, blank, other = self.run_classify(self.card_tx("행복상회", "편의점"), self.card_tx("행복상회", "전자상거래PG"),
                                                           self.tx("행복상회"), self.card_tx("행복상회", "약국"))
        self.assertEqual([t.confirmed_category_id for t in (same, online, blank)], ["HOUSEHOLD", "HOUSEHOLD", "HOUSEHOLD"])
        self.assertEqual((other.confirmed_category_id, other.confirmation_state, other.suggestion_reason),
                         ("MEDICAL_HEALTH", "suggested", None))

    def test_card_type_retail_and_department_store(self, predict):
        with patch("finance.classifier.ask_jev") as ask:
            store, mart, department = self.run_classify(self.card_tx("새봄상회", "편의점"), self.card_tx("행복상회", "슈퍼마켓"), self.card_tx("좋은상회", "백화점"))
        ask.assert_not_called()
        self.assertEqual([t.confirmed_category_id for t in (store, mart)], ["FOOD", "FOOD"])
        self.assertEqual((department.confirmed_category_id, department.suggestion_reason), ("UNCLASSIFIED", classifier.MULTI_ITEM_REASON))

    def test_online_blank_or_unsure_card_type_falls_through(self, predict):
        # 전자상거래·빈칸은 가게 업종이 아니고, 판단 보류·낮은 점수·장애·키 없음이면 지금 흐름으로 간다
        keys = {"TYPESAFE_API_KEY": "k", "JEV_MODEL": "m"}
        with patch("finance.classifier.ask_jev") as ask:
            for name, industry in (("가게1", "전자상거래PG"), ("가게2", "전자상거래(안심클릭미적용)"), ("가게3", ""), ("가게4", None)):
                self.run_classify(self.card_tx(name, industry))
            ask.assert_not_called()
        for reply in ({"return_value": ("UNKNOWN", 0.99, {}, "m")}, {"return_value": ("FOOD", 0.6, {}, "m")}, {"side_effect": OSError("down")}):
            with patch("finance.classifier.ask_jev", **reply), patch.dict(os.environ, keys), self.assertNoLogs("finance.classifier", "ERROR"):
                predict.reset_mock()
                self.run_classify(self.card_tx("가게5", "기타업종"))
                predict.assert_called_once_with(["가게5"])
        with patch("finance.classifier.ask_jev") as ask, patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            self.run_classify(self.card_tx("가게6", "기타업종"))
            ask.assert_not_called()

    def test_user_choice_beats_card_type(self, predict):
        chosen = {"confirmed_category_id": "HOUSEHOLD", "confirmation_state": "confirmed", "classification_source": "user"}
        self.tx("새봄상회", field_states={"apply_to_future": True}, **chosen)
        self.tx("행복약국", **chosen)
        answer = ("MEDICAL_HEALTH", 0.97, {}, "jev-test")
        with patch("finance.classifier.ask_jev", return_value=answer), patch.dict(os.environ, {"TYPESAFE_API_KEY": "k", "JEV_MODEL": "m"}):
            remembered, differs = self.run_classify(self.card_tx("새봄상회", "편의점"), self.card_tx("행복약국", "약국"))
        self.assertEqual(remembered.confirmed_category_id, "HOUSEHOLD")  # '앞으로도 적용'
        self.assertEqual((differs.confirmed_category_id, differs.confirmation_state), ("MEDICAL_HEALTH", "suggested"))  # 체크 없는 지난 선택보다 업종

    def test_transit_card_charge_is_transport(self, predict):
        # 11/06: 티머니 충전은 페이 충전이 아니라 교통비
        (tx,) = self.run_classify(self.tx("모바일티머니충전"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source), ("TRANSPORT_CAR", "suggested", "rule"))
        predict.assert_called_once_with([])

    def test_searched_retail_is_food_for_later_transactions(self, predict):
        # 검색으로 마트가 확인된 가게는 SVM 이 다른 답을 확신해도 식비. 백화점으로 확인된 가게는 계속 묻는다
        predict.side_effect = lambda names: [[("BEAUTY", 0.99)] for _ in names]
        self.tx("행복상회", field_states={"classifier": {"industry": "슈퍼마켓", "multi_item": True}})
        self.tx("행복쇼핑", field_states={"classifier": {"industry": "백화점", "multi_item": True}})
        mart, department = self.run_classify(self.tx("행복상회"), self.tx("행복쇼핑"))
        self.assertEqual((mart.confirmed_category_id, mart.classification_source), ("FOOD", "rule"))
        self.assertEqual((department.confirmed_category_id, department.suggestion_reason), ("UNCLASSIFIED", classifier.MULTI_ITEM_REASON))
        predict.assert_called_once_with([])

    def test_rules_classify_before_ml(self, predict):
        taxi, ott, laundry, delivery = self.run_classify(
            self.tx("이동의즐거움_택시_0"), self.tx("넷플릭스서비시스코리아"), self.tx("크린토피아세탁"), self.tx("(주)우아한형제들"))
        self.assertEqual([(t.confirmed_category_id, t.confirmation_state, t.classification_source)
                          for t in (taxi, ott, laundry, delivery)],
                         [("TRANSPORT_CAR", "suggested", "rule"), ("CULTURE_LEISURE", "suggested", "rule"),
                          ("ETC", "suggested", "rule"), ("FOOD", "suggested", "rule")])
        predict.assert_called_once_with([])

    def test_pg_prefixed_multi_item_ignores_memory(self, predict):
        # PR #63 리뷰: 'NICE*쿠팡'을 식비로 고른 기억이 있어도 새 거래는 확인 필요
        self.tx("NICE*쿠팡", confirmed_category_id="FOOD", confirmation_state="confirmed", classification_source="user")
        (tx,) = self.run_classify(self.tx("NICE*쿠팡"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source, tx.suggestion_reason),
                         ("UNCLASSIFIED", "needs_review", None, classifier.MULTI_ITEM_REASON))
        self.assertEqual(tx.suggestion_candidates, [{"category": "FOOD", "confidence": None}])
        predict.assert_called_once_with([])

    def test_pg_prefixed_multi_item_ignores_confident_svm(self, predict):
        # PR #63 리뷰: SVM 이 FOOD 0.99 를 줘도 다품목이면 SVM 을 부르지 않는다. 다품목이 아니면 접두어를 뗀 이름으로 SVM
        predict.side_effect = lambda names: [[("FOOD", 0.99)] for _ in names]
        coupang, gs25, musinsa = self.run_classify(self.tx("NICE*쿠팡"), self.tx("KCP*GS25 역삼점"), self.tx("KCP*무신사"))
        self.assertEqual((coupang.confirmed_category_id, coupang.confirmation_state), ("UNCLASSIFIED", "needs_review"))
        self.assertEqual((gs25.confirmed_category_id, gs25.classification_source), ("FOOD", "rule"))
        self.assertEqual((musinsa.confirmed_category_id, musinsa.classification_source), ("FOOD", "ml"))
        predict.assert_called_once_with(["무신사"])

    def test_same_name_is_judged_once_per_run(self, predict):
        # 호출이 거래 잠금 안에서 돌아서 같은 이름의 검색·Jev 는 한 번만
        predict.side_effect = lambda names: [[("FOOD", 0.4)] for _ in names]
        self.judge.return_value = {"apply": ("FOOD", 0.95), "record": {"choice": "FOOD"}}
        first, second, other = self.run_classify(self.tx("새봄분식"), self.tx("새봄분식"), self.tx("새봄상회"))
        self.assertEqual([t.confirmed_category_id for t in (first, second, other)], ["FOOD", "FOOD", "FOOD"])
        self.assertEqual(self.judge.call_count, 2)

    def test_transfer_and_confirmed_never_reach_search(self, predict):
        # 받는 사람 이름이 외부 검색으로 나가지 않는다: 이체도, 이체에서 바뀐 확정 소비도 분류 대상이 아니다
        transfer = self.tx("이하늘", transaction_type="transfer")
        converted = self.tx("이하늘", confirmed_category_id="FOOD", confirmation_state="confirmed", classification_source="user",
                            field_states={"original_transaction_type": "transfer"})
        self.run_classify(transfer, converted)
        predict.assert_called_once_with([])
        self.judge.assert_not_called()

    def test_name_conflict_skips_confident_svm(self, predict):
        # 분류 워크플로 10/26 4장: 같은 이름에 다른 업종이 있으면 SVM 0.99 여도 확정하지 않고 검색+Jev
        with patch("finance.classifier.conflict_names", return_value=frozenset({"루덴치과의원"})):
            (tx,) = self.run_classify(self.tx("루덴치과의원"))
            self.judge.assert_called_once_with("루덴치과의원")
            self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                             ("UNCLASSIFIED", "needs_review", classifier.CONFLICT_REASON))
            self.assertEqual(tx.suggested_category_id, "MEDICAL_HEALTH")  # SVM 답은 후보로 남김

            # PR #68 리뷰: 같은 이름의 검색 결과 + 높은 Jev 점수여도 어느 가게인지 모르니 후보로만
            record = {"step": "search_jev", "source": "naver", "industry": "치과의원", "ms": 100}
            self.judge.return_value = {"apply": ("MEDICAL_HEALTH", 0.97), "record": record}
            (tx,) = self.run_classify(tx)
            self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source, tx.suggestion_reason),
                             ("UNCLASSIFIED", "needs_review", None, classifier.CONFLICT_REASON))
            self.assertEqual((tx.suggested_category_id, tx.suggestion_source), ("MEDICAL_HEALTH", "llm"))
            self.assertTrue(tx.field_states["classifier"]["name_conflict"])

    def test_name_conflict_keeps_jev_answer_as_candidate_only(self, predict):
        # PR #68 리뷰 재현: 청담(충돌) · SVM 은 BEAUTY · 검색은 같은 이름 '청담' 한식 · Jev 는 FOOD 0.99
        predict.side_effect = lambda names: [[("BEAUTY", 0.99), ("FOOD", 0.005)] for _ in names]
        record = {"step": "search_jev", "source": "naver", "industry": "한식", "ms": 100}
        self.judge.return_value = {"apply": ("FOOD", 0.99), "record": record}
        with patch("finance.classifier.conflict_names", return_value=frozenset({"청담"})):
            (tx,) = self.run_classify(self.tx("청담"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                         ("UNCLASSIFIED", "needs_review", classifier.CONFLICT_REASON))
        self.assertEqual([c["category"] for c in tx.suggestion_candidates], ["FOOD", "BEAUTY"])
        self.assertTrue(tx.field_states["classifier"]["name_conflict"])

        # 검색 실패·결과 없음도 확인 필요 그대로
        for reason in (classifier.LOOKUP_FAILED_REASON, classifier.NOT_FOUND_REASON):
            self.judge.return_value = {"review": ([], reason), "record": record}
            with patch("finance.classifier.conflict_names", return_value=frozenset({"청담"})):
                (tx,) = self.run_classify(tx)
            self.assertEqual((tx.confirmation_state, tx.suggestion_reason), ("needs_review", reason))

        # 충돌 목록에 없으면 같은 Jev 답은 그대로 자동 적용
        self.judge.return_value = {"apply": ("FOOD", 0.99), "record": record}
        predict.side_effect = fake_predict
        (tx,) = self.run_classify(tx)
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("FOOD", "suggested", "llm"))

    def test_user_past_choice_differs_from_model(self, predict):
        # 사용자가 전에 고친 가게('앞으로도 적용' 아님)는 SVM 이 확신해도 다르면 다시 묻는다. 같으면 그대로 적용
        self.tx("루덴치과의원", confirmed_category_id="BEAUTY", confirmation_state="confirmed", classification_source="user")
        (tx,) = self.run_classify(self.tx("루덴치과의원"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                         ("UNCLASSIFIED", "needs_review", classifier.USER_DIFFERS_REASON))
        self.assertEqual([c["category"] for c in tx.suggestion_candidates], ["BEAUTY", "MEDICAL_HEALTH", "FOOD"])

        self.tx("루덴치과의원", confirmed_category_id="MEDICAL_HEALTH", confirmation_state="confirmed",
                classification_source="user", occurred_on="2026-10-30")
        (tx,) = self.run_classify(self.tx("루덴치과의원"))
        self.assertEqual((tx.confirmed_category_id, tx.classification_source), ("MEDICAL_HEALTH", "ml"))

    def test_unsure_ml_goes_to_search_and_jev(self, predict):
        record = {"step": "search_jev", "source": "naver", "industry": "미용실", "ms": 120}
        self.judge.return_value = {"apply": ("BEAUTY", 0.96), "record": record}
        (tx,) = self.run_classify(self.tx("청담"))
        self.judge.assert_called_once_with("청담")
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source, tx.suggestion_confidence),
                         ("BEAUTY", "suggested", "llm", Decimal("0.9600")))
        self.assertEqual(tx.field_states["classifier"], record)

        self.judge.return_value = {"review": ([("FOOD", 0.5), ("BEAUTY", 0.3)], classifier.UNSURE_REASON), "record": record}
        (tx,) = self.run_classify(tx)
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_source, tx.suggestion_reason),
                         ("UNCLASSIFIED", "needs_review", "llm", classifier.UNSURE_REASON))
        self.assertEqual([c["category"] for c in tx.suggestion_candidates], ["FOOD", "BEAUTY"])

    def test_unknown_store_skips_svm_and_jev(self, predict):
        predict.side_effect = lambda names: [[("FOOD", 0.91)] for _ in names]
        (tx,) = self.run_classify(self.tx("비식별거래처_0042"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                         ("UNCLASSIFIED", "needs_review", classifier.UNKNOWN_STORE_REASON))
        predict.assert_called_once_with([])
        self.judge.assert_not_called()

    def test_pay_charge_asks_user_and_ignores_memory(self, predict):
        self.tx("토스페이충전", confirmed_category_id="FOOD", confirmation_state="confirmed", classification_source="user")
        (tx,) = self.run_classify(self.tx("토스페이충전"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                         ("UNCLASSIFIED", "needs_review", classifier.PAY_CHARGE_REASON))
        self.assertEqual(tx.suggestion_candidates, [{"category": "FOOD", "confidence": None}])
        predict.assert_called_once_with([])

    def test_pg_only_asks_without_search(self, predict):
        # 11/16: 결제대행사만 찍히면 어느 가게인지 몰라 SVM · 검색 없이 묻는다. 지난 선택은 후보로
        self.tx("대표인증_3 - 발트페이먼츠", confirmed_category_id="FOOD", confirmation_state="confirmed",
                classification_source="user")
        (tx,) = self.run_classify(self.tx("대표인증_3 - 발트페이먼츠"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                         ("UNCLASSIFIED", "needs_review", classifier.PG_REASON))
        self.assertEqual(tx.suggestion_candidates, [{"category": "FOOD", "confidence": None}])
        predict.assert_called_once_with([])
        self.judge.assert_not_called()

    def test_pg_only_still_uses_apply_to_future(self, predict):
        # 11/11 (PR #114): 결제대행사 이름이어도 '앞으로도 적용'을 고르면 같은 이름 + 같은 카드사 업종 거래에 쓴다
        self.tx("대표인증_3 - 발트페이먼츠", confirmed_category_id="FOOD", confirmation_state="confirmed",
                classification_source="user", field_states={"apply_to_future": True})
        (tx,) = self.run_classify(self.tx("대표인증_3 - 발트페이먼츠"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("FOOD", "suggested", "rule"))

    def test_b_is_checked_when_a_is_not_decided(self, predict):
        # 11/16 실거래 '메가MGC_인증_3 - 메가 MGC 커피': A '메가MGC' 는 근거를 못 찾고 B 로 다시 보면 식비
        predict.side_effect = lambda names: [[("FOOD", 0.4), ("BEAUTY", 0.3)] for _ in names]
        a_record = {"step": "search_jev", "store_mismatch": True, "web_accepted": False}
        b_record = {"step": "search_jev", "web_accepted": True}
        self.judge.side_effect = lambda name: {
            "메가MGC": {"review": ([("FOOD", 0.1)], classifier.NOT_FOUND_REASON), "record": a_record},
            "메가 MGC 커피": {"apply": ("FOOD", 0.97), "record": b_record},
        }[name]
        (tx,) = self.run_classify(self.tx("메가MGC_인증_3 - 메가 MGC 커피"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("FOOD", "suggested", "llm"))
        self.assertEqual([c.args[0] for c in self.judge.call_args_list], ["메가MGC", "메가 MGC 커피"])
        self.assertEqual(tx.field_states["classifier"], {**b_record, "used": "B", "other": a_record})

    def test_decided_a_never_looks_at_b(self, predict):
        (tx,) = self.run_classify(self.tx("루덴치과의원 - 새봄식당"))
        self.assertEqual((tx.confirmed_category_id, tx.classification_source), ("MEDICAL_HEALTH", "ml"))
        predict.assert_called_once_with(["루덴치과의원"])
        self.judge.assert_not_called()

    def test_pg_found_by_search_moves_to_b(self, predict):
        # A 를 검색해 보니 결제대행사 → B 로. B 는 규칙(택시)으로 정해진다
        predict.side_effect = lambda names: [[("FOOD", 0.4)] for _ in names]
        pg = {"step": "search_jev", "pg": "web"}
        self.judge.return_value = {"pg": True, "record": pg}
        (tx,) = self.run_classify(self.tx("테스트결제 - 카카오T일반택시_0"))
        self.assertEqual((tx.confirmed_category_id, tx.classification_source), ("TRANSPORT_CAR", "rule"))
        self.assertEqual(tx.field_states["classifier"], {"step": "rule", "used": "B", "other": pg})
        self.judge.assert_called_once_with("테스트결제")

    def test_both_unresolved_shows_the_side_with_store_evidence(self, predict):
        predict.side_effect = lambda names: [[("FOOD", 0.4)] for _ in names]
        a = {"review": ([("FOOD", 0.2)], classifier.NOT_FOUND_REASON), "record": {"industry": "한식", "store_mismatch": True}}
        b = {"review": ([("BEAUTY", 0.6), ("FOOD", 0.3)], classifier.UNSURE_REASON), "record": {"industry": "미용실"}}
        self.judge.side_effect = lambda name: {"새봄": a, "새봄헤어": b}[name]
        (tx,) = self.run_classify(self.tx("새봄_인증_3 - 새봄헤어"))
        self.assertEqual((tx.confirmation_state, tx.suggestion_reason), ("needs_review", classifier.UNSURE_REASON))
        self.assertEqual([c["category"] for c in tx.suggestion_candidates], ["BEAUTY", "FOOD"])
        self.assertEqual(tx.field_states["classifier"]["used"], "B")

    def test_pg_on_b_keeps_a_review(self, predict):
        # B 가 결제대행사면 A 의 결과를 보여 준다 (B 는 검색하지 않음)
        predict.side_effect = lambda names: [[("FOOD", 0.4)] for _ in names]
        self.judge.return_value = {"review": ([("FOOD", 0.2)], classifier.NOT_FOUND_REASON), "record": {"step": "search_jev"}}
        (tx,) = self.run_classify(self.tx("새봄_인증_3 - 발트페이먼츠"))
        self.assertEqual(tx.suggestion_reason, classifier.NOT_FOUND_REASON)
        self.assertEqual(tx.field_states["classifier"], {"step": "search_jev", "used": "A", "other": {"step": "rule"}})
        self.judge.assert_called_once_with("새봄")

    def test_only_the_last_name_is_judged_as_cut(self, predict):
        # 11/16: 카드사가 자른 건 맨 끝 이름뿐 → 결제 경로 뒤의 B, 'A - B' 의 B 만 cut
        predict.side_effect = lambda names: [[("FOOD", 0.4)] for _ in names]
        self.judge.return_value = {"apply": ("FASHION_SHOPPING", 0.97), "record": {"cut_name": True}}
        (tx,) = self.run_classify(self.tx("대표_4_카카오페이 - 주식회사 더뮤지엄비지"))
        self.judge.assert_called_once_with("주식회사 더뮤지엄비지", cut=True)
        self.assertEqual(tx.confirmed_category_id, "FASHION_SHOPPING")

        self.judge.reset_mock()
        self.judge.return_value = {"review": ([], classifier.NOT_FOUND_REASON), "record": {}}
        self.run_classify(self.tx("메가MGC_인증_3 - 메가 MGC 커피(서울서일초"))
        self.assertEqual([(c.args[0], c.kwargs.get("cut", False)) for c in self.judge.call_args_list],
                         [("메가MGC", False), ("메가 MGC 커피", True)])

    def test_apple_asks_user_until_apply_to_future(self, predict):
        # 11/02: 애플은 음악·iCloud·앱이 한 이름으로 찍혀서 '확인 필요' + 문화·여가 후보. '앞으로도 적용'이면 자동
        fresh, kcp = self.run_classify(self.tx("Apple Services"), self.tx("Apple_KCP"))
        for tx in (fresh, kcp):
            self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.suggestion_reason),
                             ("UNCLASSIFIED", "needs_review", classifier.MIXED_SERVICE_REASON))
            self.assertEqual(tx.suggestion_candidates, [{"category": "CULTURE_LEISURE", "confidence": None}])

        self.tx("Apple Services", confirmed_category_id="HOUSING_TELECOM", confirmation_state="confirmed",
                classification_source="user")
        (tx,) = self.run_classify(self.tx("Apple Services"))
        self.assertEqual([c["category"] for c in tx.suggestion_candidates], ["HOUSING_TELECOM", "CULTURE_LEISURE"])

        self.tx("Apple Services", confirmed_category_id="HOUSING_TELECOM", confirmation_state="confirmed",
                classification_source="user", occurred_on="2026-10-30", field_states={"apply_to_future": True})
        (tx,) = self.run_classify(self.tx("Apple Services"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state), ("HOUSING_TELECOM", "suggested"))
        self.assertTrue(all(call.args == ([],) for call in predict.call_args_list))  # SVM 에 보낸 이름 없음
        self.judge.assert_not_called()

    def test_leaves_confirmed_non_spending_and_nameless_alone(self, predict):
        confirmed = self.tx("루덴치과의원", confirmed_category_id="FOOD", confirmation_state="confirmed",
                            classification_source="user")
        transfer = self.tx("루덴치과의원", transaction_type="transfer")
        nameless = self.tx(None)
        before = [(t.confirmed_category_id, t.confirmation_state, t.classification_source)
                  for t in (confirmed, transfer, nameless)]
        after = self.run_classify(confirmed, transfer, nameless)
        self.assertEqual([(t.confirmed_category_id, t.confirmation_state, t.classification_source) for t in after], before)
        predict.assert_called_once_with([])


class MultiItemTest(SimpleTestCase):
    def test_known_multi_item_names(self):
        for name in ("GS25 역삼점", "이마트24 서초서리아점", "세븐일레븐 서초으뜸점", "웰빙마트", "카카오쇼핑하기_카카오페이",
                     "(주)쿠팡", "(주)아성다이소", "현대백화점 판교점", "번개장터", "NICE*쿠팡", "KCP*GS25 역삼점",
                     # 동명 브랜드 CSV
                     "CU", "CU 역삼점", "씨유 정선사북점", "씨유대전", "지에스25 태백진주점", "롯데슈퍼 탑동3점", "노브랜드 원주단구점", "농협하나로마트 광교점", "신앙촌상회", "매점"):
            self.assertTrue(classifier.is_multi_item(name), name)
        for name in ("루덴치과의원", "스타벅스 강남역점", "쿠팡이츠", "롯데하이마트 강남점", "", None,
                     "CUBE카페", "씨유엔터테인먼트", "노브랜드버거 강릉사천점", "씨제이올리브영 원주기업도시점", "아이스크림할인점", "신앙촌상회 문정점"):
            self.assertFalse(classifier.is_multi_item(name), name)


class RuleTest(SimpleTestCase):
    def test_rule_categories(self):
        cases = {
            "전국고속버스운송사업조합": "TRANSPORT_CAR", "카카오페이택시": "TRANSPORT_CAR", "카카오페이택시충전": None,
            "쿠팡플레이": "CULTURE_LEISURE", "멜론": "CULTURE_LEISURE", "리디(주)": None, "리디아울렛": None,
            "강남장례식장": "ETC", "OO화재해상보험": "ETC", "옷수선": "ETC", "수선화꽃집": None, "루덴치과의원": None,
            # PR #64 리뷰: 짧은 구독 이름은 정확히 같을 때만
            "웨이브": "CULTURE_LEISURE", "티빙": "CULTURE_LEISURE", "넷플릭스서비시스코리아": "CULTURE_LEISURE",
            "웨이브헤어": None, "멜론카페": None, "플로렌스치과": None,
        }
        for name, code in cases.items():
            self.assertEqual(classifier.rule_category(name), code, name)
        self.assertFalse(classifier.is_multi_item("쿠팡플레이"))

    def test_pay_suffix_is_removed(self):
        # 11/02 실거래 명세: 결제수단이 이름 뒤에 붙는다
        for name, store in {"멜론_카카오페이": "멜론", "Apple_KCP": "Apple", "NICE*멜론_네이버페이": "멜론",
                            "이동의즐거움_택시_0": "이동의즐거움_택시_0", "카카오페이택시": "카카오페이택시"}.items():
            self.assertEqual(classifier.store_name(name), store, name)
        self.assertEqual(classifier.rule_category(classifier.store_name("멜론_카카오페이")), "CULTURE_LEISURE")

    def test_conflict_names_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "label_conflicts.csv"
            path.write_text('name,counts,majority_ratio,kept\n리디,"{""FASHION_SHOPPING"": 3}",0.75,False\n', encoding="utf-8-sig")
            classifier.conflict_names.cache_clear()
            with patch("finance.classifier.CONFLICTS_PATH", path):
                self.assertEqual(classifier.conflict_names(), {"리디"})
            classifier.conflict_names.cache_clear()
            with patch("finance.classifier.CONFLICTS_PATH", Path(tmp) / "없음.csv"):
                self.assertEqual(classifier.conflict_names(), frozenset())  # 파일이 없으면 차단 없이 지금처럼
        classifier.conflict_names.cache_clear()

    def test_taxi_and_pc_room(self):
        # 11/02 카드 명세·카카오 장소 조사: '택시'가 낱말이거나 '개인택시'일 때만 교통. 이름 속 '택시' 식당은 제외
        cases = {
            "카카오_개인택시": "TRANSPORT_CAR", "카카오T 택시": "TRANSPORT_CAR", "개인택시 서울12가3456": "TRANSPORT_CAR",
            "카카오_법인택시6": "TRANSPORT_CAR", "서울법인택시조합": None,
            "택시파스타 압구정로데오역점": None, "피자택시": None, "옐로우택시": None, "택시종사자쉼터": None,
            "서울개인택시조합새마을금고": None, "카카오페이택시충전": None,
            "레벨업 피시방 남부터미널점": "CULTURE_LEISURE", "탑PC방": "CULTURE_LEISURE",
        }
        for name, code in cases.items():
            self.assertEqual(classifier.rule_category(name), code, name)

    def test_transport_dictionary(self):
        # 11/16: 후불교통 · 교통카드 · 하이패스 충전 · 주유 · 주차 · 자동차보험 = 교통·차량
        cases = {
            "후불교통": "TRANSPORT_CAR", "후불교통대금": "TRANSPORT_CAR", "11월후불교통": "TRANSPORT_CAR",
            "교통카드충전": "TRANSPORT_CAR", "에스엠하이플러스": "TRANSPORT_CAR", "하이플러스": "TRANSPORT_CAR",
            "하이패스": "TRANSPORT_CAR", "SK에너지대흥주유소": "TRANSPORT_CAR", "GS칼텍스 죽전점": "TRANSPORT_CAR",
            "S-OIL": "TRANSPORT_CAR", "현대오일뱅크": "TRANSPORT_CAR", "풍덕천주유소": "TRANSPORT_CAR",
            "하이파킹 한남점": "TRANSPORT_CAR", "AJ파크 이태원": "TRANSPORT_CAR", "롯데월드몰주차장": "TRANSPORT_CAR",
            "DB손해보험 다이렉트": "TRANSPORT_CAR", "삼성화재다이렉트": "TRANSPORT_CAR", "현대해상다이렉트": "TRANSPORT_CAR",
            "KB손해보험 자동차보험": "TRANSPORT_CAR", "캐롯손해보험": "TRANSPORT_CAR",
            # 교통이 아니거나 교통비로 자동 분류하지 않음
            "교통범칙금": None, "교통과태료": None, "교통사고합의금": None,
            "전동셔터방범샷다주차장문오버헤드도어": None, "주유소식당": None,
            # 자동차보험이 아닌 보험은 지금처럼 기타
            "OO화재해상보험": "ETC", "OO생명보험": "ETC",
        }
        for name, code in cases.items():
            self.assertEqual(classifier.rule_category(name), code, name)

    def test_telecom_bills(self):
        # 11/16: 통신요금 = 주거·통신. 짧은 이름은 전체가 같거나 앞뒤에 번호만 붙을 때만 (실제 명세 'SKT0104948…', '010…SKT')
        cases = {
            "SKT0104948": "HOUSING_TELECOM", "01012345678SKT": "HOUSING_TELECOM", "SKB1234567890": "HOUSING_TELECOM",
            "SKT": "HOUSING_TELECOM", "SKT 010-4948-1234": "HOUSING_TELECOM",
            "KT": "HOUSING_TELECOM", "KT 1234567": "HOUSING_TELECOM", "(주)케이티": "HOUSING_TELECOM",
            "LGU+0105555": "HOUSING_TELECOM", "SK텔레콤": "HOUSING_TELECOM", "SK텔레콤 자동납부": "HOUSING_TELECOM",
            "KT통신요금": "HOUSING_TELECOM", "LG유플러스": "HOUSING_TELECOM", "SK브로드밴드": "HOUSING_TELECOM",
            "KT스카이라이프": "HOUSING_TELECOM", "LG헬로비전": "HOUSING_TELECOM",
            "케이티엠모바일": "HOUSING_TELECOM", "헬로모바일": "HOUSING_TELECOM", "토스모바일": "HOUSING_TELECOM",
            "세븐모바일": "HOUSING_TELECOM",
            # 통신사가 아니거나 다른 용도
            "KT&G": None, "KTX": None, "SKT1 굿즈샵": None, "SK에너지": "TRANSPORT_CAR", "LG전자": None, "토스": None,
            "SK매직 렌탈": None, "KT 렌탈": None, "SK텔레콤 상품권": None, "모바일상품권": None, "카페모바일": None,
        }
        for name, code in cases.items():
            self.assertEqual(classifier.rule_category(name), code, name)
        # 결제대행 표기를 뗀 뒤에도 같다
        self.assertEqual(classifier.rule_category(classifier.store_name("KCP-SK텔레콤_SK텔레콤")), "HOUSING_TELECOM")

    def test_starbucks_brand(self):
        # 11/02: 지점명·회사명·앱 충전 모두 식비. 이름 뒤쪽에 '스타벅스'가 들어간 다른 가게는 제외
        for name in ("스타벅스 강남역점", "스타벅스코리아", "스타벅스계좌", "STARBUCKS KOREA", "(주)스타벅스코리아"):
            self.assertEqual(classifier.rule_category(name), "FOOD", name)
        self.assertIsNone(classifier.rule_category("홍단헤어 스타벅스점"))

    def test_named_brands(self):
        # 11/05: 검색 근거로 구별되지 않는 곳만 이름으로 정한다. 이름 뒤쪽에 들어간 다른 가게는 제외. 지점명은 예시
        cases = {
            "아트박스테스트홈플러스점": "FASHION_SHOPPING", "카카오프렌즈테스트점": "FASHION_SHOPPING",
            "펀앤펀테스트점": "CULTURE_LEISURE", "토피코테스트점": "CULTURE_LEISURE", "테스트아트박스": None,
        }
        for name, code in cases.items():
            self.assertEqual(classifier.rule_category(name), code, name)
        self.assertFalse(classifier.is_multi_item("아트박스테스트홈플러스점"))

    def test_transit_cards(self):
        # 11/05: 교통카드는 어떤 표기든 교통. 11/06: 충전도 교통이고 다른 페이 충전은 계속 묻는다
        for name in ("모바일티머니선불형", "티머니731", "캐시비", "레일플러스", "모바일티머니충전", "티머니 충전"):
            self.assertEqual(classifier.rule_category(name), "TRANSPORT_CAR", name)
            self.assertFalse(classifier.is_pay_charge(name), name)
        self.assertTrue(classifier.is_pay_charge("네이버페이충전"))

    def test_store_after_pay_route(self):
        # 11/05: 'A - B' 는 A 가 결제 경로면 B, 아니면 A 가 가맹점
        cases = {
            "네이버페이 - 봄툰": "봄툰", "카카오페이_3 - 리디": "리디", "한국정보통신 - 테스트파킹(주)": "테스트파킹(주)",
            "PAYCO오더 - 테스트식당": "테스트식당", "쿠팡이츠 - 쿠팡페이 주식회사": "쿠팡이츠",
            "트립닷컴_구_NICE - 나이스정보통신(대리점)": "트립닷컴", "메가MGC_인증_3 - 메가 MGC 커피": "메가MGC",
            "쿠팡_쿠페이 - 쿠팡": "쿠팡_쿠페이", "테스트 - ": "테스트 -", "치킨 - 피자": "치킨",
            # 11/09 실거래: 결제 경로가 A 의 꼬리에 붙은 표기
            "대표_3_카카오페이 - 파파존스 청라점": "파파존스 청라점", "대표_3_네이버페이 - 테스트식당": "테스트식당",
            "카카오스타일 - 카카오스타일": "카카오스타일",
        }
        for name, store in cases.items():
            self.assertEqual(classifier.store_name(name), store, name)
            self.assertEqual(classifier.store_name(store), store, name)  # is_multi_item 이 한 번 더 부른다
        self.assertEqual(classifier.rule_category(classifier.store_name("네이버페이 - 멜론")), "CULTURE_LEISURE")
        self.assertEqual(classifier.rule_category(classifier.store_name("쿠팡이츠 - 쿠팡")), "FOOD")
        self.assertEqual(classifier.rule_category(classifier.store_name("트립닷컴_구_NICE - 나이스정보통신(대리점)")), "TRAVEL_LODGING")
        self.assertTrue(classifier.is_multi_item("쿠팡_쿠페이 - 쿠팡"))
        self.assertFalse(classifier.is_multi_item("쿠팡이츠 - 쿠팡"))

    def test_general_taxi_name(self):
        for name in ("카카오T일반택시_0", "카카오T일반택시_법인_4"):
            self.assertEqual(classifier.rule_category(name), "TRANSPORT_CAR", name)
        self.assertIsNone(classifier.rule_category("서울일반택시조합"))

    def test_named_pg_prefix_is_removed(self):
        # 11/02 카드 명세: 결제대행 이름이 '-'·'_'로 붙는 표기
        self.assertEqual(classifier.store_name("이니시스(빌링)_3_레벨업 피시방 남부터미널점"), "레벨업 피시방 남부터미널점")
        self.assertEqual(classifier.store_name("KCP-쿠팡_쿠팡"), "쿠팡_쿠팡")
        self.assertTrue(classifier.is_multi_item("KCP-쿠팡_쿠팡"))
        self.assertEqual(classifier.store_name("카카오_개인택시"), "카카오_개인택시")

    def test_unknown_store_names(self):
        for name in ("비식별거래처_0001", "비식별가맹점", "거래처_12", "1234", "A", ""):
            self.assertTrue(classifier.is_unknown_store(name), name)
        for name in ("루덴치과의원", "GS25 역삼점", "티머니731", "11번가", "CU"):
            self.assertFalse(classifier.is_unknown_store(name), name)

    def test_pay_charge_names(self):
        for name in ("토스페이충전", "네이버페이충전", "카카오페이 충전", "NICE*토스페이충전"):
            self.assertTrue(classifier.is_pay_charge(name), name)
        for name in ("네이버페이", "카카오페이택시", "토스", "", None):
            self.assertFalse(classifier.is_pay_charge(name), name)

    def test_fallback_name(self):
        # 11/16: 'A - B' 에서 A 를 가맹점으로 쓸 때 다음으로 볼 B
        cases = {
            "메가MGC_인증_3 - 메가 MGC 커피": "메가 MGC 커피", "치킨 - 피자": "피자", "새봄_인증_3 - 발트페이먼츠": "발트페이먼츠",
            "메가MGC_인증_3 - 메가 MGC 커피(서울서일초": "메가 MGC 커피",  # 카드사가 자른 지점명은 뗀다
            "네이버페이 - 봄툰": None, "대표인증_3 - 발트페이먼츠": None, "대표_3_카카오페이 - 파파존스 청라점": None,
            "카카오스타일 - 카카오스타일": None, "테스트 - ": None, "루덴치과의원": None, "": None, None: None,
        }
        for name, alt in cases.items():
            self.assertEqual(classifier.fallback_name(name), alt, name)

    def test_cut_branch_tail(self):
        # 11/16 실거래: 카드사가 이름을 잘라 괄호가 안 닫힌 꼬리는 뗀다. 닫힌 괄호는 그대로
        self.assertEqual(classifier.store_name("인터넷상거래 - 주식회사 노이즈엑스(NoizX C"), "주식회사 노이즈엑스")
        self.assertEqual(classifier.store_name("메가 MGC 커피(서울서일초"), "메가 MGC 커피")
        self.assertEqual(classifier.store_name("한국정보통신 - 테스트파킹(주)"), "테스트파킹(주)")
        self.assertEqual(classifier.store_name("롯데쇼핑(주)롯"), "롯데쇼핑(주)롯")

    def test_maybe_cut(self):
        # 11/16 실거래: 잘린 적요는 41~42바이트, 안 잘린 건 37바이트 이하
        for name in ("대표_4_카카오페이 - 주식회사 더뮤지엄비지", "인터넷상거래 - 주식회사 노이즈엑스(NoizX C"):
            self.assertTrue(classifier.maybe_cut(name), name)
        for name in ("PAYCO오더 - 엔에이치엔페이코 주식회사", "메가MGC_인증_3 - 메가 MGC 커피", "", None):
            self.assertFalse(classifier.maybe_cut(name), name)

    def test_pg_names(self):
        for name in ("발트페이먼츠", "(주)발트페이먼츠", "토스페이먼츠", "나이스페이먼츠", "Toss Payments", "KG이니시스", "KCP",
                     "네이버페이", "카카오페이"):  # 결제 경로 이름만 찍힌 것도 (11/16 실거래 '네이버페이')
            self.assertTrue(classifier.is_pg_name(name), name)
        for name in ("메가 MGC 커피", "카카오페이택시", "네이버페이충전", "페이먼트커피", "", None):
            self.assertFalse(classifier.is_pg_name(name), name)


@patch.dict(os.environ, {"TYPESAFE_API_KEY": "test", "JEV_MODEL": "jev-test"})
class JudgeTest(SimpleTestCase):
    def run_judge(self, found, answer=None, name="새봄동물", web=(), cut=False):
        stores = {"side_effect": found} if isinstance(found, Exception) else {"return_value": [found] if isinstance(found, tuple) else found or []}
        texts = {"side_effect": web} if isinstance(web, Exception) else {"return_value": list(web)}
        answers = {"side_effect": answer} if isinstance(answer, list) else {"return_value": answer}
        with patch("finance.classifier.search_store", **stores), patch("finance.classifier.search_web", **texts), \
                patch("finance.classifier.ask_jev", **answers) as ask:
            return (classifier.judge(name, cut=True) if cut else classifier.judge(name)), ask

    def test_confident_jev_applies(self):
        result, ask = self.run_judge(("naver", "새봄동물", "동물병원"),
                                     ("MEDICAL_HEALTH", 0.97, {"MEDICAL_HEALTH": 0.97, "HOUSEHOLD": 0.03}, "jev-test"))
        self.assertEqual(result["apply"], ("MEDICAL_HEALTH", 0.97))
        self.assertEqual(ask.call_args.args[0], "가맹점 이름: 새봄동물\n검색 업종: 동물병원")
        self.assertEqual((result["record"]["source"], result["record"]["jev_model"]), ("naver", "jev-test"))
        self.assertIn("ms", result["record"])

    def test_unknown_or_low_stays_unclassified(self):
        result, _ = self.run_judge(("naver", "새봄동물", "음식점"),
                                   ("UNKNOWN", 0.8, {"UNKNOWN": 0.8, "FOOD": 0.15, "ETC": 0.05}, "jev-test"))
        self.assertEqual(result["review"], ([("FOOD", 0.15), ("ETC", 0.05)], classifier.UNSURE_REASON))
        result, _ = self.run_judge(None, ("FOOD", 0.6, {"FOOD": 0.6, "BEAUTY": 0.4}, "jev-test"))
        self.assertEqual(result["review"][1], classifier.NOT_FOUND_REASON)

    def test_multi_item_industry_skips_jev(self):
        result, ask = self.run_judge(("naver", "새봄동물", "백화점"))
        self.assertEqual(result["review"], ([], classifier.MULTI_ITEM_REASON))
        self.assertTrue(result["record"]["multi_item"])
        ask.assert_not_called()

    def test_food_retail_industry_skips_jev(self):
        for industry in ("편의점", "슈퍼마켓", "대형마트", "식자재마트"):
            result, ask = self.run_judge(("naver", "새봄동물", industry))
            self.assertEqual(result["retail"], "FOOD", industry)
            self.assertTrue(result["record"]["multi_item"])  # 같은 가게의 다음 거래도 다품목으로 처리
            ask.assert_not_called()

    def test_non_store_industry_is_not_evidence(self):
        result, ask = self.run_judge(("kakao", "비바리퍼블리카", "금융서비스"),
                                     ("UNKNOWN", 0.9, {"UNKNOWN": 0.9, "FOOD": 0.1}, "jev-test"), name="비바리퍼블리카")
        self.assertEqual(ask.call_args.args[0], "가맹점 이름: 비바리퍼블리카")
        self.assertTrue(result["record"]["dropped_industry"])

    def test_api_failure_ends_in_review(self):
        with self.assertLogs("finance.classifier", "WARNING"):
            result, _ = self.run_judge(OSError("timeout"))
        self.assertEqual(result["review"], ([], classifier.LOOKUP_FAILED_REASON))
        self.assertEqual(result["record"]["error"], "OSError")

    def test_high_jev_score_without_store_evidence_stays_in_review(self):
        # PR #64 리뷰: 검색 0건 · 가게가 아닌 업종 · 다른 가게가 검색됐으면 Jev 0.99 여도 확정하지 않음
        food = ("FOOD", 0.99, {"FOOD": 0.99, "ETC": 0.01}, "jev-test")
        cases = {
            "검색 0건": None,
            "가게 아닌 업종": ("kakao", "새봄동물", "금융서비스"),
            "다른 가게": ("naver", "새봄식당", "한식"),
        }
        for label, found in cases.items():
            with self.subTest(label):
                result, ask = self.run_judge(found, food)
                self.assertNotIn("apply", result)
                self.assertEqual(result["review"], ([("FOOD", 0.99), ("ETC", 0.01)], classifier.NOT_FOUND_REASON))
                self.assertEqual(ask.call_args.args[0], "가맹점 이름: 새봄동물")  # 다른 가게 업종은 Jev 에도 안 넘김
        self.assertTrue(result["record"]["store_mismatch"])

    def test_pg_is_found_by_map_or_web(self):
        # 11/16 실제 검색: 나이스페이먼츠 지도 '지불,결제대행', KG이니시스 웹 '온라인 전자결제PG사' → Jev 를 부르지 않는다
        result, ask = self.run_judge(("naver", "나이스페이먼츠", "지불,결제대행"), name="나이스페이먼츠")
        self.assertEqual((result["pg"], result["record"]["pg"]), (True, "industry"))
        ask.assert_not_called()
        result, ask = self.run_judge(None, name="KG이니시스", web=["KG이니시스 / 온라인 전자결제PG사, 신용카드, 계좌이체"])
        self.assertEqual((result["pg"], result["record"]["pg"]), (True, "web"))
        ask.assert_not_called()

    def test_coffee_brand_web_is_not_pg(self):
        # 11/16 실제 검색: '메가 MGC 커피'는 지도는 다른 지점이지만 웹 설명이 근거가 되어 식비
        food = ("FOOD", 0.97, {"FOOD": 0.97, "ETC": 0.03}, "jev-test")
        result, _ = self.run_judge(("naver", "메가MGC커피 광화문역점", "카페"), food, name="메가 MGC 커피",
                                   web=["메가MGC커피 / 메가커피 커피전문점, 프랜차이즈"])
        self.assertEqual(result["apply"], ("FOOD", 0.97))
        self.assertTrue(result["record"]["web_accepted"])
        self.assertNotIn("pg", result["record"])

    def test_cut_name_allows_prefix_match(self):
        # 11/16 실거래 '대표_4_카카오페이 - 주식회사 더뮤지엄비지'(41바이트): 카드사가 '더뮤지엄비지터'를 잘랐다. 실제 웹 결과 그대로
        fashion = ("FASHION_SHOPPING", 0.97, {"FASHION_SHOPPING": 0.97, "ETC": 0.03}, "jev-test")
        web = ["새상품) 더티셔츠뮤지엄 셔츠 S | 셔츠 / 더티셔츠뮤지엄의 깔끔한 디자인의 셔츠예요",
               "'디렉터 부부 첫 컬래버' 다이닛x더뮤지엄비지터, 성수동 팝업 연다 / 디자이너 박문수의 브랜드 '더뮤지엄비지터(THE MUSEUM VISITOR)'",
               "'크림뮤지엄' 관련 상품 찾기 | 총 6건"]
        result, ask = self.run_judge(None, fashion, name="주식회사 더뮤지엄비지", web=web, cut=True)
        self.assertEqual(result["apply"], ("FASHION_SHOPPING", 0.97))
        self.assertTrue(result["record"]["cut_name"])
        self.assertIn("더뮤지엄비지터", ask.call_args.args[0])
        self.assertNotIn("더티셔츠", ask.call_args.args[0])
        # 잘리지 않은 적요면 지금처럼 낱말이 끝나야 근거
        result, _ = self.run_judge(None, fashion, name="주식회사 더뮤지엄비지", web=web)
        self.assertEqual(result["review"][1], classifier.NOT_FOUND_REASON)
        self.assertNotIn("cut_name", result["record"])

    def test_cut_name_is_completed_and_searched_again(self):
        # 11/16 실제 검색: '더뮤지엄비지터'로 다시 찾으면 카카오 '더 뮤지엄비지터' · 의류판매, 웹 'Official Online Store'
        def stores(query):
            return [("kakao", "더 뮤지엄비지터", "의류판매")] if query == "더뮤지엄비지터" else []

        def texts(query):
            if query == "더뮤지엄비지터":
                return ["더 뮤지엄 비지터 / The Museum Visitor | Official Online Store | Art & Design Studio"]
            return ["다이닛x더뮤지엄비지터, 성수동 팝업 연다 / 디자이너 박문수의 브랜드 '더뮤지엄비지터(THE MUSEUM VISITOR)'"]

        def answer(state):
            confidence = 0.98 if "의류판매" in state else 0.79
            return "FASHION_SHOPPING", confidence, {"FASHION_SHOPPING": confidence, "ETC": 1 - confidence}, "jev-test"

        with patch("finance.classifier.search_store", side_effect=stores) as store, \
                patch("finance.classifier.search_web", side_effect=texts), patch("finance.classifier.ask_jev", side_effect=answer):
            result = classifier.judge("주식회사 더뮤지엄비지", cut=True)
        self.assertEqual(result["apply"], ("FASHION_SHOPPING", 0.98))
        self.assertEqual((result["record"]["industry"], result["record"]["cut_name"], result["record"]["completed_name"]),
                         ("의류판매", True, True))
        self.assertEqual([c.args[0] for c in store.call_args_list], ["더뮤지엄비지", "더뮤지엄비지터"])
        self.assertEqual(classifier.completed_name(texts("x"), "더뮤지엄비지"), "더뮤지엄비지터")
        self.assertIsNone(classifier.completed_name(["더뮤지엄비지 매장"], "더뮤지엄비지"))

    def test_cut_name_prefix_on_map_and_short_names(self):
        beauty = ("BEAUTY", 0.95, {"BEAUTY": 0.95, "ETC": 0.05}, "jev-test")
        result, _ = self.run_judge(("naver", "새봄헤어살롱 성수점", "미용실"), beauty, name="새봄헤어살", cut=True)
        self.assertEqual(result["apply"], ("BEAUTY", 0.95))
        # 5글자보다 짧으면 잘렸어도 앞부분 일치를 허용하지 않는다 (리디 ↔ 리디앤폴)
        result, _ = self.run_judge(("naver", "리디앤폴", "서점"), beauty, name="리디", cut=True)
        self.assertTrue(result["record"]["store_mismatch"])
        self.assertNotIn("cut_name", result["record"])
        self.assertTrue(classifier.same_store("더뮤지엄비지", "더뮤지엄비지터 성수점", prefix=True))
        self.assertFalse(classifier.same_store("더뮤지엄비지", "더뮤지엄비지터 성수점"))

    def test_same_store(self):
        # 11/05 결정: 공백·기호를 뺀 이름이 완전히 같을 때만
        self.assertTrue(classifier.same_store("토피코테스트점", "토피코 테스트점"))
        self.assertTrue(classifier.same_store("스타벅스 강남역점", "스타벅스 강남역점"))
        for query, found in (("새봄동물", "새봄동물병원"), ("새봄동물", "새봄식당"), ("리디", "팩토리디"), ("리디", "리디앤폴"),
                             ("스타벅스", "홍단헤어 스타벅스점"), ("김밥나라", "김밥천국"), ("새봄동물", ""), ("", "")):
            self.assertFalse(classifier.same_store(query, found), found)

    def test_web_only_applies_when_name_is_in_the_text(self):
        travel = ("TRAVEL_LODGING", 0.99, {"TRAVEL_LODGING": 0.99, "ETC": 0.01}, "jev-test")
        result, ask = self.run_judge(None, travel, name="여행앱", web=["여행앱 - 항공권 호텔 예약", "다른 글"])
        self.assertEqual(result["apply"], ("TRAVEL_LODGING", 0.99))
        self.assertEqual(ask.call_args.args[0], "가맹점 이름: 여행앱\n웹 검색 설명: 여행앱 - 항공권 호텔 예약")
        self.assertTrue(result["record"]["web_accepted"])
        self.assertNotIn("항공권", str(result["record"]))  # 웹 원문은 저장하지 않는다

        result, ask = self.run_judge(None, travel, name="여행앱", web=["전혀 다른 글"])  # 이름이 없으면 근거 아님
        self.assertEqual(ask.call_args.args[0], "가맹점 이름: 여행앱")
        self.assertEqual(result["review"][1], classifier.NOT_FOUND_REASON)
        self.assertFalse(result["record"]["web_accepted"])

    def test_map_and_web_are_asked_separately(self):
        store = ("naver", "새봄동물", "미용실")
        beauty = ("BEAUTY", 0.97, {"BEAUTY": 0.97}, "jev-test")
        culture = ("CULTURE_LEISURE", 0.97, {"CULTURE_LEISURE": 0.97}, "jev-test")
        unknown = ("UNKNOWN", 0.6, {"UNKNOWN": 0.6, "ETC": 0.4}, "jev-test")
        web = ["새봄동물 웹툰 서비스"]

        result, ask = self.run_judge(store, [beauty, culture], web=web)  # 답이 다르면 묻는다
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(result["review"], ([("BEAUTY", 0.97), ("CULTURE_LEISURE", 0.97)], classifier.UNSURE_REASON))
        self.assertTrue(result["record"]["evidence_conflict"])

        result, _ = self.run_judge(store, [beauty, beauty], web=web)  # 답이 같으면 적용
        self.assertEqual(result["apply"], ("BEAUTY", 0.97))

        result, _ = self.run_judge(store, [beauty, unknown], web=web)  # 한쪽 판단 보류는 충돌이 아니다
        self.assertEqual(result["apply"], ("BEAUTY", 0.97))

    def test_web_failure_falls_back_to_map(self):
        answer = ("BEAUTY", 0.97, {"BEAUTY": 0.97}, "jev-test")
        with self.assertLogs("finance.classifier", "WARNING"):
            result, ask = self.run_judge(("naver", "새봄동물", "미용실"), answer, web=OSError("limit"))
        self.assertEqual(result["apply"], ("BEAUTY", 0.97))
        self.assertEqual(result["record"]["web_error"], "OSError")
        ask.assert_called_once()

    def test_multi_item_among_results_wins(self):
        # PR #94 리뷰: 함께 검색된 업종을 모두 보고 모두 남긴다. 식비 기본값은 전부 편의점·마트일 때만
        for other in ("카페", "백화점", "미용실"):
            with self.subTest(other):
                result, ask = self.run_judge([("naver", "새봄동물", "슈퍼마켓"), ("kakao", "새봄동물", other)])
                self.assertEqual(result["review"], ([], classifier.MULTI_ITEM_REASON))
                self.assertEqual(result["record"]["industry"], f"슈퍼마켓, {other}")
                self.assertTrue(result["record"]["multi_item"])
                ask.assert_not_called()
        result, _ = self.run_judge([("naver", "새봄동물", "슈퍼마켓"), ("kakao", "새봄동물", "대형마트")])
        self.assertEqual(result["retail"], "FOOD")

    def test_web_can_stop_the_food_default(self):
        store = ("naver", "새봄동물", "슈퍼마켓")
        web = ["새봄동물 미용 예약"]
        result, _ = self.run_judge(store, ("BEAUTY", 0.95, {"BEAUTY": 0.95}, "jev-test"), web=web)
        self.assertEqual(result["review"], ([("FOOD", None), ("BEAUTY", 0.95)], classifier.MULTI_ITEM_REASON))
        self.assertTrue(result["record"]["evidence_conflict"])
        for answer in (("FOOD", 0.95, {}, "jev-test"), ("UNKNOWN", 0.95, {}, "jev-test"), ("BEAUTY", 0.6, {}, "jev-test")):
            result, _ = self.run_judge(store, answer, web=web)
            self.assertEqual(result["retail"], "FOOD", answer)

    def test_service_name_for_search(self):
        # 11/06: 해외 결제 표기는 검색에만 서비스 이름으로 줄인다
        for raw, name in (("OPENAI *CHATGPT SUBSCR", "CHATGPT"), ("OPENAI*CHATGPTSUBSCR", "CHATGPT"), ("ANTHROPIC* CLAUDE SUB", "CLAUDE"),
                          ("ANTHROPIC*CLAUDESUB", "CLAUDE"), ("RUNPOD.IO", "RUNPOD"), ("TYPESAFE AI, INC.", "TYPESAFE AI"), ("MIDJOURNEY INC.", "MIDJOURNEY"),
                          ("CURSOR, AI POWERED IDE", "CURSOR"), ("GITHUB COPILOT", "GITHUB COPILOT"), ("새봄 서브", "새봄 서브"), ("SUB", "SUB")):
            self.assertEqual(classifier.service_name(classifier.store_name(raw)), name, raw)
        result, ask = self.run_judge(None, ("ETC", 0.98, {"ETC": 0.98}, "jev-test"), name="RUNPOD.IO", web=["Runpod GPU 클라우드"])
        self.assertEqual(result["apply"], ("ETC", 0.98))
        self.assertEqual(ask.call_args.args[0], "가맹점 이름: RUNPOD\n웹 검색 설명: Runpod GPU 클라우드")

    def test_name_inside_another_store_is_not_web_evidence(self):
        # PR #94 리뷰: 지도에서 뺀 다른 가게가 웹 근거로 다시 들어오면 안 된다
        for text, expected in (("팩토리디 / 헤어 전문 미용실 예약 안내", False), ("리디앤폴 카페 메뉴", False), ("리디는 전자책 서비스", True),
                               ("리디 - 나무위키", True), ("리디의 웹소설", True), ("[리디] 공지", True)):
            self.assertEqual(classifier.names_store(text, "리디"), expected, text)
        self.assertTrue(classifier.names_store("메가 MGC 커피 안내", "메가MGC"))
        self.assertTrue(classifier.names_store("Trip.com 트립닷컴에서 예약", "트립닷컴"))
        self.assertFalse(classifier.names_store("아무 글", ""))
        result, ask = self.run_judge(None, ("BEAUTY", 0.97, {"BEAUTY": 0.97}, "jev-test"), name="리디", web=["팩토리디 / 헤어 전문 미용실"])
        self.assertEqual(ask.call_args.args[0], "가맹점 이름: 리디")
        self.assertEqual(result["review"][1], classifier.NOT_FOUND_REASON)

    def test_search_store_uses_both_maps(self):
        naver = {"items": [{"title": "<b>새봄</b> 동물&amp;병원", "category": "병원>동물병원"}]}
        kakao = {"documents": [{"place_name": "새봄동물", "category_name": "가정,생활 > 반려동물 > 동물병원"}]}
        keys = {"NAVER_CLIENT_ID": "id", "KAKAO_REST_API_KEY": "key"}

        def reply(*failing):
            def fake(url, headers):
                if any(word in url for word in failing):
                    raise OSError("down")
                return naver if "naverapihub" in url else kakao
            return fake

        with patch.dict(os.environ, keys):
            with patch("finance.classifier._get_json", side_effect=reply()):
                self.assertEqual(classifier.search_store("새봄동물"),
                                 [("naver", "새봄 동물&병원", "동물병원"), ("kakao", "새봄동물", "동물병원")])
            with patch("finance.classifier._get_json", side_effect=reply("naverapihub")):
                self.assertEqual(classifier.search_store("새봄동물"), [("kakao", "새봄동물", "동물병원")])
            with patch("finance.classifier._get_json", side_effect=reply("naverapihub", "kakao")), self.assertRaises(OSError):
                classifier.search_store("새봄동물")

    def test_search_web_cuts_text(self):
        with patch.dict(os.environ, {"NAVER_CLIENT_ID": "id"}), \
                patch("finance.classifier._get_json", return_value={"items": [{"title": "<b>제목</b>", "description": "가" * 300}]}) as get:
            (text,) = classifier.search_web("새봄동물")
        self.assertTrue(text.startswith("제목 / 가"))
        self.assertEqual(len(text), classifier.WEB_TEXT_LIMIT)
        self.assertIn("/search/v1/webkr", get.call_args.args[0])


@skipUnless(classifier.MODEL_PATH.exists(), "모델 파일이 없는 PC (docs/ML_MODELS.md)")
class RealModelTest(SimpleTestCase):
    def test_shared_model_loads_and_predicts(self):
        top, empty = classifier.predict(["루덴치과의원", "  "])
        self.assertEqual(top[0][0], "MEDICAL_HEALTH")
        self.assertGreaterEqual(top[0][1], classifier.THRESHOLD)
        self.assertEqual(len(top), 3)
        self.assertEqual(empty, [])


@patch("finance.classifier.judge", return_value=None)
@patch("finance.classifier.predict", side_effect=fake_predict)
class ClassifyCommandTest(TestCase):
    def test_reclassifies_saved_spending_but_not_confirmed(self, predict, judge):
        from io import StringIO

        from django.core.management import call_command

        user = get_user_model().objects.create_user(email="reclassify@example.com")
        base = dict(user=user, source_kind="manual", occurred_on="2026-10-30", amount=10000, direction="out",
                    transaction_type="spending", confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review")
        waiting = Tx.objects.create(merchant_name="루덴치과의원", **base)
        mine = Tx.objects.create(merchant_name="청담", **{**base, "confirmed_category_id": "BEAUTY",
                                                         "confirmation_state": "confirmed", "classification_source": "user"})
        out = StringIO()
        call_command("classify_transactions", "--email", "Reclassify@example.com", stdout=out)
        waiting.refresh_from_db()
        mine.refresh_from_db()
        self.assertEqual((waiting.confirmed_category_id, waiting.classification_source), ("MEDICAL_HEALTH", "ml"))
        self.assertEqual((mine.confirmed_category_id, mine.confirmation_state), ("BEAUTY", "confirmed"))
        self.assertIn("reclassify@example.com: ml 1건", out.getvalue())


class DeliveryAppTest(SimpleTestCase):
    def test_retail_category(self):
        for name, code in {"GS25 역삼점": "FOOD", "CU 역삼점": "FOOD", "씨유CU역삼점": "FOOD", "이마트24 서초점": "FOOD", "웰빙마트": "FOOD", "배민B마트 강남점": "FOOD",
                           "(주)아성다이소": "HOUSEHOLD", "쿠팡": None, "쿠팡마트": None, "현대백화점 판교점": None, "번개장터": None,
                           "롯데하이마트 강남점": None, "신앙촌상회": None, "루덴치과의원": None, "": None, None: None}.items():
            self.assertEqual(classifier.retail_category(name), code, name)

    def test_zigzag_operator_is_fashion(self):
        # 11/09: 지도(회사 사무실)와 웹(패션)이 엇갈려 묻던 지그재그 운영사
        self.assertEqual(classifier.rule_category(classifier.store_name("카카오스타일 - 카카오스타일")), "FASHION_SHOPPING")
        self.assertFalse(classifier.is_multi_item("카카오스타일"))

    def test_delivery_app_is_food_but_b_mart_is_multi_item(self):
        for name in ("배달의민족", "(주)우아한형제들", "요기요"):
            self.assertFalse(classifier.is_multi_item(name), name)
            self.assertEqual(classifier.rule_category(name), "FOOD", name)
        for name in ("배민B마트", "배민B마트 강남점", "배민스토어", "NICE*배민B마트"):
            self.assertTrue(classifier.is_multi_item(name), name)


@patch("finance.classifier.judge", return_value=None)
@patch("finance.classifier.predict", side_effect=fake_predict)
class ClassifyNamesCommandTest(TestCase):
    def test_prints_table_and_leaves_nothing_in_db(self, predict, judge):
        import tempfile
        from io import StringIO

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as tmp:
            names = os.path.join(tmp, "names.txt")
            with open(names, "w", encoding="utf-8") as f:
                f.write("루덴치과의원\nGS25 역삼점\n쿠팡\n크린토피아세탁\n")
            out = StringIO()
            call_command("classify_names", names, stdout=out)
        text = out.getvalue()
        self.assertIn("루덴치과의원 | MEDICAL_HEALTH | 4 SVM", text)
        self.assertIn("GS25 역삼점 | FOOD | 3 규칙", text)
        self.assertIn("쿠팡 | UNCLASSIFIED | 1 다품목", text)
        self.assertIn("크린토피아세탁 | ETC | 3 규칙", text)
        self.assertFalse(Tx.objects.exists())
        self.assertFalse(get_user_model().objects.filter(email__startswith="classify-names-").exists())
