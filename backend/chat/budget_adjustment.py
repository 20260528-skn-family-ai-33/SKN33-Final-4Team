"""예산 대화와 웹 응답 연결. 조회·선택으로 예산을 승인하지 않는다."""
from copy import deepcopy

from django.conf import settings

from budgets.adjustment_core.financial_conversation import handle as handle_expanded, starts_new_request
from budgets.adjustment_core.conversation import is_salary_resume
from budgets.adjustment_core.interpretation import InterpretationFailed
from budgets.adjustment_core import followups


def frequency_check(user, store, cid, state, request, get_context, complete):
    from budgets.overview import confirmed_adjustments
    from budgets.adjustment_core.agent import _ask, _result, _save, CANCEL
    from budgets.adjustment_core.interpretation import read, dialogue_context

    pending = state.get('pending') or {}
    text = (request.get('text') or '').strip()
    if request.get('mode') == 'cancel' or text in CANCEL:
        return None, request
    if pending.get('kind') == 'adjustment_frequency':
        data = {}
        choice = next((value for value, label in pending['choices'] if text in (value, label)), None)
        if choice is None and text:
            try:
                data = read(text, complete, **dialogue_context(state))
            except InterpretationFailed:
                data = {}
            if data.get('mine') is False:
                from budgets.adjustment_core.conversation import not_mine
                return not_mine(None, data.get('handoff'), data.get('handoff_category'), {}), request
            choice = data.get('selected_choice')
            if data.get('cancel'):
                choice = 'keep_budget'
        if choice == 'keep_budget':
            _save(store, cid, None)
            return _result('ended', ['지금 예산을 유지할게요.']), request
        if choice == 'continue_adjustment':
            state['frequency_acknowledged'] = pending['basis']
            resumed = deepcopy(pending['request'])
            if data.get('requests') or any(data.get(field) for field in ('sources', 'destinations', 'excluded', 'scope')):
                resumed['text'] = '\n'.join(filter(None, [resumed.get('text'), text]))
            state.pop('pending', None)
            if pending.get('previous_pending'):
                state['pending'] = pending['previous_pending']
            _save(store, cid, state)
            return None, resumed
        return _result('awaiting_input', [pending['line']], choices=pending['choices'], explanation={}), request
    if state.get('requests') or state.get('work_ref') or state.get('salary_handoff'):
        return None, request
    try:
        context = get_context()
    except ValueError:
        return None, request
    count = confirmed_adjustments(user).filter(cycle_id=context['cycle_id']).count()
    basis = [context['cycle_id'], count]
    if count < 1 or state.get('frequency_acknowledged') == basis:
        return None, request
    return _ask(store, cid, state, 'adjustment_frequency',
                f'이번 급여 주기에 예산을 {count}회 조정했어요. 다시 조정할까요?',
                [('continue_adjustment', '계속 조정하기'), ('keep_budget', '지금 예산 유지하기')],
                explanation={}, basis=basis, request=deepcopy(request), previous_pending=deepcopy(pending)), request


