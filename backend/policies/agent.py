"""청년 정책 챗봇 (6단계 고정 흐름 + 8단계 행동 고르기). 라우터 계약은 handler(request) → dict (docs/policy-agent-router-handoff.md).

다음에 무엇을 할지는 decide() 한 곳에서 정한다. 버튼 · 취소 · 지역은 규칙, 직접 쓴 문장은 planner(LLM)가 고른 행동을 따르고
LLM 이 실패하면 규칙으로 돌아간다. handle() 의 반복문 · 도구(tools.py) · 답변 모양은 같이 쓴다.

흐름: (생년월일 확인) → 문장 읽기(LLM 1회) → 찾을 지역: 문장 속 지역(이번 검색에만) > 사는 지역(저장해 둔 지역) > 지역 버튼 →
막연하면 맞춤 추천(분야는 LLM 이 짐작, 못 하면 취업 상태로 코드가 고름 — 멘토 요청 ④) → 검색 · 자격 → 돌아보기 →
추천 3개 카드 + [N번 저장] [저장한 정책 보기] [다음에도 …로 찾기] [지역 바꾸기]. 분야 버튼은 없다 (기능 버튼으로 오면 말로 묻는다)
- 저장 · 지역 기억은 핸들러가 DB 에 쓰지 않고 save_policies · save_profile 로 돌려준다 (엔진이 대화 저장 트랜잭션에서 쓴다)
- 사실(정책명 · 기간 · 지원 내용 · 링크 · 자격)은 모두 DB 값 · 코드 계산이다. 1차 추천 이유는 찾은 키워드로 코드가 쓴다 (LLM 이유는 다음)
- 버튼은 이름이 다음 문장으로 들어온다. 질문할 때 {이름: 값}을 상태에 남겨 두고 같은 이름이면 그 값을 쓴다
- 핸들러는 DB 에 쓰지 않는다. 상태는 store.save_agent_state 로만 남긴다. not_mine · failed 는 상태를 바꾸지 않는다
"""
import re
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings

from . import planner, profiles, reading, review, saved, tools
from .llm import EmbedFailed
from .reading import Need, ReadFailed
from .regions import REGIONS

AGENT = "policy"
CANCEL_WORDS = {"취소", "됐어", "그만"}  # 라우터(chat.router_core.CANCEL_WORDS)와 같은 단어
MAX_STEPS = 4  # 한 입력에서 decide() 를 부르는 최대 횟수 (읽기 → 지역 고르기 → 다시 정하기 …)
SHOW = 3
NOTICE = "자격 확정이 아니에요. 공고 원문으로 확인해 주세요."
# 맞춤 추천 분야 (코드 → 이름, 검색 문장 · 키워드). 막연한 문장이면 LLM(planner)이 코드를 고르고 검색은 이 값으로 한다
FIELDS = {
    "housing": ("월세 · 주거", Need("청년 월세 주거비 지원", ["월세", "주거"])),
    "job": ("취업 · 일자리", Need("청년 취업 구직 지원", ["취업", "구직"])),
    "training": ("교육 · 직업훈련", Need("청년 직업훈련 교육 지원", ["직업훈련", "교육"])),
    "living": ("생활비", Need("청년 생활비 생계 지원", ["생활비", "생계"])),
    "saving": ("저축 · 자산", Need("청년 저축 자산형성 지원", ["저축", "자산형성"])),
    "startup": ("창업", Need("청년 창업 지원", ["창업"])),
    "mind": ("마음 건강", Need("청년 심리상담 마음건강 지원", ["심리", "상담"])),
}
FIELD_BY_LABEL = {label: code for code, (label, _) in FIELDS.items()}  # 예전 대화 상태의 분야 버튼 이름 (11-12 전)
# LLM 이 분야를 못 짐작했거나 가짜 모드일 때: 이 대화 · 회원 정보의 취업 상태로 코드가 고른다
DEFAULT_FIELDS = {"jobless": ["job", "living", "training"], "employed": ["housing", "saving", "living"],
                  None: ["living", "housing", "job"]}
