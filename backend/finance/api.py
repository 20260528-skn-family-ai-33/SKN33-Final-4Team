"""
finance 앱 API: 거래 내역을 JSON으로 준다. ?category=식비 로 골라 보기
"""
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import examples, mock

PENDING = "확인 필요"  # AI가 카테고리를 확신 못 한 거래 묶음 이름
NOT_SPENDING = ("income", "transfer")  # 월급 입금 · 남에게 보낸 이체 — 목록엔 보이지만 소비 합계엔 안 들어감 (V①)


def net_amount(tx):
    """취소를 뺀 실제 소비 금액 (전체 취소 = 0, 부분취소 = 금액 - 취소 금액). 입금 · 이체는 원래 금액"""
    if tx.get("cancel") == "full":
        return 0
    return tx["amount"] - tx.get("cancel_amount", 0)


def filter_days(selected):
    """고른 카테고리의 거래만 남기고, 날짜별 합계를 다시 계산한다"""
    def keep(tx):
        if selected == "전체":
            return True
        if selected == PENDING:
            return tx["kind"] == "pending"
        return tx.get("cat") == selected

    days = []
    for day in mock.TX_DAYS:
        items = [{**tx, "net": net_amount(tx)} for tx in day["items"] if keep(tx)]
        if items:
            # 날짜 합계는 소비만 (입금 · 이체 제외)
            total = sum(tx["net"] for tx in items if tx["kind"] not in NOT_SPENDING)
            days.append({"date": day["date"], "items": items, "total": total})
    return days


@api_view(["GET"])
def transactions(request):
    """GET /api/finance/transactions/?category=식비 — 거래 내역 데이터"""
    selected = request.query_params.get("category", "전체")
    return Response({
        "month": mock.MONTH,
        "recent": mock.RECENT,
        "pending_count": mock.PENDING_COUNT,
        "pending_label": PENDING,
        "categories": mock.CATEGORIES,
        "selected": selected,
        "days": filter_days(selected),
    })


# ======== 껍데기 API (2026-10-24) — DB 변경 없이 고정 예시 응답 ========
# 16번은 위 기존 화면용 목업을 유지한다. 인증/입력 검증/멱등성은 구현 전이다.
@api_view(["POST"])
def consents(request):
    return Response(examples.CONSENTS_EXAMPLE, status=201)  # 6 동의 저장


@api_view(["GET", "PUT"])
def connect_accounts(request):
    return Response(examples.ACCOUNTS_EXAMPLE)  # 7 후보 / 8 선택 저장


@api_view(["GET"])
def connections(request):
    return Response(examples.CONNECTIONS_EXAMPLE)  # 9 연결 목록


@api_view(["DELETE"])
def connection_detail(request, connection_id):
    return Response(status=204)  # 10 연결 해제 (실제로 해제하지 않음)


@api_view(["GET", "PATCH"])
def transaction_detail(request, transaction_id):
    # UUID 형식은 URL에서 검사. 존재 여부·소유권 검사는 실제 구현 때 추가한다.
    example = (
        examples.TRANSACTION_UPDATED_EXAMPLE
        if request.method == "PATCH"
        else examples.TRANSACTION_EXAMPLE
    )
    return Response({**example, "transactionId": str(transaction_id)})  # 17 상세 / 18 수정
