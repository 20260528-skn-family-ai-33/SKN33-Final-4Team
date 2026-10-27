"""확정 API 계약과 현재 서버 응답을 별도로 보여 주는 Swagger 화면."""

import json

from django.http import JsonResponse
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from .openapi import build_contract, build_runtime_schema


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _schema_response(request, schema_builder, filename):
    if request.method != "GET":
        response = JsonResponse(
            {"error": {"code": "method_not_allowed", "message": "GET 요청만 사용할 수 있어요."}},
            status=405,
            json_dumps_params={"ensure_ascii": False},
        )
        response["Allow"] = "GET"
        return response
    # OpenAPI 예약 키(requestBody, operationId 등)는 API의 camelCase 변환을 거치지 않는다.
    response = JsonResponse(schema_builder(), json_dumps_params={"ensure_ascii": False})
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def contract_schema(request):
    """미구현 API까지 포함한 확정 계약. 데이터베이스를 조회하지 않는다."""
    return _schema_response(request, build_contract, "moneycushion-openapi.json")


def runtime_schema(request):
    """현재 호출 가능한 계약 범위의 GET과 실제 목업 응답 구조."""
    return _schema_response(request, build_runtime_schema, "moneycushion-openapi-runtime.json")


class MoneyCushionSwaggerView(SpectacularSwaggerView):
    template_name = "config/swagger_ui.html"
    template_name_js = "config/swagger_ui.js"
    authentication_classes = []
    permission_classes = [AllowAny]
    runtime = False

    @extend_schema(exclude=True)
    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        operations = [
            operation
            for path in build_contract()["paths"].values()
            for method, operation in path.items()
            if method in HTTP_METHODS
        ]
        runtime_count = sum(
            method in HTTP_METHODS
            for path in build_runtime_schema()["paths"].values()
            for method in path
        )
        response.data.update(
            {
                "runtime": self.runtime,
                "contract_count": len(operations),
                "runtime_count": runtime_count,
                "implemented_count": sum(op.get("x-implementation-status") == "implemented" for op in operations),
                "mock_count": sum(op.get("x-implementation-status") == "mock" for op in operations),
                "planned_count": sum(op.get("x-implementation-status") == "planned" for op in operations),
                "contract_docs_url": reverse("api:swagger-ui"),
                "runtime_docs_url": reverse("api:swagger-ui-runtime"),
                "download_filename": "moneycushion-openapi-runtime.json" if self.runtime else "moneycushion-openapi.json",
                "settings": json.dumps(
                    {
                        "supportedSubmitMethods": ["get", "post", "put", "patch", "delete"] if self.runtime else [],
                        "validatorUrl": None,
                        "persistAuthorization": False,
                        "withCredentials": False,
                        "queryConfigEnabled": False,
                        "filter": True,
                        "deepLinking": True,
                        "displayRequestDuration": True,
                        "docExpansion": "list",
                        "defaultModelsExpandDepth": -1,
                    }
                ),
            }
        )
        return response
