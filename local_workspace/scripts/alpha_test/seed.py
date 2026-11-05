"""CSV → 기존 spending_fixture.seed 행 계약. 기본 시드와 다른 UUID·계정만 쓴다."""
from collections import Counter
import csv
from datetime import date, datetime, time
import json
from pathlib import Path
from runpy import run_path
from zoneinfo import ZoneInfo

from spending_fixture import seed

G = run_path(str(Path(__file__).with_name('generate.py')))
KST = ZoneInfo('Asia/Seoul')
ACCOUNTS = {'home':'alpha.home@example.test','onboarding':'alpha.onboarding@example.test'}


def uid(persona, *parts):
    return seed.uid('alpha-persona-v1', persona, *parts)


def load():
    rows, opening = G['generate']()
    with (G['DATA']/'transactions.csv').open(encoding='utf-8-sig',newline='') as f:
        if list(csv.DictReader(f)) != [{k:str(v) for k,v in r.items()} for r in rows]:
            raise ValueError('transactions.csv가 고정 seed 생성 결과와 다릅니다. generate.py를 다시 실행하세요.')
    return rows, opening


def plan(as_of=None):
    """현재 KST 날짜까지의 합성 전표. 시각은 생활 패턴용이며 DB 접근은 없다."""
    as_of = as_of or datetime.now(KST)
    if as_of.tzinfo is None:
        raise ValueError('시간대가 있는 기준 시각이 필요합니다')
    as_of = as_of.astimezone(KST)
    if as_of.date() < date(2026,11,1):
        raise ValueError('알파 시드는 2026년 11월 1일 이후에 사용합니다')
    all_rows, opening = load()
    tx = [r for r in all_rows if date.fromisoformat(r['occurred_on']) <= as_of.date()]
    cutoff = min(as_of.date(),date(2026,11,5))
    fetched = min(as_of,datetime(2026,11,5,23,59,59,tzinfo=KST))
    balances=dict(opening)
    for r in tx:
        if r['source_kind']=='bank': balances[r['instrument']]=r['balance_after']
    out=[]

    def add(model, pk, **fields):
        out.append((model,pk,fields))

    for persona,email in ACCOUNTS.items():
        user=uid(persona,'user')
        asset=lambda name:uid(persona,'asset',name)
        conn=lambda kind:uid(persona,'connection',kind)
        home=persona=='home'
        # 온보딩은 실제 카드사(신한카드 0306) — ① 이 카드값 적요의 카드사 이름으로 card_payment 를 판단하게 (11/4). 승인 계정은 그대로
        issuer='FIXTURE_CARD' if home else '0306'
        add('User',user,email=email,nickname='김하린',password='!alpha-unusable',is_staff=False)
        if home:
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
        if home:
            for month in range(7,12):
                start=date(2026,month,1);end=date(2026,month+1,1)
                cycle=cycles[month]=uid(persona,'cycle',month)
                budget=budgets[month]=uid(persona,'budget',month)
                add('BudgetCycle',cycle,user_id=user,starts_on=start,ends_before=end,expected_payday=end,
                    confirmed_income=3000000,approved_carryover=0,status='active' if month==11 else 'closed')
                add('Budget',budget,user_id=user,cycle_id=cycle,version_no=1,status='approved',funding_amount=3000000,
                    reason='알파 테스트 승인 스냅샷 (ML·엔진 검수 결과 아님)',engine_version='alpha-fixture-v1',
                    approved_at=seed.at(start,9),input_snapshot={'source':'synthetic','travel_free':'assumed_pending'})
                lines=[('living',c,a) for c,a in G['BUDGETS'].items()]
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
            for role,name,amount,day,category,instrument in G['FIXED']:
                add('RecurringExpense',uid(persona,'recurring',role),user_id=user,name=name,amount=amount,
                    payment_day=day,category_id=category,status='confirmed',source_type='manual',
                    payment_account_id=asset('A') if instrument=='A' else None,
                    payment_card_id=asset('E') if instrument=='E' else None,confirmed_at=seed.at('2026-07-01',0))
            add('RecurringSaving',uid(persona,'saving'),user_id=user,name='합성 첫목돈적금',amount=900000,
                transfer_day=2,from_account_id=asset('A'),to_account_id=asset('C'),status='confirmed',source_type='manual',
                confirmed_at=seed.at('2026-07-01',0))
        for r in tx:
            tid=uid(persona,'tx',r['source_key'])
            if not home and r['source_kind']=='bank':
                # 온보딩: 은행은 실제 첫 연결처럼 출금 transfer · 입금 income 으로 넣고 파이프라인이 판단 (11/4, apply_plan)
                r={**r,'transaction_type':'transfer' if r['direction']=='out' else 'income','confirmed_category':'UNCLASSIFIED'}
                if r['role']=='card_payment':
                    r['source_description']='신한카드 청구 납부'  # 카드 E 와 같은 카드사 이름 (CARD_WORDS)
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
                # 온보딩 카드 거래는 시드 카테고리로 확정 — 파이프라인 분류기가 다시 바꾸지 않게 (분류기 검증 대상 아님, 11/4)
                confirmation_state='needs_review' if r['confirmed_category']=='UNCLASSIFIED'
                    else 'confirmed' if not home and r['source_kind']=='card' else 'suggested')
            if r['is_duplicate']:
                add('TransactionLink',uid(persona,'link',r['source_key']),user_id=user,
                    from_transaction_id=uid(persona,'tx',r['pair_key']),to_transaction_id=tid,
                    relation_type='SAME_PURCHASE',matched_amount=r['amount'],confirmation_state='confirmed',confirmed_at=fetched)
            elif home and r['transaction_type'] in ('own_transfer','saving') and r['direction']=='in':  # 온보딩은 파이프라인 ①이 짝지음
                origin=next(x for x in tx if x['pair_key']==r['pair_key'] and x['direction']=='out')
                add('TransactionLink',uid(persona,'own-link',r['source_key']),user_id=user,
                    from_transaction_id=uid(persona,'tx',origin['source_key']),to_transaction_id=tid,
                    relation_type='OWN_TRANSFER',matched_amount=r['amount'],confirmation_state='confirmed',confirmed_at=fetched)
            if home and r['transaction_type']=='spending' and not r['is_duplicate']:
                month=date.fromisoformat(r['occurred_on']).month
                add('SpendingAllocation',uid(persona,'allocation',r['source_key']),user_id=user,transaction_id=tid,
                    cycle_id=cycles[month],budget_id=budgets[month],funding_bucket=r['funding_bucket'],
                    category_id=r['confirmed_category'],amount=r['amount'])
        if home:
            # 계좌 고정비와 카드 청구서만 납부 의무. 구독을 카드대금과 중복 예약하지 않는다.
            for month in range(7,12):
                for role,name,amount,day,category,instrument in G['FIXED'][:4]:
                    r=next((r for r in tx if r['role']==role and r['occurred_on'][:7]==f'2026-{month:02d}'),None)
                    due=uid(persona,'due',month,role)
                    add('PaymentDue',due,user_id=user,obligation_type='recurring',recurring_id=uid(persona,'recurring',role),
                        payment_account_id=asset('A'),occurrence_key=f"recurring:{uid(persona,'recurring',role)}:2026-{month:02d}-01",
                        due_date=date(2026,month,day),confirmed_amount=amount,source_type='manual',confirmation_state='confirmed',
                        paid_amount=amount if r else 0,payment_state='paid' if r else 'unpaid')
                    if r:
                        add('PaymentMatch',uid(persona,'match',month,role),user_id=user,due_id=due,
                            transaction_id=uid(persona,'tx',r['source_key']),matched_amount=amount,matched_at=fetched)
        # 청구서는 온보딩 계정에도 금융 조회 자료로 보관. 납부 의무는 승인 계정에만 둔다.
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
            if home:
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


