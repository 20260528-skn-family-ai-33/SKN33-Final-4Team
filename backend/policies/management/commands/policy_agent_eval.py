"""에이전트 평가: 평가 파일(questions.json 모양 + field)의 질문마다 정책 에이전트를 N번 돌려 채점한다.

검색만 보는 policy_eval 과 달리 실제 대화 1턴(읽기 · 행동 고르기 · 검색 · 자격 · 돌아보기 · 카드)을 끝까지 돌린다.
- 정답 있음: 첫 화면(추천 3칸)에 정답 묶음이 하나라도 있으면 통과
- 정답 없음(kind=없음): 정책을 하나도 보여 주지 않으면 통과 (not_mine 포함)
- 라우터: 같은 문장을 라우터가 정책 담당으로 보내는지 따로 본다 (자유 문장으로 시작한 대화의 입구)
--mode rules 는 LLM 없이 규칙 + 실제 임베딩 (비교 기준선). 결과는 eval/results/<label>.jsonl · .summary.json
"""
import json
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from chat.llm import openai_rest
from chat.router_core import MemoryStateStore
from policies import agent, evaluate, tools
from policies.llm import embedder

EVAL = Path(__file__).resolve().parents[2] / "eval"
local = threading.local()  # 스레드마다: 지금 질문의 프로필 · LLM 호출 수


def _counting(complete):
    """ResponsesClient.complete 를 감싸 역할(스키마 이름)별 호출 수 · 토큰을 센다"""
    def wrapped(self, system, user, timeout=None):
        calls = getattr(local, "calls", None)
        if calls is not None:
            calls[self.schema(system)[0]] += 1
        before = len(self.attempts)
        try:
            return complete(self, system, user, timeout)
        finally:
            usage = (self.attempts[before:] or [{}])[-1].get("usage") or {}
            if calls is not None:
                calls["tokens"] += usage.get("total_tokens") or 0
    return wrapped


def _profile_for(original):
    """회원 없이 평가 파일의 profile 을 쓴다. 문장에서 말한 취업 상태는 그대로 우선한다"""
    def profile_for(user, text=None, job=None):
        said = original(user, text, job)
        given = getattr(local, "profile", None)
        return replace(given, job=said.job or given.job) if given else said
    return profile_for


