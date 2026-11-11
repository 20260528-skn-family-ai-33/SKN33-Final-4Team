"""예산 조정 ↔ 소비 분석 협업을 실제 LLM 으로 확인한다 (11/11). 자동 테스트(test_*.py)가 아니라 손으로 돌리는 확인 도구다.

가짜 해석으로 배선만 (외부 호출 없음):
  python manage.py test chat.live_collab_check
실제 LLM (.env 의 OPENAI_API_KEY · LLM_MODEL, 호출 상한 기본 80회 · 재시도 없음):
  COLLAB_LIVE=1 python manage.py test chat.live_collab_check          (PowerShell: $env:COLLAB_LIVE=1; python manage.py test chat.live_collab_check)
  상한 바꾸기: COLLAB_LIVE_MAX_CALLS=120

테스트 DB(test_…)에 고정 예산만 넣고 실제 메시지 API 를 부른다. 거래는 없어서 쓴 돈은 0원으로 나온다 — 보는 것은 금액이 아니라
'어느 에이전트가 답했는지 · LLM 을 몇 번 불렀는지 · 조정안이 어떻게 바뀌었는지'다.
준비 단계(조정안 카드 만들기 · 소비 분석 다녀오기)는 호출을 아끼려고 가짜 해석으로 하고, 확인할 문장만 실제 LLM 에 보낸다.
"""
import os

from django.test import TestCase, override_settings

from chat.llm.fake import FakeReading
from chat.test_agent_collab import SETTINGS, CollabFixture

LIVE = os.environ.get("COLLAB_LIVE") == "1"
TRANSPORT = "TRANSPORT_CAR"

# (준비 상태, 문장, 답해야 할 에이전트, 조정안 조건 | None, 가짜 해석으로도 확인 가능한지)
#   card = 조정안 카드가 떠 있음 · ask = 어디서 조정할지 묻는 중(카드 없음) · back = 카드 → "교통은 얼마 남았어?"로 소비 분석에 와 있음
CASES = [
    ("card", "교통은 얼마 남았어?", "spending", None, True),
    ("card", "교통비 얼마 썼어?", "spending", None, True),
    ("card", "이번 달 식비 왜 늘었어?", "spending", None, False),
    ("card", "이건 얼마 남았어?", "spending", None, True),
    ("card", "쇼핑 예산은 얼마나 남았지?", "spending", None, False),
    ("card", "식비 얼마까지 늘릴 수 있어?", "budget", None, True),
    ("card", "줄일 만한 데 있어?", "budget", None, False),
    ("card", "쇼핑 더 줄여도 돼", "budget", None, True),
    ("card", "교통에서 빼줘", "budget", lambda c: c.get(TRANSPORT, 0) < 0, True),
    ("card", "이대로 해줘", "budget", None, True),
    ("ask", "교통은 얼마 남았어?", "spending", None, True),
    ("ask", "지금 식비 얼마 썼지?", "spending", None, False),
    ("ask", "쇼핑에서 가져와", "budget", None, False),
    ("ask", "저축은 건드리지 마", "budget", None, False),
    ("back", "그럼 거기서 빼줘", "budget", lambda c: c.get(TRANSPORT, 0) < 0, True),
    ("back", "그거 줄여줘", "budget", lambda c: c.get(TRANSPORT, 0) < 0, True),
    ("back", "거기서 2만 원만 빼줘", "budget", lambda c: c.get(TRANSPORT) == -20000, False),
    ("back", "쇼핑은 얼마 남았어?", "spending", None, True),
    ("back", "교통·차량에서 가져오기", "budget", lambda c: c.get(TRANSPORT, 0) < 0, True),  # 버튼 이름 그대로 = LLM 0회
]


class Recorded:
    """실제 해석기를 감싸 어떤 해석을 몇 번 불렀는지만 남긴다 (원문 · 응답은 남기지 않는다)"""

    def __init__(self, inner):
        self.inner, self.calls, self.missed = inner, [], 0

    def __call__(self, system, user, timeout=None):
        from chat.llm.schemas import schema_for
        self.calls.append({"role": schema_for(system)[0]})
        return self.inner(system, user, timeout)


@override_settings(**SETTINGS)
class LiveCollabCheck(CollabFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.checker = None
        if LIVE:
            from chat.llm.live import LiveReading
            cls.checker = Recorded(LiveReading(int(os.environ.get("COLLAB_LIVE_MAX_CALLS", "80")), None))

    def prepare(self, state):
        self.cid, self.llm = None, FakeReading()
        if state == "ask":
            self.say("식비 5만 원 늘려줘")
        else:
            self.proposal()
            if state == "back":
                self.say("교통은 얼마 남았어?")
        if LIVE:
            self.llm = self.checker
        self.llm.calls.clear()  # 준비 단계 호출은 세지 않는다

    def show(self, data, content):
        lines = (content.get("lead") or []) + content["lines"]
        card = (content.get("budgetAdjustment") or {}).get("proposal")
        print(f"   → [{data['agent']}] {' / '.join(lines)[:110]}")
        if card:
            print(f"     조정안: {self.changes(content)}")
        if content["choices"]:
            print(f"     버튼: {[c['label'] for c in content['choices']]}")
        print(f"     LLM 호출: {self.roles() or '없음'}")

    def test_1_target_dialogue(self):
        print(f"\n===== 목표 대화 ({'실제 LLM' if LIVE else '가짜 해석'}) =====")
        self.cid, self.llm = None, self.checker if LIVE else FakeReading()
        print("사용자: 식비 5만 원 늘려줘")
        data, content = self.say("식비 5만 원 늘려줘")
        self.show(data, content)
        if any(c["value"] == "living" for c in content["choices"]):
            print("사용자: (버튼) 생활비 내에서")
            data, content = self.pick(content, "living")
            self.show(data, content)
        for text, agent in (("교통은 얼마 남았어?", "spending"), ("그럼 거기서 빼줘", "budget")):
            print(f"사용자: {text}")
            data, content = self.say(text)
            self.show(data, content)
            self.assertEqual(data["agent"], agent, text)
        self.assertLess(self.changes(content).get(TRANSPORT, 0), 0)

    def test_2_sentences(self):
        print(f"\n===== 문장별 확인 ({'실제 LLM' if LIVE else '가짜 해석 — 정해 둔 문장만'}) =====")
        bad = []
        for state, text, agent, check, offline in CASES:
            if not LIVE and not offline:
                print(f"건너뜀 | {state:4} | {text}  (가짜 해석에 없는 문장 — 실제 LLM 으로만 확인)")
                continue
            self.prepare(state)
            response = self.client.post("/api/chat/messages/", {"conversationId": self.cid, "text": text}, format="json")
            if response.status_code != 200:
                got, changes, ok = f"HTTP {response.status_code}", {}, False
            else:
                data = response.json()
                content = data["messages"][-1]["content"]
                got = data["agent"] if not data["route"]["fallback"] else "해석 실패"
                changes = self.changes(content) if (content.get("budgetAdjustment") or {}).get("proposal") else {}
                ok = got == agent and (check is None or check(changes))
            roles = [call["role"] for call in self.llm.calls]
            self.llm.calls.clear()
            print(f"{'OK  ' if ok else 'NG  '} | {state:4} | {text} → {got} (기대 {agent}) | LLM {len(roles)}회 {roles}"
                  + (f" | 조정안 {changes}" if check else ""))
            if not ok:
                bad.append(text)
        self.assertEqual(bad, [], "기대와 다르게 간 문장")
