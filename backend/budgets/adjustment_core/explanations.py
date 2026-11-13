"""User-facing reasons, effects and next actions; facts come from server results."""
from importlib import import_module

CATEGORY_NAMES = {code: name for code, name, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES}


NAMES = {**CATEGORY_NAMES, "ETC": "기타", "LIVING": "생활비", "SAVINGS": "저축",
         "CUSHION_MONTHLY": "월 쿠션 적립액", "SPARE": "여윳돈"}
FIELDS = ("why", "effect", "next")
STEPS = {'request': ('바꾸고 싶은 항목부터 정하면 돼요.', '', '예를 들어 “식비를 늘리고 싶어”라고 말해 주세요.'),
    'category': ('어떤 예산을 바꿀지 아직 정하지 않았어요.', '', '식비·교통비처럼 바꿀 항목을 골라 주세요.'),
    'amount': ('원하는 금액으로 예산을 미리 계산해요.', '미리보기만으로 예산이 바뀌지는 않아요.', '금액 칸에 직접 입력하거나 버튼으로 바꿔 주세요.'),
    'amount_mode': ('말씀한 금액만큼 바꾸는 것인지, 전체 예산을 그 금액으로 맞추는 것인지 확인이 필요해요.', '', '두 방법 중 원하는 쪽을 골라 주세요.'),
    'direction': ('예산을 늘릴지 줄일지 아직 정하지 않았어요.', '', '늘리기 또는 줄이기를 골라 주세요.'),
    'funding_scope': ('돈을 어디에서 가져오는지에 따라 줄어드는 예산이 달라져요.', '고른 방법으로 먼저 미리보기를 만들어요.', '가능한 방법을 고르거나, 직접 줄여도 되는 생활비를 정해 주세요.'),
    'sources': ('예산을 늘리려면 다른 곳에 배정된 돈을 가져와야 해요.', '선택한 항목의 예산이 줄어들어요.', '돈을 가져올 항목을 골라 주세요.'),
    'destinations': ('줄인 예산을 어디에 넣을지 정하는 단계예요.', '받는 항목의 예산이 늘어나요.', '받을 항목을 고르고 금액을 입력해 주세요.'),
    'funding': ('선택한 곳에서 가져올 수 있는 돈이 부족해요.', '', '늘릴 금액을 줄이거나 돈을 가져올 곳을 바꿔 주세요.'),
    'destination': ('줄인 돈을 받을 항목이 아직 정해지지 않았어요.', '', '어느 항목에 얼마씩 넣을지 알려 주세요.'),
    'available_quote': ('이미 쓴 돈과 남겨 두기로 한 금액을 제외하고 옮길 수 있는 돈을 계산했어요.', '모두 옮겨야 한다는 뜻은 아니에요.', '원하는 금액으로 바꾸거나 이 금액의 미리보기를 확인해 주세요.'),
    'comparison': ('같은 요청을 서로 다른 방법으로 계산한 예시예요.', '추천은 선택을 돕는 표시이고, 예산은 확정해야 바뀌어요.', '각 카드의 “이 안 선택”을 누르면 확정 전 미리보기가 나와요.'),
    'partial': ('선택한 곳에서 가져올 수 있는 돈이 요청한 금액보다 적어요.', '부족한 돈을 다른 곳에서 임의로 가져오지 않아요.', '가능한 금액만 옮기거나, 가져올 곳을 바꿔 주세요.'),
    'unlock': ('금액을 고정해 둔 항목이라 잠금을 풀어야 바꿀 수 있어요.', '잠금은 예산 확정 때 풀리고 다음 예산에도 이어져요.', '잠금을 풀지, 그대로 둘지 골라 주세요.'),
    'scope': ('이번 예산만 바꿀지, 다음 예산에도 같은 금액을 유지할지 확인이 필요해요.', '', '원하는 적용 기간을 골라 주세요.'),
    'clarification': ('말씀하신 내용 중 한 가지를 더 확인해야 해요.', '', ''),
    'saved': ('예산 변경이 완료됐어요.', '확정한 금액과 설정을 반영했어요.', ''),
    'canceled': ('이번 조정을 취소했어요.', '기존에 확정한 예산은 그대로예요.', ''),
    'expired': ('예산이나 지출 내역이 바뀌어서 이전 미리보기를 그대로 적용할 수 없어요.', '확정한 예산을 되돌리지는 않아요.', '원하는 변경을 다시 입력해 최신 금액으로 계산해 주세요.'),
    'rejected': ('이 변경안은 적용하지 않았어요.', '기존 예산은 그대로예요.', '다른 금액이나 방법으로 다시 조정할 수 있어요.'),
    'saving': ('예산을 저장하고 있어요.', '', '완료 안내가 나올 때까지 기다려 주세요.'),
    'canceling': ('취소가 완료됐는지 확인하고 있어요.', '', '결과가 나올 때까지 기다려 주세요.'),
    'failed': ('처리 결과를 확인하지 못했어요.', '예산이 저장됐는지는 아직 알 수 없어요.', '현재 예산을 확인한 뒤 다시 시도해 주세요.'),
    'unsupported': ('이 대화에서는 생활비·저축·월 쿠션 예산을 조정할 수 있어요.', '', '조정할 항목을 알려 주세요.'),
    'not_mine': ('다른 기능에서 도와드릴 수 있는 요청이에요.', '', ''),
    'replied': ('요청하신 내용을 확인했어요.', '', ''),
    'proposal': ('요청한 항목과 금액으로 계산한 미리보기예요.', '“예산 확정하기”를 눌러야 실제 예산에 반영돼요.', '바뀌는 금액을 확인해 주세요. 원하면 다시 수정할 수 있어요.'),
    'unchanged': ('요청한 금액이 현재 예산과 같아요.', '바뀌는 금액이 없어요.', '다른 금액으로 바꾸려면 알려 주세요.'),
    'back': ('앞선 선택으로 돌아왔어요.', '확정한 예산은 되돌리지 않아요.', '원하는 조건을 다시 골라 주세요.')}
