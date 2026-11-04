"""Deterministic synthetic ledger; real merchant identities, synthetic purchases."""
import argparse
import calendar
import csv
from collections import Counter
from datetime import date
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'local_workspace/data/alpha_test'
REPORT = ROOT / 'local_workspace/reports/alpha_test'
SEED = 20261105
# shortcut: 11/5 알파 전용 기간, 다음 테스트 때 기간·예산·정답을 함께 갱신한다.
BUDGETS = dict(FOOD=520000, TRANSPORT_CAR=100000, HOUSEHOLD=70000,
               FASHION_SHOPPING=100000, MEDICAL_HEALTH=50000, BEAUTY=50000,
               EDUCATION=50000, CULTURE_LEISURE=80000, ETC=30000)
# role, category, October days, fixed amount, variant bounds, weekday, time
RULES = [
 ('lunch','FOOD',[2,5,6,7,8,12,13,14,15,16,19,20,21,22,26,27,28,29],10000,8000,13000,None,'12:10'),
 ('coffee','FOOD',[2,5,6,7,8,12,13,14,15,16,19,20,21,22,26,27],4000,3000,6000,None,'13:00'),
 ('dinner','FOOD',[3,6,10,14,21,28],15000,12000,20000,None,'19:30'),
 ('grocery','FOOD',[4,11,18,25],25000,20000,35000,6,'16:00'),
 ('weekend','FOOD',[17,31],33000,25000,40000,5,'18:30'),
 ('transit','TRANSPORT_CAR',[30],60000,50000,80000,None,'08:00'),
 ('taxi','TRANSPORT_CAR',[10,24],10000,8000,18000,5,'22:30'),
 ('household','HOUSEHOLD',[11,25],30000,20000,40000,6,'14:00'),
 ('fashion','FASHION_SHOPPING',[17],90000,50000,120000,5,'15:00'),
 ('clinic','MEDICAL_HEALTH',[20],18000,10000,30000,1,'18:30'),
 ('pharmacy','MEDICAL_HEALTH',[20],12000,5000,20000,1,'18:50'),
 ('hair','BEAUTY',[18],25000,20000,40000,6,'11:00'),
 ('cosmetics','BEAUTY',[25],15000,10000,30000,6,'15:00'),
 ('books','EDUCATION',[4,18],15000,12000,25000,6,'15:00'),
 ('cinema','CULTURE_LEISURE',[3,17],15000,12000,18000,5,'20:00'),
 ('bowling','CULTURE_LEISURE',[11,25],20000,15000,30000,6,'17:00'),
 ('laundry','ETC',[4,18],10000,8000,15000,6,'10:00'),
 ('convenience','UNCLASSIFIED',[7,21],5000,3000,10000,2,'21:00'),
]
FIXED = [('rent','월세',600000,5,'HOUSING_TELECOM','A'),
         ('maintenance','관리비',70000,10,'HOUSING_TELECOM','A'),
         ('utility','전기·가스·수도 합산',30000,15,'HOUSING_TELECOM','A'),
         ('telecom','휴대폰 요금',50000,20,'HOUSING_TELECOM','A'),
         ('ott','넷플릭스 온라인 구독',12000,8,'CULTURE_LEISURE','E'),
         ('music','멜론 온라인 구독',8000,12,'CULTURE_LEISURE','E')]


