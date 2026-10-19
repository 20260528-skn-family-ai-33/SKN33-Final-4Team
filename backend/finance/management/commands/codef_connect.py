"""
CODEF 은행 연결 → connectedId 받기 (test_codef.py 의 2단계 대신)

    python manage.py codef_connect              # 신한
    python manage.py codef_connect --org 0004   # 국민

인터넷뱅킹 아이디·비밀번호를 물어본다. 비밀번호는 화면에 안 보이고, 암호화해서 CODEF 에 보낸 뒤 버린다.
.env 에 이미 CODEF_CONNECTED_ID 가 있으면 새로 만들지 않고 그 ID 에 은행을 추가한다.
"""
from getpass import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from finance import codef


class Command(BaseCommand):
    help = "CODEF 은행 연결 (connectedId 발급 · 은행 추가)"

    def add_arguments(self, parser):
        parser.add_argument("--org", default="0088", help="은행 코드 (신한 0088 · 국민 0004 · 우리 0020 · 하나 0081 · 농협 0011)")

    def handle(self, *args, org, **options):
        bank = codef.BANKS.get(org, org)
        login_id = input(f"{bank} 인터넷뱅킹 아이디: ").strip()
        password = getpass("비밀번호 (입력해도 화면에 안 보임): ")
        cid = settings.CODEF["CONNECTED_ID"]
        try:
            if cid:
                codef.add_bank(cid, org, login_id, password)
                self.stdout.write(self.style.SUCCESS(f"{bank} 을(를) 기존 connectedId 에 추가했어요."))
            else:
                cid = codef.connect_bank(org, login_id, password)
                self.stdout.write(self.style.SUCCESS("연결 성공! .env 에 아래 한 줄을 추가해 주세요 (깃·단톡방에 올리지 마세요):"))
                self.stdout.write(f'CODEF_CONNECTED_ID="{cid}"')
        except codef.CodefError as e:
            raise CommandError(f"연결 실패 {e}")
        finally:
            del password  # 메모리에서도 바로 지우기
