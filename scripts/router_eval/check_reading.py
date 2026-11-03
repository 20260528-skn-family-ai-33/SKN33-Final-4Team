"""대상·지표 해석의 고정 평가. 실제 호출은 --live + 명시적 상한/장부/출력 경로 + ROUTER_EVAL_LIVE=1 필수."""
import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DJANGO_SETTINGS_MODULE"] = "spending_fixture.settings"
import django
django.setup()
from spending_analysis import reading, investigation as I
from chat.llm.live import LiveReading

PREVIOUS = {"target": "spend", "compare": "prev_month", "categories": ["FOOD"]}
CASES = [
    ("r01", "지난달 식비는 왜 많이 나왔어?", None, {"period": "last_month", "categories": ["FOOD"], "explain": True, "hypothesis": None}),
    ("r02", "지난주 식비는 왜 많이 나왔어?", None, {"period": "last_week", "categories": ["FOOD"], "explain": True, "hypothesis": None}),
    ("r03", "커피를 자주 사 먹어서 그래?", PREVIOUS, {"categories": [], "explain": True, "hypothesis.target": "unavailable", "hypothesis.metric": "count"}),
    ("r04", "점심값이 비싸진 거야?", PREVIOUS, {"categories": [], "explain": True, "hypothesis.target": "slot:lunch", "hypothesis.metric": "average"}),
    ("r05", "이번 달 주말 식비 왜 늘었어?", None, {"period": "this_month", "weekday": ["weekend"], "categories": ["FOOD"], "explain": True}),
    ("r06", "배달음식이 늘어서 그런가...", PREVIOUS, {"categories": [], "explain": True, "hypothesis.target": "online:배달앱", "hypothesis.metric": "amount"}),
    ("r07", "그럼 교통비는?", {"target": "spend", "period": "last_month", "categories": ["FOOD"]}, {"categories": ["TRANSPORT_CAR"], "refers_to": "previous", "hypothesis": None}),
    ("r08", "저녁에 결제를 더 자주 해서 그런가?", PREVIOUS, {"categories": [], "explain": True, "hypothesis.target": "slot:evening", "hypothesis.metric": "count"}),
]


def at(data, key):
    for part in key.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--cases", help="사례 ID 쉼표 목록, 없으면 전체")
    p.add_argument("--max-calls", type=int, required=True)
    p.add_argument("--ledger", required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()
    if not args.live or os.environ.get("ROUTER_EVAL_LIVE") != "1" or args.max_calls <= 0:
        raise SystemExit("명시적 실제 호출 승인과 상한이 필요합니다")
    out = Path(args.output_dir).expanduser().resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit("출력은 저장소 밖 경로여야 합니다")
    out.mkdir(parents=True, exist_ok=True)
    contract = reading.PROMPT + I.INSTRUCTION + json.dumps(I.catalog(), ensure_ascii=False)
    cases = [c for c in CASES if not args.cases or c[0] in args.cases.split(",")]
    manifest = {"cases": cases, "prompt_sha": hashlib.sha256(contract.encode()).hexdigest(),
                "note": "기대값은 호출 전에 고정. 실제 모델 검증 결과이며 단일 실행 표본."}
    (out / "reading-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    live = LiveReading(args.max_calls, args.ledger)
    rows = []
    for cid, question, previous, expected in cases:
        try:
            got = json.loads(json.dumps(asdict(reading.read(question, None, live, previous=previous)), ensure_ascii=False))
            mismatches = {k: {"expected": v, "actual": at(got, k)} for k, v in expected.items() if at(got, k) != v}
            row = {"id": cid, "question": question, "reading": got, "pass": not mismatches, "mismatches": mismatches}
        except Exception as error:
            row = {"id": cid, "question": question, "pass": False, "error": type(error).__name__}
        rows.append(row)
        (out / "reading-results.json").write_text(json.dumps({"rows": rows, "usage": live.usage()}, ensure_ascii=False, indent=2))
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print(json.dumps({"passed": sum(r["pass"] for r in rows), "total": len(rows), "usage": live.usage()}))


if __name__ == "__main__":
    main()
