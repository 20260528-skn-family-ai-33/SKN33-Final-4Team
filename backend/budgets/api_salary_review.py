"""Shared, owner-scoped salary review for MyPage and the paused budget conversation."""
import uuid

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat.models import Conversation
from config.idempotency import idempotent
from finance.inputs import body_dict, reject_unknown_fields
from .salary_redistribution import preview, public_review, review


def conversation_for(request, data):
    value = data.get('conversation_id')
    if value is None:
        return None
    try:
        key = uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise ValidationError({'conversation_id': ['대화 번호를 확인해 주세요.']}) from None
    return get_object_or_404(Conversation.objects.select_for_update(), user=request.user, pk=key)


@idempotent(required=True)
def create_preview(request):
    data = body_dict(request)
    reject_unknown_fields(data, {'conversation_id', 'basis_hash', 'saving_percent', 'request_only'})
    if not isinstance(data.get('basis_hash'), str) or not data['basis_hash']:
        raise ValidationError({'basis_hash': ['검토 기준이 필요해요. 화면을 다시 확인해 주세요.']})
    if type(data.get('saving_percent')) is not int or not 0 <= data['saving_percent'] <= 100:
        raise ValidationError({'saving_percent': ['0부터 100 사이의 비율을 입력해 주세요.']})
    if type(data.get('request_only', False)) is not bool:
        raise ValidationError({'request_only': ['요청만 반영할지 확인해 주세요.']})
    if data.get('request_only') and data['saving_percent'] != 0:
        raise ValidationError({'saving_percent': ['요청만 이어갈 때는 추가 저축 비율을 0%로 정해 주세요.']})
    return Response(preview(request.user, data, conversation_for(request, data)))


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def salary_review(request):
    from .api import lock_user
    from users.privacy import require_bank_not_revoked
    with transaction.atomic():
        lock_user(request.user)
        require_bank_not_revoked(request.user)
        if request.method == 'POST':
            return create_preview(request)
        data = {'conversation_id': request.query_params.get('conversationId', request.query_params.get('conversation_id'))}
        return Response(public_review(review(request.user, conversation_for(request, data))))
