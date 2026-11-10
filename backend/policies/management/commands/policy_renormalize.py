from django.core.management.base import BaseCommand

from policies import sync

SHOWN = ("min_age", "max_age", "apply_type", "last_apply_end")


class Command(BaseCommand):
    help = "저장된 원본으로 정리 칸(나이 · 기간 · 코드 …)을 다시 만든다. normalize.py 규칙을 고친 뒤 실행. 기본은 바뀔 것만 보여 준다"

    def add_arguments(self, parser):
        parser.add_argument("--save", action="store_true", help="실제로 저장 (없으면 미리보기)")
        parser.add_argument("--show", type=int, default=30, help="바뀐 정책을 몇 개까지 보여 줄지")

    def handle(self, *args, save, show, **options):
        counts, changed = sync.renormalize(save=save)
        self.stdout.write(f"정책 {len(changed)}건이 " + ("바뀌었어요" if save else "바뀔 거예요 (--save 로 저장)"))
        for name, count in sorted(counts.items(), key=lambda item: -item[1]):
            self.stdout.write(f"  {name}: {count}건")
        for policy, diff in changed[:show]:
            parts = ", ".join(f"{name} {before} → {after}" for name, (before, after) in diff.items() if name in SHOWN)
            self.stdout.write(f"  {policy.pk} {policy.name[:30]} {parts}")
        if "embed_text" in counts:
            self.stdout.write("임베딩 문장이 바뀐 정책이 있어요. sync_policies 를 다시 돌려 임베딩을 만들어 주세요.")
