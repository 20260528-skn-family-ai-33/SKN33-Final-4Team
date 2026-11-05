"""서비스 시드의 로그인, 데이터 격리, 재실행 보존 및 원자성 검증."""
from datetime import datetime, date
from functools import partial
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase, override_settings

from budgets.models import Budget
from finance.models import BankAccount, FinancialTransaction
from finance.test_bank_response import NoCodefNetwork
from users.models import User
from users.management.commands import seed_alpha_home
from users.management.commands.seed_alpha_home import load_alpha


ALPHA = load_alpha()
SNAPSHOT = {**ALPHA, 'plan': partial(ALPHA['plan'], datetime(2026, 11, 5, 12, tzinfo=ZoneInfo('Asia/Seoul')))}
PASSWORD = 'Alpha-test!7qR29x'
COMMAND = 'users.management.commands.seed_alpha_home'


class NoExternalCalls(NoCodefNetwork):
    """setUpClass 부터(setUpTestData 포함) 실제 CODEF 클라이언트와 LLM 전송을 막는다. 모킹이 빠지면 AssertionError"""

    @classmethod
    def setUpClass(cls):
        patcher = patch('chat.llm.openai_rest.ResponsesClient.complete',
                        side_effect=AssertionError('테스트에서 실제 LLM 을 부르면 안 된다'))
        patcher.start()
        cls.addClassCleanup(patcher.stop)
        super().setUpClass()


def run_seed(**kwargs):
    output = StringIO()
    with patch(f'{COMMAND}.load_alpha', return_value=SNAPSHOT):
        call_command('seed_alpha_home', stdout=output, **kwargs)
    return output.getvalue()


class AlphaHomePreviewTest(NoExternalCalls, SimpleTestCase):
    def test_preview_does_not_access_database_or_require_password(self):
        with patch.dict('os.environ', {'ALPHA_LOGIN_PASSWORD': ''}):
            output = run_seed()
        self.assertIn('dry-run', output)
        self.assertIn("'User': 1", output)
        self.assertNotIn('onboarding', output)

    def test_plan_rows_all_belong_to_home(self):
        rows = SNAPSHOT['plan']()[0]
        home = ALPHA['uid']('home', 'user')
        self.assertEqual([pk for name, pk, _ in rows if name == 'User'], [home])
        # User · FinancialProfile 은 pk 가 회원 ID, 나머지는 user_id — 연결 · 계좌 · 거래 · 예산 · 납부 연결까지 전부 home
        self.assertTrue(all((fields.get('user_id') or pk) == home for _, pk, fields in rows))
        self.assertNotIn('alpha.onboarding@example.test', str(rows))

    def test_does_not_depend_on_local_workspace(self):
        with patch('runpy.run_path', side_effect=AssertionError('local_workspace 스크립트를 읽으면 안 된다')):
            plan = load_alpha()['plan']
            self.assertTrue(plan(datetime(2026, 11, 5, 12, tzinfo=ZoneInfo('Asia/Seoul')))[0])
        self.assertTrue(ALPHA['MERCHANTS'].is_file())
        self.assertNotIn('local_workspace', str(ALPHA['MERCHANTS']))
        self.assertNotIn('local_workspace', open(seed_alpha_home.__file__, encoding='utf-8').read())


@override_settings(ROOT_URLCONF='config.urls')
class AlphaHomeServiceTest(NoExternalCalls, TestCase):
    def setUp(self):
        self.password_env = patch.dict('os.environ', ALPHA_LOGIN_PASSWORD=PASSWORD)
        self.password_env.start()
        self.addCleanup(self.password_env.stop)

    def test_create_only_home_and_use_normal_login_and_home(self):
        other = User.objects.create_user(email='existing@example.test', password='Existing!29xR')
        before = other.password
        run_seed(apply=True)
        user = User.objects.get(email='alpha.home@example.test')
        self.assertTrue(user.check_password(PASSWORD))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(User.objects.filter(email='alpha.onboarding@example.test').exists())
        self.assertEqual(User.objects.count(), 2)
        self.assertTrue(Budget.objects.filter(user=user, status='approved').exists())
        self.assertTrue(FinancialTransaction.objects.filter(user=user).exists())
        planned = sum(name == 'FinancialTransaction' for name, _, _ in SNAPSHOT['plan']()[0])
        self.assertEqual(FinancialTransaction.objects.count(), planned)  # 다른 계정의 종속 행 없음
        self.assertEqual(BankAccount.objects.exclude(user=user).count(), 0)
        other.refresh_from_db()
        self.assertEqual(other.password, before)
        response = self.client.post('/api/auth/login/', {'email': user.email, 'password': PASSWORD}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['onboardingCompleted'])
        with patch('django.utils.timezone.localdate', return_value=date(2026, 11, 5)):
            for url in ('/api/home/', '/api/budgets/', '/api/finance/transactions/'):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, (url, response.content))

    def test_repeat_preserves_password_and_test_edits(self):
        run_seed(apply=True)
        user = User.objects.get(email='alpha.home@example.test')
        password_hash = user.password
        user.nickname = '테스트 중 변경'
        user.save(update_fields=['nickname'])
        count = FinancialTransaction.objects.count()
        with self.assertRaisesRegex(CommandError, '이미 있습니다'):
            run_seed(apply=True)
        user.refresh_from_db()
        self.assertEqual(user.nickname, '테스트 중 변경')
        self.assertEqual(user.password, password_hash)
        self.assertEqual(FinancialTransaction.objects.count(), count)

    def test_email_or_uuid_collision_does_not_overwrite_user(self):
        for fields in ({'email': 'alpha.home@example.test'},
                       {'email': 'someone@example.test', 'user_id': ALPHA['uid']('home', 'user')}):
            with self.subTest(fields=fields):
                user = User.objects.create_user(**fields)
                with self.assertRaisesRegex(CommandError, '이미 있습니다'):
                    run_seed(apply=True)
                self.assertEqual(User.objects.count(), 1)
                self.assertFalse(FinancialTransaction.objects.exists())
                user.delete()

    def test_invalid_password_writes_nothing(self):
        for value in ('', '123'):
            with patch.dict('os.environ', ALPHA_LOGIN_PASSWORD=value), self.assertRaises(CommandError):
                run_seed(apply=True)
        self.assertFalse(User.objects.exists())

    def test_midway_failure_rolls_back_all_rows(self):
        with patch.object(BankAccount, 'save', side_effect=IntegrityError('collision')):
            with self.assertRaisesRegex(CommandError, '전체 적재를 취소'):
                run_seed(apply=True)
        self.assertFalse(User.objects.exists())
        self.assertFalse(Budget.objects.exists())
