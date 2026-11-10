"""Opt-in API adapter. Approved initial policy; unverified history never enables B."""
from copy import deepcopy
from datetime import date
from fractions import Fraction
import json
from hashlib import sha256
import os
from pathlib import Path
import sys

from django.conf import settings

from .design_candidates import build_candidates, ml_profile, CATEGORY_NAMES
from .design_selection import choose_budget
from .recommend import EngineCheckFailed, check_result
from .design_units import finalize_budget
from .design_references import model_reference, transport_reference
from .design_targets import target_conflicts, final_target_changes
from .design_history import history_checks, verify_budget_history
from .design_consent import has_current_consent
from scripts.budget_design.inference import predict_ml

# shortcut: B helpers live in scripts; move them into budgets when the service data contract is finalized.
DESIGN_SCRIPTS = str(Path(__file__).resolve().parents[2] / 'scripts/budget_design')
if DESIGN_SCRIPTS not in sys.path:
    sys.path.append(DESIGN_SCRIPTS)
from plan_b_history import CHECKS, completed_months, summarize_history
from plan_b_budget import build_priority_history_budget
from plan_b_forecast import forecast_budget
from plan_selection import select_plan

ENGINE_VERSION = 'design-ab-20261107-history-proof'
# Initial recommendation only; later budget adjustments use their own rules.
INITIAL_TRANSFER_PERCENT = 20
INITIAL_POLICY = {'version': '2026-11-10-forecast-band', 'a': {},
                  'b_forecast_band': {'down_percent': 20, 'up_percent': 20}}


def initial_a_policy(config, inp, other_reserved):
    """Apply the overall living-budget cap; category amounts are limited by funds and protections."""
    policy = deepcopy(config)
    living = inp['target'] + other_reserved
    policy['total_limit'] = living * INITIAL_TRANSFER_PERCENT // 100
    policy['increase_rates'] = dict.fromkeys(CATEGORY_NAMES, None)
    for key in ('increase_limits', 'decrease_limits', 'ceilings'):
        policy[key] = dict.fromkeys(CATEGORY_NAMES, living)
    return policy


def enabled():
    return getattr(settings, 'BUDGET_DESIGN_ENABLED', os.getenv('BUDGET_DESIGN_ENABLED', '').lower() == 'true') is True


def external_enabled():
    return enabled() and getattr(settings, 'BUDGET_DESIGN_EXTERNAL_ENABLED',
        os.getenv('BUDGET_DESIGN_EXTERNAL_ENABLED', '').lower() == 'true') is True


def history_for_user(user, today):
    """Use recorded collection coverage. A verifier must separately confirm normalization per month."""
    from finance.api import listed_transactions, transaction_item
    from finance.merchant_rules import normalize_merchant
    from spending_analysis.coverage import coverage
    from spending_analysis.periods import Period

    months = completed_months(today)
    verifier = getattr(settings, 'BUDGET_HISTORY_VERIFIER', verify_budget_history)
    if verifier is not None and not callable(verifier):
        raise ValueError('invalid_history_verifier')
    proofs = history_checks(months, verifier(user, months) if verifier else {})
    result = {}
    for month in months:
        start = date.fromisoformat(month+'-01')
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        coverage_state = coverage(user, Period(start, end))['state']
        items = []
        for tx in listed_transactions(user).filter(occurred_on__gte=start, occurred_on__lt=end).order_by('occurred_on', 'pk'):
            row = transaction_item(tx)
            # Income and transfers have no category/funding in the display API and do not enter B.
            if row['transaction_type'] != 'spending':
                continue
            item = {key: row[key] for key in ('transaction_id', 'occurred_on', 'transaction_type', 'net_amount', 'category', 'funding')}
            merchant = normalize_merchant(tx.merchant_name)
            if merchant:
                item['merchant_key'] = sha256(merchant.casefold().encode()).hexdigest()
            if row['category'] == 'UNCLASSIFIED':
                item['suggestion_candidates'] = deepcopy(tx.suggestion_candidates)
                item['suggestion_source'] = tx.suggestion_source
            items.append(item)
        result[month] = {'items': items,
            'coverage_state': coverage_state,
            'coverage_complete': coverage_state == 'complete',
            'normalization_checks': {key: proofs.get(month, {}).get(key, 'unknown') for key in CHECKS}}
    return result


def _blocked(status, reason, selection=None):
    messages = {'needs_policy': '추천 설정을 준비 중이에요. 설정이 완료된 뒤 다시 시도해 주세요.',
                'needs_data': '추천에 필요한 정보를 확인하지 못했어요. 연결 상태와 입력 정보를 확인해 주세요.',
                'needs_upper_allocation_review': '현재 생활비로는 보호 조건을 모두 지키기 어려워요. 지킬 돈 배분을 다시 확인해 주세요.'}
    summary = ('추천 계산을 잠시 사용할 수 없어요. 잠시 후 다시 시도해 주세요.'
               if reason == 'ml_unavailable' else messages.get(status, '추천 예산을 만들지 못했어요.'))
    return {'status': status, 'reason': reason, 'summary': summary,
            'conflicts': [], '_design': {'selection': selection}}