STEPS.update({
    'discussion': ('바꾸고 싶은 예산과 지켜야 할 조건을 함께 정하는 단계예요.', '', '금액을 몰라도 원하는 방향부터 말해 주세요.'),
    'distribution': ('줄인 돈을 원하는 항목에 나누는 단계예요.', '나눈 금액의 합계는 줄인 돈과 같아야 해요.', '받을 항목을 고르고 금액을 입력해 주세요.'),
    'manual_distribution': ('자동 추천 대신 직접 줄일 항목과 금액을 정하는 단계예요.', '이미 쓴 돈과 잠금·최소 금액 설정은 계속 확인해요.', '줄여도 되는 항목을 고르고 금액을 입력해 주세요.'),
})


def step_explanation(kind, state=None):
    if kind == 'amount':
        return {}
    if kind == 'destinations' and any(item.get('category') == 'LIVING' and item.get('direction') == 'up'
                                    for item in (state or {}).get('requests', [])):
        return {'title': '늘릴 생활비 항목 고르기', 'why': '생활비에 더할 돈을 어느 항목에 넣을지 정하는 단계예요.',
                'effect': '아직 돈을 옮기거나 예산을 바꾸지 않았어요.', 'next': '예산을 늘릴 항목을 골라 주세요.'}
    titles = {"comparison": "조정안 선택 방법", "partial": "요청한 금액을 모두 옮길 수 없는 이유",
              "unlock": "잠금 해제 전에 확인할 내용", "amount": "금액 입력 방법", "proposal": "예산 확정 전에 확인할 내용",
              "sources": "돈을 가져올 곳 고르기", "destinations": "줄인 돈을 나누는 방법"}
    if kind not in STEPS:
        return {}
    return {"title": titles.get(kind, "이 단계 안내"), **dict(zip(FIELDS, STEPS[kind]))}


