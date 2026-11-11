"""Recover a failed recommendation with verified proposals and explicit user approval."""
from copy import copy, deepcopy

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException

from chat.models import AsyncTask
from users.models import FinancialProfile
from users.privacy import require_bank_not_revoked

from .models import Budget, BudgetCycle
from .recommend import ENGINE_VERSION, POLICY_VERSION, budget_input_snapshot, hash_snapshot
from .salary import salary_cycle_dates


class RecoveryExpired(APIException):
    status_code = 409
    default_code = 'expired'
    default_detail = '예산 조건이나 주기가 바뀌었어요. 다시 추천받은 뒤 조정을 시작해 주세요.'


def read_design_handoff(user, source_task_id, today, *, for_update=False, allow_resolved=False):
    from . import design_service

    require_bank_not_revoked(user)
    tasks = AsyncTask.objects.filter(user=user, task_type='budget_recommend')
    if for_update:
        tasks = tasks.select_for_update()
    task = get_object_or_404(tasks, pk=source_task_id)
    data = task.result if isinstance(task.result, dict) else {}
    handoff = data.get('_design_handoff')
    if (task.status != 'succeeded' or not isinstance(handoff, dict)
            or data.get('budget_id') is not None or (data.get('handoff') != {'mode': 'DESIGN_RECOVERY', 'source_task_id': str(task.pk)}
                and not (allow_resolved and handoff.get('resolved_budget_id')))
            or handoff.get('schema_version') != 'budget-design-handoff-v1'
            or handoff.get('source_task_id') != str(task.pk) or handoff.get('input_hash') != task.input_hash
            or not isinstance(handoff.get('issue'), dict)
            or data.get('outcome') != handoff['issue'].get('status')):
        raise RecoveryExpired()
    if (not isinstance(handoff.get('versions'), dict) or not isinstance(handoff.get('constraints'), dict)
            or any(k not in handoff['constraints'] for k in ('minimums', 'locks', 'category_targets'))
            or not isinstance(handoff.get('priorities'), list)):
        raise RecoveryExpired()
    if handoff.get('resolved_budget_id'):
        if not allow_resolved:
            raise RecoveryExpired()
        get_object_or_404(Budget, pk=handoff['resolved_budget_id'], user=user, approved_at__isnull=False,
                         validation_summary__design_recovery__source_task_id=str(task.pk))
        return task, deepcopy(handoff)
    cycle = get_object_or_404(BudgetCycle, pk=handoff['cycle_id'], user=user)
    profile = FinancialProfile.objects.filter(user=user).first()
    if (profile is None or profile.payday is None or cycle.status != 'active' or not cycle.starts_on <= today < cycle.ends_before
            or BudgetCycle.objects.filter(user=user, starts_on__lte=today, ends_before__gt=today).exclude(pk=cycle.pk).exists()):
        raise RecoveryExpired()
    effective = copy(profile)
    if profile.payday_changes_on and today >= profile.payday_changes_on:
        effective.payday = profile.scheduled_payday
    if effective.payday is None or salary_cycle_dates(effective, today)[:2] != (cycle.starts_on, cycle.ends_before):
        raise RecoveryExpired()
    versions = handoff['versions']
    if (versions.get('engine') != ENGINE_VERSION or versions.get('policy') != POLICY_VERSION
            or versions.get('policy_fingerprint') != design_service.fingerprint(design_service.configured_policy())):
        raise RecoveryExpired()
    current = budget_input_snapshot(user, cycle, profile=effective, today=today, card=False)
    if (hash_snapshot(current) != task.input_hash
            or any(current[k] != handoff['constraints'][k] for k in ('minimums', 'locks', 'category_targets'))
            or current['engine_input']['priorities'] != handoff['priorities']
            or Budget.objects.filter(user=user, cycle=cycle, status__in=('validated', 'approved'), created_at__gt=task.created_at)
                .exclude(validation_summary__design_recovery__source_task_id=str(task.pk)).exists()):
        raise RecoveryExpired()
    return task, deepcopy(handoff)