def configured_policy():
    policy = getattr(settings, 'BUDGET_DESIGN_POLICY', None)
    if policy is None:
        try:
            raw = os.getenv('BUDGET_DESIGN_POLICY')
            policy = deepcopy(INITIAL_POLICY) if raw is None else json.loads(raw)
        except json.JSONDecodeError:
            return None
    return policy


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def reference_from_initial_input(inp):
    """Compatibility provider: callers supply the stored initial input, never the adjusted target."""
    return model_reference(inp, predict_ml(ml_profile(inp)))


def recommend(user, snapshot, available_spare, today, *, allow_external=False, synthetic=False):
    """External calls require an explicit server flag and synthetic data or current user consent."""
    if type(allow_external) is not bool or type(synthetic) is not bool:
        raise ValueError('invalid_external_permission')
    policy = configured_policy()
    if not isinstance(policy, dict):
        return _blocked('needs_policy', 'design_policy_required')
    if type(available_spare) is not int or available_spare < 0:
        raise EngineCheckFailed('invalid_available_spare')
    inp = deepcopy(snapshot['engine_input'])
    try:
        months = history_for_user(user, today)
        history = summarize_history(months, as_of=today)
        selection = select_plan(history)
    except (ValueError, TypeError, KeyError):
        return _blocked('needs_data', 'history_validation_failed')
    if selection['plan'] == 'a' and not isinstance(policy.get('a'), dict):
        return _blocked('needs_policy', 'a_policy_required', selection)
    try:
        ml = predict_ml(ml_profile(inp))
        ratios = {code: ml['ratios'][name] for code, name in CATEGORY_NAMES.items()}
    except (ValueError, TypeError, KeyError):
        return _blocked('needs_data', 'ml_unavailable', selection)
    reality = {'has_vehicle': inp['has_vehicle']}
    if history.get('status') == 'ready':
        reality['transport_history'] = history['categories']['TRANSPORT_CAR']['baseline_amount']
    from .design_reality import system_floors
    conflicts = target_conflicts(inp, system_floors(reality))
    if conflicts:
        blocked = _blocked('target_conflict', 'category_target_conflict', selection)
        blocked.update(conflicts=conflicts, summary='저장한 유지 목표를 현재 보호 조건·금액 단위·생활비와 함께 맞출 수 없어요.')
        return blocked
    references = model_reference(inp, ml)
    references['transport_history_reference'] = transport_reference(history, fingerprint(months))
    a_policy = None
    b_cut_rates = dict.fromkeys(CATEGORY_NAMES, 100)
    consent_verified = allow_external and not synthetic and has_current_consent(user)
    llm_allowed = allow_external and (synthetic or consent_verified)
    llm = {'status': 'not_run', 'reason': 'consent_required' if allow_external else 'external_disabled'}
    forecast_limits = {}
    try:
        if selection['plan'] == 'a':
            a_policy = initial_a_policy(policy['a'], inp, snapshot['etc'])
            engine = build_candidates(inp, ratios, policy=a_policy, reality_profile=reality)
            if engine['status'] != 'proposed':
                return {**(engine.get('result') or _blocked(engine['status'], engine.get('reason'), selection)),
                        '_design': {'selection': selection}}
            chosen = next(c for c in engine['candidates'] if c['id'] == 'base')
            out, explanations = chosen['result'], chosen['explanations']
            if llm_allowed:
                selected = choose_budget(inp, engine, ml_ratios=ratios, reality_profile=reality,
                    living_budget=inp['target'] + snapshot['etc'], other_reserved=snapshot['etc'],
                    fallback_to_base=True, synthetic=synthetic, trace_config={'LANGSMITH_TRACING': 'false'})
                out = {**out, 'allocations': selected['allocations']}
                check_result(inp, out)
                explanations = selected['explanations']
                source, reason = selected['selection_source'], selected.get('fallback')
                llm = {'status': 'completed' if source == 'llm_tools' else
                       'not_run' if source == 'single_candidate' or reason == 'missing_api_key' else 'fallback',
                       'source': source, 'reason': reason}
            transfer = 0
            view = {**out, 'result': out, 'explanations': explanations, 'engine': engine, 'history': history,
                    'system_floors': engine.get('system_floors', {})}
        else:
            weights = {code: Fraction(str(v)) for code, v in ratios.items()}
            reference = {code: inp['target'] * v / sum(weights.values()) for code, v in weights.items()}
            options = dict(as_of=today, shortfall_cut_rates=b_cut_rates, other_reserved=snapshot['etc'],
                           reality_profile=reality, ml_reference=reference, available_spare=available_spare)
            if llm_allowed and policy.get('b_forecast_band') is not None:
                result = forecast_budget(inp, months, **options, target_month=today.month,
                    forecast_band=policy['b_forecast_band'], allow_external=True, synthetic=synthetic,
                    consent_verified=consent_verified)
                llm = {key: result.get(key) for key in ('attempts', 'retries', 'validation_failures', 'violation_types')}
                llm.update(status=result['llm_status'], reason=result.get('fallback') or result.get('reason'),
                           variant='b_forecast')
                if result['llm_status'] == 'completed':
                    forecast_limits = result['bounds']
            else:
                result = build_priority_history_budget(inp, months, **options)
                if llm_allowed:
                    llm['reason'] = 'forecast_band_required'
            if result['status'] != 'proposed':
                blocked = _blocked(result['status'], result.get('reason'), selection)
                shortfall = result.get('funding_shortfall')
                if result['status'] == 'needs_upper_allocation_review' and type(shortfall) is int and shortfall > 0:
                    blocked['summary'] = f'여윳돈을 보태도 생활비가 {shortfall:,}원 부족해요. 지킬 돈 배분을 다시 확인해 주세요.'
                if result['status'] == 'infeasible':
                    blocked.update(result['result'])
                return blocked
            out, explanations = result['result'], result['explanations']
            transfer = result['spare_transfer']
            view = result
    except EngineCheckFailed:
        raise
    except (ValueError, TypeError, KeyError):
        return _blocked('needs_policy', 'invalid_design_policy', selection)
    final = finalize_budget(inp, view, policy=a_policy,
        b_cut_rates=b_cut_rates if selection['plan'] == 'b' else None,
        lower_bounds={c: b['lower'] for c, b in forecast_limits.items()},
        upper_bounds={c: b['upper'] for c, b in forecast_limits.items()},
        other_reserved=snapshot['etc'], other_minimum=snapshot.get('minimums', {}).get('ETC', 0),
        other_locked='ETC' in snapshot.get('locks', {}))
    if final['status'] != 'proposed':
        return _blocked(final['status'], final['reason'], selection)
    out, explanations, transfer = final['result'], final['explanations'], final['spare_transfer']
    checked = {**inp, 'target': final['funded_target']}
    check_result(checked, out)
    target_changes = final_target_changes(snapshot.get('category_targets', inp.get('category_targets', {})),
                                        {**out['allocations'], 'ETC': final['other_reserved']},
                                        {**view['allocations'], 'ETC': snapshot['etc']})
    return {**out, '_design': {'selection': selection, 'engine_version': ENGINE_VERSION,
        'policy_fingerprint': fingerprint(policy), 'history_fingerprint': fingerprint(months),
        'spare_transfer': transfer, 'available_spare': available_spare, 'funded_target': checked['target'],
        'other_reserved': final['other_reserved'], 'rounding': final['rounding'],
        'candidate_adjustment': final.get('candidate_adjustment'),
        'llm': llm,
        'references': references, 'target_conflicts': target_changes,
        'explanations': [{'category': e['category'], 'text': e['text']} for e in explanations]}}


