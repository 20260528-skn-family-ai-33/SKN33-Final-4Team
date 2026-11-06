"""alpha.home 합성 계정 계획 (seed_alpha_home 명령용, DB 접근 없음).

local_workspace/scripts/alpha_test 의 생성기(generate.py) · 계획(seed.py)에서 home 계정에 필요한 부분만 옮겼다.
UUID · 금액 · 관계 · 날짜는 그 스크립트의 home 계정 결과와 같다 (onboarding 계정 행은 만들지 않는다).
상가 자료는 data/alpha_home_merchants.csv (공개 상가정보 발췌 · 결제와 금액은 전부 합성).
"""
import calendar
import csv
from datetime import date, datetime, time
from pathlib import Path
import random
from zoneinfo import ZoneInfo

from . import seed

KST = ZoneInfo('Asia/Seoul')
EMAIL = 'alpha.home@example.test'
PERSONA = 'home'
MERCHANTS = Path(__file__).with_name('data') / 'alpha_home_merchants.csv'
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
    with MERCHANTS.open(encoding='utf-8-sig', newline='') as f:
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



def uid(persona, *parts):
    return seed.uid('alpha-persona-v1', persona, *parts)


def plan(as_of=None):
    """현재 KST 날짜까지의 home 계정 합성 전표 → (rows, tx, opening, as_of). rows = [(모델 이름, pk, 칸)] 생성 순서.
    모든 행이 home 계정 소속이다 (User 행은 pk, 나머지는 user_id). 시각은 생활 패턴용이며 DB 접근은 없다"""
    as_of = as_of or datetime.now(KST)
    if as_of.tzinfo is None:
        raise ValueError('시간대가 있는 기준 시각이 필요합니다')
    as_of = as_of.astimezone(KST)
    if as_of.date() < date(2026,11,1):
        raise ValueError('알파 시드는 2026년 11월 1일 이후에 사용합니다')
    all_rows, opening = generate()
    tx = [r for r in all_rows if date.fromisoformat(r['occurred_on']) <= as_of.date()]
    cutoff = min(as_of.date(),date(2026,11,5))
    fetched = min(as_of,datetime(2026,11,5,23,59,59,tzinfo=KST))
    balances=dict(opening)
    for r in tx:
        if r['source_kind']=='bank': balances[r['instrument']]=r['balance_after']
    out=[]

    def add(model, pk, **fields):
        out.append((model,pk,fields))

    persona=PERSONA
    user=uid(persona,'user')
    asset=lambda name:uid(persona,'asset',name)
    conn=lambda kind:uid(persona,'connection',kind)
    issuer='FIXTURE_CARD'
    add('User',user,email=EMAIL,nickname='김하린',password='!alpha-unusable',is_staff=False)
    add('FinancialProfile',user,birth_date=date(1998,4,12),gender='female',has_car=False,
        housing_type='alone',monthly_take_home_income=3000000,payday=1,living_budget=1050000,
        savings_floor=900000,cushion_enabled=True,cushion_target=9000000,cushion_monthly=150000)
    for rank,code in enumerate(('FOOD','TRANSPORT_CAR','CULTURE_LEISURE'),1):
        add('CategoryPriority',uid(persona,'priority',rank),user_id=user,category_id=code,rank=rank)
    for kind in ('bank','bank_savings','card'):
        business='card' if kind=='card' else 'bank'
        add('Connection',conn(kind),user_id=user,business_type=business,
            organization_code=issuer if kind=='card' else 'FIXTURE_'+kind.upper(),alias='합성 금융 '+kind,status='connected')
        add('Consent',uid(persona,'consent',kind),user_id=user,connection_id=conn(kind),
            purpose='finance_inquiry',product_scope=business,document_version='alpha-fixture',
            document_hash='synthetic-consent-not-a-real-agreement',granted_at=seed.at('2026-07-01',0))
    for name in ('A','B','C'):
        add('BankAccount',asset(name),user_id=user,connection_id=conn('bank_savings' if name=='C' else 'bank'),source_account_key='alpha:'+name,
            masked_number='****'+{'A':'1001','B':'1002','C':'2001'}[name],
            name={'A':'합성 월급통장','B':'합성 생활통장','C':'합성 첫목돈적금'}[name],
            account_type='deposit' if name=='C' else 'demand',currency='KRW',balance=balances[name],
            withdrawable_amount=balances[name],is_selected=name!='C',fetched_at=fetched)
    for name,bank,kind in (('D','B','체크'),('E','A','신용')):
        add('Card',asset(name),user_id=user,connection_id=conn('card'),payment_account_id=asset(bank),
            issuer_code=issuer,name='합성 일상 '+kind,masked_number='****'+('3001' if name=='D' else '3002'),
            source_card_key='alpha:'+name,card_type=kind,origin='provider',is_selected=True,
            first_connected_at=seed.at('2026-06-01',0),fetched_at=fetched)
    cycles,budgets={},{}
    for month in range(7,12):
        start=date(2026,month,1);end=date(2026,month+1,1)
        cycle=cycles[month]=uid(persona,'cycle',month)
        budget=budgets[month]=uid(persona,'budget',month)
        add('BudgetCycle',cycle,user_id=user,starts_on=start,ends_before=end,expected_payday=end,
            confirmed_income=3000000,approved_carryover=0,status='active' if month==11 else 'closed')
        add('Budget',budget,user_id=user,cycle_id=cycle,version_no=1,status='approved',funding_amount=3000000,
            reason='알파 테스트 승인 스냅샷 (ML·엔진 검수 결과 아님)',engine_version='alpha-fixture-v1',
            approved_at=seed.at(start,9),input_snapshot={'source':'synthetic','travel_free':'assumed_pending'})
        lines=[('living',c,a) for c,a in BUDGETS.items()]
        lines += [('fixed','HOUSING_TELECOM',750000),('fixed','CULTURE_LEISURE',20000),
                  ('savings',None,900000),('cushion',None,150000)]
        for bucket,code,amount in lines:
            add('BudgetItem',uid(persona,'item',month,bucket,code),user_id=user,budget_id=budget,
                bucket=bucket,category_id=code,allocated_amount=amount)
    add('CushionEntry',uid(persona,'cushion','initial'),user_id=user,entry_type='initial',amount=550000,
        request_key='alpha:initial',occurred_at=seed.at('2026-07-01',0))
    for month in range(7,12):
        add('CushionEntry',uid(persona,'cushion',month),user_id=user,cycle_id=cycles[month],budget_id=budgets[month],
            entry_type='allocation',amount=150000,request_key=f'alpha:allocation:{month}',occurred_at=seed.at(date(2026,month,1),9))
    for role,name,amount,day,category,instrument in FIXED:
        add('RecurringExpense',uid(persona,'recurring',role),user_id=user,name=name,amount=amount,
            payment_day=day,category_id=category,status='confirmed',source_type='manual',
            payment_account_id=asset('A') if instrument=='A' else None,
            payment_card_id=asset('E') if instrument=='E' else None,confirmed_at=seed.at('2026-07-01',0))
    add('RecurringSaving',uid(persona,'saving'),user_id=user,name='합성 첫목돈적금',amount=900000,
        transfer_day=2,from_account_id=asset('A'),to_account_id=asset('C'),status='confirmed',source_type='manual',
        confirmed_at=seed.at('2026-07-01',0))
    for r in tx:
        tid=uid(persona,'tx',r['source_key'])
        meta={k:r[k] for k in ('store_id','branch_name','industry_large_code','industry_large','industry_medium_code',
              'industry_medium','industry_small_code','industry_small','sigungu','dong','legal_dong','source_snapshot','metadata_source')}
        add('FinancialTransaction',tid,user_id=user,source_kind=r['source_kind'],
            account_id=asset(r['instrument']) if r['source_kind']=='bank' else None,
            card_id=asset(r['instrument']) if r['source_kind']=='card' else None,source_key=r['source_key'],
            occurred_on=date.fromisoformat(r['occurred_on']),occurred_time=time.fromisoformat(r['occurred_time']),
            amount=r['amount'],direction=r['direction'],transaction_type=r['transaction_type'],
            merchant_name=r['merchant_name'] or None,merchant_category=r['industry_small'] or None,
            source_description=r['source_description'],source_detail=meta,
            balance_after=r['balance_after'] if r['source_kind']=='bank' else None,
            confirmed_category_id=r['confirmed_category'],canceled_amount=0,is_canceled=False,
            classification_source='rule' if r['transaction_type']=='spending' else None,
            confirmation_state='needs_review' if r['confirmed_category']=='UNCLASSIFIED' else 'suggested')
        if r['is_duplicate']:
            add('TransactionLink',uid(persona,'link',r['source_key']),user_id=user,
                from_transaction_id=uid(persona,'tx',r['pair_key']),to_transaction_id=tid,
                relation_type='SAME_PURCHASE',matched_amount=r['amount'],confirmation_state='confirmed',confirmed_at=fetched)
        elif r['transaction_type'] in ('own_transfer','saving') and r['direction']=='in':
            origin=next(x for x in tx if x['pair_key']==r['pair_key'] and x['direction']=='out')
            add('TransactionLink',uid(persona,'own-link',r['source_key']),user_id=user,
                from_transaction_id=uid(persona,'tx',origin['source_key']),to_transaction_id=tid,
                relation_type='OWN_TRANSFER',matched_amount=r['amount'],confirmation_state='confirmed',confirmed_at=fetched)
        if r['transaction_type']=='spending' and not r['is_duplicate']:
            month=date.fromisoformat(r['occurred_on']).month
            add('SpendingAllocation',uid(persona,'allocation',r['source_key']),user_id=user,transaction_id=tid,
                cycle_id=cycles[month],budget_id=budgets[month],funding_bucket=r['funding_bucket'],
                category_id=r['confirmed_category'],amount=r['amount'])
    # 계좌 고정비와 카드 청구서만 납부 의무. 구독을 카드대금과 중복 예약하지 않는다.
    for month in range(7,12):
        for role,name,amount,day,category,instrument in FIXED[:4]:
            r=next((r for r in tx if r['role']==role and r['occurred_on'][:7]==f'2026-{month:02d}'),None)
            due=uid(persona,'due',month,role)
            add('PaymentDue',due,user_id=user,obligation_type='recurring',recurring_id=uid(persona,'recurring',role),
                payment_account_id=asset('A'),occurrence_key=f"recurring:{uid(persona,'recurring',role)}:2026-{month:02d}-01",
                due_date=date(2026,month,day),confirmed_amount=amount,source_type='manual',confirmation_state='confirmed',
                paid_amount=amount if r else 0,payment_state='paid' if r else 'unpaid')
            if r:
                add('PaymentMatch',uid(persona,'match',month,role),user_id=user,due_id=due,
                    transaction_id=uid(persona,'tx',r['source_key']),matched_amount=amount,matched_at=fetched)
    for month in range(7,12):
        bill=uid(persona,'bill',month)
        add('CardBill',bill,user_id=user,connection_id=conn('card'),card_id=None,payment_account_id=asset('A'),
            issuer_code=issuer,statement_key=f'alpha:bill:{month}',scope_type='issuer',billing_month=date(2026,month,1),
            total_amount=140000,payment_due_date=date(2026,month,14),withdrawal_due_date=date(2026,month,14),
            source_type='provider',confirmation_state='confirmed',items_state='complete',fetched_at=fetched)
        for role,amount,day in [('ott',12000,8),('music',8000,12),('hotel',120000,24)]:
            original=next((r for r in all_rows if r['role']==role and r['occurred_on'][:7]==f'2026-{month-1:02d}'),None)
            add('CardBillItem',uid(persona,'bill-item',month,role),user_id=user,bill_id=bill,card_id=asset('E'),
                source_item_key=f'alpha:bill-item:{month}:{role}',item_type='one_time',
                merchant_name=(original or next(r for r in all_rows if r['role']==role))['merchant_name'],
                original_purchase_amount=amount,billed_amount=amount,
                purchased_on=date.fromisoformat(original['occurred_on']) if original else date(2026,6,day),fetched_at=fetched)
        paid=next((r for r in tx if r['role']=='card_payment' and r['occurred_on'][:7]==f'2026-{month:02d}'),None)
        due=uid(persona,'due','bill',month)
        add('PaymentDue',due,user_id=user,obligation_type='card_bill',bill_id=bill,payment_account_id=asset('A'),
            occurrence_key=f'alpha:bill:{month}',due_date=date(2026,month,14),confirmed_amount=140000,
            source_type='provider',confirmation_state='confirmed',paid_amount=140000 if paid else 0,
            payment_state='paid' if paid else 'unpaid')
        if paid:
            add('PaymentMatch',uid(persona,'match','bill',month),user_id=user,due_id=due,
                transaction_id=uid(persona,'tx',paid['source_key']),matched_amount=140000,matched_at=fetched)
    for name in ('A','B','D','E'):
        add('SyncRun',uid(persona,'sync',name),user_id=user,connection_id=conn('bank' if name in ('A','B') else 'card'),
            account_id=asset(name) if name in ('A','B') else None,card_id=asset(name) if name in ('D','E') else None,
            product_code='bank_sync' if name in ('A','B') else 'card_approval_list',status='success',
            requested_from=date(2026,7,1),requested_to=cutoff,covered_from=date(2026,7,1),covered_to=cutoff,
            started_at=fetched,completed_at=fetched)
    # 최초 연결일부터의 카드사 청구서 조회 근거. 승인내역 조회와 별개다.
    for name in ('D','E'):
        add('SyncRun',uid(persona,'sync','bills',name),user_id=user,connection_id=conn('card'),
            card_id=asset(name),product_code='card_bill_list',status='success',
            requested_from=date(2026,6,1),requested_to=cutoff,
            covered_from=date(2026,6,1),covered_to=cutoff,started_at=fetched,completed_at=fetched)
    return out,tx,opening,as_of


def model_map():
    from django.apps import apps
    return {m.__name__:m for app in ('users','finance','budgets') for m in apps.get_app_config(app).get_models()}
