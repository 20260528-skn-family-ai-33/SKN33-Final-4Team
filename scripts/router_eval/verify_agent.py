"""소비 분석 추가 판단 1회(MEMO 31) 확인: 격리 DB + 실제 서비스 채팅 API(CHAT_AGENT=on). 보고서(HTML/JSON)는 저장소 밖에.

외부 호출 없는 확인(배선만, 가짜 해석 · 가짜 판단):
  .venv/bin/python scripts/router_eval/verify_agent.py --output-dir /tmp/agent-offline
실제 LLM(호출 상한 · 장부 필수, 재시도 없음):
  .venv/bin/python scripts/router_eval/verify_agent.py --live --max-calls 40 --ledger /tmp/agent-ledger.json --output-dir /tmp/agent-live
  실패 사례만 다시: --ids A1 F2

자동 채점 = 판단을 불렀는지 · 쓴 도구 · 못 확인한 요구를 숨기지 않았는지 · 판단을 안 불러야 할 질문은 안 불렀는지 · 되묻기 · 이어받기 · B.
"""
import argparse
import html
import json
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from verify_service import ROOT, connection, seed, set_login, PASSWORD, SERVICE, Client, login, override_settings
from chat import engine
from chat.llm.fake import FakeReading, SPENDING_SAID

COMPARE = "지난달보다 식비 더 썼어?"


def case(id, persona, *actions, path, tool=None, note="", expect=()):
    """path = 마지막 질문이 거칠 길: judge(판단 1회) · direct(판단 없음) · ask(되묻기) · b(가설 → B).
    tool = 판단이 고를 도구(None = answer). expect = 화면에 꼭 있어야 하는 글자"""
    return dict(id=id, persona=persona, actions=list(actions), path=path, tool=tool, note=note, expect=list(expect))


# 기대 동작은 실행 전에 고정한다. 각 사례는 새 대화
CASES = [
    case("A1", "B", "지난달보다 식비 늘었는데 어느 시간대에서 많이 늘었어?", path="judge", tool="where_changed",
         note="비교 + 나누기 — multi_step 없어도 서버가 나눈다 (저녁 1위)", expect=["저녁"]),
    case("A2", "B", "지난달보다 식비 늘었는데 어느 시간대에서 늘었어?", path="judge", tool="where_changed", note="multi_step → 시간대로 나눔"),
    case("A3", "A", "식비 예산 얼마 남았고 지난달보다 많이 쓰고 있어?", path="judge", tool="budget_lookup", note="비교 + 예산 (예산 주기 문장)",
         expect=["이번 예산 기간은"]),
    case("A4", "A", "지난달보다 어느 요일에 더 썼어?", path="judge", tool="where_changed", note="문서 예시 — 전체 비교 → 요일"),
    case("S1", "A", "이번 달 식비 얼마야?", path="direct"),
    case("S2", "A", COMPARE, path="direct"),
    case("S3", "A", "식비 예산 얼마 남았어?", path="direct"),
    case("S4", "A", "9월이랑 8월 식비 비교해줘", path="direct"),
    case("S5", "A", "이번달 식비를 지난달과 비교", path="direct", expect=["식비 · 지난달 같은 기간과 비교"],
         note="이번 달 + 지난달을 함께 말한 비교 — period=last_month 로 읽으면 기간을 되묻는다 (11/10 화면)"),
    case("W1", "B", "지난달보다 식비 늘었으면 왜 늘었는지도 알려줘", path="direct", note="일반 이유 — 짧은 서버 설명, 판단 · B 없음"),
    case("H1", "B", COMPARE, "배달 때문이야?", path="b", note="가설 → B 만"),
    case("F1", "A", "3월보다 식비 늘었으면 어느 시간대인지도 알려줘", path="ask", expect=["3월"], note="볼 수 없는 달 — 기존 경로가 기간을 먼저 되묻는다"),
    case("F2", "B", "지난달보다 식비 늘었는데 어느 시간대에서 많이 늘었어?", "그럼 교통비는?", path="direct",
         expect=["교통·차량", "지난달 같은 기간과 비교"], note="판단 뒤 후속 질문이 첫 조회(지난달 비교)를 이어받는다"),
]


