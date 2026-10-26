"""정기 저축 API 테스트. 실행: backend 폴더에서 python manage.py test budgets
(테스트용 DB를 따로 만들었다가 지운다. 내 연습 데이터는 건드리지 않는다)

- 본문 모양 검사: {} 가 아닌 본문([] · 42 · "hello")은 500 이 아니라 400 invalid
- 중지 + 예산 초안 만료: 둘 다 되거나 둘 다 안 되거나
- 동시 요청: 같은 저축을 동시에 바꿔도 상태가 덮어써지지 않음 (PostgreSQL 에서만 실행)
"""
import json
import threading
import time
from datetime import date
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from finance.models import RecurringSaving

from .models import Budget, BudgetCycle

URL = "/api/budgets/savings/"


def make_user(email="naeun@example.com"):
    return get_user_model().objects.create_user(email=email, password="Example123!")


def make_saving(user, status="suggested", **fields):
    data = dict(user=user, name="KB 적금", amount=300000, transfer_day=25, status=status,
                source_type="detected" if status in ("suggested", "ignored") else "manual")
    data.update(fields)
    return RecurringSaving.objects.create(**data)


def make_draft_budget(user):
    cycle = BudgetCycle.objects.create(user=user, starts_on=date(2026, 10, 25), ends_before=date(2026, 11, 25))
    return Budget.objects.create(user=user, cycle=cycle, version_no=1, status="draft")


def send(client, method, url, body):
    """본문을 JSON 그대로 보내기 ([] · 42 · "hello" 도 그대로)"""
    return getattr(client, method)(url, data=json.dumps(body), content_type="application/json")


class SavingsBodyTypeTest(TestCase):
    """본문이 JSON 객체가 아니면 400 invalid (500 이 나면 안 됨)"""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.saving = make_saving(cls.user, status="confirmed")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_not_object_body_is_400(self):
        targets = [
            ("post", URL),                                # 직접 추가
            ("patch", f"{URL}{self.saving.pk}/"),         # 수정
            ("post", f"{URL}confirm/"),                   # 후보 확정
            ("post", f"{URL}ignore/"),                    # 후보 무시
        ]
        for method, url in targets:
            for body in ([], [1, 2], 42, "hello", None):
                with self.subTest(method=method, url=url, body=body):
                    r = send(self.client, method, url, body)
                    self.assertEqual(r.status_code, 400, r.content)
                    self.assertEqual(r.json()["error"]["code"], "invalid")

        # 아무것도 저장 · 변경되지 않음
        self.assertEqual(RecurringSaving.objects.count(), 1)
        self.saving.refresh_from_db()
        self.assertEqual(self.saving.status, "confirmed")


class SavingsStateTest(TestCase):
    """한 번에 하나씩 보내도 상태 규칙이 지켜지는지"""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_confirm_then_ignore_is_400(self):
        s = make_saving(self.user)
        self.assertEqual(send(self.client, "post", f"{URL}confirm/", {"savingIds": [str(s.pk)]}).status_code, 200)
        self.assertEqual(send(self.client, "post", f"{URL}ignore/", {"savingIds": [str(s.pk)]}).status_code, 400)
        s.refresh_from_db()
        self.assertEqual(s.status, "confirmed")

    def test_ignore_then_confirm_is_400(self):
        s = make_saving(self.user)
        self.assertEqual(send(self.client, "post", f"{URL}ignore/", {"savingIds": [str(s.pk)]}).status_code, 200)
        self.assertEqual(send(self.client, "post", f"{URL}confirm/", {"savingIds": [str(s.pk)]}).status_code, 400)
        s.refresh_from_db()
        self.assertEqual(s.status, "ignored")

    def test_patch_after_stop_is_404(self):
        s = make_saving(self.user, status="confirmed")
        self.assertEqual(self.client.delete(f"{URL}{s.pk}/").status_code, 204)
        self.assertEqual(send(self.client, "patch", f"{URL}{s.pk}/", {"amount": 500000}).status_code, 404)
        s.refresh_from_db()
        self.assertEqual((s.status, s.amount), ("stopped", 300000))

    def test_stop_and_budget_expire_roll_back_together(self):
        """예산 만료가 실패하면 중지도 되돌림 → 다시 보내면 둘 다 처리"""
        s = make_saving(self.user, status="confirmed")
        budget = make_draft_budget(self.user)

        with mock.patch("django.db.models.query.QuerySet.update", side_effect=RuntimeError("DB 오류")):
            r = self.client.delete(f"{URL}{s.pk}/")
        self.assertEqual(r.status_code, 500)
        s.refresh_from_db()
        budget.refresh_from_db()
        self.assertEqual((s.status, budget.status), ("confirmed", "draft"))   # 둘 다 그대로

        # 다시 보내면 중지 + 만료 둘 다
        self.assertEqual(self.client.delete(f"{URL}{s.pk}/").status_code, 204)
        s.refresh_from_db()
        budget.refresh_from_db()
        self.assertEqual((s.status, budget.status), ("stopped", "expired"))


