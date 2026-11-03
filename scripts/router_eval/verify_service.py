"""더미 서비스 API 시나리오 → 사람이 읽는 HTML/JSON 보고서. 외부 LLM 호출 없음.
저장소 루트: .venv/bin/python scripts/router_eval/verify_service.py --output-dir /private/tmp/spending-service-check
임시 테스트 DB만 생성·정리한다. 기존 더미 회원·대화·비밀번호를 바꾸지 않는다.
"""
import argparse
import html
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DJANGO_SETTINGS_MODULE"] = "spending_fixture.settings"
import django
django.setup()
from django.db import connection
from django.test import Client, override_settings
from spending_fixture import seed, set_login
from spending_fixture.test_service import SERVICE, PASSWORD, login

COMPARE = "지난달보다 식비 더 썼어?"
# 기대는 실제 실행 전에 고정한다. 각 시나리오는 새 대화다.
CASES = [
    ("지난달 설명과 후속 범위", "A", ["지난달 식비는 왜 많이 나왔어?", "그럼 교통비는?"], "지난달(9월 1일~30일)", None, None),
    ("지난주 설명", "A", ["지난주 식비는 왜 많이 나왔어?"], "10월 19일~25일", None, None),
    ("주말 필터", "A", ["이번 달 주말 식비 왜 늘었어?"], "주말", None, None),
    ("커피 품목 식별 불가", "A", [COMPARE, "커피를 자주 사 먹어서 그래?"], "커피에 해당하는 내역", "insufficient", 0),
    ("점심 평균 비교", "A", [COMPARE, "점심값이 비싸진 거야?"], "점심 건당 평균", "observation", 1),
    ("점심 결제 건수 비교", "A", [COMPARE, "점심을 더 자주 먹어서 그래?"], "점심 결제 건수", "observation", 1),
    ("기존 평균 근거 재사용", "A", [COMPARE, "건당 금액이 올라서야?"], "12,000원에서 15,000원", "observation", 0),
    ("배달 이름 범위 확인", "A", [COMPARE, "배달 때문이야?"], "두 기간 모두 결제가 없었어요", "observation", 1),
    ("밤 시간대 확인", "A", [COMPARE, "밤에 많이 먹어서 그래?"], "밤", "observation", 1),
    ("개업 여부 식별 불가", "A", [COMPARE, "새로 생긴 가게에서 많이 사서 그래?"], "새로 생긴 가게", "insufficient", 0),
    ("불완전 자료에서 중단", "C", ["왜 식비가 늘었어?"], "내역이 다 모이지 않아", None, 0),
]


def run():
    design = seed.load("design.json")
    seed.apply(design)
    set_login.set_passwords(PASSWORD, design)
    rows = []
    with SERVICE, override_settings(CHAT_EXPLAIN_PLAN=True, ALLOWED_HOSTS=["testserver"]), patch("chat.llm.live.LiveReading", side_effect=AssertionError("외부 LLM 금지")):
        for title, persona, questions, fragment, support, count in CASES:
            client = Client(enforce_csrf_checks=True)
            assert login(client, design["personas"][persona]["synthetic_email"]).status_code == 200
            cid, turns = None, []
            for question in questions:
                response = client.post("/api/chat/messages/", json.dumps({"conversationId": cid, "text": question}),
                                       content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["mc_fixture_csrftoken"].value)
                assert response.status_code == 200, response.content
                body = response.json()
                cid = body["conversationId"]
                content = body["messages"][1]["content"]
                turns.append({"question": question, "lines": content.get("lines", []), "trace": content.get("devTrace", {})})
            last = turns[-1]
            trace = last["trace"]
            checks = {"예상 내용": fragment in "\n".join(last["lines"])}
            if support:
                checks["근거 상태"] = trace.get("support", {}).get("status") == support
                checks["B 실패 없음"] = trace.get("fallback") is None
            if count is not None:
                checks["필요한 조회 수"] = len(trace.get("queries", [])) == count
            if title == "점심 평균 비교":
                checks["가격 단정 금지"] = any("상품 가격이 올랐는지는 판단할 수 없어요" in l for l in last["lines"])
            rows.append({"title": title, "persona": persona, "expected": fragment, "checks": checks,
                         "pass": all(checks.values()), "turns": turns})
    return rows


