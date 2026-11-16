"""예산 조정 대화의 응답·상태 공통 도우미. 저장은 주입한 서버 포트만 한다."""
from copy import deepcopy

from .explanations import choice_descriptions as describe_choices, status_explanation, step_explanation


CANCEL = {"취소", "그만", "그만할래", "그만해줘", "예산 조정 취소"}
APPLY = {"적용", "승인", "이 안으로 적용", "그대로 적용"}
ENDED = {"saved", "failed", "canceled", "expired", "rejected"}


def _result(status, lines=(), *, work_ref=None, choices=(), proposal=None, comparison=None, detail=False,
            explanation=None, choice_descriptions=None, **extra):
    descriptions = describe_choices(None, choices) if choice_descriptions is None else choice_descriptions
    reply = {"lines": list(lines), "notices": [],
             "choices": [{"value": v, "label": label, "description": descriptions.get(v, "")} for v, label in choices],
             "explanation": deepcopy(explanation if explanation is not None else
                                     status_explanation(status, proposal=proposal, error=extra.get("error")))}
    if proposal is not None:
        reply["proposal"] = deepcopy(proposal)
    if comparison is not None:
        reply["comparison"] = deepcopy(comparison)
    if detail:
        reply["detail"] = True
    return {"status": status, "work_ref": work_ref, "reply": reply, **extra}


def _save(store, cid, state):
    store.save_agent_state(cid, "budget", deepcopy(state) if state else None)


def _ask(store, cid, state, kind, line, choices=(), *, explanation=None, choice_descriptions=None, **details):
    if explanation is None:
        from .responses import question
        line = question(kind, state, line, details.get('index'))
    if explanation is None and kind == 'clarification':
        explanation = {'title': '다시 확인할 내용', 'why': line, 'effect': '', 'next': ''}
    explanation = step_explanation(kind, state) if explanation is None else explanation
    descriptions = describe_choices(kind, choices) if choice_descriptions is None else choice_descriptions
    state["pending"] = {"kind": kind, "line": line, "choices": list(choices),
                        "explanation": deepcopy(explanation), "choice_descriptions": dict(descriptions), **details}
    _save(store, cid, state)
    return _result("awaiting_input", [line], work_ref=state.get("work_ref"), choices=choices,
                   explanation=explanation, choice_descriptions=descriptions)


def _finished(store, cid, ref, checked):
    phase = checked["state"]
    if phase == "saved" and not checked.get("saved_ref"):
        return _result("failed", ["저장 결과를 확인하지 못했어요."], work_ref=ref, error="invalid_saved_ref")
    _save(store, cid, None)
    lines = {"saved": "예산 조정이 저장됐어요.", "canceled": "예산 조정을 그만뒀어요.",
             "expired": "조건이 바뀌어 이전 조정안이 만료됐어요. 다시 요청해 주세요.",
             "rejected": "이 조정안은 적용하지 않았어요.", "failed": "조정을 완료하지 못했어요. 다시 요청해 주세요."}
    return _result("ended", [lines[phase]], work_ref=ref, saved_ref=checked.get("saved_ref"),
                   explanation=step_explanation(phase))


def _check(ref, task_status):
    checked = task_status(ref)
    if not isinstance(checked, dict) or checked.get("state") not in ENDED | {"proposing", "awaiting_approval", "saving"}:
        raise ValueError("invalid_task_status")
    if checked.get("task_ref", ref) != ref:
        raise ValueError("invalid_task_reference")
    return checked


def _lock_snapshot(context, codes):
    return {code: {"amount": context["locks"][code],
                   "revision": context.get("lock_revisions", {}).get(code),
                   "base_budget_id": context["base_budget_id"]} for code in codes}

