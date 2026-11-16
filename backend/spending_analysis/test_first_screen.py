"""소비 분석 첫 화면 (11/16 김혜리 요구사항): 조회가 아니라 분석 · 적은 내역도 분석 · 그래프는 내용에 맞는 것만.
첫 카드만 읽어도 무엇이 달라졌고 어디에 몰렸는지 알 수 있어야 하고, 총론 한 문장이나 월별 막대만으로 끝나면 안 된다."""
from datetime import date
from unittest import mock

from django.test import SimpleTestCase

from . import insight, reasoning

TODAY = date(2026, 10, 29)


def category(code, name, amount, delta=0):
    return {"code": code, "name": name, "current": {"amount": amount}, "previous": {"amount": amount - delta}, "delta": delta}


def summary(**extra):
    base = {"findings": [], "snapshot": [], "observation": [], "candidates": [], "trends": {}, "overall_trend": {},
            "basis": {"comparison_available": True}, "categories": [
                category("FOOD", "식비", 390000, 42000), category("CULTURE_LEISURE", "문화·여가", 90000, 20000),
                category("MEDICAL_HEALTH", "의료·건강", 20000, -30000)]}
    return {**base, **extra}


def story(**findings):
    trend = findings.get("trend", "trend_flat")
    by_id = {"TREND": {"id": "TREND", "kind": trend, "conclusion": "지난 3개월 한 달 생활비는 72만 원~78만 원으로 꾸준했어요.",
                       "evidence": ["7월 72만 원 → 8월 76만 원 → 9월 78만 원."]},
             "FORECAST": {"id": "FORECAST", "kind": "forecast", "conclusion": "이 흐름이면 이번 달 생활비는 약 86만 원이 될 거예요.",
                          "evidence": ["이번 달 말 약 86만 원 (지금까지 81만 원)", "다음 달(11월)도 지금처럼 쓰면 약 82만 원이에요."]}}
    st = {"findings": list(by_id.values()), "chart": {"kind": "months", "title": "월별 생활비"}, "actions": [], "slot": None, "weekend": None}
    return by_id, st


OFFSET = {"id": "OFFSET", "kind": "offset", "comparable": True, "quiet": False,
          "conclusion": "총액은 지난달 같은 기간과 비슷하지만 식비(+4.2만 원)에서 늘고 의료·건강(-3만 원)에서 줄었어요.",
          "evidence": ["식비 +42,000원 · 의료·건강 -30,000원"]}


class FirstScreenTest(SimpleTestCase):
    def overview(self, summary_, trend="trend_flat"):
        by_id, st = story(trend=trend)
        with mock.patch.object(insight.benchmark, "for_user", return_value=([], [])):
            blocks, _, _ = insight._story_blocks(None, summary_, "overview", TODAY, by_id, st)
        return blocks

    def test_overview_leads_with_this_months_changes_not_only_monthly_totals(self):
        blocks = self.overview(summary(findings=[OFFSET]))
        key = blocks[0]["lines"][1:]
        self.assertEqual(key[0], OFFSET["conclusion"])  # 이번 달 무엇이 늘고 줄었나가 먼저
        self.assertNotIn("꾸준했어요", key[0])
        self.assertIn("이 흐름이면 이번 달 생활비는 약 86만 원이 될 거예요.", key)

    def test_overview_shows_category_share_and_skips_monthly_bars_when_trend_is_flat(self):
        kinds = [(b["kind"], b.get("title")) for b in self.overview(summary(findings=[OFFSET]))]
        self.assertIn(("rows", "이번 달 어디에 썼나요"), kinds)
        self.assertNotIn(("months", "월별 생활비"), kinds)
        moving = [(b["kind"], b.get("title")) for b in self.overview(summary(findings=[OFFSET]), trend="trend_up")]
        self.assertIn(("months", "월별 생활비"), moving)  # 추이가 실제로 변할 때만 월별 막대

    def test_few_payments_still_get_an_analysis_from_observation(self):
        seen = {"id": "OBSERVED", "kind": "observation", "section": "지출의 특징", "conclusion": "지금 확인된 지출은 식비 결제예요.",
                "evidence": ["확인된 결제액 15,000원 중 식비에 15,000원을 썼어요."]}
        blocks = self.overview(summary(observation=[seen], categories=[category("FOOD", "식비", 15000)]))
        self.assertEqual(blocks[0]["lines"][1], seen["conclusion"])
        self.assertTrue(any(b.get("title") == "지출의 특징" for b in blocks))
        self.assertFalse(any(b.get("title") == "이번 달 어디에 썼나요" for b in blocks))  # 한 항목뿐이면 비중 그래프 없음

    def test_category_share_rows_leave_out_unclassified(self):
        rows = insight._share_rows(summary(categories=[category("FOOD", "식비", 30000), category("UNCLASSIFIED", "확인 필요", 50000),
                                                       category("BEAUTY", "뷰티·미용", 10000)]))
        self.assertEqual([(r["name"], r["note"]) for r in rows["rows"]], [("식비", "75%"), ("뷰티·미용", "25%")])

    def test_same_titled_sections_are_merged(self):
        blocks = [{"kind": "text", "title": "지난달과 비교", "lines": ["지난달과 비교", "식비가 늘었어요."]},
                  {"kind": "rows", "title": "x", "rows": []},
                  {"kind": "text", "title": "지난달과 비교", "lines": ["지난달과 비교", "식비가 늘었어요.", "문화·여가도 늘었어요."]}]
        merged = insight._merge_sections(blocks)
        self.assertEqual([b.get("title") for b in merged], ["지난달과 비교", "x"])
        self.assertEqual(merged[0]["lines"], ["지난달과 비교", "식비가 늘었어요.", "문화·여가도 늘었어요."])

    def test_offset_sentence_names_what_rose_and_fell(self):
        changed = [{"category": "FOOD", "delta": 42000}, {"category": "MEDICAL_HEALTH", "delta": -30000}]
        text = reasoning._offset_text(changed)
        self.assertEqual(text, "총액은 지난달 같은 기간과 비슷하지만 식비(+4.2만 원)에서 늘고 의료·건강(-3만 원)에서 줄었어요.")
        self.assertNotIn("상쇄", text)
