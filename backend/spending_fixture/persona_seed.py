"""시연용 페르소나 계정 만들기 (seed_persona 명령용).

순서는 실제 첫 연결 + 온보딩과 같다.
  1. 연결 흉내: 회원 · 동의 · 금융 연결 · 계좌 · 카드 · 거래 · 수집 이력을 넣는다 (CODEF 대신).
     거래는 scripts/persona/data/transactions_<p>.csv 에서 오늘(KST)까지만.
     체크카드 결제가 통장 출금으로도 찍히는 '같은 구매' 연결은 아직 파이프라인에 없어서 여기서 넣는다.
  2. 거래 처리: finance.pipeline.process_new_transactions 를 그대로 돌린다
     (내 계좌 이체 판단 → 분류 → 저축 · 납부 맞추기 → 고정지출 · 저축 후보 감지).
     분류는 기본으로 '확인 필요' 거래만 실제 분류기에 넣는다. 나머지 가게 거래는 CSV 정답 카테고리를 규칙 결과로 둔다
     (시연 숫자가 매번 같게). --real-classify 면 전부 실제 분류기를 거친다.
  3. 온보딩: 실제 API 를 서버 안에서 순서대로 부른다
     (내 정보 → 월급 → 월급 확인 → 고정지출 · 저축 확정 → 지킬 돈 → 추천 예산 → 승인).
모든 값은 가상이다. CODEF · 금융기관은 부르지 않는다. 회원의 connectedId 가 없어서 자동 불러오기 대상도 아니다.
"""
import csv
import json
import uuid
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings

KST = ZoneInfo('Asia/Seoul')
DATA = Path(settings.BASE_DIR).parent / 'scripts' / 'persona' / 'data'
NAMESPACE = uuid.UUID('6f8f4c1e-2d0b-4b1e-9c33-6d656d6f2d61')  # 페르소나 UUID5 기준 (고정)

