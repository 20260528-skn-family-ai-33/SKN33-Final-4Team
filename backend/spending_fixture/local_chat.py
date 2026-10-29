"""개발용 로컬 채팅 화면 — 실제 서비스와 분리 (chat API · replies.py · config · 대화 DB 를 쓰지 않는다)

페르소나(윤서 · 민재 · 지우)를 골라 run_turn → 소비 분석 에이전트(격리 fixture DB 의 실제 분석) → messages.render 를 확인한다.
- DB: spending_fixture.settings 로 고정, 시작 때 로컬 fixture DB 인지 · fixture 회원이 있는지 검사 (서비스 DB 면 멈춤)
- 대화 상태: 페르소나마다 MemoryStateStore (서버를 다시 켜면 사라진다)
- 예산 · 정책: 대역(아래 BudgetDouble · PolicyDouble). 실제 조정 · 승인 · 저장 · 정책 검색 없음
- LLM: 기본은 가짜 해석(정해 둔 문장만). 실제 호출은 LOCAL_CHAT_LIVE=1 + --live + --max-calls + --ledger(저장소 밖) 일 때만,
  평가 도구의 역할별 호출 어댑터 · 장부를 그대로 쓴다. 재시도 없음. 키 · 요청 · 응답 원문은 화면에 보내지 않는다

실행 (backend/ 에서):  python -m spending_fixture.local_chat  →  http://127.0.0.1:8765
"""
import argparse
import json
import os
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["DJANGO_SETTINGS_MODULE"] = "spending_fixture.settings"  # 다른 설정이 들어와도 fixture 설정으로만
import django  # noqa: E402

django.setup()

from chat.interpretation.legacy import SAID, RoleFakeLLM  # noqa: E402
from chat.router_core import CANCEL_WORDS, MemoryStateStore, messages, run_turn  # noqa: E402
from spending_analysis import agent as spending_agent  # noqa: E402
from spending_analysis import reading as spending_reading  # noqa: E402
from users.models import User  # noqa: E402

from . import seed  # noqa: E402

HERE = Path(__file__).resolve().parent
ROUTER_EVAL = HERE.parents[1] / "scripts" / "router_eval"
HOST, PORT = "127.0.0.1", 8765
DESIGN = seed.load("design.json")
AS_OF = seed.AS_OF
TODAY = AS_OF.date()
PERSONAS = {p: {"alias": v["alias"], "job": v["job"]} for p, v in DESIGN["personas"].items()}
# 가짜 해석에 더 넣는 문장 (기존 대역 SAID 에 없는 정책 요청 · 예시용)
EXTRA_SAID = {
    "청년 정책 추천해줘": {"intents": ["policy"]},
    "소비 분석해줘": {"intents": ["spending"], "target": "overview"},
    "왜 식비가 늘었어?": {"intents": ["spending"], "explain": True, "categories": ["FOOD"], "refers_to": "previous"},
    "이번 달 3개월 식비": {"intents": ["spending"], "period": "this_month", "months": 3, "categories": ["FOOD"]},
}
# 소비 형식으로만 답하는 가짜 해석 (통합 형식에 없는 칸 — 세부 지출 · 이번 달 포함 · 별칭)
SPENDING_SAID = {
    "커피 얼마나 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "커피", "category": "FOOD"}]},
    "이번 달 포함 최근 3개월 식비 어때?": {"mine": True, "target": "overview", "period": "recent", "months": 3,
                                 "include_current": True, "categories": ["FOOD"]},
    "지난달 대비 최근 3개월로 비교해줘": {"mine": True, "target": "spend", "compare": "prev_month", "period": "recent",
                                "months": 3},
    "데이트비는 식비로 볼게": {"mine": True, "alias": {"word": "데이트비", "category": "FOOD"}},
}
EXAMPLES = ["이번 달 얼마 썼어?", "식비 얼마 썼어?", "그럼 교통비는?", "식비 예산 얼마 남았어?", "지난달보다 식비 더 썼어?",
            "최근 6개월 식비 어때?", "커피값 얼마 썼어?", "최근 0개월 식비 알려줘", "왜 늘었어?", "왜 식비가 늘었어?",
            "이번 달 3개월 식비", "소비 분석해줘", "식비 예산 줄여줘",
            "그렇게 해줘", "취소", *SPENDING_SAID, "청년 정책 추천해줘", "날씨 어때?", "지난달 얼마 썼어?"]


