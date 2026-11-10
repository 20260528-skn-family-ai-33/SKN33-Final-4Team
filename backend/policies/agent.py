"""6단계 최소 고정 흐름 챗봇. 라우터 계약은 handler(request) → dict (docs/policy-agent-router-handoff.md).

다음에 무엇을 할지는 decide() 한 곳에서 코드 규칙으로 정한다. 8단계에서 decide() 만 LLM 행동 고르기로 바꾸고,
handle() 의 반복문 · 도구(tools.py) · 답변 모양은 그대로 쓴다.

흐름: (생년월일 확인) → 사는 지역 버튼 → 문장 읽기(LLM 1회) → 다른 지역이면 확인 버튼 → 막연하면 분야 버튼 → 검색 · 자격 → 추천 3개
- 사실(정책명 · 기간 · 지원 내용 · 링크 · 자격)은 모두 DB 값 · 코드 계산이다. 1차 추천 이유는 찾은 키워드로 코드가 쓴다 (LLM 이유는 다음)
- 버튼은 이름이 다음 문장으로 들어온다. 질문할 때 {이름: 값}을 상태에 남겨 두고 같은 이름이면 그 값을 쓴다
- 핸들러는 DB 에 쓰지 않는다. 상태는 store.save_agent_state 로만 남긴다. not_mine · failed 는 상태를 바꾸지 않는다
"""
from dataclasses import dataclass, field
from datetime import date

from . import reading, tools
from .llm import EmbedFailed
from .reading import Need, ReadFailed
from .regions import REGIONS

AGENT = "policy"
CANCEL_WORDS = {"취소", "됐어", "그만"}  # 라우터(chat.router_core.CANCEL_WORDS)와 같은 단어
MAX_STEPS = 4  # 한 입력에서 decide() 를 부르는 최대 횟수 (읽기 → 지역 고르기 → 다시 정하기 …)
SHOW = 3
NOTICE = "자격 확정이 아니에요. 공고 원문으로 확인해 주세요."
# 막연할 때 묻는 분야 (코드가 정한 검색 문장 · 키워드). 8단계에서는 LLM 이 되묻기를 고른다
FIELDS = {
    "월세 · 주거": Need("청년 월세 주거비 지원", ["월세", "주거"]),
    "취업 · 일자리": Need("청년 취업 구직 지원", ["취업", "구직"]),
    "교육 · 직업훈련": Need("청년 직업훈련 교육 지원", ["직업훈련", "교육"]),
    "생활비": Need("청년 생활비 생계 지원", ["생활비", "생계"]),
    "저축 · 자산": Need("청년 저축 자산형성 지원", ["저축", "자산형성"]),
    "창업": Need("청년 창업 지원", ["창업"]),
    "마음 건강": Need("청년 심리상담 마음건강 지원", ["심리", "상담"]),
}
MORE, OTHER_FIELD, CHANGE_REGION = "다른 정책 더 보기", "다른 분야 찾기", "지역 바꾸기"
SAVE = "save:"  # 저장 버튼 값 = "save:" + 정책번호 (번호는 서버 상태에서만 나온다)
SAVE_LABEL = "{number}번 저장"
SAVED = "‘{name}’ 정책을 저장했어요."
ASK_REGION = "어느 지역에 사세요? 사는 곳 기준으로 정책을 찾아요."
ASK_AREA = "{name}의 어디인가요?"
ASK_FIELD = "어떤 지원을 찾으세요? 버튼을 고르거나 고민을 문장으로 써 주세요."
ASK_PLACE = "말씀하신 곳({there})이 사는 곳({home})과 달라요. 어느 지역 기준으로 찾을까요?"
PICK_BUTTON = "아래 버튼에서 골라 주세요."
NO_BIRTH = "청년 정책은 나이 조건이 있어서 생년월일이 필요해요. 내 정보에서 생년월일을 입력한 뒤 다시 찾아 주세요."
NONE_FOUND = "{region} 기준으로 지금 신청할 수 있는 정책을 찾지 못했어요. 다른 분야나 지역으로 찾아볼까요?"
NO_MORE = "더 보여 드릴 정책이 없어요."
ENDED = "청년 정책 찾기를 마칠게요."


