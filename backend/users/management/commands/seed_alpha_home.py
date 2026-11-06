"""현재 서비스 DB에 온보딩을 완료한 합성 계정 하나(alpha.home@example.test)를 생성한다.

    python manage.py seed_alpha_home                                  # 미리보기 (DB 연결·쓰기 없음)
    ALPHA_LOGIN_PASSWORD='<비밀번호>' python manage.py seed_alpha_home --apply

계획은 spending_fixture/alpha_home.py + data/alpha_home_merchants.csv (Git 관리, 로컬 실험 폴더 불필요).
같은 이메일·UUID 계정이 있으면 아무것도 바꾸지 않고 중단한다.
"""
from collections import Counter
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q


def load_alpha():
    """home 계정 계획 모듈 (spending_fixture.alpha_home, Git 관리). plan()은 home 계정 행만 만든다"""
    from spending_fixture import alpha_home
    return vars(alpha_home)


class Command(BaseCommand):
    help = '현재 DB에 alpha.home 계정 생성. 기본은 미리보기, --apply로 적재 (기존 계정 보존).'
    requires_system_checks = []  # 미리보기는 DB에 접근하지 않는다.

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='현재 설정의 default DB에 생성')

    def handle(self, *args, **options):
        alpha = load_alpha()
        rows, _, _, as_of = alpha['plan']()
        counts = Counter(name for name, _, _ in rows)
        self.stdout.write(f"대상 DB: {connection.settings_dict['NAME']}")
        self.stdout.write(f"계정: {alpha['EMAIL']} / KST 기준: {as_of.isoformat()}")
        self.stdout.write(f'생성 예정 건수: {dict(counts)}')
        if not options['apply']:
            self.stdout.write('dry-run: DB 연결·쓰기 없음. ALPHA_LOGIN_PASSWORD 설정 후 --apply로 생성합니다.')
            return

        models = alpha['model_map']()
        user_row = next(row for row in rows if row[0] == 'User')
        user = models['User'](pk=user_row[1], **user_row[2])
        password = os.environ.get('ALPHA_LOGIN_PASSWORD')
        if not password:
            raise CommandError('환경변수 ALPHA_LOGIN_PASSWORD에 로그인 비밀번호를 설정하세요.')
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages)) from exc
        user.set_password(password)

        try:
            with transaction.atomic():
                if models['User'].objects.filter(Q(pk=user.pk) | Q(email=user.email)).exists():
                    raise CommandError('같은 이메일 또는 UUID의 계정이 이미 있습니다. 기존 데이터·비밀번호는 변경하지 않습니다.')
                for name, pk, fields in rows:
                    obj = user if name == 'User' else models[name](pk=pk, **fields)
                    obj.full_clean(exclude=[f.name for f in obj._meta.fields if f.is_relation],
                                   validate_unique=False, validate_constraints=False)
                    obj.save(force_insert=True)
        except IntegrityError as exc:
            raise CommandError('기존 데이터 충돌 또는 참조 데이터 누락으로 전체 적재를 취소했습니다. migrate 상태를 확인하세요.') from exc
        self.stdout.write(self.style.SUCCESS('alpha.home 생성 완료. 기본 서비스 화면에서 설정한 비밀번호로 로그인하세요.'))
