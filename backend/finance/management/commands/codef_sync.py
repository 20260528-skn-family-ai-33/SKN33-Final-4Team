from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from finance import codef
from finance.sync import sync_bank


class Command(BaseCommand):
    help = "CODEF 은행 계좌·거래를 회원 DB에 저장"

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="저장할 회원 이메일")
        banks = " · ".join(f"{name} {org}" for org, name in codef.BANKS.items())
        parser.add_argument("--org", default="0088", help=f"은행 코드 ({banks})")
        parser.add_argument("--days", type=int, default=90, help="거래내역 기간 (일)")

    def handle(self, *args, email, org, days, **options):
        if days < 1:
            raise CommandError("조회 기간은 1일 이상이어야 해요.")
        connected_id = settings.CODEF["CONNECTED_ID"]
        if not connected_id:
            raise CommandError("CODEF_CONNECTED_ID 설정이 필요해요.")
        try:
            user = get_user_model().objects.get(email=email.strip().lower())
        except get_user_model().DoesNotExist:
            raise CommandError("회원을 찾을 수 없어요.") from None

        end = timezone.localdate()
        try:
            start = end - timedelta(days=days)
        except OverflowError:
            raise CommandError("조회 기간이 너무 커요.") from None
        try:
            result = sync_bank(user, connected_id, org, start, end)
        except Exception:
            raise CommandError("은행 동기화에 실패했어요. SyncRun 실행 기록을 확인해 주세요.") from None

        self.stdout.write(
            f"계좌 {result['account_count']}개 · 새 거래 {result['new_transaction_count']}건"
            f" · 이미 있던 거래 {result['existing_transaction_count']}건"
        )
