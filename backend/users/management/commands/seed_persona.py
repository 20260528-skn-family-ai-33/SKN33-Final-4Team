"""시연용 페르소나 계정(온보딩 완료)을 현재 DB에 만든다.

    python manage.py seed_persona a                                   # 미리보기 (DB 쓰기 없음)
    DEMO_LOGIN_PASSWORD='<비밀번호>' python manage.py seed_persona a --apply
    DEMO_LOGIN_PASSWORD='<비밀번호>' python manage.py seed_persona a --apply --reset   # 지우고 다시

거래는 오늘(KST)까지만 넣는다. 시연 전날 밤에 --reset 으로 다시 만들면 전날까지의 거래가 들어간다.
순서와 원칙은 spending_fixture/persona_seed.py 맨 위 설명을 본다.
"""
from collections import Counter
from datetime import datetime
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from spending_fixture import persona_seed as ps


class Command(BaseCommand):
    help = '시연용 페르소나 계정 생성. 기본은 미리보기, --apply 로 생성.'

    def add_arguments(self, parser):
        parser.add_argument('persona', choices=sorted(ps.PERSONAS))
        parser.add_argument('--apply', action='store_true', help='현재 설정의 default DB에 생성')
        parser.add_argument('--reset', action='store_true', help='같은 이메일의 시연 계정을 먼저 지운다')
        parser.add_argument('--real-classify', action='store_true',
                            help='가게 거래까지 전부 실제 분류기에 넣는다 (외부 검색 · LLM 호출, 결과가 매번 다를 수 있음)')

    def handle(self, *args, persona, **options):
        cfg = ps.PERSONAS[persona]
        as_of = datetime.now(ps.KST).date()
        rows = ps.load_rows(persona, as_of)
        uniq = [r for r in rows if r['is_duplicate'] == '0']
        self.stdout.write(f"대상 DB: {connection.settings_dict['NAME']} · 계정: {cfg['email']} ({cfg['nickname']})")
        self.stdout.write(f'기준일(KST): {as_of} · CSV 행 {len(rows)} · 거래 {len(uniq)} · '
                          f"종류 {dict(Counter(r['transaction_type'] for r in uniq))}")
        if not options['apply']:
            self.stdout.write('미리보기: DB에 쓰지 않았어요. DEMO_LOGIN_PASSWORD 를 정하고 --apply 로 만드세요.')
            return

        User = get_user_model()
        if options['reset']:
            deleted = ps.delete_persona(persona)
            self.stdout.write(f'기존 시연 계정 삭제: 행 {deleted}개')
        if User.objects.filter(email=cfg['email']).exists():
            raise CommandError('같은 이메일의 계정이 이미 있어요. 다시 만들려면 --reset 을 붙이세요.')
        password = os.environ.get('DEMO_LOGIN_PASSWORD')
        if not password:
            raise CommandError('환경변수 DEMO_LOGIN_PASSWORD 에 로그인 비밀번호를 넣어 주세요.')
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages)) from exc

        log = lambda text: self.stdout.write('  · ' + text)
        with transaction.atomic():
            user, txs = ps.insert_connected_state(persona, as_of, password, real_classify=options['real_classify'])
        log(f'1. 연결 상태 저장: 거래 {len(txs)}행 (체크카드 통장 중복 포함)')
        try:
            ps.register_loans(persona, user, log)
        except RuntimeError as exc:
            raise CommandError(f'대출 등록 중 멈춤: {exc}\n계정은 남아 있어요. 고친 뒤 --reset 으로 다시 만드세요.') from exc
        ps.run_pipeline(user, txs, real_classify=options['real_classify'])
        log('2. 거래 처리(파이프라인) 완료')
        try:
            budget = ps.onboard(persona, user, log)
        except RuntimeError as exc:
            raise CommandError(f'3. 온보딩 중 멈춤: {exc}\n계정은 남아 있어요. 고친 뒤 --reset 으로 다시 만드세요.') from exc
        log('3. 온보딩 완료')
        ps.summarize(user, budget, log)
        self.stdout.write(self.style.SUCCESS(f"완료: {cfg['email']} 로 로그인하세요."))
