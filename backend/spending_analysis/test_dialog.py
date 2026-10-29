"""소비 분석 에이전트 대화 계층 테스트 (유형 1~9 · 되묻기 · 후속 질문 · 설명 근거 · 분석 기록) — 라우터 없이 소비만
실행: backend 폴더에서 python manage.py test spending_analysis --settings=spending_fixture.settings

단계 B 에서 chat/test_router_core.py 의 소비 규칙 테스트를 옮겼다. 기대값은 그대로이고 부르는 곳만 바뀌었다:
- self.say = dialog.step (소비 전용 해석 → 결정). LLM 은 RoleFakeLLM — 통합 형식 가짜 추출값(legacy.SAID)을 소비 형식으로 바꿔 답한다
- 대기 질문은 dialog.pending (소비 되묻기 칸)
DB 를 쓰지 않는다.
"""
import json

from django.test import SimpleTestCase

from chat.interpretation.legacy import RoleFakeLLM, item
from chat.router_core import MemoryStateStore

from . import dialog
from .dialog import record_analysis, run_items
from .reading import CATEGORY_NAMES

PERIOD = {"from": "2026-10-01", "to": "2026-10-29"}


class SpendingDialogTest(SimpleTestCase):
    def setUp(self):
        self.store, self.llm = MemoryStateStore(), RoleFakeLLM()

    def say(self, text, talk="talk-1", llm=None):
        return dialog.step(self.store, talk, text, llm or self.llm)

    def done(self, decision, talk="talk-1", fail=()):
        """가짜 분석 실행: 항목별 조회 결과를 기록. fail 에 든 카테고리는 조회 실패"""
        def fetch(one):
            if one["category"] in fail:
                raise LookupError
            return {"evidence": "confirmed", "period": PERIOD, "as_of": "2026-10-29T10:00:00+09:00", "facts": {"F1": 1}}
        record_analysis(self.store, talk, decision, run_items(decision.items, fetch))

    def ask_then(self, first, then):
        self.done(self.say(first))
        return self.say(then)

    def test_other_domain_is_not_mine_and_changes_nothing(self):
        self.say("최근 0개월 식비 알려줘")
        before = self.store.export("talk-1")
        for text in ["식비 예산 줄여줘", "소비 분석하고 예산도 줄여줘", "날씨 어때?"]:
            with self.subTest(text):
                self.assertEqual(self.say(text).status, "not_mine")  # 목적지는 정하지 않는다
                self.assertEqual(self.store.export("talk-1"), before)

    def test_own_domain_unsupported_and_missing_info_are_not_not_mine(self):
        self.assertEqual(self.say("지난달 얼마 썼어?").reason, "last_month_only")  # 미지원 = 소비가 안내
        self.assertEqual(self.say("최근 0개월 식비 알려줘").asking, "months")    # 정보 부족 = 소비가 되물음
        self.assertEqual(self.say("모르는 카테고리 코드").status, "failed")      # 해석 오류 = failed

    def test_summary_shares_conditions_and_only_confirmed_facts(self):
        self.done(self.say("최근 2개월 식비랑 교통비 알려줘"))
        record_analysis(self.store, "talk-1", self.say("지난달보다 식비 더 썼어?"), [
            {"item": item(7, "FOOD"), "ok": True, "result": {"evidence": "limited", "limits": ["previous:partial"],
                                                              "period": PERIOD, "as_of": "t", "facts": {"x": 1}}}])
        shared = dialog.summary(self.store, "talk-1")
        self.assertEqual((shared["has_question"], shared["previous"]["compare"]), (False, "prev_month"))
        self.assertEqual(shared["items"], [{"type": 7, "category": "FOOD", "ok": True, "evidence": "limited",
                                            "limits": ["previous:partial"], "period": PERIOD, "as_of": "t"}])  # 부분 자료 수치 없음

    def test_spending_types_1_to_9(self):
        cases = {
            "이번 달 얼마 썼어?": (item(1),),
            "식비 얼마 썼어?": (item(2, "FOOD"),),
            "식비 예산 얼마 남았어?": (item(3, "FOOD"),),
            "예산 얼마 남았어?": (item(4),),
            "소비 알려줘": (item(5),),
            "지난달보다 많이 썼어?": (item(6),),
            "지난달보다 식비 더 썼어?": (item(7, "FOOD"),),
            "최근 몇 달 소비 어때?": (item(8, months_requested=None, months_applied=3),),
            "최근 6개월 식비 어때?": (item(9, "FOOD", months_requested=6, months_applied=3),),
        }
        for text, items in cases.items():
            with self.subTest(text):
                decision = self.say(text)
                self.assertEqual((decision.status, decision.intent), ("ready", "spending"))
                self.assertEqual(decision.items, items)  # 1~7번엔 개월 칸 자체가 없다 (기본 3개월 잘못 적용 안 함)

    def test_months_over_max_keeps_original_and_notices(self):
        decision = self.say("최근 6개월 식비 어때?")
        self.assertEqual(decision.interpretation.months, 6)
        self.assertEqual(decision.items[0]["months_applied"], 3)
        self.assertEqual(decision.notices, ("months_capped",))
        self.assertEqual(self.say("최근 몇 달 소비 어때?").notices, ())

    def test_many_categories_keep_order(self):
        decision = self.say("최근 2개월 식비랑 교통비 알려줘")
        self.assertEqual(decision.items, (
            item(9, "FOOD", months_requested=2, months_applied=2),
            item(9, "TRANSPORT_CAR", months_requested=2, months_applied=2),
        ))
        self.assertIsNone(dialog.pending(self.store, "talk-1"))  # 하나를 고르게 되묻지 않는다

    def test_partial_failure_keeps_successes(self):
        def fetch(one):
            if one["category"] == "TRANSPORT_CAR":
                raise LookupError
            return {"category": one["category"]}

        results = run_items(self.say("최근 2개월 식비랑 교통비 알려줘").items, fetch)
        self.assertEqual([(r["item"]["category"], r["ok"]) for r in results], [("FOOD", True), ("TRANSPORT_CAR", False)])
        self.assertEqual(results[0]["result"], {"category": "FOOD"})
        self.assertEqual(results[1]["error"], "LookupError")

    def test_last_month_alone_is_unsupported(self):
        decision = self.say("지난달 얼마 썼어?")
        self.assertEqual((decision.status, decision.intent, decision.reason, decision.items),
                         ("unsupported", "spending", "last_month_only", ()))

    def test_target_follows_analysis_subject_not_request_wording(self):
        # 같은 '알려줘 · 보여줘'라도 총액 → spend, 잔액 → budget_left, 전반 → overview 로 유형이 갈린다
        cases = {
            "의료비 합계 알려줘": (item(2, "MEDICAL_HEALTH"),), "의료비 합계 보여줘": (item(2, "MEDICAL_HEALTH"),),
            "의료비 예산 남은 거 알려줘": (item(3, "MEDICAL_HEALTH"),), "의료비 예산 남은 거 보여줘": (item(3, "MEDICAL_HEALTH"),),
            "의료비 쓴 거 어떤지 알려줘": (item(2, "MEDICAL_HEALTH"),),
            "이번 달 합계 알려줘": (item(1),), "남은 예산 보여줘": (item(4),), "이번 달 소비 흐름 보여줘": (item(5),),
        }
        for text, items in cases.items():
            with self.subTest(text):
                self.assertEqual(self.say(text, talk=text).items, items)

    def test_food_overview_is_type_2(self):
        self.assertEqual(self.say("식비 알려줘").items, (item(2, "FOOD"),))

    def test_category_compare_is_unsupported(self):
        decision = self.say("식비와 교통비 중 뭐가 더 많아?")
        self.assertEqual((decision.status, decision.reason, decision.items), ("unsupported", "category_compare", ()))
        self.assertIsNone(dialog.pending(self.store, "talk-1"))
        self.assertEqual(self.say("최근 2개월 식비랑 교통비 알려줘").status, "ready")  # 단순 복수 조회는 그대로

    def test_budget_left_with_months_asks_current_period(self):
        for text, about, done in [("최근 3개월 식비 예산 얼마나 남았어?", ("FOOD",), (item(3, "FOOD"),)),
                                  ("최근 3개월 예산 얼마나 남았어?", (), (item(4),))]:
            with self.subTest(text):
                decision = self.say(text)  # 8·9번 추이로 바꾸지 않는다
                self.assertEqual((decision.status, decision.intent, decision.asking, decision.ask_about),
                                 ("ask", "spending", "budget_current", about))
                self.assertEqual(self.say("음…").asking, "budget_current")  # 불분명 → 같은 질문, 상태 유지
                self.assertIsNotNone(dialog.pending(self.store, "talk-1"))
                decision = self.say("응 이번 기간으로")
                self.assertEqual((decision.status, decision.items), ("ready", done))  # 개월 칸 없음 = 현재 예산 기간
                self.assertIsNone(dialog.pending(self.store, "talk-1"))

    def test_budget_left_keeps_meaning_for_every_unsupported_mode(self):
        # 남은 예산은 비교 방식이 무엇이든 6·7번(소비 비교) · 8·9번(소비 추이) 으로 바뀌지 않는다
        cases = [
            ("지난달보다 식비 예산 더 남았어?", "prev_month", (item(3, "FOOD"),)),
            ("지난달보다 예산 더 남았어?", "prev_month", (item(4),)),
            ("최근 3개월 식비 예산 얼마나 남았어?", "recent", (item(3, "FOOD"),)),
            ("최근 3개월 예산 얼마나 남았어?", "recent", (item(4),)),
            ("지난달 식비 예산 얼마 남았어?", "last_month", (item(3, "FOOD"),)),
        ]
        for text, mode, done in cases:
            with self.subTest(text):
                decision = self.say(text)
                self.assertEqual((decision.status, decision.asking, decision.reason, decision.items),
                                 ("ask", "budget_current", mode, ()))  # reason = 지원 안 하는 비교 방식 (미지원 안내용)
                self.assertEqual(self.say("응 이번 기간으로").items, done)

    def test_budget_left_category_compare_is_unsupported_not_asked(self):
        decision = self.say("식비랑 교통비 중 예산 뭐가 더 남았어?")
        self.assertEqual((decision.status, decision.reason, decision.asking, decision.items),
                         ("unsupported", "category_compare", None, ()))
        self.assertIsNone(dialog.pending(self.store, "talk-1"))
        # 단순 복수 카테고리 잔액 조회는 각각 3번
        self.assertEqual(self.say("식비랑 교통비 예산 얼마 남았어?").items, (item(3, "FOOD"), item(3, "TRANSPORT_CAR")))

    def test_amount_mode_answer_alone_completes_nothing(self):
        # 소비 되묻기 중 by/to 만 있는 답: 개월 수를 채우지 못한다 → 대기 상태 그대로
        # [구조 변경] 분리 전에는 통합 형식의 금액 보조값 형식 오류로 failed 였다. 소비 해석에는 금액 칸이 없어서
        # 이제는 같은 질문을 다시 한다(ask). 라우터 쪽 형식 오류(failed)는 chat/test_router_core.py 에서 그대로 확인
        self.say("최근 0개월 식비 알려줘")
        saved = dialog.pending(self.store, "talk-1")
        decision = self.say("5만 원만큼")
        self.assertEqual((decision.status, decision.asking), ("ask", "months"))
        self.assertEqual(dialog.pending(self.store, "talk-1"), saved)

    def test_budget_left_with_months_declined(self):
        self.say("최근 3개월 식비 예산 얼마나 남았어?")
        decision = self.say("아니 괜찮아")
        self.assertEqual((decision.status, decision.reason, decision.items), ("cancelled", "declined", ()))
        self.assertIsNone(dialog.pending(self.store, "talk-1"))

    def test_unknown_category_is_asked_not_etc(self):
        decision = self.say("커피값 얼마 썼어?")
        self.assertEqual((decision.status, decision.asking, decision.ask_about), ("ask", "category", ("커피값",)))
        decision = self.say("식비")
        self.assertEqual(decision.items, (item(2, "FOOD"),))  # 쓴 돈(target) 조건 유지

    def test_partly_unknown_categories_keep_place_and_period(self):
        decision = self.say("최근 2개월 데이트비랑 식비 알려줘")
        self.assertEqual((decision.status, decision.asking, decision.ask_about), ("ask", "category", ("데이트비",)))
        self.assertEqual(dialog.pending(self.store, "talk-1")["request"]["categories"], ({"unknown": "데이트비"}, "FOOD"))
        self.assertEqual(self.say("응").asking, "category")  # 답이 아니면 다시 묻고 확인한 조건은 그대로
        decision = self.say("문화여가야")
        self.assertEqual(decision.items, (item(9, "CULTURE_LEISURE", months_requested=2, months_applied=2),
                                          item(9, "FOOD", months_requested=2, months_applied=2)))

    def test_category_question_offers_service_categories(self):
        decision = self.say("커피값 얼마 썼어?")
        self.assertEqual(decision.options, tuple({"code": c, "name": n} for c, n in CATEGORY_NAMES.items()))
        self.assertEqual(decision.options[8], {"code": "CULTURE_LEISURE", "name": "문화·여가"})  # 기존 화면 이름 그대로
        self.assertEqual(len(decision.options), 12)

    def test_unclassified_is_spending_only(self):
        # 소비 조회는 지원
        self.assertEqual(self.say("확인 필요 얼마 썼어?").items, (item(2, "UNCLASSIFIED"),))
        no_budget = ({"category": "UNCLASSIFIED", "reason": "no_budget_category"},)
        # 남은 예산 조회만 있으면 미지원: 넘길 항목 없음, 다른 코드 · 0 원으로 바꾸지 않음, 현재 기간 질문도 안 함
        # (예산 조정은 라우터가 거르지 않는다 → test_budget_adjust_is_never_asked_or_blocked_by_router)
        for text in ["확인 필요 예산 얼마 남았어?", "최근 3개월 확인 필요 예산 얼마 남았어?"]:
            with self.subTest(text):
                decision = self.say(text)
                self.assertEqual((decision.status, decision.intent, decision.reason, decision.skipped),
                                 ("unsupported", "spending", "no_budget_category", no_budget))
                self.assertEqual(decision.items, ())  # 소비 결정에는 예산 전달 칸이 없다 (분리 전 handoff=None 확인)
                self.assertIsNone(dialog.pending(self.store, "talk-1"))

    def test_budget_left_many_categories_keep_supported_and_split_unclassified(self):
        no_budget = ({"category": "UNCLASSIFIED", "reason": "no_budget_category"},)
        decision = self.say("식비랑 확인 필요랑 기타 예산 얼마 남았어?")
        self.assertEqual((decision.status, decision.items, decision.skipped),
                         ("ready", (item(3, "FOOD"), item(3, "ETC")), no_budget))  # ETC 는 예산 경로로
        self.assertEqual(self.say("식비 예산 얼마 남았어?").skipped, ())

    def test_budget_left_category_question_leaves_out_unclassified(self):
        options = self.say("데이트비 예산 얼마 남았어?").options
        self.assertEqual(len(options), 11)
        self.assertNotIn("UNCLASSIFIED", [o["code"] for o in options])
        self.assertIn({"code": "ETC", "name": "기타"}, options)

    def test_category_pick_by_code_or_name_fills_each_hole_in_order(self):
        for first, second in [("CULTURE_LEISURE", "BEAUTY"), ("문화·여가", "뷰티 미용")]:
            with self.subTest(first):
                self.store = MemoryStateStore()  # 같은 대화면 앞에서 고른 '데이트비' 를 기억해 다시 묻지 않는다
                calls = len(self.llm.calls)
                self.say("최근 2개월 데이트비랑 식비랑 간식비 알려줘")
                decision = self.say(first)  # 첫 번째 모르는 자리만 채우고 남은 자리를 다시 묻는다
                self.assertEqual((decision.status, decision.ask_about), ("ask", ("간식비",)))
                decision = self.say(second)
                self.assertEqual([i["category"] for i in decision.items], ["CULTURE_LEISURE", "FOOD", "BEAUTY"])
                self.assertEqual({i["months_applied"] for i in decision.items}, {2})
                self.assertEqual(len(self.llm.calls), calls + 1)  # 목록에서 고른 답은 LLM 을 부르지 않는다

    def test_b_zero_months_then_answer(self):
        decision = self.say("최근 0개월 식비 알려줘")
        self.assertEqual((decision.status, decision.asking), ("ask", "months"))
        decision = self.say("2개월")
        self.assertEqual(decision.items, (item(9, "FOOD", months_requested=2, months_applied=2),))
        self.assertIsNone(dialog.pending(self.store, "talk-1"))

    def test_f_failure_keeps_state(self):
        self.say("최근 0개월 식비 알려줘")
        saved = dialog.pending(self.store, "talk-1")
        for text, llm in [("2개월", RoleFakeLLM(broken=True)), ("JSON 아님", None), ("형식 깨짐", None),
                          ("모르는 칸", None), ("모르는 카테고리 코드", None),
                          ("의도 빠짐", None), ("모르는 말 형식 틀림", None)]:
            with self.subTest(text):
                self.assertEqual(self.say(text, llm=llm).status, "failed")
                self.assertEqual(dialog.pending(self.store, "talk-1"), saved)

    def test_follow_up_without_previous_analysis_asks_scope(self):
        # 10/29 1단계: '완료 후 상속 없음' 폐기 → 직전 성공 분석이 있으면 이어받고, 없으면 기본값으로 채우지 않고 묻는다
        # (이전 테스트는 라우터 ready 만으로 맥락이 생기지 않는다는 점도 함께 확인한다)
        self.say("최근 2개월 식비랑 교통비 알려줘")  # 소비 분석 계층이 성공을 기록하지 않음
        decision = self.say("그럼 교통비는?")
        self.assertEqual((decision.status, decision.asking, decision.reason, decision.ask_about),
                         ("ask", "scope", "no_previous_analysis", ("TRANSPORT_CAR",)))
        decision = self.say("이번 달")  # 답한 조건 + 문장의 카테고리, 나머지는 기본값
        self.assertEqual(decision.items, (item(2, "TRANSPORT_CAR"),))
        self.assertIsNone(dialog.pending(self.store, "talk-1"))
        self.assertIsNone(self.store.get_analysis("talk-1"))

    def test_required_follow_ups(self):
        cases = [
            ("최근 2개월 식비 어때?", "그럼 교통비는?", (item(9, "TRANSPORT_CAR", months_requested=2, months_applied=2),)),
            ("최근 2개월 식비 어때?", "이번 달은?", (item(2, "FOOD"),)),  # 최근 추이 · 개월 수 제거
            ("지난달보다 식비 더 썼어?", "교통비도", (item(7, "TRANSPORT_CAR"),)),
            ("최근 몇 달 소비 어때?", "이번 달 총소비 알려줘", (item(1),)),  # 독립 요청 → 이어받지 않음
        ]
        for first, then, items in cases:
            with self.subTest(then):
                self.store = MemoryStateStore()
                decision = self.ask_then(first, then)
                self.assertEqual((decision.status, decision.items), ("ready", items))

    def test_budget_left_follow_up_asks_current_period(self):
        for first, about, done in [("최근 2개월 식비 어때?", ("FOOD",), (item(3, "FOOD"),)),
                                   ("최근 몇 달 소비 어때?", (), (item(4),))]:
            with self.subTest(first):
                self.store = MemoryStateStore()
                decision = self.ask_then(first, "예산은?")  # 소비 기간을 예산 기간으로 조용히 바꾸지 않는다
                self.assertEqual((decision.status, decision.asking, decision.reason, decision.ask_about),
                                 ("ask", "budget_current", "recent", about))
                self.assertEqual(self.say("음…").asking, "budget_current")
                self.assertEqual(self.say("응 이번 기간으로").items, done)
        self.say("예산은?")
        self.assertEqual(self.say("아니 괜찮아").reason, "declined")
        self.assertEqual(self.store.get_analysis("talk-1")["request"]["period"], "recent")  # 직전 분석(최근 몇 달)은 그대로

    def test_follow_up_without_target_keeps_previous_subject(self):
        # 후속 질문에서 대상을 말하지 않으면 target 은 비어 있고 직전 대상(잔액)을 이어받는다.
        # 추출값에 overview 를 채우면 3번이 2번으로 바뀐다 — 지시문이 막아야 하는 오류
        decision = self.ask_then("의료비 예산 남은 거 알려줘", "쇼핑은?")
        self.assertEqual(decision.items, (item(3, "FASHION_SHOPPING"),))
        self.assertIsNone(decision.interpretation.target)

    def test_context_survives_pending_and_changes_only_on_success(self):
        self.done(self.say("최근 2개월 식비 어때?"))
        saved = self.store.get_analysis("talk-1")
        self.say("최근 0개월 식비 알려줘")
        decision = self.say("2개월")  # 되묻기 완료 → 대기 상태만 해제
        self.assertIsNone(dialog.pending(self.store, "talk-1"))
        self.assertEqual(self.store.get_analysis("talk-1"), saved)  # ready 만으로는 기록하지 않음
        self.done(decision, fail=("FOOD",))  # 조회 전부 실패 → 직전 성공 유지
        self.assertEqual(self.store.get_analysis("talk-1"), saved)
        self.done(self.say("지난달보다 식비 더 썼어?"))
        self.assertEqual(self.store.get_analysis("talk-1")["request"]["compare"], "prev_month")

    def test_partial_success_keeps_ok_and_failed_apart(self):
        self.done(self.say("최근 2개월 식비랑 교통비 알려줘"), fail=("TRANSPORT_CAR",))
        items = self.store.get_analysis("talk-1")["items"]
        self.assertEqual([(i["category"], i["ok"]) for i in items], [("FOOD", True), ("TRANSPORT_CAR", False)])
        self.assertEqual((items[0]["period"], items[0]["as_of"]), (PERIOD, "2026-10-29T10:00:00+09:00"))
        self.assertNotIn("period", items[1])  # 실패 항목은 결과 없음
        # 실패한 항목을 설명 대상으로 참조하지 않는다
        self.assertEqual(self.say("교통비는 왜 늘었어?").reason, "reference_not_available")
        decision = self.say("왜 늘었어?")  # 성공한 결과가 하나뿐 → 그것
        self.assertEqual([i["category"] for i in decision.explain["items"]], ["FOOD"])

    def test_explain_needs_confirmed_evidence(self):
        """ok(조회 끝남)만으로는 설명 근거가 아니다: 부분 자료 · evidence 없음은 evidence_incomplete"""
        for result in ({"evidence": "limited", "limits": ["partial"], "period": PERIOD, "as_of": "t"},
                       {"period": PERIOD, "as_of": "t"}):
            with self.subTest(result=result):
                self.store = MemoryStateStore()
                decision = self.say("최근 2개월 식비 어때?")
                record_analysis(self.store, "talk-1", decision, [{"item": decision.items[0], "ok": True, "result": result}])
                self.assertEqual(self.store.get_analysis("talk-1")["request"]["categories"], ["FOOD"])  # 조건은 남김
                self.assertEqual(self.say("왜 늘었어?").reason, "evidence_incomplete")

    def test_explain_refers_to_previous_result_not_new_query(self):
        decision = self.ask_then("지난달보다 식비 더 썼어?", "왜 늘었어?")
        self.assertEqual((decision.status, decision.intent, decision.items), ("ready", "spending", ()))  # 정형 조회로 안 바꿈
        self.assertEqual(decision.explain["question"], "왜 늘었어?")
        self.assertEqual(decision.explain["items"][0]["period"], PERIOD)  # 그 결과의 기간 · 조회 시점을 참조
        self.assertEqual(decision.explain["items"][0]["as_of"], "2026-10-29T10:00:00+09:00")

    def test_ambiguous_explain_target_is_asked(self):
        self.done(self.say("최근 2개월 식비랑 교통비 알려줘"))
        decision = self.say("왜 늘었어?")
        self.assertEqual((decision.status, decision.asking, decision.ask_about),
                         ("ask", "explain_target", ("FOOD", "TRANSPORT_CAR")))
        self.assertEqual(self.say("응").asking, "explain_target")
        decision = self.say("교통·차량")  # 목록에서 고른 이름
        self.assertEqual([i["category"] for i in decision.explain["items"]], ["TRANSPORT_CAR"])

    def test_explain_without_previous_and_unclear_reference_are_asked(self):
        self.assertEqual(self.say("왜 늘었어?").reason, "no_previous_analysis")
        self.done(self.say("최근 2개월 식비 어때?"))
        decision = self.say("아까 그거")
        self.assertEqual((decision.status, decision.asking, decision.reason), ("ask", "scope", "reference_unclear"))

    def test_conversations_keep_their_own_context(self):
        self.done(self.say("최근 2개월 식비 어때?", talk="talk-1"), talk="talk-1")
        self.done(self.say("지난달보다 식비 더 썼어?", talk="talk-2"), talk="talk-2")
        self.assertEqual(self.say("그럼 교통비는?", talk="talk-1").items[0]["type"], 9)
        self.assertEqual(self.say("그럼 교통비는?", talk="talk-2").items[0]["type"], 7)
        self.assertEqual(self.say("그럼 교통비는?", talk="talk-3").asking, "scope")

    def test_failures_keep_pending_and_context(self):
        self.done(self.say("최근 2개월 식비 어때?"))
        self.say("최근 0개월 식비 알려줘")
        state = (dialog.pending(self.store, "talk-1"), self.store.get_analysis("talk-1"))
        for text, llm in [("그럼 교통비는?", RoleFakeLLM(broken=True)), ("JSON 아님", None), ("설명 형식 틀림", None)]:
            with self.subTest(text):
                self.assertEqual(self.say(text, llm=llm).status, "failed")
                self.assertEqual((dialog.pending(self.store, "talk-1"), self.store.get_analysis("talk-1")), state)

    def test_previous_conditions_sent_to_llm_without_results(self):
        self.ask_then("최근 2개월 식비 어때?", "그럼 교통비는?")
        self.assertEqual(self.llm.calls[-1]["previous"], {"target": "overview", "period": "recent", "compare": None,
                                                           "months": 2, "categories": ["FOOD"]})

    def test_switch_to_all_categories(self):
        cases = [
            ("식비 얼마 썼어?", "그럼 전체는?", (item(1),)),
            ("지난달보다 식비 더 썼어?", "전체 소비는?", (item(6),)),
            ("최근 2개월 식비 어때?", "전체는?", (item(8, months_requested=2, months_applied=2),)),
            ("식비 예산 얼마 남았어?", "전체 예산은?", (item(4),)),
            # 카테고리를 말하지 않았을 뿐인 후속 질문은 카테고리를 이어받는다
            ("최근 2개월 식비 어때?", "그 기간 전체로 보면?", (item(9, "FOOD", months_requested=2, months_applied=2),)),
        ]
        for first, then, items in cases:
            with self.subTest(then):
                self.store = MemoryStateStore()
                self.assertEqual(self.ask_then(first, then).items, items)

    def test_months_only_follow_ups(self):
        cases = [
            ("최근 2개월 식비 어때?", "그럼 3개월은?", (item(9, "FOOD", months_requested=3, months_applied=3),), ()),
            ("최근 2개월 식비 어때?", "6개월로 보면?", (item(9, "FOOD", months_requested=6, months_applied=3),),
             ("months_capped",)),
            ("최근 2개월 식비 어때?", "이번 달은?", (item(2, "FOOD"),), ()),
            ("지난달보다 식비 더 썼어?", "최근 2개월로 봐줘", (item(9, "FOOD", months_requested=2, months_applied=2),), ()),
        ]
        for first, then, items, notices in cases:
            with self.subTest(then):
                self.store = MemoryStateStore()
                decision = self.ask_then(first, then)
                self.assertEqual((decision.items, decision.notices), (items, notices))
        self.assertEqual(decision.interpretation.months, 2)

    def test_conflicting_explicit_conditions_are_asked(self):
        # 지난달 대비 + 최근 N개월 = 달끼리 비교하려는 뜻은 같고 이번 달 포함 여부만 모호 → 포함 여부를 묻는다
        decision = self.ask_then("최근 2개월 식비 어때?", "지난달보다 최근 2개월은?")
        self.assertEqual((decision.status, decision.asking, decision.reason), ("ask", "include_choice", "include_unclear"))
        self.assertEqual([o["label"] for o in decision.options], ["완료된 2개월", "이번 달 포함 2개월"])
        decision = self.say("2개월")  # 포함 여부를 말하지 않았다 → 임의로 고르지 않고 다시 묻는다
        self.assertEqual((decision.asking, [o["label"] for o in decision.options]),
                         ("include_choice", ["완료된 2개월", "이번 달 포함 2개월"]))
        decision = self.say("완료된 2개월")  # 이어받은 식비는 유지
        self.assertEqual(decision.items, (item(9, "FOOD", months_requested=2, months_applied=2),))
        for text, asking in [("이번 달 3개월 식비", "period_choice"), ("전체랑 식비", "category_choice")]:  # 새 요청에서도 같은 규칙
            with self.subTest(text):
                decision = self.say(text)
                self.assertEqual((decision.asking, decision.reason), (asking, "conflicting_conditions"))
                self.store.save_agent_state("talk-1", "spending", None)

    def test_conflict_choice_keeps_other_conditions(self):
        """선택지 이름을 고르면 LLM 없이 그 묶음만 바꾸고, 카테고리 · 대상은 그대로 둔다"""
        for label, items in [("이번 달", (item(2, "FOOD"),)),
                             ("최근 3개월", (item(9, "FOOD", months_requested=3, months_applied=3),))]:
            with self.subTest(label):
                self.store = MemoryStateStore()
                decision = self.say("이번 달 3개월 식비")
                self.assertEqual([o["label"] for o in decision.options], ["이번 달", "최근 3개월"])
                calls = len(self.llm.calls)
                decision = self.say(label)
                self.assertEqual((decision.status, decision.items, len(self.llm.calls)), ("ready", items, calls))
                self.assertIsNone(dialog.pending(self.store, "talk-1"))
        self.say("전체랑 식비")
        self.assertEqual(self.say("전체").items, (item(5),))
        self.say("전체랑 식비")
        self.assertEqual(self.say("식비").items, (item(2, "FOOD"),))  # 카테고리 이름 = 선택지 이름

    def test_this_month_compared_with_last_month_is_not_a_conflict(self):
        said = {"이번 달 지난달보다 더 썼어?": {"mine": True, "target": "spend", "period": "this_month", "compare": "prev_month"},
                "지난달 2개월 교통비": {"mine": True, "period": "last_month", "months": 2, "categories": ["TRANSPORT_CAR"]}}
        llm = lambda system, user: json.dumps(said[json.loads(user)["text"]])  # noqa: E731
        self.assertEqual(self.say("이번 달 지난달보다 더 썼어?", llm=llm).items, (item(6),))
        decision = self.say("지난달 2개월 교통비", llm=llm)
        self.assertEqual([o["label"] for o in decision.options], ["지난달", "최근 2개월"])
        self.assertEqual(self.say("지난달").reason, "last_month_only")  # 고른 뒤에는 원래 규칙 (지난달 단독 미지원)


    def test_earlier_analysis_is_not_linked_to_previous(self):
        self.done(self.say("최근 2개월 식비 어때?"))
        self.done(self.say("지난달보다 식비 더 썼어?"))
        decision = self.say("그 전에 본 거 다시")  # 직전(지난달 대비)으로 임의 연결하지 않고 묻는다
        self.assertEqual((decision.status, decision.asking, decision.reason, decision.items),
                         ("ask", "scope", "reference_unclear", ()))

    def test_bad_all_categories_value_fails_and_keeps_state(self):
        self.done(self.say("최근 2개월 식비 어때?"))
        saved = self.store.get_analysis("talk-1")
        self.assertEqual(self.say("전체 형식 틀림").status, "failed")
        self.assertEqual(self.store.get_analysis("talk-1"), saved)


