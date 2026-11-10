"""User-facing reasons, effects and next actions; facts come from server results."""
from importlib import import_module

CATEGORY_NAMES = {code: name for code, name, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES}


NAMES = {**CATEGORY_NAMES, "ETC": "기타", "LIVING": "생활비", "SAVINGS": "저축",
         "CUSHION_MONTHLY": "월 쿠션 적립액", "SPARE": "여윳돈"}
FIELDS = ("why", "effect", "next")
STEPS = {
    "request": ("원하는 변경을 알아야 예산을 계산할 수 있어요.", "아직 금액이나 설정을 바꾸지 않아요.", "조정하고 싶은 항목을 말해 주세요. 금액을 몰라도 시작할 수 있어요."),
    "category": ("어느 항목을 바꿀지 아직 정해지지 않았어요.", "선택한 항목을 조정 대상으로 확인해요.", "생활비 항목·저축·월 쿠션 중 원하는 대상을 골라 주세요."),
    "amount": ("희망 금액을 알아야 가능한 금액과 부족액을 비교할 수 있어요.", "입력한 금액으로 미리 계산하며 아직 저장하지 않아요.", "‘5만 원 더’처럼 말하거나 ‘가능한 금액 확인’을 선택해 주세요."),
    "amount_mode": ("같은 금액도 증감액·최종 예산·남은 예산인지에 따라 결과가 달라요.", "남은 금액을 지정하면 기지출을 더해 전체 예산으로 환산해요.", "말한 금액이 어떤 의미인지 골라 주세요."),
    "direction": ("금액을 늘릴지 줄일지 알아야 출처와 목적지를 정할 수 있어요.", "증액은 재원, 감액은 줄인 돈의 목적지를 확인해요.", "늘리기 또는 줄이기를 선택해 주세요."),
    "funding_scope": ("같은 증감액이라도 생활비·여윳돈·저축·월 쿠션 중 어디를 바꾸는지에 따라 영향이 달라요.", "선택한 범위 안에서만 보호 조건을 지키며 계산해요.", "조정 범위를 선택하거나 ‘잘 모르겠어요’로 세 가지 방법을 비교해 주세요."),
    "sources": ("증액할 돈의 출처가 정해져야 이중 배분을 피할 수 있어요.", "선택한 재원의 확인된 가용액만 사용하며 다른 재원을 자동으로 가져오지 않아요.", "재원을 고르거나 비교를 요청해 주세요."),
    "destinations": ("줄인 돈을 어디에 배분할지 정해야 전체 예산 합계가 맞아요.", "목적지의 배정을 늘리거나 여윳돈으로 남기는 변경안을 계산해요.", "받을 항목을 선택해 주세요."),
    "funding": ("현재 정보나 허용한 재원만으로 조정을 완료할 수 없어요.", "추가 재원이나 감액액을 자동으로 정하지 않아요.", "재원·금액을 직접 지정하거나 다른 방법을 비교해 주세요."),
    "destination": ("감액한 돈의 배분 목적지가 필요해요.", "목적지가 정해지기 전에는 예산을 저장하지 않아요.", "늘릴 항목과 금액을 알려 주세요."),
    "available_quote": ("금액을 정하기 쉽도록 보호 조건을 지키는 최대 이동액을 계산했어요.", "최대 가능액은 필요한 소비액의 권고가 아니며 선택해도 미리보기까지만 진행해요.", "이 금액으로 변경안을 보거나 금액·재원을 다시 정해 주세요."),
    "comparison": ("방법마다 다른 항목에 미치는 영향을 같은 금액 기준으로 비교할 수 있어요.", "비교·추천만으로 예산을 바꾸거나 안을 자동 선택하지 않아요.", "각 안의 근거를 확인한 뒤 하나를 고르거나 금액을 바꿔 주세요."),
    "partial": ("현재 선택한 범위에서는 희망 금액을 전부 마련할 수 없어요.", "부족액을 다른 재원에서 자동으로 가져오지 않아요.", "가능한 금액만 반영할지, 금액을 바꿀지, 다른 재원까지 비교할지 선택해 주세요."),
    "unlock": ("선택한 항목이 잠겨 있어 그대로는 금액을 바꿀 수 없어요.", "동의한 해제는 최종 승인할 때 실행하고 다음 주기에도 이어져요. 지금은 해제하지 않아요.", "승인 시 해제에 동의하거나 잠금을 유지하고 다른 항목을 선택해 주세요."),
    "scope": ("목표 해제와 계속 유지처럼 동시에 적용할 수 없는 요청이 있어요.", "서로 충돌하는 설정을 임의로 저장하지 않아요.", "이번 주기 변경과 다음 주기 목표 중 원하는 처리를 확인해 주세요."),
    "clarification": ("요청에 불명확하거나 서로 충돌하는 부분이 있어요.", "확인되지 않은 의도를 추측해 예산을 바꾸지 않아요.", "확인 질문에 해당하는 부분만 답해 주세요."),
    "saved": ("서버에서 이 작업의 저장 근거를 확인했어요.", "승인한 변경안과 동의한 설정 변경이 반영됐어요.", "현재 적용 예산을 다시 확인하거나 새 조정을 시작할 수 있어요."),
    "canceled": ("사용자가 현재 조정을 취소했어요.", "미승인 변경안은 적용하지 않으며 이미 승인된 기록을 되돌리지는 않아요.", "다른 조정을 원하면 새 요청을 알려 주세요."),
    "expired": ("기준 예산·지출·설정 또는 주기가 이전 계산 때와 달라졌어요.", "옛 변경안은 승인할 수 없고 현재 예산을 과거 값으로 되돌리지 않아요.", "최신 조건으로 다시 계산한 변경안을 확인해 주세요."),
    "rejected": ("이 변경안은 적용하지 않는 것으로 확인됐어요.", "이 미승인안의 금액·설정은 저장하지 않아요.", "원하면 금액이나 재원을 바꿔 새 안을 만들어 주세요."),
    "saving": ("승인 요청의 저장 처리를 확인하고 있어요.", "확인 전에는 저장 성공·취소를 단정하거나 중복 승인하지 않아요.", "서버가 반환하는 완료 결과를 기다려 주세요."),
    "canceling": ("현재 변경안을 취소했다는 서버 확인이 아직 필요해요.", "취소 요청만으로 저장이 멈췄다고 단정하지 않고 새 변경안으로 덮어쓰지 않아요.", "실제 취소·저장 결과를 확인한 뒤 요청을 이어가 주세요."),
    "failed": ("요청 또는 처리 상태를 확인하지 못했어요.", "오류만으로 저장됐거나 저장되지 않았다고 단정할 수 없어요.", "현재 작업 상태를 확인한 뒤 다시 요청해 주세요."),
    "unsupported": ("요청한 기능이나 항목이 현재 지원 범위에 포함되지 않아요.", "다른 항목으로 몰래 바꿔 처리하지 않아요.", "지원하는 생활비·저축·월 쿠션 조정을 선택해 주세요."),
    "not_mine": ("현재 요청은 예산 조정과 다른 기능의 요청으로 보입니다.", "이 답변으로 예산 변경을 실행하지 않아요.", "해당 기능으로 이동하거나 예산 조정 요청을 알려 주세요."),
    "replied": ("현재 요청에 대한 서버 확인 결과를 안내해요.", "조회나 설명만으로 예산을 변경하지 않아요.", "필요한 내용을 확인하고 다음 선택을 해 주세요."),
    "proposal": ("금액과 보호 조건을 검수한 변경안이에요.", "최종 승인 전에는 금액·잠금·목표를 바꾸지 않아요.", "전후 금액과 설정을 확인하고 확정하거나 다시 조정해 주세요."),
    "unchanged": ("현재 예산과 같은 요청이며 추가로 바꿀 설정이 없어요.", "새 변경안을 저장하거나 같은 금액을 중복 반영하지 않아요.", "다른 금액을 요청하거나 대화를 종료할 수 있어요."),
    "back": ("직전의 미확정 선택을 다시 확인하려는 요청이에요.", "앞선 선택으로 돌아가지만 이미 승인한 예산은 되돌리지 않아요.", "원하는 조건을 다시 선택해 주세요."),
}