MORE, OTHER_FIELD, CHANGE_REGION = "다른 정책 더 보기", "다른 분야 찾기", "지역 바꾸기"
SAVE = "save:"  # 저장 버튼 값 = "save:" + 정책번호 (번호는 서버 상태에서만 나온다)
SAVE_LABEL = "{number}번 저장"
SAVED = "‘{name}’ 정책을 저장했어요. [저장한 정책 보기]에서 모아 볼 수 있어요."
SAVE_FAILED = "‘{name}’ 정책은 지금 저장할 수 없어요. 정책 목록에서 빠졌을 수 있어요."
SAVED_LIST = "저장한 정책 보기"
SAVED_WORDS = re.compile(r"저장한\s*(?:정책|거|것|목록)|저장\s*목록|북마크")  # 문장으로 물어도 저장 목록을 보인다
SAVED_HEAD = "저장한 청년 정책이에요. (최근 저장 순)"
NO_SAVED = "아직 저장한 정책이 없어요. 추천 결과에서 [N번 저장]을 누르면 여기에 모여요."
SAVED_NEED_LOGIN = "저장한 정책은 로그인한 뒤 볼 수 있어요."
APPLY_LABEL = tools.STATE
REMEMBER_LABEL = "다음에도 {name}로 찾기"  # 시군구 이름은 모두 구 · 시 · 군(받침 없음)으로 끝나 '로'가 맞다
REMEMBERED = "다음부터 {name} 기준으로 찾을게요. 내 정보의 사는 지역도 {name}로 바꿨어요."
CONFIRM_HOME = "가입 지역({name})으로 찾으시는 거 맞나요?"
YES, NO = "예", "아니요"
CONFIRM_YES = {YES, "네", "넵", "응", "맞아", "맞아요"}
CONFIRM_NO = {NO, "아니오", "아니", "아뇨"}
HESITATIONS = {"", "음", "흠", "글쎄", "글쎄요", "잠깐", "잠시만"}
ASK_REGION = "어느 지역에 사세요? 사는 곳 기준으로 정책을 찾아요."
ASK_AREA = "{name}의 어디인가요?"
ASK_WHICH_PLACE = "말씀하신 곳이 여러 곳이에요. 어느 지역인가요?"
ASK_OTHER_REGION = "어느 지역으로 찾을까요?"
ASK_TOPIC = "어떤 고민이 있으세요? 편하게 말해 주세요. (예: 월세가 부담돼요 · 취업 준비 중이에요 · 돈을 모으고 싶어요)"
AUTO_LEAD = "어떤 지원인지 말씀이 없어서 {fields} 쪽으로 찾아봤어요. 고민을 말해 주시면 더 맞게 찾아 드려요."
OTHER_TOPIC = "다른 고민도 문장으로 말해 주세요."
PICK_BUTTON = "아래 버튼에서 골라 주세요."
NO_BIRTH = "청년 정책은 나이 조건이 있어서 생년월일이 필요해요. 내 정보에서 생년월일을 입력한 뒤 다시 찾아 주세요."
NONE_FOUND = "{region} 기준으로 지금 신청할 수 있는 정책을 찾지 못했어요. 다른 고민을 말해 주시거나 지역을 바꿔 볼까요?"
REVIEW_FAILED = "추천할 정책을 확인하지 못했어요. 잠시 후 같은 질문으로 다시 시도해 주세요."
NO_MORE = "더 보여 드릴 정책이 없어요."
ENDED = "청년 정책 찾기를 마칠게요."
WHICH = "어느 정책을 말씀하시는 건가요?"
NO_TARGET = "어느 정책을 말씀하시는지 몰라요. 먼저 정책을 찾아 볼게요."
DETAIL_HEAD = "공고 원문에 적힌 내용이에요."
GONE = "그 정책은 지금 정책 목록에 없어요. 공고가 내려갔을 수 있어요."


@dataclass
class Action:
    """decide() 가 고른 다음 행동. kind 마다 쓰는 칸이 다르다"""
    kind: str  # read · use_region · ask_region · ask_topic · confirm · confirm_yes · search · more · save · show_saved · remember · not_mine · end · reask
    text: str | None = None  # 읽거나 검색할 문장 (지역을 고른 뒤 이어서 할 문장)
    region: str | None = None
    codes: list[str] = field(default_factory=list)  # 고를 지역 후보
    needs: list[Need] | None = None  # 이미 읽은 필요 (다시 읽지 않는다)
    home: bool = True  # 고른 지역이 사는 곳인가 (아니면 이번 검색에만 쓴다)
    topics: list[str] = field(default_factory=list)  # detail: 궁금한 항목 (planner.TOPICS)
    fields: list[str] | None = None  # 맞춤 추천이면 분야 코드 목록 (빈 목록 = 코드가 기본값으로 고름), 아니면 None


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


