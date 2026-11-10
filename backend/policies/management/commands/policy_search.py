from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from policies import reading, search
from policies.llm import EmbedFailed, embedder
from policies.regions import REGIONS, find_region

STATE = {"open": "신청 중", "always": "상시", "upcoming": "신청 예정", "unknown": "기간 확인 필요"}


def region_code(text):
    try:
        return find_region(text)
    except ValueError as error:
        raise CommandError(str(error))


class Command(BaseCommand):
    help = "정책 검색 결과 확인 (지역 · 마감 거르기 → 벡터 + 키워드). 자격 판단은 하지 않는다"

    def add_arguments(self, parser):
        parser.add_argument("query", help="사용자 문장")
        parser.add_argument("--region", required=True, help="지역 코드 또는 이름 일부 (예: 41597, 동탄구)")
        parser.add_argument("--keyword", nargs="*", default=[], help="텍스트 검색 키워드 (4단계 전까지 직접 넣는다)")
        parser.add_argument("--date", type=date.fromisoformat, help="기준일 (YYYY-MM-DD, 기본 오늘)")
        parser.add_argument("--limit", type=int, default=10, help="보여 줄 개수")
        parser.add_argument("--live-embed", action="store_true", help="CHAT_LLM 설정과 상관없이 실제 임베딩 호출")
        parser.add_argument("--read", action="store_true",
                            help="4단계 질문 읽기(실제 LLM 1회)로 검색 문장 · 키워드를 뽑아 필요마다 검색 (--keyword 는 무시)")

    def handle(self, *args, query, region, keyword, date, limit, live_embed, read, **options):
        code, today = region_code(region), date or timezone.localdate()
        try:
            total = search.candidates(code, today).count()
            embed = embedder(True if live_embed else None)[1]
            if read:
                got = reading.read(query, reading.reader(True, max_calls=1)[1])
                self._reading(got)
                hits = search.search_needs(reading.needs_for(got, query), code, today, limit=limit, embed=embed)
            else:
                hits = search.search(query, code, today, keywords=keyword, limit=limit, embed=embed)
        except (ValueError, EmbedFailed, reading.ReadFailed) as error:
            raise CommandError(f"검색하지 못했어요: {error}")
        self.stdout.write(f"{REGIONS[code]['name']} · {today} · 후보 {total}건")
        if not hits:
            self.stdout.write("맞는 정책 없음")
        for number, hit in enumerate(hits, 1):
            distance = "-" if hit.distance is None else f"{hit.distance:.3f}"
            marks = [STATE[hit.apply_state], "전국" if hit.nationwide else "지역"] + ["사업 종료"] * hit.business_ended
            found = f" [{', '.join(hit.keywords)}]" if hit.keywords else ""
            self.stdout.write(f"{number:>2}. {hit.score:.4f} 거리 {distance} {' · '.join(marks)} | {hit.policy.name}{found}")

    def _reading(self, got):
        if got.vague:
            self.stdout.write("읽기: 막연함 (6단계에서 분야 버튼으로 묻는다) → 원래 문장으로 검색")
        for number, need in enumerate(got.needs, 1):
            self.stdout.write(f"읽기 {number}: {need.query} [{', '.join(need.keywords) or '키워드 없음'}]")
        if got.place:
            names = ", ".join(REGIONS[c]["name"] for c in got.place_codes) or "지역 표에 없음"
            self.stdout.write(f"문장 속 지명: {got.place} → {names} (검색은 --region 기준, 확인 버튼은 6단계)")
