from datetime import date
from unittest import mock

from django.test import TestCase

from policies import sync
from policies.llm import EmbedFailed, fake_embed
from policies.models import Policy, PolicySync
from policies.tests.test_normalize import row

DAY = date(2026, 10, 29)


def rows(count, **changes):
    return [row(plcyNo=f"T{i}", plcyNm=f"정책 {i}", **changes) for i in range(count)]


class LoadTest(TestCase):
    def test_처음_적재와_같은_목록_재실행(self):
        first = sync.load(rows(3), source="file", data_date=DAY)
        self.assertEqual((first.status, first.received, first.changed), ("ok", 3, 3))
        again = sync.load(rows(3), source="file", data_date=DAY)
        self.assertEqual((again.changed, again.deactivated), (0, 0))

    def test_바뀐_정책만_고치고_임베딩은_지우지_않는다(self):
        sync.load(rows(2), source="file", data_date=DAY)
        sync.embed_changed("fake", fake_embed)
        data = rows(2)
        data[0]["aplyYmd"] = "20261201 ~ 20261231"
        result = sync.load(data, source="file", data_date=DAY)
        self.assertEqual(result.changed, 1)
        policy = Policy.objects.get(pk="T0")
        self.assertEqual(policy.last_apply_end, date(2026, 12, 31))
        self.assertIsNotNone(policy.embedding)

    def test_사라진_정책은_비활성화하고_돌아오면_되살린다(self):
        sync.load(rows(5), source="file", data_date=DAY)
        result = sync.load(rows(5)[:4], source="file", data_date=DAY)
        self.assertEqual(result.deactivated, 1)
        self.assertFalse(Policy.objects.get(pk="T4").is_active)
        sync.load(rows(5), source="file", data_date=DAY)
        self.assertTrue(Policy.objects.get(pk="T4").is_active)

    def test_잘못된_목록은_기존_데이터를_건드리지_않는다(self):
        sync.load(rows(10), source="file", data_date=DAY)
        for bad in ([], {"a": 1}, rows(7), rows(10) + rows(1), [*rows(9), row(plcyNo="")]):
            with self.assertRaises(sync.SyncRejected):
                sync.load(bad, source="file", data_date=DAY)
        self.assertEqual(Policy.objects.filter(is_active=True).count(), 10)
        self.assertEqual(PolicySync.objects.filter(status="failed").count(), 5)

    def test_표에_없는_지역_코드는_세고_적재는_계속한다(self):
        with self.assertLogs("policies.sync", "WARNING"):
            result = sync.load(rows(1, zipCd="99999"), source="file", data_date=DAY)
        self.assertEqual((result.status, result.unknown_regions), ("ok", 1))


class EmbedTest(TestCase):
    def setUp(self):
        sync.load(rows(3), source="file", data_date=DAY)

    def test_처음엔_전부_다음엔_없음(self):
        self.assertEqual(sync.embed_changed("fake", fake_embed), (3, 0))
        self.assertEqual(sync.embed_changed("fake", fake_embed), (0, 0))

    def test_모델이_바뀌면_다시_만든다(self):
        sync.embed_changed("fake", fake_embed)
        self.assertEqual(sync.embed_changed("other-model", fake_embed), (3, 0))

    def test_문장이_바뀐_정책만_다시_만든다(self):
        sync.embed_changed("fake", fake_embed)
        data = rows(3)
        data[1]["plcySprtCn"] = "새 지원 내용"
        sync.load(data, source="file", data_date=DAY)
        self.assertEqual(sync.embed_changed("fake", fake_embed), (1, 0))

    def test_비활성_정책은_임베딩하지_않는다(self):
        Policy.objects.filter(pk="T2").update(is_active=False)
        self.assertEqual(sync.embed_changed("fake", fake_embed), (2, 0))

    def test_실패하면_멈추고_다음_실행이_이어서_한다(self):
        with mock.patch.object(sync, "BATCH", 1):
            calls = []

            def flaky(texts):
                calls.append(texts)
                if len(calls) == 2:
                    raise EmbedFailed("HTTP 500")
                return fake_embed(texts)

            with self.assertLogs("policies.sync", "WARNING"):
                self.assertEqual(sync.embed_changed("fake", flaky), (1, 2))
            self.assertEqual(len(calls), 2)
            self.assertEqual(sync.embed_changed("fake", fake_embed), (2, 0))
        self.assertEqual(Policy.objects.filter(embedding__isnull=True).count(), 0)


class RenormalizeTest(TestCase):
    def test_규칙이_바뀌면_원본으로_다시_정리하고_임베딩은_그대로(self):
        sync.load(rows(2, sprtTrgtMinAge="15", sprtTrgtMaxAge="69", addAplyQlfcCndCn="15세~69세 구직자"),
                  source="file", data_date=DAY)
        sync.embed_changed("fake", fake_embed)
        Policy.objects.filter(pk="T0").update(min_age=None, max_age=None)  # 옛 규칙으로 저장된 상태
        Policy.objects.filter(pk="T1").update(is_active=False)
        before = Policy.objects.get(pk="T0")

        counts, changed = sync.renormalize(save=False)
        self.assertEqual((counts, [p.pk for p, _ in changed]), ({"min_age": 1, "max_age": 1}, ["T0"]))
        self.assertIsNone(Policy.objects.get(pk="T0").min_age)  # 미리보기는 저장하지 않는다

        sync.renormalize()
        after = Policy.objects.get(pk="T0")
        self.assertEqual((after.min_age, after.max_age, after.embedded_hash), (15, 69, before.embedded_hash))
        self.assertGreater(after.updated_at, before.updated_at)
        self.assertFalse(Policy.objects.get(pk="T1").is_active)
        self.assertEqual(sync.renormalize()[1], [])  # 다시 돌리면 바뀔 게 없다