def decide(state, text, read=None, choice=None):
    """다음 행동. 가입 지역(내 정보)으로 찾게 되면 이 대화에서 처음 한 번은 맞는지 묻는다 (멘토 요청 ②, 11-11).
    문장에서 지역을 말했거나 이 대화에서 고른 지역이면 묻지 않는다"""
    action = _decide(state, text, read, choice)
    saved = state.get("remembered")
    if action.kind == "search" and saved and action.region == saved and not state.get("confirmed"):
        return Action("confirm", text=action.text, region=saved, needs=action.needs, fields=action.fields)
    return action


def _decide(state, text, read=None, choice=None):
    """상태 · 이번 입력(· 읽은 결과 · LLM 이 고른 행동) → 다음 행동. 부작용 없음.
    버튼 · 취소 · 지역은 규칙으로 정하고, 읽은 문장의 행동은 choice(planner.ask)가 있으면 그것을, 없으면 규칙을 따른다"""
    ask, home = state.get("ask"), state.get("region")
    if text is None:  # 기능 버튼으로 들어옴
        return Action("ask_region", codes=TOP) if not home else Action("ask_topic")
    said = text.strip()
    if said.strip(" .!~") in CANCEL_WORDS:
        return Action("end")
    if ask and said in ask["options"]:
        return _answer(ask, ask["options"][said], home)
    if ask and ask["kind"] in {"confirm", "region"}:
        # 짧은 답 전체가 일치할 때만 처리한다. "아니, 취업 지원 찾아줘"는 새 질문으로 읽는다.
        short = said.strip(" \t\r\n.!?~…。！？")
        if ask["kind"] == "confirm":
            if short in CONFIRM_YES:
                return _answer(ask, "yes", home)
            if short in CONFIRM_NO:
                return _answer(ask, "no", home)
        if short in HESITATIONS | CONFIRM_YES | CONFIRM_NO:
            return Action("reask")
    if SAVED_WORDS.search(said):  # 버튼 이름을 먼저 본다 ('저장한 정책 보기' 버튼도 이름으로 들어온다)
        return Action("show_saved")
    if ask and ask["kind"] == "region":
        code = _picked_region(ask, said)
        if code:
            return _answer(ask, code, home)
        if read is None:  # 지역 이름이 아니면 먼저 읽는다 — 소비 · 예산 질문은 지역을 다시 묻지 않고 다른 담당으로
            return Action("read", text=said)
        # 지역 대신 새 정책 문장을 보냈으면 아래의 공통 해석으로 질문과 검색 조건을 함께 바꾼다.
    if read is None:  # 사는 곳을 몰라도 먼저 읽는다 — 문장 속 지역으로 바로 찾을 수 있다
        return Action("read", text=said)
    picked = choice[0] if choice else None
    if picked == "end":
        return Action("end")
    if picked == "detail":
        number, results = choice[1], state.get("results") or []
        return Action("detail", text=results[number - 1] if number else None, topics=choice[2])
    if picked == "not_mine" or (picked is None and not read.mine):
        return Action("not_mine")
    fields = choice[3] if choice and len(choice) > 3 else []
    if ask and ask["kind"] in {"confirm", "region"} and not (read.needs or read.vague or fields):
        return Action("reask")
    # 막연한 문장 → 분야를 묻지 않고 맞춤 추천 (멘토 요청 ④)
    action = _place_action(read, said, home, fields=list(fields) if read.vague or (fields and not read.needs) else None)
    if ask and ask["kind"] == "region" and not home and not read.place_codes:
        # 가입지역도 새 지명도 없으면 고르던 지역 단계는 유지한다. 이전 질문의 키워드·분야는 버린다.
        action.codes, action.home = ask["codes"], ask.get("home", True)
    return action