def guard():
    """서비스 DB 에 연결되지 않았는지 · fixture 가 적재됐는지 확인. 아니면 멈춘다"""
    from django.conf import settings
    if settings.SETTINGS_MODULE != "spending_fixture.settings":
        raise SystemExit("spending_fixture.settings 로만 실행한다")
    seed.guard()  # 로컬 호스트 + money_cushion_router_fixture(또는 그 자동 테스트 DB)만
    ids = seed.user_ids(DESIGN)
    if User.objects.filter(pk__in=ids.values()).count() != len(ids):
        raise SystemExit("fixture 회원이 없어요. 먼저: python -m spending_fixture.seed --apply")


# ---------- 예산 · 정책 대역 ----------

class BudgetDouble:
    """[대역] 예산 조정 에이전트 + 읽기 전용 서버 상태. 조정안 · 승인 · 저장을 실제로 하지 않는다.
    규칙: 시작 → 작업 T1… 승인 대기 / '그렇게 해줘' → 서버 상태 저장 중(끝내지 않음) / 취소 요청 → 서버 상태 취소됨 /
    그 밖의 문장은 다른 분야(not_mine)로 돌려보내 라우터가 다시 분류하게 한다"""
    RULES = "시작하면 승인 대기 작업을 만들고, '그렇게 해줘'는 저장 중으로, '취소'는 취소됨으로 서버 상태만 바꿔요."

    def __init__(self):
        self.states, self.count, self.last = {}, 0, None

    def task_status(self, task_ref):
        return {"task_ref": task_ref, "checked_at": AS_OF.isoformat(), **self.states[task_ref]}

    def __call__(self, request):
        self.last = request  # 받은 공통 입력 (확인용 — 소비 공유 요약이 넘어왔는지)
        mode, text, ref = request["mode"], request["text"], request.get("work_ref")
        said = (text or "").strip(" .!~")
        if mode == "start":
            self.count += 1
            ref = f"T{self.count}"
            self.states[ref] = {"state": "awaiting_approval"}
            return self._reply("awaiting_input", ref, "예산 조정 요청을 받았어요. (조정안 없음 · 서버 상태: 승인 대기)")
        if mode == "cancel" or said in CANCEL_WORDS:
            if ref:
                self.states[ref] = {"state": "canceled"}
            return self._reply("ended", ref, "취소 요청을 받았어요. (서버 상태: 취소됨)")
        if mode == "resume" or text is None:
            return self._reply("awaiting_input", ref, "진행 중인 예산 조정을 받았어요.")
        if said == "그렇게 해줘" and ref:
            self.states[ref] = {"state": "saving"}
            return self._reply("awaiting_input", ref, "승인 요청을 받았어요. (서버 상태: 저장 중 — 대역은 저장을 끝내지 않아요)")
        return {"status": "not_mine"}

    @staticmethod
    def _reply(status, ref, line):
        return {"status": status, "work_ref": ref, "reply": {"lines": [f"[대역] {line}"], "notices": []}}


class PolicyDouble:
    """[대역] 청년 정책 추천 에이전트. 추천 · 검색 없음. '취소'의 뜻은 미정이라 아무것도 바꾸지 않는다"""
    RULES = "시작하면 정책 작업 P1…을 만들고, '취소'는 받기만 해요(뜻 미정). 그 밖의 문장은 다른 분야로 돌려보내요."

    def __init__(self):
        self.count = 0

    def __call__(self, request):
        mode, text, ref = request["mode"], request["text"], request.get("work_ref")
        if mode == "start":
            self.count += 1
            return self._reply(f"P{self.count}", "청년 정책 추천 에이전트는 아직 없어요. 요청을 받기만 했어요.")
        if (text or "").strip(" .!~") in CANCEL_WORDS:
            return self._reply(ref, "취소 요청을 받았지만 뜻(질문만 / 추천 종료)이 정해지지 않아 아무것도 바꾸지 않았어요.")
        if text is None:
            return self._reply(ref, "진행 중인 청년 정책 추천을 받았어요.")
        return {"status": "not_mine"}

    @staticmethod
    def _reply(ref, line):
        return {"status": "awaiting_input", "work_ref": ref, "reply": {"lines": [f"[대역] {line}"], "notices": []}}


