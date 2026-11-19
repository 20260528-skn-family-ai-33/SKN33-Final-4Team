"""에이전트 평가: 평가 파일의 질문마다 정책 에이전트를 돌려 채점한다.

검색만 보는 policy_eval 과 달리 실제 대화(읽기 · 행동 고르기 · 검색 · 자격 · 돌아보기 · 카드)를 끝까지 돌린다.
평가 파일은 두 모양을 읽는다.
- questions 모양 (agent_cases.json): 질문 1턴. 사는 지역은 확인을 마친 상태로 둔다
- cases 모양 (profile_cases.json · history_cases.json): 가입 프로필 + 질문 + 후속 단계. 가입 지역 확인이 나오면 "예"로 잇는다
채점은 두 가지다.
- 코드 채점: 정답 묶음이 첫 화면에 있으면 통과, 정답 없음은 정책을 하나도 보여 주지 않으면 통과
- --judge: LLM 채점(policies/judge.py). 기준 5개 × 1~5점 → 가중치로 0~100점
--mode rules = LLM 없이 규칙 + 실제 임베딩, --mode search = 문장 그대로 검색한 상위 3개만 (읽기 · 자격 · 돌아보기 없음).
라우터 확인은 질문 1턴 파일에서만 한다. 결과는 eval/results/<label>.jsonl · .summary.json
"""
import json
import os
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from chat.llm import openai_rest
from chat.router_core import MemoryStateStore
from policies import agent, evaluate, judge, profiles, search, tools
from policies.eligibility import Profile
from policies.llm import embedder
from policies.models import Policy
from policies.regions import REGIONS

EVAL = Path(__file__).resolve().parents[2] / "eval"
local = threading.local()  # 스레드마다: 지금 질문의 프로필 · 저장된 사는 지역 · 회원 여부 · LLM 호출 수
DEFAULT_JUDGE = "gpt-6.2-sol"
CID = "c"


def _counting(complete):
    """ResponsesClient.complete 를 감싸 역할(스키마 이름)별 호출 수 · 토큰을 센다. 채점자 호출은 세지 않는다"""
    def wrapped(self, system, user, timeout=None):
        name = self.schema(system)[0] if self.schema else "-"
        calls = getattr(local, "calls", None) if name != "policy_judge" else None
        if calls is not None:
            calls[name] += 1
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


def _view(result, store):
    """답 하나 → 채점자에게 보일 모양 (문장 · 카드 · 버튼)과 화면 정책 번호"""
    reply = result.get("reply") or {}
    sections = reply.get("sections", [])
    shown = [s.get("policy_no") for s in sections if s.get("policy_no")]
    if sections and not shown:  # 옛 버전 카드에는 정책 번호 칸이 없다 → 상태의 방금 보인 목록
        shown = list((store.get_agent_state(CID, agent.AGENT) or {}).get("last") or [])
    return {"status": result.get("status"), "lines": reply.get("lines", []),
            "cards": [{"title": s.get("title"), "rows": s.get("lines", [])} for s in sections],
            "buttons": [c.get("label") for c in reply.get("choices", [])], "notices": reply.get("notices", [])}, shown


def _age(birth, today):
    if not birth:
        return None
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


