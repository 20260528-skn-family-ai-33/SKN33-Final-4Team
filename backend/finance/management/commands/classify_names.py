import csv
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance import classifier
from finance.models import FinancialTransaction


def stage(tx):
    """어느 단계에서 정해졌는지 (임시 회원이라 2단계 사용자 기억은 없음)"""
    if tx.suggestion_reason == classifier.MULTI_ITEM_REASON and "classifier" not in tx.field_states:
        return "1 다품목"
    if tx.suggestion_reason == classifier.PAY_CHARGE_REASON:
        return "1 페이 충전"
    if "classifier" in tx.field_states:
        return "5 검색+Jev"
    if tx.classification_source == "rule":
        return "3 규칙"
    if tx.suggestion_source == "ml":
        return "4 SVM"
    return "-"


class Command(BaseCommand):
    help = "가맹점 이름 목록을 실제 거래 분류 에이전트(1~6단계, 실제 SVM·검색·Jev)로 분류해 표로 보여 준다. DB에는 아무것도 남기지 않는다"

    def add_arguments(self, parser):
        parser.add_argument("names_file", help="가맹점 이름을 한 줄에 하나씩 적은 UTF-8 파일")
        parser.add_argument("--csv", dest="csv_path", help="결과를 CSV로도 저장할 경로 (정답 칸을 붙여 임계값 평가에 쓰기)")

    def handle(self, *args, names_file, csv_path=None, **options):
        path = Path(names_file)
        if not path.exists():
            raise CommandError("이름 파일을 찾을 수 없어요.")
        names = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        if not names:
            raise CommandError("이름이 비어 있어요.")

        rows = []
        # 임시 회원·거래로 실제 classify() 를 그대로 돌리고, 결과만 읽은 뒤 전부 되돌린다
        with transaction.atomic():
            user = get_user_model().objects.create_user(email=f"classify-names-{uuid.uuid4().hex}@example.test")
            txs = [
                FinancialTransaction.objects.create(
                    user=user, source_kind="manual", occurred_on="2026-01-01", amount=10000, direction="out",
                    transaction_type="spending", merchant_name=name,
                    confirmed_category_id="UNCLASSIFIED", confirmation_state="needs_review",
                )
                for name in names
            ]
            classifier.classify(user, FinancialTransaction.objects.filter(pk__in=[t.pk for t in txs]))
            for tx in FinancialTransaction.objects.filter(pk__in=[t.pk for t in txs]).order_by("created_at"):
                record = tx.field_states.get("classifier", {})
                candidates = [c["category"] for c in tx.suggestion_candidates or []]
                rows.append({
                    "이름": tx.merchant_name,
                    "결과": tx.confirmed_category_id,
                    "단계": stage(tx),
                    "확신도": "" if tx.suggestion_confidence is None else f"{tx.suggestion_confidence:.2f}",
                    "후보": " ".join(candidates),
                    "검색": f"{record.get('source') or '0건'}:{record.get('industry') or '-'}" if record else "",
                    "메모": " ".join(k for k in ("dropped_industry", "store_mismatch", "error") if record.get(k)),
                    "Jev": f"{record.get('choice')} {record.get('confidence')}" if record.get("choice") else "",
                    "ms": record.get("ms", ""),
                    "이유": tx.suggestion_reason or "",
                })
            transaction.set_rollback(True)

        for r in rows:
            self.stdout.write(" | ".join(str(r[k]) for k in ("이름", "결과", "단계", "확신도", "후보", "검색", "Jev", "ms", "메모")))
        counts = {}
        for r in rows:
            key = "확인 필요" if r["결과"] == "UNCLASSIFIED" else r["단계"]
            counts[key] = counts.get(key, 0) + 1
        self.stdout.write(f"\n총 {len(rows)}건: " + " · ".join(f"{k} {n}" for k, n in sorted(counts.items())))
        if csv_path:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]) + ["정답"])
                writer.writeheader()
                writer.writerows(rows)
            self.stdout.write(f"CSV 저장: {csv_path}")
