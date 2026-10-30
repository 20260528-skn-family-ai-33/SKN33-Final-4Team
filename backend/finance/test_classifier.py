import os
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

    def setUp(self):
        # 5단계(외부 API)는 JudgeTest 에서 본다. 여기서는 '쓸 수 없음'(None)이 기본
        judge = patch("finance.classifier.judge", return_value=None)
        self.judge = judge.start()
        self.addCleanup(judge.stop)

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
        for tx in (coupang, gs25):
            self.assertEqual((tx.confirmed_category_id, tx.confirmation_state), ("UNCLASSIFIED", "needs_review"))
        self.assertEqual((musinsa.confirmed_category_id, musinsa.classification_source), ("FOOD", "ml"))
        predict.assert_called_once_with(["무신사"])

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
            "쿠팡플레이": "CULTURE_LEISURE", "멜론": "CULTURE_LEISURE", "리디(주)": "CULTURE_LEISURE", "리디아울렛": None,
            "강남장례식장": "ETC", "OO화재해상보험": "ETC", "옷수선": "ETC", "수선화꽃집": None, "루덴치과의원": None,
        }
        for name, code in cases.items():
            self.assertEqual(classifier.rule_category(name), code, name)
        self.assertFalse(classifier.is_multi_item("쿠팡플레이"))


@patch.dict(os.environ, {"TYPESAFE_API_KEY": "test", "JEV_MODEL": "jev-test"})
class JudgeTest(SimpleTestCase):
    def run_judge(self, found, answer=None, name="새봄동물"):
        with patch("finance.classifier.search_store", **({"side_effect": found} if isinstance(found, Exception) else {"return_value": found})), \
                patch("finance.classifier.ask_jev", return_value=answer) as ask:
            return classifier.judge(name), ask

    def test_confident_jev_applies(self):
        result, ask = self.run_judge(("naver", "새봄동물병원", "동물병원"),
                                     ("MEDICAL_HEALTH", 0.97, {"MEDICAL_HEALTH": 0.97, "HOUSEHOLD": 0.03}, "jev-test"))
        self.assertEqual(result["apply"], ("MEDICAL_HEALTH", 0.97))
        self.assertEqual(ask.call_args.args[0], "가맹점 이름: 새봄동물\n검색 업종: 동물병원")
        self.assertEqual((result["record"]["source"], result["record"]["jev_model"]), ("naver", "jev-test"))
        self.assertIn("ms", result["record"])

    def test_unknown_or_low_stays_unclassified(self):
        result, _ = self.run_judge(("naver", "새봄", "음식점"),
                                   ("UNKNOWN", 0.8, {"UNKNOWN": 0.8, "FOOD": 0.15, "ETC": 0.05}, "jev-test"))
        self.assertEqual(result["review"], ([("FOOD", 0.15), ("ETC", 0.05)], classifier.UNSURE_REASON))
        result, _ = self.run_judge(None, ("FOOD", 0.6, {"FOOD": 0.6, "BEAUTY": 0.4}, "jev-test"))
        self.assertEqual(result["review"][1], classifier.NOT_FOUND_REASON)

    def test_multi_item_industry_skips_jev(self):
        result, ask = self.run_judge(("naver", "OO상회", "편의점"))
        self.assertEqual(result["review"], ([], classifier.MULTI_ITEM_REASON))
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

    def test_without_jev_key_returns_none(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            self.assertIsNone(classifier.judge("새봄동물"))


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
    def test_delivery_app_is_food_but_b_mart_is_multi_item(self):
        for name in ("배달의민족", "(주)우아한형제들", "요기요"):
            self.assertFalse(classifier.is_multi_item(name), name)
            self.assertEqual(classifier.rule_category(name), "FOOD", name)
        for name in ("배민B마트", "배민B마트 강남점", "배민스토어", "NICE*배민B마트"):
            self.assertTrue(classifier.is_multi_item(name), name)
