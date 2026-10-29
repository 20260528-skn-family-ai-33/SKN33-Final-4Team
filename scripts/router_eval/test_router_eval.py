"""평가 도구 오프라인 검증 — 네트워크 · 실제 키 없이

    cd <저장소> && .venv/bin/python -m unittest discover -s scripts/router_eval -p 'test_*.py' -v

단계 B: 평가 실행(run.main)은 dry-run · replay(통합 형식 원문 → 역할별 형식 변환)만 한다. live 는 계획 확정 전이라 막혀 있다.
호출 어댑터(openai_rest.ResponsesClient)와 장부(run.Ledger)의 동작은 가짜 HTTP 응답으로 직접 확인한다
(분리 전에는 run.main --live 를 거쳐 같은 동작을 확인했다).
"""
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

import run  # django.setup() 포함
from openai_rest import CapReached, ResponseError, ResponsesClient

DATA = run.load()
TURNS = DATA["cases"] + [t for s in DATA["scenarios"] for t in s["turns"]]
GOLD_BY_TEXT = {c["text"]: run.gold(c["expect_llm"]) for c in DATA["cases"] if c.get("expect_llm")}
N_CASES = len(DATA["cases"])
N_SCENARIOS = len(DATA["scenarios"])


def budget_in_progress(state):
    task = state.get("budget_task")
    return bool(task and not task.get("suspended"))


# 턴 시작 때 예산 작업이 진행 중인 단일 사례 = 원문이 예산 에이전트로 가는 구조 변경 항목 (사례 파일에서 계산)
CHANGED_CASES = sorted(c["id"] for c in DATA["cases"] if budget_in_progress(c.get("state", {})))


def quiet_main(argv):
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("네트워크 호출")), \
            mock.patch("sys.stdout", io.StringIO()):
        return run.main(argv)


def dry_run_output(argv):
    out = tempfile.mkdtemp()
    quiet_main([*argv, "--output-dir", out])
    return next(os.path.join(out, f) for f in os.listdir(out) if f.endswith("-results.jsonl"))


def rewrite(saved, changes):
    """저장 결과의 사례별 원문을 바꾼 사본 (replay 로 추출 · 형식 오류 채점을 확인)"""
    path = os.path.join(tempfile.mkdtemp(), "results.jsonl")
    with open(saved, encoding="utf-8") as src, open(path, "w", encoding="utf-8") as dst:
        for line in src:
            row = json.loads(line)
            if row.get("id") in changes:
                row["raw"] = [changes[row["id"]](row["raw"][0])]
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


