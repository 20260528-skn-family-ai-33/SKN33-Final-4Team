"""
자동 불러오기 — 하루 3번 cron 으로 돌린다 (10/27 나은 결정 7)

    # 매일 07:00 · 13:00 · 21:00 (한국 시간). 자정 전후 은행 점검 시간을 피한다
    0 7,13,21 * * *  cd /앱경로/backend && python manage.py finance_auto_sync

- 대상: 연결이 살아 있는(connected) 기관이 있는 활성 회원. 범위 · 기간 규칙은 수동 다시 불러오기와 같다 (api_sync.sync_connections)
- 그 회원이 다시 불러오는 중(진행 중 작업)이면 건너뛴다
- 카드번호 · 카드 비밀번호가 필요한 기관(현대카드)은 자동에서 뺀다 (수동 새로고침 때만)
- 결과는 회원마다 작업(AsyncTask, task_type=finance_sync) 하나로 남는다. 화면에서 수동으로 누르면 진행 중인 자동 작업 번호를 받는다
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from config import tasks
from finance.api_sync import TASK_TYPE, needs_card_fields, sync_connections, target_hash
from finance.models import Connection


class Command(BaseCommand):
    help = "연결된 회원 전체의 금융 정보를 자동으로 다시 불러온다 (cron 하루 3번)"

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(
            is_active=True, status="active", codef_connected_id_ciphertext__isnull=False,
            connections__status=Connection.Status.CONNECTED, connections__disconnected_at__isnull=True,
        ).distinct().order_by("pk")
        done = busy = empty = 0
        for user in users:
            targets = [
                row for row in Connection.objects.filter(
                    user=user, status=Connection.Status.CONNECTED, disconnected_at__isnull=True,
                ).order_by("created_at")
                if not needs_card_fields(row)
            ]
            if not targets:
                empty += 1
                continue
            task, created = tasks.start_or_reuse(user, TASK_TYPE, target_hash(targets), key_prefix="finance_sync_auto")
            if not created:
                busy += 1
                continue
            tasks.run(task, lambda user=user, targets=targets: sync_connections(user, targets))
            done += 1
        # 회원 정보 · 금융 정보는 출력하지 않는다. 숫자만
        self.stdout.write(f"자동 불러오기 {done}명 · 진행 중이라 건너뜀 {busy}명 · 대상 기관 없음 {empty}명")