def handler(user, store, adapter, complete, conversation_key):
    def handle(request):
        if request.get("mode") != "cancel":
            from users.privacy import require_bank_not_revoked
            require_bank_not_revoked(user)
            if getattr(settings, "CHAT_LLM", "fake") == "live":
                from budgets.ai_consent import ConsentRequired, require_consent
                try:
                    require_consent(user)
                except ConsentRequired:
                    return {"status": "failed", "error": "budget_adjust_consent_required"}
        if getattr(settings, 'CHAT_LLM', 'fake') == 'live':
            from .llm.budget_tracing import trace_turn
            conversation = getattr(adapter, 'conversation', None)
            return trace_turn(request, str(conversation.pk) if conversation is not None else None, lambda: _handle(request))
        return _handle(request)

    def _handle(request):
        previous = store.get_agent_state(conversation_key, "budget") or {}
        reference = previous.get("work_ref") or request.get("work_ref")
        task_state = adapter.task_status(reference).get('state') if reference else None
        if task_state == "saved" and not previous.get('salary_handoff'):
            previous = {}
            store.save_agent_state(conversation_key, "budget", {})
            adapter.base_budget_id = adapter.mode = None
            if request.get("mode") in (None, "start", "continue") and request.get("work_ref") in (None, reference):
                request = {**request, "mode": "start", "work_ref": None, "active_task": None}
        adapter.reference_context = previous.get("reference_context")
        refresh_salary = is_salary_resume(request, previous)
        salary_context = None

        def get_context():
            nonlocal refresh_salary
            if salary_context is not None:
                return deepcopy(salary_context)
            if refresh_salary:
                context = adapter.fresh_salary_context()
                refresh_salary = False
                return context
            return adapter.get_context()

        used = 0 if starts_new_request(request.get("text")) else previous.get("llm_call_count", 0)
        called = 0

        def interpret(system, text):
            nonlocal called
            if used + called >= 10:
                raise InterpretationFailed("call_limit")
            called += 1
            return complete(system, text)

        frequency_reply, request = frequency_check(user, store, conversation_key, previous, request, get_context, interpret)
        followup_answer = (followups.answer(previous, request.get('text'))
                           if request.get('mode') != 'cancel' and not previous.get('salary_handoff')
                           and task_state not in followups.TERMINAL else None)
        salary_reply = None
        if refresh_salary:
            from budgets.salary_redistribution import review_proposal, salary_review_needed
            from budgets.adjustment_service import AdjustmentAdapter
            from budgets.adjustment_core.agent import _result
            from budgets.adjustment_core.financial_conversation import _ask
            try:
                # Both entry points review the increase against approved money, not an unapproved salary draft.
                context = AdjustmentAdapter(user, conversation=getattr(adapter, 'conversation', None),
                    today=adapter.today, mode='NORMAL_ADJUSTMENT').get_context()
            except ValueError:
                context = None  # The existing resume path preserves the request and offers a retry.
            card = review_proposal(user, previous, context, conversation=getattr(adapter, 'conversation', None)) if context else None
            if card:
                salary_context = context
                previous.update(proposal=card, work_ref=card['task_ref'])
                previous.pop('pending', None)
                if card['state'] == 'saved':
                    previous.pop('salary_handoff', None)
                store.save_agent_state(conversation_key, 'budget', previous)
                salary_reply = _result('replied', [
                    '확정한 변경을 반영했어요. 같은 요청을 다시 적용하지 않을게요.' if card['state'] == 'saved'
                    else '월급 증가분으로 만든 변경안이에요. 최종 확정 전까지 기존 예산은 그대로예요.'
                ], proposal=card, work_ref=card['task_ref'])
            elif context and salary_review_needed(context, previous):
                salary_context = context
                previous = deepcopy(previous)
                previous.pop('work_ref', None)
                previous.pop('proposal', None)
                salary_reply = _ask(store, conversation_key, previous, 'salary_allocation_review',
                    '기존 요청을 먼저 반영하고, 남은 월급 증가분을 나눌게요. 저축할 비율을 확인해 주세요.',
                    [('salary_resume', '이전 예산 조정 이어하기')], explanation={})
                salary_reply['continued_work'] = True
        result = frequency_reply or salary_reply or followup_answer or handle_expanded(
            {**request, "conversation_id": conversation_key}, store=store, complete=interpret,
            get_context=get_context, simulate=adapter.simulate, propose=adapter.propose,
            task_status=adapter.task_status, cancel=adapter.cancel, allocator=adapter.allocator,
        )
        answered = False
        if result["status"] == "not_mine" and (result.get("suggest") or {}).get("to") == "budget_basis":
            # '이 예산 왜 이렇게 잡았어?'는 예산 질문 — 소비 분석으로 넘기지 않고 저장된 배정 근거로 답한 뒤 조정을 이어 간다
            result, answered = basis_reply(user, store, conversation_key, result, request), True
        if result["status"] == "not_mine":
            return result
        if result.get("new_work"):
            used = 0
        state = store.get_agent_state(conversation_key, "budget") or {}
        adapter.llm_call_count = used + called
        if state or result["status"] == "failed":
            state["llm_call_count"] = used + called
            if adapter.reference_context is not None:
                state["reference_context"] = deepcopy(adapter.reference_context)
            store.save_agent_state(conversation_key, "budget", state)
        reply = result.get("reply", {})
        if result.get("work_ref") is None:
            result["work_ref"] = state.get("work_ref") or (reply.get("proposal") or {}).get("task_ref")
        payload = {key: deepcopy(reply[key]) for key in
                   ("proposal", "comparison", "alternatives", "alternatives_notice", "explanation", "detail", "recommendation_advice", "distribution_input")
                   if key in reply}
        payload.update(status=result["status"], work_ref=result.get("work_ref"),
                       request_summary=reply.get('request_summary', ''),
                       can_approve=bool(reply.get("proposal") and reply['proposal'].get('state') == 'awaiting_approval'
                                        and reply['proposal'].get('can_approve', True) and not state.get("pending")
                                        and result["status"] == "replied"),
                       phase="over_entry" if state.get("over_entry") else (state.get("pending") or {}).get("kind"))
        salary = (state.get('pending') or {}).get('salary') if payload['phase'] == 'salary_change' else None
        if salary:
            from users.models import FinancialProfile
            payday = FinancialProfile.objects.filter(user=user).values_list('payday', flat=True).first()
            payload['salary_change'] = {'before': salary['before'], 'after': salary['after'], 'payday': payday}
        items = state.get('requests', [])
        def active_context():
            from budgets.adjustment_schedule import planning_context
            return planning_context(get_context(), state.get('planning'))

        if state.get('planning', {}).get('scope') == 'next_cycle' and payload['phase'] not in {'salary_handoff', 'salary_allocation_review'}:
            try:
                planned = active_context()
                payload['planning'] = {'effective_on': planned['effective_on'], 'income_before': planned['income_before'],
                                       'income_after': planned['monthly_income'], 'one_off': state['planning'].get('one_off', False),
                                       'expense': state['planning'].get('expense')}
            except ValueError:
                pass  # The conversation is still asking for the missing planning input.
        proposal = reply.get('proposal')
        if (proposal and proposal.get('state') == 'awaiting_approval' and not proposal.get('one_off')
                and not state.get('pending') and result['status'] == 'replied'):
            from budgets.adjustment_core.conversation import distribution_fields, _calculation
            donors = [{'category': row['category'], 'amount': -row['diff'], 'amount_mode': 'by', 'direction': 'down'}
                      for row in proposal.get('changes', []) if row['diff'] < 0]
            try:
                fields = distribution_fields(_calculation(active_context(), state), donors, state.get('excluded', []))
                fields['values'] = [{'category': row['category'], 'amount': row['diff']}
                                    for row in proposal['changes'] if row['diff'] > 0]
                if (fields['categories'] and sum(row['amount'] for row in fields['values']) == fields['amount']
                        and all(row['category'] in fields['categories'] for row in fields['values'])):
                    payload['distribution_edit'] = fields
            except (ValueError, KeyError):
                pass  # 편집할 수 없는 기존 안은 확정·자연어 대화 경로를 유지한다.
        if (not answered and result['status'] == 'awaiting_input' and not reply.get('proposal') and len(items) == 1
                and items[0].get('category') and items[0].get('direction') in {'up', 'down'}
                and items[0].get('amount') is None and payload['phase'] in {'amount', 'comparison'}):
            context = (state.get('comparison') or {}).get('context', {})
            has_plan = any(choice['value'].startswith('plan_') for choice in reply.get('choices', []))
            if payload['phase'] == 'amount' or (not has_plan and context.get('spending_status') == 'complete'
                                               and items[0]['category'] not in context.get('locks', {})):
                from budgets.adjustment_core.explanations import NAMES
                code, direction = items[0]['category'], items[0]['direction']
                current = active_context()
                before = (sum(current['allocations'].values()) if code == 'LIVING' else
                          {**current['allocations'], **current['buckets']}.get(code))
                payload['amount_input'] = {'category': code, 'direction': direction, 'before_amount': before}
                if direction == 'up':
                    from budgets.adjustment_core.funding import increase_input_limit
                    payload['amount_input']['max_amount'] = increase_input_limit(current, code) // 5000 * 5000
                if direction == 'down':
                    if code in current.get('allocations', {}):
                        floor = max(current.get(field, {}).get(code, 0) for field in ('spent', 'minimums', 'system_floors'))
                        maximum = max(current['allocations'][code] - floor, 0)
                        if code in current.get('locks', {}) and code not in state.get('unlock_categories', []):
                            maximum = 0
                    else:
                        maximum = max(min(current.get('buckets', {}).get(code, 0) - current.get('bucket_floors', {}).get(code, 0),
                                          current.get('bucket_available', {}).get(code, 0)), 0)
                    payload['amount_input']['max_amount'] = maximum // 5000 * 5000
                payload['request_summary'] = ''
                if followup_answer is None:
                    reply['lines'] = [f"{NAMES.get(code, code)} 예산을 얼마 {'늘릴까요' if direction == 'up' else '줄일까요'}?"]
        reply["budget_adjustment"] = payload
        if (not answered and (reply.get("proposal") or reply.get("comparison")) and not reply.get("detail")
                and not reply.get('recommendation_advice')):
            reply["lines"] = reply.get("lines", [])[:1]
        from budgets.adjustment_core.responses import guidance
        payload['guidance'] = guidance(result, state)
        if items and payload['phase'] not in {'increase_review', 'salary_handoff', 'salary_allocation_review'} and result['status'] not in {'ended', 'failed', 'unsupported'}:
            from budgets.adjustment_core.funding import large_increase_warning
            try:
                warning = large_increase_warning(active_context(), items, reason=state.get('reason'))
            except ValueError:
                warning = None  # Planning inputs are not complete yet.
            if warning:
                payload['increase_warning'] = warning
        if previous.get('work_ref') and not result.get('work_ref') and state.get('pending') and result['status'] == 'awaiting_input':
            result['continued_work'] = True
        questions = [] if payload['phase'] in {'salary_handoff', 'salary_allocation_review', 'adjustment_frequency'} else ([row for row in previous.get('followups', []) if row['question'] != request.get('text')]
                     if followup_answer is not None else followups.suggest(
                         reply, state, request.get('text'), complete if used + called < 10 else None))
        if questions:
            reply['examples'] = [row['question'] for row in questions]
        if state:
            state.pop('followups', None)
            if questions:
                state['followups'] = questions
            store.save_agent_state(conversation_key, 'budget', state)
        return result
    return handle


