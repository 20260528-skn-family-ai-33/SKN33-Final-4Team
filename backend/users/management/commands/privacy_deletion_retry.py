"""끝나지 않은 동의 철회 · 회원 탈퇴 삭제를 이어서 처리한다 (R21, users/deletion.py)

    python manage.py privacy_deletion_retry          # 미완료 · 실행 권한이 빈 기록만
    python manage.py privacy_deletion_retry --limit 20

여러 번 · 동시에 실행해도 회원 잠금 + 실행 토큰으로 기록마다 하나만 실행한다. 30일 지난 완료 기록은 지운다.
탈퇴 접수 뒤 로그아웃된 회원은 이 명령으로만 이어진다. 자동 실행 스케줄은 아직 없다(수동 운영) → 운영자가 실행하고
'미완료' 줄이 남으면 오류 코드를 보고 원인(외부 장애 · 키 문제 등)을 해결한 뒤 다시 실행한다.
"""
from django.core.management.base import BaseCommand

from users import deletion


class Command(BaseCommand):
    help = "미완료 개인정보 삭제(동의 철회 · 회원 탈퇴) 재처리"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, limit=None, **options):
        rows = deletion.retry_open(limit)
        left = [row for row in rows if deletion.state(row) is not None]
        for row in left:  # 회원 ID · 범위 · 오류 코드만 (원문 없음)
            self.stdout.write(f"미완료 deletion={row.pk} scope={row.scope} step={row.failed_step} error={row.error_code}")
        purged = deletion.purge_completed()
        self.stdout.write(f"처리 {len(rows)}건 · 완료 {len(rows) - len(left)}건 · 남음 {len(left)}건 · 오래된 완료 기록 삭제 {purged}건")