@skipUnless(connection.vendor == "postgresql", "행 잠금(select_for_update)은 PostgreSQL 에서만 확인")
class SavingsConcurrencyTest(TransactionTestCase):
    """같은 저축에 요청 두 개가 동시에 와도 상태가 덮어써지지 않는지

    'slow' 스레드는 저축을 저장하기 직전에 잠깐 멈춘다 → 그 사이에 다른 요청이 끼어든다.
    잠금이 없으면 끼어든 요청이 옛 상태를 보고 진행해서 결과가 꼬인다.
    """

    def setUp(self):
        self.user = make_user()

    def run_together(self, first, second, gap=0.1, pause=0.5):
        """first 는 'slow' 스레드(저장 직전 pause 초 멈춤), second 는 gap 초 뒤에 시작. 응답 상태코드 2개를 돌려줌"""
        results = {}
        original_save = RecurringSaving.save

        def slow_save(obj, *args, **kwargs):
            if threading.current_thread().name == "slow":
                time.sleep(pause)
            return original_save(obj, *args, **kwargs)

        def worker(key, request):
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                results[key] = request(client).status_code
            finally:
                connection.close()   # 스레드마다 따로 연 DB 연결 닫기

        with mock.patch.object(RecurringSaving, "save", slow_save):
            t1 = threading.Thread(target=worker, args=("first", first), name="slow")
            t2 = threading.Thread(target=worker, args=("second", second), name="fast")
            t1.start()
            time.sleep(gap)
            t2.start()
            t1.join(10)
            t2.join(10)
        return results["first"], results["second"]

    def test_patch_and_delete_do_not_revive_stopped(self):
        """수정 중에 중지가 끼어들어도 → 최종 상태는 중지"""
        s = make_saving(self.user, status="confirmed")
        codes = self.run_together(
            lambda c: send(c, "patch", f"{URL}{s.pk}/", {"amount": 500000}),
            lambda c: c.delete(f"{URL}{s.pk}/"),
        )
        self.assertEqual(codes, (200, 204))
        s.refresh_from_db()
        self.assertEqual((s.status, s.amount), ("stopped", 500000))

    def test_delete_and_patch_patch_sees_stopped(self):
        """중지 중에 수정이 끼어들면 → 수정은 404, 상태는 중지"""
        s = make_saving(self.user, status="confirmed")
        codes = self.run_together(
            lambda c: c.delete(f"{URL}{s.pk}/"),
            lambda c: send(c, "patch", f"{URL}{s.pk}/", {"amount": 500000}),
        )
        self.assertEqual(codes, (204, 404))
        s.refresh_from_db()
        self.assertEqual((s.status, s.amount), ("stopped", 300000))

    def test_confirm_and_ignore_only_one_wins(self):
        """같은 후보에 확정 · 무시가 동시에 → 하나만 200, 나머지는 400"""
        s = make_saving(self.user)
        body = {"savingIds": [str(s.pk)]}
        codes = self.run_together(
            lambda c: send(c, "post", f"{URL}confirm/", body),
            lambda c: send(c, "post", f"{URL}ignore/", body),
        )
        self.assertEqual(codes, (200, 400))
        s.refresh_from_db()
        self.assertEqual(s.status, "confirmed")

    def test_batch_reverse_order_no_deadlock(self):
        """[A, B] 확정과 [B, A] 무시가 동시에 → 잠금 순서가 같아서 멈추지 않고 하나만 성공"""
        a = make_saving(self.user, name="A")
        b = make_saving(self.user, name="B")
        codes = self.run_together(
            lambda c: send(c, "post", f"{URL}confirm/", {"savingIds": [str(a.pk), str(b.pk)]}),
            lambda c: send(c, "post", f"{URL}ignore/", {"savingIds": [str(b.pk), str(a.pk)]}),
        )
        self.assertEqual(codes, (200, 400))
        self.assertEqual(
            sorted(RecurringSaving.objects.values_list("status", flat=True)), ["confirmed", "confirmed"],
        )