class Command(BaseCommand):
    help = "에이전트 평가 (대화 1턴 × N번). 실제 LLM · 임베딩을 부른다 — 먼저 --limit 으로 비용 · 시간을 보고 돌리기"

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, default=EVAL / "agent_cases.json")
        parser.add_argument("--label", required=True, help="결과 이름 (예: main-agent, main-rules)")
        parser.add_argument("--mode", choices=("agent", "rules"), default="agent", help="rules = LLM 없이 규칙 (기준선)")
        parser.add_argument("--runs", type=int, default=3, help="질문마다 반복 (LLM 은 실행마다 답이 다르다)")
        parser.add_argument("--only", nargs="*", default=[], help="이 id 로 시작하는 질문만 (예: f-tr c-)")
        parser.add_argument("--limit", type=int, help="앞에서 N개만")
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--no-route", action="store_true", help="라우터 확인을 건너뛴다")

    def handle(self, *args, file, label, mode, runs, only, limit, workers, no_route, **options):
        try:
            today, questions = evaluate.load(file.read_text(encoding="utf-8"))
        except evaluate.EvalFileError as error:
            raise CommandError("\n".join(error.problems))
        fields = {item["id"]: item.get("field") or "-" for item in json.loads(file.read_text(encoding="utf-8"))["questions"]}
        questions = [q for q in questions if not only or q.id.startswith(tuple(only))][:limit]
        if mode == "rules":
            settings.CHAT_LLM = "fake"  # 읽기 · 행동 고르기 · 돌아보기 · 다시 검색을 끈다. 임베딩만 실제로 넘긴다
        elif getattr(settings, "CHAT_LLM", "fake") != "live":
            raise CommandError("--mode agent 는 CHAT_LLM=live 가 필요해요")
        embed = embedder(live=True)[1]
        route = None if no_route or mode == "rules" else self._router()
        openai_rest.ResponsesClient.complete = _counting(openai_rest.ResponsesClient.complete)
        tools.profile_for = _profile_for(tools.profile_for)

        jobs = [(q, run) for q in questions for run in range(1, runs + 1)]
        self.stdout.write(f"{label} · {mode} · 질문 {len(questions)}개 × {runs}번 = {len(jobs)}턴 · 기준일 {today}")
        out = EVAL / "results"
        out.mkdir(exist_ok=True)
        started, rows = time.time(), []
        with ThreadPoolExecutor(workers) as pool, (out / f"{label}.jsonl").open("w", encoding="utf-8") as log:
            for n, row in enumerate(pool.map(lambda job: self._one(*job, fields, today, embed, route), jobs), 1):
                rows.append(row)
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                if n % 20 == 0:
                    self.stdout.write(f"  {n}/{len(jobs)} · {time.time() - started:.0f}초")
        summary = self._summary(rows, label, mode, runs, today)
        (out / f"{label}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        self._print(summary)

    def _router(self):
        from chat import engine
        from chat.router_core import reading as router_reading
        complete = engine.reading()

        def route(text):
            try:
                return list(router_reading.read(text, None, complete, {"active": None}).intents)
            except Exception as error:  # 호출 실패는 '못 넘김'과 구분해 남긴다
                return [f"error:{type(error).__name__}"]
        return route

    def _one(self, question, run, fields, today, embed, route):
        local.profile, local.calls = question.profile, Counter()
        region = question.region
        store = MemoryStateStore()
        store.save_agent_state("c", agent.AGENT, {"region": region, "confirmed": True})
        begin, error, result = time.time(), None, {}
        try:
            result = agent.handle({"conversation_id": "c", "text": question.query, "today": today.isoformat()},
                                  store=store, embed=embed)
        except Exception as exc:  # 한 질문의 오류로 전체를 멈추지 않는다
            error = f"{type(exc).__name__}: {exc}"[:200]
        seconds = time.time() - begin
        agent_calls = Counter(local.calls)
        intents = route(question.query) if route else None
        connection.close()  # 스레드마다 연결이 쌓이지 않게
        reply = result.get("reply") or {}
        shown = [s.get("policy_no") for s in reply.get("sections", [])]
        if question.answers:
            hits = [bool(group & set(shown)) for group in question.answers]
            passed = any(hits)
        else:
            # 정책을 지어 붙이지 않았으면 통과. not_mine(내 지출 얘기로 보고 돌려줌)도 정책을 보여 주지 않았다
            hits, passed = [], not shown and result.get("status") in ("replied", "not_mine")
        return {"id": question.id, "seed": question.id.split(".")[0], "field": fields[question.id], "kind": question.kind,
                "run": run, "query": question.query, "status": result.get("status"), "error": error or result.get("error"),
                "shown": shown, "names": [s.get("title") for s in reply.get("sections", [])],
                "lines": reply.get("lines", [])[:2], "passed": passed, "groups_hit": sum(hits), "groups": len(hits),
                "route": intents, "routed": None if intents is None else "policy" in intents,
                "calls": sum(v for k, v in agent_calls.items() if k != "tokens"), "tokens": agent_calls["tokens"],
                "seconds": round(seconds, 2)}

    def _summary(self, rows, label, mode, runs, today):
        def stats(group):
            by_q = defaultdict(list)
            for r in group:
                by_q[r["id"]].append(r)
            routed = [r["routed"] for r in group if r["routed"] is not None]
            return {
                "questions": len(by_q), "turns": len(group),
                "pass": _rate(r["passed"] for r in group),
                # 라우터까지 통과 (자유 문장으로 시작했을 때 사용자가 보는 결과)
                "pass_routed": _rate(r["passed"] and r["routed"] for r in group) if routed else None,
                "routed": _rate(routed) if routed else None,
                "stable": _rate(len({tuple(r["shown"]) for r in rs}) == 1 for rs in by_q.values()),
                "all_pass": _rate(all(r["passed"] for r in rs) for rs in by_q.values()),
                "errors": sum(1 for r in group if r["error"] or r["status"] == "failed"),
                "calls": round(sum(r["calls"] for r in group) / len(group), 2),
                "tokens": round(sum(r["tokens"] for r in group) / len(group)),
                "seconds": round(sum(r["seconds"] for r in group) / len(group), 2),
            }
        fields = sorted({r["field"] for r in rows})
        return {"label": label, "mode": mode, "runs": runs, "today": today.isoformat(),
                "model": getattr(settings, "LLM_MODEL", None) or __import__("os").environ.get("LLM_MODEL"),
                "all": stats(rows), "fields": {f: stats([r for r in rows if r["field"] == f]) for f in fields}}

    def _print(self, summary):
        def pct(value):
            return "   -  " if value is None else f"{value * 100:5.1f}%"
        self.stdout.write(f"\n{'분야':<6}{'질문':>5}{'통과':>8}{'라우터':>8}{'둘다':>8}{'매번같음':>8}{'매번통과':>8}"
                          f"{'LLM':>6}{'토큰':>7}{'초':>6}{'오류':>5}")
        for name, s in [*summary["fields"].items(), ("전체", summary["all"])]:
            self.stdout.write(f"{name:<6}{s['questions']:>5}{pct(s['pass']):>8}{pct(s['routed']):>8}{pct(s['pass_routed']):>8}"
                              f"{pct(s['stable']):>8}{pct(s['all_pass']):>8}{s['calls']:>6}{s['tokens']:>7}{s['seconds']:>6}"
                              f"{s['errors']:>5}")
        self.stdout.write("통과 = 정답이 첫 화면 3칸에 있음(정답 없음은 아무것도 안 보여 줌) · 라우터 = 정책 담당으로 보냄 · "
                          "매번같음 = 반복 실행에서 보여 준 정책이 모두 같음 · LLM · 토큰 · 초 = 턴당 평균 (라우터 제외)")


def _rate(values):
    values = list(values)
    return round(sum(bool(v) for v in values) / len(values), 4) if values else None