def _place_action(read, said, home, fields=None):
    """찾을 지역 정하기. 문장 속 지역이 우선이고 이번 검색에만 쓴다 (사는 곳으로 기억하지 않는다).
    시군구 하나로 정해지면 바로 검색, 넓거나 여러 곳이면 그 안에서만 고르게 한다. 지명이 없으면 사는 곳, 그것도 모르면 묻는다.
    고르게 할 때도 읽은 필요를 질문에 남겨 고른 뒤 다시 읽지 않는다. fields 가 있으면(맞춤 추천) 그대로 넘긴다"""
    needs, codes = (None if fields is not None else reading.needs_for(read, said)), read.place_codes
    if codes and home and _within(codes, home):  # '서울' 이라고 했고 사는 곳이 관악구 → 관악구로
        codes = []
    if len(codes) == 1 and REGIONS[codes[0]]["final"]:
        return Action("search", text=said, region=codes[0], needs=needs, fields=fields)
    if codes:
        options = codes if len(codes) > 1 else _children(codes[0])
        return Action("ask_region", codes=options, text=said, needs=needs, home=False, fields=fields)
    if home:
        return Action("search", text=said, region=home, needs=needs, fields=fields)
    return Action("ask_region", codes=TOP, text=said, needs=needs, fields=fields)


def _answer(ask, value, home):
    """질문의 답(버튼 값) → 행동"""
    kind, pending, fields = ask["kind"], ask.get("text"), ask.get("fields")
    needs = [Need(q, k) for q, k in ask["needs"]] if ask.get("needs") is not None else None
    if kind == "region":
        if REGIONS[value]["final"]:
            return Action("use_region", region=value, text=pending, needs=needs, home=ask.get("home", True), fields=fields)
        return Action("ask_region", codes=_children(value), text=pending, needs=needs, home=ask.get("home", True),
                      fields=fields)
    if kind == "field":  # 11-12 전에 시작한 대화의 분야 버튼 (지금은 분야 버튼을 보이지 않는다)
        return Action("search", region=home, needs=[FIELDS[FIELD_BY_LABEL[value]][1]])
    if kind == "which":
        return Action("detail", text=value, topics=ask["topics"])
    if kind == "confirm":
        if value == "yes":
            return Action("confirm_yes", text=pending, region=home, needs=needs, fields=fields)
        return Action("ask_region", codes=TOP, text=pending, needs=needs, home=False, fields=fields)  # 이번 검색 지역만
    if kind == "after":
        if value.startswith(SAVE):
            return Action("save", text=value[len(SAVE):])
        if value == "saved":
            return Action("show_saved")
        if value == "remember":
            return Action("remember")  # 이번에 찾은 지역 (state["region_used"]) — 사는 곳이 아니다
        if value == "more":
            return Action("more")
        if value == "field":  # 11-12 전 대화의 [다른 분야 찾기]
            return Action("ask_topic")
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
    parent = REGIONS[action.codes[0]]["name"].rsplit(" ", 1)[0]
    area = action.codes != TOP and all(REGIONS[c]["name"].startswith(parent + " ") for c in action.codes)  # 한 시도 안
    line = ASK_AREA.format(name=parent) if area else ASK_REGION if action.home else \
        ASK_OTHER_REGION if action.codes == TOP else ASK_WHICH_PLACE  # 가입 지역 [아니요] · '중구' 처럼 여러 시도
    options = [(code, _label(code) if area else REGIONS[code]["name"]) for code in action.codes]
    return _ask(store, cid, state, "region", [line], options, codes=action.codes, text=action.text,
                needs=None if action.needs is None else [[n.query, n.keywords] for n in action.needs], home=action.home,
                fields=action.fields)


def _ask_confirm(store, cid, state, action):
    return _ask(store, cid, state, "confirm", [CONFIRM_HOME.format(name=REGIONS[action.region]["name"])],
                [("yes", YES), ("no", NO)], text=action.text,
                needs=None if action.needs is None else [[n.query, n.keywords] for n in action.needs], fields=action.fields)


def _ask_topic(store, cid, state, lines=(ASK_TOPIC,)):
    """무엇을 찾을지 말로 묻는다 (버튼 없음, 멘토 요청 ④). 다음 문장은 새 질문으로 읽는다"""
    state["ask"] = None
    store.save_agent_state(cid, AGENT, state)
    return _reply("awaiting_input", lines)


