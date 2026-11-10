"""답변 조합 — 6 · 7번(지난달 비교) 답을 서버 문장으로 LLM 이 고르고 잇고, 부족하면 보충한다 (11/10)

흐름: 서버 사실 문장(F1 …) → LLM: 문장 고르기 · 순서 · 연결 말 + 보충(extra) → 서버 검사 → 카드 맨 아래 text 블록
- 숫자가 든 문장은 서버만 만든다 (answer 템플릿 그대로). LLM 에는 문장 종류 · 대상 · 방향 · 숫자를 가린 문장만 보낸다 (금액 · 날짜 없음)
- must = 꼭 넣어야 하는 문장(항목마다 증감). 빠지거나 형식이 틀리면 조합 전체를 버리고 지금 카드 그대로 둔다
- 보충은 supplement.check 로 따로 검사한다. 탈락하면 보충만 버리고 고른 문장은 그대로 쓴다
- 보충이 없고 카드와 같은 말이면 붙이지 않는다 (same_as_card): 항목 하나짜리 카드는 이번 달 · 지난달 금액과 증감을 다 보여 주므로
  어떤 문장을 골라도 반복이다. 여러 항목 카드는 지난달 금액이 없어 증감 문장만 골랐을 때만 반복이다
- lines · notices · 카드 숫자는 바꾸지 않는다 (LLM 이 꺼져 있거나 실패하면 지금과 같은 답)
"""
import json
import re
import time

from . import supplement
from .answer import TEXT, _amount, change_sentence, relabel, span, won
from .clauses import CONNECTIVES, validate
from .scope import filter_label, name as scope

TYPES = (6, 7)
TIMEOUT = 8  # 초 — 넘으면 조합 없이 지금 카드 그대로
MAX_SENTENCES = 4
SAID = {
    "previous": "지난달 같은 기간({period})에는 {name}에 {amount} 썼어요.",
    "previous_total": "지난달 같은 기간({period}) 소비는 {amount}이었어요.",
}
PROMPT = """지난달 비교 질문의 답을 서버 문장으로 조합한다. facts의 id는 서버가 숫자까지 넣은 완성 문장으로 바뀐다.
facts의 preview는 그 문장에서 숫자만 □로 가린 것이다. 문장에는 대상 이름과 기간이 이미 들어 있다.
facts의 kind: change(지난달 대비 증감) · this_month(이번 달 금액) · last_month(지난달 같은 기간 금액).
sentences: 질문에 답하는 순서로 1~4개. text에는 자리표시({F1})와, 필요하면 앞에 연결 말(""" + "·".join(CONNECTIVES) + """)만 쓴다.
대상 이름·제목·쉼표·콜론 같은 다른 말은 붙이지 않는다(예: "식비는 {F1}" 금지, "{F1}" 또는 "그리고 {F4}"만).
text 하나에 자리표시는 2개까지, 같은 id는 한 번만. facts에는 그 text에 쓴 id를 적는다.
must=true인 id는 반드시 넣는다. 질문과 관련 없는 문장은 빼도 된다.
""" + supplement.PROMPT_RULES + """
JSON: {"sentences":[{"text":"{F1}","facts":["F1"]}],"extra":null,"extra_reason":null}"""


def _comparison(res):
    return res["comparison"]["category" if res["item"].get("category") else "total"]


def applies(results):
    """조합 대상: 모든 항목이 6 · 7번이고 모두 조회에 성공했으며 비교할 수 있을 때만.
    일부 실패 · 섞인 요청은 지금 답 그대로. 비교할 수 없으면 카드 맨 위 안내(lead)로 충분해 부르지 않는다 (같은 안내가 두 번 보임, 11/10 실제 평가 N8)"""
    return bool(results) and all(r["ok"] and r["result"]["type"] in TYPES and _comparison(r["result"])["available"]
                                 for r in results)


def _now(res, name):
    """이번 달 금액 문장 — 확정 · 일부 내역 · 내역 없음 (answer 템플릿 그대로)"""
    item, cur = res["item"], res["current"]
    period, seen = f"이번 달({span(cur['period'])})", _amount(cur["observed"], item)
    if cur["confirmed"] is None and cur["coverage"]["state"] == "none" and not seen:
        return TEXT["no_data"].format(period=period, name=name)
    if cur["confirmed"] is None:
        return TEXT["observed"].format(period=period, name=name, amount=won(seen))
    if name == "전체":
        return TEXT["total"].format(period=period, amount=won(seen))
    return TEXT["category"].format(period=period, name=name, amount=won(seen))


def said_name(item):
    """문장에 넣는 범위 이름. 카드 제목은 '식비 · 저녁'이지만 문장에서는 '저녁 식비' ('주말 · 저녁'이 같이 있으면 '주말 저녁 식비')"""
    name, extra = scope(item), filter_label(item)
    if extra and name.endswith(" · " + extra):
        return f"{extra.replace(' · ', ' ')} {name[:-len(' · ' + extra)]}"
    return name


