"""Review only newly available salary, preserving the approved budget and saved request."""
from copy import deepcopy
from fractions import Fraction

from django.shortcuts import get_object_or_404
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from .adjustment_context import AdjustmentContextError, adjustment_today, context_fingerprint
from .adjustment_core.diagnostics import with_overrides
from .adjustment_core.funding import _data, available_bucket_source
from .adjustment_schedule import planning_context
from .adjustment_service import AdjustmentAdapter, AdjustmentExpired, proposal_card
from .models import Budget


REVIEW_POLICY = 'salary-request-first-ab-20261117-planned-spare'
REQUEST_FIELDS = ('requests', 'planning', 'scope', 'sources', 'destinations', 'excluded',
                  'spare_minimum', 'clear_targets', 'unlock_categories', 'unlock_consents',
                  'financial_overrides', 'one_off', 'expense_request', 'set_aside_amount')


def request_state(state):
    handoff = state.get('salary_handoff') or {}
    saved = {**{key: deepcopy(state[key]) for key in REQUEST_FIELDS if key in state},
             'items': deepcopy(handoff.get('items', state.get('requests', []))),
             'targets': deepcopy(handoff.get('targets', {}))}
    # A chosen example supplies its own sources; the pre-selection request holds the user's constraints.
    if state.get('plan_selected') and state.get('plan_request'):
        for field in ('sources', 'destinations'):
            saved[field] = list(state['plan_request'].get(field, []))
    return saved


def state_for(conversation):
    return deepcopy(((conversation.slots or {}).get('state') or {}).get('agent:budget', {})) if conversation else {}


def salary_review_needed(context, state):
    income = (context.get('monthly_income') if state.get('planning', {}).get('scope') == 'next_cycle'
              else context.get('effective_income', context.get('monthly_income')))
    return type(income) is int and income > context.get('funding_amount', income)


def income_context(base, proof):
    """Replay the same income/period at preview and approval; old spare is never a new source."""
    context = planning_context(base, proof.get('planning'))
    income = context['monthly_income'] if proof.get('planning') else base.get('effective_income', base['monthly_income'])
    increase = income - base['funding_amount']
    if increase <= 0:
        raise ValueError('salary_increase_changed')
    old_spare = context['buckets']['SPARE'] - increase if proof.get('planning') else context['buckets']['SPARE']
    if not proof.get('planning'):
        context['funding_amount'] = income
        context['buckets']['SPARE'] += increase
    context['salary_review'] = deepcopy(proof)
    context['salary_review']['policy'] = REVIEW_POLICY
    context['salary_review']['income_before'] = base['funding_amount']
    context['salary_review']['income_after'] = income
    # Old unknown balances stay unknown; only the confirmed salary increase is available here.
    known_spare = (base.get('bucket_available', {}).get('SPARE', 0)
                   if base.get('bucket_status', {}).get('SPARE') == 'ready' else 0)
    context['bucket_available']['SPARE'] = (max(old_spare, 0) if proof.get('planning') else known_spare) + increase
    context['bucket_status']['SPARE'] = 'ready'
    context['salary_review']['spare_anchor'] = max(old_spare, 0)
    context['salary_review']['spare_verified'] = bool(proof.get('planning') or base.get('bucket_status', {}).get('SPARE') == 'ready')
    context.setdefault('reserve_anchors', {})['SPARE'] = max(base.get('reserve_anchors', {}).get('SPARE', 0), old_spare)
    if proof.get('planning'):
        context['income_before'] = base['funding_amount']
    context.pop('context_hash', None)
    return with_overrides(context, proof.get('financial_overrides') or {})


