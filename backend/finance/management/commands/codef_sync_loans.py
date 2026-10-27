from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finance import codef
from finance.codef_loans import LoanDataError
from finance.models import Connection
from finance.sync_loans import LoanConnectionChanged, sync_bank_loans


class Command(BaseCommand):
    help = "기존 은행 연결로 대출을 조회하고 Loan에 저장 (대출 조회 동의 필요)"

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="CODEF 연결 소유 회원 이메일")
        parser.add_argument("--org", required=True, help="이미 연결된 은행 기관 코드")
        parser.add_argument("--start", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
        parser.add_argument("--end", required=True, type=date.fromisoformat, help="YYYY-MM-DD")

    def handle(self, *args, email, org, start, end, **options):
        # 공개 샌드박스 대출이 실제 회원 DB에 섞이지 않도록 전용 격리 테스트만 사용한다.
        if settings.CODEF.get("ENV") == "sandbox":
            raise CommandError("샌드박스는 scripts/check_codef_loans_sandbox.py로 검사해 주세요.")
        if start > end:
            raise CommandError("조회 시작일은 종료일보다 늦을 수 없어요.")
        connected_id = settings.CODEF.get("CONNECTED_ID")
        if not connected_id:
            raise CommandError("CODEF_CONNECTED_ID 설정이 필요해요.")
        try:
            user = get_user_model().objects.get(email=email.strip().lower())
        except get_user_model().DoesNotExist:
            raise CommandError("회원을 찾을 수 없어요.") from None
        try:
            result = sync_bank_loans(user, connected_id, org, start, end)
        except Connection.DoesNotExist:
            raise CommandError("해당 회원의 활성 은행 연결을 찾을 수 없어요.") from None
        except PermissionError:
            raise CommandError("해당 회원의 대출 조회 동의가 필요해요.") from None
        except LoanConnectionChanged:
            raise CommandError("조회 중 은행 연결 상태가 바뀌어 저장하지 않았어요.") from None
        except codef.CodefError:
            raise CommandError("CODEF 대출 조회에 실패했어요. SyncRun 기록을 확인해 주세요.") from None
        except LoanDataError:
            raise CommandError("대출 응답에 지원하지 않거나 확인할 수 없는 항목이 있어 전체 저장하지 않았어요. SyncRun 기록을 확인해 주세요.") from None
        except Exception:
            raise CommandError("대출 동기화 실패. 회원·은행 연결 및 SyncRun 기록을 확인해 주세요.") from None
        self.stdout.write(
            f"대출 {result['loan_count']}개 · 신규 {result['created_count']}개"
            f" · 갱신 {result['updated_count']}개 · 최신 조회 보존 {result['skipped_count']}개"
        )
