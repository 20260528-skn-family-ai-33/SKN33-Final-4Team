from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from budgets.models import Category
from .models import CategoryMinimum, CategoryPriority, FinancialProfile


URL = "/api/profile/"


def payload():
    return {
        "birthDate": "1999-03-15", "gender": "female", "housingType": "alone", "hasCar": False,
        "priorities": [
            {"category": "FOOD", "rank": 1},
            {"category": "CULTURE_LEISURE", "rank": 2},
            {"category": "TRAVEL_LODGING", "rank": 3},
        ],
        "categoryMinimums": [{"category": "FOOD", "minAmount": 300000}],
    }


def expected_response(data):
    result = deepcopy(data)
    names = dict(Category.objects.values_list("pk", "display_name"))
    for field in ("priorities", "categoryMinimums"):
        for row in result[field]:
            row["categoryName"] = names[row["category"]]
    return result


class ProfileTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="profile@example.com", password="Example123!")
        self.other = get_user_model().objects.create_user(email="other@example.com", password="Example123!")
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.client.get("/api/csrf/")
        self.client.credentials(HTTP_X_CSRFTOKEN=self.client.cookies["csrftoken"].value)

    def put(self, data=None):
        return self.client.put(URL, payload() if data is None else data, format="json")

    def assert_invalid(self, data, field=None):
        before = self.client.get(URL).json()
        response = self.put(data)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"]["code"], "invalid")
        self.assertIn("fields", response.json()["error"])
        if field:
            self.assertIn(field, response.json()["error"]["fields"])
        self.assertEqual(self.client.get(URL).json(), before)

    def test_empty_get_does_not_create_profile(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "birthDate": None, "gender": None, "housingType": None, "hasCar": None,
            "priorities": [], "categoryMinimums": [],
        })
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())

    def test_create_then_get_without_finance_consent(self):
        response = self.put()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), expected_response(payload()))
        self.assertEqual(self.client.get(URL).json(), expected_response(payload()))
        self.assertEqual(FinancialProfile.objects.get(user=self.user).birth_date, date(1999, 3, 15))

    def test_anonymous_get_and_put(self):
        client = APIClient()
        for method in ("get", "put"):
            with self.subTest(method=method):
                response = getattr(client, method)(URL, payload() if method == "put" else None, format="json")
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_put_requires_valid_csrf(self):
        for token in (None, "x" * 32):
            with self.subTest(token=token):
                self.client.credentials(**({"HTTP_X_CSRFTOKEN": token} if token else {}))
                response = self.put()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "permission_denied")
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())

    def test_unsupported_methods(self):
        for method in ("post", "patch", "delete"):
            response = getattr(self.client, method)(URL, payload(), format="json")
            self.assertEqual(response.status_code, 405)

    def test_malformed_json(self):
        response = self.client.put(URL, '{"birthDate":', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "parse_error")

    def test_non_object_body(self):
        for data in ([], [payload()], "text", 3, True):
            with self.subTest(data=data):
                self.assert_invalid(data, "nonFieldErrors")

    def test_every_field_required_and_non_null(self):
        for field in payload():
            for missing in (True, False):
                with self.subTest(field=field, missing=missing):
                    data = payload()
                    if missing:
                        del data[field]
                    else:
                        data[field] = None
                    self.assert_invalid(data, field)

    def test_unknown_fields_including_other_owners(self):
        for field, value in (("userId", str(self.other.pk)), ("nickname", "overwrite"),
                             ("payday", 25), ("cushionMonthly", 100), ("budgetDirection", "saving")):
            with self.subTest(field=field):
                self.assert_invalid({**payload(), field: value}, field)
        for field in ("priorities", "categoryMinimums"):
            data = payload()
            data[field][0]["extra"] = 1
            self.assert_invalid(data, field)

    def test_birth_date_formats_and_bounds(self):
        with patch("users.profile_serializers.timezone.localdate", return_value=date(2026, 10, 28)):
            for value in ("991231", "2026-1-01", "2026-02-30", "2026-10-29", "1905-10-28",
                          "1999-03-15T00:00:00", 19990315, True, ""):
                with self.subTest(value=value):
                    self.assert_invalid({**payload(), "birthDate": value}, "birthDate")
            for value in ("2026-10-28", "1905-10-29", "2000-02-29"):
                response = self.put({**payload(), "birthDate": value})
                self.assertEqual(response.status_code, 200, response.content)

    def test_gender_housing_and_car_types(self):
        cases = {"gender": ["남성", "other", "", 1, True],
                 "housingType": ["rent", "", 1, True],
                 "hasCar": [0, 1, "true", "false", "", []]}
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**payload(), field: value}, field)
        data = {**payload(), "gender": "male", "housingType": "family", "hasCar": True}
        self.assertEqual(self.put(data).json(), expected_response(data))

    def test_priority_minimum_count_and_shape(self):
        for value in ([], payload()["priorities"][:2], {}, "FOOD", [1, 2, 3]):
            with self.subTest(value=value):
                self.assert_invalid({**payload(), "priorities": value}, "priorities")

    def test_priority_rank_gaps_duplicates_and_types(self):
        for ranks in ([1, 2, 4], [1, 1, 2], [2, 3, 4], [0, 1, 2], [1, 2, 11],
                      [1, 2, "3"], [1, 2, 3.0], [True, 2, 3]):
            with self.subTest(ranks=ranks):
                data = payload()
                for row, rank in zip(data["priorities"], ranks):
                    row["rank"] = rank
                self.assert_invalid(data, "priorities")

    def test_priority_categories(self):
        for value in ("ETC", "UNCLASSIFIED", "food", "식비", "MISSING", 1, " FOOD", "CULTURE_LEISURE"):
            data = payload()
            data["priorities"][0]["category"] = value
            self.assert_invalid(data, "priorities")

    def test_all_ten_priorities_and_response_sort(self):
        codes = list(Category.objects.filter(is_ml_label=True).order_by("display_order").values_list("pk", flat=True))
        data = payload()
        data["priorities"] = [{"category": code, "rank": rank} for rank, code in enumerate(codes, 1)]
        expected = deepcopy(data)
        data["priorities"].reverse()
        self.assertEqual(self.put(data).json(), expected_response(expected))
        data["priorities"].append({"category": "ETC", "rank": 11})
        self.assert_invalid(data, "priorities")

    def test_minimum_bounds_types_categories_and_duplicates(self):
        for amount in (0, -1, 10_000_000_000, 1.0, "100", True):
            data = payload()
            data["categoryMinimums"][0]["minAmount"] = amount
            self.assert_invalid(data, "categoryMinimums")
        for code in ("UNCLASSIFIED", "missing", 12):
            data = payload()
            data["categoryMinimums"][0]["category"] = code
            self.assert_invalid(data, "categoryMinimums")
        data = payload()
        data["categoryMinimums"] *= 2
        self.assert_invalid(data, "categoryMinimums")
        for rows in ({}, "FOOD", [1]):
            self.assert_invalid({**payload(), "categoryMinimums": rows}, "categoryMinimums")

    def test_all_eleven_minimums_and_upper_bound(self):
        codes = list(Category.objects.filter(is_living_budget_category=True).order_by("display_order").values_list("pk", flat=True))
        data = payload()
        data["categoryMinimums"] = [{"category": code, "minAmount": 9_999_999_999} for code in codes]
        expected = deepcopy(data)
        data["categoryMinimums"].reverse()
        self.assertEqual(self.put(data).json(), expected_response(expected))
        data["categoryMinimums"].append({"category": "FOOD", "minAmount": 1})
        self.assert_invalid(data, "categoryMinimums")

    def test_inactive_category_rejected(self):
        Category.objects.filter(pk="FOOD").update(is_active=False)
        self.assert_invalid(payload())

    def test_rank_swap_and_full_replacement(self):
        self.assertEqual(self.put().status_code, 200)
        data = payload()
        data["priorities"][0]["rank"], data["priorities"][1]["rank"] = 2, 1
        data["priorities"][2]["category"] = "EDUCATION"
        data["priorities"].sort(key=lambda row: row["rank"])
        data["categoryMinimums"] = []
        self.assertEqual(self.put(data).json(), expected_response(data))
        self.assertFalse(CategoryMinimum.objects.filter(user=self.user).exists())
        self.assertFalse(CategoryPriority.objects.filter(user=self.user, category_id="TRAVEL_LODGING").exists())

    def test_repeat_put_is_idempotent(self):
        first = self.put().json()
        self.assertEqual(self.put().json(), first)
        self.assertEqual(FinancialProfile.objects.filter(user=self.user).count(), 1)
        self.assertEqual(CategoryPriority.objects.filter(user=self.user).count(), 3)
        self.assertEqual(CategoryMinimum.objects.filter(user=self.user).count(), 1)

    def test_other_profile_columns_preserved(self):
        saved = FinancialProfile.objects.create(
            user=self.user, monthly_take_home_income=2500000, monthly_disposable_income=2000000,
            disposable_income_basis="manual", payday=25, payday_rule="last_day", budget_direction="stable",
            living_budget=1000000, savings_floor=200000, cushion_enabled=True, cushion_target=7500000,
            cushion_monthly=100000, cushion_use_policy="manual", loan_presence="yes",
        )
        before = FinancialProfile.objects.filter(pk=saved.pk).values().get()
        self.assertEqual(self.put().status_code, 200)
        after = FinancialProfile.objects.filter(pk=saved.pk).values().get()
        for field in ("birth_date", "gender", "housing_type", "has_car", "updated_at"):
            before.pop(field)
            after.pop(field)
        self.assertEqual(after, before)

    def test_member_isolation(self):
        FinancialProfile.objects.create(user=self.other, housing_type="family")
        CategoryPriority.objects.create(user=self.other, category_id="EDUCATION", rank=1)
        CategoryMinimum.objects.create(user=self.other, category_id="ETC", min_amount=123)
        self.assertEqual(self.put().json(), expected_response(payload()))
        self.assertEqual(FinancialProfile.objects.get(user=self.other).housing_type, "family")
        self.assertEqual(list(CategoryPriority.objects.filter(user=self.other).values_list("category_id", "rank")), [("EDUCATION", 1)])
        self.assertEqual(CategoryMinimum.objects.get(user=self.other).min_amount, 123)

    def test_validation_failure_preserves_existing_settings(self):
        self.put()
        data = payload()
        data["housingType"] = "family"
        data["categoryMinimums"][0]["minAmount"] = -1
        self.assert_invalid(data, "categoryMinimums")

    @override_settings(DEBUG=False)
    def test_database_failure_rolls_back_entire_write(self):
        self.put()
        before = self.client.get(URL).json()
        data = {**payload(), "housingType": "family", "categoryMinimums": []}
        with patch("users.api_profile.CategoryMinimum.objects.bulk_create", side_effect=RuntimeError("test rollback")):
            with self.assertLogs("config.api", level="ERROR"):
                response = self.put(data)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "server_error")
        self.assertEqual(self.client.get(URL).json(), before)

    @override_settings(DEBUG=False)
    def test_first_save_failure_leaves_no_partial_profile(self):
        with patch("users.api_profile.CategoryMinimum.objects.bulk_create", side_effect=RuntimeError("test rollback")):
            with self.assertLogs("config.api", level="ERROR"):
                response = self.put()
        self.assertEqual(response.status_code, 500)
        self.assertFalse(FinancialProfile.objects.filter(user=self.user).exists())
        self.assertFalse(CategoryPriority.objects.filter(user=self.user).exists())