def status_explanation(status, *, proposal=None, error=None):
    if error in {"task_check_failed", "invalid_saved_ref"}:
        return {"why": "서버에서 이 작업의 실제 저장·취소 결과를 확인하지 못했어요.",
                "effect": "저장됐는지 알 수 없어 성공·실패를 추정하거나 다시 승인하지 않아요.",
                "next": "작업 상태를 다시 확인해 주세요. 확인 전에는 같은 안을 중복 적용하지 마세요.",
                "brief": "저장·취소 여부가 미확인 상태예요. 같은 안을 중복 승인하지 마세요."}
    if error == 'call_limit':
        return {'title': '대화를 이어가려면', 'why': '이번 조정에서 사용할 수 있는 대화 횟수에 도달했어요.',
                'effect': '확정 전 변경안은 유지했어요. 이번 요청으로 예산을 새로 저장하지 않았어요.',
                'next': '현재 변경안을 확인하거나, 조정을 취소한 뒤 새로 시작해 주세요.'}
    if error == 'spending_unverified':
        return result_explanation({}, {'conflicts': [{'code': error}]})
    if error == 'invalid_over_handoff':
        return {'title': '초과 내역을 확인할 수 없어요', 'why': '예산 화면에서 넘어온 항목이나 초과 금액을 확인하지 못했어요.',
                'effect': '기존 예산은 바뀌지 않았어요.', 'next': '예산 화면에서 초과한 항목을 다시 선택해 주세요.'}
    if error:
        if error in {'context_unavailable', 'invalid_context'}:
            why, next_step = '현재 예산 정보를 불러오지 못했어요.', '예산 화면에서 현재 예산을 확인한 뒤 다시 시도해 주세요.'
        elif error in {'simulation_failed', 'proposal_failed', 'invalid_comparison_result', 'invalid_available_result', 'planner_unavailable'}:
            why, next_step = '변경안을 계산하는 중 문제가 생겼어요.', '잠시 후 같은 요청을 다시 보내 주세요.'
        elif error == 'replay_mismatch':
            why, next_step = '선택한 안을 현재 예산으로 다시 확인하지 못했어요.', '최신 예산으로 조정안을 다시 비교해 주세요.'
        elif error in {'call_failed', 'feedback_unavailable'} or str(error).startswith('invalid_'):
            why, next_step = '말씀하신 요청을 읽는 중 문제가 생겼어요.', '바꾸고 싶은 항목을 다시 알려 주세요. 금액은 몰라도 괜찮아요.'
        else:
            why, next_step = '이번 요청의 처리 결과를 확인하지 못했어요.', '현재 예산과 변경안 상태를 확인한 뒤 다시 시도해 주세요.'
        return {'title': '요청을 처리하지 못한 이유', 'why': why,
                'effect': '이번 요청으로 예산을 새로 확정하지 않았어요.', 'next': next_step}
    if proposal:
        effect = "표시한 금액과 설정은 최종 승인 때 함께 반영해요. 지금은 미리보기예요."
        if proposal.get("unlock_categories"):
            effect += " 잠금 해제는 승인 시 실행되며 다음 주기에도 이어져요."
        if any(amount is None for amount in proposal.get("target_changes", {}).values()):
            effect += " 해제한 유지 목표는 다음 예산부터 적용하지 않아요."
        return {"title": "예산 확정 전에 확인할 내용", "why": "요청한 항목과 금액으로 계산한 미리보기예요.",
                "effect": effect, "next": "바뀌는 금액과 설정을 확인한 뒤 “예산 확정하기”를 눌러 주세요."}
    return step_explanation(status)