PERSONAS = {
    'a': {
        'email': 'jiwoo@cushion.com',  # 11/18: 시연 화면에서 자연스럽게 (전 demo.jiwoo@moneycushion.test)
        'nickname': '김지우',
        'csv': 'transactions_a.csv',
        'first_connected': date(2026, 7, 1),  # 수집 시작일 ('4달 전 1일')
        'bank': {'org': '0088', 'alias': '신한은행'},
        'card': {'org': '0306', 'alias': '신한카드'},
        # CSV instrument → (이름, 종류, 끝 4자리, 관리 대상)
        'accounts': {'A': ('신한 주거래 우대통장', 'demand', '4821', True),
                     'C': ('신한 청년도약 적금', 'deposit', '7730', False)},
        # CSV instrument → (이름, 신용/체크, 끝 4자리, 결제 계좌)
        'cards': {'D': ('신한카드 Deep Dream 체크', '체크', '9154', 'A')},
        'profile': {
            'birthDate': '2000-05-14', 'gender': 'female', 'housingType': 'alone', 'hasCar': False,
            'regionCode': '11620',  # 서울 관악구
            'priorities': [{'category': 'FOOD', 'rank': 1}, {'category': 'CULTURE_LEISURE', 'rank': 2},
                           {'category': 'FASHION_SHOPPING', 'rank': 3}],
            'categoryMinimums': [],
        },
        'salary': {'takeHomeIncome': 2_600_000, 'payday': 25},
        'bills_checked_to_payday': True,  # 11/17: 지난 카드값 미반영 안내를 띄우지 않음 (아래 insert_connected_state 설명)
        # 감지가 안 되면 직접 등록할 고정지출: (CSV role, 이름, 금액, 날짜, 카테고리, 결제 수단)
        'fixed': [('rent', '월세', 500_000, 1, 'HOUSING_TELECOM', 'A'),
                  ('maintenance', '관리비', 70_000, 10, 'HOUSING_TELECOM', 'A'),
                  ('telecom', '휴대폰 요금', 55_000, 15, 'HOUSING_TELECOM', 'A'),
                  ('ott', '넷플릭스', 17_000, 5, 'CULTURE_LEISURE', 'D')],
        # 11/18: 학자금 대출 없음 (대출이 있는 페르소나는 'loans' 칸에 직접 등록할 대출을 넣는다, loans.md 11)
    },
    'b': {
        'email': 'demo.minjun@moneycushion.test',
        'nickname': '박민준',
        'csv': 'transactions_b.csv',
        'first_connected': date(2026, 7, 1),
        'bank': {'org': '0004', 'alias': '국민은행'},
        'card': {'org': '0301', 'alias': '국민카드'},
        'accounts': {'A': ('KB 직장인우대통장', 'demand', '3307', True),
                     'C': ('KB 내맘대로적금', 'deposit', '5512', False),
                     'H': ('KB 주택청약종합저축', 'deposit', '0948', False)},
        'cards': {'D': ('KB국민 노리2 체크카드', '체크', '6620', 'A')},
        'profile': {
            'birthDate': '1999-08-21', 'gender': 'male', 'housingType': 'family', 'hasCar': True,
            'regionCode': '41465',  # 경기도 용인시 수지구
            'priorities': [{'category': 'TRANSPORT_CAR', 'rank': 1}, {'category': 'FOOD', 'rank': 2},
                           {'category': 'CULTURE_LEISURE', 'rank': 3}],
            'categoryMinimums': [],
        },
        'salary': {'takeHomeIncome': 2_900_000, 'payday': 10},
        'fixed': [('parking_pass', '회사 월 주차권', 150_000, 1, 'TRANSPORT_CAR', 'D'),
                  ('youtube', '유튜브 프리미엄', 14_900, 3, 'CULTURE_LEISURE', 'D'),
                  ('parents', '부모님 생활비', 300_000, 11, 'ETC', 'A'),
                  ('gym', '헬스장', 59_000, 15, 'CULTURE_LEISURE', 'D'),
                  ('telecom', '휴대폰 요금', 69_000, 20, 'HOUSING_TELECOM', 'A')],
        'loans': [],
    },
    'c': {
        'email': 'demo.seoyeon@moneycushion.test',
        'nickname': '이서연',
        'csv': 'transactions_c.csv',
        # 10/12 첫 연결 → 수집 시작일은 '4달 전 1일'(6/1). 통장 · 카드는 9/14 입사 때 새로 만들어서 거래는 9/14부터
        'connected_on': date(2026, 10, 12),
        'first_connected': date(2026, 6, 1),
        'bank': {'org': '0081', 'alias': '하나은행'},
        'card': {'org': '0313', 'alias': '하나카드'},
        'accounts': {'A': ('하나 급여하나 통장', 'demand', '2716', True),
                     'C': ('하나 청년도약계좌', 'deposit', '8843', False)},
        'cards': {'D': ('하나 트래블로그 체크카드', '체크', '5307', 'A')},
        'profile': {
            'birthDate': '1998-03-09', 'gender': 'female', 'housingType': 'family', 'hasCar': False,
            'regionCode': '28237',  # 인천 부평구
            'priorities': [{'category': 'MEDICAL_HEALTH', 'rank': 1}, {'category': 'FOOD', 'rank': 2},
                           {'category': 'FASHION_SHOPPING', 'rank': 3}],
            'categoryMinimums': [],
        },
        'salary': {'takeHomeIncome': 2_500_000, 'payday': 25},
        'bills_checked_to_payday': True,  # 11/17: A와 같음
        'fixed': [('telecom', '휴대폰 요금', 52_000, 22, 'HOUSING_TELECOM', 'A'),
                  ('ott', '티빙', 13_900, 5, 'CULTURE_LEISURE', 'D'),
                  ('parents', '부모님 생활비', 200_000, 26, 'ETC', 'A')],
        'loans': [],
    },
}