def facts(results):
    """조회 결과 → 서버 사실 문장 [{id, kind, subject, direction, must, text}] (항목 순서대로)"""
    out = []

    def add(kind, subject, text, direction=None, must=False):
        out.append({"id": f"F{len(out) + 1}", "kind": kind, "subject": subject, "direction": direction, "must": must,
                    "text": relabel(text, res)})  # 달끼리 비교면 '지난달' → '9월'

    res = None

    for r in results:  # applies() 를 통과한 결과 = 모두 비교 가능
        res = r["result"]
        item, name = res["item"], said_name(res["item"])
        c = _comparison(res)
        subject = "전체 소비는" if name == "전체" else f"{name} 지출은"
        direction = "up" if c["delta"] > 0 else "down" if c["delta"] < 0 else "same"
        # 증감률(↑ 12.07%)은 카드의 큰 숫자 · 배지에 이미 있어 문장에서는 뺀다 (11/10 사용자 확인). 금액 · 방향은 그대로
        change = re.sub(r"\s*[↑↓] [0-9.]+%$", "", change_sentence(res))
        add("change", name, f"{subject} {change}", direction, must=True)
        add("this_month", name, _now(res, name))
        before, period = _amount(res["previous"]["confirmed"], item), span(res["previous"]["period"])
        add("last_month", name, SAID["previous_total"].format(period=period, amount=won(before)) if name == "전체"
            else SAID["previous"].format(period=period, name=name, amount=won(before)))
    return out


def for_llm(table):
    """LLM 에 보낼 모양 — 금액 · 날짜 없이. preview = 문장에서 숫자를 □로 가린 것 (대상 이름이 문장에 이미 있다는 걸 보이려고)"""
    return [{**{k: f[k] for k in ("id", "kind", "subject", "direction", "must")},
             "preview": re.sub(r"[0-9][0-9,.]*", "□", f["text"])} for f in table]


def check(raw, table):
    """LLM 응답 → (문장 목록 | None, trace). None 이면 조합 전체를 버린다 (보충만 틀리면 보충만 버린다)"""
    trace = {"used": [], "rejected": [], "extra_reason": None, "extra_dropped": []}
    try:
        data = json.loads(raw)
        sentences, extra, reason = data["sentences"], data.get("extra"), data.get("extra_reason")
    except Exception:
        return None, {**trace, "rejected": ["format"]}
    if not isinstance(sentences, list) or not 1 <= len(sentences) <= MAX_SENTENCES:
        return None, {**trace, "rejected": ["format"]}
    known = {f["id"]: f for f in table}
    lines, used, why = [], [], []
    for n, s in enumerate(sentences, 1):
        if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not isinstance(s.get("facts"), list):
            return None, {**trace, "rejected": ["format"]}
        ids = list(dict.fromkeys(re.findall(r"\{(F\d+)\}", s["text"])))
        problems = validate(s["text"], ids, known, "F")
        if set(s["facts"]) != set(ids):
            problems.append("facts")
        if set(ids) & set(used):
            problems.append("repeated")
        if problems:
            why += [f"{n}:{p}" for p in problems]
            continue
        used += ids
        filled = re.sub(r"\{(F\d+)\}", lambda m: known[m.group(1)]["text"], s["text"].strip())
        lines.append(re.sub(r"\.\s*\.", ".", re.sub(r"\s+", " ", filled)))
    missing = [f["id"] for f in table if f["must"] and f["id"] not in used]
    if missing:
        why.append("missing_must:" + ",".join(missing))
    if why:
        return None, {**trace, "rejected": why}
    text, dropped = supplement.check(extra, reason)
    trace.update(used=used, extra_reason=reason if text else None, extra_dropped=dropped)
    return lines + ([text] if text else []), trace


def attach(reply, results, question, complete, clock=time.monotonic):
    """조합에 성공하면 카드(view) 맨 아래에 text 블록을 붙인 새 reply, 아니면 reply 그대로 → (reply, trace | None)
    complete(system, user, timeout=초) — 해석과 같은 LLM. 호출 오류 · 시간 초과는 재시도 없이 지금 답으로"""
    if complete is None or not reply.get("view") or not applies(results):
        return reply, None
    table = facts(results)
    started = clock()
    try:
        raw = complete(PROMPT, json.dumps({"question": question, "facts": for_llm(table)}, ensure_ascii=False),
                       timeout=TIMEOUT)
    except Exception:
        return reply, {"fallback": "call_failed"}
    if clock() - started > TIMEOUT:
        return reply, {"fallback": "timeout"}
    lines, trace = check(raw, table)
    trace["ms"] = round((clock() - started) * 1000)  # 조합 호출에 걸린 시간 (개발용 분석 과정)
    if lines is None:
        return reply, {**trace, "fallback": "rejected"}
    if not trace["extra_reason"] and (len(results) == 1 or all(f["must"] for f in table if f["id"] in trace["used"])):
        # 보충이 없고 카드에 이미 있는 내용뿐이면 문장 칸을 붙이지 않는다 (11/10 사용자 확인)
        # 항목 하나: 카드가 이번 달 · 지난달 금액 · 증감을 모두 보여 준다 / 여러 항목: 증감 문장만 골랐을 때
        return reply, {**trace, "fallback": "same_as_card"}
    return {**reply, "view": [*reply["view"], {"kind": "text", "lines": lines}]}, {**trace, "fallback": None}
