"""Compare actual Plan A ML and Plan B history budgets on one synthetic user.

Edit test_input(). --llm calls both agents (A up to three calls, B one).
No database writes, result files or production policy changes.
"""
import argparse
from copy import deepcopy
from datetime import date
from fractions import Fraction
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from run_plan_b_test import synthetic_input
from simulator import CATEGORIES, predict_ml
from plan_selection import select_plan


def test_input(case='shortfall'):
    """All policy numbers below are editable synthetic experiment values only."""
    inp, months, codes = synthetic_input()
    if case == 'surplus':
        inp['target'] = 1_300_000
    elif case == 'medical':
        for row, amount in zip(months.values(), (0, 0, 600_000)):
            next(item for item in row['items'] if item['category'] == 'MEDICAL_HEALTH')['net_amount'] = amount
    elif case != 'shortfall':
        raise ValueError('invalid_comparison_case')
    top = [r['category'] for r in inp['priorities'] if r['rank'] <= 3]
    return {
        'input': inp, 'months': months, 'as_of': date(2026, 11, 3), 'other_reserved': 0,
        'profile': {'age': 27, 'sex': 'female', 'has_vehicle': False,
                    'take_home_income': 3_000_000, 'housing_fixed': 700_000},
        'a_policy': {'total_limit': 100_000, 'increase_rates': dict.fromkeys(top, 10),
                     'increase_limits': dict.fromkeys(top, 100_000),
                     'decrease_limits': dict.fromkeys(codes, 50_000),
                     'ceilings': dict.fromkeys(top, 1_000_000)},
        'b_cut_rates': dict.fromkeys(codes, 50), 'increase_threshold': 0.1,
    }


def run_a(case, use_llm):
    from budgets.design_candidates import build_candidates, ml_profile, select_candidate
    from budgets.design_selection import choose_budget, MODEL
    inp = case['input']
    ml = case.get('_ml')
    if ml is None:
        ml = predict_ml(ml_profile({**case['profile'], 'target': inp['target']}))
    ratios = {code: ml['ratios'][name] for code, name in CATEGORIES.items()}
    reality = {'has_vehicle': case['profile']['has_vehicle']}
    engine = build_candidates(inp, ratios, policy=case['a_policy'], reality_profile=reality)
    out = {'status': engine['status'], 'allocations': None, 'engine': engine,
           'model': MODEL if use_llm else None, 'ml_artifact': ml['artifact_id']}
    if engine['status'] != 'proposed':
        return {**out, 'reason': engine.get('reason', engine['status'])}
    if use_llm:
        selected = choose_budget(inp, engine, ml_ratios=ratios, reality_profile=reality,
                                 living_budget=inp['target'] + case['other_reserved'],
                                 other_reserved=case['other_reserved'])
    else:
        choice = select_candidate(inp, engine['candidates'])
        selected = {'allocations': choice['result']['allocations'], 'selection_source': 'rule',
                    'fallback': choice['fallback'], 'explanations': choice['explanations']}
    return {**out, **selected}


def run_b(case, use_llm):
    from plan_b_llm import design_history_budget
    return design_history_budget(case['input'], case['months'], as_of=case['as_of'],
        shortfall_cut_rates=case['b_cut_rates'], increase_threshold=case['increase_threshold'],
        other_reserved=case['other_reserved'], variant=case.get('b_variant', 'priority_increase_first'),
        baseline=case.get('baseline', 'one_off_adjusted'),
        user_reincluded_one_offs=case.get('user_reincluded_one_offs', ()),
        reality_profile={'has_vehicle': case['profile']['has_vehicle']}, allow_external=use_llm,
        ml_reference=case.get('ml_reference'), available_spare=case.get('available_spare', 0))


def _prepare_ml(case):
    from budgets.design_candidates import ml_profile
    case['_ml'] = predict_ml(ml_profile({**case['profile'], 'target': case['input']['target']}))
    weights = {c: Fraction(str(case['_ml']['ratios'][label])) for c, label in CATEGORIES.items()}
    case['ml_reference'] = {c: case['input']['target']*v/sum(weights.values()) for c,v in weights.items()}