def uid(persona, *parts):
    return uuid.uuid5(NAMESPACE, ':'.join(['persona', persona, *map(str, parts)]))


def next_payday(day, payday):
    """day 다음에 오는 명목 월급날 (주말 · 휴일로 당겨지는 실제 날짜보다 늦거나 같다 → 주기 끝을 넉넉히 덮는다)"""
    if day.day < payday:
        return day.replace(day=payday)
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, payday)


def at(day, hour=0, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=KST)


def load_rows(persona, as_of):
    cfg = PERSONAS[persona]
    with (DATA / cfg['csv']).open(encoding='utf-8-sig', newline='') as f:
        rows = [r for r in csv.DictReader(f) if date.fromisoformat(r['occurred_on']) <= as_of]
    return rows


# ---------------------------------------------------------------- 1. 연결 흉내
def insert_connected_state(persona, as_of, password, *, real_classify=False):
    from django.contrib.auth import get_user_model
    from finance.models import BankAccount, Card, Connection, FinancialTransaction, SyncRun, TransactionLink
    from users.consent_documents import CURRENT, canonical_hash
    from users.models import Consent

    cfg = PERSONAS[persona]
    User = get_user_model()
    now = datetime.now(KST)
    first = at(cfg.get('connected_on', cfg['first_connected']), 9)  # 가입 · 동의 · 연결 시각
    user = User(pk=uid(persona, 'user'), email=cfg['email'], nickname=cfg['nickname'], is_staff=False,
                email_verified_at=first)
    user.set_password(password)
    user.save(force_insert=True)
    User.objects.filter(pk=user.pk).update(created_at=first)

    for purpose in ('terms', 'privacy'):
        doc = CURRENT[(purpose, None)]
        Consent.objects.create(user=user, connection=None, purpose=purpose, product_scope=None,
                               document_version=doc['version'], document_hash=canonical_hash(doc), granted_at=first)
    conns = {}
    for kind in ('bank', 'card'):
        conns[kind] = Connection.objects.create(
            pk=uid(persona, 'connection', kind), user=user, business_type=kind, organization_code=cfg[kind]['org'],
            alias=cfg[kind]['alias'], status='connected', last_authenticated_at=first)
        doc = CURRENT[('finance_inquiry', kind)]
        Consent.objects.create(user=user, connection=conns[kind], purpose='finance_inquiry', product_scope=kind,
                               document_version=doc['version'], document_hash=canonical_hash(doc), granted_at=first)

    rows = load_rows(persona, as_of)
    balances = {}
    for r in rows:
        if r['source_kind'] == 'bank':
            balances[r['instrument']] = int(r['balance_after'])
    assets = {}
    for key, (name, kind, last4, selected) in cfg['accounts'].items():
        assets[key] = BankAccount.objects.create(
            pk=uid(persona, 'asset', key), user=user, connection=conns['bank'], source_account_key=f'persona-{persona}:{key}',
            masked_number='****' + last4, name=name, account_type=kind, currency='KRW',
            balance=balances.get(key, 0), withdrawable_amount=balances.get(key, 0), is_selected=selected, fetched_at=now)
    for key, (name, card_type, last4, pay) in cfg['cards'].items():
        assets[key] = Card.objects.create(
            pk=uid(persona, 'asset', key), user=user, connection=conns['card'], payment_account=assets[pay],
            issuer_code=cfg['card']['org'], name=name, masked_number='****' + last4,
            source_card_key=f'persona-{persona}:{key}', card_type=card_type, origin='provider', is_selected=True,
            first_connected_at=first, fetched_at=now)

    txs = {}
    for r in rows:
        bank = r['source_kind'] == 'bank'
        spending_card = not bank and r['transaction_type'] == 'spending'
        review = r['confirmed_category'] == 'UNCLASSIFIED'
        description = r['source_description']
        if r['role'] == 'saving':
            description = cfg['nickname']  # 내 계좌끼리는 양쪽 적요에 가입 이름이 찍힌다 (R11 자동 판단 기준)
        if bank:
            # 은행은 실제 첫 연결처럼 출금 transfer · 입금 income 으로 넣고 파이프라인이 판단한다
            tx_type = 'transfer' if r['direction'] == 'out' else 'income'
            category, state, source = 'UNCLASSIFIED', 'needs_review', None
        else:
            tx_type = r['transaction_type']
            category = r['confirmed_category'] or 'UNCLASSIFIED'
            state = 'needs_review' if review else 'suggested'
            source = 'rule'
        if real_classify and spending_card:
            category, state = 'UNCLASSIFIED', 'needs_review'
        txs[r['source_key']] = FinancialTransaction.objects.create(
            pk=uid(persona, 'tx', r['source_key']), user=user, source_kind=r['source_kind'],
            account=assets[r['instrument']] if bank else None, card=None if bank else assets[r['instrument']],
            source_key=r['source_key'], occurred_on=date.fromisoformat(r['occurred_on']),
            occurred_time=time.fromisoformat(r['occurred_time']), amount=int(r['amount']), direction=r['direction'],
            transaction_type=tx_type, merchant_name=(r['merchant_name'] or None) if not bank or tx_type == 'spending' else None,
            merchant_category=r['industry_small'] or None, source_description=description,
            source_detail={'synthetic': True, 'store_id': r['store_id'], 'sigungu': r['sigungu'], 'dong': r['dong'],
                           'metadata_source': r['metadata_source']},
            balance_after=int(r['balance_after']) if bank else None, installment_months=0,
            confirmed_category_id=category, classification_source=source, confirmation_state=state,
            canceled_amount=0, is_canceled=False)
    for r in rows:
        if r['is_duplicate'] == '1' and r['pair_key'] in txs:
            TransactionLink.objects.create(
                pk=uid(persona, 'link', r['source_key']), user=user, from_transaction=txs[r['pair_key']],
                to_transaction=txs[r['source_key']], relation_type='SAME_PURCHASE', matched_amount=int(r['amount']),
                confirmation_state='confirmed', confirmed_at=now)

    start = cfg['first_connected']  # 수집 시작일 (첫 연결의 '4달 전 1일')
    # 11/17 (A · C만): 체크카드라 청구서가 없다는 것을 다음 월급날까지 확인한 것으로 둔다.
    # 실제 조회는 오늘까지만 기록돼서 주기 중간에는 '지난 카드값이 아직 반영 안 됐어요'가 뜬다 (budgets/protect.py first_bill_unavailable).
    # 이번 주기 끝까지 청구서 0건으로 두면 no_bill_in_cycle(선차감 0원 · 지킬 돈 화면 작은 안내만)이 된다. 계산 규칙은 그대로
    bills_to = next_payday(as_of, cfg['salary']['payday']) if cfg.get('bills_checked_to_payday') else as_of
    for key, asset in assets.items():
        is_card = key in cfg['cards']
        if not is_card and not cfg['accounts'][key][3]:
            continue  # 관리 대상이 아닌 적금은 수집 범위 판정에서 빠진다
        SyncRun.objects.create(
            pk=uid(persona, 'sync', key), user=user, connection=conns['card' if is_card else 'bank'],
            account=None if is_card else asset, card=asset if is_card else None,
            product_code='card_approval_list' if is_card else 'bank_sync', status='success',
            requested_from=start, requested_to=as_of, covered_from=start, covered_to=as_of,
            started_at=now, completed_at=now)
        if is_card:
            # 카드 청구서 조회 기록: 지킬 돈의 첫 청구서 선차감이 이 기록으로 '다 불러옴'을 판단한다 (budgets/protect.py).
            # 체크카드라 청구서는 없다 → 조회는 성공, 청구서 0건 (없으면 card_bill_pending 으로 막힘)
            SyncRun.objects.create(
                pk=uid(persona, 'sync', 'bills', key), user=user, connection=conns['card'], card=asset,
                product_code='card_bill_list', status='success',
                requested_from=start, requested_to=bills_to, covered_from=start, covered_to=bills_to,
                started_at=now, completed_at=now)
    return user, list(txs.values())