@dataclass
class Action:
    """decide() 가 고른 다음 행동. kind 마다 쓰는 칸이 다르다"""
    kind: str  # read · use_region · ask_region · ask_field · ask_place · search · more · not_mine · end · reask
    text: str | None = None  # 읽거나 검색할 문장 (지역을 고른 뒤 이어서 할 문장)
    region: str | None = None
    codes: list[str] = field(default_factory=list)  # 고를 지역 후보
    needs: list[Need] | None = None  # 이미 읽은 필요 (다시 읽지 않는다)
    home: bool = True  # 고른 지역이 사는 곳인가 (아니면 이번 검색에만 쓴다)
    place: str | None = None


def _children(code):
    name = REGIONS[code]["name"]
    depth = len(name.split()) + 1
    return [c for c, r in REGIONS.items() if r["name"].startswith(name + " ") and len(r["name"].split()) == depth]


TOP = sorted(c for c, r in REGIONS.items() if " " not in r["name"])


def _label(code):
    """버튼 이름: 시도는 전체 이름, 그 아래는 끝 단위 ('관악구')"""
    return REGIONS[code]["name"].split()[-1]


def _within(place_codes, home):
    """문장 속 지명이 사는 곳 자신이거나 사는 곳을 품는 곳인가 ('서울' · '관악구' ⊇ 관악구)"""
    return any(code in REGIONS[home]["match"] for code in place_codes)


def _picked_region(ask, said):
    """지역 질문에 버튼 대신 이름을 쓴 경우 ('서울', '관악'): 후보 중 하나로 정해지면 그 코드"""
    options = set(ask["options"].values())
    found = [code for code in reading.place_regions(said)
             if code in options or any(REGIONS[code]["name"].startswith(REGIONS[o]["name"] + " ") for o in options)]
    return found[0] if len(found) == 1 else None


def decide(state, text, read=None):
    """상태 · 이번 입력(· 읽은 결과) → 다음 행동. 부작용 없음. 이 함수만 바꾸면 흐름이 바뀐다"""
    ask, home = state.get("ask"), state.get("region")
    if text is None:  # 기능 버튼으로 들어옴
        return Action("ask_region", codes=TOP) if not home else Action("ask_field")
    said = text.strip()
    if said.strip(" .!~") in CANCEL_WORDS:
        return Action("end")
    if ask and said in ask["options"]:
        return _answer(ask, ask["options"][said], home)
    if ask and ask["kind"] == "region":
        code = _picked_region(ask, said)
        if code:
            return _answer(ask, code, home)
        if not home:  # 사는 곳을 아직 모른다 → 새 문장이면 기억해 두고 다시 묻는다
            return Action("ask_region", codes=ask["codes"], text=ask.get("text") or said)
        return Action("reask")
    if not home:
        return Action("ask_region", codes=TOP, text=said)
    if read is None:
        return Action("read", text=said)
    if not read.mine:
        return Action("not_mine")
    if read.vague:
        return Action("ask_field")
    codes = read.place_codes
    if codes and not _within(codes, home):
        return Action("ask_place", text=said, codes=codes, needs=read.needs, place=read.place)
    return Action("search", text=said, region=home, needs=reading.needs_for(read, said))


def _answer(ask, value, home):
    """질문의 답(버튼 값) → 행동"""
    kind, pending = ask["kind"], ask.get("text")
    needs = [Need(q, k) for q, k in ask["needs"]] if ask.get("needs") is not None else None
    if kind == "region":
        if REGIONS[value]["final"]:
            return Action("use_region", region=value, text=pending, needs=needs, home=ask.get("home", True))
        return Action("ask_region", codes=_children(value), text=pending, needs=needs, home=ask.get("home", True))
    if kind == "field":
        return Action("search", region=home, needs=[FIELDS[value]])
    if kind == "place":
        if value == "home":
            return Action("search", text=pending, region=home, needs=needs)
        codes = ask["codes"]
        final = [c for c in codes if REGIONS[c]["final"]]
        if len(codes) == 1 and final:
            return Action("search", text=pending, region=final[0], needs=needs)
        options = codes if len(codes) > 1 else _children(codes[0])
        return Action("ask_region", codes=options, text=pending, needs=needs, home=False)
    if kind == "after":
        if value.startswith(SAVE):
            return Action("save", text=value[len(SAVE):])
        if value == "more":
            return Action("more")
        if value == "field":
            return Action("ask_field")
        return Action("ask_region", codes=TOP)
    raise ValueError(f"모르는 질문 종류: {kind}")