def step_explanation(kind, state=None):
    return dict(zip(FIELDS, STEPS.get(kind, STEPS["request"])))


def status_explanation(status, *, proposal=None, error=None):
    if error in {"task_check_failed", "invalid_saved_ref"}:
        return {"why": "서버에서 이 작업의 실제 저장·취소 결과를 확인하지 못했어요.",
                "effect": "저장됐는지 알 수 없어 성공·실패를 추정하거나 다시 승인하지 않아요.",
                "next": "작업 상태를 다시 확인해 주세요. 확인 전에는 같은 안을 중복 적용하지 마세요.",
                "brief": "저장·취소 여부가 미확인 상태예요. 같은 안을 중복 승인하지 마세요."}
    if error in {"call_failed", "invalid_json", "invalid_fields", "context_unavailable", "invalid_context", "simulation_failed", "proposal_failed"}:
        return {"why": "요청 해석·기준 자료 조회·변경안 검수 중 필요한 확인을 완료하지 못했어요.",
                "effect": "이 단계에서 새로운 승인 처리를 요청하지 않았어요. 기존 작업의 저장 여부는 별도 상태 확인을 따릅니다.",
                "next": "확인되지 않은 조건을 다시 알려 주거나 기준 자료를 조회한 뒤 재시도해 주세요."}
    if proposal:
        effect = "표시한 금액과 설정은 최종 승인 때 함께 반영해요. 지금은 미리보기예요."
        if proposal.get("unlock_categories"):
            effect += " 잠금 해제는 승인 시 실행되며 다음 주기에도 이어져요."
        if any(amount is None for amount in proposal.get("target_changes", {}).values()):
            effect += " 목표 해제는 다음 설계부터 해당 목표를 사용하지 않는 변경이에요."
        return {"why": "선택한 재원과 보호 조건을 서버가 검수해 변경안을 만들었어요.",
                "effect": effect, "next": "전후 금액·목표·잠금 변경을 확인하고 최종 확정하거나 다시 조정해 주세요."}
    return step_explanation(status if status in STEPS else "request")