def review_proposal(user, state, context, conversation=None):
    marker = state.get('salary_review_proposal') or {}
    if conversation is not None and not marker:
        return None
    income = context.get('monthly_income') if state.get('planning', {}).get('scope') == 'next_cycle' else context.get('effective_income', context.get('monthly_income'))
    candidates = Budget.objects.select_related('task').filter(
        user=user, cycle_id=context['cycle_id'], task__conversation=conversation, task__task_type='budget_adjust',
        status__in=['validated', 'approved', 'superseded'],
    ).filter(Q(base_budget_id=context['base_budget_id']) | Q(pk=context['base_budget_id']))
    if marker:
        candidates = candidates.filter(pk=marker.get('proposal_id'))
    request_hash = context_fingerprint(request_state(state))
    for budget in candidates.order_by('-version_no'):
        proof = (budget.input_snapshot or {}).get('adjustment_context', {}).get('salary_review', {})
        if proof.get('income_after') != income or proof.get('request_hash') != request_hash:
            continue
        card = proposal_card(user, budget, conversation=conversation)
        if card['state'] not in {'awaiting_approval', 'saved'}:
            continue
        if card['state'] == 'saved':
            if card.get('effective_on'):
                if card.get('schedule_status') not in {'scheduled', 'applied'}:
                    continue
            elif context.get('base_budget_id') != str(budget.pk):
                continue
        return card
    return None


def _spare_minimum(context, state):
    minimum = state.get('spare_minimum')
    planned = context.get('diagnostic_inputs', {}).get('planned_spare', {})
    if (planned.get('status') in {'confirmed', 'user_confirmed'} and planned.get('scope') == 'unbudgeted_only'
            and type(planned.get('amount')) is int and planned['amount'] >= 0):
        minimum = max(minimum or 0, planned['amount'])
    return minimum


def _stored_review(user, card):
    budget = Budget.objects.get(user=user, pk=card['proposal_id'])
    return deepcopy(budget.task.result.get('salary_review', {}))