class PendingAmountInputTest(SimpleTestCase):
    """소비 되묻기(개월 수) 중 금액이 들어간 입력 — 소비 해석 결과(역할 형식 그대로)에 따라 갈린다.
    소비 해석에 금액 칸이 없다는 이유로 금액 문장을 모두 같은 질문 반복으로 처리하지 않는다"""
    READINGS = {
        "최근 0개월 식비 알려줘": {"mine": True, "period": "recent", "months": 0, "categories": ["FOOD"]},
        "5만 원만큼": {"mine": True, "followup": "unclear"},                     # 맥락상 불분명한 답
        "예산을 5만 원 줄여줘": {"mine": False},                                  # 명확한 다른 분야 요청
        "지난달보다 5만 원 더 썼어?": {"mine": True, "followup": "new", "compare": "prev_month", "target": "spend"},
        "5만 원으로": {"mine": True, "followup": "answer", "amount": 50000},      # 소비 형식에 없는 칸 = 형식 오류
    }

    def setUp(self):
        self.store = MemoryStateStore()
        dialog.step(self.store, "t", "최근 0개월 식비 알려줘", self.llm)
        self.asked = dialog.pending(self.store, "t")

    def llm(self, system, user):
        return json.dumps(self.READINGS[json.loads(user)["text"]], ensure_ascii=False)

    def say(self, text):
        before = self.store.export("t")
        return dialog.step(self.store, "t", text, self.llm), before

    def test_unclear_amount_answer_keeps_question(self):
        decision, before = self.say("5만 원만큼")
        self.assertEqual((decision.status, decision.asking), ("ask", "months"))
        self.assertEqual(self.store.export("t"), before)

    def test_clear_other_domain_request_is_not_mine(self):
        decision, before = self.say("예산을 5만 원 줄여줘")
        self.assertEqual((decision.status, self.store.export("t")), ("not_mine", before))  # 다시 묻지 않고 라우터로

    def test_spending_request_with_amount_is_new_request(self):
        decision, _ = self.say("지난달보다 5만 원 더 썼어?")
        self.assertEqual((decision.status, decision.items), ("ready", (item(6),)))
        self.assertIsNone(dialog.pending(self.store, "t"))  # 새 요청이 질문을 대신했다 (같은 질문 반복 아님)

    def test_format_error_is_failed(self):
        decision, before = self.say("5만 원으로")
        self.assertEqual((decision.status, self.store.export("t")), ("failed", before))


