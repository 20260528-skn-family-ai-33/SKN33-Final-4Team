"""검수된 조정안 조회·승인. 예산 계산은 서버의 동일 계산기로 다시 검사한다."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.idempotency import idempotent
from finance.inputs import body_dict, reject_unknown_fields

from .models import Budget


def is_adjustment(budget):
    return bool(budget.base_budget_id or budget.task_id and budget.task.task_type == "budget_adjust")


def approve_response(request, budget_id, *, expected_mode):
    from users.privacy import require_bank_not_revoked
    from .api import lock_user
    from .adjustment_service import AdjustmentExpired, approve_adjustment
    from .adjustment_context import adjustment_today
    from .overview import budget_data

    data = body_dict(request)
    reject_unknown_fields(data, {"keep_for_next_cycle"})
    keep = data.get("keep_for_next_cycle", False)
    if type(keep) is not bool:
        raise ValidationError({"keep_for_next_cycle": ["다음 달 유지 여부는 참 또는 거짓으로 보내 주세요."]})
    with transaction.atomic():
        lock_user(request.user)
        require_bank_not_revoked(request.user)
        chosen = get_object_or_404(Budget.objects.select_for_update(of=("self",)).select_related("task"),
                                   pk=budget_id, user=request.user)
        if not is_adjustment(chosen):
            from django.http import Http404
            raise Http404
        try:
            approval = approve_adjustment
            if (chosen.validation_summary or {}).get('design_recovery'):
                from .design_recovery_plans import approve
                approval = approve
            card = approval(request.user, chosen, keep_for_next_cycle=keep,
                                      key=request.headers["Idempotency-Key"], expected_mode=expected_mode,
                                      today=adjustment_today())
        except AdjustmentExpired as error:
            return Response({"error": {"code": "expired", "message": str(error.detail)}}, status=409)
        chosen.refresh_from_db()
        return Response({"adjustment": card,
                         "budget": budget_data(request.user, adjustment_today(), **({} if card.get('effective_on') else {'selected': chosen}))})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@idempotent(required=True, commit_error_codes=("expired",))
def proposal_approve(request, proposal_id):
    return approve_response(request, proposal_id, expected_mode="NORMAL_ADJUSTMENT")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def proposal_detail(request, proposal_id):
    from django.http import Http404
    from .adjustment_service import proposal_card
    chosen = get_object_or_404(Budget.objects.select_related("task"), pk=proposal_id, user=request.user)
    if not is_adjustment(chosen):
        raise Http404
    return Response(proposal_card(request.user, chosen))
