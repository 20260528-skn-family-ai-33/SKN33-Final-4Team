"""화면에서 돈을 보기 좋게 쓰는 필터. 사용: {% load money %} → {{ 2800000|won }} = 2,800,000원"""
from django import template

register = template.Library()


@register.filter
def comma(value):
    """2800000 → 2,800,000"""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value


@register.filter
def won(value):
    """2800000 → 2,800,000원"""
    return f"{comma(value)}원"
