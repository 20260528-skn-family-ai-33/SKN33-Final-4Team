"""청년 정책 API (docs/api/policies.md). 지금은 저장한 정책(북마크)만 있다"""
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from finance.inputs import body_dict, reject_unknown_fields

from . import regions as region_table, saved
from .models import Policy


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def saved_list(request):
    """저장한 정책 목록 (최신순). 마감 · 수집 종료된 정책도 상태와 함께 남는다"""
    return Response({"items": saved.items(request.user, timezone.localdate())})


@api_view(["PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def saved_detail(request, policy_no):
    """PUT = 저장 (이미 저장돼 있어도 200), DELETE = 저장 취소 (저장돼 있지 않아도 204).
    정책번호가 공식 번호라 주소 속 ID 가 UUID 가 아니다 (policy 표의 예외). 없는 정책은 404"""
    if request.method == "DELETE":
        if not Policy.objects.filter(pk=policy_no).exists():
            raise NotFound()
        saved.unsave(request.user, policy_no)
        return Response(status=204)
    if request.body:  # 본문은 없어도 된다. 보냈다면 빈 {} 만
        reject_unknown_fields(body_dict(request), set())
    row, _ = saved.save(request.user, policy_no)
    if row is None:  # 없는 정책 · 수집이 끝난(비활성) 정책
        raise NotFound()
    return Response(saved.item(row, timezone.localdate()))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def regions(request):
    """사는 지역 드롭다운 (온보딩 · 내 정보). 시도 → 시군구. 명세 docs/api/policies.md"""
    return Response({"items": region_table.tree()})
