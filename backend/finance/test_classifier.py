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
            "쿠팡플레이": "CULTURE_LEISURE", "멜론": "CULTURE_LEISURE", "리디(주)": "CULTURE_LEISURE", "리디아울렛": None,
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

    def test_taxi_and_pc_room(self):
        # 11/02 카드 명세·카카오 장소 조사: '택시'가 낱말이거나 '개인택시'일 때만 교통. 이름 속 '택시' 식당은 제외
        cases = {
            "카카오_개인택시": "TRANSPORT_CAR", "카카오T 택시": "TRANSPORT_CAR", "개인택시 서울12가3456": "TRANSPORT_CAR",
            "택시파스타 압구정로데오역점": None, "피자택시": None, "옐로우택시": None, "택시종사자쉼터": None,
            "서울개인택시조합새마을금고": None, "카카오페이택시충전": None,
            "레벨업 피시방 남부터미널점": "CULTURE_LEISURE", "탑PC방": "CULTURE_LEISURE",
        }
        for name, code in cases.items():
            self.assertEqual(classifier.rule_category(name), code, name)

    def test_starbucks_brand(self):
        # 11/02: 지점명·회사명·앱 충전 모두 식비. 이름 뒤쪽에 '스타벅스'가 들어간 다른 가게는 제외
        for name in ("스타벅스 강남역점", "스타벅스코리아", "스타벅스계좌", "STARBUCKS KOREA", "(주)스타벅스코리아"):
            self.assertEqual(classifier.rule_category(name), "FOOD", name)
        self.assertIsNone(classifier.rule_category("홍단헤어 스타벅스점"))

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
        result, ask = self.run_judge(("naver", "새봄동물", "편의점"))
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

    def test_same_store(self):
        self.assertTrue(classifier.same_store("새봄동물", "새봄동물병원"))
        self.assertTrue(classifier.same_store("스타벅스 강남역점", "스타벅스 강남역점"))
        self.assertFalse(classifier.same_store("새봄동물", "새봄식당"))
        # PR #64 재리뷰: 공백만 다른 이름도 같은 결과, 첫 단어만 같으면 다른 가게
        self.assertFalse(classifier.same_store("새봄 동물", "새봄식당"))
        self.assertFalse(classifier.same_store("스타벅스 강남역점", "스타벅스 강남구청역점"))
        self.assertFalse(classifier.same_store("새봄동물", ""))

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
                f.write("루덴치과의원\nGS25 역삼점\n크린토피아세탁\n")
            out = StringIO()
            call_command("classify_names", names, stdout=out)
        text = out.getvalue()
        self.assertIn("루덴치과의원 | MEDICAL_HEALTH | 4 SVM", text)
        self.assertIn("GS25 역삼점 | UNCLASSIFIED | 1 다품목", text)
        self.assertIn("크린토피아세탁 | ETC | 3 규칙", text)
        self.assertFalse(Tx.objects.exists())
        self.assertFalse(get_user_model().objects.filter(email__startswith="classify-names-").exists())
