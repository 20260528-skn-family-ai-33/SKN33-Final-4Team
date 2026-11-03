"""사용자 확인 목록: 격리 DB + 실제 서비스 API, 고정 해석/실제 LLM 결과를 분리한다.
--live는 명시적 호출 예산 승인을 받은 뒤에만 사용. 실패 재시도 없음, 장부 공유.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import patch

from verify_service import ROOT, connection, seed, set_login, PASSWORD, SERVICE, Client, login, override_settings, render
from chat import engine
from chat.llm.fake import FakeReading

COMPARE = "지난달보다 식비 더 썼어?"
TOTAL = "이번 달 얼마 썼어?"

def case(id, question, expected=(), *, persona="A", before=(), rule=None, reject=(), plan=False):
    return dict(id=id, title=question if isinstance(question, str) else question.get("label", str(question)),
                persona=persona, actions=[*before, question], expected=list(expected), rule=rule, reject=list(reject), plan=plan)

CASES = [
    case("0-1", "배달에 얼마나 썼지?", ["배달앱", "0원", "10월 1일~29일"], before=[COMPARE], reject=["전체를 확인", "지난달과 비교"]),
    case("0-2", "점심엔 몇 번 결제했어?", ["점심", "12건", "10월 1일~29일"], before=[COMPARE], reject=["비교는 합계로만"]),
    case("0-3", "지난달 비교해줘", ["42,000원", "2.86%", "9월 1일~29일"]),
    case("0-4", "왜 늘었을까?", ["42,000원"], before=["지난달 비교해줘"], rule="result"),
    case("1-1", TOTAL, ["1,510,000원"]),
    case("1-2", "이번 달 식비 얼마야?", ["390,000원"]),
    case("1-3", "식비 예산 얼마 남았어?", ["290,000원"]),
    case("1-4", "생활비 얼마 남았어?", ["720,000원"]),
    case("1-5", "소비 분석해줘", ["주거·통신"], rule="chart"),
    case("1-6", "지난달보다 많이 썼어?", ["42,000원", "2.86%"]),
    case("1-7", COMPARE, ["42,000원", "12.07%"]),
    case("1-8", "최근 3개월 식비 어때?", ["7월", "330,000원", "8월", "350,000원", "9월", "360,000원"]),
    case("1-9", "최근 6개월 식비 어때?", ["3개월까지", "330,000원", "350,000원", "360,000원"]),
    case("2-1", "주말에 식비 얼마 썼어?", ["105,000원", "주말"]),
    case("2-2", "밤에 쓴 돈 얼마야?", ["120,000원", "밤"]),
    case("2-3", "이번 달 가장 비싸게 결제한 건 뭐야?", ["월세", "600,000원", "고정지출"]),
    case("2-4", "제일 많이 간 가게 다섯 곳", ["과자서원"], rule="merchant_rank"),
    case("2-5", "지난달 식비 얼마 썼어?", ["360,000원", "9월 1일~30일"]),
    case("2-6", "이번 주에 쓴 돈 얼마야?", ["165,000원", "고정지출 700,000원"]),
    case("2-7", "20일부터 27일까지 쓴 돈", ["10월 20일~27일"]),
    case("2-8", "5월 식비 얼마야?", ["5월", "7월"], rule="choices"),
    case("2-9", "10월 5일부터 10일까지 카테고리별로 얼마 썼어?", ["10월 5일~10일"], reject=["10월 1일~29일"]),
    case("3-1", "그럼 교통비는?", ["교통·차량", "90,000원"], before=[TOTAL]),
    case("3-2", "그럼 식비는?", ["주말", "식비", "105,000원"], before=["주말에 쇼핑 얼마나 했어?"]),
    case("3-3", {"pick": "최근 3개월", "label": "기간 선택: 최근 3개월"}, ["최근 3개월 식비", "330,000원"], before=["이번 달 3개월 식비"]),
    case("3-4", {"pick": "이번 달", "from": 0, "status": 409, "label": "이전 기간 버튼 거절"}, before=["이번 달 3개월 식비", TOTAL]),
    case("3-5", {"pick": "식비 전체 보기", "label": "커피 → 식비 전체 동의"}, ["식비 전체", "390,000원"], before=["커피 얼마나 썼어?"]),
    case("3-6", "데이트비 얼마 썼어?", ["식비", "390,000원"], before=["데이트비 얼마 썼어?", {"pick": "식비"}, "데이트비는 식비로 볼게"]),
    case("3-7", "남자친구가 이번 달 쇼핑에 얼마 썼어?", ["다른 사람", "볼 수 없어요"]),
    case("3-8", "엄마 생일 선물로 얼마 썼어?", rule="own_gift", reject=["다른 사람의", "본인 소비만"]),
    case("3-9", "왜 늘었어?", ["42,000원"], before=[COMPARE], rule="result"),
    case("4-1", "패션 쇼핑 몇 건 결제했어?", ["2건", "300,000원"], persona="B"),
    case("4-2", "식비 예산 초과했어?", ["132,000원", "초과"], persona="B"),
    case("4-3", TOTAL, ["일부 내역 기준"], persona="C"),
    case("4-4", "지난달 식비 얼마 썼어?", ["확인할 수 있는 내역이 없어요"], persona="C", reject=["0원"]),
    case("4-5", "생활비 얼마 남았어?", ["승인된 예산이 없어서"], persona="C", reject=["510,000원"]),
    case("4-6", "지난주 식비 얼마야?", ["식비로 분류된 금액 기준"], persona="C"),
    case("5-1", "주거비 예산 얼마 남았어?", ["예산 범위가 아니에요"], rule="choices"),
    case("5-2", "카테고리별로 예산 얼마나 남았어?", ["기타", "50,000원"], reject=["주거·통신", "여행·숙박"]),
    case("5-3", "식비를 5만원 늘리고 싶어요", ["준비 중"], before=[{"choice": "budget"}], rule="budget_form"),
    case("5-4", {"choice": "policy", "label": "청년 정책 버튼"}, ["준비 중"], rule="policy"),
    case("5-5", "식비 예산 5만 원 줄여줘", ["준비 중"], before=[TOTAL], rule="budget"),
    # 사용자가 실제로 적은 다른 표현 + 비교 범위 보존 회귀
    case("R-1", "배달에 얼마 썼지?", ["배달앱", "0원"], before=[COMPARE]),
    case("R-2", "점심엔 몇 건 결제했어?", ["점심", "12건"], before=[COMPARE]),
    case("R-3", "그럼 교통비는?", ["지난달 같은 기간", "90,000원"], before=[COMPARE]),
    case("R-4", "배달에 얼마나 썼지?", ["확인할 수 있는 내역이 없어요"], persona="C", before=["지난달 식비 얼마 썼어?"], reject=["전체를 확인"]),
]


def visible(content):
    # 개발용 추적의 숫자를 답변으로 오인하지 않는다.
    texts = content.get("lead", []) + content.get("lines", []) + content.get("notices", [])
    def walk(value, key=None):
        if isinstance(value, dict):
            for k, v in value.items(): walk(v, k)
        elif isinstance(value, list):
            for v in value: walk(v, key)
        elif isinstance(value, str) and key not in ("kind", "code", "ratio", "direction"):
            texts.append(value)
        elif key == "amount" and isinstance(value, int): texts.append(f"{value:,}원")
    for key in ("sections", "view", "chart"): walk(content.get(key))
    return "\n".join(texts)


def checks_for(case, turns):
    last = turns[-1]
    c = last.get("content", {})
    text = visible(c)
    checks = {"응답 상태": last["status"] == (case["actions"][-1].get("status", 200) if isinstance(case["actions"][-1], dict) else 200)}
    checks.update({f"포함: {x}": x in text for x in case["expected"]})
    checks.update({f"금지: {x}": x not in text for x in case["reject"]})
    if last["status"] == 200:
        checks["호출/형식 실패 없음"] = not last["route"].get("fallback")
    rule = case["rule"]
    if rule == "result": checks["분석 결과"] = c.get("result") is True
    if rule == "choices": checks["선택지"] = bool(c.get("choices"))
    if rule == "chart":
        checks["그래프"] = bool(c.get("chart"))
        checks["주거·통신 최다"] = bool(c.get("chart", {}).get("rows")) and c["chart"]["rows"][0]["code"] == "HOUSING_TELECOM"
    if rule == "merchant_rank":
        groups = [b["rows"] for b in c.get("view", []) if b.get("kind") == "rows"]
        ranked = groups[0] if groups else []
        counts = [int(r["value"].removesuffix("건")) for r in ranked if r.get("value", "").endswith("건")]
        checks["건수순 첫 가게"] = bool(ranked) and ranked[0]["name"] == "과자서원" and counts == sorted(counts, reverse=True)
        checks["다섯 곳"] = len(ranked) == len(counts) == 5
    if rule == "own_gift": checks["분류 질문 또는 내 조회"] = bool(c.get("choices") or c.get("result"))
    if rule == "budget_form":
        checks["예산 폼"] = turns[0]["content"].get("inputs") == ["budget_form"]
    if rule in ("budget", "budget_form", "policy"):
        checks["담당 전환"] = c.get("agent") == ("policy" if rule == "policy" else "budget")
    checks["모든 중간 응답 성공"] = all(t["status"] == 200 and not t.get("route", {}).get("fallback") for t in turns[:-1])
    return checks


def run(live=None, cases=None):
    design = seed.load("design.json")
    seed.apply(design)
    set_login.set_passwords(PASSWORD, design)
    rows = []
    with ExitStack() as stack:
        stack.enter_context(SERVICE)
        stack.enter_context(override_settings(ALLOWED_HOSTS=["testserver"]))
        stack.enter_context(patch.object(engine, "reading", return_value=live or FakeReading()))
        if not live:
            stack.enter_context(patch("urllib.request.urlopen", side_effect=AssertionError("외부 호출 금지")))
        for case in cases or CASES:
            client = Client(enforce_csrf_checks=True)
            assert login(client, design["personas"][case["persona"]]["synthetic_email"]).status_code == 200
            cid, turns = None, []
            with override_settings(CHAT_EXPLAIN_PLAN=case["plan"]):
                for action in case["actions"]:
                    try:
                        if isinstance(action, str):
                            body, question = {"text": action}, action
                        elif "choice" in action:
                            body, question = {"choice": action["choice"]}, action.get("label", action["choice"])
                        else:
                            source = turns[action.get("from", -1)]["content"]
                            value = next(c["value"] for c in source["choices"] if c["label"] == action["pick"])
                            body = {"choice": {"value": value, "turn": source["turn"]}}
                            question = "[" + action["pick"] + "]"
                        response = client.post("/api/chat/messages/", json.dumps({"conversationId": cid, **body}), content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["mc_fixture_csrftoken"].value)
                        data = response.json()
                        c = data.get("messages", [{}, {}])[1].get("content", {})
                        cid = data.get("conversationId", cid)
                        turns.append({"question": question, "status": response.status_code, "content": c, "route": data.get("route", {}), "lines": c.get("lead", []) + c.get("lines", []) + c.get("notices", []), "trace": c.get("devTrace", {}), "error": data.get("error")})
                    except (KeyError, StopIteration) as error:
                        turns.append({"question": str(action), "status": 0, "content": {}, "route": {}, "lines": ["필요한 선택지가 없어 진행하지 못함"], "trace": {}, "error": type(error).__name__})
                        break
            checks = checks_for(case, turns)
            row = {**case, "title": case["id"] + " · " + case["title"], "checks": checks, "pass": all(checks.values()), "turns": turns}
            rows.append(row)
            print(case["id"], "PASS" if row["pass"] else "FAIL", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--ledger")
    parser.add_argument("--ids", nargs="+", help="재검증할 사례 번호(호출 장부는 기존 것 유지)")
    args = parser.parse_args()
    out = Path(args.output_dir).expanduser().resolve()
    if out == ROOT or ROOT in out.parents: raise SystemExit("보고서는 저장소 밖에 저장해 주세요")
    selected = [c for c in CASES if not args.ids or c["id"] in args.ids]
    if not selected or (args.ids and set(args.ids) - {c["id"] for c in selected}):
        raise SystemExit("알 수 없는 사례 번호")
    live = None
    if args.live:
        if not args.max_calls or not args.ledger: raise SystemExit("승인받은 --max-calls와 --ledger가 필요해요")
        from chat.llm.live import LiveReading
        live = LiveReading(args.max_calls, args.ledger)
        # 목록의 'B 꺼짐' 조건: 해석은 실제 LLM, 설명은 서버 템플릿만.
        live.explains = False
    old = connection.creation.create_test_db(verbosity=0, autoclobber=True, keepdb=False)
    try: rows = run(live, selected)
    finally: connection.creation.destroy_test_db(old, verbosity=0)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    page = render(rows)
    if live:
        page = page.replace("가짜 LLM + 실제 서비스 조회·계산·검사·대화 상태를 사용했습니다. 실제 LLM의 자연어 이해 품질 검증은 별도입니다.", "실제 LLM 해석 + 실제 서비스 조회·계산·검사·대화 상태를 사용했습니다. B는 확인 목록의 조건대로 껐습니다.")
        (out / "usage.json").write_text(json.dumps(live.usage(), indent=2))
    (out / "report.html").write_text(page)
    print(json.dumps({"passed": sum(r["pass"] for r in rows), "total": len(rows), "usage": live.usage() if live else None, "report": str(out / "report.html")}, ensure_ascii=False))
    for row in rows:
        if not row["pass"]:
            print(row["id"], {k:v for k,v in row["checks"].items() if not v}, row["turns"][-1]["lines"])
    return int(any(not r["pass"] for r in rows))

if __name__ == "__main__": raise SystemExit(main())
