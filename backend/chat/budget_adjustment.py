"""예산 대화와 웹 응답 연결. 조회·선택으로 예산을 승인하지 않는다."""
from copy import deepcopy

from django.conf import settings

from budgets.adjustment_core.financial_conversation import handle as handle_expanded, starts_new_request
from budgets.adjustment_core.interpretation import InterpretationFailed


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
        previous = store.get_agent_state(conversation_key, "budget") or {}
        reference = previous.get("work_ref") or request.get("work_ref")
        if reference and adapter.task_status(reference).get("state") == "saved":
            previous = {}
            store.save_agent_state(conversation_key, "budget", {})
            adapter.base_budget_id = adapter.mode = None
            if request.get("mode") in (None, "start", "continue") and request.get("work_ref") in (None, reference):
                request = {**request, "mode": "start", "work_ref": None, "active_task": None}
        adapter.reference_context = previous.get("reference_context")
        used = 0 if starts_new_request(request.get("text")) else previous.get("llm_call_count", 0)
        called = 0

        def interpret(system, text):
            nonlocal called
            if used + called >= 10:
                raise InterpretationFailed("call_limit")
            called += 1
            return complete(system, text)

        result = handle_expanded(
            {**request, "conversation_id": conversation_key}, store=store, complete=interpret,
            get_context=adapter.get_context, simulate=adapter.simulate, propose=adapter.propose,
            task_status=adapter.task_status, cancel=adapter.cancel, allocator=adapter.allocator,
        )
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
                       can_approve=bool(reply.get("proposal") and not state.get("pending")
                                        and result["status"] == "replied"),
                       phase="over_entry" if state.get("over_entry") else (state.get("pending") or {}).get("kind"))
        items = state.get('requests', [])
        def active_context():
            from budgets.adjustment_schedule import planning_context
            return planning_context(adapter.get_context(), state.get('planning'))

        if state.get('planning', {}).get('scope') == 'next_cycle':
            try:
                planned = active_context()
                payload['planning'] = {'effective_on': planned['effective_on'], 'income_before': planned['income_before'],
                                       'income_after': planned['monthly_income'], 'one_off': state['planning'].get('one_off', False),
                                       'expense': state['planning'].get('expense')}
            except ValueError:
                pass  # The conversation is still asking for the missing planning input.
        proposal = reply.get('proposal')
        if proposal and payload['can_approve'] and proposal.get('state') == 'awaiting_approval' and not proposal.get('one_off'):
            from budgets.adjustment_core.conversation import distribution_fields
            donors = [{'category': row['category'], 'amount': -row['diff'], 'amount_mode': 'by', 'direction': 'down'}
                      for row in proposal.get('changes', []) if row['diff'] < 0]
            try:
                fields = distribution_fields(active_context(), donors, state.get('excluded', []))
                fields['values'] = [{'category': row['category'], 'amount': row['diff']}
                                    for row in proposal['changes'] if row['diff'] > 0]
                if (fields['categories'] and sum(row['amount'] for row in fields['values']) == fields['amount']
                        and all(row['category'] in fields['categories'] for row in fields['values'])):
                    payload['distribution_edit'] = fields
            except (ValueError, KeyError):
                pass  # 편집할 수 없는 기존 안은 확정·자연어 대화 경로를 유지한다.
        if (result['status'] == 'awaiting_input' and not reply.get('proposal') and len(items) == 1
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
                reply['lines'] = [f"{NAMES.get(code, code)} 예산을 얼마 {'늘릴까요' if direction == 'up' else '줄일까요'}?"]
        reply["budget_adjustment"] = payload
        if (reply.get("proposal") or reply.get("comparison")) and not reply.get("detail") and not reply.get('recommendation_advice'):
            reply["lines"] = reply.get("lines", [])[:1]
        from budgets.adjustment_core.responses import guidance
        payload['guidance'] = guidance(result, state)
        if items and result['status'] not in {'ended', 'failed', 'unsupported'}:
            from budgets.adjustment_core.funding import large_increase_warning
            try:
                warning = large_increase_warning(active_context(), items)
            except ValueError:
                warning = None  # Planning inputs are not complete yet.
            if warning:
                payload['increase_warning'] = warning
        if previous.get('work_ref') and not result.get('work_ref') and state.get('pending') and result['status'] == 'awaiting_input':
            result['continued_work'] = True
        return result
    return handle


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