def choice_descriptions(kind, choices):
    common = {
        "living": "기존 생활비 항목끼리 옮겨 생활비 총액을 유지해요.",
        "spare": "배분되지 않은 여윳돈과 해당 항목 사이의 배정을 바꿔요.",
        "extended": "저축·월 쿠션의 배분 계획까지 변경 대상에 포함해요. 누적 쿠션 사용과는 달라요.",
        "compare": "세 방법의 가능한 금액·영향·추천 근거를 비교하고 직접 선택해요.",
        "available": "서버가 보호 조건을 지키는 최대 가능액을 조회해요.",
        "preview": "제시된 금액으로 변경안을 미리 봐요. 아직 저장하지 않아요.",
        "manual": "자동 제안 금액 대신 희망 금액을 직접 정해요.",
        "choose_source": "현재 금액을 확정하지 않고 조정 범위를 다시 선택해요.",
        "back": "앞선 미확정 선택으로 돌아가며 이미 승인한 예산은 되돌리지 않아요.",
        "by": "말한 금액만큼 현재 배정액에 더하거나 빼요.",
        "to": "해당 항목의 전체 예산을 말한 금액으로 맞춰요.",
        "remaining": "기지출에 말한 잔액을 더해 전체 예산을 계산해요.",
        "up": "늘릴 금액을 어디서 가져올지 확인해요.",
        "down": "줄인 금액을 어디에 배분할지 확인해요.",
        "unlock": "최종 승인 시에만 해당 잠금을 풀고 예산을 바꿔요.",
        "keep_lock": "잠금을 유지하고 다른 조정 방법을 선택해요.",
        "cycle": "이번 주기만 바꾸고 기존의 다음 주기 목표는 별도 해제 요청이 없는 한 유지해요.",
        "ongoing": "직접 요청한 항목의 최종 금액을 다음 설계의 목표·기본 배분으로 유지해요.",
        "apply": "변경안과 다음 달 유지 여부를 확인하는 최종 확정 단계로 이동해요.",
        "cancel": "현재 미승인 조정을 취소하며 이미 승인한 예산을 되돌리지 않아요.",
        "partial_accept": "표시한 부분 반영액만 확인하고 최종 확정 단계로 이동해요. 부족액은 그대로 남아요.",
        "partial_amount": "현재 미승인안을 취소한 뒤 같은 조건에서 희망 금액을 다시 입력해요.",
        "partial_compare": "현재 미승인안을 취소하고 원래 요청액을 다른 재원에서도 충당할 수 있는지 비교해요.",
    }
    output = {}
    for value, label in choices:
        if value in common:
            output[value] = common[value]
        elif value.startswith("plan_"):
            output[value] = "이 안의 검수 금액과 재원으로 변경안을 확인해요. 추가 선택이 필요한 안은 먼저 확인 질문을 이어가요."
        elif kind in {"sources", "funding"}:
            output[value] = "선택한 항목에서 확인된 가용액만 재원으로 사용해요. 보호 조건은 유지해요."
        elif kind in {"destinations", "destination"}:
            output[value] = "선택한 항목에 배분할 금액을 계산하고 전체 예산 합계를 검수해요."
        else:
            output[value] = "이 항목을 조정 대상으로 확인한 뒤 필요한 금액·재원을 물어봐요."
    return output