class AliasTest(SimpleTestCase):
    """대화 안 카테고리 별칭: 사용자가 카테고리 질문에 직접 고른 대응만 이 대화에서 다시 쓴다"""
    SAID = {
        "데이트비 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"unknown": "데이트비"}]},
        "데이트비 예산 얼마 남았어?": {"mine": True, "target": "budget_left", "categories": [{"unknown": "데이트비"}]},
        "데이트비랑 식비 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"unknown": "데이트비"}, "FOOD"]},
        "데이트비 예산 줄여줘": {"mine": False},
        "여가 쪽이야": {"mine": True, "followup": "answer", "categories": ["CULTURE_LEISURE"]},
    }

    def setUp(self):
        self.store, self.calls = MemoryStateStore(), 0

    def llm(self, system, user):
        self.calls += 1
        return json.dumps(self.SAID[json.loads(user)["text"]], ensure_ascii=False)

    def say(self, text, talk="talk-1"):
        return dialog.step(self.store, talk, text, self.llm)

    def test_picked_category_is_remembered_and_reused(self):
        self.assertEqual(self.say("데이트비 얼마 썼어?").asking, "category")
        decision = self.say("문화·여가")
        self.assertEqual((decision.items, decision.aliases),
                         ((item(2, "CULTURE_LEISURE"),), ({"word": "데이트비", "code": "CULTURE_LEISURE", "new": True},)))
        decision = self.say("데이트비 예산 얼마 남았어?")  # 남은 예산 조회에도 다시 묻지 않고 쓴다
        self.assertEqual((decision.items, decision.aliases[0]["new"]), ((item(3, "CULTURE_LEISURE"),), False))
        self.assertEqual(self.say("데이트비랑 식비 얼마 썼어?").items, (item(2, "CULTURE_LEISURE"), item(2, "FOOD")))
        self.assertEqual(self.say("데이트비 예산 줄여줘").status, "not_mine")  # 다른 분야 요청을 소비로 끌어오지 않는다
        self.assertEqual(self.say("데이트비 얼마 썼어?", talk="talk-2").asking, "category")  # 다른 대화와 분리

    def test_repick_changes_the_alias(self):
        self.say("데이트비 얼마 썼어?")
        self.say("문화·여가")
        self.say("데이트비 예산 얼마 남았어?")
        calls = self.calls
        decision = self.say(dialog.REPICK.format(word="데이트비"))
        self.assertEqual((decision.asking, decision.ask_about, self.calls), ("category", ("데이트비",), calls))
        self.assertNotIn("UNCLASSIFIED", [o["code"] for o in decision.options])  # 남은 예산 질문이었다
        self.assertEqual(self.say("식비").items, (item(3, "FOOD"),))
        self.assertEqual(self.say("데이트비 얼마 썼어?").items, (item(2, "FOOD"),))

    def test_alias_to_unclassified_keeps_budget_exclusion(self):
        self.say("데이트비 얼마 썼어?")
        self.assertEqual(self.say("확인 필요").items, (item(2, "UNCLASSIFIED"),))  # 소비 조회는 지원
        decision = self.say("데이트비 예산 얼마 남았어?")
        self.assertEqual((decision.status, decision.reason), ("unsupported", "no_budget_category"))

    def test_interpreted_answer_is_not_remembered_and_cancel_keeps_aliases(self):
        self.say("데이트비 얼마 썼어?")
        self.assertEqual(self.say("여가 쪽이야").items, (item(2, "CULTURE_LEISURE"),))  # 해석한 답은 이번 조회에만
        self.assertEqual(self.say("데이트비 얼마 썼어?").asking, "category")
        self.say("문화·여가")
        dialog.cancel_question(self.store, "talk-1")
        self.assertEqual(self.say("데이트비 얼마 썼어?").items, (item(2, "CULTURE_LEISURE"),))


