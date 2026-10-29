"""단계 B 역할별 평가 도구 오프라인 검증 — 네트워크 · 실제 키 없이 (live 경로는 가짜 HTTP 응답으로)

    cd <저장소> && .venv/bin/python -m unittest discover -s scripts/router_eval -p 'test_*.py' -v
"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

import make_cases_b
import stage_b

DATA = stage_b.load()
LIVE_ENV = {"ROUTER_EVAL_LIVE": "1", "OPENAI_API_KEY": "sk-test-not-real", "LLM_MODEL": "gpt-6-luna"}
BY_ID = {**{c["id"]: c for c in DATA["cases"]},
         **{f"{s['id']}#{i}": t for s in DATA["scenarios"] for i, t in enumerate(s["turns"], 1)}}


def quiet(argv, opener=None, env=None):
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("네트워크 호출")), \
            mock.patch("sys.stdout", io.StringIO()), mock.patch.dict(os.environ, env or {}):
        return stage_b.main(argv, opener=opener)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RoleOpener:
    """가짜 Responses API: 지시문으로 역할을 가려 사례의 그 역할 기대값을 돌려준다"""

    def __init__(self, fail_first=0, fail=None):
        self.requests, self.fail_first, self.fail = [], fail_first, fail

    def __call__(self, request, timeout):
        body = json.loads(request.data)
        self.requests.append(body)
        if self.fail or len(self.requests) <= self.fail_first:
            raise self.fail or urllib.error.HTTPError("https://example.invalid", 500, "error", None, None)
        role = stage_b.ROLE_OF[body["instructions"]]
        text = json.loads(body["input"])["text"]
        unit = next(u for u in BY_ID.values() if u["text"] == text and u.get(stage_b.EXPECT[role]))
        out = {"status": "completed", "usage": {"input_tokens": 1500, "output_tokens": 60},
               "output": [{"type": "message", "content": [{"type": "output_text",
                                                            "text": stage_b.gold(unit[stage_b.EXPECT[role]])}]}]}
        return FakeResponse(json.dumps(out).encode("utf-8"))


class DryRunTest(unittest.TestCase):
    def test_dry_run_consistent_and_counts_planned_calls(self):
        meta, cases, flows = quiet(["--split", "all", "--scenarios"])
        self.assertEqual(meta["planned_calls"], meta["made_calls"])  # 사례 파일의 기대 역할 수 = 실제 호출 수
        rows = cases + [t for s in flows for t in s["turns"]]
        self.assertTrue(all(r["kind"] == "ok" for r in rows))
        self.assertTrue(all(s["ok"] for s in flows))
        for row in rows:
            with self.subTest(row["id"]):
                self.assertEqual(sorted(row["roles"]), sorted(row["expected_roles"]))  # 역할마다 한 번, 기대한 역할만

    def test_scores_are_separate_and_exclusions_reported(self):
        meta, _, _ = quiet(["--split", "all", "--scenarios"])
        dev = meta["single"]["regression_dev"]
        self.assertEqual({k: dev[k]["of"] for k in ("router_reading", "spending_reading", "decision", "state")},
                         {"router_reading": 25, "spending_reading": 23, "decision": 27, "state": 27})
        self.assertEqual(dev["policy_pending_excluded"], ["d28"])  # 정책 미정은 분모에서 빼고 따로
        new_b = meta["scenarios"]["new_b"]
        self.assertEqual(new_b["policy_pending_excluded"], ["bs1#3", "bs2#3"])
        self.assertIn("bs2#2", new_b["double_dependent"])  # 정책 대역 결과가 결정에 쓰인 단위
        self.assertEqual(set(meta["independent"]) - {"why"}, {"regression_dev", "regression_new", "reference", "new_b"})
        self.assertFalse(any(v for k, v in meta["independent"].items() if k != "why"))  # 독립 평가 아님

    def test_scenario_state_is_carried_not_replaced(self):
        """첫 턴 소비 해석을 틀리게 하면 그 실제 결과 상태가 다음 턴으로 이어진다 (정답 상태로 바꾸지 않음)"""
        flow = next(s for s in DATA["scenarios"] if s["id"] == "bs1")

        def complete_for(turn):
            if turn is flow["turns"][0]:
                def wrong(system, user):
                    role = stage_b.ROLE_OF[system]
                    return json.dumps({"mine": True, "months": 2, "categories": ["FOOD"], "period": "recent"}) \
                        if role == "spending" else stage_b.gold(turn["expect_router"])
                return wrong
            return stage_b.gold_complete(turn)

        result = stage_b.run_scenario(flow, complete_for)
        self.assertEqual((result["turns"][0]["kind"], result["first_error_turn"]), ("extraction", 1))
        self.assertTrue(result["turns"][1]["after_first_error"])  # 되묻기가 없어서 뒤 턴 상태가 어긋난다

    def test_role_schemas_cover_readings(self):
        for role in stage_b.ROLES:
            schema = stage_b.role_schema(role)
            self.assertEqual(set(schema["required"]), set(stage_b.ROLES[role][1].__dataclass_fields__))
        self.assertEqual(stage_b.role_schema("spending")["properties"]["mine"], {"type": "boolean"})
        name, schema = stage_b.schema_for(stage_b.spending_explain.PROMPT)  # 설명 문장은 채점 역할이 아니라 스키마만
        self.assertEqual((name, schema["required"]), ("spending_explanation", ["sentences"]))
        self.assertNotIn(stage_b.spending_explain.PROMPT, stage_b.ROLE_OF)

    def test_followup_is_scored_only_while_that_role_asks(self):
        """[B-2026-10-29.2] 질문이 없는 역할에 통합 형식의 followup 기대를 옮기지 않는다 (관찰만)"""
        expect = {"must": {"intents": ["spending"], "followup": "new", "target": "spend"}, "allow": {}}
        router = make_cases_b.role_expect(expect, "router", {"text": "x", "pending": None})
        self.assertNotIn("followup", router["must"])
        self.assertIn("followup", router["observe"])
        asked = make_cases_b.role_expect(expect, "router", {"text": "x", "pending": {"asking": "intent"}})
        self.assertEqual(asked["must"]["followup"], "new")
        fresh = make_cases_b.role_expect({"must": {"intents": ["spending"]}, "allow": {}}, "router", {"text": "x", "pending": None})
        self.assertNotIn("observe", fresh)  # 기대에 followup 이 없던 단위는 그대로 (비어 있어야 함)

    def test_chosen_spending_expectation_comes_from_original_sentence(self):
        """라우터 질문에서 소비를 고르면 소비는 원래 문장을 해석한다 → 답 문장이 아니라 원래 문장의 기대값"""
        units = {t["id"]: t for t in DATA["cases"]}
        units.update({f"{s['id']}#{i}": t for s in DATA["scenarios"] for i, t in enumerate(s["turns"], 1)})
        spending = units["s8#2"]["expect_spending"]
        self.assertNotIn("followup", spending["must"])
        self.assertEqual(spending["must"]["mine"], True)
        self.assertEqual({k: units[k].get("policy_review") is not None for k in ("n10", "s8#2", "d22")},
                         {"n10": True, "s8#2": True, "d22": False})

    def test_policy_review_is_listed_but_kept_in_scores(self):
        meta, _, _ = quiet(["--split", "all", "--scenarios"])
        self.assertIn("n10", meta["single"]["regression_new"]["policy_review"])
        self.assertIn("s8#2", meta["scenarios"]["regression_new"]["policy_review"])

    def test_converted_cases_are_reproducible_and_source_kept(self):
        self.assertEqual(make_cases_b.dump(make_cases_b.build()), stage_b.CASES_B.read_text(encoding="utf-8"))
        self.assertEqual(DATA["source"]["sha256"][:8], "cffc9d75")  # 분리 전 사례 파일 그대로


class LivePathTest(unittest.TestCase):
    def ledger(self):
        return os.path.join(tempfile.mkdtemp(), "ledger.json")

    def test_live_is_off_by_default(self):
        for argv, env in ((["--live", "--max-calls", "5", "--ledger", self.ledger()], {"ROUTER_EVAL_LIVE": ""}),
                          (["--live", "--ledger", self.ledger()], LIVE_ENV), (["--live", "--max-calls", "5"], LIVE_ENV)):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                quiet(argv, env=env)

    def test_live_counts_every_call_with_role_schema(self):
        opener, path = RoleOpener(), self.ledger()
        meta, cases, _ = quiet(["--live", "--max-calls", "50", "--ledger", path, "--structured", "--split", "new_b",
                                "--price-in", "0.1", "--price-out", "0.5"], opener=opener, env=LIVE_ENV)
        planned = sum(meta["planned_calls"].values())
        self.assertEqual((len(opener.requests), meta["ledger"]["used"]), (planned, planned))
        self.assertTrue(all(c["kind"] == "ok" for c in cases))
        names = {r["text"]["format"]["name"] for r in opener.requests}
        self.assertEqual(names, {"router_reading", "spending_reading"})  # 지시문마다 다른 스키마
        self.assertTrue(meta["usage"]["cost_complete"])
        self.assertNotIn("sk-test-not-real", json.dumps(meta, ensure_ascii=False))

    def test_failures_count_no_retry_and_cap_holds_across_runs(self):
        opener, path = RoleOpener(fail_first=2), self.ledger()
        meta, cases, _ = quiet(["--live", "--max-calls", "3", "--ledger", path, "--split", "new_b"], opener=opener,
                               env=LIVE_ENV)
        self.assertEqual(len(opener.requests), 3)  # 실패 2 + 성공 1, 재시도 없음 · 실패도 상한에 포함
        self.assertEqual((meta["ledger"]["limit"], meta["ledger"]["used"]), (3, 3))
        self.assertEqual([c["kind"] for c in cases[:2]], ["call_failed", "call_failed"])
        self.assertIn("cap_skipped", [c["kind"] for c in cases])
        again, _, _ = quiet(["--live", "--max-calls", "9", "--ledger", path, "--split", "regression_dev"], opener=opener,
                            env=LIVE_ENV)
        self.assertEqual((len(opener.requests), again["ledger"]["limit"], again["ledger"]["used"]), (3, 3, 3))  # 초기화 안 됨

    def test_checked_unit_is_not_run_again_on_same_ledger(self):
        """점검 단위(b05)를 본 실행 목록에 다시 넣으면 호출 전에 멈춘다. 장부에는 실행한 단위가 남는다"""
        opener, path = RoleOpener(), self.ledger()
        quiet(["--live", "--max-calls", "50", "--ledger", path, "--split", "new_b", "--cases", "b05"], opener=opener,
              env=LIVE_ENV)
        sent = len(opener.requests)
        with self.assertRaises(SystemExit):
            quiet(["--live", "--max-calls", "50", "--ledger", path, "--split", "new_b", "--cases", "b04,b05,b06"],
                  opener=opener, env=LIVE_ENV)
        self.assertEqual(len(opener.requests), sent)  # 겹친 b04 · b06 도 보내지 않음
        meta, cases, _ = quiet(["--live", "--max-calls", "50", "--ledger", path, "--split", "new_b", "--cases", "b04,b06"],
                               opener=opener, env=LIVE_ENV)
        self.assertEqual([c["id"] for c in cases], ["b04", "b06"])
        self.assertEqual(meta["ledger"]["units"], ["b05", "b04", "b06"])

    def test_unknown_or_badly_split_case_ids_stop(self):
        """쉼표 목록이 아닌 값(줄 하나에 붙은 목록 등) · 고른 묶음에 없는 ID 는 조용히 빠지지 않고 멈춘다"""
        for ids in ("b05 b06", "b05,zz9", "s1"):
            with self.subTest(ids=ids), self.assertRaises(SystemExit):
                quiet(["--split", "new_b", "--cases", ids])

    def test_auth_error_stops_remaining_calls(self):
        opener = RoleOpener(fail=urllib.error.HTTPError("https://example.invalid", 401, "unauthorized", None,
                                                        io.BytesIO(b'{"error": {"type": "invalid_request_error"}}')))
        meta, cases, _ = quiet(["--live", "--max-calls", "20", "--ledger", self.ledger(), "--split", "new_b"],
                               opener=opener, env=LIVE_ENV)
        self.assertEqual((len(opener.requests), meta["fatal_error"]), (1, "auth"))
        self.assertEqual(cases[1]["kind"], "aborted")


class ReplayTest(unittest.TestCase):
    def test_replay_of_dry_run_output(self):
        out = tempfile.mkdtemp()
        dry, _, _ = quiet(["--split", "regression_new", "--scenarios", "--output-dir", out])
        saved = next(os.path.join(out, f) for f in os.listdir(out) if f.endswith("-b-results.jsonl"))
        meta, _, _ = quiet(["--split", "regression_new", "--scenarios", "--replay", saved])
        self.assertEqual((meta["mode"], meta["single"], meta["scenarios"]), ("replay", dry["single"], dry["scenarios"]))


if __name__ == "__main__":
    unittest.main()
