"""초과 항목으로 조정을 시작한다. 전달 금액을 신뢰하지 않고 현재 예산을 다시 확인한다."""
from copy import deepcopy

from .agent import _check, _finished, _result, _save, ENDED
from .conversation import handle_expanded
from .explanations import CATEGORY_NAMES


CHOICES = [("over_keep", "기존 조정 이어가기"), ("over_restart", "초과 항목으로 새 조정")]


def _choice(state):
    category = state["over_entry"]["category"]
    return _result("awaiting_input", [f"진행 중인 조정은 그대로 뒀어요. {CATEGORY_NAMES[category]} 초과 항목으로 새로 시작할까요?"],
                   work_ref=state.get("work_ref"), choices=CHOICES,
                   proposal=state.get("proposal"))


def handle(request, state, ports):
    """해당 입력이 초과 진입·선택이 아니면 None. 외부 화면·라우터는 hints만 전달한다."""
    cid, store = request["conversation_id"], ports["store"]
    hints, text = request.get("hints"), (request.get("text") or "").strip()
    incoming = isinstance(hints, dict) and hints.get("reason") == "over"
    queued = state.get("over_entry")
    picked = next((value for value, label in CHOICES if text in (value, label)), None)
    if queued and picked:
        incoming = False
    if queued and picked == "over_keep" and not incoming:
        state = deepcopy(state)
        state.pop("over_entry")
        _save(store, cid, state)
        return handle_expanded({**request, "text": "", "hints": {}}, **ports)
    if not incoming and not (queued and picked == "over_restart"):
        return _choice(state) if queued else None
    if incoming:
        categories, amount = hints.get("categories"), hints.get("over_amount")
        if (set(hints) != {"reason", "categories", "over_amount"}
                or not isinstance(categories, list) or len(categories) != 1
                or not isinstance(categories[0], str) or categories[0] not in CATEGORY_NAMES
                or categories[0] in {"ETC", "UNCLASSIFIED"}
                or type(amount) is not int or amount <= 0):
            return _result("failed", ["초과한 소비 카테고리 하나와 양수인 원 단위 초과액을 보내 주세요."], error="invalid_over_handoff")
        category = categories[0]
    else:
        category = queued["category"]
    try:
        context = ports["get_context"]()
    except Exception:
        return _result("failed", ["현재 예산·지출을 확인하지 못했어요. 기존 조정은 그대로예요."], error="context_unavailable")
    if context.get("mode") != "NORMAL_ADJUSTMENT" or context.get("source_status") != "approved":
        return _result("unsupported", ["초과 항목 조정은 현재 승인된 생활비 예산에서 시작해 주세요."])
    if context.get("spending_status") != "complete":
        return _result("failed", ["지출 자료가 완전하지 않아 초과액을 확인하지 못했어요. 자료를 확인한 뒤 다시 시작해 주세요."], error="spending_unverified")
    allocated, spent = context["allocations"].get(category), context.get("spent", {}).get(category)
    if type(allocated) is not int or type(spent) is not int or allocated <= 0 or spent <= allocated:
        return _result("unsupported", ["현재 확인된 초과 항목이 아니에요. 예산 화면에서 최신 상태를 확인해 주세요."], work_ref=state.get("work_ref"))
    if incoming and state.get("over_budget", {}).get("category") == category:
        if text:
            return None
        return handle_expanded({**request, "text": "", "hints": {}}, **ports)
    busy = any(state.get(key) for key in ("requests", "proposal", "work_ref", "financial_offer", "pending_edit", "clear_targets"))
    busy = busy or bool(request.get("work_ref"))
    busy = busy or state.get("pending", {}).get("kind") not in (None, "request", "discussion")
    if incoming and busy:
        state = deepcopy(state)
        state["over_entry"] = {"category": category}
        _save(store, cid, state)
        return _choice(state)
    ref = state.get("work_ref") or request.get("work_ref")
    if ref:
        try:
            checked = _check(ref, ports["task_status"])
            if checked["state"] == "saving":
                return _result("awaiting_input", ["기존 조정안을 저장 중이에요. 저장 결과를 확인한 뒤 다시 선택해 주세요."], work_ref=ref, choices=CHOICES)
            if checked["state"] not in ENDED:
                ports["cancel"](ref)
                checked = _check(ref, ports["task_status"])
            if checked["state"] not in ENDED:
                return _result("awaiting_input", ["기존 변경안의 취소를 확인하고 있어요. 확인 후 다시 선택해 주세요."], work_ref=ref, choices=CHOICES)
            if checked["state"] != "canceled":
                return _finished(store, cid, ref, checked)
        except Exception:
            return _result("failed", ["기존 변경안의 취소 결과를 확인하지 못했어요. 새 조정을 시작하지 않았어요."], work_ref=ref, error="task_check_failed")
    _save(store, cid, {"over_budget": {"category": category, "over_amount": spent - allocated}})
    data = {"requests": [{"category": category, "direction": "up", "amount": None, "amount_mode": "by"}],
            "reference": "new"}
    result = handle_expanded({**request, "text": "", "mode": "start", "work_ref": None, "hints": {}},
                             initial_data=data, **ports)
    # 먼저 재원을 고른 뒤, 최대 가용액을 추정하지 않고 희망 증액액을 확인한다.
    state = store.get_agent_state(cid, "budget")
    if state:
        state["use_available"] = False
        _save(store, cid, state)
    result["reply"]["lines"].insert(0, f"{CATEGORY_NAMES[category]} 예산을 {spent - allocated:,}원 넘었어요. 이 항목의 증액을 상담할게요.")
    result["new_work"] = True
    return result
