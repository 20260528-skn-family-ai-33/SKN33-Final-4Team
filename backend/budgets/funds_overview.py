"""Keep budget headroom, unallocated money and accumulated cushion separate."""
from django.utils import timezone

from .adjustment_context import AdjustmentContextError, get_adjustment_context
from .cushion import cushion_data


def funds_overview(user, today=None):
    today = today or timezone.localdate()
    cushion = cushion_data(user)['cushion']
    try:
        context = get_adjustment_context(user, today=today, allow_reference_backfill=False)
    except AdjustmentContextError:
        context = None
    lines = []
    if context:
        complete = context.get('spending_status') == 'complete'
        from .scope import OUT_OF_BUDGET
        living = sum(v for c, v in context['allocations'].items() if c not in OUT_OF_BUDGET) - sum(v for c, v in context.get('spent', {}).items() if c not in OUT_OF_BUDGET)
        lines.append(f'남은 생활비: {living:,}원 — 이미 생활비로 배정한 돈 중 남은 금액이에요.' if complete else
                     '남은 생활비: 지출 내역을 더 확인해야 해요.')
        spare = context['bucket_available'].get('SPARE')
        lines.append(f'미배정 여윳돈: {spare:,}원 — 다른 예산에 배정하거나 사용·예약한 돈을 제외한 금액이에요.'
                     if context['bucket_status'].get('SPARE') == 'ready' and spare is not None else
                     '미배정 여윳돈: 사용·예약 내역 확인이 필요해 지금 쓸 수 있는 금액을 확정할 수 없어요.')
    else:
        lines.append('남은 생활비·미배정 여윳돈: 이번 주기의 확정 예산을 먼저 확인해야 해요.')
    lines.append(f"쌓인 머니쿠션: {cushion['now']:,}원 — 비상용으로 따로 모은 돈이에요.")
    monthly = context['buckets']['CUSHION_MONTHLY'] if context else cushion.get('monthly')
    if monthly is not None:
        lines.append(f'이번 주기 머니쿠션 적립 예산: {monthly:,}원 — 이미 모인 쿠션과는 달라요.')
    lines.append('남은 생활비에는 앞으로 쓸 돈도 포함돼요. 별도로 모아 둔 여윳돈은 이 금액에 임의로 더하지 않았어요.')
    return lines