class Command(BaseCommand):
    help = "에이전트 평가 (대화 × N번). 실제 LLM · 임베딩을 부른다 — 먼저 --limit 으로 비용 · 시간을 보고 돌리기"

    def add_arguments(self, parser):
        parser.add_argument("--file", type=Path, nargs="+", default=[EVAL / "agent_cases.json"])
        parser.add_argument("--label", required=True, help="결과 이름 (예: main-agent, main-rules)")
        parser.add_argument("--mode", choices=("agent", "rules", "search"), default="agent",
                            help="rules = LLM 없이 규칙 (기준선), search = 문장 그대로 검색 상위 3개")
        parser.add_argument("--runs", type=int, default=3, help="질문마다 반복 (LLM 은 실행마다 답이 다르다)")
        parser.add_argument("--only", nargs="*", default=[], help="이 id 로 시작하는 질문만 (예: f-tr c-)")
        parser.add_argument("--limit", type=int, help="파일마다 앞에서 N개만")
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--no-route", action="store_true", help="라우터 확인을 건너뛴다")
        parser.add_argument("--rescore", action="store_true",
                            help="실행하지 않고 results/<label>.jsonl 의 기준별 점수로 총점 · 요약만 다시 낸다 (가중치를 바꿨을 때)")
        parser.add_argument("--judge", action="store_true", dest="grade", help="LLM 채점 (JUDGE_MODEL, 없으면 gpt-6.2-sol)")

    def handle(self, *args, file, label, mode, runs, only, limit, workers, no_route, grade, rescore, **options):
        if rescore:
            return self._rescore(label)
        jobs = []
        for path in file:
            jobs += self._load(path, only, limit)
        if mode == "rules":
            settings.CHAT_LLM = "fake"  # 읽기 · 행동 고르기 · 돌아보기 · 다시 검색을 끈다. 임베딩만 실제로 넘긴다
        elif mode == "agent" and getattr(settings, "CHAT_LLM", "fake") != "live":
            raise CommandError("--mode agent 는 CHAT_LLM=live 가 필요해요")
        grader = None
        if grade:
            model = os.environ.get("JUDGE_MODEL") or DEFAULT_JUDGE
            grader = (model, judge.judge_complete(model))  # 묶인 메서드: grader[1].__self__.attempts 로 사용량을 센다
            if grader[1] is None:
                raise CommandError("--judge 는 OPENAI_API_KEY 가 필요해요")
        embed = embedder(live=True)[1]
        route = None if no_route or mode != "agent" else self._router()
        openai_rest.ResponsesClient.complete = _counting(openai_rest.ResponsesClient.complete)
        tools.profile_for = _profile_for(tools.profile_for)
        saved_region, member = profiles.remembered_region, profiles.is_member
        profiles.remembered_region = lambda user: getattr(local, "saved", None) if getattr(local, "case", False) else saved_region(user)
        profiles.is_member = lambda user: True if getattr(local, "case", False) else member(user)

        work = [(job, run) for job in jobs for run in range(1, runs + 1)]
        self.stdout.write(f"{label} · {mode} · 질문 {len(jobs)}개 × {runs}번 = {len(work)}번"
                          + (f" · 채점 {grader[0]}" if grader else ""))
        out = EVAL / "results"
        out.mkdir(exist_ok=True)
        started, rows = time.time(), []
        with ThreadPoolExecutor(workers) as pool, (out / f"{label}.jsonl").open("w", encoding="utf-8") as log:
            for n, row in enumerate(pool.map(lambda item: self._run(*item, mode, embed, route, grader), work), 1):
                rows.append(row)
                log.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                if n % 20 == 0:
                    self.stdout.write(f"  {n}/{len(work)} · {time.time() - started:.0f}초")
        summary = self._summary(rows, label, mode, runs, grader and grader[0])
        if grader:
            usage = [a.get("usage") or {} for a in grader[1].__self__.attempts]
            summary["judge_usage"] = {"calls": len(usage), "input": sum(u.get("input_tokens") or 0 for u in usage),
                                      "output": sum(u.get("output_tokens") or 0 for u in usage)}
        (out / f"{label}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        self._print(summary)

    def _rescore(self, label):
        out = EVAL / "results"
        rows = [json.loads(line) for line in (out / f"{label}.jsonl").open(encoding="utf-8")]
        old = json.loads((out / f"{label}.summary.json").read_text(encoding="utf-8"))
        for row in rows:
            if row.get("judge"):
                row["judge"]["total"] = judge.total(row["judge"])
        with (out / f"{label}.jsonl").open("w", encoding="utf-8") as log:
            log.writelines(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
        summary = {**self._summary(rows, label, old["mode"], old["runs"], old["judge_model"]),
                   **({"judge_usage": old["judge_usage"]} if "judge_usage" in old else {})}
        (out / f"{label}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        self._print(summary)

    # ---------- 평가 파일 ----------

    def _load(self, path, only, limit):
        data = json.loads(path.read_text(encoding="utf-8"))
        source = path.stem
        if "questions" in data:
            try:
                today, questions = evaluate.load(path.read_text(encoding="utf-8"))
            except evaluate.EvalFileError as error:
                raise CommandError("\n".join(error.problems))
            fields = {item["id"]: item.get("field") or "-" for item in data["questions"]}
            questions = [q for q in questions if not only or q.id.startswith(tuple(only))][:limit]
            return [{"type": "question", "source": source, "id": q.id, "field": fields[q.id], "kind": q.kind,
                     "query": q.query, "today": today, "question": q} for q in questions]
        if "cases" in data:
            cases = [c for c in data["cases"] if not only or c["id"].startswith(tuple(only))][:limit]
            answers = self._answers(data)
            return [{"type": "case", "source": source, "id": c["id"], "field": c.get("field") or c.get("category") or "-",
                     "kind": c.get("origin", "-"), "query": c["query"], "today": date.fromisoformat(c["evaluation_date"]),
                     "case": c, "answers": answers.get(c["id"], []), "fixtures": data.get("fixtures", {})} for c in cases]
        raise CommandError(f"{path}: questions 나 cases 가 있는 평가 파일이 아니에요")

    def _answers(self, data):
        """원래 질문(questions.json)에서 온 사례의 정답 묶음. 같은 문장일 때만 쓴다"""
        try:
            _, questions = evaluate.load((EVAL / "questions.json").read_text(encoding="utf-8"))
        except (OSError, evaluate.EvalFileError):
            return {}
        by_id = {q.id: q for q in questions}
        out = {}
        for case in data["cases"]:
            source = case.get("source") or {}
            q = by_id.get(source.get("key")) if source.get("file") == "questions.json" else None
            if q and q.query == case["query"]:
                out[case["id"]] = [sorted(group) for group in q.answers]
        return out

    # ---------- 실행 ----------

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

    def _send(self, store, text, today, embed, mode, region):
        if mode == "search":  # 문장 그대로 검색한 상위 3개 (읽기 · 자격 · 돌아보기 · 대화 없음)
            hits = search.search(text, region, today, embed=embed)[:agent.SHOW] if text else []
            sections = [{"policy_no": h.policy.pk, "title": h.policy.name,
                         "lines": [tools.tidy(h.policy.raw.get("plcySprtCn") or "")[:tools.SUPPORT_CHARS]]} for h in hits]
            return {"status": "replied", "reply": {"lines": [], "sections": sections, "choices": [], "notices": []}}
        return agent.handle({"conversation_id": CID, "text": text, "today": today.isoformat()}, store=store, embed=embed)

    def _turn(self, store, text, today, embed, mode, region, turns, auto=False):
        result = self._send(store, text, today, embed, mode, region)
        view, shown = _view(result, store)
        state = store.get_agent_state(CID, agent.AGENT) or {}
        if result.get("save_profile"):  # 엔진이 저장했다고 본다 (가입 지역 바꾸기)
            local.saved = result["save_profile"].get("region_code") or local.saved
        turns.append({"user": text, "auto": auto, "bot": view, "shown": shown,
                      "search_region": state.get("region_used") if shown else None, "saved": result.get("save_profile")})
        return result, state

    def _confirmed(self, store, text, today, embed, mode, region, turns, auto=False):
        """보낸 뒤 가입 지역 확인이 나오면 "예"로 잇는다 (평가셋 실행 규칙: 예로 계속한 최종 검색을 평가)"""
        _, state = self._turn(store, text, today, embed, mode, region, turns, auto=auto)
        if mode != "search" and (state.get("ask") or {}).get("kind") == "confirm":
            _, state = self._turn(store, agent.YES, today, embed, mode, region, turns, auto=True)
        return state

    def _run(self, job, run, mode, embed, route, grader):
        local.calls, local.case = Counter(), job["type"] == "case"
        begin, error, turns, region = time.time(), None, [], None
        today, store = job["today"], MemoryStateStore()
        try:
            if job["type"] == "question":
                q = job["question"]
                local.profile, region = q.profile, q.region
                store.save_agent_state(CID, agent.AGENT, {"region": region, "confirmed": True})
                self._turn(store, q.query, today, embed, mode, region, turns)
            else:
                region = self._case(job, store, today, embed, mode, turns)
        except Exception as exc:  # 한 질문의 오류로 전체를 멈추지 않는다
            error = f"{type(exc).__name__}: {exc}"[:200]
        seconds = time.time() - begin
        agent_calls = Counter(local.calls)
        intents = route(job["query"]) if route and job["type"] == "question" else None
        row = self._score(job, turns, error)
        row.update(run=run, route=intents, routed=None if intents is None else "policy" in intents,
                   calls=sum(v for k, v in agent_calls.items() if k != "tokens"), tokens=agent_calls["tokens"],
                   seconds=round(seconds, 2), turns=turns)
        if grader and not error:
            row["judge"] = judge.grade(self._judge_view(job, turns, region), grader[1])
        connection.close()  # 스레드마다 연결이 쌓이지 않게
        return row

    def _case(self, job, store, today, embed, mode, turns):
        """가입 프로필 + 질문 + 후속 단계. 가입 지역 확인이 나오면 첫 질문 뒤에 "예"로 잇는다 (평가셋 실행 규칙)"""
        case = job["case"]
        signup = case["signup_profile"]
        birth = date.fromisoformat(signup["birth_date"]) if signup.get("birth_date") else None
        local.profile = Profile(birth_date=birth, gender=signup.get("gender"),
                                job=tools.EMPLOYED if signup.get("monthly_take_home_income") else None)
        local.saved = signup.get("region_code")
        region = case["expected"].get("search_region_code") or local.saved
        setup = case.get("conversation_setup") or {}
        prior = (job["fixtures"].get(setup.get("fixture_id")) or {}).get("prior_messages") or setup.get("prior_messages") or []
        for message in prior:
            if message.get("role") == "user":
                state = self._confirmed(store, message["text"], today, embed, mode, region, turns, auto=True)
        state = self._confirmed(store, case["query"], today, embed, mode, region, turns)
        for step in case.get("follow_up_steps") or [] if mode != "search" else []:
            kind = step["kind"]
            if step.get("new_conversation"):
                store = MemoryStateStore()
            if kind == "conditional_confirmation" and (state.get("ask") or {}).get("kind") != "confirm":
                continue
            if kind == "profile_update":  # 내 정보에서 사는 지역을 바꿔 저장한 것으로 본다 (채팅 밖)
                local.saved = step.get("expected_saved_region_code") or local.saved
                turns.append({"user": f"(채팅 밖) {step['text']}", "auto": True, "bot": None, "shown": [],
                              "search_region": None, "saved": {"region_code": local.saved}})
                continue
            _, state = self._turn(store, step["text"], today, embed, mode, region, turns)
        return region

    # ---------- 채점 ----------

    def _score(self, job, turns, error):
        """코드 채점: 첫 검색 화면에 정답 묶음이 있나 (정답 없음은 아무것도 안 보여 줌)"""
        start = next((i for i, t in enumerate(turns) if t["user"] == job["query"] and not t["auto"]), None)
        screen = []  # 질문 턴과 바로 뒤에 평가기가 이어서 보낸 턴("예")까지가 첫 화면
        for t in turns[start:] if start is not None else []:
            if screen and not t["auto"]:
                break
            screen.append(t)
        main = screen[-1] if screen else {}
        shown = [n for t in screen for n in t["shown"]][:agent.SHOW]
        if job["type"] == "question":
            groups = [set(g) for g in job["question"].answers]
        else:
            groups = [set(g) for g in job["answers"]]
        status = (main.get("bot") or {}).get("status")
        if groups:
            hits = [bool(group & set(shown)) for group in groups]
            passed = any(hits)
        elif job["type"] == "question" or job["case"]["expected"].get("first_semantic_action") != "search":
            hits = []
            passed = None if job["type"] == "case" else (not shown and status in ("replied", "not_mine"))
        else:
            hits, passed = [], None  # 정답을 적지 않은 찾기 사례는 LLM 채점만 본다
        names = dict(Policy.objects.filter(pk__in=shown).values_list("pk", "name"))
        expected_region = (job.get("case") or {}).get("expected", {}).get("search_region_code")
        searched = next((t["search_region"] for t in screen if t["search_region"]), None)
        return {"id": job["id"], "source": job["source"], "field": job["field"], "kind": job["kind"], "query": job["query"],
                "status": status, "error": error, "shown": shown, "names": [names.get(n) for n in shown],
                "passed": passed, "groups_hit": sum(hits), "groups": len(hits),
                "region_ok": None if not expected_region or not searched else searched == expected_region,
                "unknown_policies": sum(1 for n in shown if n not in names)}

    def _judge_view(self, job, turns, region):
        profile = local.profile
        age = _age(profile.birth_date, job["today"]) if profile else None
        if job["type"] == "question":
            q = job["question"]
            groups = [sorted(g) for g in q.answers]
            expected = {"질문 종류": q.kind, "첫 행동": "search (맞는 정책이 없는 질문이면 정책을 보여 주지 않는다)",
                        "정답 정책": self._names(groups) if groups else ("없음" if q.kind == "없음" else "정해 두지 않음")}
        else:
            e = job["case"]["expected"]
            expected = {"첫 행동": e.get("first_semantic_action"),
                        "찾을 지역": REGIONS.get(e.get("search_region_code") or "", {}).get("name") or e.get("search_region_name"),
                        "꼭 할 일": e.get("must_do", []), "하면 안 되는 일": e.get("must_not", []),
                        "후속 단계": [{"입력": s["text"], "기대 행동": s.get("expected_action"), "꼭 할 일": s.get("must_do", [])}
                                  for s in job["case"].get("follow_up_steps") or []]}
            if job["answers"]:
                expected["정답 정책"] = self._names(job["answers"])
        shown = [n for t in turns for n in t["shown"]]
        return {"사용자": {"만 나이": age, "사는 곳": REGIONS.get(region or "", {}).get("name"),
                        "취업": "재직자(월급 정보)" if profile and profile.job == tools.EMPLOYED else "정보 없음",
                        "평가일": job["today"].isoformat()},
                "기대 출력": expected,
                "대화": [{"사용자": t["user"] + (" (평가기가 이어서 보냄)" if t["auto"] else ""), "챗봇": t["bot"]} for t in turns],
                "화면 정책의 DB 원문": judge.facts_for(list(dict.fromkeys(shown)), region, age)}

    def _names(self, groups):
        names = dict(Policy.objects.filter(pk__in=[n for g in groups for n in g]).values_list("pk", "name"))
        return [" / ".join(dict.fromkeys(names.get(n, n) for n in g)) for g in groups]

    def _summary(self, rows, label, mode, runs, judge_model):
        def stats(group):
            by_q = defaultdict(list)
            for r in group:
                by_q[r["id"]].append(r)
            routed = [r["routed"] for r in group if r["routed"] is not None]
            passed = [r["passed"] for r in group if r["passed"] is not None]
            graded = [r["judge"] for r in group if r.get("judge")]
            out = {
                "questions": len(by_q), "turns": len(group),
                "pass": _rate(passed) if passed else None, "pass_n": len(passed),
                "pass_routed": _rate(r["passed"] and r["routed"] for r in group if r["passed"] is not None) if routed else None,
                "routed": _rate(routed) if routed else None,
                "region_ok": _rate(r["region_ok"] for r in group if r["region_ok"] is not None)
                if any(r["region_ok"] is not None for r in group) else None,
                "stable": _rate(len({tuple(r["shown"]) for r in rs}) == 1 for rs in by_q.values()),
                "errors": sum(1 for r in group if r["error"] or r["status"] == "failed"),
                "unknown_policies": sum(r["unknown_policies"] for r in group),
                "calls": round(sum(r["calls"] for r in group) / len(group), 2),
                "tokens": round(sum(r["tokens"] for r in group) / len(group)),
                "seconds": round(sum(r["seconds"] for r in group) / len(group), 2),
            }
            if judge_model:
                out["judged"], out["judge_failed"] = len(graded), sum(1 for r in group if not r.get("judge") and not r["error"])
                if graded:
                    out["score"] = round(sum(g["total"] for g in graded) / len(graded), 1)
                    out["criteria"] = {k: round(sum(g[k] for g in graded) / len(graded), 2)
                                       for k in (*judge.WEIGHTS, *judge.REFERENCE) if all(k in g for g in graded)}
                    out["hard_fail"] = sum(1 for g in graded if g["hard_fail"])
            return out
        return {"label": label, "mode": mode, "runs": runs, "judge_model": judge_model,
                "model": os.environ.get("LLM_MODEL"), "all": stats(rows),
                "sources": {s: stats([r for r in rows if r["source"] == s]) for s in sorted({r["source"] for r in rows})},
                "fields": {f: stats([r for r in rows if r["field"] == f])
                           for f in sorted({r["field"] for r in rows if r["source"] == "agent_cases"})}}

    def _print(self, summary):
        def pct(value):
            return "   -  " if value is None else f"{value * 100:5.1f}%"
        self.stdout.write(f"\n{'묶음':<14}{'질문':>5}{'통과':>8}{'지역':>8}{'라우터':>8}{'점수':>7}{'LLM':>6}{'초':>6}{'오류':>5}")
        for name, s in [*summary["sources"].items(), *summary["fields"].items(), ("전체", summary["all"])]:
            self.stdout.write(f"{name[:12]:<14}{s['questions']:>5}{pct(s['pass']):>8}{pct(s['region_ok']):>8}{pct(s['routed']):>8}"
                              f"{s.get('score', '-'):>7}{s['calls']:>6}{s['seconds']:>6}{s['errors']:>5}")
        self.stdout.write("통과 = 정답이 첫 화면 3칸에 있음(정답 없음은 아무것도 안 보여 줌) · 지역 = 기대한 지역으로 찾음 · "
                          "점수 = LLM 채점 총점(0~100) · LLM · 초 = 질문당 평균 (라우터 · 채점 제외)")


def _rate(values):
    values = list(values)
    return round(sum(bool(v) for v in values) / len(values), 4) if values else None