def _common_after(state, region, member):
    """추천 · 저장 목록 뒤에 늘 붙는 버튼: [저장한 정책 보기] · [다음에도 …로 찾기] · [다른 분야 찾기] · [지역 바꾸기]"""
    after = [("saved", SAVED_LIST)] if member else []
    if member and region and state.get("remembered") != region:  # 저장된 지역과 다른 곳으로 찾았을 때 (말한 지역 · 고른 지역)
        after.append(("remember", REMEMBER_LABEL.format(name=_label(region))))
    return after + [("region", CHANGE_REGION)]  # [다른 분야 찾기]는 없다 — 다른 고민은 문장으로 (멘토 요청 ④)


def _after_buttons(state, member):
    """추천 뒤 버튼을 상태에서 다시 만든다. 저장해 둔 질문의 버튼을 꺼내 쓰지 않는다 —
    대화 상태는 DB jsonb 에 저장되는데 jsonb 는 객체 키 순서를 바꿔서 버튼 순서가 뒤섞인다"""
    results, last, shown = state.get("results") or [], state.get("last") or [], state.get("shown", 0)
    done = state.get("saved") or []
    after = [(SAVE + no, SAVE_LABEL.format(number=number))
             for number, no in enumerate(last, shown - len(last) + 1) if no not in done]
    after += [("more", MORE)] if len(results) > shown else []
    return after + _common_after(state, state.get("region_used"), member)


def _card(result, sections):
    """카드 모양 답변: 정책마다 굵은 제목 + 점 목록 (채팅 화면의 결과 카드, chat/engine.py 가 result 로 표시)"""
    result["status"] = "replied"  # 추천 뒤에도 담당을 유지하고 후속 질문을 받는다
    result["reply"]["sections"] = sections
    return result


def _show(store, cid, state, found, region, today, start, member=False, lead=None):
    """추천 3칸 (start 번째부터). 사실은 tools.facts, 이유는 찾은 키워드로. lead = 맞춤 추천 안내 줄 (첫 결과에만)"""
    page = found[start:start + SHOW]
    done = state.get("saved") or []  # 예전 상태(칸 없음)도 읽는다
    after = [(SAVE + f.policy.pk, SAVE_LABEL.format(number=number))
             for number, f in enumerate(page, start + 1) if f.policy.pk not in done]
    after += [("more", MORE)] if len(found) > start + SHOW else []
    after += _common_after(state, region, member)
    state.update(results=[f.policy.pk for f in found], shown=start + len(page), region_used=region,
                 last=[f.policy.pk for f in page], focus=None)
    name = REGIONS[region]["name"]
    if not page:  # 추천 없음은 정상 결과 → failed 가 아니라 replied (계약 3절)
        lines = ([lead] if lead else []) + [NONE_FOUND.format(region=name) if start == 0 else NO_MORE]
        result = _ask(store, cid, state, "after", lines, [a for a in after if not a[0].startswith(SAVE)])
        result["status"] = "replied"
        return result
    lines = ([lead] if lead else []) + [f"{name} 기준으로 찾은 청년 정책이에요." if start == 0 else "다른 정책도 보여 드릴게요."]
    sections = []
    for number, item in enumerate((tools.facts(f, today) for f in page), start + 1):
        rows = [f"신청: {item['period']}", f"지원: {item['support'] or '정보 없음'}"]
        if item["check"]:
            rows.append(f"확인할 것: {', '.join(item['check'])}")
        if item["soon_over"]:
            rows.append("곧 나이 상한을 넘어요. 신청 기간을 서둘러 확인해 보세요.")
        if item["keywords"]:
            rows.append(f"관련: {', '.join(item['keywords'])}")
        rows.append(f"링크: {item['link']}" if item["link"] else "링크: 정보 없음 (온통청년에서 정책명으로 찾아 주세요)")
        sections.append({"title": f"{number}. {item['name']}", "lines": rows})
    if start == 0 and not lead:
        lines.append(OTHER_TOPIC)
    day = tools.data_date()
    result = _card(_ask(store, cid, state, "after", lines, after), sections)
    result["reply"]["notices"] = [NOTICE] + ([f"정책 정보는 {day} 기준이에요."] if day else [])
    return result


