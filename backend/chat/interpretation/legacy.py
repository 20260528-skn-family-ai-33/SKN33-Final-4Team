"""단계 B 이전(통합 프롬프트) 형식 보존 · 변환 — 테스트 대역과 저장 응답 변환 replay 전용 (서비스 코드는 쓰지 않는다)

- schema.py · llm.py: 기존 통합 형식 · 프롬프트(해시 6a3bb2a3) 그대로 — 평가 기록과 비교하려고 보존
- to_router / to_spending: 통합 형식 JSON → 역할별 형식 JSON. '각 역할 모델이 같은 뜻으로 답했다'고 가정한 기계적 변환이다
  (실제 역할별 프롬프트의 성능을 뜻하지 않는다)
- RoleFakeLLM: 문장별 통합 형식 가짜 추출값(SAID)을 부른 역할의 형식으로 돌려주는 LLM 대역
- view: run_turn 결과 → 분리 전 Decision 과 같은 칸 (기존 테스트 · 평가 기대값을 그대로 비교하려고)
"""
import json
from types import SimpleNamespace

from chat.router_core import reading as router_reading

# 통합 형식에서 소비 해석에만 있는 칸 / 라우터 해석에만 있는 칸
SPENDING_ONLY = ("target", "period", "compare", "months", "all_categories", "refers_to", "explain",
                 "after_budget_change", "confirm", "include_current", "alias")
ROUTER_ONLY = ("intents", "choice", "resume", "direction", "amount", "amount_mode")


def to_router(data):
    """통합 → 라우터 형식: 소비 전용 칸을 뺀다 (모르는 칸은 그대로 → 형식 검사에서 실패)"""
    return {k: v for k, v in data.items() if k not in SPENDING_ONLY} if isinstance(data, dict) else data


def to_spending(data, payload):
    """통합 → 소비 형식: intents 를 mine 으로 바꾼다
    mine = 사용자가 소비를 골랐거나(chosen) · 소비 되묻기의 답 · 취소 · 불분명이거나 · 의도에 소비가 있고 예산 조정이 없을 때.
    의도가 아예 없으면 mine 을 넣지 않는다 (형식 검사에서 실패 — 분리 전 '의도 빠짐' 실패와 같은 뜻)"""
    if not isinstance(data, dict):
        return data
    intents = data.get("intents")
    if payload.get("chosen"):
        mine = True
    elif payload.get("pending") and data.get("followup") in ("answer", "cancel", "unclear"):
        mine = True
    elif intents is None:
        mine = None
    else:
        mine = "spending" in intents and "budget_adjust" not in intents
    if mine is False:
        return {"mine": False}
    out = {k: v for k, v in data.items() if k not in ROUTER_ONLY}
    if mine is not None:
        out["mine"] = mine
    return out


class RoleFakeLLM:
    """LLM 대역: 부른 쪽(라우터 · 소비)을 지시문으로 가려 그 형식으로 답한다. calls 에 입력과 역할을 남긴다"""

    def __init__(self, said=None, broken=False):
        self.said, self.broken, self.calls = said if said is not None else SAID, broken, []

    def __call__(self, system, user):
        payload = json.loads(user)
        role = "router" if system == router_reading.PROMPT else "spending"
        self.calls.append({**payload, "role": role})
        if self.broken:
            raise TimeoutError("LLM 응답 없음")
        if payload["text"] == "JSON 아님":
            return "JSON 아님"
        data = self.said[payload["text"]]
        return json.dumps(to_router(data) if role == "router" else to_spending(data, payload), ensure_ascii=False)


def view(turn):
    """run_turn 결과 → 분리 전 Decision 의 칸 (소비가 처리했으면 소비 결정 + 라우터 전환 안내)"""
    d, r = turn["decision"], turn["result"] or {}
    s = r.get("decision")
    out = {"status": d.status, "intent": d.intent, "items": (), "notices": d.notices, "asking": d.asking,
           "ask_about": d.ask_about, "options": d.options, "reason": d.reason, "skipped": (), "budget_basis": None,
           "explain": None, "request": None, "interpretation": d.reading,
           "handoff": d.handoff if d.agent != "spending" else None}
    if s is not None:
        out.update(status=s.status, intent=s.intent, items=s.items, notices=s.notices + d.notices, asking=s.asking,
                   ask_about=s.ask_about, options=s.options, reason=s.reason, skipped=s.skipped,
                   budget_basis=s.budget_basis, explain=s.explain, request=s.request, interpretation=s.interpretation)
    elif r.get("status") == "failed":
        out.update(status="failed", reason=r.get("error"))
    return SimpleNamespace(**out)