def validate_funding(snapshot, out, available_spare):
    meta = out.get('_design', {})
    if not isinstance(meta, dict) or type(available_spare) is not int or available_spare < 0:
        raise EngineCheckFailed('invalid_funding_metadata')
    transfer = meta.get('spare_transfer', 0)
    if type(transfer) is not int or not 0 <= transfer <= available_spare:
        raise EngineCheckFailed('invalid_spare_transfer')
    other = meta.get('other_reserved', snapshot['etc'])
    if (type(other) is not int or other < snapshot.get('minimums', {}).get('ETC', 0)
            or ('ETC' in snapshot.get('locks', {}) and other != snapshot['locks']['ETC'])):
        raise EngineCheckFailed('invalid_other_reservation')
    inp = {**snapshot['engine_input'], 'target': snapshot['engine_input']['target'] + transfer + snapshot['etc'] - other}
    if meta and meta.get('funded_target') != inp['target']:
        raise EngineCheckFailed('funded_target_mismatch')
    check_result(inp, out)
    if meta and (any(v % 5000 for c, v in out['allocations'].items() if c not in inp['locks'])
                 or ('ETC' not in snapshot.get('locks', {}) and other % 5000)):
        raise EngineCheckFailed('invalid_budget_unit')
    return transfer


def approval_ready(user, budget, today):
    meta = (budget.validation_summary or {}).get('design', {})
    if not meta:
        return True
    try:
        transfer = meta.get('spare_transfer')
        available = meta.get('available_spare')
        if (type(transfer) is not int or type(available) is not int or not 0 <= transfer <= available
                or meta.get('funded_target') != budget.input_snapshot['engine_input']['target'] + transfer
                    + budget.input_snapshot['etc'] - meta.get('other_reserved', budget.input_snapshot['etc'])):
            return False
        return (enabled() and meta.get('engine_version') == ENGINE_VERSION
                and meta.get('policy_fingerprint') == fingerprint(configured_policy())
                and meta.get('history_fingerprint') == fingerprint(history_for_user(user, today)))
    except (ValueError, TypeError, KeyError):
        return False
