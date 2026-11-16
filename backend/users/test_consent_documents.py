"""동의 문서 원문 · 해시 일치 (R24). 실행: backend 에서 python manage.py test users.test_consent_documents"""
import hashlib
import json
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from users import consent_documents as docs
from users.models import Consent

URL = "/api/finance/consents/"
HERE = Path(docs.__file__).parent


def row(scope, version="v2"):
    return {"purpose": "finance_inquiry", "productScope": scope, "documentVersion": version}


class DocumentListTest(TestCase):
    def test_anonymous_gets_current_documents_with_their_own_hash(self):
        res = self.client.get("/api/consent-documents/")
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        keys = [(i["purpose"], i["productScope"], i["version"]) for i in items]
        self.assertEqual(keys, [("terms", None, "v1"), ("privacy", None, "v1"),
                                ("finance_inquiry", "bank", "v2"), ("finance_inquiry", "card", "v2"),
                                ("finance_inquiry", "loan", "v2"), ("budget_ai", None, "v3"),
                                ("budget_ai_health", None, "v1"), ("budget_adjust_ai", None, "v2"),
                                ("budget_adjust_ai_health", None, "v2")])
        signup = json.loads((HERE / "signup_terms_v1.json").read_text(encoding="utf-8"))
        finance = json.loads((HERE / "finance_inquiry_v2.json").read_text(encoding="utf-8"))
        budget_ai = json.loads((HERE / "budget_ai_v3.json").read_text(encoding="utf-8"))
        health = json.loads((HERE / "budget_ai_health_v1.json").read_text(encoding="utf-8"))
        adjust = json.loads((HERE / "budget_adjust_ai_v2.json").read_text(encoding="utf-8"))
        adjust_health = json.loads((HERE / "budget_adjust_ai_health_v2.json").read_text(encoding="utf-8"))
        files = [signup["terms"], signup["privacy"], finance["bank"], finance["card"], finance["loan"], budget_ai, health,
                 adjust, adjust_health]
        for item, original in zip(items, files):
            self.assertEqual(item["document"], original)  # 화면이 받는 원문 = 서버가 해시하는 원문
            self.assertEqual(item["documentHash"], docs.canonical_hash(original))
            self.assertEqual(item["document"]["version"], item["version"])
        self.assertEqual(len({i["documentHash"] for i in items}), 9)
        self.assertTrue(adjust["required"] and adjust_health["required"])  # 조정 동의는 필수 (11/12)
        self.assertIn(("budget_adjust_ai", None, "v1"), docs.ARCHIVE)  # 옛 v1 기록은 지우지 않는다

    def test_v1_finance_hash_is_still_the_old_placeholder(self):
        old = hashlib.sha256("머니쿠션 금융 정보 조회 동의 v1 (최종 문구 확정 전 임시 원문)".encode("utf-8")).hexdigest()
        for scope in ("bank", "card", "loan"):
            self.assertEqual(docs.ARCHIVE[("finance_inquiry", scope, "v1")], old)


class FinanceConsentDocumentTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="doc@example.com", password="Example123!")
        self.client.force_login(self.user)

    def post(self, *rows):
        return self.client.post(URL, data=json.dumps({"consents": list(rows)}), content_type="application/json")

    def test_each_scope_stores_its_own_document_hash(self):
        res = self.post(row("bank"), row("card"), row("loan"))
        self.assertEqual(res.status_code, 201, res.content)
        for scope in ("bank", "card", "loan"):
            saved = Consent.objects.get(user=self.user, product_scope=scope)
            self.assertEqual(saved.document_version, "v2")
            self.assertEqual(saved.document_hash, docs.canonical_hash(docs.CURRENT[("finance_inquiry", scope)]))

    def test_retired_unknown_and_wrong_combinations_are_rejected(self):
        res = self.post(row("bank", "v1"))
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "document_outdated"))
        for bad in (row("bank", "v9"), {**row("bank"), "productScope": "insurance"},
                    {**row("bank"), "purpose": "terms"}):
            with self.subTest(bad=bad):
                self.assertEqual(self.post(bad).status_code, 400)
        # 한 줄이라도 틀리면 아무것도 저장하지 않는다
        self.assertEqual(self.post(row("bank"), row("card", "v1")).status_code, 409)
        self.assertFalse(Consent.objects.exists())

    def test_version_changed_after_screen_opened_is_not_saved_as_new_one(self):
        newer = {**docs.CURRENT[("finance_inquiry", "bank")], "version": "v3", "date": "2099.01.01"}
        archive = {**docs.ARCHIVE, ("finance_inquiry", "bank", "v3"): docs.canonical_hash(newer)}
        current = {**docs.CURRENT, ("finance_inquiry", "bank"): newer}
        with mock.patch.dict(docs.ARCHIVE, archive), mock.patch.dict(docs.CURRENT, current):
            res = self.post(row("bank", "v2"))  # 화면은 v2 를 보여 줬다
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "document_outdated"))
        self.assertFalse(Consent.objects.exists())

    def test_old_v1_records_stay_and_new_v2_is_added_once(self):
        old = Consent.objects.create(user=self.user, purpose="finance_inquiry", product_scope="bank",
                                     document_version="v1", document_hash=docs.FINANCE_V1_HASH)
        first = self.post(row("bank"))
        again = self.post(row("bank"), row("bank"))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(again.json()["items"], first.json()["items"])
        old.refresh_from_db()
        self.assertEqual((old.document_version, old.document_hash, old.revoked_at), ("v1", docs.FINANCE_V1_HASH, None))
        self.assertEqual(Consent.objects.filter(user=self.user).count(), 2)


class SignupConsentDocumentTest(TestCase):
    def signup(self, version="v1"):
        return self.client.post("/api/auth/signup/", data=json.dumps({
            "email": "new@example.com", "password": "Example123!", "nickname": "세진",
            "consents": [{"purpose": "terms", "documentVersion": version}, {"purpose": "privacy", "documentVersion": "v1"}],
        }), content_type="application/json")

    def test_signup_stores_hash_of_shown_documents(self):
        res = self.signup()
        self.assertEqual(res.status_code, 201, res.content)
        saved = {c.purpose: c for c in Consent.objects.all()}
        for purpose in ("terms", "privacy"):
            self.assertEqual(saved[purpose].document_version, "v1")
            self.assertEqual(saved[purpose].document_hash, docs.canonical_hash(docs.CURRENT[(purpose, None)]))

    def test_unknown_signup_version_is_rejected(self):
        res = self.signup("v2")
        self.assertEqual(res.status_code, 400)
        self.assertIn("consents", res.json()["error"]["fields"])
        self.assertFalse(get_user_model().objects.exists())
