"""Plan B synthetic comparison. --llm enables one paid OpenAI call and opt-in tracing.

Edit synthetic_input() to try amounts/settings. No real user data or DB writes.
Rates and thresholds are explicit experiment inputs, never service defaults.
"""
import argparse
from datetime import date
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))


def synthetic_input():
    from plan_b_history import CATEGORY_CODES, CHECKS
    inp = {'target': 1_000_000, 'minimums': {'FOOD': 350_000},
           'locks': {'EDUCATION': 100_000},
           'priorities': [{'category': c, 'rank': rank} for rank, c in
                          enumerate(('FOOD', 'EDUCATION', 'MEDICAL_HEALTH'), 1)]}
    values = {'FOOD': [400_000]*3, 'EDUCATION': [100_000]*3,
              'MEDICAL_HEALTH': [100_000]*3, 'FASHION_SHOPPING': [100_000, 100_000, 400_000],
              'CULTURE_LEISURE': [200_000]*3, 'BEAUTY': [100_000]*3}
    months = {}
    for i, month in enumerate(('2026-08', '2026-09', '2026-10')):
        months[month] = {
            'coverage_complete': True,
            # Synthetic records only; this does not verify production transactions.
            'normalization_checks': dict.fromkeys(CHECKS, 'verified'),
            'items': [{'occurred_on': month + '-15', 'net_amount': amounts[i],
                       'merchant_key': 'synthetic-recurring-' + code,
                       'transaction_type': 'spending', 'category': code, 'funding': 'living'}
                      for code, amounts in values.items()]}
    return inp, months, CATEGORY_CODES


def run_comparison(*, cut_rate, increase_threshold, use_llm=False, user_protected=()):
    from plan_b_llm import design_history_budget
    inp, months, codes = synthetic_input()
    options = dict(as_of=date(2026, 11, 3), shortfall_cut_rates=dict.fromkeys(codes, cut_rate),
                   user_protected=user_protected, increase_threshold=increase_threshold)
    rule = design_history_budget(inp, months, **options)
    increase_rule = design_history_budget(inp, months, **options, variant='priority_increase_first')
    selected = (design_history_budget(inp, months, **options, variant='priority_increase_first',
                                      allow_external=True) if use_llm else increase_rule)
    return rule, increase_rule, selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm', action='store_true', help='합성 데이터로 OpenAI 1회 호출')
    parser.add_argument('--cut-rate', type=float, required=True, help='테스트 감액 한도(%%)')
    parser.add_argument('--increase-threshold', type=float, required=True,
                        help='테스트 증가율 기준(예: 0.1 = 10%%)')
    parser.add_argument('--protect', action='append', default=[], help='되돌리기 보호 카테고리 코드')
    args = parser.parse_args(argv)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    try:
        rule, increase_rule, selected = run_comparison(cut_rate=args.cut_rate,
            increase_threshold=args.increase_threshold, use_llm=args.llm, user_protected=args.protect)
    except Exception as error:
        print(f'실행 오류 종류: {type(error).__name__}')
        return 2
    print('합성 데이터 전용 / 감액률·임계치는 테스트값 / 저장 없음')
    print(f"상태: {selected['status']} / LLM: {selected['llm_status']} / 호출: {selected['llm_calls']}")
    print(f"대체 사유: {selected['fallback']}")
    trace = selected['langsmith']
    print(f"LangSmith: {trace['status']} / trace_id: {trace.get('trace_id', '-')}")
    if selected['status'] != 'proposed':
        print(f"계산 중단: {selected.get('reason', selected['status'])}")
        return 2
    print('카테고리 | 순위 나눔 | 늘어난 곳 먼저(규칙) | 최종 (원)')
    for code, amount in selected['allocations'].items():
        print(f"{code} | {rule['allocations'][code]:,} | {increase_rule['allocations'][code]:,} | {amount:,}")
    print(f"합계: {sum(selected['allocations'].values()):,}원 / 증가 항목: {selected.get('increased', [])}")
    for explanation in selected.get('explanations', []):
        if explanation['text']:
            print(explanation['text'])
    return 3 if args.llm and selected['llm_status'] != 'completed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