# ---------- LLM ----------

class FakeReading(RoleFakeLLM):
    """가짜 해석: 정해 둔 문장만 이해한다(SAID). 모르는 문장은 해석 실패 → 라우터 실패 문구. 설명 LLM 은 없다(템플릿)"""
    explains = False

    def __init__(self):
        super().__init__({**SAID, **EXTRA_SAID})
        self.missed = 0

    def __call__(self, system, user):
        text = json.loads(user)["text"]
        if text in SPENDING_SAID:  # 라우터에는 소비 요청으로, 소비 해석에는 소비 형식 그대로
            self.calls.append({**json.loads(user), "role": "fake"})
            data = SPENDING_SAID[text] if system == spending_reading.PROMPT else {"intents": ["spending"]}
            return json.dumps(data, ensure_ascii=False)
        if text not in self.said:
            self.missed += 1
        return super().__call__(system, user)


class LiveReading:
    """실제 호출: 평가 도구의 ResponsesClient(역할별 Structured Outputs) + 저장소 밖 장부. 재시도 없음
    설명 LLM 도 같은 클라이언트 · 상한 · 장부를 쓴다 ("왜 늘었어?" 한 번에 1회 더)"""
    explains = True

    def __init__(self, max_calls, ledger_path, model=None):
        sys.path.insert(0, str(ROUTER_EVAL))
        import run as eval_run  # noqa: PLC0415  (평가 도구: 장부)
        import stage_b  # noqa: PLC0415  (역할별 스키마 · 호출 어댑터)
        key, model = os.environ.get("OPENAI_API_KEY"), model or os.environ.get("LLM_MODEL")
        if not key or not model:
            raise SystemExit("OPENAI_API_KEY · LLM_MODEL 이 필요해요")
        self.ledger = eval_run.Ledger(ledger_path, max_calls)
        self.client = stage_b.ResponsesClient(model, key, min(max_calls, self.ledger.remaining), schema=stage_b.schema_for,
                                              on_attempt=self.ledger.spend)
        self.model, self.missed = model, 0

    def __call__(self, system, user):
        return self.client.complete(system, user)

    def usage(self):
        return {"used": self.ledger.data["used"], "limit": self.ledger.data["limit"], "blocked": self.client.blocked,
                "failed": sum(1 for a in self.client.attempts if a["kind"] not in (None, "ok"))}


# ---------- 대화 ----------