def _show_saved(store, cid, state, user, today):
    """저장한 정책 목록 (DB 읽기만). 마감 · 수집 종료도 상태와 함께 보인다"""
    member = profiles.is_member(user)
    after = _common_after(state, None, member)[1 if member else 0:]  # 지금 보고 있는 목록이라 [저장한 정책 보기]는 뺀다
    if not member:
        result = _ask(store, cid, state, "after", [SAVED_NEED_LOGIN], after)
        result["status"] = "replied"
        return result
    items = saved.items(user, today)
    if not items:
        result = _ask(store, cid, state, "after", [NO_SAVED], after)
        result["status"] = "replied"
        return result
    # 지금 보고 있는 목록이 다음 "그거" · "2번"의 대상이다 (지난 추천을 가리키지 않게). 모두 저장된 정책이라 저장 버튼은 없다
    numbers = [item["policy_no"] for item in items]
    state.update(results=numbers, shown=len(numbers), last=numbers, focus=None,
                 saved=list(dict.fromkeys([*(state.get("saved") or []), *numbers])))
    sections = []
    for number, item in enumerate(items, 1):
        state_text = APPLY_LABEL[item["apply_state"]] + (f" (마감 {item['apply_end']})" if item["apply_end"] else "")
        rows = [f"신청: {state_text}", f"저장: {item['saved_at'][:10]}"]
        if not item["active"]:
            rows.append("정책 목록에서 빠진 정책이에요. 공고가 내려갔을 수 있어요.")
        rows.append(f"링크: {item['link']}" if item["link"] else "링크: 정보 없음 (온통청년에서 정책명으로 찾아 주세요)")
        sections.append({"title": f"{number}. {item['name']}", "lines": rows})
    return _card(_ask(store, cid, state, "after", [SAVED_HEAD], after), sections)


def _remember(store, cid, state, region):
    """[다음에도 …로 찾기]: 핸들러는 DB 에 쓰지 않는다 → save_profile 로 돌려주면 엔진이 대화 저장 트랜잭션에서 저장한다"""
    state.update(remembered=region, region=region, confirmed=True)
    options = _after_buttons(state, True)  # 지역 기억은 회원만 (remembered 를 먼저 바꿔 [다음에도 …] 가 빠진다)
    result = _ask(store, cid, state, "after", [REMEMBERED.format(name=_label(region))], options)
    result["status"] = "replied"
    result["save_profile"] = {"region_code": region}
    return result


def _profile_job(profile):
    """Profile 의 취업 상태 코드 → DEFAULT_FIELDS 키"""
    return {tools.JOBLESS: "jobless", tools.EMPLOYED: "employed"}.get(profile.job)


def _job(state):
    """이 대화에서 마지막으로 말한 취업 상태 ("jobless" · "employed" · None). 예전 상태의 jobless: True 도 읽는다"""
    return state.get("job") or ("jobless" if state.get("jobless") else None)


def _shown(state):
    """직전 추천 [(화면 번호, 정책명)] — 지금까지 보여 준 것 전부 (더 보기 포함). LLM 은 이 번호 안에서만 고른다"""
    numbers = (state.get("results") or [])[:state.get("shown", 0)]
    return [(number, tools.policy_name(policy_no)) for number, policy_no in enumerate(numbers, 1)]