def _shares(amount, weights, unit=5000):
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {code: int(Fraction(amount * weight, total) // unit) * unit for code, weight in weights.items()}


def _targets(context, state):
    saved = request_state(state)
    codes = [item.get('category') for item in saved['items']]
    if len(set(codes)) != len(codes) or ('LIVING' in codes and set(codes) & set(context['allocations'])):
        raise ValueError('overlapping_living_requests')
    before = {**context['allocations'], **context['buckets']}
    targets, explicit = {}, set()
    for item in saved['items']:
        code = item.get('category')
        target = saved['targets'].get(code)
        if target is None:
            if item.get('amount') is None:
                raise ValueError('request_amount_required')
            target = _data(context, {'items': [item]})['targets'][code]
        if code == 'SPARE':
            raise ValueError('spare_request_requires_review')
        current = sum(context['allocations'].values()) if code == 'LIVING' else before.get(code)
        if current is None:
            raise ValueError('request_category_required')
        if item.get('direction') == 'up':
            target = max(current, target)
        elif item.get('direction') == 'down':
            target = min(current, target)
        if code == 'LIVING':
            if target < current:
                raise ValueError('living_reduction_requires_review')
            weights = {c: n for c, n in context['allocations'].items()
                       if c not in {'ETC', 'TRAVEL_LODGING'} and c not in context['locks'] and c not in state.get('excluded', [])}
            shares = _shares(target - current, weights)
            remainder = target - current - sum(shares.values())
            if remainder and weights:
                shares[max(weights, key=weights.get)] += remainder
            if remainder and not weights:
                raise ValueError('living_targets_locked')
            targets.update({c: before[c] + n for c, n in shares.items()})
            explicit.update(context['allocations'])
        else:
            targets[code] = target
            explicit.add(code)
    return targets, explicit


def review(user, conversation=None, today=None):
    state = state_for(conversation)
    at = adjustment_today(today)
    if not Budget.objects.filter(user=user, status='approved', cycle__status='active',
                                 cycle__starts_on__lte=at, cycle__ends_before__gt=at).exists():
        return {'status': 'no_budget', 'message': '아직 승인한 예산이 없어요. 새 조건으로 예산을 먼저 정해 주세요.'}
    adapter = AdjustmentAdapter(user, conversation=conversation, today=today, mode='NORMAL_ADJUSTMENT')
    try:
        base = adapter.get_context()
    except AdjustmentContextError as error:
        missing = error.code in {'no_budget', 'no_cycle', 'profile_not_ready'}
        return {'status': 'no_budget' if missing else 'needs_input',
                'message': '먼저 현재 예산을 확인해 주세요.' if missing else '최신 예산 기준을 확인하지 못했어요. 기존 요청은 보관했어요.',
                'reason': error.code}
    previous = review_proposal(user, state, base, conversation)
    stored = _stored_review(user, previous) if previous else {}
    if not salary_review_needed(base, state):
        return {**stored,
                'status': 'no_increase', 'message': '현재 급여가 예산에 이미 반영되어 있어요.',
                **({'proposal': previous} if previous else {})}
    proof = {'planning': deepcopy(state.get('planning')), 'conversation_id': str(conversation.pk) if conversation else None,
             'request_hash': context_fingerprint(request_state(state)),
             'financial_overrides': deepcopy(state.get('financial_overrides', {}))}
    try:
        context = income_context(base, proof)
    except ValueError as error:
        return {'status': 'needs_input', 'reason': str(error),
                'message': '변경을 시작할 급여 주기를 확인해 주세요. 기존 요청은 보관했어요.'}
    prior = state.get('salary_handoff', {}).get('context')
    planned = proof['financial_overrides'].get('planned_spare', {})
    if (prior and planned.get('status') in {'confirmed', 'user_confirmed'}
            and (prior.get('cycle_id'), prior.get('effective_on')) != (context.get('cycle_id'), context.get('effective_on'))):
        return {'status': 'needs_input', 'reason': 'planned_expense_period_changed',
                'message': '예산 주기가 바뀌었어요. 챗봇에서 이 주기에 따로 쓸 예정인 금액을 확인해 주세요. 기존 요청은 보관했어요.'}
    increase = context['funding_amount'] - base['funding_amount']
    before = {**context['allocations'], **base['buckets']}
    result = {'status': 'ready', 'basis_hash': context_fingerprint(context),
              'income_before': base['funding_amount'], 'income_after': context['funding_amount'], 'increase': increase,
              'scope': 'next_cycle' if proof['planning'] else ('ongoing' if state.get('scope') == 'ongoing' else 'cycle'),
              'saving_percent': round(100 * base['buckets']['SAVINGS'] / max(base['funding_amount'] - base['fixed_amount'], 1)),
              'before': before, '_context': context, '_state': state, '_adapter': adapter}
    result['previous_saving_percent'] = result['saving_percent']
    if proof['planning']:
        result['effective_on'] = context['effective_on']
    if previous:
        result['proposal'] = previous
        result['saving_percent'] = stored.get('saving_percent', result['saving_percent'])
        result['summary'] = deepcopy(stored.get('summary', {}))
        result['request_only'] = stored.get('request_only', False)
        if previous['state'] == 'saved':
            result.update(status='no_increase', message='이 월급으로 검토한 예산 변경을 이미 예약했어요.')
            return result
    try:
        if set(request_state(state).get('sources', [])) - {'SPARE'}:
            raise ValueError('explicit_source_requires_review')
        if 'SPARE' in state.get('excluded', []):
            raise ValueError('excluded_source_requires_review')
        targets, explicit = _targets(context, state)
    except ValueError as error:
        messages = {
            'explicit_source_requires_review': '이전에 지정한 재원으로 가져오는 요청이 있어요. 늘어난 월급을 대신 사용할지 챗봇에서 정해 주세요. 지정한 재원은 그대로 보관했어요.',
            'excluded_source_requires_review': '여윳돈을 사용하지 않는 조건이 있어요. 늘어난 월급을 배분해도 되는지 챗봇에서 확인해 주세요. 제외 조건은 그대로 유지했어요.',
            'request_amount_required': '보관한 요청에서 조정할 금액이 아직 정해지지 않았어요. 챗봇에서 요청 금액을 먼저 정해 주세요.',
        }
        result.update(status='needs_input', reason=str(error), message=messages.get(str(error), '보관한 요청의 금액·재원 조건을 먼저 확인해 주세요. 기존 요청은 그대로 유지했어요.'))
        return result
    requested = sum(target - before[code] for code, target in targets.items())
    available = available_bucket_source(context, 'SPARE', spare_minimum=_spare_minimum(context, state))
    allocatable = min(increase, available['maximum']) if available['maximum'] is not None else increase
    if not previous and ('SAVINGS' in explicit or 'SAVINGS' in state.get('excluded', [])):
        result['saving_percent'] = 0
        result['message'] = ('요청한 저축 금액을 먼저 반영했어요. 추가 저축 없이 나머지를 나눠요.'
                             if 'SAVINGS' in explicit else '저축을 유지하는 조건에 맞춰 추가 저축 없이 나머지를 나눠요.')
    result.update(requested_amount=requested, remaining_amount=max(allocatable - requested, 0),
                  shortfall_amount=max(requested - allocatable, 0),
                  request_changes=[{'category': code, 'before': before[code], 'after': target, 'diff': target-before[code]}
                                   for code, target in targets.items() if target != before[code]],
                  _targets=targets, _explicit=explicit)
    if requested > allocatable:
        result.update(status='needs_input', message=f'지킬 금액을 남기면 요청한 예산에 {requested-allocatable:,}원이 부족해요. 요청 금액은 그대로 보관했어요.')
    elif allocatable < increase:
        result['message'] = f'예정 지출과 지킬 금액을 위해 증가분 중 {increase-allocatable:,}원은 여윳돈으로 남겨 둬요.'
    return result


def public_review(result):
    return {key: value for key, value in result.items() if not key.startswith('_')}


def preview(user, data, conversation=None, today=None):
    result = review(user, conversation, today)
    if result.get('basis_hash') != data['basis_hash']:
        raise AdjustmentExpired()
    if result['status'] != 'ready':
        return public_review(result)
    percent = data['saving_percent']
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValidationError({'saving_percent': ['0부터 100 사이의 비율을 입력해 주세요.']})
    if data.get('request_only') and percent != 0:
        raise ValidationError({'saving_percent': ['기존 요청만 이어갈 때는 추가 저축 비율을 0%로 정해 주세요.']})
    result['request_only'] = bool(data.get('request_only'))
    context, state, adapter = result['_context'], result['_state'], result['_adapter']
    before = {**context['allocations'], **context['buckets']}
    targets, explicit = deepcopy(result['_targets']), result['_explicit']
    remaining = result['remaining_amount']
    saving = remaining * percent // 100 // 5000 * 5000
    if 'SAVINGS' in explicit or 'SAVINGS' in state.get('excluded', []):
        if saving:
            raise ValidationError({'saving_percent': ['보관한 저축 금액을 유지하려면 추가 저축 비율을 0%로 정해 주세요.']})
    else:
        targets['SAVINGS'] = before['SAVINGS'] + saving
    from .salary_design import review_remaining
    assessment = ({'status': 'skipped', 'allocations': {}, 'unallocated': remaining,
                   'selection': None, 'reason': 'request_only'} if data.get('request_only') else
                  review_remaining(user, context, targets, explicit, remaining - saving,
                                   excluded=state.get('excluded', []), clear_targets=state.get('clear_targets', []),
                                   today=adjustment_today(today)))
    shares = assessment['allocations']
    for code, amount in shares.items():
        targets[code] = targets.get(code, before[code]) + amount
    unlock = []
    for code in state.get('unlock_categories', []):
        snapshot = {'amount': context['locks'].get(code), 'revision': context.get('lock_revisions', {}).get(code),
                    'base_budget_id': context['base_budget_id']}
        if state.get('unlock_consents', {}).get(code) == snapshot:
            unlock.append(code)
    calculation = deepcopy(context)
    for code in unlock:
        calculation['locks'].pop(code, None)
    envelope = {'items': [{'category': code, 'amount_mode': 'to', 'amount': amount, 'automatic': code not in explicit}
                          for code, amount in targets.items() if amount != before[code]],
                'sources': ['SPARE'], 'excluded': state.get('excluded', [])}
    minimum = _spare_minimum(context, state)
    if minimum is not None:
        envelope['spare_minimum'] = minimum
    calculated = adapter.simulate(calculation, envelope)
    if calculated['status'] not in {'proposed', 'unchanged'}:
        return {**public_review(result), 'status': 'needs_input', 'calculation': calculated,
                'message': '기존 보호 조건과 함께 맞출 수 있는 금액을 확인해 주세요. 요청과 기존 예산은 유지했어요.'}
    card = adapter.propose(context, calculated, scope=result['scope'], unlock_categories=unlock,
                           target_changes=(None if result['scope'] == 'ongoing' and not state.get('clear_targets')
                                           else {code: None for code in state.get('clear_targets', [])}))
    adapter.flush(conversation)
    unallocated = assessment['unallocated']
    if assessment['status'] == 'ready':
        message = (f'필요한 생활비에 {sum(shares.values()):,}원을 더 배정하고, 남은 {unallocated:,}원은 여윳돈으로 남겼어요.'
                   if unallocated else '기존 요청과 저축을 반영한 뒤, 필요한 생활비에 남은 금액을 배정했어요.')
    elif assessment['status'] == 'skipped':
        message = f'유지할 항목과 요청 금액을 지키고, 남은 {unallocated:,}원은 여윳돈으로 남겼어요.' if unallocated else ''
    else:
        message = f'요청과 저축은 반영했어요. 추가 배분 근거를 확인하지 못해 {unallocated:,}원은 여윳돈으로 남겨 뒀어요.'
    allocation_review = {'status': assessment['status'], 'plan': (assessment.get('selection') or {}).get('plan'),
                         'reason': assessment.get('reason'), 'message': message,
                         'allocated_amount': sum(shares.values()), 'unallocated_amount': unallocated}
    summary = {'saving_increase': saving, 'redistributed_amount': sum(shares.values()),
               'remaining_spare': calculated['buckets']['SPARE'], 'changes': card['changes'],
               'allocation_review': allocation_review}
    from chat.models import AsyncTask
    task = AsyncTask.objects.get(user=user, pk=card['task_ref'])
    task.result = {**task.result, 'salary_review': {
        **{key: value for key, value in public_review(result).items() if key not in {'proposal', 'summary'}},
        'saving_percent': percent, 'summary': summary}}
    task.save(update_fields=['result'])
    if conversation:
        state['salary_review_proposal'] = {'proposal_id': card['proposal_id'], 'task_ref': card['task_ref'],
            'income_after': result['income_after'], 'base_budget_id': context['base_budget_id'],
            'request_hash': context['salary_review']['request_hash']}
        slots = deepcopy(conversation.slots or {})
        slots.setdefault('state', {})['agent:budget'] = state
        slots['rev'] = slots.get('rev', 0) + 1
        conversation.slots = slots
        conversation.save(update_fields=['slots'])
    return {**public_review(result), 'saving_percent': percent, 'proposal': card,
            'summary': summary}


def fresh_income_context(user, base, saved):
    proof = saved['salary_review']
    if proof.get('policy') != REVIEW_POLICY:
        raise ValueError('salary_review_policy_changed')
    if proof.get('conversation_id'):
        from chat.models import Conversation
        talk = get_object_or_404(Conversation, user=user, pk=proof['conversation_id'])
        if context_fingerprint(request_state(state_for(talk))) != proof['request_hash']:
            raise ValueError('salary_request_changed')
    return income_context(base, {key: deepcopy(proof.get(key)) for key in
                                ('planning', 'conversation_id', 'request_hash', 'financial_overrides')})
