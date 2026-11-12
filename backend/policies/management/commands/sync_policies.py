import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from policies import sync
from policies.llm import embedder


class Command(BaseCommand):
    help = "청년 정책을 적재하고 바뀐 정책만 임베딩 (shortcut: 파일 적재만 있음, 온통청년 API 승인 뒤 API 수집 추가)"

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="온통청년 정책 JSON 파일")
        parser.add_argument("--data-date", required=True, type=date.fromisoformat, help="데이터 기준일 (YYYY-MM-DD)")
        parser.add_argument("--live-embed", action="store_true", help="CHAT_LLM 설정과 상관없이 실제 임베딩 호출")
        parser.add_argument("--no-embed", action="store_true", help="적재만 하고 임베딩은 하지 않음")

    def handle(self, *args, file, data_date, live_embed, no_embed, **options):
        try:
            with open(file, encoding="utf-8") as source:
                rows = json.load(source)
        except (OSError, ValueError) as error:
            raise CommandError(f"파일을 읽을 수 없어요: {error}")
        try:
            result = sync.load(rows, source="file", data_date=data_date)
        except sync.SyncRejected as error:
            raise CommandError(f"적재하지 않았어요. 기존 데이터는 그대로예요: {error}")
        self.stdout.write(f"받음 {result.received} · 바뀜 {result.changed} · 비활성화 {result.deactivated}"
                          f" · 표에 없는 지역 코드 {result.unknown_regions}")
        if no_embed:
            return
        model, embed = embedder(True if live_embed else None)
        done, left = sync.embed_changed(model, embed)
        self.stdout.write(f"임베딩({model}) 완료 {done} · 남음 {left}")
        if left:
            raise CommandError("임베딩이 끝나지 않았어요. 다시 실행하면 남은 정책만 이어서 해요.")
