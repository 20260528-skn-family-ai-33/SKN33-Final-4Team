from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from policies import search
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

    def handle(self, *args, query, region, keyword, date, limit, live_embed, **options):
        code, today = region_code(region), date or timezone.localdate()
        try:
            total = search.candidates(code, today).count()
            hits = search.search(query, code, today, keywords=keyword, limit=limit,
                                 embed=embedder(True if live_embed else None)[1])
        except (ValueError, EmbedFailed) as error:
            raise CommandError(f"검색하지 못했어요: {error}")
        self.stdout.write(f"{REGIONS[code]['name']} · {today} · 후보 {total}건")
        if not hits:
            self.stdout.write("맞는 정책 없음")
        for number, hit in enumerate(hits, 1):
            distance = "-" if hit.distance is None else f"{hit.distance:.3f}"
            marks = [STATE[hit.apply_state], "전국" if hit.nationwide else "지역"] + ["사업 종료"] * hit.business_ended
            found = f" [{', '.join(hit.keywords)}]" if hit.keywords else ""
            self.stdout.write(f"{number:>2}. {hit.score:.4f} 거리 {distance} {' · '.join(marks)} | {hit.policy.name}{found}")