class Session:
    """한 페르소나의 대화. 상태는 이 객체의 메모리 저장소에만 (서버 재시작 · 새 대화면 사라진다)"""

    def __init__(self, persona, llm, number=1):
        self.persona, self.llm, self.number = persona, llm, number
        self.user = User.objects.get(pk=seed.user_ids(DESIGN)[persona])
        self.store, self.budget, self.policy = MemoryStateStore(), BudgetDouble(), PolicyDouble()
        self.cid, self.log, self.asking, self.turn = f"local-{persona}-{number}", [], None, 0

    def agents(self):
        spending = partial(spending_agent.handle, store=self.store, complete=self.llm, user=self.user,
                           explain_complete=self.llm if self.llm.explains else None)
        return {"spending": spending, "budget": self.budget, "policy": self.policy}

    def send(self, text=None, button=None, choice=None):
        """→ 화면에 그릴 {bot, dev}. choice = 앞 답변의 선택지 {value, label} (의도 선택은 버튼으로, 나머지는 그 이름을 문장으로)"""
        said = None
        if choice is not None and choice.get("turn") != self.turn:
            # 지난 답변의 선택지: 최신 질문의 답으로 쓰지 않는다 (상태 · 호출 변화 없음)
            return {"bot": {"lines": [], "choices": [], "notices": [STALE]}, "dev": ["[화면] 지난 질문의 선택지예요."]}
        if choice is not None:
            if choice["value"] in BUTTONS:  # 기능 선택지(의도 선택 · 불명확 · 범위 밖 답변)는 버튼과 같다
                button = choice["value"]
            else:
                text = choice["label"]
                said = self._choice_reading(choice)
        missed_before = self.llm.missed
        blocked_before = getattr(getattr(self.llm, "client", None), "blocked", 0)
        before = self.store.get_budget_result(self.cid)
        try:
            if said is not None:
                saved, self.llm.said = self.llm.said, {**self.llm.said, text: said}
            turn = run_turn(self.cid, self.store, self.llm, text=text, button=button, agents=self.agents(),
                            server=self.budget, today=TODAY, as_of=AS_OF)
        finally:
            if said is not None:
                self.llm.said = saved
        after = self.store.get_budget_result(self.cid)
        outcome = after["outcome"] if after and after != before else None
        bot = messages.render(turn, outcome) or {"lines": [], "choices": [], "notices": [], "review": []}
        self.asking = self._asking(turn)
        self.turn += 1
        bot["choices"] = [{"value": c["value"], "label": c["label"], "turn": self.turn} for c in bot["choices"]]
        dev = self._dev(turn, bot, missed_before, blocked_before)
        bot.pop("review", None)  # 내부 코드는 화면에 보내지 않는다 — 검토 중 안내(dev)로만
        bot.pop("explain", None)
        self.log += [{"who": "user", "text": text if text is not None else f"[{BUTTONS[button]}]"},
                     {"who": "bot", "bot": bot, "dev": dev}]
        return {"bot": bot, "dev": dev}

    def _choice_reading(self, choice):
        """가짜 해석일 때만: 고른 선택지를 그 질문의 답으로 읽은 결과 (실제 LLM 이면 이름 문장을 그대로 해석)"""
        if not isinstance(self.llm, FakeReading):
            return None
        value = choice["value"]
        if self.asking in ("cancel_target", "budget_task_choice"):
            return {"intents": [], "followup": "answer", "choice": value}
        if self.asking in ("category", "explain_target"):
            return {"intents": [], "followup": "answer", "categories": [value]}
        if self.asking == "budget_current":
            return {"intents": [], "followup": "answer", "confirm": value}
        return None

    @staticmethod
    def _asking(turn):
        d, r = turn["decision"], turn["result"]
        if r is None:
            return d.asking if d.status == "ask" else None
        return (r.get("detail") or {}).get("asking") if r["status"] == "awaiting_input" else None

    def _dev(self, turn, bot, missed_before, blocked_before):
        """개발용 표시 (사용자 문구 아님). 원문 로그 · 키는 넣지 않는다"""
        notes, r = [], turn["result"] or {}
        if self.llm.missed > missed_before:
            notes.append("[가짜 해석] 정해 둔 문장이 아니라 해석하지 못했어요. 예시 문장을 쓰거나 실제 LLM 모드로 켜 주세요.")
        if getattr(getattr(self.llm, "client", None), "blocked", 0) > blocked_before:
            notes.append("[호출 상한] LLM 호출 상한에 도달해 해석하지 않았어요.")
        made = (r.get("reply") or {}).get("explain")
        if made:
            notes.append("[설명] 근거(금액 · 건수 · 평균 결제액 · 가맹점별 증감)는 서버가 계산했어요. " + (
                "설명 LLM 은 쓰지 않고 템플릿 문장으로 답했어요." if not made["llm"] else
                "설명 LLM 호출이 실패하거나 상한에 걸려 템플릿 문장으로 답했어요." if made["failed"] else
                "설명 LLM 문장이 검사(숫자 · 없는 근거 · 금지 표현)에서 걸러져 템플릿 문장으로 답했어요." if made["rejected"] else
                "근거 문장은 설명 LLM 이 쓰고 숫자는 서버 값을 끼웠어요."))
        for code in bot["review"]:
            notes.append(f"[검토 중] {messages.UNDECIDED[code]} — 정책이 정해지지 않은 경로라 정해진 답이 없어요.")
        doubles = [a for a in turn["delivered"] if a in ("budget", "policy")]
        if doubles:
            notes.append("[대역] " + " · ".join(BUTTONS[a] for a in dict.fromkeys(doubles)) + " 은(는) 실제 에이전트가 아닌 대역이 답했어요.")
        return notes