def choice_descriptions(kind, choices):
    common = {
        "living": "기존 생활비 항목끼리 옮겨 생활비 총액을 유지해요.",
        "spare": "배분되지 않은 여윳돈과 해당 항목 사이의 배정을 바꿔요.",
        "extended": "저축·월 쿠션의 배분 계획까지 변경 대상에 포함해요. 누적 쿠션 사용과는 달라요.",
        "compare": "현재 계산 가능한 방법의 금액과 예산 변화를 비교해요.",
        "manual_living": "줄여도 괜찮은 생활비 항목과 금액을 직접 정해요. 확정 전까지 예산은 바뀌지 않아요.",
        "available": "이미 쓴 돈과 남겨 둘 돈을 제외하고 얼마나 옮길 수 있는지 확인해요.",
        "preview": "제시된 금액으로 변경안을 미리 봐요. 아직 저장하지 않아요.",
        "manual": "자동 제안 금액 대신 희망 금액을 직접 정해요.",
        "choose_source": "현재 금액을 확정하지 않고 조정 범위를 다시 선택해요.",
        "back": "앞선 미확정 선택으로 돌아가며 이미 승인한 예산은 되돌리지 않아요.",
        "by": "말한 금액만큼 현재 배정액에 더하거나 빼요.",
        "to": "해당 항목의 전체 예산을 말한 금액으로 맞춰요.",
        "remaining": "이미 쓴 돈에 말한 잔액을 더해 전체 예산을 계산해요.",
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
        "partial_compare": "다른 곳에서 돈을 가져오는 방법으로 다시 비교해요.",
    }
    output = {}
    for value, label in choices:
        if value in common:
            output[value] = common[value]
        elif value.startswith("plan_"):
            output[value] = "이 방법의 미리보기를 열어요. 아직 예산을 확정하지 않아요."
        elif kind in {"sources", "funding"}:
            output[value] = "이 항목에서 돈을 가져와요. 이미 쓴 돈과 남겨 둘 돈은 지켜요."
        elif kind in {"destinations", "destination"}:
            output[value] = "이 항목에 돈을 더해요."
        else:
            output[value] = "이 항목의 예산을 얼마나 바꿀지 정해요."
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
    for code in candidates:
        if code in recipients:
            continue
        name = NAMES.get(code, code)
        if code in excluded:
            details.append(f"{name} 항목은 이번 요청에서 사용하지 않기로 선택해 돈을 가져올 곳에서 제외했어요.")
            continue
        if code in context.get("buckets", {}):
            if context.get("bucket_status", {}).get(code) != "ready":
                details.append(f"{name}에서 얼마를 옮길 수 있는지 아직 확인하지 못했어요.")
                continue
            capacity = max(min(context.get("bucket_available", {}).get(code, 0),
                               before[code] - context.get("bucket_floors", {}).get(code, 0)), 0)
            capacity = capacity // unit * unit
            details.append(f"{name}에서 이미 사용한 돈과 남겨 둘 돈을 빼면 {capacity:,}원을 옮길 수 있어요.")
            continue
        if code not in allocations:
            continue
        if code in context.get("locks", {}):
            details.append(f"{name} 항목은 {context['locks'][code]:,}원으로 잠겨 있어 자동 감액할 수 없어요.")
            continue
        if code not in direct and len(recipients) == 1:
            recipient = recipients[0]
            receiver_rank, rank = priorities.get(recipient), priorities.get(code)
            if rank is not None and code not in sources:
                details.append(f'{rank}순위로 정한 {name}은 먼저 줄여도 되는지 확인해야 해요. 생활비 직접 조정에서 선택할 수 있어요.')
                continue
            if rank is not None and (receiver_rank is None or rank <= receiver_rank):
                details.append(f"{name}은 더 중요하거나 같은 순위로 정한 항목이라 자동으로 줄이지 않아요.")
                continue
        floor = max(context.get("minimums", {}).get(code, 0), context.get("system_floors", {}).get(code, 0),
                    context.get("spent", {}).get(code, 0))
        automatic = code not in direct
        reference = context.get("ml_reference") or {}
        if automatic and code not in context.get("ml_not_applicable", ("ETC",)):
            if code not in reference:
                details.append(f"{name}에 평소 필요한 금액을 확인하지 못해 자동으로 줄일 금액을 정할 수 없어요.")
                continue
            floor = max(floor, min(allocations[code], reference[code]))
        after = result.get("allocations", allocations).get(code, allocations[code])
        if after <= floor:
            if automatic:
                details.append(f"자동 추천에서는 {name}에 {floor:,}원을 남겨요. 더 줄이고 싶으면 생활비 직접 조정을 선택해 주세요.")
            else:
                details.append(f"{name}: 이미 쓴 돈이나 최소 금액 설정 때문에 {floor:,}원은 남겨야 해요.")
        elif after - floor < unit:
            details.append(f"{name}에 {floor:,}원을 남기면 추가로 옮길 수 있는 돈이 거의 없어요.")
    return details[:4]


