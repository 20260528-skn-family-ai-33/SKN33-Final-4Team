from django.test import SimpleTestCase

from policies.regions import REGIONS
from policies.tools import region_text


def finals(prefix):
    return [c for c, r in REGIONS.items() if r["final"] and r["name"].startswith(prefix)]


class RegionTextTest(SimpleTestCase):
    def test_nationwide_over_threshold(self):
        self.assertEqual(region_text([str(n) for n in range(201)]), "전국")

    def test_whole_province_collapses_to_its_name(self):
        self.assertEqual(region_text(finals("서울특별시 ")), "서울특별시")
        self.assertEqual(region_text(["11000"]), "서울특별시")  # 시도 코드 하나도 시도 전체

    def test_whole_city_collapses_over_its_districts(self):
        self.assertEqual(region_text(finals("경기도 수원시 ")), "경기도 수원시")

    def test_partial_lists_names_then_counts(self):
        self.assertEqual(region_text(["11680", "11650"]), "서울특별시 서초구 · 강남구")
        self.assertEqual(region_text(finals("서울특별시 ")[:6]), "서울특별시 6곳")

    def test_several_provinces_separated_by_comma(self):
        self.assertEqual(region_text(["11000", "26000"]), "서울특별시, 부산광역시")
        self.assertEqual(region_text(["11000", "26000", "27000"]), "서울특별시, 부산광역시 외 1개 시·도")

    def test_retired_code_maps_to_new_district(self):
        self.assertIsNotNone(region_text(["28140"]))
        self.assertIsNone(region_text([]))