def apply_plan(result):
    from django.db import transaction
    seed.guard()
    rows,_,_,as_of=result
    if as_of > datetime.now(KST):
        raise ValueError('미래 기준 시각의 계획은 적재할 수 없습니다')
    m=model_map()
    ids=[uid(p,'user') for p in ACCOUNTS]
    with transaction.atomic():
        if m['User'].objects.filter(email__in=ACCOUNTS.values()).exclude(pk__in=ids).exists():
            raise ValueError('알파 이메일을 다른 회원이 사용 중입니다')
        for p,email in ACCOUNTS.items():
            if m['User'].objects.filter(pk=uid(p,'user')).exclude(email=email).exists():
                raise ValueError('알파 UUID의 이메일이 다릅니다')
        m['User'].objects.filter(pk__in=ids).delete()
        # 계획 순서대로 생성한다. FK 순서를 모델별 묶기로 바꾸지 않는다.
        for name,pk,fields in rows:
            obj=m[name](pk=pk,**fields)
            obj.full_clean(exclude=[f.name for f in obj._meta.fields if f.is_relation],
                           validate_unique=False,validate_constraints=False)
            obj.save(force_insert=True)
        run_onboarding_pipeline(m, as_of)
    return Counter(name for name,_,_ in rows)


def run_onboarding_pipeline(m, as_of):
    """온보딩 계정은 실제 첫 연결처럼 거래 전체를 저장 뒤 처리에 한 번 넣는다 (① 유형 판단 → … → ⑦ 후보 감지, 11/4).
    '오늘'은 시드 기준 시각의 날짜로 둔다 (나중에 적재해도 최근 거래 조건이 같게)"""
    from unittest import mock
    from finance.pipeline import process_new_transactions
    user=m['User'].objects.get(pk=uid('onboarding','user'))
    ids=list(m['FinancialTransaction'].objects.filter(user=user).order_by('pk').values_list('pk',flat=True))
    with mock.patch('django.utils.timezone.localdate',return_value=as_of.date()):
        process_new_transactions(user,ids)


def main(args):
    if args.create_db:
        return seed.create_db()
    if args.reset:
        raise SystemExit('알파는 --apply로 전용 두 계정만 재생성합니다. 기본 --reset과 섞지 않습니다.')
    if args.status:
        User=model_map()['User']
        print(json.dumps(list(User.objects.filter(pk__in=[uid(p,'user') for p in ACCOUNTS]).values('email','nickname')),ensure_ascii=False))
        return 0
    result=plan()
    if args.apply:
        counts=apply_plan(result)
    else:
        counts=Counter(name for name,_,_ in result[0])
    print('apply' if args.apply else 'dry-run: DB 연결·쓰기 없음')
    print('KST 기준:',result[3].isoformat())
    print('계정:',ACCOUNTS)
    print('모델 건수:',dict(counts))
    print('기존 A/B/C 계정은 유지. 로그인 비밀번호는 별도 set_login --alpha로 설정합니다.')
    return 0