def _limit_details(context, result):
    request = result.get("requests", {})
    items = request.get("items", []) if isinstance(request, dict) else request
    sources = list(request.get("sources", [])) if isinstance(request, dict) else []
    allocations = context.get("allocations", {})
    before = {**allocations, **context.get("buckets", {})}
    targets = result.get("requested_targets", {})
    direct = {item["category"] for item in items if not item.get("automatic", False)
              and targets.get(item["category"], before.get(item["category"], 0)) < before.get(item["category"], 0)}
    recipients = [code for code, target in targets.items() if target > before.get(code, 0)]
    candidates = []
    for code in sources or direct:
        candidates.extend(allocations if code == "LIVING" else [code])
    priorities = context.get("priorities", {})
    excluded = set(request.get("excluded", [])) if isinstance(request, dict) else set()
    if "LIVING" in excluded:
        excluded.update(allocations)
    candidates = sorted(dict.fromkeys(candidates), key=lambda code: priorities.get(code, float("inf")))
    details = []
    unit = context.get("fallback_unit", context.get("automatic_unit", 1))
    unit_label = "5천 원" if unit == 5000 else "1만 원" if unit == 10000 else f"{unit:,}원"
    for code in candidates:
        if code in recipients:
            continue
        name = NAMES.get(code, code)
        if code in excluded:
            details.append(f"{name} 항목은 이번 요청에서 사용하지 않기로 선택해 재원에서 제외했어요.")
            continue
        if code in context.get("buckets", {}):
            if context.get("bucket_status", {}).get(code) != "ready":
                details.append(f"{name}의 가용 상태는 확인되지 않았어요.")
                continue
            capacity = max(min(context.get("bucket_available", {}).get(code, 0),
                               before[code] - context.get("bucket_floors", {}).get(code, 0)), 0)
            capacity = capacity // unit * unit
            details.append(f"{name}에서 이미 실행·사용됐거나 보호해야 하는 부분을 제외한 이동 가능액은 {capacity:,}원이에요.")
            continue
        if code not in allocations:
            continue
        if code in context.get("locks", {}):
            details.append(f"{name} 항목은 {context['locks'][code]:,}원으로 잠겨 있어 자동 감액할 수 없어요.")
            continue
        if code not in direct and len(recipients) == 1:
            recipient = recipients[0]
            receiver_rank, rank = priorities.get(recipient), priorities.get(code)
            if rank is not None and (receiver_rank is None or rank <= receiver_rank):
                details.append(f"{rank}순위 {name} 항목은 요청 항목보다 낮은 순위가 아니어서 자동 재원으로 쓰지 않아요.")
                continue
        floor = max(context.get("minimums", {}).get(code, 0), context.get("system_floors", {}).get(code, 0),
                    context.get("spent", {}).get(code, 0))
        automatic = code not in direct
        reference = context.get("ml_reference") or {}
        if automatic and code not in context.get("ml_not_applicable", ("ETC",)):
            if code not in reference:
                details.append(f"{name}의 ML 참조가 없어 자동 감액 가능액을 확정하지 않아요.")
                continue
            floor = max(floor, min(allocations[code], reference[code]))
        after = result.get("allocations", allocations).get(code, allocations[code])
        if after <= floor:
            title = "자동 감액 하한" if automatic else "기지출·최소액 등 보호 하한"
            details.append(f"{name} 항목은 {title} {floor:,}원 아래로 줄일 수 없어요.")
        elif after - floor < unit:
            details.append(f"{name} 항목은 하한 {floor:,}원을 지키면 추가 이동 가능액이 {after - floor:,}원이라 "
                           f"{unit_label} 단위로 더 줄일 수 없어요.")
    return details[:4]


