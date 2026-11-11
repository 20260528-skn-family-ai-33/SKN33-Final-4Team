"""LLM 보충 문장 검사 — 답변 조합(narrate) · B 답(claims)이 같이 쓴다 (11/10)

서버 문장(자리표시)으로 질문에 다 답하지 못할 때만 LLM 이 숫자 없는 문장을 1~2개 덧붙인다.
숫자 · 금액 · 비율은 서버 문장에만 둔다. 보충이 규칙을 어기면 보충만 버리고 나머지 답은 그대로 쓴다.
"""
import re

REASONS = ("uncovered", "next_step")  # uncovered = 질문 일부를 서버 문장이 다루지 못함, next_step = 이어서 물어볼 것 안내
MAX_CHARS = 120
MAX_SENTENCES = 2
# 숫자를 쓰지 않아도 금액 · 비율을 말하는 표현 ('만 원' · '삼천원' · '퍼센트' · '두 배' · '절반'). '원래' · '원인'은 금액이 아니다
NUMBER_WORDS = re.compile(r"(십|백|천|만|억)\s*원(?![래인하가])|퍼센트|(두|세|네|몇)\s*배|절반")
# 서비스가 다루지 않는 권유와 원인 단정 (지시문에도 같은 규칙)
BANNED = ("투자", "주식", "코인", "대출을 받", "때문이에요", "때문입니다", "때문이야")

PROMPT_RULES = f"""extra: 위 문장만으로 질문에 다 답하지 못할 때만 1~{MAX_SENTENCES}문장(최대 {MAX_CHARS}자)을 직접 쓴다. 충분하면 null.
- 숫자·금액·비율·횟수를 쓰지 않는다(아라비아 숫자, '만 원' 같은 한글 숫자 모두). 숫자는 서버 문장에만 있다.
- 원인을 단정하지 않고, 많다·적다를 판정하지 않으며, 투자·대출을 권하지 않는다. 자리표시를 쓰지 않는다.
- 해요체 존댓말로 쓴다.
extra_reason: extra가 있으면 uncovered(질문 일부를 서버 문장이 다루지 못함) 또는 next_step(이어서 물어볼 것을 안내), 없으면 null."""


def check(extra, reason):
    """→ (보충 문장 | None, 탈락 이유 목록). 보충이 없으면 (None, [])"""
    if extra is None:
        return None, ([] if reason is None else ["reason_without_extra"])
    if not isinstance(extra, str) or not extra.strip():
        return None, ["format"]
    text = " ".join(extra.split())
    why = []
    if reason not in REASONS:
        why.append("reason")
    if len(text) > MAX_CHARS:
        why.append("too_long")
    if len(re.findall(r"[.?!](?=\s|$)", text)) > MAX_SENTENCES:
        why.append("too_many_sentences")
    if re.search(r"[0-9０-９%％]", text) or NUMBER_WORDS.search(text):
        why.append("number")
    if re.search(r"[{}]", text):
        why.append("placeholder")
    if any(word in text for word in BANNED):
        why.append("banned")
    return (None, why) if why else (text, [])