class DryRunTest(unittest.TestCase):
    def test_dry_run_comparable_items_all_ok(self):
        meta, cases, scenarios = quiet_main(["--split", "all", "--scenarios"])
        self.assertEqual(meta["mode"], "dry-run")
        self.assertEqual(meta["single"]["structure_changed"], CHANGED_CASES)
        self.assertTrue(all(c["kind"] == "ok" for c in cases if c["comparable"]))  # 사례 정답 ↔ 새 구조 정합성
        comparable = [t for s in scenarios for t in s["turns"] if t["comparable"]]
        self.assertTrue(comparable and all(t["kind"] == "ok" for t in comparable))
        self.assertEqual(meta["scenarios"]["count"], N_SCENARIOS)

    def test_each_role_called_at_most_once_per_turn(self):
        _, cases, scenarios = quiet_main(["--split", "all", "--scenarios"])
        for turn in cases + [t for s in scenarios for t in s["turns"]]:
            with self.subTest(turn["text"]):
                self.assertEqual(len(turn["roles"]), len(set(turn["roles"])))  # 같은 역할에 같은 입력을 두 번 보내지 않는다
        picked = next(t for s in scenarios for t in s["turns"] if t["text"] == "문화·여가")
        self.assertEqual(picked["calls"], 0)  # 코드가 바로 처리하는 입력은 호출 0회

    def test_live_is_blocked_until_stage_b_plan(self):
        for argv in (["--live", "--max-calls", "1"], ["--live"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as raised:
                quiet_main(argv)
            self.assertIn("MEMO.md 13번", str(raised.exception))

    def test_output_dir_inside_repo_is_rejected(self):
        with self.assertRaises(SystemExit):
            run.output_dir(str(run.ROOT / "scripts" / "router_eval" / "out"))


class ScoringTest(unittest.TestCase):
    def test_wrong_extraction_is_llm_failure(self):
        def months_copied(raw):
            data = json.loads(raw)
            data.update(period="recent", months=2)  # 이전 조건을 추출값에 복사한 오류
            return json.dumps(data, ensure_ascii=False)

        def category_added(raw):
            data = json.loads(raw)
            data["categories"] = ["FOOD"]  # 말하지 않은 조건을 채운 오류
            return json.dumps(data, ensure_ascii=False)

        saved = rewrite(dry_run_output(["--split", "dev"]), {"d16": months_copied, "d01": category_added})
        _, cases, _ = quiet_main(["--split", "dev", "--replay", saved])
        by_id = {c["id"]: c for c in cases}
        self.assertEqual(by_id["d16"]["kind"], "llm")
        self.assertFalse(by_id["d16"]["llm"]["fields"]["months"]["ok"])
        self.assertEqual(by_id["d01"]["kind"], "llm")
        self.assertFalse(by_id["d01"]["llm"]["fields"]["categories"]["ok"])

    def test_format_error(self):
        saved = rewrite(dry_run_output(["--split", "dev"]), {"d01": lambda raw: "JSON 아님"})
        meta, cases, _ = quiet_main(["--split", "dev", "--replay", saved])
        self.assertEqual(next(c for c in cases if c["id"] == "d01")["kind"], "format")
        self.assertLess(meta["single"]["format_ok_rate"], 1.0)

    def test_route_mismatch_records_candidates_not_verdict(self):
        case = copy.deepcopy(next(c for c in DATA["cases"] if c["id"] == "d02"))
        case["expect_route"]["items"] = [{"type": 99, "category": "FOOD"}]  # 일부러 틀린 기대값
        result = run.run_case(case, lambda turn: GOLD_BY_TEXT[turn["text"]])
        self.assertEqual(result["kind"], "route")
        self.assertFalse(result["diagnosis"]["gold_replay_ok"])
        self.assertIn("기대값", result["diagnosis"]["candidates"])  # 코드 오류로 확정하지 않는다

    def test_structured_schema_covers_interpretation(self):
        schema = run.schema()  # 분리 전 통합 형식 스키마 (역할별 스키마는 13번 계획)
        self.assertEqual(set(schema["required"]), set(run.Interpretation.__dataclass_fields__))
        client = ResponsesClient("gpt-6-luna", "k", 1, schema=schema)
        self.assertEqual(client.body("s", "u")["text"]["format"]["type"], "json_schema")


class PromptLeakTest(unittest.TestCase):
    # 이미 알려진 겹침 (16차 기록) — 새 겹침만 막는다. 단계 B 역할별 지시문에도 같은 기준
    KNOWN = {
        "전체는?",  # all_categories 설명의 "그럼 전체는?" 안 (d18 · s1 3턴) — 8차부터
        "예산은?",  # all_categories 설명의 "전체 예산은?" 안 (d21) — 8차부터
        "응",       # followup 설명의 예 "응" (d24) — 10차부터. 한 글자 답이라 설명에 피하기 어려움
    }

    def test_prompts_contain_no_new_evaluation_sentence(self):
        """평가 문장(개발 · 확인 · 연속 대화)이 지시문 본문에 그대로 들어 있으면 평가가 오염된다"""
        for name, prompt in (("legacy", run.llm.PROMPT), ("router", run.router_reading.PROMPT),
                             ("spending", run.spending_reading.PROMPT)):
            with self.subTest(name):
                leaked = {t["text"] for t in TURNS if t.get("expect_llm") and t["text"] in prompt}
                self.assertEqual(leaked - self.KNOWN, set())


class NewSetTest(unittest.TestCase):
    def test_new_set_split_and_prompt_pin(self):
        meta, cases, scenarios = quiet_main(["--split", "new", "--scenarios", "--prompt-sha", run.hashlib.sha256(
            run.llm.PROMPT.encode("utf-8")).hexdigest()[:8]])
        self.assertTrue(all(c["split"] == "new" for c in cases))
        self.assertTrue(all(s["id"] in {"s4", "s5", "s6", "s7", "s8"} for s in scenarios))
        with self.assertRaises(SystemExit):  # 지시문이 고정값과 다르면 실행하지 않는다
            quiet_main(["--split", "new", "--prompt-sha", "deadbeef"])

    def test_cascade_marking(self):
        scenario = copy.deepcopy(next(s for s in DATA["scenarios"] if s["id"] == "s4"))
        wrong = json.loads(run.gold(scenario["turns"][0]["expect_llm"]))
        wrong["categories"] = ["FOOD"]  # 첫 턴을 틀리게 → 되묻기 없이 진행 → 뒤 턴도 어긋난다

        def source_for(turn):
            if turn is scenario["turns"][0]:
                return json.dumps(wrong, ensure_ascii=False)
            return run.gold(turn["expect_llm"]) if turn.get("expect_llm") else None

        result = run.run_scenario(scenario, source_for)
        self.assertEqual(result["first_error_turn"], 1)
        self.assertFalse(result["turns"][0]["after_first_error"])
        self.assertTrue(any(t.get("after_first_error") for t in result["turns"][1:]))


class ReplayTest(unittest.TestCase):
    def test_replay_rescores_saved_responses_without_network(self):
        saved = dry_run_output(["--split", "dev"])
        dry, _, _ = quiet_main(["--split", "dev"])
        meta, _, _ = quiet_main(["--split", "dev", "--replay", saved])
        self.assertEqual((meta["mode"], meta["single"]["kinds"]), ("replay", dry["single"]["kinds"]))
        with self.assertRaises(SystemExit):  # 저장 응답이 없는 사례(holdout)는 재채점하지 않는다
            quiet_main(["--split", "holdout", "--replay", saved])


# ---------- 호출 어댑터 · 장부 (분리 전 live 경로 테스트와 같은 동작을 직접 확인) ----------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def responses_body(text, usage=True, status="completed", content=None, incomplete=None):
    body = {"status": status, "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": content if content is not None else [{"type": "output_text", "text": text}]}]}
    if usage:
        body["usage"] = {"input_tokens": 2000, "output_tokens": 80}
    if incomplete:
        body["incomplete_details"] = {"reason": incomplete}
    return json.dumps(body).encode("utf-8")


