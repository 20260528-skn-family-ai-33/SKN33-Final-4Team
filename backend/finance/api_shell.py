"""
혜리 담당 API 껍데기 (2026-10-24) — 금융 동의 · 연결 6~10, 거래 17 · 18
- 주소 · 방식은 명세(docs/api/finance-connect.md · transactions.md)와 같고,
  응답은 명세의 예시 JSON(finance/shell_examples.json)을 그대로 돌려준다.
- DB · 계산 · 로그인 확인 · 입력 검사 없음. 진짜 기능은 나중에 함수 속만 바꾼다.
- 16 거래 목록은 지금 화면이 쓰는 finance/api.py 의 transactions 에 예시 칸을 더해 둠
"""
import copy
import json
from pathlib import Path

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.idempotency import idempotent

EXAMPLES = json.loads(Path(__file__).with_name("shell_examples.json").read_text(encoding="utf-8"))


def example(name):
    """명세 예시 응답 하나를 복사해서 준다 (원본이 바뀌지 않게)"""
    return copy.deepcopy(EXAMPLES[name])


@api_view(["POST"])
@idempotent()
def consents(request):
    """6. POST /api/finance/consents/ — 금융 조회 동의 저장 (201)"""
    return Response(example("consents_create"), status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT"])
def connect_accounts(request):
    """7. GET 계좌 후보 보기 · 8. PUT 계좌 선택 저장 — /api/finance/connect/accounts/
    8의 응답은 명세상 7과 같은 모양이라 7 예시를 그대로 준다"""
    return Response(example("connect_accounts"))


@api_view(["GET"])
def connections(request):
    """9. GET /api/finance/connections/ — 연결 목록 보기"""
    return Response(example("connections"))


@api_view(["DELETE"])
def connection_disconnect(request, connection_id):
    """10. DELETE /api/finance/connections/<id>/ — 연결 해제 (204, 본문 없음)"""
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET", "PATCH"])
def transaction_detail(request, transaction_id):
    """17. GET 거래 한 건 보기 · 18. PATCH 거래 분류 수정 — /api/finance/transactions/<id>/"""
    if request.method == "PATCH":
        return Response(example("transaction_patch"))
    return Response(example("transaction_detail"))
