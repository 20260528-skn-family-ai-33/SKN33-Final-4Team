"""Model-reference arithmetic and missing data must not become invented peer facts."""
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock
from datetime import date

from django.test import SimpleTestCase

from . import benchmark, insight, lifestyle, reasoning, narrative
import json
from .reading import parse
from .test_insight import decision
from .test_reasoning import analyze, month, row


class BenchmarkTest(SimpleTestCase):
    def setUp(self):
        self.prediction = dict(amounts=dict.fromkeys(benchmark.MODEL_CODES.values(), 100000),
                               category_mae=dict.fromkeys(benchmark.MODEL_CODES.values(), 20000),
                               total_mae=100000, samples=2635, model='v13_model_c')
        self.profile = dict(income=3000000, fixed=700000, savings_goal=1000000)
        self.summary = {'reference_month': {'label': '10월 전체', 'categories': {'FOOD': 180000}},
                        'total_forecast': {'state': 'ready', 'month_end': 2400000}}

    def test_reference_is_full_month_and_savings_are_conditional_room(self):
        lines, notes = benchmark.compare(self.summary, self.prediction, self.profile)
        text = ' '.join(lines)
        self.assertIn('10월 전체 식비 지출은 180,000원', text)
        self.assertIn('기준 약 100,000원보다 80,000원 높아요', text)
        self.assertIn('600,000원이 남아요', text)
        self.assertIn('400,000원 줄여야', text)
        self.assertIn('600,000원 적은 전망', text)
        self.assertIn('실제 저축액이 아니', ' '.join(notes))
        self.assertIn('실제 비슷한 사람들의 평균', ' '.join(notes))

    def test_month_to_date_and_model_errors_are_not_spending_differences(self):
        lines, notes = benchmark.compare({'current': {'amount': 900000}}, self.prediction, self.profile)
        self.assertFalse(lines)
        self.assertIn('한 달 전체', notes[0])
        self.summary['reference_month']['categories']['FOOD'] = 110000
        lines, _ = benchmark.compare(self.summary, self.prediction, self.profile)
        self.assertNotIn('높아요', ' '.join(lines))
        self.assertNotIn('20,000원', ' '.join(lines))

    def test_unknown_classification_never_proves_lower_spending(self):
        self.summary['reference_month'].update(categories={'FOOD': 10000}, unclassified=200000)
        lines, notes = benchmark.compare(self.summary, self.prediction, self.profile)
        self.assertNotIn('낮아요', ' '.join(lines))
        self.assertIn('적게 쓴다는 판단은 하지', ' '.join(notes))

    def test_failed_or_missing_profile_is_only_a_benchmark_limit(self):
        lines, notes = benchmark.for_user(object(), {}, date(2026, 11, 13))
        self.assertFalse(lines)
        self.assertIn('소비 패턴과 추이는 그대로', notes[0])

    def test_invalid_subprocess_output_is_not_used(self):
        benchmark._predict.cache_clear()
        with mock.patch.object(benchmark.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"amounts": {}}')):
            with self.assertRaises(ValueError):
                benchmark._predict('{}', 'python', 'model')

    def test_missing_ml_demographics_does_not_hide_known_saving_room(self):
        user = SimpleNamespace(financial_profile=SimpleNamespace(birth_date=None, gender=None, has_car=None,
                               monthly_take_home_income=3000000, savings_floor=1000000))
        lines, notes = benchmark.for_user(user, self.summary, date(2026, 11, 13))
        self.assertIn('600,000원이 남아요', ' '.join(lines))
        self.assertIn('ML 비교에 필요한', ' '.join(notes))

    def test_zero_spending_is_not_promoted_as_a_low_spending_strength(self):
        self.summary['reference_month']['categories'] = {'FOOD': 0}
        lines, _ = benchmark.compare(self.summary, self.prediction, self.profile)
        self.assertNotIn('0원으로', ' '.join(lines))