class DesignRecoveryAdapter:
    """Stage verified recovery plans; persist only after the chat revision check."""
    def __init__(self, user, source_task_id, today):
        self.user, self.source_task_id, self.today = user, source_task_id, today
        self.task, self.handoff = read_design_handoff(user, source_task_id, today, allow_resolved=True)
        self.plan, self.bot, self.store, self.state, self.key = None, None, None, None, None
        self.cancel_id = None

    def reply(self, store, key, text):
        from chat.router_core import CANCEL_WORDS
        from . import design_recovery_plans as plans

        state = store.get_agent_state(key, 'budget') or {}
        self.store, self.state, self.key = store, state, key
        card = None
        resolved = self.handoff.get('resolved_budget_id')
        if resolved:
            card = plans.proposal_card(self.user, get_object_or_404(Budget, pk=resolved, user=self.user), today=self.today)
            store.save_agent_state(key, 'budget', {})
            store.set_active(key, None)
            return {'agent': 'budget', 'inputs': [], 'result': False, 'lines': ['승인한 예산에 이미 반영했어요.'], 'choices': [],
                    'budget_adjustment': {'status': 'saved', 'can_approve': False, 'proposal': card}}, {
                        'intent': 'budget', 'slots': {}, 'fallback': False}
        if (text or '').strip(' .!~') in CANCEL_WORDS:
            self.cancel_id = state.get('recovery_proposal_id')
            store.save_agent_state(key, 'budget', {})
            store.set_active(key, None)
            lines, status, choices = ['예산 조정을 그만뒀어요. 기존 설정은 그대로예요.'], 'ended', []
        else:
            scope = {'이번 주기만 필요해요': 'cycle', '계속 필요한 금액이에요': 'ongoing'}.get(text)
            if scope:
                state['recovery_scope'] = scope
            funding = {'저축 여유분도 검토해요': 'savings', '월 쿠션 여유분도 검토해요': 'cushion',
                       '저축·월 쿠션을 함께 검토해요': 'extended', '여윳돈으로 다시 계산해요': 'spare'}.get(text)
            requested = bool(scope or funding or text == '조건을 유지해서 해결안 보기')
            state['pending'] = {'kind': 'design_recovery'}
            store.save_agent_state(key, 'budget', state)
            store.set_active(key, 'budget')
            issue = self.handoff['issue']
            needs_money = issue['kind'] == 'funding_shortfall' or issue['shortfall'] is not None
            if issue['reason'] == 'lock_below_minimum':
                lead = '잠금 금액과 최소금액이 서로 충돌해요. 두 설정을 그대로 두면 예산을 만들 수 없어요.'
            elif issue['reason'] == 'locks_mismatch_living' and not needs_money:
                lead = '모든 항목이 잠겨 있어 남는 생활비를 배분할 곳이 없어요.'
            elif issue['kind'] == 'target_conflict':
                lead = '유지 목표를 최소금액·잠금·금액 단위와 함께 맞출 수 없어요.'
            else:
                lead = '현재 조건에서는 생활비 배분을 다시 검토해야 해요.'
            question = ('필요한 금액이 이번 주기에만 필요한가요, 앞으로도 계속 필요한가요?' if needs_money else
                        '설정은 임의로 바꾸지 않을게요. 어떤 의도로 정한 금액인지 알려 주세요.')
            lines = [lead, question] if not scope else ['적용 범위를 확인했어요. 최소금액·잠금·우선순위는 그대로 유지해요.']
            status = 'awaiting_input'
            choices = [] if scope or not needs_money else [{'value': 'recovery_cycle', 'label': '이번 주기만 필요해요'},
                                       {'value': 'recovery_ongoing', 'label': '계속 필요한 금액이에요'}]
            if not needs_money and issue['reason'] != 'lock_below_minimum':
                choices = [{'value': 'recovery_review', 'label': '조건을 유지해서 해결안 보기'}]
            if requested:
                result = plans.calculate(self.user, self.task, self.handoff, self.today,
                                         scope=state.get('recovery_scope', 'cycle'), funding=funding or 'spare')
                if result['status'] == 'proposed':
                    self.plan = result
                    state.pop('pending', None)
                    lines, choices, status = ['조건을 지키는 해결안을 계산했어요. 확인 후 승인해 주세요.'], [], 'awaiting_approval'
                elif result.get('reason') == 'funding_insufficient':
                    lines = [f"선택한 재원으로 계산하면 생활비가 {result['shortfall']:,}원 부족해요."]
                    options = [('savings', '저축 여유분도 검토해요'), ('cushion', '월 쿠션 여유분도 검토해요'),
                               ('extended', '저축·월 쿠션을 함께 검토해요')]
                    choices = [{'value': f'recovery_{code}', 'label': label} for code, label in options
                               if code != funding and funding != 'extended']
                elif result.get('reason') in ('settings_need_confirmation', 'locked_below_spent_or_minimum'):
                    lines = ['최소금액·잠금·유지 목표 또는 이미 쓴 금액이 서로 충돌해요. 설정을 확인한 뒤 다시 추천받아 주세요.']
                    choices = []
                elif result.get('reason') in ('spending_unverified', 'funding_unverified'):
                    lines = ['거래 내역이나 사용 가능한 재원을 확인하지 못했어요. 내역을 확인한 뒤 다시 계산해 주세요.']
                    choices = [{'value': 'recovery_retry', 'label': '여윳돈으로 다시 계산해요'}]
                else:
                    lines = ['현재 확인된 재원과 보호 조건으로는 해결안을 만들 수 없어요. 설정은 그대로 유지했어요.']
                    choices = [{'value': 'recovery_retry', 'label': '여윳돈으로 다시 계산해요'}]
            elif state.get('recovery_proposal_id'):
                card = plans.proposal_card(self.user, get_object_or_404(Budget, pk=state['recovery_proposal_id'], user=self.user), today=self.today)
                lines, choices, status = ['계산한 해결안을 확인해 주세요.'], [], card['state']
        self.bot = {'agent': 'budget', 'inputs': [], 'result': False, 'lines': lines, 'choices': choices,
                'budget_adjustment': {'status': status, 'phase': 'design_recovery', 'can_approve': bool(card and card['can_approve']),
                                      **({'proposal': card} if card else {}),
                                      'source_task_id': str(self.source_task_id)}}, {
                    'intent': 'budget', 'slots': {}, 'fallback': False}
        return self.bot

    def flush(self, conversation):
        # respond() holds the user lock: bind once and recheck settings changed during preparation.
        task, handoff = read_design_handoff(self.user, self.source_task_id, self.today, for_update=True, allow_resolved=True)
        if task.conversation_id is not None and task.conversation_id != conversation.pk:
            raise RecoveryExpired('이미 연결된 조정 대화를 다시 열어 주세요.')
        if task.conversation_id is None:
            task.conversation = conversation
            task.save(update_fields=['conversation'])
        from . import design_recovery_plans as plans
        if self.cancel_id:
            old = Budget.objects.filter(pk=self.cancel_id, user=self.user, task__conversation=conversation,
                                        validation_summary__design_recovery__source_task_id=str(task.pk), status='validated').first()
            if old:
                old.status = 'rejected'
                old.save(update_fields=['status'])
                old.task.status = 'canceled'
                old.task.save(update_fields=['status'])
        if self.plan is not None:
            from .adjustment_service import AdjustmentExpired
            try:
                budget = plans.save_plan(self.user, task, handoff, self.plan, conversation, self.today)
            except (AdjustmentExpired, ValueError):
                raise RecoveryExpired() from None
            card = plans.proposal_card(self.user, budget, conversation=conversation, today=self.today)
            self.bot[0]['budget_adjustment'].update(proposal=card, can_approve=card['can_approve'])
            self.state['recovery_proposal_id'] = str(budget.pk)
            self.store.save_agent_state(self.key, 'budget', self.state)
