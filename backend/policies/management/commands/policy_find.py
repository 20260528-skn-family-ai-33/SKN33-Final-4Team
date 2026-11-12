from collections import Counter
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from policies import search
from policies.management.commands.policy_search import STATE, region_code
from policies.normalize import _clean
from policies.regions import REGIONS


class Command(BaseCommand):
    help = "정답 후보 찾기 (3단계). 검색 순위와 상관없이 그 지역 후보 중 단어가 들어간 정책을 모두 보여 준다"

    def add_arguments(self, parser):
        parser.add_argument("words", nargs="+", help="찾을 단어 (하나라도 들어가면 나온다)")
        parser.add_argument("--region", required=True, help="지역 코드 또는 이름 일부 (예: 41597, 동탄구)")
        parser.add_argument("--date", type=date.fromisoformat, help="기준일 (YYYY-MM-DD, 기본 오늘)")
        parser.add_argument("--limit", type=int, default=40, help="보여 줄 개수")
        parser.add_argument("--detail", action="store_true", help="지원 내용 앞부분도 보여 준다")

    def handle(self, *args, words, region, date, limit, detail, **options):
        code, today = region_code(region), date or timezone.localdate()
        try:
            pool = search.candidates(code, today)
        except ValueError as error:
            raise CommandError(str(error))
        words = list(dict.fromkeys(w.strip() for w in words if w.strip()))
        found = {}
        for word in words:
            head = Q(name__icontains=word) | Q(raw__plcyKywdNm__icontains=word)
            for policy in pool.filter(head | Q(search_text__icontains=word)):
                where = "이름" if word in policy.name or word in (policy.raw.get("plcyKywdNm") or "") else "본문"
                found.setdefault(policy.pk, (policy, {}))[1][word] = where
        names = Counter(pool.values_list("name", flat=True))
        ordered = sorted(found.values(), key=lambda item: (-list(item[1].values()).count("이름"), -len(item[1]),
                                                           item[0].name, item[0].pk))
        self.stdout.write(f"{REGIONS[code]['name']} · {today} · 후보 {pool.count()}건 중 {len(found)}건")
        for policy, matched in ordered[:limit]:
            marks = [STATE[search._apply_state(policy, today)],
                     "전국" if len(policy.zip_codes) > search.NATIONWIDE_CODES else "지역"]
            marks += ["사업 종료"] * search._business_ended(policy, today)
            marks += [f"같은 이름 {names[policy.name]}건"] * (names[policy.name] > 1)
            words_found = ", ".join(f"{w}:{where}" for w, where in matched.items())
            self.stdout.write(f"{policy.pk} {' · '.join(marks)} | {policy.name} [{words_found}]")
            if detail:
                self.stdout.write(f"    {_clean(policy.raw, 'plcySprtCn')[:120]}")
        if len(found) > limit:
            self.stdout.write(f"… {len(found) - limit}건 더 있어요 (--limit 으로 늘리기)")