def item(type_, category=None, **months):
    return {"type": type_, "category": category, **months}


# 문장 → 가짜 LLM 이 돌려줄 해석 (실제 LLM 이 이렇게 답한다고 가정)
SAID = {
    # 요청 말투(알려줘 · 보여줘)가 아니라 분석 대상으로 target 을 정한 추출값 (지시문 원칙 대비 사례)
    "의료비 합계 알려줘": {"intents": ["spending"], "target": "spend", "categories": ["MEDICAL_HEALTH"]},
    "의료비 합계 보여줘": {"intents": ["spending"], "target": "spend", "categories": ["MEDICAL_HEALTH"]},
    "의료비 예산 남은 거 알려줘": {"intents": ["spending"], "target": "budget_left", "categories": ["MEDICAL_HEALTH"]},
    "의료비 예산 남은 거 보여줘": {"intents": ["spending"], "target": "budget_left", "categories": ["MEDICAL_HEALTH"]},
    "의료비 쓴 거 어떤지 알려줘": {"intents": ["spending"], "target": "overview", "categories": ["MEDICAL_HEALTH"]},
    "이번 달 합계 알려줘": {"intents": ["spending"], "target": "spend", "period": "this_month"},
    "남은 예산 보여줘": {"intents": ["spending"], "target": "budget_left"},
    "이번 달 소비 흐름 보여줘": {"intents": ["spending"], "target": "overview", "period": "this_month"},
    "쇼핑은?": {"intents": ["spending"], "categories": ["FASHION_SHOPPING"], "refers_to": "previous"},
    "이번 달 얼마 썼어?": {"intents": ["spending"], "target": "spend", "period": "this_month"},
    "식비 얼마 썼어?": {"intents": ["spending"], "target": "spend", "categories": ["FOOD"]},
    "식비 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left", "categories": ["FOOD"]},
    "예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left"},
    "소비 알려줘": {"intents": ["spending"], "target": "overview"},
    "지난달보다 많이 썼어?": {"intents": ["spending"], "target": "spend", "compare": "prev_month"},
    "지난달보다 식비 더 썼어?": {"intents": ["spending"], "target": "spend", "compare": "prev_month",
                             "categories": ["FOOD"]},
    "최근 몇 달 소비 어때?": {"intents": ["spending"], "target": "overview", "period": "recent"},
    "최근 2개월 식비 어때?": {"intents": ["spending"], "target": "overview", "period": "recent", "months": 2,
                          "categories": ["FOOD"]},
    "최근 6개월 식비 어때?": {"intents": ["spending"], "target": "overview", "period": "recent", "months": 6,
                          "categories": ["FOOD"]},
    "최근 2개월 식비랑 교통비 알려줘": {"intents": ["spending"], "target": "overview", "period": "recent",
                              "months": 2, "categories": ["FOOD", "TRANSPORT_CAR"]},
    "지난달 얼마 썼어?": {"intents": ["spending"], "target": "spend", "period": "last_month"},
    "식비 예산 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "categories": ["FOOD"]},
    "소비 분석하고 예산도 줄여줘": {"intents": ["spending", "budget_adjust"], "target": "overview",
                            "direction": "down"},
    "최근 0개월 식비 알려줘": {"intents": ["spending"], "period": "recent", "months": 0, "categories": ["FOOD"]},
    "식비와 교통비 중 뭐가 더 많아?": {"intents": ["spending"], "compare": "category",
                              "categories": ["FOOD", "TRANSPORT_CAR"]},
    "커피값 얼마 썼어?": {"intents": ["spending"], "target": "spend", "categories": [{"unknown": "커피값"}]},
    "최근 2개월 데이트비랑 식비 알려줘": {"intents": ["spending"], "target": "overview", "period": "recent", "months": 2,
                                "categories": [{"unknown": "데이트비"}, "FOOD"]},
    "최근 2개월 데이트비랑 식비랑 간식비 알려줘": {"intents": ["spending"], "target": "overview", "period": "recent",
                                      "months": 2, "categories": [{"unknown": "데이트비"}, "FOOD", {"unknown": "간식비"}]},
    "데이트비 예산 5만 원 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "amount": 50000,
                           "categories": [{"unknown": "데이트비"}]},
    # 예산 잔액 + 지원 안 하는 비교 방식 (카테고리 있음 = 3번 대상, 없음 = 4번 대상)
    "지난달보다 식비 예산 더 남았어?": {"intents": ["spending"], "target": "budget_left", "compare": "prev_month",
                              "categories": ["FOOD"]},
    "지난달보다 예산 더 남았어?": {"intents": ["spending"], "target": "budget_left", "compare": "prev_month"},
    "지난달 식비 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left", "period": "last_month",
                             "categories": ["FOOD"]},
    "식비랑 교통비 중 예산 뭐가 더 남았어?": {"intents": ["spending"], "target": "budget_left", "compare": "category",
                                "categories": ["FOOD", "TRANSPORT_CAR"]},
    "최근 3개월 식비 예산 얼마나 남았어?": {"intents": ["spending"], "target": "budget_left", "period": "recent",
                                 "months": 3, "categories": ["FOOD"]},
    "최근 3개월 예산 얼마나 남았어?": {"intents": ["spending"], "target": "budget_left", "period": "recent", "months": 3},
    "식비 알려줘": {"intents": ["spending"], "target": "overview", "categories": ["FOOD"]},
    "식비 예산을 5만 원 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "categories": ["FOOD"],
                          "amount": 50000, "amount_mode": "by"},
    "식비 예산을 5만 원으로 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "categories": ["FOOD"],
                            "amount": 50000, "amount_mode": "to"},
    # 직전 분석을 이어받는 후속 질문 (명시한 칸만 채우고 나머지는 null)
    "그럼 교통비는?": {"intents": ["spending"], "categories": ["TRANSPORT_CAR"], "refers_to": "previous"},
    "이번 달은?": {"intents": ["spending"], "period": "this_month", "refers_to": "previous"},
    "교통비도": {"intents": ["spending"], "categories": ["TRANSPORT_CAR"], "refers_to": "previous"},
    "예산은?": {"intents": ["spending"], "target": "budget_left", "refers_to": "previous"},
    "이번 달 총소비 알려줘": {"intents": ["spending"], "target": "spend", "period": "this_month", "refers_to": "none"},
    "아까 그거": {"intents": ["spending"], "refers_to": "unclear"},
    "왜 늘었어?": {"intents": ["spending"], "explain": True, "refers_to": "previous"},
    "교통비는 왜 늘었어?": {"intents": ["spending"], "explain": True, "categories": ["TRANSPORT_CAR"],
                     "refers_to": "previous"},
    "예산 좀 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "refers_to": "previous"},
    "이번 달": {"intents": [], "followup": "answer", "period": "this_month"},
    # 전체 범위 전환 · 개월 수만 바꾸기 · 모순 · 더 이전 분석
    "그럼 전체는?": {"intents": ["spending"], "all_categories": True, "refers_to": "previous"},
    "전체 소비는?": {"intents": ["spending"], "all_categories": True, "refers_to": "previous"},
    "전체는?": {"intents": ["spending"], "all_categories": True, "refers_to": "previous"},
    "전체 예산은?": {"intents": ["spending"], "target": "budget_left", "all_categories": True, "refers_to": "previous"},
    "그 기간 전체로 보면?": {"intents": ["spending"], "refers_to": "previous"},  # 기간 범위 → all_categories 아님
    "그럼 3개월은?": {"intents": ["spending"], "months": 3, "refers_to": "previous"},
    "6개월로 보면?": {"intents": ["spending"], "months": 6, "refers_to": "previous"},
    "최근 2개월로 봐줘": {"intents": ["spending"], "period": "recent", "months": 2, "refers_to": "previous"},
    "지난달보다 최근 2개월은?": {"intents": ["spending"], "compare": "prev_month", "period": "recent", "months": 2,
                          "refers_to": "previous"},
    "이번 달 3개월 식비": {"intents": ["spending"], "period": "this_month", "months": 3, "categories": ["FOOD"]},
    "전체랑 식비": {"intents": ["spending"], "all_categories": True, "categories": ["FOOD"]},
    "그 전에 본 거 다시": {"intents": ["spending"], "refers_to": "unclear"},
    # 2단계: 예산 조정 진행 중 · 보류 · 재개
    "그렇게 해줘": {"intents": [], "followup": "answer"},
    # 정책: "소비 알려줘/보여줘" = 전반 분석 5번 (평가 사례 d25 와 같은 추출값)
    "일단 이번 달 소비부터 보여줘": {"intents": ["spending"], "target": "overview", "period": "this_month", "followup": "new"},
    "아까 예산 조정 이어서 하자": {"intents": ["budget_adjust"], "resume": True},
    "교통비 예산도 늘려줘": {"intents": ["budget_adjust"], "direction": "up", "categories": ["TRANSPORT_CAR"]},
    "이어서": {"intents": [], "followup": "answer", "choice": "resume"},
    "새로 할래": {"intents": [], "followup": "answer", "choice": "restart"},
    "예산 조정 취소": {"intents": [], "followup": "answer", "choice": "budget_task"},
    "질문 취소": {"intents": [], "followup": "answer", "choice": "question"},
    "바뀐 예산으로 분석해줘": {"intents": ["spending"], "after_budget_change": True},
    "바뀐 예산으로 식비 남은 거 알려줘": {"intents": ["spending"], "target": "budget_left", "categories": ["FOOD"],
                                "after_budget_change": True},
    "바뀐 예산으로 이번 달 소비 알려줘": {"intents": ["spending"], "target": "overview", "period": "this_month",
                                "after_budget_change": True},
    "재개 형식 틀림": {"intents": ["budget_adjust"], "resume": "yes"},
    "전체 형식 틀림": {"intents": ["spending"], "all_categories": "yes", "refers_to": "previous"},
    "설명 형식 틀림": {"intents": ["spending"], "explain": "yes"},
    "날씨 어때?": {"intents": ["unsupported"]},
    # 대기 중 후속 답변
    "소비 분석": {"intents": [], "followup": "answer", "choice": "spending"},
    "2개월": {"intents": [], "followup": "answer", "months": 2},
    "응": {"intents": [], "followup": "unclear"},
    "식비": {"intents": [], "followup": "answer", "categories": ["FOOD"]},
    "문화여가야": {"intents": [], "followup": "answer", "categories": ["CULTURE_LEISURE"]},
    "응 이번 기간으로": {"intents": [], "followup": "answer", "confirm": True},
    "아니 괜찮아": {"intents": [], "followup": "answer", "confirm": False},
    "음…": {"intents": [], "followup": "unclear"},
    "이제 됐고 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left", "followup": "new"},
    # 형식 오류
    "형식 깨짐": {"intents": ["spending"], "months": "6"},
    "모르는 칸": {"intents": ["spending"], "amount_guess": 1000},
    "모르는 카테고리 코드": {"intents": ["spending"], "categories": ["COFFEE"]},
    "식비 예산 5만 원 조정해줘": {"intents": ["budget_adjust"], "direction": "down", "categories": ["FOOD"],
                          "amount": 50000},
    "5만 원만큼": {"intents": [], "followup": "answer", "amount_mode": "by"},
    "금액 없이 by/to": {"intents": ["budget_adjust"], "direction": "down", "amount_mode": "by"},
    "예산 조정 금액 형식 틀림": {"intents": ["budget_adjust"], "direction": "down", "amount": "5만", "categories": ["FOOD"]},
    "예산 조정 카테고리 형식 틀림": {"intents": ["budget_adjust"], "categories": ["COFFEE"]},
    "복합 의도 카테고리 형식 틀림": {"intents": ["spending", "budget_adjust"], "categories": ["COFFEE"]},
    "데이트비 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left",
                          "categories": [{"unknown": "데이트비"}]},
    "예산 조정": {"intents": [], "followup": "answer", "choice": "budget_adjust"},
    "확인 필요 얼마 썼어?": {"intents": ["spending"], "target": "spend", "categories": ["UNCLASSIFIED"]},
    "확인 필요 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left", "categories": ["UNCLASSIFIED"]},
    "최근 3개월 확인 필요 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left", "period": "recent",
                                   "months": 3, "categories": ["UNCLASSIFIED"]},
    "식비랑 확인 필요랑 기타 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left",
                                    "categories": ["FOOD", "UNCLASSIFIED", "ETC"]},
    "확인 필요 예산 5만 원만큼 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "amount": 50000,
                              "amount_mode": "by", "categories": ["UNCLASSIFIED"]},
    "식비랑 확인 필요 예산 5만 원만큼 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "amount": 50000,
                                  "amount_mode": "by", "categories": ["FOOD", "UNCLASSIFIED"]},
    "기타 예산 줄여줘": {"intents": ["budget_adjust"], "direction": "down", "categories": ["ETC"]},
    "식비랑 교통비 예산 얼마 남았어?": {"intents": ["spending"], "target": "budget_left",
                              "categories": ["FOOD", "TRANSPORT_CAR"]},
    "의도 빠짐": {"target": "spend"},
    "모르는 말 형식 틀림": {"intents": ["spending"], "categories": [{"unknown": 3}]},
}
