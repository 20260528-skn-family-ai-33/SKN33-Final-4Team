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
from django.test import TestCase, SimpleTestCase
from budgets.overview import budget_data
from budgets.scope import living_spent
from finance.api import listed_transactions, transaction_item, _apply_category
from finance.spending import spending_summary
from finance.models import BankAccount, FinancialTransaction, CardBill, PaymentDue
from users.models import FinancialProfile, User
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
