"""답변 조합 + 보충 · B 보충(11/10) 확인: 격리 DB + 실제 서비스 채팅 API. 보고서(HTML/JSON)는 저장소 밖에.

외부 호출 없는 확인(배선만, 가짜 해석이 아는 문장만):
  .venv/bin/python scripts/router_eval/verify_narrate.py --output-dir /tmp/narrate-offline
실제 LLM(호출 상한 · 장부 필수, 재시도 없음):
  .venv/bin/python scripts/router_eval/verify_narrate.py --live --max-calls 60 --ledger /tmp/narrate-ledger.json --output-dir /tmp/narrate-live
  실패 사례만 다시: --ids N2 B1

자동 채점은 '서버 검사를 통과했는가'까지만 본다. 보충 문장이 질문에 맞는지 · 자연스러운지는 보고서를 사람이 읽고 판단한다.
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
from spending_analysis import narrate

COMPARE = "지난달보다 식비 더 썼어?"


def case(id, persona, *actions, path="narrate", note="", expect=()):
    """expect = 마지막 답(화면 내용 전체)에 꼭 들어 있어야 하는 글자들"""
    return dict(id=id, persona=persona, actions=list(actions), path=path, note=note, expect=list(expect))


# 기대 동작은 실행 전에 고정한다. 각 사례는 새 대화. path = 마지막 질문이 거칠 길 (narrate = 지난달 비교 조합, plan = B)
CASES = [
    case("N1", "A", COMPARE, note="기본: 증감 문장을 꼭 넣는다. 보충은 없어도 된다"),
    case("N2", "A", "지난달보다 많이 썼어?", note="전체 비교"),
    case("N3", "A", "지난달보다 식비 더 썼어? 이 정도면 많이 쓴 거야?", note="'많이 쓴 거야?'는 서버 문장이 다루지 못한다 → 판정 없이 보충(예산과 비교 안내 등)"),
    case("N4", "A", "지난달이랑 비교해서 식비 줄었어?", note="실제로는 늘었다 — 조합이 '줄었다'고 하지 않는지(숫자 · 방향은 서버 문장)"),
    case("N5", "A", "지난달보다 식비 얼마나 더 썼어? 지난달엔 얼마였어?", note="지난달 금액 문장(last_month)을 고르는지"),
    case("N6", "A", "지난달보다 식비랑 교통비 더 썼어?", note="두 카테고리 — 증감 문장 둘 다 must"),
    case("N7", "B", COMPARE, note="민재: 크게 늘어난 경우"),
    case("N8", "C", "지난달보다 많이 썼어?", path="skip", note="지우: 비교 불가 → 카드 맨 위 안내로 충분, 조합을 부르지 않는다"),
    # 달끼리 비교 (11/10) — 기준일 10/29: 8월과는 같은 날짜까지(8/1~29), 9월 vs 8월은 한 달 전체
    case("M1", "A", "8월보다 식비 더 썼어?", expect=["8월 같은 기간과 비교", "8월 1일~29일"], note="이번 달 vs 8월 같은 날짜"),
    case("M2", "A", "9월이랑 8월 식비 비교해줘", expect=["식비 · 8월과 비교", "360,000원", "350000"], note="끝난 달끼리 한 달 전체"),
    case("M3", "A", "8월이랑 9월 중에 식비 언제 더 많이 썼어?", expect=["식비 · 8월과 비교"], note="순서를 바꿔 말해도 나중 달이 보는 쪽"),
    case("M4", "A", "지난달이랑 8월 비교해줘", expect=["전체 소비 · 8월과 비교"], note="지난달(9월) vs 8월"),
    case("M5", "A", "10월이랑 9월 식비 비교해줘", expect=["지난달 같은 기간과 비교"], note="이번 달 vs 지난달 = 기존 지난달 비교"),
    case("M6", "A", "3월보다 식비 더 썼어?", path="ask", expect=["3월"], note="볼 수 없는 달 — 다른 달로 바꾸지 않고 기간을 고르게"),
    case("M7", "A", "9월이랑 8월 식비 비교해줘", "그럼 교통비는?", expect=["교통·차량 · 8월과 비교"], note="후속 질문이 두 달을 이어받는지"),
    case("B1", "B", COMPARE, "왜 식비가 늘었어?", path="plan", note="B 일반 설명 + 보충"),
    case("B2", "A", COMPARE, "점심값이 비싸진 거야?", path="plan", note="가격 가설 — 원인 단정 없이"),
    case("B3", "A", COMPARE, "배달 때문이야?", path="plan", note="배달 결제 없음 — 부정 단정 없이"),
]


class OfflineReading(FakeReading):
    """외부 호출 없는 배선 확인용: 가짜 해석 + 조합은 must 문장만 고르는 정해 둔 답"""
    explains = True

    def __call__(self, system, user, timeout=None):
        if system == narrate.PROMPT:
            facts = json.loads(user)["facts"]
            ids = [f["id"] for f in facts if f["must"]]
            return json.dumps({"sentences": [{"text": "{" + i + "}", "facts": [i]} for i in ids],
                               "extra": None, "extra_reason": None})
        return super().__call__(system, user, timeout)


def known_offline(c):
    fake = FakeReading()
    return all(a in fake.said or a in SPENDING_SAID for a in c["actions"])


def checks_for(c, turn):
    trace, content = turn["trace"], turn["content"]
    said = json.dumps(content, ensure_ascii=False)
    found = {f"'{word}' 있음": word in said for word in c.get("expect", [])}
    if c["path"] == "ask":  # 답하지 않고 기간을 고르게 한다
        return {**found, "선택지로 되물음": bool(content.get("choices")) and not content.get("result")}
    if c["path"] == "skip":
        return {"조합 안 부름": "narrate" not in trace and turn["status"] == 200}
    if c["path"] == "narrate":
        t = trace.get("narrate")
        text = (content.get("view") or [{}])[-1]
        # 조합 단계까지 갔는지 먼저 본다 — 해석이 다른 길(설명 등)로 보내면 trace 에 narrate 가 없다
        return {**found, "비교로 해석": t is not None,
                # same_as_card = 검사는 통과했지만 카드와 같은 말이라 붙이지 않음 (정상)
                "조합 통과(서버 검사)": t is not None and (t.get("fallback") == "same_as_card"
                                                   or (t.get("fallback") is None and text.get("kind") == "text")),
                "보충 탈락 없음": not (t or {}).get("extraDropped")}
    return {"B 통과(서버 검사)": trace.get("fallback") is None and bool(trace.get("plan")),
            "보충 탈락 없음": not trace.get("extraDropped")}


def run(live=None, cases=None):
    design = seed.load("design.json")
    seed.apply(design)
    set_login.set_passwords(PASSWORD, design)
    rows = []
    with ExitStack() as stack:
        stack.enter_context(SERVICE)
        stack.enter_context(override_settings(ALLOWED_HOSTS=["testserver"], CHAT_NARRATE=True, CHAT_EXPLAIN_PLAN=True))
        stack.enter_context(patch.object(engine, "reading", return_value=live or OfflineReading()))
        if not live:
            stack.enter_context(patch("urllib.request.urlopen", side_effect=AssertionError("외부 호출 금지")))
        for c in cases:
            client = Client(enforce_csrf_checks=True)
            assert login(client, design["personas"][c["persona"]]["synthetic_email"]).status_code == 200
            cid, turns = None, []
            for question in c["actions"]:
                started = time.monotonic()
                response = client.post("/api/chat/messages/", json.dumps({"conversationId": cid, "text": question}),
                                       content_type="application/json",
                                       HTTP_X_CSRFTOKEN=client.cookies["mc_fixture_csrftoken"].value)
                data = response.json()
                content = data.get("messages", [{}, {}])[1].get("content", {})
                cid = data.get("conversationId", cid)
                turns.append({"question": question, "status": response.status_code, "seconds": round(time.monotonic() - started, 2),
                              "content": content, "trace": content.get("devTrace", {})})
            checks = checks_for(c, turns[-1])
            rows.append({**c, "checks": checks, "pass": all(checks.values()), "turns": turns})
            print(c["id"], "PASS" if rows[-1]["pass"] else "FAIL", turns[-1]["seconds"], "초", flush=True)
    return rows


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
            out.append('<div class="said">' + "<br>".join(esc(l) for l in b["lines"]) + "</div>")
    notes = content.get("lead", []) + content.get("notices", [])
    return '<div class="card">' + "".join(out) + "".join(f"<p class=note>{esc(n)}</p>" for n in notes) + "</div>"


def render(rows, usage):
    esc = html.escape
    parts = []
    for r in rows:
        turns = "".join(f'<div class="q">{esc(t["question"])} <small>({t["seconds"]}초)</small></div>' + _card(t["content"]) +
                        "<details><summary>분석 과정</summary><pre>" + esc(json.dumps(t["trace"], ensure_ascii=False, indent=2)) +
                        "</pre></details>" for t in r["turns"])
        verdict = " / ".join(k + (": 통과" if v else ": 실패") for k, v in r["checks"].items())
        parts.append(f'<section><h2>{"통과" if r["pass"] else "실패"} · {r["id"]} ({r["persona"]})</h2>'
                     f'<p class=note>볼 점: {esc(r["note"])}</p><p>{esc(verdict)}</p>{turns}</section>')
    head = ('<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
            '<title>답변 조합 확인</title><style>body{font:16px/1.7 system-ui,sans-serif;max-width:860px;margin:32px auto;padding:0 16px;'
            'background:#f4f6f8;color:#172b3a}section{background:#fff;border:1px solid #dce3e8;border-radius:14px;padding:20px;margin:18px 0}'
            '.q{background:#e8f0ff;border-radius:8px;padding:10px;margin-top:16px}.card{border:1px solid #dce3e8;border-radius:10px;'
            'padding:12px;margin-top:8px}.said{background:#effaf6;border-left:4px solid #00BFA5;padding:8px 12px;margin-top:8px}'
            '.note{color:#56687a;font-size:14px}pre{white-space:pre-wrap;font-size:12px}h3{font-size:16px;margin:0}</style>')
    summary = f"<h1>답변 조합 · B 보충 확인</h1><p><b>{sum(r['pass'] for r in rows)}/{len(rows)} 서버 검사 통과</b>"
    summary += f" · LLM 호출 {usage}</p>" if usage else " · 외부 호출 없음(배선 확인)</p>"
    summary += "<p class=note>초록 칸 = LLM 이 고른 서버 문장 + 보충. 질문에 맞는지 · 자연스러운지는 사람이 읽고 판단한다.</p>"
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