def _unit_note(context, result):
    unit = context.get("automatic_unit", 1)
    fallback = context.get("fallback_unit", unit)
    request = result.get("requests", {})
    items = request.get("items", []) if isinstance(request, dict) else request
    if unit <= 1 or not (items or result.get("transfers")):
        return ""
    if unit == 10000 and fallback == 5000:
        return "실제 예산은 기본 1만 원, 필요할 때 5천 원 단위로 맞춰요."
    label = "1만 원" if unit == 10000 else f"{unit:,}원"
    return f"보호 조건을 지키며 실제 예산을 {label} 단위로 맞춰요."


def result_explanation(context, result):
    status = result.get("status")
    unit_note = _unit_note(context, result)
    if status == "partial":
        summaries = [f"{NAMES.get(row['category'], row['category'])} 요청 {row['requested_amount']:,}원 중 "
                     f"{row['applied_amount']:,}원만 반영 가능하고 {row['missing_amount']:,}원이 부족해요."
                     for row in result.get("shortfalls", [])]
        calculation = {**context, "locks": {code: amount for code, amount in context.get("locks", {}).items()
                                            if code not in result.get("unlock_categories", [])}}
        limits = _limit_details(calculation, result)
        reasons = " ".join(summaries) + " 선택한 재원 범위와 항목별 보호 조건을 지키며 계산한 결과예요. "
        reasons += " ".join(filter(None, [unit_note, *limits]))
        effect = "부분 반영액만 새 변경안에 담았고 부족액은 다른 재원에서 자동으로 가져오지 않아요."
        if result.get("unlock_categories"):
            effect += " 동의한 잠금 해제는 최종 승인 때 실행하고 다음 주기에도 이어져요."
        return {"why": reasons, "effect": effect,
                "next": "가능한 금액만 반영하거나 희망 금액을 바꾸거나 다른 재원까지 비교해 주세요.",
                "brief": unit_note or next(iter(limits), "선택한 재원과 금액으로는 요청 전부를 충당할 수 없어요.")}
    if status in {"proposed", "unchanged"}:
        settings = result.get("unlock_categories") or result.get("target_changes")
        explanation = status_explanation("replied", proposal=result) if status == "proposed" or settings else {
            "why": "요청한 금액이 현재 배정액과 같아요.", "effect": "금액 변화가 없어도 목표 해제 같은 설정 변경은 별도로 확인해요.",
            "next": "설정 변경이 없다면 종료하거나 다른 금액을 요청해 주세요."}
        if unit_note:
            explanation.update(why=explanation["why"] + " " + unit_note, brief=unit_note)
        return explanation
    details = []
    alignment_required = False
    for conflict in result.get("conflicts", []):
        code, category = conflict.get("code"), conflict.get("category")
        name = NAMES.get(category, category or "해당 항목")
        if code == "lock_below_minimum":
            amount = context.get("locks", {}).get(category, 0)
            minimum = context.get("minimums", {}).get(category, 0)
            details.append(f"{name} 잠금액 {amount:,}원이 최소 보장액 {minimum:,}원보다 작아요. 두 설정을 먼저 확인해 주세요.")
        elif code == "lock_mismatch":
            details.append(f"{name}의 현재 예산과 잠금액이 달라요. 잠금 설정과 기준 예산을 먼저 확인해 주세요.")
        elif code == "locked_category":
            amount = context.get('locks', {}).get(category)
            details.append(f"{name}에 {amount:,}원 잠금이 설정돼 있어요." if amount is not None else "잠금 설정이 현재 예산·최소액과 맞지 않아요.")
        elif code in {"protected_amount_not_reached", "below_protected_amount"}:
            floor = conflict.get("minimum_amount", max(context.get("minimums", {}).get(category, 0),
                context.get("system_floors", {}).get(category, 0), context.get("spent", {}).get(category, 0),
                context.get("bucket_floors", {}).get(category, 0)))
            details.append(f"{name}의 최소액·기지출 등 보호 하한 {floor:,}원을 지킬 수 없어요.")
        elif code in {"ml_reference_unavailable", "bucket_unverified"}:
            details.append(f"{name}의 예측 참조나 가용액 자료가 확인되지 않았어요. 미확인을 0원이나 미실행으로 간주하지 않아요.")
        elif code == "bucket_disabled":
            details.append(f"{name} 재원 조정은 아직 지원되지 않아요.")
        elif code == "allocation_policy_required":
            details.append("같은 그룹 안에서 감액을 나누는 정책이 연결되지 않아 자동 금액을 확정할 수 없어요.")
        elif code == "spending_unverified":
            details.append("이번 주기의 지출 자료가 완전히 확인되지 않아 안전하게 옮길 금액을 계산할 수 없어요. 미확인을 0원으로 계산하지 않아요.")
        elif code == "unit_alignment_required":
            alignment_required = True
            categories = conflict.get("categories", [category] if category else [])
            names = "·".join(NAMES.get(value, value) for value in categories)
            details.append((names + "의 " if names else "현재 ") + "예산 금액·합계를 허용 단위로 맞출 수 없어 확인이 필요해요. "
                           "잠금액·총액·최소 보장액을 임의로 바꾸지 않아요.")
        elif code.startswith("excluded"):
            details.append(f"{name}은 이번 요청에서 변경하지 않기로 선택했어요.")
        elif code in {"destination_required", "living_decrease_requires_upper_destination"}:
            details.append("줄인 돈을 받을 항목이나 구체적인 배분 금액이 정해지지 않았어요.")
        elif code in {"source_required", "living_increase_requires_upper_source"}:
            details.append("증액을 충당할 수 있는 올바른 재원이 정해지지 않았어요.")
        elif code == "insufficient_funds":
            details.append("현재 허용한 재원에서 보호 조건을 지키며 옮길 수 있는 돈이 부족해요.")
            if unit_note:
                details.append(unit_note)
    return {"why": " ".join(dict.fromkeys(details)) or "현재 선택한 범위와 확인된 자료만으로 검수된 변경안을 만들 수 없어요.",
            "effect": "검수되지 않은 금액을 적용하거나 다른 재원을 자동으로 사용하지 않아요.",
            "next": ("잠금액은 유지하면서 허용 단위에 맞는 기준 예산과 합계를 확인해 주세요." if alignment_required else
                     "제한 사유에 맞춰 금액·재원을 다시 선택하거나 자료를 확인해 주세요."),
            "brief": next(iter(details), "선택한 범위의 가용액이나 필요한 자료를 다시 확인해 주세요.")}