def unavailable_explanation(context, comparison):
    if any(plan.get('status') == 'ready' and (plan.get('result') or {}).get('transfers')
           and all(row['from'] in context['allocations'] for row in plan['result']['transfers'])
           for plan in comparison.get('plans', [])):
        return None
    hidden = [plan for plan in comparison.get('plans', []) if not (
        plan.get('status') in {'ready', 'partial'} and plan.get('amount', 0) > 0
        and (plan.get('result') or {}).get('changes'))]
    if not hidden:
        return None
    reasons = []
    for plan in hidden:
        result = plan.get('result') or {}
        if context.get('spending_status', 'complete') != 'complete':
            why = '최근 지출 내역을 다 불러오지 못해 옮길 수 있는 돈을 확인할 수 없어요.'
        elif result.get('conflicts'):
            why = result_explanation(context, result)['why']
            if any(c.get('code') == 'insufficient_funds' for c in result['conflicts']):
                why += ' ' + ' '.join(_limit_details(context, result)[:2])
        elif plan.get('status') == 'needs_destination':
            why = '돈을 받을 항목을 먼저 골라야 계산할 수 있어요.'
        elif plan.get('status') == 'needs_data':
            why = '여기에서 옮길 수 있는 금액을 아직 확인하지 못했어요.'
        elif plan.get('status') == 'needs_policy':
            why = '자동으로 나누는 기능을 지금 사용할 수 없어요.'
        elif plan.get('status') == 'needs_confirmation':
            why = '현재 예산 금액과 잠금·최소 금액 설정을 함께 맞출 수 없어 다시 확인해야 해요.'
        else:
            why = '순위·잠금·남겨 둘 금액을 지키면 자동으로 옮길 여유가 없어요.'
        reasons.append(f"{plan['label']}: {why}")
    if comparison.get('status') == 'unavailable' and comparison.get('message'):
        reasons.insert(0, comparison['message'])
    return {'title': '추천에서 제외한 방법과 이유',
            'brief': '지금은 ' + ' · '.join(plan['label'] for plan in hidden) + ' 방법을 추천에서 제외했어요.',
            'why': '\n'.join(reasons), 'effect': '',
            'next': '표시된 방법을 고르거나, 생활비 직접 조정에서 항목과 금액을 정해 주세요.'}