STALE = "이전 질문의 선택지라 처리하지 않았어요. 가장 최근 질문에서 골라 주세요."
BUTTONS = {"spending": "소비 분석하기", "budget": "예산 조정하기", "policy": "청년 정책 추천받기"}


class App:
    def __init__(self, llm):
        self.llm, self.sessions = llm, {}

    def session(self, persona, new=False):
        if persona not in PERSONAS:
            raise KeyError(persona)
        old = self.sessions.get(persona)
        if new or old is None:
            self.sessions[persona] = Session(persona, self.llm, (old.number + 1) if old else 1)
        return self.sessions[persona]

    def info(self):
        live = isinstance(self.llm, LiveReading)
        return {"personas": PERSONAS, "as_of": AS_OF.isoformat(), "today": TODAY.isoformat(),
                "mode": "실제 LLM" if live else "가짜 해석", "model": self.llm.model if live else None,
                "calls": self.llm.usage() if live else {"fake": len(self.llm.calls)},
                "examples": [] if live else EXAMPLES, "buttons": BUTTONS, "explains": self.llm.explains,
                "doubles": {"budget": BudgetDouble.RULES, "policy": PolicyDouble.RULES}}

    def handle(self, path, body):
        if path == "/api/info":
            return self.info()
        session = self.session(body.get("persona"), new=path == "/api/new")
        if path == "/api/new" or path == "/api/history":
            return {"log": session.log, "conversation": session.number}
        if path == "/api/send":
            button = body.get("button")
            if button is not None and button not in BUTTONS:
                raise KeyError(button)
            out = session.send(text=(body.get("text") or "").strip() or None, button=button, choice=body.get("choice"))
            return {**out, "calls": self.info()["calls"]}
        raise KeyError(path)


def handler_for(app):
    page = (HERE / "local_chat.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, data, kind="application/json; charset=utf-8"):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            if self.path == "/api/info":
                return self._send(200, app.info())
            return self._send(404, {"error": "없는 주소"})

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                return self._send(200, app.handle(self.path, body))
            except (KeyError, ValueError, json.JSONDecodeError):
                return self._send(400, {"error": "잘못된 요청"})
            except Exception as error:  # 개발용 서버: 원인 종류만 보여 주고 원문 · 스택은 콘솔에도 내보내지 않는다
                return self._send(500, {"error": f"처리 중 오류 ({type(error).__name__})"})

        def log_message(self, *args):
            pass  # 요청 줄(문장 원문이 들어갈 수 있음)을 콘솔에 남기지 않는다

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="개발용 로컬 채팅 (fixture DB · 메모리 대화 · 예산/정책 대역)")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--live", action="store_true", help="실제 LLM (LOCAL_CHAT_LIVE=1 · --max-calls · --ledger 필요)")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--ledger", help="호출 장부 JSON (저장소 밖). 서버를 다시 켜도 상한이 초기화되지 않는다")
    parser.add_argument("--model")
    args = parser.parse_args(argv)
    guard()
    if args.live:
        if os.environ.get("LOCAL_CHAT_LIVE") != "1" or not args.max_calls or args.max_calls < 1 or not args.ledger:
            raise SystemExit("실제 LLM 은 LOCAL_CHAT_LIVE=1 + --max-calls(1 이상) + --ledger 를 모두 줘야 켜져요")
        llm = LiveReading(args.max_calls, args.ledger, args.model)
    else:
        llm = FakeReading()
    server = HTTPServer((HOST, args.port), handler_for(App(llm)))
    print(f"로컬 채팅: http://{HOST}:{args.port}  (기준일 {TODAY}, {'실제 LLM' if args.live else '가짜 해석'}) — Ctrl+C 로 종료")
    print("대화는 메모리에만 있어서 서버를 다시 켜면 초기화돼요.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