def known_offline(c):
    fake = FakeReading()
    return all(a in fake.said or a in SPENDING_SAID for a in c["actions"])


def _card(content):
    esc, out = html.escape, []
    for b in content.get("view", []):
        if b.get("kind") == "head":
            out.append(f"<h3>{esc(b['title'])}</h3>")
        elif b.get("kind") == "total":
            out.append(f"<p><b>{esc(b.get('caption') or '')} {b.get('amount', 0):,}원</b> {esc(b.get('note') or '')}</p>")
        elif b.get("kind") == "rows":
            out.append("<ul>" + "".join(f"<li>{esc(r['name'])} {esc(str(r.get('value') or ''))}"
                                        f"{'' if r.get('amount') is None else format(r['amount'], ',') + '원'}</li>"
                                        for r in b["rows"]) + "</ul>")
        elif b.get("kind") == "text":
            out.append('<div class="said">' + "<br>".join(esc(line) for line in b["lines"]) + "</div>")
    notes = content.get("lead", []) + content.get("notices", [])
    return '<div class="card">' + "".join(out) + "".join(f"<p class=note>{esc(n)}</p>" for n in notes) + "</div>"


class Counted:
    """해석기를 감싸 호출 수만 센다 (사례마다 LLM 호출 수). 나머지 속성은 그대로"""

    def __init__(self, inner):
        self.inner, self.n = inner, 0

    def __call__(self, *args, **kwargs):
        self.n += 1
        return self.inner(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def checks_for(c, turn):
    trace, content = turn["trace"].get("agent"), turn["content"]
    said = json.dumps(content, ensure_ascii=False)
    found = {f"'{word}' 있음": word in said for word in c["expect"]}
    if c["path"] == "judge":
        t = trace or {}
        chosen = (t.get("choice") or {}).get("tool")
        return {**found, "판단 1회 이하": turn["calls"] <= 3, "고른 도구": chosen == c["tool"],
                "끝까지 확인": t.get("status") == "tool" and not t.get("unmet")}
    if c["path"] == "ask":
        return {**found, "판단 안 함": trace is None, "선택지로 되물음": bool(content.get("choices")) and not content.get("result")}
    if c["path"] == "b":
        return {**found, "판단 안 함": trace is None, "B 거침": bool(turn["trace"].get("plan") or turn["trace"].get("stop"))}
    return {**found, "판단 안 함": trace is None and turn["status"] == 200}


def run(live=None, cases=None):
    design = seed.load("design.json")
    seed.apply(design)
    set_login.set_passwords(PASSWORD, design)
    rows = []
    with ExitStack() as stack:
        stack.enter_context(SERVICE)
        # 판단 · B(가설만) 둘 다 켠다 — 서비스 설정과 같은 조합
        stack.enter_context(override_settings(ALLOWED_HOSTS=["testserver"], CHAT_AGENT=True, CHAT_EXPLAIN_PLAN=True))
        llm = Counted(live or FakeReading())
        stack.enter_context(patch.object(engine, "reading", return_value=llm))
        if not live:
            stack.enter_context(patch("urllib.request.urlopen", side_effect=AssertionError("외부 호출 금지")))
        for c in cases:
            client = Client(enforce_csrf_checks=True)
            assert login(client, design["personas"][c["persona"]]["synthetic_email"]).status_code == 200
            cid, turns = None, []
            for question in c["actions"]:
                started, before = time.monotonic(), llm.n
                response = client.post("/api/chat/messages/", json.dumps({"conversationId": cid, "text": question}),
                                       content_type="application/json",
                                       HTTP_X_CSRFTOKEN=client.cookies["mc_fixture_csrftoken"].value)
                data = response.json()
                content = data.get("messages", [{}, {}])[1].get("content", {})
                cid = data.get("conversationId", cid)
                turns.append({"question": question, "status": response.status_code, "seconds": round(time.monotonic() - started, 2),
                              "calls": llm.n - before, "content": content, "trace": content.get("devTrace", {})})
            checks = checks_for(c, turns[-1])
            tools = [t for t in [((turns[-1]["trace"].get("agent") or {}).get("choice") or {}).get("tool")] if t]
            rows.append({**c, "checks": checks, "pass": all(checks.values()), "turns": turns, "used_tools": tools})
            print(c["id"], "PASS" if rows[-1]["pass"] else "FAIL", f"{turns[-1]['seconds']}초", f"호출 {turns[-1]['calls']}",
                  " → ".join(tools) or "-", flush=True)
    return rows


def render(rows, usage):
    import html
    esc, parts = html.escape, []
    for r in rows:
        turns = "".join(f'<div class="q">{esc(t["question"])} <small>({t["seconds"]}초 · LLM 호출 {t["calls"]}번)</small></div>'
                        + _card(t["content"]) + "<details><summary>분석 과정</summary><pre>"
                        + esc(json.dumps(t["trace"], ensure_ascii=False, indent=2)) + "</pre></details>" for t in r["turns"])
        verdict = " / ".join(k + (": 통과" if v else ": 실패") for k, v in r["checks"].items())
        tools = " → ".join(r["used_tools"]) or "추가 도구 없음"
        parts.append(f'<section><h2>{"통과" if r["pass"] else "실패"} · {r["id"]} ({r["persona"]})</h2>'
                     f'<p class=note>볼 점: {esc(r["note"])}</p><p>쓴 도구: {esc(tools)}</p><p>{esc(verdict)}</p>{turns}</section>')
    head = ('<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>추가 판단 확인</title><style>body{font:16px/1.7 system-ui,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;'
            'background:#f4f6f8;color:#172b3a}section{background:#fff;border:1px solid #dce3e8;border-radius:14px;padding:20px;margin:18px 0}'
            '.q{background:#e8f0ff;border-radius:8px;padding:10px;margin-top:16px}.card{border:1px solid #dce3e8;border-radius:10px;'
            'padding:12px;margin-top:8px}.said{background:#effaf6;border-left:4px solid #00BFA5;padding:8px 12px;margin-top:8px}'
            '.note{color:#56687a;font-size:14px}pre{white-space:pre-wrap;font-size:12px}h3{font-size:16px;margin:0}</style>')
    summary = f"<h1>추가 판단 확인</h1><p><b>{sum(r['pass'] for r in rows)}/{len(rows)} 통과</b>"
    summary += f" · LLM 호출 {usage}</p>" if usage else " · 외부 호출 없음(배선 확인)</p>"
    return head + summary + "".join(parts) + "</html>"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--ledger")
    parser.add_argument("--ids", nargs="*")
    args = parser.parse_args()
    out = Path(args.output_dir).expanduser().resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("보고서는 저장소 밖 경로에 저장해 주세요")
    selected = [c for c in CASES if not args.ids or c["id"] in args.ids]
    live = None
    if args.live:
        if not args.max_calls or not args.ledger:
            raise SystemExit("승인한 --max-calls 와 --ledger 가 필요해요")
        from chat.llm.live import LiveReading
        live = LiveReading(args.max_calls, args.ledger)
    else:
        skipped = [c["id"] for c in selected if not known_offline(c)]
        selected = [c for c in selected if known_offline(c)]
        if skipped:
            print("가짜 해석이 모르는 문장이라 건너뜀(실제 LLM 에서만):", " ".join(skipped))
    old = connection.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    try:
        rows = run(live, selected)
    finally:
        connection.creation.destroy_test_db(old, verbosity=0)
    usage = live.usage() if live else None
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"usage": usage, "rows": rows}, ensure_ascii=False, indent=2))
    (out / "report.html").write_text(render(rows, usage))
    print(json.dumps({"passed": sum(r["pass"] for r in rows), "total": len(rows), "usage": usage,
                      "report": str(out / "report.html")}, ensure_ascii=False))
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
