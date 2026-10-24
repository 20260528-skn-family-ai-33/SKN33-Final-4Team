"""
budgets 앱 API: React 화면에 필요한 데이터를 JSON으로 준다.
지금은 mock.py(가짜 데이터)를 그대로 보낸다. DB가 생기면 이 파일 안쪽만 바꾸면 된다.
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import mock


def _must_pay():
    """'꼭 나갈 돈' — link 는 누르면 갈 React 화면 주소 (없으면 null = 카드 청구서 팝업)"""
    return [{**p, "link": p.get("link")} for p in mock.MUST_PAY]


@api_view(["GET"])
def home(request):
    """GET /api/home/ — 홈 화면 데이터"""
    return Response({
        "period": mock.PERIOD,
        "home": mock.HOME,
        "must_pay": _must_pay(),
        # H-06: 1차는 항상 null(예산 API 는 ML 후). 시안은 mock.BUDGET 이 None 일 때만 null 을 보내 안내 카드를 보여 준다
        "budget": None if mock.BUDGET is None else {"sample": True},
    })


@api_view(["GET"])
def budget(request):
    """GET /api/budgets/ — 카테고리 예산 화면 데이터"""
    return Response({
        "period": mock.PERIOD,
        "monthly": mock.MONTHLY,
        "month_split": mock.MONTH_SPLIT,
        "categories": mock.CATEGORIES,
        "living_used": mock.HOME["living_used"],
        "living_budget": mock.HOME["living_budget"],
        "unclassified": mock.UNCLASSIFIED,
    })


@api_view(["GET"])
def cushion(request):
    """GET /api/budgets/cushion/ — 머니 쿠션 화면 데이터"""
    return Response({"cushion": mock.CUSHION})

# ======== 껍데기 API (2026-10-24) — 명세 예시 응답을 그대로 돌려준다 ========
from . import examples


# ---- 고정지출 19~24 ----
@api_view(["GET", "POST"])
def fixed_list(request):
    if request.method == "GET":
        return Response(examples.FIXED_LIST_EXAMPLE)               # 19 목록 보기
    return Response(examples.FIXED_CREATED_EXAMPLE, status=201)    # 20 직접 추가


@api_view(["PATCH", "DELETE"])
def fixed_detail(request, recurring_id):
    if request.method == "PATCH":
        return Response(examples.FIXED_UPDATED_EXAMPLE)            # 21 수정
    return Response(status=204)                                    # 22 중지 (본문 없음)


@api_view(["POST"])
def fixed_confirm(request):
    return Response(examples.FIXED_CONFIRM_EXAMPLE)                # 23 후보 확정


@api_view(["POST"])
def fixed_ignore(request):
    return Response(examples.FIXED_IGNORE_EXAMPLE)                 # 24 후보 무시


# ---- 정기 저축 25~30 ----
@api_view(["GET", "POST"])
def savings_list(request):
    if request.method == "GET":
        return Response(examples.SAVINGS_LIST_EXAMPLE)             # 25 목록 보기
    return Response(examples.SAVINGS_CREATED_EXAMPLE, status=201)  # 26 직접 추가


@api_view(["PATCH", "DELETE"])
def savings_detail(request, saving_id):
    if request.method == "PATCH":
        return Response(examples.SAVINGS_UPDATED_EXAMPLE)          # 27 수정
    return Response(status=204)                                    # 28 중지 (본문 없음)


@api_view(["POST"])
def savings_confirm(request):
    return Response(examples.SAVINGS_CONFIRM_EXAMPLE)              # 29 후보 확정


@api_view(["POST"])
def savings_ignore(request):
    return Response(examples.SAVINGS_IGNORE_EXAMPLE)               # 30 후보 무시