"""
혜리 담당 API 중 아직 껍데기인 것 — 10. 연결 해제
- CODEF 계정에서 그 기관을 빼는 동작이 정해지면(finance-connect.md '연결 해제' 확인 필요) 진짜로 바꾼다.
- 6~9 · 16~18 은 실제 구현으로 옮겼다 (finance/api_connect.py · finance/api.py, 10/26)
"""
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["DELETE"])
def connection_disconnect(request, connection_id):
    """10. DELETE /api/finance/connections/<id>/ — 연결 해제 (204, 본문 없음). 아직 아무것도 바꾸지 않음"""
    return Response(status=status.HTTP_204_NO_CONTENT)