def generate():
    with (DATA / 'merchants.csv').open(encoding='utf-8-sig', newline='') as f:
        merchants = {r['role']: r for r in csv.DictReader(f)}
    rng, rows = random.Random(SEED), []

    def add(month, day, time, role, amount, category='UNCLASSIFIED', kind='spending',
            funding='living', instrument='D', direction='out', pair='', duplicate=False,
            ans='', description=''):
        when = date(2026, month, day)
        if when > date(2026,11,5):
            return
        merchant = merchants.get(role, {})
        row = dict(source_key=f'alpha-persona:{len(rows)+1:04d}', occurred_on=when.isoformat(),
                   occurred_time=time, weekday=when.weekday(), role=role,
                   merchant_name=merchant.get('merchant_name', 'CGV 온라인 예매' if role=='cinema' else description),
                   amount=amount, direction=direction, transaction_type=kind,
                   confirmed_category=category, funding_bucket=funding,
                   instrument=instrument, source_kind='card' if instrument in ('D','E') else 'bank',
                   pair_key=pair, is_duplicate=int(duplicate), ans_category_if_user_set=ans,
                   is_fixed=int(funding=='fixed'), fixed_role=role if funding=='fixed' else '',
                   metadata_source='store_snapshot' if merchant else 'online_manual' if role in ('ott','music','cinema') else 'non_store',
                   source_description=description or merchant.get('merchant_name', ''))
        row.update({k:merchant.get(k,'') for k in ('store_id','branch_name','industry_large_code','industry_large',
                    'industry_medium_code','industry_medium','industry_small_code','industry_small',
                    'sigungu','dong','legal_dong','source_snapshot')})
        rows.append(row)
        if instrument=='D':
            row['pair_key']=row['source_key']
            add(month,day,time,role,amount,category,kind,funding,'B',direction,
                pair=row['pair_key'],duplicate=True,description=row['source_description'])

    for month in range(7,12):
        add(month,1,'09:00','salary',3000000,kind='income',instrument='A',direction='in',description='합성 회사 급여')
        for instrument,direction in [('A','out'),('B','in')]:
            add(month,1 if month<10 else 2,'09:00','own_transfer',1050000,kind='own_transfer',instrument=instrument,
                direction=direction,pair=f'own:{month}',description='내 생활통장 이체')
        for instrument,direction in [('A','out'),('C','in')]:
            add(month,2,'09:10','saving',900000,kind='saving',instrument=instrument,
                direction=direction,pair=f'saving:{month}',description='첫목돈적금 납입')
        add(month,14,'10:00','card_payment',140000,kind='card_payment',instrument='A',description='전월 신용카드 청구 납부')
        add(month,17,'12:00','lend',50000,kind='transfer',instrument='A',description='지인 단기 대여')
        add(month,18,'12:00','repay',50000,kind='income',instrument='A',direction='in',description='지인 대여금 회수')
        for role,cat,octdays,amount,low,high,weekday,time in RULES:
            if month==10:
                days=octdays
            else:
                eligible=[d for d in range(1,calendar.monthrange(2026,month)[1]+1)
                          if (date(2026,month,d).weekday()==weekday if weekday is not None
                              else date(2026,month,d).weekday()<5)]
                if role=='transit': days=[calendar.monthrange(2026,month)[1]]
                elif role=='dinner': days=[3,6,10,14,21,28]
                else: days=[eligible[min(i*len(eligible)//len(octdays),len(eligible)-1)] for i in range(len(octdays))]
            for day in days:
                value=amount if month>=10 else rng.randrange(low,high+1,100)
                add(month,day,time,role,value,cat,description={'taxi':'택시 요금','transit':'대중교통 후불 합산'}.get(role,''))
        for role,name,amount,day,cat,instrument in FIXED:
            add(month,day,'10:00',role,amount,cat,funding='fixed',instrument=instrument,description=name)
        add(month,24,'15:00','hotel',120000,'TRAVEL_LODGING',funding='free',instrument='E')
    # Demo changes November only; October amounts, count and balances stay intact.
    for day,amount,category,label in [(3,18000,'FOOD','점심 더치페이'),(5,22000,'CULTURE_LEISURE','볼링 더치페이')]:
        add(11,day,'18:00','dutch',amount,kind='transfer',instrument='B',ans=category,description=label)
    rows.sort(key=lambda r:(r['occurred_on'],r['occurred_time'],r['source_key']))
    opening={'A':2000000,'B':200000,'C':5400000}
    for r in rows:
        if r['occurred_on']<'2026-10-01' and r['source_kind']=='bank':
            opening[r['instrument']] -= r['amount']*(1 if r['direction']=='in' else -1)
    balances=dict(opening)
    for r in rows:
        if r['source_kind']=='bank':
            balances[r['instrument']]+=r['amount']*(1 if r['direction']=='in' else -1)
            r['balance_after']=balances[r['instrument']]
            assert r['balance_after']>=0, (r['occurred_on'],r['instrument'],r['balance_after'],opening)
        else: r['balance_after']=''
    return rows,opening


def summarize(rows, opening, through, *, after=False):
    selected=[r for r in rows if r['occurred_on']<=through]
    month=through[:7]
    current=[r for r in selected if r['occurred_on'].startswith(month)]
    consumption=[]
    for r in current:
        if r['is_duplicate']: continue
        if r['transaction_type']=='spending' or (after and r['ans_category_if_user_set']):
            consumption.append({**r,'category':r['ans_category_if_user_set'] if after and r['ans_category_if_user_set'] else r['confirmed_category']})
    categories=Counter()
    for r in consumption: categories[r['category']]+=r['amount']
    living=sum(r['amount'] for r in consumption if not r['is_fixed'] and r['category'] not in ('HOUSING_TELECOM','TRAVEL_LODGING'))
    balances=dict(opening)
    for r in selected:
        if r['source_kind']=='bank': balances[r['instrument']]+=r['amount']*(1 if r['direction']=='in' else -1)
    return dict(as_of=through,raw_rows=len(current),spending_count=len(consumption),
                living_spent=living,living_remaining=1050000-living,
                housing=categories['HOUSING_TELECOM'],travel=categories['TRAVEL_LODGING'],
                outside_budget=categories['HOUSING_TELECOM']+categories['TRAVEL_LODGING'],
                fixed=sum(r['amount'] for r in consumption if r['is_fixed']),
                total_spending=sum(categories.values()),by_category=dict(sorted(categories.items())),
                unclassified_count=sum(r['category']=='UNCLASSIFIED' for r in consumption),
                unclassified_amount=categories['UNCLASSIFIED'],balances=balances,
                demand_balance=balances['A']+balances['B'],
                travel_free_deduction_assumed=categories['TRAVEL_LODGING'],
                free_remaining_if_deducted=130000-categories['TRAVEL_LODGING'])


def answers(rows,opening):
    return {f'2026-{m:02d}':{state:summarize(rows,opening,date(2026,m,5 if m==11 else calendar.monthrange(2026,m)[1]).isoformat(),after=state=='after')
                            for state in ('before','after')} for m in range(7,12)}


def markdown_answers(expected):
    lines=['<!-- alpha-answers:start -->','## 9 알파 테스트 확장 정답',
           '아래 표는 `generate.py`가 거래 원장에서 계산한다. 7~10월은 월 전체, 11월은 11/5까지이며 서버 시계를 바꾸지 않는다. 실제 날짜보다 미래인 거래는 시드에 적재하면 안 된다.',
           '| 기간 | 생활비 변경 전 | 전체 소비 변경 전 | 전체 소비 변경 후 | 주거·여행 | 원장 행 | 소비 건수 전→후 |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for month,states in expected['months'].items():
        b,a=states['before'],states['after']
        lines.append(f"| {month} | {b['living_spent']:,} | {b['total_spending']:,} | {a['total_spending']:,} | {b['outside_budget']:,} | {b['raw_rows']} | {b['spending_count']}→{a['spending_count']} |")
    lines+=['','### 월말과 11월 5일 잔액','','더치페이 분류 전후의 은행 잔액은 동일하다. 계좌 출금은 이미 발생했기 때문이다.',
            '| 기간 | 월급 A | 생활 B | 적금 C | 입출금 합계 |','| --- | ---: | ---: | ---: | ---: |']
    for month,states in expected['months'].items():
        b=states['before'];cash=b['balances']
        lines.append(f"| {month} | {cash['A']:,} | {cash['B']:,} | {cash['C']:,} | {b['demand_balance']:,} |")
    lines+=['','### 더치페이 시연','','11/3 18:00 점심 정산 18,000원은 `ans_category_if_user_set=FOOD`, 11/5 18:00 볼링 정산 22,000원은 `CULTURE_LEISURE`다. 초기 `transaction_type=transfer`, `confirmed_category=UNCLASSIFIED`이며 비소비다. 사용자 카테고리 선택 API로 바꿔야 소비가 된다. 정답 열은 사용자 선택을 위한 외부 정답이며 분류기 입력에 넣지 않는다.',
            '','11/5 기준 생활비는 변경 전 246,000원 → 변경 후 286,000원, 남은 생활비는 804,000원 → 764,000원이다. 10월 단기 대여·회수는 그대로 두었으며 변경 시연 대상이 아니다.',
            '','### 최근 3개월 해석','','11/5에 “최근 완료된 3개월”은 8~10월이다. 7~9월은 별도 과거 비교 구간이다. 요청 해석에 따라 두 기간을 혼용하지 않는다.',
            '','### 파일과 재생성','','- 거래 원장: `local_workspace/data/alpha_test/transactions.csv`',
            '- 상가 원문 발췌: `local_workspace/data/alpha_test/merchants.csv`',
            '- 월별 정답과 시작 잔액: `local_workspace/reports/alpha_test/answers.json`',
            '- 생성기: `local_workspace/scripts/alpha_test/generate.py`',
            '- seed 고정값: `20261105`',
            '','```powershell','.venv/Scripts/python.exe local_workspace/scripts/alpha_test/generate.py',
            '.venv/Scripts/python.exe local_workspace/scripts/alpha_test/generate.py --check',
            '.venv/Scripts/python.exe -m unittest discover -s local_workspace/tests -p test_alpha_persona.py -v','```',
            '','7~9월은 규칙의 금액 범위 안에서 100원 단위 변형이다. 10월과 11월은 고정 금액을 사용한다. 7~9월 생활통장 이체는 월초 구매의 잔액 부족을 막기 위해 1일에 넣고, 10~11월은 기존대로 2일이다. 7월 시작 잔액은 이전 달 추가 자료 때문에 10/1 시작 잔액이 달라지지 않도록 역산했다.',
            '','거래 원장의 상호는 실재 상가 스냅샷을 참조하지만 결제와 금액은 전부 합성이다. 현재 영업 여부나 실제 가격은 검증한 것이 아니다. 원문 상호·지점을 분리하고 `store_id`·업종 대중소 코드/명·시군구·행정동·법정동·스냅샷 근거를 보존한다. 영화는 CGV 온라인 예매로 직접 추가했다. 교통 요금·개인 월세·급여·송금에는 상가 업종이 없어 `non_store`, 온라인 결제에는 `online_manual`로 표시하고 상가 ID·업종을 조작하지 않는다.',
            '','### 시드와 계정 사용','','풀 받은 `backend/spending_fixture/seed.py`에 `--alpha`를 연결했다. 구현은 `local_workspace/scripts/alpha_test/seed.py`에 두어 기존 3인 시드와 분리했다. 첨부한 scope의 생활비 계산은 `backend/budgets/scope.py`에 추가하고 10월 930,000원을 검증했다. 기본 시드는 그대로이며 알파는 전용 두 UUID·이메일만 재생성한다.',
            '','시드는 `alpha.home@example.test` 승인 계정과 `alpha.onboarding@example.test` 온보딩 계정 2개를 생성한다. 승인 계정에는 11/1~12/1 주기, 월급 3,000,000원, 생활비 1,050,000원, 고정 770,000원, 저축 900,000원, 쿠션 150,000원, 미배정 130,000원의 테스트 승인 스냅샷을 넣는다. 온보딩 계정은 계좌·카드·거래만 넣고 승인 예산은 넣지 않는다. **시드 연결과 DB 없는 계획 검증을 완료했으며 실제 적재는 아직 하지 않았다.** 온보딩 계정에는 금융 자료와 합성 조회 동의·이력만 있고 프로필·예산·고정지출 설정은 없다.',
            '','실행 방법은 backend/spending_fixture/README.md의 알파 절을 따른다. 저장소 루트에서 생성기를 실행한 다음 backend 폴더에서 `../.venv/Scripts/python.exe -m spending_fixture.seed --alpha`로 dry-run, `--alpha --apply`로 적재한다. 그 뒤 FIXTURE_LOGIN_PASSWORD 환경변수를 설정하고 `../.venv/Scripts/python.exe -m spending_fixture.set_login --alpha`로 두 계정의 비밀번호를 정한다. 재적재하면 비밀번호와 시연 변경이 초기화된다.',
            '','더미 서버는 PowerShell에서 `$env:CHAT_TODAY = "today"`를 설정한 뒤 기존 service_settings로 실행한다. 기본 3인 시드의 10/29 날짜는 바꾸지 않았다. 알파 시드는 현재 KST 날짜 이하의 합성 전표를 넣고 미래 날짜는 제외한다. 당일 전표는 생활 패턴용 가상 시각과 무관하게 포함한다. 11/5에 재실행하면 11/5 월중 정답 전체가 된다.',
            '','최신 main의 홈 승인 예산 표시와 생활비 범위 연결을 유지한다. 적금 C는 deposit으로 보존하며 main의 소비 수집 판정에서 제외된다. 청구서는 카드사 단위 scope_type=issuer, card_id=null이며, card_bill_list 성공 이력은 최초 연결일 6/1부터 적재일(최대 11/5)까지 둔다. 승인 내역 수집 이력과 별개의 합성 근거이며 실제 금융 조회가 아니다. 이 PC에서는 PostgreSQL 접속이 되지 않아 재실행하지 못했으며 SQLite 보조 API 회귀 검증을 구분해서 기록한다. local_workspace는 기본적으로 Git 제외 대상이지만, 이번 알파 기능의 필수 코드·합성 자료·정답·설계서·테스트만 명시적으로 추적해 저장소에 포함한다. 원자료·모델·개인 환경은 포함하지 않는다.',
            '','금융 연결 단계 생략은 service_settings의 지정 온보딩 UUID·이메일과 관리 계좌 A/B·카드 D/E·거래를 서버가 확인한 경우에만 적용한다. 프로필 응답의 onboardingNextStep=salary를 받아 월급 설정으로 이동한다. 일반 계정은 connect이다. 지정된 두 알파 계정(UUID·이메일 일치)에만 GET /api/finance/connect/accounts/와 GET /api/finance/connect/cards/의 저장 자료 조회를 허용한다. 인증·조회 동의·자산 및 연결 소유자 검사를 유지하고 해제된 연결은 제외한다. 계좌 목록에는 관리 대상 입출금 A/B와 받는 저축 계좌인 미선택 적금 C도 포함한다. 일반 계정은 기존 503, 미인증 조회는 403이고 일반 서비스 API는 그대로다. 금융 동의·계좌/카드 선택 변경·연결·동기화는 계속 503으로 차단한다.',
            '','연결 생략은 자동 감지를 구현한 것이 아니다. detect_candidates()는 여전히 미구현이고 온보딩 계정의 후보·확정 고정지출·저축은 비어 있다. 이 PR의 온보딩은 고정지출 6개 합계 77만원과 적금 90만원을 직접 등록하고 지킬 돈을 저장하는 경로로 테스트한다. 승인 계정에 미리 넣은 confirmed/manual 설정은 합성 시드이며 실제 감지 결과가 아니다.',
            '','숙박의 여윳돈 차감은 사용자의 결정란이 비어 있어 기존 설계의 차감 가정으로만 계산했다. `free_remaining_if_deducted`와 `travel_free_deduction_assumed`는 그 가정의 숫자다. 소비·생활비·잔액 정답은 이 가정과 독립적이다.',
            '<!-- alpha-answers:end -->']
    return '\n\n'.join(lines[:3])+'\n\n'+'\n'.join(lines[3:])+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Recompute and compare without writes')
    args=parser.parse_args()
    rows,opening=generate()
    expected=dict(seed=SEED,opening_as_of='2026-06-30',opening_balances=opening,
                  travel_free_deduction='assumption_pending_user_decision',months=answers(rows,opening))
    docpath=ROOT/'local_workspace/output/alpha_test/일반소비_자취직장인_페르소나_알파테스트_설계서.md'
    block=markdown_answers(expected)
    assert expected['months']['2026-10']['before']['living_spent']==930000
    assert expected['months']['2026-10']['before']['total_spending']==1820000
    assert expected['months']['2026-10']['before']['raw_rows']==147
    assert expected['months']['2026-10']['before']['balances']=={'A':2160000,'B':320000,'C':6300000}
    if args.check:
        with (DATA/'transactions.csv').open(encoding='utf-8-sig',newline='') as f: saved=list(csv.DictReader(f))
        assert saved==[{k:str(v) for k,v in r.items()} for r in rows]
        assert json.loads((REPORT/'answers.json').read_text(encoding='utf-8'))==expected
        assert block in docpath.read_text(encoding='utf-8')
    else:
        DATA.mkdir(parents=True,exist_ok=True);REPORT.mkdir(parents=True,exist_ok=True)
        with (DATA/'transactions.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
        (REPORT/'answers.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        text=docpath.read_text(encoding='utf-8')
        if '<!-- alpha-answers:start -->' in text:
            start=text.index('<!-- alpha-answers:start -->');end=text.index('<!-- alpha-answers:end -->',start)+len('<!-- alpha-answers:end -->')
            text=text[:start]+block.rstrip()+text[end:]
        else:text=text.rstrip()+'\n\n'+block
        docpath.write_text(text,encoding='utf-8')
    for month,states in expected['months'].items():
        print(month,states['before']['living_spent'],states['before']['total_spending'],states['after']['total_spending'])


if __name__=='__main__': main()