def basis_reply(user, store, conversation_key, result, request):
    """저장된 예산의 배정 근거를 답하고, 진행 중이던 질문(있으면)을 그대로 다시 보여 준다. 예산·조정 상태는 바꾸지 않는다"""
    from datetime import date
    from django.db import DatabaseError
    from django.utils import timezone
    from budgets.adjustment_core.conversation import _pending_reply
    from budgets.adjustment_core.agent import _result
    from budgets.adjustment_core.explanations import NAMES
    from budgets.budget_explanation import explain_current_budget

    state = store.get_agent_state(conversation_key, "budget") or {}
    code = result["suggest"].get("category") or next(
        (item.get("category") for item in state.get("requests", []) if item.get("category") in NAMES), None)
    ref = result.get("work_ref") or state.get("work_ref")
    if code is None:
        return _result("awaiting_input", ["어느 항목 예산이 왜 그렇게 정해졌는지 궁금하세요? 항목 이름을 알려 주세요."], work_ref=ref)
    try:
        today = date.fromisoformat(request["today"]) if request.get("today") else timezone.localdate()
        lines = explain_current_budget(user, [code], today)
    except (DatabaseError, ValueError, TypeError):
        lines = ["저장된 예산 근거를 지금 불러오지 못했어요. 진행 중인 조정은 그대로 두었어요."]
    if state.get("pending") and state["pending"].get("kind") not in ("request", "discussion"):  # 고르던 질문이 있으면 이어서
        reply = _pending_reply(state, ref)
        reply["reply"]["lines"] = [*lines, *reply["reply"]["lines"]]
        return reply
    name = NAMES.get(code, code)
    return _result("awaiting_input", [*lines, f"{name} 예산을 줄이거나 다른 항목으로 옮기고 싶으면 금액과 함께 말씀해 주세요."],
                   work_ref=ref)


def refreshed_content(message):
    """승인·만료된 과거 카드도 새로고침 후 현재 서버 상태로 표시한다."""
    content = deepcopy(message.content)
    adjustment = content.get("budget_adjustment")
    proposal = adjustment and adjustment.get("proposal")
    if not proposal:
        return content
    from budgets.adjustment_service import proposal_card
    from budgets.models import Budget
    row = Budget.objects.filter(pk=proposal.get("proposal_id"), user=message.user).first()
    if row is None:
        adjustment.update(can_approve=False, status="expired")
        return content
    current = proposal_card(message.user, row, conversation=message.conversation)
    adjustment["proposal"] = {**proposal, **current}
    if current.get("state") != "awaiting_approval":
        adjustment.update(can_approve=False, status=current.get("state", "expired"))
    return content
