from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import ProtectedError
from django.test import TestCase
from rest_framework.test import APIClient

from policies import saved
from policies.models import Policy, SavedPolicy
from policies.tests.test_search import add

NUMBER = "20261021005400214208"


def user(email="saver@example.com"):
    return get_user_model().objects.create_user(email=email)


class SaveTest(TestCase):
    def setUp(self):
        self.user = user()
        add(NUMBER, plcyNm="청년 월세 지원", aplyPrdSeCd="0057001", aplyYmd="20261101 ~ 20261130",
            aplyUrlAddr="https://example.org/apply")

    def test_두_번_저장해도_한_줄(self):
        first, created = saved.save(self.user, NUMBER)
        again, created_again = saved.save(self.user, NUMBER)
        self.assertEqual((created, created_again, first.pk), (True, False, again.pk))
        self.assertEqual(saved.save_all(self.user, [NUMBER, NUMBER, "NONE"]), ["NONE"])
        self.assertEqual(SavedPolicy.objects.count(), 1)

    def test_없거나_비활성인_정책은_저장하지_않는다(self):
        Policy.objects.filter(pk=NUMBER).update(is_active=False)
        self.assertEqual(saved.save(self.user, NUMBER), (None, False))
        self.assertEqual(saved.save(self.user, "NONE"), (None, False))

    def test_목록은_채팅_카드와_같은_지역과_지원_요약을_준다(self):
        add("LOAN", plcyNm="청년 전세 대출", zipCd="11110", plcySprtCn="ㅁ 최대 2억 원 저금리 전세자금 대출")
        saved.save(self.user, "LOAN")
        [row] = saved.items(self.user, date(2026, 11, 10))
        self.assertEqual((row["region"], row["support_type"], row["support"]),
                         ("서울특별시 종로구", "대출 · 상환 필요", "최대 2억 원 저금리 전세자금 대출"))

    def test_목록은_마감과_수집_종료도_상태로_보여_준다(self):
        saved.save(self.user, NUMBER)
        [row] = saved.items(self.user, date(2026, 11, 10))
        self.assertEqual((row["apply_state"], row["apply_end"], row["link"], row["active"]),
                         ("open", date(2026, 11, 30), "https://example.org/apply", True))
        Policy.objects.filter(pk=NUMBER).update(is_active=False)
        [row] = saved.items(self.user, date(2026, 12, 1))
        self.assertEqual((row["apply_state"], row["active"]), ("closed", False))

    def test_탈퇴하면_지우고_정책은_지울_수_없다(self):
        saved.save(self.user, NUMBER)
        with self.assertRaises(ProtectedError):
            Policy.objects.filter(pk=NUMBER).delete()
        self.user.delete()
        self.assertFalse(SavedPolicy.objects.exists())


class ApiTest(TestCase):
    def setUp(self):
        self.user, self.other = user(), user("other@example.com")
        add(NUMBER, plcyNm="청년 월세 지원")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def url(self, number=NUMBER):
        return f"/api/policies/{number}/saved/"

    def test_저장_목록_취소(self):
        response = self.client.put(self.url())
        self.assertEqual((response.status_code, response.json()["policyNo"], response.json()["applyState"]),
                         (200, NUMBER, "always"))
        self.assertEqual(self.client.put(self.url(), {}, format="json").status_code, 200)  # 두 번째도 성공
        items = self.client.get("/api/policies/saved/").json()["items"]
        self.assertEqual([i["name"] for i in items], ["청년 월세 지원"])
        self.assertEqual(set(items[0]), {"savedPolicyId", "policyNo", "name", "applyState", "applyEnd", "link",
                                         "active", "savedAt", "region", "supportType", "support"})
        self.assertTrue(items[0]["savedAt"].endswith("+09:00"))  # 한국 시간 (API 규칙)
        self.assertEqual(self.client.delete(self.url()).status_code, 204)
        self.assertEqual(self.client.delete(self.url()).status_code, 204)  # 이미 취소해도 성공
        self.assertEqual(self.client.get("/api/policies/saved/").json(), {"items": []})

    def test_남의_저장은_보이지_않는다(self):
        saved.save(self.other, NUMBER)
        self.assertEqual(self.client.get("/api/policies/saved/").json(), {"items": []})
        self.client.delete(self.url())  # 내 저장만 지운다
        self.assertEqual(SavedPolicy.objects.filter(user=self.other).count(), 1)

    def test_없는_정책은_404_본문_칸은_400(self):
        for method in ("put", "delete"):
            response = getattr(self.client, method)(self.url("NONE"))
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (404, "not_found"))
        response = self.client.put(self.url(), {"memo": "x"}, format="json")
        self.assertEqual((response.status_code, list(response.json()["error"]["fields"])), (400, ["memo"]))

    def test_로그인이_필요하다(self):
        response = APIClient().get("/api/policies/saved/")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "not_authenticated"))


class RegionsApiTest(TestCase):
    """GET /api/policies/regions/ — 온보딩 · 내 정보의 사는 지역 드롭다운 (11-11)"""

    def test_시도_아래_시군구만_돌려준다(self):
        client = APIClient()
        self.assertEqual(client.get("/api/policies/regions/").status_code, 403)  # 로그인 필요
        client.force_login(user("regions@example.com"))
        items = client.get("/api/policies/regions/").json()["items"]
        self.assertEqual(len(items), 16)
        seoul = next(item for item in items if item["code"] == "11000")
        self.assertIn({"code": "11620", "name": "관악구"}, seoul["regions"])
        gyeonggi = next(item for item in items if item["name"] == "경기도")
        self.assertIn({"code": "41597", "name": "화성시 동탄구"}, gyeonggi["regions"])  # 구가 있는 시는 구까지
        self.assertNotIn("41590", [r["code"] for r in gyeonggi["regions"]])  # 화성시 자체는 고르지 않는다
        sejong = next(item for item in items if item["code"] == "36110")
        self.assertEqual(sejong["regions"], [{"code": "36110", "name": "세종특별자치시"}])