def result_explanation(context, result):
    status = result.get("status")
    if status == "partial":
        summaries = [f"{NAMES.get(row['category'], row['category'])} 요청 {row['requested_amount']:,}원 중 "
                     f"{row['applied_amount']:,}원만 반영 가능하고 {row['missing_amount']:,}원이 부족해요."
                     for row in result.get("shortfalls", [])]
        calculation = {**context, "locks": {code: amount for code, amount in context.get("locks", {}).items()
                                            if code not in result.get("unlock_categories", [])}}
        limits = _limit_details(calculation, result)
        reasons = " ".join(summaries) + " 이미 쓴 돈과 남겨 두기로 한 금액은 제외했어요. "
        reasons += " ".join(limits)
        effect = "가능한 금액만 미리보기에 담았어요. 부족한 돈을 다른 곳에서 가져오지는 않아요."
        if result.get("unlock_categories"):
            effect += " 동의한 잠금 해제는 최종 승인 때 실행하고 다음 주기에도 이어져요."
        return {"why": reasons, "effect": effect,
                "next": "가능한 금액만 옮기거나 돈을 가져올 곳을 바꿔 주세요.",
                "title": "요청한 금액을 모두 옮길 수 없는 이유", "brief": next(iter(summaries), "가져올 수 있는 돈이 요청한 금액보다 적어요.")}
    if status in {"proposed", "unchanged"}:
        settings = result.get("unlock_categories") or result.get("target_changes")
        explanation = status_explanation("replied", proposal=result) if status == "proposed" or settings else {
            "why": "요청한 금액이 현재 배정액과 같아요.", "effect": "금액 변화가 없어도 목표 해제 같은 설정 변경은 별도로 확인해요.",
            "next": "설정 변경이 없다면 종료하거나 다른 금액을 요청해 주세요."}
        return explanation
    details = []
    alignment_required = False
    for conflict in result.get("conflicts", []):
        code, category = conflict.get("code", ''), conflict.get("category")
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
            details.append(f"{name}는 이미 쓴 돈이나 최소 금액 설정 때문에 {floor:,}원 아래로 줄일 수 없어요.")
        elif code in {"ml_reference_unavailable", "bucket_unverified"}:
            details.append(f"{name}에서 얼마를 옮길 수 있는지 아직 확인하지 못했어요.")
        elif code == "bucket_disabled":
            details.append(f"{name}에서 돈을 가져오는 기능은 아직 사용할 수 없어요.")
        elif code == "allocation_policy_required":
            details.append("여러 항목에서 자동으로 돈을 나누어 가져오는 기능을 지금 사용할 수 없어요. 항목별 금액을 직접 정해 주세요.")
        elif code == "spending_unverified":
            details.append("최근 지출 내역을 아직 다 불러오지 못했어요. 계좌·카드 내역을 새로 조회한 뒤 다시 계산할 수 있어요.")
        elif code == "unit_alignment_required":
            alignment_required = True
            categories = conflict.get("categories", [category] if category else [])
            names = "·".join(NAMES.get(value, value) for value in categories)
            details.append((names + "의 " if names else "현재 ") + "예산 금액과 잠금·최소 금액 설정을 함께 맞출 수 없어요. "
                           "잠금액·총액·최소 보장액을 임의로 바꾸지 않아요.")
        elif code.startswith("excluded"):
            details.append(f"{name}은 이번 요청에서 변경하지 않기로 선택했어요.")
        elif code in {"destination_required", "living_decrease_requires_upper_destination"}:
            details.append("줄인 돈을 받을 항목이나 구체적인 배분 금액이 정해지지 않았어요.")
        elif code in {"source_required", "living_increase_requires_upper_source"}:
            details.append("늘릴 돈을 어디에서 가져올지 아직 정하지 않았어요.")
        elif code == "insufficient_funds":
            details.append("선택한 곳에서 가져올 수 있는 돈이 요청한 금액보다 적어요.")
        elif code == 'maximum_not_verifiable':
            details.append('옮길 수 있는 최대 금액을 확인하지 못했어요. 최대 금액을 추측해 제안하지 않아요.')
    next_steps = {
        'spending_unverified': '계좌·카드 연결 관리에서 최근 내역을 조회한 뒤 다시 확인해 주세요.',
        'bucket_unverified': '계좌·카드 내역을 다시 조회하거나 다른 곳에서 돈을 가져오는 방법을 골라 주세요.',
        'ml_reference_unavailable': '생활비 직접 조정에서 줄여도 괜찮은 항목과 금액을 정해 주세요.',
        'locked_category': '잠금을 풀어도 되는지 결정하거나 다른 항목을 골라 주세요.',
        'destination_required': '줄인 돈을 받을 항목을 골라 주세요.',
        'source_required': '늘릴 돈을 가져올 항목을 골라 주세요.',
        'allocation_policy_required': '생활비 직접 조정에서 항목별 금액을 정해 주세요.',
        'living_decrease_requires_upper_destination': '줄인 생활비를 여윳돈·저축·월 쿠션 중 어디에 넣을지 골라 주세요.',
        'living_increase_requires_upper_source': '늘릴 생활비를 여윳돈·저축·월 쿠션 중 어디에서 가져올지 골라 주세요.',
        'bucket_disabled': '화면에 표시된 다른 방법을 골라 주세요.',
        'maximum_not_verifiable': '원하는 금액을 직접 입력하면 그 금액으로 조정 가능한지 확인할 수 있어요.',
        'protected_amount_not_reached': '줄일 금액을 낮추거나 다른 항목을 골라 주세요.',
        'below_protected_amount': '줄일 금액을 낮추거나 다른 항목을 골라 주세요.',
        'insufficient_funds': '늘릴 금액을 줄이거나 돈을 가져올 곳을 바꿔 주세요.',
        'lock_below_minimum': '예산 화면에서 잠금과 최소 금액 설정을 확인해 주세요.',
        'lock_mismatch': '예산 화면에서 잠금과 현재 예산을 확인해 주세요.',
        'excluded_requested_category': '유지할 항목을 다시 정하거나 다른 항목을 골라 주세요.',
        'excluded_destination': '유지할 항목을 다시 정하거나 다른 항목을 골라 주세요.',
    }
    next_step = next((next_steps[c['code']] for c in result.get('conflicts', []) if c.get('code') in next_steps),
                     '현재 예산과 변경 조건을 다시 확인한 뒤 시도해 주세요.')
    return {"title": "지금 조정할 수 없는 이유", "why": " ".join(dict.fromkeys(details)) or "현재 정보로는 변경안을 계산하지 못했어요.",
            "effect": "기존 예산은 바뀌지 않았어요.",
            "next": ("예산 화면에서 잠금과 최소 금액 설정을 확인한 뒤 다시 조정해 주세요." if alignment_required else
                     next_step),
            "brief": next(iter(details), "현재 정보로는 옮길 수 있는 금액을 확인하지 못했어요.")}
