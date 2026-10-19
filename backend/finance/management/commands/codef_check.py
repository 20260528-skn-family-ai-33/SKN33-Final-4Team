"""
CODEF 확인 명령 (test_codef.py · test_transactions.py 대신)

    python manage.py codef_check                     # 키 확인 + 보유 계좌 (신한)
    python manage.py codef_check --org 0004          # 국민은행
    python manage.py codef_check --tx 1234           # 끝 4자리 1234 계좌의 최근 30일 거래 5건
    python manage.py codef_check --tx 1234 --days 7 --limit 10

connectedId 는 .env 의 CODEF_CONNECTED_ID 를 쓴다. 없으면 먼저 codef_connect.
⚠️ 거래 설명에는 개인정보가 있을 수 있어요. 터미널 화면을 단톡방 등에 그대로 올리지 마세요.
"""
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from finance import codef


class Command(BaseCommand):
    help = "CODEF 키·연결·계좌·거래내역 확인 (저장하지 않음)"

    def add_arguments(self, parser):
        parser.add_argument("--org", default="0088", help="은행 코드 (신한 0088 · 국민 0004 · 우리 0020 · 하나 0081 · 농협 0011)")
        parser.add_argument("--tx", metavar="끝4자리", help="이 계좌의 거래내역도 보기")
        parser.add_argument("--days", type=int, default=30, help="거래내역 기간 (일)")
        parser.add_argument("--limit", type=int, default=5, help="거래내역 몇 건 보여 줄지")

    def handle(self, *args, org, tx, days, limit, **options):
        bank = codef.BANKS.get(org, org)
        self.stdout.write(f"환경: {settings.CODEF['ENV']} · 은행: {bank}({org})")

        # 1. 키 확인
        if not codef.token_ok():
            raise CommandError("토큰 실패 — .env 의 CODEF_DEMO_ID · CODEF_DEMO_SECRET 를 확인해 주세요.")
        self.stdout.write(self.style.SUCCESS("1. 키 확인 OK"))

        cid = settings.CODEF["CONNECTED_ID"]
        if not cid:
            raise CommandError("CODEF_CONNECTED_ID 가 없어요. 먼저 python manage.py codef_connect")

        # 2. 보유 계좌
        try:
            accounts = codef.list_accounts(cid, org)
        except codef.CodefError as e:
            raise CommandError(f"계좌 조회 실패 {e}")
        self.stdout.write(self.style.SUCCESS(f"2. 보유 계좌 {len(accounts)}개"))
        for a in accounts:
            self.stdout.write(f"   {codef.mask(a['account_no'])}  {a['name']:<12} {a['balance']:>12,}원  (구분 {a['kind_code']})")

        if not tx:
            return

        # 3. 거래내역 — 끝 4자리로 계좌 찾기 (전체 번호를 칠 필요 없게)
        found = [a for a in accounts if a["account_no"].endswith(tx)]
        if len(found) != 1:
            raise CommandError(f"끝자리 {tx} 인 계좌가 {len(found)}개예요. 위 목록에서 다시 골라 주세요.")
        end = date.today()
        start = end - timedelta(days=days)
        try:
            rows = codef.list_transactions(cid, org, found[0]["account_no"], start, end)
        except codef.CodefError as e:
            raise CommandError(f"거래내역 조회 실패 {e}")
        self.stdout.write(self.style.SUCCESS(f"3. {codef.mask(tx)} 거래 {len(rows)}건 ({start} ~ {end}) · 최근 {min(limit, len(rows))}건"))
        for r in rows[:limit]:
            sign = "-" if r["direction"] == "out" else "+"
            self.stdout.write(f"   {r['occurred_on']} {r['occurred_time'] or ''}  {sign}{r['amount']:>10,}원  잔액 {r['balance_after']:>11,}원  {r['source_description']}")
            self.stdout.write(f"      설명칸 {r['source_detail']}")
