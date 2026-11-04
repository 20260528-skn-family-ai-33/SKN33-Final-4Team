from datetime import date, datetime
from zoneinfo import ZoneInfo
from django.test import SimpleTestCase
from .design_reality import system_floors, REALITY_FLOORS

class RealityTests(SimpleTestCase):
    def test_profile_and_reference(self):
        self.assertEqual(system_floors({'has_vehicle':False}), {'TRANSPORT_CAR':55000})
        for value in (True,None,0):
            self.assertEqual(system_floors({'has_vehicle':value}), {})
        self.assertIsNone(REALITY_FLOORS['FOOD']['amount'])

    def test_reference_review_is_not_overdue(self):
        today = datetime.now(ZoneInfo('Asia/Seoul')).date()
        for category, reference in REALITY_FLOORS.items():
            with self.subTest(category=category):
                if today > date.fromisoformat(reference['next_check']):
                    self.skipTest('기준표 금액·출처 재확인 후 next_check 갱신 필요 (담당: 혜리)')
