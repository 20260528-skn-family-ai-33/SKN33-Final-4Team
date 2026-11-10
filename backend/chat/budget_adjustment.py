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
                   ("proposal", "comparison", "alternatives", "alternatives_notice", "explanation", "detail")
                   if key in reply}
        payload.update(status=result["status"], work_ref=result.get("work_ref"),
                       can_approve=bool(reply.get("proposal") and not state.get("pending")
                                        and result["status"] == "replied"),
                       phase=(state.get("pending") or {}).get("kind"))
        for choice in reply.get("choices", []):
            if choice["value"] == "compare":
                choice["label"] = "비교하기"
        reply["budget_adjustment"] = payload
        if (reply.get("proposal") or reply.get("comparison")) and not reply.get("detail"):
            reply["lines"] = reply.get("lines", [])[:1]
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