# ---------------------------------------------------------------- 2. 거래 처리
def run_pipeline(user, txs, *, real_classify=False):
    from finance import classifier
    from finance.pipeline import process_new_transactions

    original = classifier.classify

    def only_review(u, qs):
        # 기본: '확인 필요' 거래만 실제 분류기에 (가게 거래는 CSV 정답을 규칙 결과로 유지)
        return original(u, qs.filter(confirmation_state='needs_review'))

    target = original if real_classify else only_review
    with mock.patch.object(classifier, 'classify', target):
        process_new_transactions(user, [tx.pk for tx in txs])


# ---------------------------------------------------------------- 3. 온보딩 (실제 API)
class Api:
    def __init__(self, user, log):
        from django.test import Client

        host = next((h.lstrip('.') for h in settings.ALLOWED_HOSTS if h not in ('*', '')), 'localhost')
        self.client = Client(HTTP_HOST=host)
        self.client.force_login(user)
        self.log = log
        self.secure = not settings.DEBUG

    def call(self, method, path, body=None, *, key=None, ok=(200, 201, 202)):
        headers = {'HTTP_IDEMPOTENCY_KEY': key} if key else {}
        fn = getattr(self.client, method.lower())
        kwargs = {'secure': self.secure, **headers}
        if method != 'GET':
            kwargs.update(data=json.dumps(body if body is not None else {}), content_type='application/json')
        res = fn('/api/' + path, **kwargs)
        data = res.json() if res.content and res.headers.get('Content-Type', '').startswith('application/json') else None
        if res.status_code not in ok:
            raise RuntimeError(f'{method} /api/{path} → {res.status_code} {json.dumps(data, ensure_ascii=False)[:600]}')
        return data


