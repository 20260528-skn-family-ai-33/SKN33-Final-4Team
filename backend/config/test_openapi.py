"""문서의 누락·인증 계약·실제 목업과의 혼동을 DB 연결 없이 검사한다."""

from collections import Counter
import json
from pathlib import Path
import re

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, override_settings

from .openapi import build_contract, build_runtime_schema


ROOT = Path(__file__).resolve().parents[2]
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
SCHEMA_URLS = ("/api/schema/", "/api/schema/runtime/")
DOCS_URLS = ("/api/docs/", "/api/docs/runtime/")


def operation_items(schema):
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def normalized_path(path):
    """Django <uuid:name>, 원문 <id>, OpenAPI {name}의 이름 차이만 제거."""
    return re.sub(r"<[^>]+>|\{[^}]+\}", "{}", path)


def resolve_parameter(schema, parameter):
    if "$ref" not in parameter:
        return parameter
    value = schema
    for segment in parameter["$ref"].removeprefix("#/").split("/"):
        value = value[segment]
    return value


@override_settings(ALLOWED_HOSTS=["testserver"], DEBUG=False)
class OpenAPIContractTests(SimpleTestCase):
    # SimpleTestCase는 DB 접근 자체를 실패시킨다. 회원·금융자료가 없어도 문서를 볼 수 있어야 한다.
    databases = set()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contract = build_contract()

    def test_contract_covers_the_35_operations_in_the_source_manifest(self):
        manifest = (ROOT / "docs/api/README.md").read_text(encoding="utf-8")
        section = manifest.split("## 1. 한눈에 보기", 1)[1].split("## 2.", 1)[0]
        entries = re.findall(r"`(GET|POST|PUT|PATCH|DELETE)\s+(/api/[^`]+)`", section)
        expected = {(method.lower(), normalized_path(path)) for method, path in entries}
        operations = list(operation_items(self.contract))
        actual = {(method, normalized_path(path)) for path, method, _ in operations}

        self.assertEqual(len(entries), 35)
        self.assertEqual(len(operations), 35)
        self.assertSetEqual(actual, expected)
        self.assertEqual(len({op["operationId"] for _, _, op in operations}), 35)
        self.assertEqual(len({tag for _, _, op in operations for tag in op["tags"]}), 9)
        for path, method, operation in operations:
            with self.subTest(path=path, method=method):
                self.assertTrue((ROOT / operation["x-source"]).is_file())

    def test_contract_separates_implemented_mock_and_planned_operations(self):
        states = {
            (method, path): operation["x-implementation-status"]
            for path, method, operation in operation_items(self.contract)
        }
        self.assertEqual(Counter(states.values()), {"implemented": 8, "mock": 7, "planned": 20})
        self.assertEqual(states[("get", "/api/csrf/")], "implemented")
        self.assertEqual(states[("get", "/api/home/")], "mock")
        self.assertEqual(states[("get", "/api/finance/transactions/")], "implemented")
        self.assertEqual(states[("post", "/api/finance/loans/")], "planned")
        for (method, path), state in states.items():
            if path.startswith("/api/budgets/fixed/"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(state, "mock")
            if path.startswith("/api/budgets/savings/"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(state, "implemented")

    def test_session_csrf_and_request_number_contracts(self):
        public = {"/api/csrf/", "/api/auth/signup/", "/api/auth/login/", "/api/auth/logout/"}
        # README의 요청 번호 적용 목록. PUT/PATCH/DELETE와 로그인 전 가입은 포함하지 않는다.
        idempotent = {
            "/api/finance/consents/", "/api/finance/loans/", "/api/chat/messages/",
            "/api/budgets/fixed/", "/api/budgets/fixed/confirm/", "/api/budgets/fixed/ignore/",
            "/api/budgets/savings/", "/api/budgets/savings/confirm/", "/api/budgets/savings/ignore/",
        }
        scheme = self.contract["components"]["securitySchemes"]["sessionAuth"]
        self.assertEqual((scheme["type"], scheme["in"], scheme["name"]), ("apiKey", "cookie", "sessionid"))
        request_number_operations = set()
        for path, method, operation in operation_items(self.contract):
            with self.subTest(path=path, method=method):
                security = operation.get("security", self.contract.get("security"))
                self.assertEqual(security, [] if path in public else [{"sessionAuth": []}])
                parameters = [resolve_parameter(self.contract, p) for p in operation.get("parameters", [])]
                headers = {p["name"].lower(): p for p in parameters if p["in"] == "header"}
                self.assertEqual("x-csrftoken" in headers, method != "get")
                if method != "get":
                    self.assertTrue(headers["x-csrftoken"]["required"])
                if "idempotency-key" in headers:
                    self.assertEqual(method, "post")
                    if path.startswith(("/api/budgets/fixed/", "/api/budgets/savings/")):
                        # fixed/savings 원문은 선택 헤더다. 적용 대상과 필수 여부는 별개다.
                        self.assertFalse(headers["idempotency-key"]["required"])
                    request_number_operations.add(path)
        self.assertSetEqual(request_number_operations, idempotent)

    def test_path_variables_have_matching_required_uuid_parameters(self):
        for path, method, operation in operation_items(self.contract):
            with self.subTest(path=path, method=method):
                parameters = [resolve_parameter(self.contract, p) for p in operation.get("parameters", [])]
                parameters += [resolve_parameter(self.contract, p) for p in self.contract["paths"][path].get("parameters", [])]
                path_parameters = [p for p in parameters if p["in"] == "path"]
                names = re.findall(r"\{([^}]+)\}", path)
                self.assertCountEqual([p["name"] for p in path_parameters], names)
                for parameter in path_parameters:
                    self.assertTrue(parameter["required"])
                    self.assertEqual(parameter["schema"].get("format"), "uuid")

    def test_schema_http_response_preserves_openapi_and_payload_keys(self):
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        document = response.json()
        # CamelJSONRenderer를 잘못 거치면 예시 이름(not_authenticated 등)까지 바뀐다.
        self.assertEqual(document, self.contract)
        self.assertIn("x-source-sha256", document)
        self.assertIn("requestBody", document["paths"]["/api/finance/loans/"]["post"])
        self.assertIn("required", document["components"]["schemas"]["LoanCreateRequest"])
        self.assertIn("monthlyPayment", document["components"]["schemas"]["Loan"]["properties"])
        self.assertNotIn("monthly_payment", document["components"]["schemas"]["Loan"]["properties"])
        error_examples = document["paths"]["/api/finance/connect/cards/"]["get"]["responses"]["403"]["content"]["application/json"]["examples"]
        self.assertIn("not_authenticated", error_examples)
        self.assertIn("머니쿠션", response.content.decode())

    def test_runtime_document_contains_mock_gets_and_implemented_operations(self):
        response = self.client.get("/api/schema/runtime/")
        self.assertEqual(response.status_code, 200)
        runtime = response.json()
        self.assertEqual(runtime, build_runtime_schema())
        savings = {path: item for path, item in self.contract["paths"].items() if path.startswith("/api/budgets/savings/")}
        self.assertSetEqual(set(runtime["paths"]), {
            "/api/csrf/", "/api/home/", "/api/finance/transactions/", "/api/budgets/fixed/", *savings,
        })
        self.assertEqual(runtime["security"], [])
        for path, method, operation in operation_items(runtime):
            with self.subTest(path=path, method=method):
                self.assertEqual(operation["security"], [])
                if path in savings:
                    # 구현된 API는 확정 계약의 모든 방식을 실행할 수 있고, CSRF 헤더는 화면이 자동으로 넣는다.
                    self.assertEqual(operation["x-implementation-status"], "implemented")
                    self.assertFalse(any(p.get("$ref", "").endswith("/CsrfToken") for p in operation.get("parameters", [])))
                else:
                    self.assertEqual(method, "get")
        for path in savings:
            self.assertSetEqual({m for m in runtime["paths"][path] if m in HTTP_METHODS}, {m for m in savings[path] if m in HTTP_METHODS})
        self.assertNotIn("/api/budgets/", runtime["paths"])
        self.assertNotIn("/api/budgets/fixed/{id}/", runtime["paths"])

    def test_runtime_references_resolve_to_included_components(self):
        runtime = build_runtime_schema()
        text = json.dumps(runtime["paths"]) + json.dumps(runtime["components"])
        for ref in set(re.findall(r'"\$ref": "(#/components/[^"]+)"', text)):
            with self.subTest(ref=ref):
                value = runtime
                for segment in ref.removeprefix("#/").split("/"):
                    self.assertIn(segment, value)
                    value = value[segment]

    def test_runtime_response_examples_match_the_actual_http_payloads(self):
        runtime = self.client.get("/api/schema/runtime/").json()
        for path, methods in runtime["paths"].items():
            if path.startswith(("/api/budgets/savings/", "/api/finance/transactions/")):
                continue  # 구현된 API: 로그인 · DB 가 필요한 실제 응답이라 샘플 예시와 비교하지 않는다
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                expected = methods["get"]["responses"]["200"]["content"]["application/json"]["example"]
                self.assertEqual(response.json(), expected)
                if path == "/api/csrf/":
                    self.assertIn("csrftoken", response.cookies)
                    self.assertEqual(response.json(), {"ok": True})

    def test_swagger_pages_use_local_assets_and_the_correct_execution_mode(self):
        for path, schema_url, submit_methods in (
            ("/api/docs/", "/api/schema/", []),
            ("/api/docs/runtime/", "/api/schema/runtime/", ["get", "post", "patch", "delete"]),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, schema_url)
                self.assertContains(response, "구현 8개")
                self.assertContains(response, "목업 7개")
                self.assertContains(response, "미구현 20개")
                self.assertContains(response, "/api/docs/runtime/")
                options = json.loads(response.data["settings"])
                self.assertEqual(options["supportedSubmitMethods"], submit_methods)
                self.assertIsNone(options["validatorUrl"])
                self.assertFalse(options["persistAuthorization"])
                self.assertFalse(options["queryConfigEnabled"])
                html = response.content.decode()
                assets = re.findall(r'(?:src|href)="([^"]*drf_spectacular_sidecar/[^"]+)"', html)
                self.assertGreaterEqual(len(assets), 3)
                for asset in assets:
                    self.assertTrue(asset.startswith("/static/"), asset)
                    self.assertTrue(finders.find(asset.removeprefix("/static/")), asset)
                self.assertNotRegex(html, r'<script[^>]+src="https?://')

    def test_schema_urls_reject_non_get_methods_as_json_405(self):
        for path in SCHEMA_URLS:
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
                with self.subTest(path=path, method=method):
                    response = self.client.generic(method, path)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response["Allow"], "GET")
                    self.assertEqual(response["Content-Type"], "application/json")
                    if method != "HEAD":
                        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")

    def test_swagger_urls_do_not_accept_mutations(self):
        for path in DOCS_URLS:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(path=path, method=method):
                    response = self.client.generic(method, path)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response.data["error"]["code"], "method_not_allowed")

    def test_unknown_schema_docs_and_original_api_routes_keep_json_404(self):
        for path in (
            "/api/schema/missing/", "/api/docs/missing/", "/api/does-not-exist/",
            "/api/finance/transactions/not-a-uuid/", "/api/schema", "/api/docs",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                error = response.json()["error"]
                self.assertEqual(error["code"], "not_found")
                self.assertNotIn("fields", error)
                if not path.endswith("/"):
                    self.assertIn("주소 끝에 /", error["message"])