def handle(request, *, store, user=None, complete=None, embed=None, plan=None, check=None):
    """request = 라우터 공통 입력 {agent, mode, entry, text, work_ref, conversation_id, today, as_of, context}
    complete = 읽기 LLM (기본 reading.reader()), embed = 임베딩 (기본 llm.embedder()),
    plan = 행동 고르기 LLM (기본 planner.chooser()), check = 돌아보기 LLM (기본 review.reviewer()).
    plan · check 는 가짜 모드면 None → 규칙 · 거르지 않음, False 면 끈다. 모두 테스트 대역을 넣을 수 있다"""
    cid, text, today = request["conversation_id"], request.get("text"), _today(request)
    state = dict(store.get_agent_state(cid, AGENT) or {})
    if request.get("mode") == "cancel":
        return _end(store, cid)
    profile = tools.profile_for(user, text)
    if user is not None and profile.birth_date is None:  # 온보딩 값을 다시 묻지 않는다 — 내 정보로 안내하고 끝낸다
        return _end(store, cid, NO_BIRTH)
    said = tools.said_job(text)
    if said:  # 대화에서 말한 취업 상태는 뒤의 버튼 · 상세에서도 쓰고, 고쳐 말하면 바꾼다 (PR 97 재리뷰)
        state["job"] = said
        state.pop("jobless", None)
    if not state.get("region"):  # 저장해 둔 사는 지역이 있으면 다시 묻지 않는다 ([지역 바꾸기]로 바꿀 수 있다)
        remembered = profiles.remembered_region(user)
        if remembered:
            state.update(region=remembered, remembered=remembered)
    read = None
    action = decide(state, text)
    for _ in range(MAX_STEPS):
        if action.kind == "read":
            try:
                read = reading.read(action.text, complete or reading.reader()[1])
            except ReadFailed as error:
                return {"status": "failed", "work_ref": None, "error": f"read:{error}"}
            plan = plan if plan is not None else planner.chooser()
            results = state.get("results") or []
            focus = results.index(state["focus"]) + 1 if state.get("focus") in results else None
            choice = planner.ask(action.text, read, _shown(state), plan, focus) if plan else None
            action = decide(state, text, read, choice)
        elif action.kind == "confirm_yes":  # 이 대화에서는 다시 묻지 않는다
            state.update(confirmed=True, ask=None)
            action = Action("search", text=action.text, region=action.region, needs=action.needs, fields=action.fields)
        elif action.kind == "use_region":
            if action.home:
                state["region"] = action.region
            if action.region == state.get("remembered"):
                state["confirmed"] = True  # 직접 고른 가입지역을 다음 질문에서 또 확인하지 않는다
            state["ask"] = None
            if action.needs is not None or action.fields is not None:  # 이미 읽은 문장 (다른 지역 확인 뒤) → 바로 검색
                action = Action("search", text=action.text, region=action.region, needs=action.needs, fields=action.fields)
            elif action.text:  # 지역을 묻기 전에 받아 둔 문장 → 이제 읽는다
                text, read = action.text, None
                action = decide(state, text)
            else:
                action = Action("ask_topic")
        else:
            break
    return _act(action, store, cid, state, user, today, embed, check)


def _act(action, store, cid, state, user, today, embed, check=None):
    kind = action.kind
    if kind == "not_mine":
        return {"status": "not_mine", "work_ref": None}
    if kind == "end":
        return _end(store, cid)
    if kind == "ask_region":
        return _ask_region(store, cid, state, action)
    if kind == "ask_topic":
        return _ask_topic(store, cid, state)
    if kind == "reask":
        ask = state["ask"]
        return _reply("awaiting_input", [PICK_BUTTON], [{"value": v, "label": k} for k, v in ask["options"].items()])
    if kind == "save":
        return _save(store, cid, state, action.text, user)
    if kind == "show_saved":
        return _show_saved(store, cid, state, user, today)
    if kind == "confirm":
        return _ask_confirm(store, cid, state, action)
    if kind == "remember":
        return _remember(store, cid, state, state.get("region_used") or state["region"])
    if kind == "detail":
        return _detail(store, cid, state, user, today, action)
    profile, member = tools.profile_for(user, action.text, _job(state)), profiles.is_member(user)
    if kind == "more":
        found = tools.again(state.get("results") or [], profile, today)
        return _show(store, cid, state, found, state.get("region_used") or state["region"], today,
                     state.get("shown", 0), member)
    if kind == "search":
        needs, lead, said = action.needs or [Need(action.text)], None, action.text
        if action.fields is not None:  # 맞춤 추천: LLM 이 짐작한 분야, 없으면 취업 상태로 코드가 고른 분야
            codes = [code for code in action.fields if code in FIELDS] or DEFAULT_FIELDS[_profile_job(profile)]
            names = [FIELDS[code][0] for code in codes]
            needs, lead = [FIELDS[code][1] for code in codes], AUTO_LEAD.format(fields=", ".join(names))
            said = f"{action.text or ''} (고른 분야: {', '.join(names)})".strip()  # 막연한 원문만 주면 돌아보기가 다 뺄 수 있다
        try:
            found, _ = tools.find(needs, action.region, profile, today, embed=embed)
        except EmbedFailed as error:
            return {"status": "failed", "work_ref": None, "error": f"embed:{error}"}
        judge = check if check is not None else review.reviewer()
        if found and judge is None and check is not False and getattr(settings, "CHAT_LLM", "fake") == "live":
            return _ask_topic(store, cid, state, [REVIEW_FAILED])  # 키·모델 설정 누락도 미검토 추천으로 우회하지 않는다
        if judge and found:  # 돌아보기: 질문과 맞는 것만 남긴다. 실패와 검색 결과 없음은 별개다
            kept = review.keep(said or " / ".join(n.query for n in needs), found, judge,
                               region=REGIONS[action.region]["name"])
            if kept is None:
                return _ask_topic(store, cid, state, [REVIEW_FAILED])
            found = kept
        return _show(store, cid, state, found, action.region, today, 0, member, lead)
    raise ValueError(f"처리하지 못한 행동: {kind}")


