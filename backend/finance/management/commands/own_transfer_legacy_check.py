"""R11 점검 (읽기 전용): 11/5 전 규칙으로 내 계좌 이체(own_transfer)가 됐지만 판단 표시(own_transfer_review)가 없는 은행 거래.

  python manage.py own_transfer_legacy_check            회원별 개수만
  python manage.py own_transfer_legacy_check --ids      거래 ID · 날짜 · 금액 · 분류 구분까지 (적요 · 계좌번호는 찍지 않음)

DB 를 바꾸지 않는다 (읽기 전용 트랜잭션). 구분:
  linked_out   규칙이 입금과 이은 출금 → 목록에서 '자동 분류'로 보이고 사용자가 [맞아요] · [연결 해제] 할 수 있다 (finance.api.LEGACY_OWN)
  linked_in    그 상대 입금 → 출금 쪽 [연결 해제]가 함께 되돌린다
  unlinked     연결 없이 own_transfer 인 거래 → 화면에서 고칠 길이 없다. 보정 대상 후보 (처리 방식은 보고 후 결정)
  saving       저축으로 바뀐 표시 없는 출금 → 저축 기능 쪽 근거라 여기서 다루지 않고 개수만
  protected    납부 연결 · 예산 배분이 있는 출금 (사용자가 바로 해제할 수 없음, 개수만)
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from finance.models import FinancialTransaction as Tx, TransactionLink
from finance.transfer_rules import OWN_REVIEW


class Command(BaseCommand):
    help = "표시 없는 예전 내 계좌 이체 기록 점검 (읽기 전용)"

    def add_arguments(self, parser):
        parser.add_argument("--ids", action="store_true", help="거래 ID 목록도 출력")

    def handle(self, *args, ids=False, **options):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            rows = (Tx.objects.filter(source_kind="bank", transaction_type__in=["own_transfer", "saving"])
                    .exclude(field_states__has_key=OWN_REVIEW).order_by("user_id", "occurred_on", "pk"))
            links = TransactionLink.objects.filter(relation_type=TransactionLink.Relation.OWN_TRANSFER)
            outs = set(links.values_list("from_transaction_id", flat=True))
            ins = set(links.values_list("to_transaction_id", flat=True))
            counts, listed = defaultdict(Counter), []
            for tx in rows.prefetch_related("payment_matches", "allocations"):
                if tx.transaction_type == "saving":
                    kind = "saving"
                elif tx.pk in outs and tx.direction == "out":
                    kind = "linked_out"
                elif tx.pk in ins and tx.direction == "in":
                    kind = "linked_in"
                else:
                    kind = "unlinked"
                counts[tx.user_id][kind] += 1
                if tx.direction == "out" and (tx.payment_matches.all() or tx.allocations.all()):
                    counts[tx.user_id]["protected"] += 1
                listed.append((tx.user_id, kind, tx))
        if not counts:
            self.stdout.write("표시 없는 예전 내 계좌 이체 기록이 없어요.")
            return
        for user_id, counter in counts.items():
            self.stdout.write(f"회원 {user_id}: " + ", ".join(f"{k} {v}" for k, v in sorted(counter.items())))
        if ids:
            for user_id, kind, tx in listed:
                self.stdout.write(f"  {user_id}\t{kind}\t{tx.pk}\t{tx.occurred_on}\t{tx.direction}\t{tx.amount}")
