from decimal import Decimal
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
        self.tx("청담", confirmed_category_id="BEAUTY", confirmation_state="confirmed", classification_source="user")
        (tx,) = self.run_classify(self.tx("청담"))
        self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                         ("BEAUTY", "suggested", "rule"))
        predict.assert_called_once_with([])

    def test_multi_item_never_reuses_memory_or_calls_ml(self, predict):
        self.tx("GS25 역삼점", confirmed_category_id="FOOD", confirmation_state="confirmed", classification_source="user")
        remembered, fresh = self.run_classify(self.tx("GS25 역삼점"), self.tx("웰빙마트"))
        for tx in (remembered, fresh):
            self.assertEqual((tx.confirmed_category_id, tx.confirmation_state, tx.classification_source),
                             ("UNCLASSIFIED", "needs_review", None))
            self.assertEqual(tx.suggestion_reason, classifier.MULTI_ITEM_REASON)
        # 과거 선택은 후보로만
        self.assertEqual((remembered.suggested_category_id, remembered.suggestion_candidates),
                         ("FOOD", [{"category": "FOOD", "confidence": None}]))
        self.assertEqual((fresh.suggested_category_id, fresh.suggestion_candidates), (None, None))
        predict.assert_called_once_with([])

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
                     "(주)쿠팡", "(주)아성다이소", "현대백화점 판교점", "번개장터",
                     # 동명 브랜드 CSV
                     "CU", "CU 역삼점", "씨유 정선사북점", "씨유대전", "지에스25 태백진주점", "롯데슈퍼 탑동3점", "노브랜드 원주단구점", "농협하나로마트 광교점", "신앙촌상회", "매점"):
            self.assertTrue(classifier.is_multi_item(name), name)
        for name in ("루덴치과의원", "스타벅스 강남역점", "쿠팡이츠", "롯데하이마트 강남점", "", None,
                     "CUBE카페", "씨유엔터테인먼트", "노브랜드버거 강릉사천점", "씨제이올리브영 원주기업도시점", "아이스크림할인점", "신앙촌상회 문정점"):
            self.assertFalse(classifier.is_multi_item(name), name)


@skipUnless(classifier.MODEL_PATH.exists(), "모델 파일이 없는 PC (docs/ML_MODELS.md)")
class RealModelTest(SimpleTestCase):
    def test_shared_model_loads_and_predicts(self):
        top, empty = classifier.predict(["루덴치과의원", "  "])
        self.assertEqual(top[0][0], "MEDICAL_HEALTH")
        self.assertGreaterEqual(top[0][1], classifier.THRESHOLD)
        self.assertEqual(len(top), 3)
        self.assertEqual(empty, [])