class FakeOpener:
    def __init__(self, fail=None, raw=None):
        self.requests, self.fail, self.raw = [], fail, raw

    def __call__(self, request, timeout):
        self.requests.append({"body": json.loads(request.data), "headers": dict(request.header_items())})
        if self.fail:
            raise self.fail
        return FakeResponse(self.raw or responses_body('{"intents": ["spending"]}'))


def client(opener, max_calls=5, **kw):
    return ResponsesClient("gpt-6-luna", "sk-test-not-real", max_calls, opener=opener, **kw)


class ClientTest(unittest.TestCase):
    def test_request_body_and_no_secret_in_records(self):
        opener = FakeOpener()
        c = client(opener)
        self.assertEqual(c.complete("s", "u"), '{"intents": ["spending"]}')
        body = opener.requests[0]["body"]
        self.assertEqual((body["model"], body["store"], body["max_output_tokens"], body["reasoning"]),
                         ("gpt-6-luna", False, 2000, {"effort": "low"}))
        self.assertNotIn("temperature", body)
        self.assertNotIn("text", body)  # 스키마 없으면 일반 출력
        recorded = json.dumps(c.attempts, ensure_ascii=False)
        self.assertNotIn("sk-test-not-real", recorded)  # 키 · 헤더는 시도 기록에 남기지 않는다
        self.assertNotIn("Authorization", recorded)
        self.assertTrue(run.usage_totals(c.attempts, 0.1, 0.5)["cost_complete"])

    def test_http_failure_is_counted_and_cost_not_zeroed(self):
        opener = FakeOpener(fail=urllib.error.HTTPError("https://example.invalid", 500, "error", None, None))
        c = client(opener, max_calls=2)
        for _ in range(2):
            with self.assertRaises(ResponseError):
                c.complete("s", "u")
        with self.assertRaises(CapReached):  # 재시도 없음, 실패도 상한에 포함
            c.complete("s", "u")
        self.assertEqual((len(opener.requests), c.blocked), (2, 1))
        usage = run.usage_totals(c.attempts, 0.1, 0.5)
        self.assertEqual((usage["unknown_usage_attempts"], usage["cost_complete"]), (2, False))

    def test_call_cap_stops_sending(self):
        opener, c = FakeOpener(), None
        c = client(opener, max_calls=3)
        for _ in range(3):
            c.complete("s", "u")
        for _ in range(4):
            with self.assertRaises(CapReached):
                c.complete("s", "u")
        self.assertEqual((len(opener.requests), c.blocked), (3, 4))

    def test_incomplete_keeps_usage_and_reason(self):
        c = client(FakeOpener(raw=responses_body('{"intents": ["sp', status="incomplete", incomplete="max_output_tokens")))
        with self.assertRaises(ResponseError) as raised:
            c.complete("s", "u")
        self.assertEqual(raised.exception.kind, "incomplete")
        self.assertEqual((c.attempts[0]["detail"], c.attempts[0]["usage"]["output_tokens"]), ("max_output_tokens", 80))
        self.assertEqual(run.usage_totals(c.attempts, None, None)["unknown_usage_attempts"], 0)  # 실패 응답도 사용량 기록
        self.assertIsNone(c.fatal)  # 같은 조건이면 반복될 수 있어도 설정 오류가 아니면 다음 요청은 보낸다

    def test_refusal_and_empty_output(self):
        for content, kind in (([{"type": "refusal", "refusal": "거절"}], "refusal"), ([], "empty_output")):
            with self.subTest(kind), self.assertRaises(ResponseError) as raised:
                client(FakeOpener(raw=responses_body("", content=content))).complete("s", "u")
            self.assertEqual(raised.exception.kind, kind)

    def test_auth_error_stops_remaining_calls(self):
        opener = FakeOpener(fail=urllib.error.HTTPError(
            "https://example.invalid", 401, "unauthorized", None,
            io.BytesIO(b'{"error": {"type": "invalid_request_error", "code": "invalid_api_key"}}')))
        c = client(opener)
        with self.assertRaises(ResponseError):
            c.complete("s", "u")
        with self.assertRaises(CapReached):  # 남은 호출을 쓰지 않는다
            c.complete("s", "u")
        self.assertEqual((len(opener.requests), c.fatal), (1, "auth"))
        self.assertEqual(c.attempts[0]["detail"], {"type": "invalid_request_error", "code": "invalid_api_key", "param": None})

    def test_ledger_limit_survives_rerun(self):
        path = os.path.join(tempfile.mkdtemp(), "ledger.json")
        first = run.Ledger(path, 3)
        for _ in range(3):
            first.spend()
        again = run.Ledger(path, 5)  # 다시 만들어도 처음 상한 · 사용량 그대로
        self.assertEqual((again.data, again.remaining), ({"limit": 3, "used": 3}, 0))
        with self.assertRaises(SystemExit):
            run.Ledger(str(run.ROOT / "ledger.json"), 3)  # 저장소 안 경로 거부

    def test_structured_request_body(self):
        opener = FakeOpener()
        client(opener, schema=run.schema()).complete("s", "u")
        fmt = opener.requests[0]["body"]["text"]["format"]
        self.assertEqual((fmt["type"], fmt["strict"]), ("json_schema", True))


if __name__ == "__main__":
    unittest.main()