def _today(request):
    return date.fromisoformat(request["today"]) if request.get("today") else date.today()


def _reply(status, lines, choices=(), notices=(), **extra):
    return {"status": status, "work_ref": None,
            "reply": {"lines": list(lines), "choices": [dict(c) for c in choices], "notices": list(notices)}, **extra}


def _ask(store, cid, state, kind, lines, options, **saved):
    """질문하고 기다린다. {버튼 이름: 값} 을 상태에 남긴다"""
    state["ask"] = {"kind": kind, "options": {label: value for value, label in options}, **saved}
    store.save_agent_state(cid, AGENT, state)
    return _reply("awaiting_input", lines, [{"value": value, "label": label} for value, label in options])


def _ask_region(store, cid, state, action):
    if action.codes == TOP:
        line = ASK_REGION
    else:
        parent = REGIONS[action.codes[0]]["name"].rsplit(" ", 1)[0]
        line = ASK_AREA.format(name=parent) if all(REGIONS[c]["name"].startswith(parent + " ") for c in action.codes) \
            else ASK_REGION
    options = [(code, REGIONS[code]["name"] if code in TOP or line == ASK_REGION else _label(code)) for code in action.codes]
    return _ask(store, cid, state, "region", [line], options, codes=action.codes, text=action.text,
                needs=None if action.needs is None else [[n.query, n.keywords] for n in action.needs], home=action.home)


def _ask_field(store, cid, state):
    return _ask(store, cid, state, "field", [ASK_FIELD], [(label, label) for label in FIELDS])


def _ask_place(store, cid, state, action):
    there = ", ".join(REGIONS[c]["name"] for c in action.codes[:3])
    home = REGIONS[state["region"]]["name"]
    options = [("there", f"{action.place} 기준으로 보기"), ("home", f"사는 곳({_label(state['region'])}) 기준으로 보기")]
    return _ask(store, cid, state, "place", [ASK_PLACE.format(there=there, home=home)], options, codes=action.codes,
                text=action.text, needs=[[n.query, n.keywords] for n in action.needs] if action.needs else None)


def _show(store, cid, state, found, region, today, start):
    """추천 3칸 (start 번째부터). 사실은 tools.facts, 이유는 찾은 키워드로"""
    page = found[start:start + SHOW]
    saved = state.get("saved") or []  # 예전 상태(칸 없음)도 읽는다
    after = [(SAVE + f.policy.pk, SAVE_LABEL.format(number=number))
             for number, f in enumerate(page, start + 1) if f.policy.pk not in saved]
    after += [("more", MORE)] if len(found) > start + SHOW else []
    after += [("field", OTHER_FIELD), ("region", CHANGE_REGION)]
    state.update(results=[f.policy.pk for f in found], shown=start + len(page), region_used=region,
                 last=[f.policy.pk for f in page])
    name = REGIONS[region]["name"]
    if not page:  # 추천 없음은 정상 결과 → failed 가 아니라 replied (계약 3절)
        lines = [NONE_FOUND.format(region=name) if start == 0 else NO_MORE]
        result = _ask(store, cid, state, "after", lines, after[-2:])  # 더 보기 없이 분야 · 지역만
        result["status"] = "replied"
        return result
    lines = [f"{name} 기준으로 찾은 청년 정책이에요." if start == 0 else "다른 정책도 보여 드릴게요."]
    for number, item in enumerate((tools.facts(f, today) for f in page), start + 1):
        lines.append(f"{number}. {item['name']} — {item['period']}")
        if item["keywords"]:
            lines.append(f"   찾은 말: {', '.join(item['keywords'])}")
        lines.append(f"   지원 내용: {item['support'] or '정보 없음'}")
        if item["check"]:
            lines.append(f"   확인할 것: {', '.join(item['check'])}")
        if item["soon_over"]:
            lines.append("   곧 나이 상한을 넘어요. 신청 기간을 서둘러 확인해 보세요.")
        lines.append(f"   링크: {item['link']}" if item["link"] else "   링크: 정보 없음 (온통청년에서 정책명으로 찾아 주세요)")
    day = tools.data_date()
    notices = [NOTICE] + ([f"정책 정보는 {day} 기준이에요."] if day else [])
    result = _ask(store, cid, state, "after", lines, after)
    result["status"] = "replied"  # 추천 뒤에도 담당을 유지하고 후속 질문을 받는다
    result["reply"]["notices"] = notices
    return result