def recommend_budget(case, *, use_llm=False):
    """Run only the selected plan. A B-calculation failure must not silently switch to A."""
    from plan_b_history import summarize_history
    case = deepcopy(case)
    selection = select_plan(summarize_history(case['months'], as_of=case['as_of']))
    key = ('b_forecast' if selection['plan'] == 'b' and case.get('include_forecast')
           and case.get('forecast_enabled') else selection['plan'])
    runner = {'a': run_a, 'b': run_b, 'b_forecast': run_forecast}[key]
    start = perf_counter()
    try:
        _prepare_ml(case)
    except Exception as error:
        result = {'status': 'error' if selection['plan'] == 'a' else 'needs_data',
                  'allocations': None, 'reason': type(error).__name__ if selection['plan'] == 'a'
                  else 'ml_reference_unavailable'}
    else:
        try:
            result = runner(case, use_llm)
        except Exception as error:
            result = {'status': 'error', 'allocations': None, 'reason': type(error).__name__}
    return {'selection': selection, 'selected_variant': key,
            'result': {**result, 'seconds': perf_counter() - start}}


def compare(case, *, use_llm=False):
    from plan_b_history import summarize_history
    case = deepcopy(case)
    history = summarize_history(case['months'], as_of=case['as_of'])
    outputs = {'history': history, 'target': case['input']['target'], 'use_llm': use_llm,
               'selection': select_plan(history),
               'available_spare': case.get('available_spare', 0),
               'priorities': case['input']['priorities']}
    try:
        _prepare_ml(case)
        outputs['ml_reference'] = deepcopy(case['ml_reference'])
    except Exception as error:
        outputs['ml_error'] = type(error).__name__
    runners = [('a', run_a), ('b', run_b)]
    if case.get('include_forecast'):
        case['b_variant'] = 'priority_order'
        runners.append(('b_forecast', run_forecast))
    for name, runner in runners:
        start = perf_counter()
        try:
            if 'ml_error' in outputs:
                result = {'status': 'error' if name == 'a' else 'needs_data', 'allocations': None,
                          'reason': outputs['ml_error'] if name == 'a' else 'ml_reference_unavailable'}
            else:
                result = runner(deepcopy(case), False if name == 'b' and case.get('include_forecast') else use_llm)
        except Exception as error:
            # One unavailable model must not hide the other result or expose inputs in errors.
            result = {'status': 'error', 'reason': type(error).__name__, 'allocations': None}
        outputs[name] = {**result, 'seconds': perf_counter() - start}
    outputs['history'] = outputs['b'].get('history', history)
    selected = outputs['selection']['plan']
    key = 'b_forecast' if selected == 'b' and case.get('include_forecast') and case.get('forecast_enabled') else selected
    outputs.update(selected_variant=key, selected_result=outputs[key])
    return outputs


def run_forecast(case, use_llm=False):
    from plan_b_forecast import forecast_budget
    return forecast_budget(case['input'], case['months'], as_of=case['as_of'],
        target_month=case['target_month'], forecast_band=case.get('forecast_band'),
        user_note=case.get('user_note', ''), text_limit=case.get('forecast_text_limit', 120),
        shortfall_cut_rates=case['b_cut_rates'], other_reserved=case['other_reserved'],
        user_reincluded_one_offs=case.get('user_reincluded_one_offs', ()),
        reality_profile={'has_vehicle': case['profile']['has_vehicle']},
        allow_external=case.get('forecast_enabled', False), synthetic=True, ml_reference=case.get('ml_reference'),
        available_spare=case.get('available_spare', 0))