def register_loans(persona, user, log):
    """거래 처리 전에 대출을 등록해 둔다 → 파이프라인의 대출 상환 맞추기가 상환 출금을 대출 상환으로 바꾸고,
    고정지출 후보로 잡지 않는다"""
    api = Api(user, log)
    for i, loan in enumerate(PERSONAS[persona].get('loans', [])):
        api.call('POST', 'finance/loans/', loan, key=f'persona-{persona}-loan-{i}')
        log(f"대출 직접 등록: {loan['lenderName']} {loan['productName']} · 매달 {loan['paymentDay']}일 "
            f"{loan['monthlyPayment']:,}원 · 남은 금액 {loan['manualBalance']:,}원")


def is_loan_payment(persona, item):
    """대출 상환 출금이 고정지출 후보로 잡혔는지 (안전장치: 확정하지 않고 무시한다)"""
    text = json.dumps(item, ensure_ascii=False)
    return any(loan['lenderName'] in text for loan in PERSONAS[persona].get('loans', []))


def onboard(persona, user, log):
    cfg = PERSONAS[persona]
    api = Api(user, log)
    api.call('PUT', 'profile/', cfg['profile'])
    log('내 정보 저장')
    api.call('PUT', 'budgets/salary/', cfg['salary'])
    log(f"월급 저장: {cfg['salary']['takeHomeIncome']:,}원 · 매달 {cfg['salary']['payday']}일")

    fixed = api.call('GET', 'budgets/fixed/')['items']
    loan_like = [f for f in fixed if f['status'] == 'suggested' and is_loan_payment(persona, f)]
    if loan_like:
        api.call('POST', 'budgets/fixed/ignore/', {'recurringIds': [f['recurringId'] for f in loan_like]},
                 key=f'persona-{persona}-fixed-ignore-loan')
        log('대출 상환이 고정지출 후보로 잡혀서 무시: ' + ', '.join(f['name'] for f in loan_like))
    suggested = [f for f in fixed if f['status'] == 'suggested' and f not in loan_like]
    if suggested:
        api.call('POST', 'budgets/fixed/confirm/', {'recurringIds': [f['recurringId'] for f in suggested]},
                 key=f'persona-{persona}-fixed-confirm')
    log('고정지출 후보 확정: ' + (', '.join(f"{f['name']} {f['amount']:,}" for f in suggested) or '없음'))
    fixed = [f for f in api.call('GET', 'budgets/fixed/')['items'] if f['status'] == 'confirmed']
    for role, name, amount, day, category, instrument in cfg['fixed']:
        same = [f for f in fixed if abs(f['amount'] - amount) <= amount * 0.1 and
                (f.get('paymentDay') in (None, day) or abs((f.get('paymentDay') or day) - day) <= 3)]
        if same:
            f = same[0]
            if f['category'] != category or f['name'] != name:
                api.call('PATCH', f"budgets/fixed/{f['recurringId']}/", {'name': name, 'category': category})
            continue
        pay = {'paymentAccountId': str(uid(persona, 'asset', instrument)), 'paymentCardId': None}
        if instrument in cfg['cards']:
            pay = {'paymentAccountId': None, 'paymentCardId': str(uid(persona, 'asset', instrument))}
        api.call('POST', 'budgets/fixed/', {'name': name, 'amount': amount, 'paymentDay': day, 'category': category,
                                            'evidenceTransactionId': None, **pay},
                 key=f'persona-{persona}-fixed-{role}')
        log(f'고정지출 직접 등록: {name} {amount:,}원 (감지 안 됨)')

    savings = api.call('GET', 'budgets/savings/')['items']
    picks = [s for s in savings if s['status'] == 'suggested']
    if picks:
        api.call('POST', 'budgets/savings/confirm/', {'savingIds': [s['savingId'] for s in picks]},
                 key=f'persona-{persona}-savings-confirm')
    log('정기 저축 확정: ' + (', '.join(f"{s['name']} {s['amount']:,}" for s in picks) or '없음 (감지 안 됨)'))

    protect = api.call('GET', 'budgets/protect/')
    if protect.get('missing') or protect.get('blocked'):
        raise RuntimeError(f"지킬 돈 계산 불가: missing={protect.get('missing')} blocked={protect.get('blocked')}")
    rec = protect['recommended']
    body = {k: rec[k] for k in ('livingBudget', 'savingFloor', 'cushionMonthly')}
    api.call('PUT', 'budgets/protect/', body)
    log(f"지킬 돈 저장: 생활비 {rec['livingBudget']:,} · 저축 {rec['savingFloor']:,} · "
        f"쿠션 {rec['cushionMonthly']:,} · 여윳돈 {rec['spare']:,} (나눌 돈 {protect['base']['available']:,})")

    started = api.call('POST', 'budgets/recommend/', {}, key=f'persona-{persona}-recommend')
    task = api.call('GET', f"tasks/{started['task']['taskId']}/")
    result = task.get('result') or {}
    if task.get('status') != 'succeeded' or not result.get('budgetId'):
        raise RuntimeError(f'추천 예산 실패: {json.dumps(task, ensure_ascii=False)[:800]}')
    api.call('POST', f"budgets/{result['budgetId']}/approve/", {}, key=f'persona-{persona}-approve')
    budget = api.call('GET', 'budgets/')  # 승인 뒤 예산 (쓴 돈 포함)
    log(f"추천 예산 승인: {result.get('summary') or ''}")
    # 온보딩 뒤: 홈의 '월급 확인' 팝업에서 이번 회차 월급을 확인한다 (온보딩 단계에는 없음)
    deposits = api.call('GET', 'budgets/salary/deposits/')
    for window in deposits['windows']:
        picks = [i for i in window['items'] if i['amount'] == cfg['salary']['takeHomeIncome'] and i['isCandidate']]
        if picks:
            api.call('POST', 'budgets/salary/decisions/',
                     {'transactionIds': [picks[0]['transactionId']], 'decision': 'confirmed',
                      'usePattern': picks[0]['patternUnusableReason'] is None},
                     key=f'persona-{persona}-salary-{window["payCycleOn"]}')
            log(f"(홈) 월급 확인: {window['payCycleOn']} 회차 {picks[0]['date']} 입금")
    return budget


