"""Explain saved budget facts without inferring new amounts or changing budgets."""
from django.db.models import Sum

from spending_analysis.budget import approved_budget
from users.privacy import require_bank_not_revoked

from .models import Budget, Category


def explain_current_budget(user, categories, today):
    require_bank_not_revoked(user)
    loaded = approved_budget(user, today)
    if loaded['state'] == 'error':
        return ['현재 예산을 불러오지 못했어요. 잠시 후 다시 확인해 주세요.']
    if loaded['state'] == 'no_cycle':
        return ['지금 사용 중인 예산이 없어요. 먼저 예산을 만들어 주세요.']
    budgets = Budget.objects.filter(user=user, cycle_id=loaded['cycle']['cycle_id'])
    if loaded['state'] == 'approved':
        budget = budgets.get(pk=loaded['budget']['budget_id'])
    else:
        budget = (budgets.filter(status='validated', base_budget__isnull=True)
                  .exclude(task__task_type='budget_adjust').order_by('-version_no').first())
    if budget is None:
        return ['아직 정해진 예산이 없어요. 먼저 추천 예산을 만들어 주세요.']
    amounts = dict(budget.items.filter(user=user, bucket='living').values('category_id')
                   .annotate(total=Sum('allocated_amount')).values_list('category_id', 'total'))
    names = dict(Category.objects.filter(pk__in=categories).values_list('pk', 'display_name'))
    origin, seen = budget, set()
    while origin.base_budget_id and origin.pk not in seen:
        seen.add(origin.pk)
        parent = budgets.filter(pk=origin.base_budget_id).first()
        if parent is None or parent.pk in seen:
            break
        origin = parent
    snapshot = origin.input_snapshot or {}
    inputs = snapshot.get('engine_input') or {}
    design = (origin.validation_summary or {}).get('design') or {}
    saved_reasons = {row['category']: row.get('text') for row in design.get('explanations', [])}
    adjustment = (budget.validation_summary or {}).get('adjustment') or {}
    changes = (adjustment.get('result') or {}).get('changes', [])
    lines = []
    status = '지금 사용 중인' if budget.status == 'approved' else '아직 확정하지 않은'
    for code in dict.fromkeys(categories):
        name = names.get(code, code)
        if code not in amounts:
            lines.append(f'{name}에는 따로 잡아 둔 생활비가 없어요.')
            continue
        lines.append(f'{status} {name} 예산은 {amounts[code]:,}원이에요.')
        facts = []
        if saved_reasons.get(code):
            facts.append(saved_reasons[code])
        plan = (design.get('selection') or {}).get('plan')
        if plan == 'a':
            facts.append('나이와 소득 등 생활 조건이 비슷한 사람들이 쓴 돈을 참고했어요. 꼭 남겨 둘 돈과 중요하게 고른 항목도 함께 고려해 예산을 나눴어요.')
        elif plan == 'b':
            facts.append('이전에 실제로 쓰신 돈을 참고했어요. 꼭 남겨 둘 돈과 중요하게 고른 항목도 함께 고려해 예산을 나눴어요.')
        for field in ('minimums', 'locks', 'category_targets'):
            value = (inputs.get(field) or {}).get(code)
            if type(value) is int and value >= 0:
                if field == 'minimums' and value > 0:
                    facts.append(f'{name}에는 적어도 {value:,}원을 남겨 두기로 하셨어요.')
                elif field == 'locks':
                    facts.append(f'{name} 예산은 {value:,}원으로 고정해 두셨어요.')
                elif field == 'category_targets':
                    facts.append(f'다음 예산을 만들 때도 {name}에 {value:,}원을 두기로 하셨어요.')
        for change in changes:
            if change.get('category') == code and change.get('after') == amounts[code] and type(change.get('before')) is int:
                facts.append(f'최근에 {change["before"]:,}원에서 {change["after"]:,}원으로 바꿨어요.')
        lines.extend(facts or ['예산을 정했을 때의 자세한 이유가 남아 있지 않아, 왜 이 금액이 됐는지는 확인할 수 없어요.'])
    return lines