class HabitAcceptanceTest(SimpleTestCase):
    def test_same_count_and_total_produce_different_interpretations(self):
        def observed(amounts):
            return reasoning.observed_pattern([{**row(date(2026, 11, d), amount, activity=None), 'occurred_time': None}
                for d, amount in zip((2, 3, 4, 5, 6, 7, 7), amounts)])
        spread = observed((15000, 16000, 15500, 17000, 16000, 16000, 16000))
        concentrated = observed((80000, 10000, 6500, 5000, 4000, 3000, 3000))
        self.assertIsNone(spread)  # 고르게 나눠 쓴 건 발견이 아니다 — '여러 날 나눠 쓴 돈이 쌓이는 형태' 같은 당연한 말을 만들지 않는다
        self.assertEqual(concentrated['count'], 7)
        self.assertIn('두 결제의 규모', concentrated['conclusion'])
        self.assertIn('90,000원', concentrated['evidence'])
        self.assertNotIn('일회성', concentrated['conclusion'])

    def test_sparse_transactions_and_refunds_do_not_establish_a_spending_style(self):
        rows = [{**r, 'occurred_time': None} for r in month(11, activity=None, days=4)]
        self.assertIsNone(reasoning.observed_pattern(rows))
        self.assertIsNone(reasoning.observed_pattern(rows + [row(date(2026, 11, 5), -5000)]))

    def test_time_total_includes_known_and_unknown_industries_without_hiding_known_majority(self):
        rows = month(11, activity=None, days=8)
        rows[0]['activity'] = 'dining'
        observed = reasoning.observed_pattern(rows)
        self.assertEqual(observed['amount'], 80000)
        self.assertIn('80,000원 전부가 점심 식비', observed['evidence'])
        for r in rows[:6]:
            r['activity'] = 'dining'
        observed = reasoning.observed_pattern(rows)
        self.assertEqual(observed['activity'], 'dining')
        self.assertEqual(observed['amount'], 60000)

    def test_habit_goal_cannot_silently_become_category_sum(self):
        reading = parse({'mine': True, 'target': 'spend', 'analysis_goal': 'habit', 'measure': 'sum', 'group_by': 'category'})
        self.assertEqual(reading.target, 'overview')
        self.assertIsNone(reading.measure)
        self.assertIsNone(reading.group_by)
        self.assertTrue(insight.eligible(decision(analysis_goal='habit', include_fixed=True)))

    def test_pattern_interpretation_not_amount_inventory(self):
        pattern = reasoning.observed_pattern(month(11, days=8))
        self.assertIn('점심 외식 중심으로', pattern['conclusion'])
        self.assertEqual((pattern['dates'], pattern['count'], pattern['average']), (8, 8, 10000))
        self.assertIn('80,000원 전부가 점심 외식', pattern['evidence'])
        self.assertNotIn('8건', pattern['evidence'])
        self.assertNotIn('식사 횟수', pattern['evidence'])

    def test_unknown_activity_cannot_become_dining_or_action(self):
        pattern = reasoning.observed_pattern(month(11, activity=None, days=8))
        self.assertIn('점심 시간대에 지출이 몰려', pattern['conclusion'])
        self.assertNotIn('외식', pattern['conclusion'])
        self.assertEqual(lifestyle.suggestions([dict(pattern=pattern, kind='observed')]), [])

    def test_actions_address_verified_pattern_and_never_cut_medical_or_large_purchase(self):
        dining = dict(pattern=reasoning.observed_pattern(month(11, days=8)), kind='payment_days', material={'meaningful': True}, regular_delta=80000)
        self.assertIn('주 1회', lifestyle.suggestions([dining])[0])
        self.assertIn('도시락', lifestyle.suggestions([dining])[0])
        self.assertEqual(lifestyle.suggestions([{**dining, 'kind': 'large_payment'}]), [])
        self.assertEqual(lifestyle.suggestions([{**dining, 'material': {'meaningful': False}}]), [])
        medical = dict(pattern=reasoning.observed_pattern(month(11, category='MEDICAL_HEALTH', activity=None, days=8)), kind='payment_days')
        self.assertEqual(lifestyle.suggestions([medical]), [])

    def test_1000_small_perturbations_do_not_become_meaningful_changes(self):
        # Whole-month bases vary by 100x; even monotone noise is not a material trend.
        for base in (10000, 50000, 100000, 500000, 1000000):
            for delta in range(-9950, 10000, 100):
                with self.subTest(base=base, delta=delta):
                    self.assertFalse(reasoning.material(delta, [base] * 6, base)['meaningful'])

    def test_unchanged_habit_survives_but_small_growth_never_dominates_it(self):
        result = analyze(month(11, 10001), month(10))
        self.assertEqual(result['findings'], [])
        self.assertIn('외식 중심으로', result['candidates'][0]['habit'])
        self.assertNotIn('증가', result['candidates'][0]['outlook'])

    def test_actual_llm_scope_error_is_blocked_even_when_semantic_checker_accepts(self):
        pattern = reasoning.observed_pattern([row(date(2026, 11, d), 20000) for d in (2, 3, 4, 5, 9, 10, 11, 12)])
        # 과거의 건수 근거를 가진 저장 응답도 대상 범위 검사를 유지한다.
        pattern.update(evidence='확인된 평일 점심 외식 결제는 8일에 걸쳐 8건이고, 한 번에 평균 20,000원을 썼어요.',
                       metric_scope=['평일', '점심', '외식'])
        finding = dict(id='F1', kind='type_mix', category='FOOD', conclusion=pattern['conclusion'],
                       evidence=[pattern['evidence']], outlook='', limits=[], pattern=pattern)
        data = narrative.payload('내 소비 습관 어때?', [finding], None, 'habit')
        # Verbatim failed evidence from the approved 2026-11-13 live run: dining subset became all food.
        raw = {'diagnosis': {'text': '평일 점심 외식 결제가 반복되는 편이에요.', 'findings': ['F1']},
               'evidence': [{'text': '확인된 식비 결제는 {{V1}}일에 걸쳐 {{V2}}건이고, 한 번에 평균 {{V3}}원을 썼어요.', 'findings': ['F1']}], 'outlook': None}
        self.assertIsNone(narrative.draft(json.dumps(raw), data))
        raw['evidence'][0]['text'] = '평일 점심 외식이 반복돼요. 확인된 식비 결제는 {{V1}}일에 걸쳐 {{V2}}건이고, 한 번에 평균 {{V3}}원을 썼어요.'
        self.assertIsNone(narrative.draft(json.dumps(raw), data))
        raw['evidence'][0]['text'] = '확인된 평일 점심 외식 결제는 {{V1}}일에 걸쳐 {{V2}}건이고, 한 번에 평균 {{V3}}원을 썼어요.'
        self.assertIsNotNone(narrative.draft(json.dumps(raw), data))
