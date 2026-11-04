"""Run with manage.py test ../local_workspace/tests -p test_alpha_fixture.py --settings=spending_fixture.settings."""
from datetime import date, datetime, timedelta
import importlib.util
from pathlib import Path
from runpy import run_path
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.test import TestCase, SimpleTestCase, Client, override_settings
from budgets.overview import budget_data
from budgets.scope import living_spent
from finance.api import listed_transactions, transaction_item, _apply_category
from finance.spending import spending_summary
from finance.models import BankAccount, Card, Connection, FinancialTransaction, CardBill, PaymentDue, SyncRun, RecurringExpense, RecurringSaving
from users.models import Consent, FinancialProfile, User
from budgets.models import Budget
from spending_fixture import seed

ROOT=Path(__file__).resolve().parents[2]
S=run_path(str(ROOT/'local_workspace/scripts/alpha_test/seed.py'))
KST=ZoneInfo('Asia/Seoul')
AS_OF=datetime(2026,11,5,23,59,59,tzinfo=KST)


class FixtureClock(datetime):
    @classmethod
    def now(cls,tz=None): return AS_OF.astimezone(tz) if tz else AS_OF.replace(tzinfo=None)


def apply_snapshot():
    with mock.patch.dict(S['apply_plan'].__globals__,datetime=FixtureClock):
        return S['apply_plan'](S['plan'](AS_OF))


class AlphaPlanTest(SimpleTestCase):
    def test_model_fields_fks_ownership_and_budget_totals_without_db(self):
        rows,_,_,_=S['plan'](AS_OF)
        models=S['model_map']()
        objects={}
        for name,pk,fields in rows:
            obj=models[name](pk=pk,**fields)
            obj.full_clean(exclude=[f.name for f in obj._meta.fields if f.is_relation],
                           validate_unique=False,validate_constraints=False)
            self.assertNotIn((name,pk),objects)
            objects[(name,pk)]=obj
        categories={c for c,*_ in __import__('importlib').import_module('budgets.migrations.0003_seed_categories').CATEGORIES}
        for (name,pk),obj in objects.items():
            for field in obj._meta.fields:
                if not field.is_relation: continue
                value=getattr(obj,field.attname)
                if value is None: continue
                target=field.related_model.__name__
                if target=='Category':
                    self.assertIn(value,categories)
                    continue
                related=objects[(target,value)]
                owner=getattr(obj,'user_id',pk if name=='User' else None)
                other=getattr(related,'user_id',related.pk if target=='User' else None)
                if owner is not None and other is not None: self.assertEqual(owner,other)
        for (name,pk),obj in objects.items():
            if name=='Budget':
                amount=sum(item.allocated_amount for (kind,_),item in objects.items()
                           if kind=='BudgetItem' and item.budget_id==pk)
                self.assertEqual(obj.funding_amount-amount,130000)

    def test_cuts_future_transactions_and_keeps_existing_ids_separate(self):
        rows,tx,_,_=S['plan'](datetime(2026,11,3,17,tzinfo=KST))
        self.assertTrue(all(r['occurred_on']<='2026-11-03' for r in tx))
        self.assertEqual([r['ans_category_if_user_set'] for r in tx if r['ans_category_if_user_set']],['FOOD'])
        self.assertTrue(set(seed.user_ids(seed.load('design.json')).values()).isdisjoint(
            pk for model,pk,_ in rows if model=='User'))

    def test_apply_rejects_future_plan(self):
        result=S['plan'](datetime.now(KST)+timedelta(days=1))
        with mock.patch('spending_fixture.seed.guard'), self.assertRaisesRegex(ValueError,'미래'):
            S['apply_plan'](result)

    def test_attachment_scope_matches_service_oracle(self):
        rows,opening=S['G']['generate']()
        normalized=[dict(transaction_type=r['transaction_type'],category=r['confirmed_category'],
                         funding='fixed' if r['is_fixed'] else 'living',net_amount=r['amount'])
                    for r in rows if r['occurred_on'].startswith('2026-10') and not r['is_duplicate']]
        self.assertEqual(living_spent(spending_summary(normalized,living_only=True)),930000)

    def test_service_date_can_use_today_without_changing_default(self):
        path=ROOT/'backend/spending_fixture/service_settings.py'
        spec=importlib.util.spec_from_file_location('spending_fixture.alpha_settings_check',path)
        for value in ('','today'):
            module=importlib.util.module_from_spec(spec)
            with mock.patch.dict('os.environ',CHAT_TODAY=value):
                spec.loader.exec_module(module)
            self.assertIsNone(module.CHAT_TODAY)

    def test_bill_plan_covers_first_connection_and_issuer_scope(self):
        rows,_,_,_=S['plan'](AS_OF)
        cards={pk:fields for name,pk,fields in rows if name=='Card'}
        runs=[fields for name,_,fields in rows if name=='SyncRun' and fields['product_code']=='card_bill_list']
        self.assertEqual(len(runs),4)
        for pk,card in cards.items():
            run=next(r for r in runs if r['card_id']==pk)
            self.assertEqual(run['covered_from'],card['first_connected_at'].date())
            self.assertEqual(run['covered_to'],AS_OF.date())
        for name,_,fields in rows:
            if name=='CardBill':
                self.assertEqual(fields['scope_type'],'issuer')
                self.assertIsNone(fields['card_id'])