def _detail(store, cid, state, user, today, action):
    """정책 상세 (8단계). 어느 정책인지 모르면 직전 추천을 버튼으로 묻고, 추천이 없으면 무엇을 찾을지 묻는다.
    값은 모두 tools.detail(DB 원문)이고, 원문에 없는 항목은 없다고 말한다 (서류 · 중복 수혜 등을 지어내지 않는다)"""
    after = _after_buttons(state, profiles.is_member(user))
    if not action.text:
        shown = _shown(state)
        if not shown:
            result = _ask_topic(store, cid, state, (NO_TARGET, ASK_TOPIC))
            result["status"] = "replied"
            return result
        last = shown[-len(state.get("last") or shown):]  # 방금 보인 칸만 버튼으로
        return _ask(store, cid, state, "which", [WHICH], [(state["results"][n - 1], f"{n}. {name}") for n, name in last],
                    topics=action.topics)
    info = tools.detail(action.text, action.topics, tools.profile_for(user, job=_job(state)), today)
    state["focus"] = action.text  # 다음 "그거"는 이 정책
    if info is None:
        result = _ask(store, cid, state, "after", [GONE], after)
        result["status"] = "replied"
        return result
    missing = f"공고 원문에 없어요. {info['agency'] or '운영 기관'}에 확인해 주세요."
    rows = [f"{label}: {value or missing}" for label, value in info["rows"]]
    rows.append(f"링크: {info['link']}" if info["link"] else "링크: 정보 없음 (온통청년에서 정책명으로 찾아 주세요)")
    if info["agency"]:
        rows.append(f"운영 기관: {info['agency']}")
    result = _card(_ask(store, cid, state, "after", [DETAIL_HEAD], after), [{"title": info["name"], "lines": rows}])
    day = tools.data_date()
    result["reply"]["notices"] = [NOTICE] + ([f"정책 정보는 {day} 기준이에요."] if day else [])
    return result


def _save(store, cid, state, policy_no, user=None):
    """[N번 저장]: 핸들러는 DB 에 쓰지 않는다 → save_policies 로 돌려주면 엔진이 대화 저장과 같은 트랜잭션에서
    policies.saved.save_all 로 저장한다 (7단계). 같은 질문의 나머지 버튼은 그대로 다시 보인다"""
    name = tools.policy_name(policy_no)
    state["saved"] = [*(state.get("saved") or []), policy_no]
    options = _after_buttons(state, profiles.is_member(user))
    result = _ask(store, cid, state, "after", [SAVED.format(name=name)], options)
    result["status"] = "replied"
    result["save_policies"] = [policy_no]
    return result


def unsaved(state, policy_nos):
    """엔진이 저장하지 못한 정책 (save_all 결과) → 저장 성공 안내를 실패 안내로 바꿀 줄과 고친 상태. 상태는 부르는 쪽이 저장한다"""
    state = {**state, "saved": [n for n in state.get("saved") or [] if n not in policy_nos]}
    return [SAVE_FAILED.format(name=tools.policy_name(n)) for n in policy_nos], state


def _end(store, cid, line=ENDED):
    store.save_agent_state(cid, AGENT, None)  # 정책 상태는 핸들러가 지운다 (라우터는 ended 에서 작업 참조만 푼다)
    return _reply("ended", [line])