def handle(request, *, store, user=None, complete=None, embed=None):
    """request = 라우터 공통 입력 {agent, mode, entry, text, work_ref, conversation_id, today, as_of, context}
    complete = 읽기 LLM (기본 reading.reader()), embed = 임베딩 (기본 llm.embedder()). 둘 다 테스트 대역을 넣을 수 있다"""
    cid, text, today = request["conversation_id"], request.get("text"), _today(request)
    state = dict(store.get_agent_state(cid, AGENT) or {})
    if request.get("mode") == "cancel":
        return _end(store, cid)
    profile = tools.profile_for(user, text)
    if user is not None and profile.birth_date is None:  # 온보딩 값을 다시 묻지 않는다 — 내 정보로 안내하고 끝낸다
        return _end(store, cid, NO_BIRTH)
    read = None
    action = decide(state, text)
    for _ in range(MAX_STEPS):
        if action.kind == "read":
            try:
                read = reading.read(action.text, complete or reading.reader()[1])
            except ReadFailed as error:
                return {"status": "failed", "work_ref": None, "error": f"read:{error}"}
            action = decide(state, text, read)
        elif action.kind == "use_region":
            if action.home:
                state["region"] = action.region
            state["ask"] = None
            if action.needs is not None:  # 이미 읽은 문장 (다른 지역 확인 뒤) → 바로 검색
                action = Action("search", text=action.text, region=action.region, needs=action.needs)
            elif action.text:  # 지역을 묻기 전에 받아 둔 문장 → 이제 읽는다
                text, read = action.text, None
                action = decide(state, text)
            else:
                action = Action("ask_field")
        else:
            break
    return _act(action, store, cid, state, user, today, embed)


def _act(action, store, cid, state, user, today, embed):
    kind = action.kind
    if kind == "not_mine":
        return {"status": "not_mine", "work_ref": None}
    if kind == "end":
        return _end(store, cid)
    if kind == "ask_region":
        return _ask_region(store, cid, state, action)
    if kind == "ask_field":
        return _ask_field(store, cid, state)
    if kind == "ask_place":
        return _ask_place(store, cid, state, action)
    if kind == "reask":
        ask = state["ask"]
        return _reply("awaiting_input", [PICK_BUTTON], [{"value": v, "label": k} for k, v in ask["options"].items()])
    if kind == "save":
        return _save(store, cid, state, action.text)
    profile = tools.profile_for(user, action.text)
    if kind == "more":
        found = tools.again(state.get("results") or [], profile, today)
        return _show(store, cid, state, found, state.get("region_used") or state["region"], today, state.get("shown", 0))
    if kind == "search":
        try:
            found, _ = tools.find(action.needs or [Need(action.text)], action.region, profile, today, embed=embed)
        except EmbedFailed as error:
            return {"status": "failed", "work_ref": None, "error": f"embed:{error}"}
        return _show(store, cid, state, found, action.region, today, 0)
    raise ValueError(f"처리하지 못한 행동: {kind}")


def _save(store, cid, state, policy_no):
    """[N번 저장]: 핸들러는 DB 에 쓰지 않는다 → save_policies 로 돌려주면 엔진이 대화 저장과 같은 트랜잭션에서
    policies.saved.save_all 로 저장한다 (7단계). 같은 질문의 나머지 버튼은 그대로 다시 보인다"""
    name = tools.policy_name(policy_no)
    state["saved"] = [*(state.get("saved") or []), policy_no]
    options = [(value, label) for label, value in state["ask"]["options"].items() if value != SAVE + policy_no]
    result = _ask(store, cid, state, "after", [SAVED.format(name=name)], options)
    result["status"] = "replied"
    result["save_policies"] = [policy_no]
    return result


def _end(store, cid, line=ENDED):
    store.save_agent_state(cid, AGENT, None)  # 정책 상태는 핸들러가 지운다 (라우터는 ended 에서 작업 참조만 푼다)
    return _reply("ended", [line])