@skipUnless(connection.vendor == "postgresql", "PostgreSQL 행 잠금 검사")
class ProfileConcurrencyTests(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(email="concurrent-profile@example.com")
        for order, code in enumerate(("FOOD", "CULTURE_LEISURE", "TRAVEL_LODGING"), 1):
            Category.objects.update_or_create(pk=code, defaults={
                "display_name": code, "display_order": order, "is_active": True,
                "is_ml_label": True, "is_living_budget_category": True,
            })

    def request(self, method, data=None, started=None, pids=None):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SELECT pg_backend_pid()")
                if pids is not None:
                    pids.append(cursor.fetchone()[0])
            client = APIClient()
            client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
            if started:
                started.set()
            response = getattr(client, method)(URL, data, format="json")
            self.assertEqual(response.status_code, 200, response.content)
            return response.json()
        finally:
            connections.close_all()

    def wait_for_locks(self, pids):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE pid = ANY(%s) AND wait_event_type = 'Lock'", [pids])
                if cursor.fetchone()[0] == len(pids):
                    return
            sleep(0.01)
        self.fail("회원 잠금을 기다리는 요청이 확인되지 않았습니다.")

    def test_concurrent_first_puts_do_not_mix_lists(self):
        second = {**payload(), "housingType": "family", "categoryMinimums": []}
        second["priorities"][0]["rank"], second["priorities"][1]["rank"] = 2, 1
        second["priorities"].sort(key=lambda row: row["rank"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            with transaction.atomic():
                get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)
                started_a, started_b = Event(), Event()
                pids = []
                first = pool.submit(self.request, "put", payload(), started_a, pids)
                other = pool.submit(self.request, "put", second, started_b, pids)
                self.assertTrue(started_a.wait(3))
                self.assertTrue(started_b.wait(3))
                self.wait_for_locks(pids)
            self.assertEqual(first.result(timeout=10), expected_response(payload()))
            self.assertEqual(other.result(timeout=10), expected_response(second))
        self.assertIn(self.request("get"), [expected_response(payload()), expected_response(second)])
        self.assertEqual(FinancialProfile.objects.filter(user=self.user).count(), 1)
        self.assertEqual(CategoryPriority.objects.filter(user=self.user).count(), 3)

    def test_get_waits_for_complete_profile_write(self):
        self.request("put", payload())
        started = Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                get_user_model().objects.select_for_update(no_key=True).get(pk=self.user.pk)
                FinancialProfile.objects.filter(user=self.user).update(housing_type="family")
                CategoryMinimum.objects.filter(user=self.user).delete()
                pids = []
                future = pool.submit(self.request, "get", started=started, pids=pids)
                self.assertTrue(started.wait(3))
                self.wait_for_locks(pids)
            expected = {**payload(), "housingType": "family", "categoryMinimums": []}
            self.assertEqual(future.result(timeout=10), expected_response(expected))
