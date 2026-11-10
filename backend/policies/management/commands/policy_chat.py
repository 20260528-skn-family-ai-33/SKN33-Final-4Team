from datetime import date
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from chat.router_core import MemoryStateStore
from policies import agent, reading
from policies.llm import embedder

BUTTON = "[버튼]"


class Command(BaseCommand):
    help = ("청년 정책 챗봇 확인 (6단계 고정 흐름). 입력을 차례로 보낸다. 버튼은 이름을 그대로 쓴다. "
            f"'{BUTTON}' 은 기능 버튼(문장 없이 들어옴). 실제 LLM · 임베딩을 부른다 (읽기는 문장마다 1회)")

    def add_arguments(self, parser):
        parser.add_argument("inputs", nargs="+", help=f"보낼 입력 차례대로 (예: {BUTTON} 서울특별시 관악구 \"월세 지원 있어?\")")
        parser.add_argument("--birth", type=date.fromisoformat, default=date(1999, 3, 1), help="생년월일 (기본 만 27세)")
        parser.add_argument("--gender", choices=("male", "female"), default=None)
        parser.add_argument("--salary", type=int, default=None, help="월 실수령액 (있으면 재직자로 본다)")
        parser.add_argument("--date", type=date.fromisoformat, default=date(2026, 11, 10), help="기준일")

    def handle(self, *args, inputs, birth, gender, salary, date, **options):
        user = SimpleNamespace(financial_profile=SimpleNamespace(birth_date=birth, gender=gender,
                                                                 monthly_take_home_income=salary))
        store, complete, embed = MemoryStateStore(), reading.reader(True, max_calls=len(inputs))[1], embedder(True)[1]
        for number, said in enumerate(inputs, 1):
            text = None if said == BUTTON else said
            request = {"agent": "policy", "mode": "start" if number == 1 else "continue",
                       "entry": "button" if text is None else "text", "text": text, "work_ref": None,
                       "input_id": str(number), "conversation_id": "conversation", "today": date.isoformat(),
                       "as_of": None, "context": {}}
            try:
                result = agent.handle(request, store=store, user=user, complete=complete, embed=embed)
            except Exception as error:  # 확인 명령이라 어디서 멈췄는지 보여 준다
                raise CommandError(f"{number}번째 입력에서 멈췄어요: {type(error).__name__}: {error}")
            self.stdout.write(f"\n나 > {said}")
            self.stdout.write(f"[{result['status']}]" + (f" {result['error']}" if result.get("error") else ""))
            reply = result.get("reply") or {}
            for line in reply.get("lines", []):
                self.stdout.write(f"봇 | {line}")
            for notice in reply.get("notices", []):
                self.stdout.write(f"안내 | {notice}")
            choices = [c["label"] for c in reply.get("choices", [])]
            if choices:
                self.stdout.write("버튼 | " + " / ".join(choices[:20]) + (" …" if len(choices) > 20 else ""))
