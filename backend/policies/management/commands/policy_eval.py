from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from policies import evaluate, reading, search
from policies.llm import EmbedFailed, embedder
from policies.regions import REGIONS

QUESTIONS = Path(__file__).resolve().parents[2] / "eval" / "questions.json"


def _rank(rank):
    return str(rank) if rank else "-"


def _percent(value):
    return f"{value * 100:5.1f}%"


class Command(BaseCommand):
    help = ("정답 문제로 검색 채점 (3단계). 키워드 없이 · 사람이 뽑은 키워드로 두 번 본다. "
            "--auto 면 4단계 질문 읽기(LLM)로 한 번 더 본다. 실제 임베딩을 부른다")

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, default=QUESTIONS, help="평가 파일 (기본 policies/eval/questions.json)")
        parser.add_argument("--only", nargs="*", default=[], help="이 id 의 질문만 (예: q01 q02)")
        parser.add_argument("--check", action="store_true", help="파일 형식 · 정답 번호만 확인하고 채점하지 않는다")
        parser.add_argument("--limit", type=int, default=search.LIMIT, help="후보 개수 (기본 search.LIMIT)")
        parser.add_argument("--auto", action="store_true", help="질문 읽기(실제 LLM, 질문마다 1회)로 '자동' 방식도 채점")

    def handle(self, *args, file, only, check, limit, auto, **options):
        try:
            today, questions = evaluate.load(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CommandError(f"평가 파일이 없어요: {file}")
        except evaluate.EvalFileError as error:
            raise CommandError("평가 파일을 고쳐 주세요\n" + "\n".join(f"  - {p}" for p in error.problems))
        if only:
            if missing := set(only) - {q.id for q in questions}:
                raise CommandError(f"없는 id: {', '.join(sorted(missing))}")
            questions = [q for q in questions if q.id in only]
        drafts = [q.id for q in questions if q.draft]
        answerable = sum(bool(q.answers) for q in questions)
        self.stdout.write(f"기준일 {today} · 질문 {len(questions)}개 "
                          f"(정답 있음 {answerable} · 정답 없음 {len(questions) - answerable - len(drafts)} · 정답 미정 {len(drafts)})")
        if drafts:
            self.stdout.write(f"정답 미정이라 채점에서 빼요: {', '.join(drafts)}")
        if check:
            self.stdout.write("형식 · 정답 번호 문제 없음")
            return
        questions = [q for q in questions if not q.draft]
        if not questions:
            raise CommandError("채점할 질문이 없어요")
        try:
            complete = reading.reader(True, max_calls=len(questions))[1] if auto else None
            results = evaluate.run(questions, today, embedder(True)[1], limit=limit, complete=complete)
        except (EmbedFailed, reading.ReadFailed) as error:
            raise CommandError(f"채점하지 못했어요: {error}")
        self._table(questions, results, limit)
        self._summary(results, limit)
        self._readings(results)
        self._distances(results)

    def _table(self, questions, results, limit):
        by_key = {(r.question.id, r.mode): r for r in results}
        names = evaluate.modes(results)
        self.stdout.write(f"\n순위 (정답 묶음마다, - = {limit}위 안에 없음)")
        rows = [(q, [",".join(map(_rank, by_key[(q.id, mode)].ranks)) or "(없음)" for mode in names]) for q in questions]
        width = max(10, *(len(cell) + 2 for _, cells in rows for cell in cells))  # 묶음이 많아도 칸이 붙지 않게
        self.stdout.write(f"{'id':<6}{'종류':<5}" + "".join(f"{name:<{width}}" for name in names) + "질문")
        for q, cells in rows:
            query = q.query if len(q.query) <= 40 else q.query[:40] + "…"
            self.stdout.write(f"{q.id:<6}{q.kind:<5}" + "".join(f"{cell:<{width}}" for cell in cells) + query)

    def _summary(self, results, limit):
        self.stdout.write(f"\n{'':<8}{'Hit@3':>8}{'Hit@10':>8}{f'{limit}위 안':>8}{'MRR':>7}{'묶음':>8}")
        for mode in evaluate.modes(results):
            s = evaluate.summary(results, mode)
            if s is None:
                self.stdout.write("정답 있는 질문이 없어요")
                return
            self.stdout.write(f"{mode:<8}{_percent(s['hit3']):>8}{_percent(s['hit10']):>8}{_percent(s['found']):>8}"
                              f"{s['mrr']:>7.3f}{_percent(s['groups']):>8}")
        self.stdout.write(f"(정답 있는 질문 {s['count']}개 기준. 묶음 = 정답 묶음 중 {limit}위 안에 든 비율)")

    def _readings(self, results):
        """자동 방식이 문장을 어떻게 읽었는지: 사람이 뽑은 키워드와 나란히 본다"""
        rows = [r for r in results if r.mode == evaluate.AUTO]
        if not rows:
            return
        self.stdout.write("\n질문 읽기 (자동) — 사람 키워드 | 읽은 필요 [키워드] · 지명")
        for r in rows:
            human = ", ".join(r.question.keywords) or "-"
            if r.read_error:
                self.stdout.write(f"  {r.question.id:<5} {human} | 읽기 실패 ({r.read_error}) → 원래 문장으로 검색")
                continue
            got = r.reading
            needs = " / ".join(f"{n.query} [{', '.join(n.keywords)}]" for n in got.needs) or "(막연 → 원래 문장으로 검색)"
            place = ""
            if got.place:
                regions = ", ".join(REGIONS[c]["name"] for c in got.place_codes[:3]) or "표에 없음"
                place = f" · 지명 {got.place} → {regions}"
            self.stdout.write(f"  {r.question.id:<5} {human} | {needs}{place}")

    def _distances(self, results):
        """'맞는 정책 없음' 기준을 정할 재료: 정답 없는 질문의 1위 거리가 정답 있는 질문과 갈리는지"""
        rows = [r for r in results if r.mode == "벡터만" and r.top_distance is not None]
        if not any(not r.question.answers for r in rows):
            return
        self.stdout.write("\n1위 거리 (벡터만, 작을수록 가깝다)")
        for r in sorted(rows, key=lambda r: r.top_distance):
            self.stdout.write(f"  {r.top_distance:.3f} {'정답 없음' if not r.question.answers else '정답 있음'} {r.question.id}")
