"""
요청 본문 검사 도구 (finance API 공통)
"""
from rest_framework.exceptions import ValidationError

NOT_ALLOWED = "이 칸은 보낼 수 없어요."  # 결정기록 ★2: 표에 없는 칸을 보내면 400


def body_dict(request):
    """요청 본문이 {...} 객체인지 확인하고 돌려준다 (목록 · 글자 등이면 400)"""
    if not isinstance(request.data, dict):
        raise ValidationError({"non_field_errors": ["요청 형식을 확인해 주세요."]})
    return request.data


def reject_unknown_fields(data, allowed):
    """명세 표에 없는 칸이 오면 400 — fields.<그 칸>: ["이 칸은 보낼 수 없어요."]
    data 의 키는 SnakeJSONParser 가 이미 snake_case 로 바꿔 둔 상태 (documentHash → document_hash)"""
    unknown = [key for key in data if key not in allowed]
    if unknown:
        raise ValidationError({key: [NOT_ALLOWED] for key in unknown})
