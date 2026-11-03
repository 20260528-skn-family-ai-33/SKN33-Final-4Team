from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance import classifier
from finance.models import FinancialTransaction


class Command(BaseCommand):
    help = "저장된 소비 거래를 거래 분류 에이전트로 다시 분류 (사용자가 확정한 거래는 건드리지 않음)"

    def add_arguments(self, parser):
        parser.add_argument("--email", help="이 회원만 다시 분류 (없으면 전체 회원)")

    def handle(self, *args, email=None, **options):
        users = get_user_model().objects.order_by("pk")
        if email:
            users = users.filter(email=email.strip().lower())
            if not users.exists():
                raise CommandError("회원을 찾을 수 없어요.")

        for user in users:
            txs = FinancialTransaction.objects.filter(user=user, transaction_type="spending").exclude(confirmation_state="confirmed")
            if not txs.exists():
                continue
            with transaction.atomic():
                # 잠금 순서는 거래 저장 뒤 처리(pipeline)와 같다: 회원 먼저 → 거래(ID 순)
                get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk).first()
                classifier.classify(user, txs.select_for_update().order_by("pk"))
            result = Counter(
                tx.classification_source or "확인 필요"
                for tx in txs.only("classification_source", "confirmation_state")
            )
            self.stdout.write(f"{user.email}: " + " · ".join(f"{k} {n}건" for k, n in sorted(result.items())))
