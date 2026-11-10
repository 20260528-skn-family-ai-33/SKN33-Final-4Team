"""직접 수정·실행하는 합성 예산 테스트. 금액/순위는 test_input()에서 변경하세요.

옵션 없음: 직접 입력 초안 + 서버 계산 (외부 호출 없음)
--ml: 실제 로컬 ML 초안 사용
--llm: OpenAI로 조정 판단 (최대 3회 API 호출, 비용 발생)
--ml --llm: 실제 ML → LLM 판단 → 서버 도구 계산
LangSmith: .env의 LANGSMITH_TRACING=true 설정 시 --llm 실행을 추적
DB·파일 저장 없음. 실제 개인정보·거래 데이터는 입력하지 마세요.
"""
import argparse
from time import perf_counter

from simulator import CATEGORIES, calculate, example


def test_input():
    # 이 함수 안을 수정하세요. 전부 테스트 예시이며 서비스 확정 정책이 아닙니다.
    form = example()
    form['living'] = '1050000'       # 고정비 제외 총생활비
    form['etc_minimum'] = '50000'    # 기타 확보액
    form['etc_lock'] = ''           # 빈칸=잠금 없음, '0'=0원 잠금
    form['total_limit'] = '100000'   # 최소금액 충족 이후 개인화 총이동 한도

    # --ml일 때 사용. 직접 입력 초안 모드에서는 아래 프로필을 사용하지 않습니다.
    form['profile'].update(age='27', sex='여성', car='없음',
                           income='3000000', housing_fixed='700000')

    # 직접 입력 초안 합계 = 총생활비 - 기타 확보액. --ml일 때 draft는 사용하지 않음.
    drafts = {
        'FOOD': 300000,             # 식비
        'HOUSING_TELECOM': 0,       # 주거·통신
        'TRANSPORT_CAR': 100000,    # 교통·차량
        'HOUSEHOLD': 100000,        # 생활용품
        'FASHION_SHOPPING': 100000, # 패션·쇼핑
        'MEDICAL_HEALTH': 100000,   # 의료·건강
        'BEAUTY': 100000,           # 뷰티·미용
        'EDUCATION': 100000,        # 교육
        'CULTURE_LEISURE': 100000,  # 문화·여가
        'TRAVEL_LODGING': 0,        # 여행·숙박
    }
    # 앞에서부터 1, 2, 3순위. 여행 제외, 최소 3개를 연속으로 지정.
    priorities = ['FOOD', 'EDUCATION', 'MEDICAL_HEALTH']
    for code, row in form['rows'].items():
        row['draft'] = str(drafts[code])
        row['rank'] = str(priorities.index(code) + 1) if code in priorities else ''
        row.update(rate='10', increase='1000000', decrease='50000', ceiling='1000000')

    # 최소금액·잠금 예시: 빈칸을 '300000'처럼 바꾸세요.
    form['rows']['FOOD']['minimum'] = ''
    form['rows']['MEDICAL_HEALTH']['lock'] = ''
    return form


def print_result(result):
    engine = result['engine']
    if engine['status'] != 'proposed':
        print('결과: 조건 충돌 / 추천 금액 없음')
        for conflict in engine['result']['conflicts']:
            print(conflict)
        return 2
    base = engine['candidates'][0]['result']['allocations']
    rule = engine['candidates'][-1]['result']['allocations']
    selected = result['selected']
    tracing = selected.get('langsmith', {'status': 'disabled'})
    print(f"LangSmith: {tracing['status']} / trace_id: {tracing.get('trace_id', '-')}")
    final = selected['allocations']
    print(f"초안: {result['source']} / 처리: {selected['selection_source']}")
    print('카테고리 | 사용한 초안 | 기본안 | 규칙 개인화안 | 최종 추천 (원)')
    for code, name in CATEGORIES.items():
        draft = '미사용' if result['source'] == 'ml_skipped' else f"{result['draft'][code]:,.2f}"
        print(f'{name} | {draft} | {base[code]:,} | {rule[code]:,} | {final[code]:,}')
    print(f"기타: {result['etc']:,} / 기타 포함 최종 합계: {sum(final.values()) + result['etc']:,}")
    for decision in selected.get('decisions', []):
        print(f"처리 기록: {decision['category']} / {decision['action']}")
    for warning in engine['warnings']:
        print(f'경고: {warning}')
    if selected['fallback']:
        print(f"LLM 실패·미사용, 규칙 계산으로 대체: {selected['fallback']}")
        return 3
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--ml', action='store_true', help='직접 입력 초안 대신 실제 ML 사용')
    parser.add_argument('--llm', action='store_true', help='OpenAI 조정 판단 사용 (API 비용 발생)')
    args = parser.parse_args(argv)
    print(f"실행 모드: ML={'ON' if args.ml else 'OFF'}, OpenAI={'ON' if args.llm else 'OFF'}")
    start = perf_counter()
    try:
        result = calculate(test_input(), use_ml=args.ml, use_llm=args.llm)
    except ValueError as error:
        print(f'입력/실행 확인: {error}')
        return 2
    except Exception as error:
        print(f'실행 오류 종류: {type(error).__name__}')
        return 2
    code = print_result(result)
    print(f'실행 시간: {perf_counter() - start:.2f}초')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