def view_html(content):
    """서버가 화면에 보내는 카드도 보고서에 표시한다(개발 추적과 분리)."""
    esc = html.escape
    def amount(row):
        return esc(str(row.get("value"))) if row.get("value") is not None else f'{row["amount"]:,}원' if row.get("amount") is not None else ""
    pieces = []
    for block in content.get("view", []):
        kind = block.get("kind")
        if kind == "head": pieces.append("<h3>" + esc(block["title"]) + "</h3>")
        elif kind == "total":
            pieces.append("<p>" + esc(block.get("caption", "")) + " <strong>" + amount(block) + "</strong><br>" + esc(block.get("note") or "") + "</p>")
        elif kind == "rows":
            pieces.append("<table>" + "".join("<tr><td>" + esc(r["name"]) + "</td><td>" + amount(r) + "</td><td>" + esc(r.get("note") or "") + "</td></tr>" for r in block["rows"]) + "</table>")
        elif block.get("lines"):
            pieces.append("<p>" + "<br>".join(esc(l) for l in block["lines"]) + "</p>")
    if content.get("chart"):
        pieces.append("<p>" + "<br>".join(esc(r["text"]) for r in content["chart"].get("rows", [])) + "</p>")
    if content.get("choices"):
        pieces.append("<p>선택지: " + " / ".join(esc(c["label"]) for c in content["choices"]) + "</p>")
    return '<div class="cards">' + "".join(pieces) + "</div>" if pieces else ""


def render(rows):
    escape = html.escape
    cards = []
    for row in rows:
        turns = "".join('<div class="question">'+escape(t["question"])+"</div><div class=answer>"+
                        "<br>".join(escape(l) for l in t["lines"])+"</div>"+view_html(t.get("content", {}))+"<details><summary>실제 분석 과정</summary><pre>"+
                        escape(json.dumps(t["trace"], ensure_ascii=False, indent=2))+"</pre></details>" for t in row["turns"])
        cards.append('<section><h2>'+('통과' if row["pass"] else '실패')+' · '+escape(row["title"])+"</h2><p>"+
                     escape(' / '.join(k+(': 통과' if v else ': 실패') for k,v in row["checks"].items()))+"</p>"+turns+"</section>")
    return '''<!doctype html><html lang=ko><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>소비 분석 확인 결과</title><style>body{font:16px/1.7 system-ui,sans-serif;background:#f4f6f8;color:#172b3a;max-width:900px;margin:40px auto;padding:0 20px}h1{font-size:28px}h2{font-size:20px}section{background:white;padding:24px;margin:20px 0;border-radius:16px;border:1px solid #dce3e8}.question{background:#e8f0ff;padding:12px;margin-top:20px;border-radius:8px}.answer{padding:14px}.cards{border:1px solid #dce3e8;padding:16px;border-radius:12px}td{padding:4px 12px 4px 0}h3{font-size:17px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}summary{cursor:pointer;color:#44596a}</style>
<h1>소비 분석 · 서비스 API 확인 결과</h1><p>더미 기준일: 2026-10-29. 가짜 LLM + 실제 서비스 조회·계산·검사·대화 상태를 사용했습니다. 실제 LLM의 자연어 이해 품질 검증은 별도입니다.</p>''' + f'<p><strong>{sum(r["pass"] for r in rows)}/{len(rows)} 시나리오 통과</strong></p>' + ''.join(cards) + '</html>'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    out = Path(args.output_dir).expanduser().resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("보고서는 저장소 밖 경로에 저장해 주세요")
    old = connection.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    try:
        rows = run()
    finally:
        connection.creation.destroy_test_db(old, verbosity=0)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    (out / "report.html").write_text(render(rows))
    print(json.dumps({"passed": sum(r["pass"] for r in rows), "total": len(rows), "report": str(out / "report.html")}, ensure_ascii=False))
    for row in rows:
        if not row["pass"]:
            print(row["title"], row["checks"])
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