def summarize(user, budget, log):
    from finance.models import FinancialTransaction

    review = FinancialTransaction.objects.filter(user=user, confirmation_state='needs_review', transaction_type='spending')
    log(f'확인 필요 거래: {review.count()}건 — ' + ', '.join(sorted({t.merchant_name or t.source_description for t in review})))
    meta = (budget or {}).get('design') or (budget or {}).get('recommendation') or {}
    log('예산 응답 요약: ' + json.dumps({k: budget.get(k) for k in ('status', 'split', 'recommendation') if budget and k in budget},
                                    ensure_ascii=False)[:600])
    for c in (budget or {}).get('categories', []):
        log(f"  {c.get('categoryName') or c.get('category')}: 예산 {c.get('allocated')} · 쓴 돈 {c.get('spent')}")
    # 서버가 저장한 예산 설계 기록 (A안/B안 선택 · 이유 · 카테고리 설명). 시드는 금액을 계산하지 않는다
    from budgets.models import Budget
    approved = Budget.objects.filter(user=user, status='approved').order_by('-approved_at').first()
    design = ((approved.validation_summary or {}).get('design') or {}) if approved else {}
    sel = design.get('selection') or {}
    if sel:
        log(f"예산 설계: {sel.get('plan', '?').upper()}안 · 이유 {sel.get('reason')} · "
            f"3개월 최다 카테고리 건수 {sel.get('max_category_count')} · 기준 달 {sel.get('months')}")
    else:
        log('예산 설계 기록 없음 (BUDGET_DESIGN_ENABLED 가 꺼져 있으면 예전 임시 엔진으로 만든 예산이에요)')
    if sel.get('plan') == 'a':
        # A안이면 왜 B안이 안 됐는지: 달마다 수집 범위 · 기록 검증 상태와 막은 이유
        from django.utils import timezone
        from budgets import design_service
        from plan_b_history import summarize_history
        today = timezone.localdate()
        months = design_service.history_for_user(user, today)
        for month, row in months.items():
            log(f"  {month}: 수집 {row['coverage_state']} · 검증 {row['normalization_checks']}")
        log(f"  막은 이유: {summarize_history(months, as_of=today).get('missing_data')}")
        try:
            from finance.history_verifier import transaction_history_report
            for month, report in transaction_history_report(user, list(months), use_confirmed_salary=True).items():
                for kind, item in report.items():
                    if item['status'] != 'verified':
                        log(f"  {month} {kind}: {item['status']} · {[r['code'] for r in item['reasons']]}")
        except Exception as exc:  # 진단 출력만 (계정 생성에는 영향 없음)
            log(f'  검증 상세를 읽지 못함: {type(exc).__name__}')
    for e in design.get('explanations', []):
        log(f"  설명 {e['category']}: {e['text']}")
    return meta


def delete_persona(persona):
    from django.contrib.auth import get_user_model

    # 이메일을 바꾼 뒤에도 예전 이메일 계정이 지워지게 고정 ID(UUID5)로도 찾는다
    return persona_users(persona).delete()[0]


def persona_users(persona):
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    return get_user_model().objects.filter(Q(email=PERSONAS[persona]['email']) | Q(pk=uid(persona, 'user')))