class UserTestScenarioTest(SimpleTestCase):
    """로컬 사용자 테스트 재현 · 변형 (소비 형식 해석 대역). 원문 → 해석(대역이 정한 추출값) → 적용 조건을 나눠 본다"""
    SAID = {
        # 1. 기간 정정
        "이번 달 3개월 식비 궁금해": {"mine": True, "period": "this_month", "months": 3, "categories": ["FOOD"]},
        "아니다 이번 달 2개월 식비가 궁금해": {"mine": True, "followup": "answer", "period": "this_month", "months": 2,
                                  "categories": ["FOOD"]},
        "아니 이번 달하고 2개월": {"mine": True, "followup": "answer", "period": "this_month", "months": 2},
        "아니다 이번 달만": {"mine": True, "followup": "answer", "period": "this_month"},
        "아니 그냥 지난달": {"mine": True, "followup": "answer", "period": "last_month"},
        # 2. 비교 표현
        "지난달보다 병원비 얼마나 썼어?": {"mine": True, "target": "spend", "compare": "prev_month",
                               "categories": ["MEDICAL_HEALTH"]},
        "지난달 대비 교통비 늘었어?": {"mine": True, "target": "spend", "compare": "prev_month",
                             "categories": ["TRANSPORT_CAR"]},
        "지난달 병원비를 지난달 대비로": {"mine": True, "period": "last_month", "compare": "prev_month",
                              "categories": ["MEDICAL_HEALTH"]},
        # 3. 이번 달 포함
        "이번 달 포함 최근 3개월 식비 어때?": {"mine": True, "target": "overview", "period": "recent", "months": 3,
                                  "include_current": True, "categories": ["FOOD"]},
        "이번 달까지 6개월 식비": {"mine": True, "months": 6, "include_current": True, "categories": ["FOOD"]},
        "지난달 대비 최근 3개월로 비교해줘": {"mine": True, "target": "spend", "compare": "prev_month", "period": "recent",
                                 "months": 3},
        "이번 달도 넣어줘": {"mine": True, "followup": "answer", "include_current": True},
        # 4. 세부 지출
        "커피 얼마나 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "커피", "category": "FOOD"}]},
        "지난달보다 커피 더 썼어?": {"mine": True, "target": "spend", "compare": "prev_month",
                             "categories": [{"detail": "커피", "category": "FOOD"}]},
        "편의점 얼마나 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "편의점", "category": None}]},
        "응 그렇게 해": {"mine": True, "followup": "answer", "confirm": True},
        # 5. 별칭 변경
        "데이트비 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"unknown": "데이트비"}]},
        "데이트비는 식비로 볼게": {"mine": True, "alias": {"word": "데이트비", "category": "FOOD"}},
        "앞으로 데이트비는 식비로 쳐줘": {"mine": True, "alias": {"word": "데이트비", "category": "FOOD"}},
        "데이트비는 식비로 보고 이번 달 얼마야?": {"mine": True, "target": "spend", "period": "this_month",
                                     "categories": [{"unknown": "데이트비"}],
                                     "alias": {"word": "데이트비", "category": "FOOD"}},
    }

    def setUp(self):
        self.store, self.calls = MemoryStateStore(), 0

    def llm(self, system, user):
        self.calls += 1
        return json.dumps(self.SAID[json.loads(user)["text"]], ensure_ascii=False)

    def say(self, text, talk="talk-1"):
        return dialog.step(self.store, talk, text, self.llm)

    def labels(self, decision):
        return [o["label"] for o in decision.options]

    def test_1_corrected_period_updates_question_and_keeps_category(self):
        self.assertEqual(self.labels(self.say("이번 달 3개월 식비 궁금해")), ["이번 달", "최근 3개월"])
        for said in ("아니다 이번 달 2개월 식비가 궁금해", "아니 이번 달하고 2개월"):  # 카테고리를 다시 말하지 않아도 식비 유지
            with self.subTest(said):
                decision = self.say(said)
                self.assertEqual((decision.asking, self.labels(decision)), ("period_choice", ["이번 달", "최근 2개월"]))
                self.assertEqual(tuple(dialog.pending(self.store, "talk-1")["request"]["categories"]), ("FOOD",))
        self.assertEqual(self.say("최근 2개월").items, (item(9, "FOOD", months_requested=2, months_applied=2),))

    def test_1_resolving_correction_runs_at_once(self):
        self.say("이번 달 3개월 식비 궁금해")
        self.assertEqual(self.say("아니다 이번 달만").items, (item(2, "FOOD"),))
        self.say("이번 달 3개월 식비 궁금해")
        self.assertEqual(self.say("아니 그냥 지난달").reason, "last_month_only")  # 고친 조건 그대로 규칙 적용 (임의 대체 없음)

    def test_2_compared_to_last_month_is_not_last_month_alone(self):
        self.assertEqual(self.say("지난달보다 병원비 얼마나 썼어?").items, (item(7, "MEDICAL_HEALTH"),))
        self.assertEqual(self.say("지난달 대비 교통비 늘었어?").items, (item(7, "TRANSPORT_CAR"),))
        # 해석이 지난달 단독 + 비교를 함께 냈으면(추출 오류일 수 있음) 코드가 덮지 않고 되묻는다
        self.assertEqual(self.labels(self.say("지난달 병원비를 지난달 대비로")), ["지난달", "지난달과 비교"])

    def test_3_include_current_is_separate_from_recent(self):
        self.assertEqual(self.say("이번 달 포함 최근 3개월 식비 어때?").items,
                         (item(9, "FOOD", months_requested=3, months_applied=3, include_current=True),))
        decision = self.say("이번 달까지 6개월 식비")  # 원래 개월 수 보존 · 최대 3개월
        self.assertEqual((decision.items, decision.notices),
                         ((item(9, "FOOD", months_requested=6, months_applied=3, include_current=True),), ("months_capped",)))
        self.assertTrue(self.store.get_analysis("talk-1") is None)  # (분석 실행 전: 기록 없음)

    def test_3_ambiguous_inclusion_is_asked(self):
        decision = self.say("지난달 대비 최근 3개월로 비교해줘")
        self.assertEqual((decision.asking, self.labels(decision)), ("include_choice", ["완료된 3개월", "이번 달 포함 3개월"]))
        self.assertEqual(self.say("이번 달도 넣어줘").items, (item(8, months_requested=3, months_applied=3, include_current=True),))
        self.say("지난달 대비 최근 3개월로 비교해줘")
        self.assertEqual(self.say("완료된 3개월").items, (item(8, months_requested=3, months_applied=3),))

    def test_4_detail_needs_consent_and_is_not_an_alias(self):
        decision = self.say("커피 얼마나 썼어?")
        self.assertEqual((decision.status, decision.asking, decision.items), ("ask", "fallback_scope", ()))
        self.assertEqual(self.labels(decision), ["식비 전체 보기", "취소"])
        calls = self.calls
        self.assertEqual(self.say("식비 전체 보기").items, (item(2, "FOOD", instead_of="커피"),))
        self.assertEqual(self.calls, calls)  # 선택지 이름 = LLM 없이
        self.assertNotIn("aliases", self.store.get_agent_state("talk-1", "spending") or {})  # 커피=식비 를 기억하지 않는다
        self.say("커피 얼마나 썼어?")
        decision = self.say("취소")  # 선택지 [취소] = 대체 범위 거절 (라우터에서는 취소 단어로 먼저 처리된다)
        self.assertEqual((decision.status, decision.reason, decision.items), ("cancelled", "declined", ()))
        self.say("커피 얼마나 썼어?")
        self.assertEqual(self.say("응 그렇게 해").items, (item(2, "FOOD", instead_of="커피"),))  # 해석한 동의도 같다

    def test_4_keeps_period_and_asks_when_scope_unclear(self):
        self.say("지난달보다 커피 더 썼어?")
        self.assertEqual(self.say("식비 전체 보기").items, (item(7, "FOOD", instead_of="커피"),))
        decision = self.say("편의점 얼마나 썼어?")  # 속한 카테고리가 분명하지 않음 → 고르게 한다
        self.assertEqual((decision.asking, len(decision.options)), ("fallback_category", 12))
        self.assertEqual(self.say("생활용품").items, (item(2, "HOUSEHOLD", instead_of="편의점"),))

    def test_5_explicit_alias_change_is_spending_and_replaces_old(self):
        self.say("데이트비 얼마 썼어?")
        self.say("문화·여가")
        for said in ("데이트비는 식비로 볼게", "앞으로 데이트비는 식비로 쳐줘"):
            with self.subTest(said):
                decision = self.say(said)
                self.assertEqual((decision.status, decision.reason, decision.aliases),
                                 ("noted", "alias_set", ({"word": "데이트비", "code": "FOOD", "new": True},)))
        self.assertEqual(self.say("데이트비 얼마 썼어?").items, (item(2, "FOOD"),))  # 옛 대응(문화·여가)을 쓰지 않는다
        self.assertEqual(self.say("데이트비 얼마 썼어?", talk="talk-2").asking, "category")  # 대화별 분리
        decision = self.say("데이트비는 식비로 보고 이번 달 얼마야?", talk="talk-3")  # 기준 + 조회를 함께
        self.assertEqual(decision.items, (item(2, "FOOD"),))

    def test_5_alias_statement_keeps_pending_question(self):
        self.say("커피 얼마나 썼어?")
        self.assertEqual(self.say("데이트비는 식비로 볼게").status, "noted")
        self.assertEqual(dialog.pending(self.store, "talk-1")["asking"], "fallback_scope")  # 묻던 질문은 그대로