def print_comparison(out):
    print('합성 데이터 비교 / 금액 원 단위 / 정책 수치는 테스트용 / 저장 없음')
    if 'selection' in out:
        choice = out['selection']
        print(f"자동 선택: {choice['plan'].upper()} / {choice['reason']} / 최대 거래 건수: {choice['max_category_count']}")
    print(f"초기 배분 예산: {out['target']:,}원 (기타 제외) / 기존 여윳돈 {out.get('available_spare', 0):,}원")
    print('우선순위: ' + ', '.join(f"{r['rank']}위 {CATEGORIES[r['category']]}" for r in out['priorities']))
    for name in ('a', 'b'):
        result = out[name]
        mode = result.get('selection_source', result.get('llm_status', '-'))
        print(f"{name.upper()}: {result['status']} / 처리: {mode} / {result['seconds']:.2f}초")
        if result.get('reason') or result.get('fallback'):
            print(f"  사유: {result.get('reason') or result.get('fallback')}")
        trace = result.get('langsmith', {})
        if trace:
            print(f"  LangSmith: {trace['status']} / {trace.get('trace_id', '-')}")
    a, b = out['a'].get('allocations'), out['b'].get('allocations')
    history = out['history'].get('categories', {})
    print('\n카테고리 | 3개월 평균(표시 반올림) | A: ML 기반 | B: 이력 기반 | B − A')
    for code, label in CATEGORIES.items():
        average = history.get(code, {}).get('average')
        columns = [label, '-' if average is None else f'{float(average):,.0f}',
                   '-' if a is None else f'{a[code]:,}', '-' if b is None else f'{b[code]:,}',
                   '-' if a is None or b is None else f'{b[code]-a[code]:+,}']
        print(' | '.join(columns))
    print('합계 | - | ' + ('-' if a is None else f'{sum(a.values()):,}') +
          ' | ' + ('-' if b is None else f'{sum(b.values()):,}'))
    if b is not None:
        print(f"B 여윳돈: {out['b'].get('total_spare', out['b'].get('unallocated', 0)):,}원 / 생활비 충당: {out['b'].get('spare_transfer', 0):,}원")
    for row in out['b'].get('summary', []):
        print('B 안내: ' + row['text'])
    for name in ('a', 'b'):
        for row in out[name].get('explanations', []):
            if row['text']:
                print(f"{name.upper()} 안내: {row['text']}")
    print('\nA는 ML 기반 증액·이동, B는 이력 기반 감액 규칙을 유지합니다. 한도 방식과 LLM 역할이 달라 품질 순위로 해석하지 마세요.')
    print('B surplus는 설정·시스템 하한 반영 후 여윳돈을 남깁니다. 기존 shortfall은 원래 낮은 소비를 유지합니다.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm', action='store_true', help='A·B 모두 실제 LLM 사용 (API 비용 발생)')
    parser.add_argument('--auto', action='store_true', help='데이터 기준으로 A/B를 선택해 해당 안만 실행')
    parser.add_argument('--case', choices=('shortfall', 'surplus', 'medical'), default='shortfall',
                        help='생활비 부족 / 평소보다 여유 / 의료비 한 달 급증 합성 사례')
    args = parser.parse_args(argv)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    if args.llm:
        from budgets.design_selection import MODEL
        print(f"LLM 모델: A={MODEL}, B={os.environ.get('LLM_MODEL') or '미설정'} (기존 설정 유지)")
    if args.auto:
        out = recommend_budget(test_input(args.case), use_llm=args.llm)
        choice, result = out['selection'], out['result']
        print(f"자동 선택: {choice['plan'].upper()} / {choice['reason']} / 최대 거래 건수: {choice['max_category_count']}")
        print(f"계산 상태: {result['status']} / 실행 경로: {out['selected_variant']}")
        return 0 if result['status'] == 'proposed' else 2
    out = compare(test_input(args.case), use_llm=args.llm)
    print_comparison(out)
    if any(out[p]['status'] != 'proposed' for p in ('a', 'b')):
        return 2
    if args.llm and (out['a'].get('fallback') or out['b'].get('llm_status') != 'completed'):
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