@unittest.skipUnless(settings.SETTINGS_MODULE=='spending_fixture.settings','fixture DB only')
class AlphaDatabaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.other=User.objects.create_user('preserve-me@example.test')
        apply_snapshot()

    def home(self): return User.objects.get(pk=S['uid']('home','user'))

    def summary(self,month,living=False):
        rows=listed_transactions(self.home()).filter(occurred_on__year=2026,occurred_on__month=month)
        return spending_summary((transaction_item(r) for r in rows),living_only=living)

    def test_months_match_independent_csv_oracles(self):
        rows,opening=S['G']['generate']()
        for key,states in S['G']['answers'](rows,opening).items():
            month=int(key[-2:]);expected=states['before']
            self.assertEqual(self.summary(month)['spent_total'],expected['total_spending'])
            self.assertEqual(living_spent(self.summary(month,True)),expected['living_spent'])
        self.assertEqual(FinancialTransaction.objects.filter(user=self.home(),occurred_on__month=10).count(),147)
        self.assertEqual(dict(BankAccount.objects.filter(user=self.home()).values_list('masked_number','balance')),
                         {'****1001':2610000,'****1002':1084000,'****2001':7200000})

    def test_approved_november_budget_and_onboarding(self):
        with transaction.atomic(): data=budget_data(self.home(),date(2026,11,5))
        self.assertEqual(data['budget']['status'],'approved')
        self.assertEqual(data['living'],dict(budget=1050000,spent=246000,remaining=804000))
        self.assertEqual(data['split']['spare'],130000)
        onboarding=User.objects.get(pk=S['uid']('onboarding','user'))
        self.assertFalse(Budget.objects.filter(user=onboarding).exists())
        self.assertFalse(FinancialProfile.objects.filter(user=onboarding).exists())
        self.assertTrue(FinancialTransaction.objects.filter(user=onboarding).exists())
        self.assertEqual(CardBill.objects.filter(user=self.home(),billing_month=date(2026,11,1)).get().total_amount,140000)
        self.assertEqual(PaymentDue.objects.filter(user=self.home(),bill__billing_month=date(2026,11,1)).get().payment_state,'unpaid')

    def test_demo_api_category_change_and_revert(self):
        cash=list(BankAccount.objects.filter(user=self.home()).values_list('pk','balance'))
        rows,_=S['G']['generate']()
        for row in rows:
            if row['ans_category_if_user_set']:
                tx=FinancialTransaction.objects.get(pk=S['uid']('home','tx',row['source_key']))
                tx.save(update_fields=_apply_category(tx,row['ans_category_if_user_set']))
        self.assertEqual(self.summary(11)['spent_total'],886000)
        self.assertEqual(living_spent(self.summary(11,True)),286000)
        self.assertEqual(list(BankAccount.objects.filter(user=self.home()).values_list('pk','balance')),cash)
        for row in rows:
            if row['ans_category_if_user_set']:
                tx=FinancialTransaction.objects.get(pk=S['uid']('home','tx',row['source_key']))
                tx.save(update_fields=_apply_category(tx,None))
        self.assertEqual(self.summary(11)['spent_total'],846000)

    def test_reapply_preserves_unrelated_users(self):
        before=set(FinancialTransaction.objects.filter(user=self.home()).values_list('pk',flat=True))
        apply_snapshot()
        self.assertEqual(before,set(FinancialTransaction.objects.filter(user=self.home()).values_list('pk',flat=True)))
        self.assertTrue(User.objects.filter(pk=self.other.pk).exists())

    def test_foreign_email_and_service_db_are_rejected(self):
        with mock.patch('spending_fixture.seed.guard',side_effect=SystemExit('wrong DB')):
            with self.assertRaises(SystemExit): S['apply_plan'](S['plan'](AS_OF))
        User.objects.filter(pk=S['uid']('home','user')).delete()
        User.objects.create_user(S['ACCOUNTS']['home'])
        with self.assertRaisesRegex(ValueError,'다른 회원'):
            apply_snapshot()

    def onboarding_client(self, user=None):
        client=Client(enforce_csrf_checks=True)
        client.force_login(user or User.objects.get(pk=S['uid']('onboarding','user')))
        client.get('/api/csrf/')
        client.defaults['HTTP_X_CSRFTOKEN']=client.cookies[settings.CSRF_COOKIE_NAME].value
        return client

    def profile_body(self):
        return dict(birthDate='1998-04-12',gender='female',housingType='alone',hasCar=False,
                    priorities=[dict(category=c,rank=i+1) for i,c in enumerate(('FOOD','TRANSPORT_CAR','CULTURE_LEISURE'))],
                    categoryMinimums=[])

    @override_settings(ROOT_URLCONF='spending_fixture.service_urls')
    def test_onboarding_profile_salary_and_protect_without_bill_hold(self):
        client=self.onboarding_client()
        with mock.patch('finance.codef._request',side_effect=AssertionError('외부 연결 금지')), \
                mock.patch('django.utils.timezone.localdate',return_value=AS_OF.date()):
            response=client.put('/api/profile/',self.profile_body(),content_type='application/json')
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(response.json()['onboardingNextStep'],'salary')
            response=client.put('/api/budgets/salary/',dict(takeHomeIncome=3000000,payday=1),content_type='application/json')
            self.assertEqual(response.status_code,200,response.content)
            response=client.get('/api/budgets/protect/')
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(response.json()['missing'],[])
            self.assertIsNone(response.json()['blocked'])
            self.assertEqual(response.json()['base']['fixedTotal'],0)
            user=User.objects.get(pk=S['uid']('onboarding','user'))
            self.assertFalse(RecurringExpense.objects.filter(user=user).exists())
            self.assertFalse(RecurringSaving.objects.filter(user=user).exists())
            response=client.get('/api/finance/connect/accounts/?v=0')
            self.assertEqual(response.status_code,200,response.content)
            accounts=response.json()['items']
            salary=next(a for a in accounts if a['name']=='합성 월급통장' and a['isSelected'])
            deposit=next(a for a in accounts if a['name']=='합성 첫목돈적금')
            self.assertFalse(deposit['isSelected'])
            self.assertEqual(deposit['status'],'connected')
            response=client.get('/api/finance/connect/cards/?v=0')
            self.assertEqual(response.status_code,200,response.content)
            credit=next(c for c in response.json()['items'] if c['cardType']=='신용' and c['isSelected'])
            self.assertTrue(all(item['displayName'] for item in [salary,deposit,credit]))
            for role,name,amount,day,category,instrument in S['G']['FIXED']:
                payment={'paymentAccountId':salary['accountId']} if instrument=='A' else {
                         'paymentCardId':credit['cardId']}
                response=client.post('/api/budgets/fixed/',dict(name=name,amount=amount,paymentDay=day,
                                     category=category,**payment),content_type='application/json')
                self.assertEqual(response.status_code,201,response.content)
            response=client.post('/api/budgets/savings/',dict(name='직접 등록 적금',amount=900000,transferDay=2,
                                fromAccountId=salary['accountId'],toAccountId=deposit['accountId']),content_type='application/json')
            self.assertEqual(response.status_code,201,response.content)
            saving=RecurringSaving.objects.get(user=user)
            self.assertEqual(str(saving.from_account_id),salary['accountId'])
            self.assertEqual(str(saving.to_account_id),deposit['accountId'])
            self.assertEqual(set(RecurringExpense.objects.filter(user=user).values_list('payment_card_id',flat=True)),
                             {None,Card.objects.get(pk=credit['cardId']).pk})
            response=client.get('/api/budgets/protect/')
            self.assertIsNone(response.json()['blocked'])
            self.assertEqual(response.json()['base']['available'],2230000)
            response=client.put('/api/budgets/protect/',dict(livingBudget=1050000,savingFloor=900000,cushionMonthly=150000),
                                content_type='application/json')
            self.assertEqual(response.status_code,200,response.content)
        user=User.objects.get(pk=S['uid']('onboarding','user'))
        self.assertEqual(RecurringExpense.objects.filter(user=user,source_type='manual',status='confirmed').count(),6)
        self.assertEqual(RecurringSaving.objects.filter(user=user,source_type='manual',status='confirmed').count(),1)
        self.assertFalse(Budget.objects.filter(user=user,status='approved').exists())

    @override_settings(ROOT_URLCONF='spending_fixture.service_urls')
    def test_only_designated_prepared_account_skips(self):
        for user in (self.other,self.home()):
            client=self.onboarding_client(user)
            response=client.put('/api/profile/',self.profile_body(),content_type='application/json')
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(response.json()['onboardingNextStep'],'connect')
        client=self.onboarding_client()
        Card.objects.filter(pk=S['uid']('onboarding','asset','E')).update(is_selected=False)
        self.assertEqual(client.put('/api/profile/',self.profile_body(),content_type='application/json').json()['onboardingNextStep'],'connect')
        Card.objects.filter(pk=S['uid']('onboarding','asset','E')).update(is_selected=True)
        connection=BankAccount.objects.get(pk=S['uid']('onboarding','asset','A')).connection
        connection.user=self.other
        connection.save(update_fields=['user'])
        self.assertEqual(client.get('/api/profile/').json()['onboardingNextStep'],'connect')

    @override_settings(ROOT_URLCONF='spending_fixture.service_urls')
    def test_client_cannot_choose_bypass_or_open_finance_calls(self):
        client=self.onboarding_client()
        response=client.put('/api/profile/',{**self.profile_body(),'onboardingNextStep':'salary'},content_type='application/json')
        self.assertEqual(response.status_code,400)
        for method,url in [('post','/api/finance/consents/'),('put','/api/finance/connect/accounts/'),
                           ('put','/api/finance/connect/cards/'),
                           ('post','/api/finance/connect/cards/'),('post','/api/finance/sync/')]:
            self.assertEqual(getattr(client,method)(url,content_type='application/json').status_code,503)
        self.assertEqual(Client().get('/api/profile/').status_code,403)
        no_csrf=Client(enforce_csrf_checks=True)
        no_csrf.force_login(User.objects.get(pk=S['uid']('onboarding','user')))
        self.assertEqual(no_csrf.put('/api/profile/',self.profile_body(),content_type='application/json').status_code,403)

    @override_settings(ROOT_URLCONF='spending_fixture.service_urls')
    def test_saved_lists_require_exact_alpha_identity_and_authentication(self):
        for kind,model,key in [('accounts',BankAccount,'accountId'),('cards',Card,'cardId')]:
            url=f'/api/finance/connect/{kind}/'
            self.assertEqual(Client().get(url).status_code,403)
            self.assertEqual(self.onboarding_client(self.other).get(url).status_code,503)
            for persona in ('home','onboarding'):
                user=User.objects.get(pk=S['uid'](persona,'user'))
                response=self.onboarding_client(user).get(url)
                self.assertEqual(response.status_code,200,response.content)
                self.assertEqual({row[key] for row in response.json()['items']},
                                 {str(pk) for pk in model.objects.filter(user=user).values_list('pk',flat=True)})
            user.email='different@example.test'
            user.save(update_fields=['email'])
            self.assertEqual(self.onboarding_client(user).get(url).status_code,503)
            user.email=S['ACCOUNTS']['onboarding']
            user.save(update_fields=['email'])

    @override_settings(ROOT_URLCONF='spending_fixture.service_urls')
    def test_saved_lists_keep_consent_connection_and_owner_checks(self):
        client=self.onboarding_client()
        user=User.objects.get(pk=S['uid']('onboarding','user'))
        for kind,model in [('accounts',BankAccount),('cards',Card)]:
            url=f'/api/finance/connect/{kind}/'
            rows=model.objects.filter(user=user)
            connection=rows.first().connection
            connection.status='disconnected'
            connection.disconnected_at=AS_OF
            connection.save(update_fields=['status','disconnected_at'])
            hidden={str(pk) for pk in rows.filter(connection=connection).values_list('pk',flat=True)}
            key='accountId' if kind=='accounts' else 'cardId'
            self.assertTrue(hidden.isdisjoint(item[key] for item in client.get(url).json()['items']))
            connection.status='connected'
            connection.disconnected_at=None
            connection.save(update_fields=['status','disconnected_at'])
            rows.filter(pk=rows.first().pk).update(user=self.other)
            connections=rows.values_list('connection_id',flat=True)
            Connection.objects.filter(pk__in=connections).update(user=self.other)
            self.assertEqual(client.get(url).json()['items'],[])
        Consent.objects.filter(user=user).update(revoked_at=AS_OF)
        for kind in ('accounts','cards'):
            response=client.get(f'/api/finance/connect/{kind}/')
            self.assertEqual(response.status_code,403,response.content)
            self.assertEqual(response.json()['error']['code'],'consent_required')

    @override_settings(ROOT_URLCONF='spending_fixture.service_urls')
    def test_other_alpha_assets_cannot_be_used_for_direct_registration(self):
        home=self.onboarding_client(self.home())
        account=home.get('/api/finance/connect/accounts/').json()['items'][0]['accountId']
        card=home.get('/api/finance/connect/cards/').json()['items'][0]['cardId']
        client=self.onboarding_client()
        for payment in ({'paymentAccountId':account},{'paymentCardId':card}):
            response=client.post('/api/budgets/fixed/',dict(name='남의 결제수단',amount=10000,
                                 paymentDay=1,category='FOOD',**payment),content_type='application/json')
            self.assertEqual(response.status_code,400,response.content)
        for field in ('fromAccountId','toAccountId'):
            response=client.post('/api/budgets/savings/',dict(name='남의 계좌',amount=10000,
                                 transferDay=2,**{field:account}),content_type='application/json')
            self.assertEqual(response.status_code,400,response.content)

    @override_settings(ROOT_URLCONF='spending_fixture.service_urls')
    def test_same_email_with_different_uuid_cannot_skip(self):
        User.objects.filter(pk=S['uid']('onboarding','user')).delete()
        other=User.objects.create_user(S['ACCOUNTS']['onboarding'])
        response=self.onboarding_client(other).put('/api/profile/',self.profile_body(),content_type='application/json')
        self.assertEqual(response.json()['onboardingNextStep'],'connect')
        for kind in ('accounts','cards'):
            self.assertEqual(self.onboarding_client(other).get(f'/api/finance/connect/{kind}/').status_code,503)

    @override_settings(ROOT_URLCONF='config.urls')
    def test_normal_service_profile_keeps_connection_flow(self):
        client=self.onboarding_client()
        response=client.put('/api/profile/',self.profile_body(),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn('onboardingNextStep',response.json())
        response=client.get('/api/finance/connect/accounts/')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual({a['name'] for a in response.json()['items']},{'합성 월급통장','합성 생활통장'})
        self.assertEqual(client.get('/api/finance/connect/cards/').status_code,200)

    def test_latest_main_home_budget_and_outside_scope_remain_connected(self):
        client=self.onboarding_client(self.home())
        with mock.patch('django.utils.timezone.localdate',return_value=AS_OF.date()):
            response=client.get('/api/home/')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['budget']['livingLeft'],804000)
        with transaction.atomic(): data=budget_data(self.home(),date(2026,10,31))
        self.assertEqual(data['living']['spent'],930000)

    def test_missing_bill_history_and_wrong_scope_are_not_silently_bypassed(self):
        from budgets.home import ensure_cycle
        from budgets.protect import card_reserved
        user=User.objects.get(pk=S['uid']('onboarding','user'))
        cycle=ensure_cycle(user,date(2026,11,1),date(2026,12,1))
        self.assertEqual(card_reserved(user,cycle,AS_OF.date()),dict(amount=0,pending=[]))
        SyncRun.objects.filter(user=user,product_code='card_bill_list').delete()
        self.assertEqual({p['reason'] for p in card_reserved(user,cycle,AS_OF.date())['pending']},{'bill_history_incomplete'})
        # 지킬 돈 검사를 우회한 것이 아니라 시드 근거를 보완한 것인지 확인한다.
        with transaction.atomic():
            for name,pk,fields in S['plan'](AS_OF)[0]:
                if name=='SyncRun' and fields['user_id']==user.pk and fields['product_code']=='card_bill_list':
                    SyncRun.objects.create(pk=pk,**fields)
            CardBill.objects.filter(user=user).update(scope_type='card')
            self.assertEqual({p['reason'] for p in card_reserved(user,cycle,AS_OF.date())['pending']},{'bill_scope_unknown'})
