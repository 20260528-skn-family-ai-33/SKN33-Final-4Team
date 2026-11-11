"""가짜 해석 (LLM 대역, CHAT_LLM = "fake" 기본값) — 정해 둔 문장만 이해한다. 챗봇 엔진 · 로컬 채팅이 같이 쓴다
DB · 네트워크 · 설정 변경 없음. 실제 LLM 은 chat.llm.live.LiveReading (명시적으로 켤 때만). spending_fixture/fake_reading.py 에서 옮김 (MEMO 25-12)
"""
import json

from chat.interpretation.legacy import SAID, RoleFakeLLM
from spending_analysis import agent_loop as spending_agent_loop
from spending_analysis import claims as spending_claims
from spending_analysis import reading as spending_reading

def hypothesis(metric, target, unavailable=None):
    return {"metric": metric, "target": target, "unavailable": unavailable}


# 가짜 해석에 더 넣는 문장 (기존 대역 SAID 에 없는 정책 요청 · 예시용)
EXTRA_SAID = {
    "청년 정책 추천해줘": {"intents": ["policy"]},
    "소비 분석해줘": {"intents": ["spending"], "target": "overview"},
    "왜 식비가 늘었어?": {"intents": ["spending"], "explain": True, "categories": ["FOOD"], "refers_to": "previous"},
    "이번 달 3개월 식비": {"intents": ["spending"], "period": "this_month", "months": 3, "categories": ["FOOD"]},
}
# 소비 형식으로만 답하는 가짜 해석 (통합 형식에 없는 칸 — 세부 지출 · 이번 달 포함 · 별칭)
SPENDING_SAID = {
    # 범위·근거 회귀를 외부 호출 없이 서비스 화면에서도 확인할 고정 입력
    "지난달 식비는 왜 많이 나왔어?": {"mine": True, "explain": True, "explain_kind": "composition",
                                  "period": "last_month", "period_said": "지난달", "categories": ["FOOD"]},
    "지난주 식비는 왜 많이 나왔어?": {"mine": True, "explain": True, "explain_kind": "composition",
                                  "period": "last_week", "period_said": "지난주", "categories": ["FOOD"]},
    "이번 달 주말 식비 왜 늘었어?": {"mine": True, "explain": True, "explain_kind": "change",
                                "period": "this_month", "period_said": "이번 달", "weekday": ["weekend"], "categories": ["FOOD"]},
    "커피를 자주 사 먹어서 그래?": {"hypothesis": hypothesis("count", "unavailable", "커피"), "mine": True, "explain": True, "explain_kind": "change", "refers_to": "previous"},
    "점심값이 비싸진 거야?": {"hypothesis": hypothesis("average", "slot:lunch"), "mine": True, "explain": True, "explain_kind": "change", "refers_to": "previous"},
    "커피 얼마나 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "커피", "category": "FOOD"}]},
    "이번 달 포함 최근 3개월 식비 어때?": {"mine": True, "target": "overview", "period": "recent", "months": 3,
                                 "include_current": True, "categories": ["FOOD"]},
    "지난달 대비 최근 3개월로 비교해줘": {"mine": True, "target": "spend", "compare": "prev_month", "period": "recent",
                                "months": 3},
    "데이트비는 식비로 볼게": {"mine": True, "alias": {"word": "데이트비", "category": "FOOD"}},
    "이번 달 카드 얼마 긁었지?": {"mine": True, "target": "spend", "payment": "card"},
    "과자서원에서 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"detail": "과자서원", "category": "FOOD"}]},
    "식비는 왜 늘었을까?": {"mine": True, "explain": True, "categories": ["FOOD"], "refers_to": "previous"},
    # B 가설 질문 (MEMO 26) — 직전 결과를 증감 설명으로
    "건당 금액이 올라서야?": {"hypothesis": hypothesis("average", "scope"), "mine": True, "explain": True, "explain_kind": "change", "refers_to": "previous"},
    "배달 때문이야?": {"hypothesis": hypothesis("amount", "online:배달앱"), "mine": True, "explain": True, "explain_kind": "change", "refers_to": "previous"},
    "제일 많이 쓴 데는 왜 많이 나왔을까?": {"mine": True, "explain": True, "refers_to": "previous"},
    # 범용 조회 (MEMO 23절 2단계) — 기대값은 data/expected_query.json (make_expected_query.py 가 따로 계산)
    "주말 식비 얼마 썼어?": {"mine": True, "target": "spend", "weekday": ["weekend"], "categories": ["FOOD"]},
    "자주 간 가게 3곳 알려줘": {"mine": True, "measure": "count", "group_by": "merchant", "top_n": 3},
    "밤에 쓴 돈 얼마야?": {"mine": True, "target": "spend", "time_slot": ["night"]},
    "야식으로 얼마 썼어?": {"mine": True, "target": "spend", "time_slot": ["late_night"]},
    "이번 달 제일 큰 지출 뭐야?": {"mine": True, "measure": "max"},
    "하루 평균 얼마 썼어?": {"mine": True, "measure": "daily_avg"},
    "과자서원 몇 번 갔어?": {"mine": True, "measure": "count", "categories": [{"detail": "과자서원", "category": "FOOD"}]},
    "배달 몇 번 시켰어?": {"mine": True, "measure": "count", "categories": [{"detail": "배달", "category": "FOOD"}]},
    "지난달 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "last_month", "categories": ["FOOD"]},
    "계좌이체로 쓴 돈 얼마야?": {"mine": True, "target": "spend", "payment": "bank"},
    "만 원 넘게 쓴 거 몇 번이야?": {"mine": True, "measure": "count", "amount_min": 10000},
    "식비 한 번에 평균 얼마 써?": {"mine": True, "measure": "avg", "categories": ["FOOD"]},
    "요일별 소비 보여줘": {"mine": True, "group_by": "weekday"},
    "시간대별로 보여줘": {"mine": True, "group_by": "time_slot"},
    "예산 밤 시간대로 보여줘": {"mine": True, "target": "budget_left", "time_slot": ["night"]},
    "이번 주 얼마 썼어?": {"mine": True, "target": "spend", "period": "this_week", "period_said": "이번 주"},
    "지난주 식비 얼마야?": {"mine": True, "target": "spend", "period": "last_week", "period_said": "지난주", "categories": ["FOOD"]},
    "8월 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "month", "month": 8, "period_said": "8월",
                       "categories": ["FOOD"]},
    "10월 3일부터 10일까지 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "10-03",
                                "date_to": "10-10", "period_said": "10월 3일부터 10일까지"},
    "5월 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "month", "month": 5, "period_said": "5월",
                       "categories": ["FOOD"]},
    "작년 식비 얼마 썼어?": {"mine": True, "target": "spend", "period_said": "작년", "categories": ["FOOD"]},
    "지난주보다 더 썼어?": {"mine": True, "target": "spend", "compare": "other_period", "period_said": "지난주보다"},
    "고정지출 포함해서 보기": {"mine": True, "refers_to": "previous", "include_fixed": True},
    "20일부터 27일까지 쓴 돈 얼마야?": {"mine": True, "target": "spend", "period": "range", "date_from": "20", "date_to": "27",
                               "period_said": "20일부터 27일까지"},
    "25일부터 31일까지 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "25", "date_to": "31",
                             "period_said": "25일부터 31일까지"},
    "30일부터 31일까지 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "30", "date_to": "31",
                             "period_said": "30일부터 31일까지"},
    "주거비 예산 얼마 남았어?": {"mine": True, "target": "budget_left", "categories": ["HOUSING_TELECOM"]},
    "식비랑 여행 예산 얼마 남았어?": {"mine": True, "target": "budget_left", "categories": ["FOOD", "TRAVEL_LODGING"]},
    "10월 5일부터 10일까지 카테고리별로 얼마 썼어?": {"mine": True, "target": "spend", "period": "range", "date_from": "10-05",
                                        "date_to": "10-10", "compare": "category", "period_said": "10월 5일부터 10일까지"},
    "엄마가 이번 달 식비 얼마 썼어?": {"mine": True, "target": "spend", "period": "this_month", "period_said": "이번 달",
                              "categories": ["FOOD"], "other_person": True},
    "패션 쇼핑 몇 건 결제했어?": {"mine": True, "measure": "count", "categories": ["FASHION_SHOPPING"]},
    "패션 쇼핑 한 번에 평균 얼마야?": {"mine": True, "measure": "avg", "categories": ["FASHION_SHOPPING"]},
    "가게별 상위 3곳 보여줘": {"mine": True, "group_by": "merchant", "top_n": 3},
    "요일별로 몇 번 결제했어?": {"mine": True, "measure": "count", "group_by": "weekday"},
    "이번 달 결제 몇 건이야?": {"mine": True, "measure": "count", "period": "this_month"},
    "카테고리별로 몇 번 썼어?": {"mine": True, "measure": "count", "group_by": "category"},
}

# 네트워크 없는 서비스 연결 검증용 고정 문장. 일반 자유 대화는 live 설정을 사용한다.
BUDGET_SAID = {
    "식비 예산 줄여줘": [{"category": "FOOD", "amount": None, "amount_mode": None, "direction": "down"}],
    "식비를 5만원 늘리고 싶어요": [{"category": "FOOD", "amount": 50000, "amount_mode": "by", "direction": "up"}],
    "식비가 부족해": [{"category": "FOOD", "amount": None, "amount_mode": None, "direction": "up"}],
    "식비를 5만 원 늘려줘": [{"category": "FOOD", "amount": 50000, "amount_mode": "by", "direction": "up"}],
    "식비를 1만 5천 원 늘려줘": [{"category": "FOOD", "amount": 15000, "amount_mode": "by", "direction": "up"}],
}


# 예산 조정 ↔ 소비 분석 협업 (11/11) — 목표 대화: "식비 5만 원 늘려줘" → (조정안) → "교통은 얼마 남았어?" → "그럼 거기서 빼줘"
BUDGET_SAID["식비 5만 원 늘려줘"] = BUDGET_SAID["식비를 5만 원 늘려줘"]
for _text in ("식비 5만 원 늘려줘", "식비를 5만 원 늘려줘"):  # 라우터에도 예산 조정 요청으로 (기능 버튼 없이 문장으로 시작)
    EXTRA_SAID[_text] = {"intents": ["budget_adjust"], "categories": ["FOOD"], "direction": "up", "amount": 50000, "amount_mode": "by"}
# 조정 중에 끼어드는 조회 질문 — 예산 조정은 not_mine(+ 갈 곳), 소비 분석은 그대로 답한다
SPENDING_SAID.update({
    "교통은 얼마 남았어?": {"mine": True, "target": "budget_left", "categories": ["TRANSPORT_CAR"]},
    "쇼핑은 얼마 남았어?": {"mine": True, "target": "budget_left", "categories": ["FASHION_SHOPPING"]},
    "교통비 얼마 썼어?": {"mine": True, "target": "spend", "categories": ["TRANSPORT_CAR"]},
    "이건 얼마 남았어?": {"mine": True, "target": "budget_left", "refers_to": "unclear"},  # 가리키는 대상은 조정안에서 푼다
})
# 소비 분석에서 본 카테고리를 가리키는 말 — 라우터 · 소비에는 예산 조정 요청, 예산 조정에는 hints.category 를 재원으로
BUDGET_POINTING = ("그럼 거기서 빼줘", "거기서 빼줘", "거기서 가져와", "그거 줄여줘")
for _text in BUDGET_POINTING:
    EXTRA_SAID[_text] = {"intents": ["budget_adjust"]}
# 조정안 카드가 떠 있을 때의 말 (FEEDBACK_PROMPT 가짜 응답: action, category, amount, amount_mode, suggested_target)
FEEDBACK_SAID = {
    "쇼핑 더 줄여도 돼": ("refine_more", "FASHION_SHOPPING", None, None, None),
    "쇼핑 너무 많이 줄였어": ("refine_less", "FASHION_SHOPPING", None, None, None),
    "쇼핑 1만 원 더 줄여줘": ("refine_more", "FASHION_SHOPPING", 10000, "by", None),
    "교통에서 빼줘": ("use_source", "TRANSPORT_CAR", None, None, None),
    "아니 식비 3만 원만 늘릴래": ("change_goal", "FOOD", 30000, "by", None),
    "이대로 해줘": ("accept", None, None, None, None),
    "왜 이렇게 바꿨어?": ("explain", None, None, None, None),
    "뭐가 제일 나아?": ("recommend", None, None, None, None),
}


def _asked_category(text, fallback):
    """조회 질문이 물어본 카테고리 코드 하나 (여러 개 · 전체면 None). 가리키는 말("이건")이면 조정 대상(fallback)"""
    said = SPENDING_SAID[text]
    codes = [c for c in said.get("categories", []) if isinstance(c, str)]
    if len(codes) == 1:
        return codes[0]
    return fallback if not codes and said.get("refers_to") == "unclear" else None


def fake_feedback(user):
    """조정안 카드가 떠 있을 때의 가짜 해석. 정해 두지 않은 말은 호출 실패처럼 (조정안 · 기존 예산 그대로)"""
    data = json.loads(user)
    text, hinted = data["text"], (data.get("hints") or {}).get("category")
    if text in SPENDING_SAID:  # 조회 질문 → 소비 분석으로
        goal = (data.get("primary_request") or {}).get("items") or [{}]
        answer = ("not_mine", _asked_category(text, goal[0].get("category")), None, None, None)
    elif text in BUDGET_POINTING:  # "거기서 빼줘" → 방금 본 카테고리를 재원으로 (없으면 서버가 대상을 되묻는다)
        answer = ("use_source", hinted, None, None, None)
    elif text in FEEDBACK_SAID:
        answer = FEEDBACK_SAID[text]
    else:
        raise TimeoutError("가짜 조정안 해석: 모르는 문장")
    return json.dumps(dict(zip(("action", "category", "amount", "amount_mode", "suggested_target"), answer)), ensure_ascii=False)


# 2026-11-02 사용자 확인 목록. 실제 해석기 규칙이 아니라 오프라인 재현용 정답 입력이다.
SPENDING_SAID.update({
    "배달에 얼마나 썼지?": {"mine": True, "target": "spend", "categories": [{"detail": "배달", "category": "FOOD"}], "refers_to": "previous"},
    "배달에 얼마 썼지?": {"mine": True, "target": "spend", "categories": [{"detail": "배달", "category": "FOOD"}], "refers_to": "previous"},
    "점심엔 몇 번 결제했어?": {"mine": True, "measure": "count", "time_slot": ["lunch"], "refers_to": "previous"},
    "점심엔 몇 건 결제했어?": {"mine": True, "measure": "count", "time_slot": ["lunch"], "refers_to": "previous"},
    "지난달 비교해줘": {"mine": True, "target": "spend", "compare": "prev_month"},
    "이번 달 식비 얼마야?": {"mine": True, "target": "spend", "period": "this_month", "categories": ["FOOD"]},
    "생활비 얼마 남았어?": {"mine": True, "target": "budget_left"},
    "최근 3개월 식비 어때?": {"mine": True, "target": "overview", "period": "recent", "months": 3, "categories": ["FOOD"]},
    "주말에 식비 얼마 썼어?": {"mine": True, "target": "spend", "weekday": ["weekend"], "categories": ["FOOD"]},
    "이번 달 가장 비싸게 결제한 건 뭐야?": {"mine": True, "measure": "max", "period": "this_month"},
    "제일 많이 간 가게 다섯 곳": {"mine": True, "measure": "count", "group_by": "merchant", "top_n": 5},
    "이번 주에 쓴 돈 얼마야?": {"mine": True, "target": "spend", "period": "this_week", "period_said": "이번 주"},
    "20일부터 27일까지 쓴 돈": {"mine": True, "target": "spend", "period": "range", "date_from": "20", "date_to": "27", "period_said": "20일부터 27일까지"},
    "5월 식비 얼마야?": {"mine": True, "target": "spend", "period": "month", "month": 5, "period_said": "5월", "categories": ["FOOD"]},
    "주말에 쇼핑 얼마나 했어?": {"mine": True, "target": "spend", "weekday": ["weekend"], "categories": ["FASHION_SHOPPING"]},
    "그럼 식비는?": {"mine": True, "refers_to": "previous", "categories": ["FOOD"]},
    "데이트비 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"unknown": "데이트비"}]},
    "남자친구가 이번 달 쇼핑에 얼마 썼어?": {"mine": True, "other_person": True, "target": "spend", "period": "this_month", "categories": ["FASHION_SHOPPING"]},
    "엄마 생일 선물로 얼마 썼어?": {"mine": True, "target": "spend", "categories": [{"unknown": "엄마 생일 선물"}]},
    "왜 늘었을까?": {"mine": True, "explain": True, "explain_kind": "change", "refers_to": "previous"},
    "식비 예산 초과했어?": {"mine": True, "target": "budget_left", "categories": ["FOOD"]},
    # 달끼리 비교 (11/10) — 기준일 10/29: 8월과는 같은 날짜까지, 9월 vs 8월은 한 달 전체
    "8월보다 식비 더 썼어?": {"mine": True, "target": "spend", "compare": "month", "compare_month": 8, "categories": ["FOOD"]},
    "9월이랑 8월 식비 비교해줘": {"mine": True, "target": "spend", "period": "month", "month": 9, "compare": "month",
                           "compare_month": 8, "categories": ["FOOD"]},
    "10월이랑 9월 식비 비교해줘": {"mine": True, "target": "spend", "period": "month", "month": 10, "compare": "month",
                            "compare_month": 9, "categories": ["FOOD"]},
    "이번달이랑 9월달 식비 비교해줘": {"mine": True, "followup": "answer", "target": "spend", "period": "this_month",
                                "period_said": "이번달이랑 9월달", "compare": "month", "compare_month": 9, "categories": ["FOOD"]},
    "이번달이랑 8월달 식비 비교해줘": {"mine": True, "followup": "answer", "target": "spend", "period": "this_month",
                                "period_said": "이번달이랑 8월달", "compare": "month", "compare_month": 8, "categories": ["FOOD"]},
    "3월보다 식비 더 썼어?": {"mine": True, "target": "spend", "compare": "month", "compare_month": 3, "categories": ["FOOD"]},
    "카테고리별로 예산 얼마나 남았어?": {"mine": True, "target": "budget_left", "compare": "category"},
    "초과한 카테고리 뭐야?": {"mine": True, "target": "budget_left", "compare": "category"},  # 예산을 넘은 카테고리 (11/11)
})
# 첫 조회 뒤 추가 판단 (MEMO 31) — 판단이 필요한 질문의 해석. 첫 조회는 기존 경로가 이 해석 그대로 한다
FOOD_PREV = {"target": "spend", "compare": "prev_month", "categories": ["FOOD"]}
AGENT_SAID = {
    # 11/10 실제 LLM 평가(verify_agent --live)의 해석값 그대로 — 회귀 기준
    # 비교 + 나누기 + 가설 없는 explain (A1 · F2 첫 질문)
    "지난달보다 식비 늘었는데 어느 시간대에서 많이 늘었어?": {
        **FOOD_PREV, "refers_to": "none", "explain": True, "explain_kind": "change", "group_by": "time_slot", "multi_step": True},
    # 요일별 하루 평균 + 비교, multi_step 없음 (A4)
    "지난달보다 어느 요일에 더 썼어?": {"target": "spend", "compare": "prev_month", "refers_to": "none", "measure": "daily_avg",
                                "group_by": "weekday"},
    # 비교 + 나누기 + multi_step (A2)
    "지난달보다 식비 늘었는데 어느 시간대에서 늘었어?": {**FOOD_PREV, "refers_to": "none", "group_by": "time_slot", "multi_step": True},
    # 남은 예산 + 지난달 소비 비교 (A3) — budget_left + compare 로 읽혔다
    "식비 예산 얼마 남았고 지난달보다 많이 쓰고 있어?": {"target": "budget_left", "compare": "prev_month", "categories": ["FOOD"],
                                       "refers_to": "none", "multi_step": True},
    # 볼 수 없는 달 (F1) — 기존 경로가 기간을 먼저 되묻는다 (판단은 조건이 확정된 뒤)
    "3월보다 식비 늘었으면 어느 시간대인지도 알려줘": {"target": "spend", "compare": "month", "categories": ["FOOD"], "refers_to": "none",
                                         "group_by": "time_slot", "compare_month": 3, "multi_step": True},
    # 일반 이유 질문 (W1) — 기존 증감 근거 + 짧은 서버 설명 (판단 · B 없음)
    "지난달보다 식비 늘었으면 왜 늘었는지도 알려줘": {**FOOD_PREV, "refers_to": "none", "explain": True, "explain_kind": "change"},
    # 이번 달과 지난달을 함께 말한 비교 — 보는 쪽은 이번 달 (11/10 화면: 실제 해석이 period=last_month + compare=prev_month 로 읽어 되물었다)
    "이번달 식비를 지난달과 비교": {**FOOD_PREV, "refers_to": "none", "period_said": "이번달 식비를 지난달과 비교"},
    # 판단이 고르는 multi_step (가짜만) — 나누기 기준을 말하지 않아 서버 요구가 없다
    "지난달보다 식비 늘었으면 어디서 늘었는지도 봐줘": {**FOOD_PREV, "multi_step": True},
    "식비랑 교통비 중에 지난달보다 뭐가 더 늘었어?": {**FOOD_PREV, "categories": ["FOOD", "TRANSPORT_CAR"], "multi_step": True},
}
for _text, _reading in AGENT_SAID.items():
    SPENDING_SAID[_text] = {"mine": True, **_reading}
JUDGE_SAID = {"지난달보다 식비 늘었으면 어디서 늘었는지도 봐줘": ("where_changed", {"by": "time_slot"})}  # needs 가 없는 질문의 정해 둔 선택


def fake_judge(user):
    """추가 판단 가짜 응답: 서버가 본 요구(needs)가 있으면 그 첫 요구, 없으면 정해 둔 선택. 모르는 질문은 호출 실패처럼"""
    data = json.loads(user)
    if data["needs"]:
        need = data["needs"][0]
        tool, args = need["tool"], ({"by": need["by"]} if need.get("by") else None)
    elif data["question"] in JUDGE_SAID:
        tool, args = JUDGE_SAID[data["question"]]
    else:
        raise TimeoutError("가짜 판단: 모르는 질문")
    return json.dumps({"action": "tool", "tool": tool, "args": args, "reason": "질문에 남은 확인"}, ensure_ascii=False)


EXTRA_SAID["식비 예산 5만 원 줄여줘"] = {"intents": ["budget_adjust"], "categories": ["FOOD"], "direction": "down", "amount": 50000, "amount_mode": "by"}


# B 계획 · 답 가짜 (MEMO 26) — 질문마다 정해 둔 출력. 답은 서버가 보낸 주장 목록에서 고른 주장(축 · 순위 · 종류)의 id 와 묶음 이름만 끼운다
PLAN_SAID = {
    "커피를 자주 사 먹어서 그래?": {"queries": [], "reason": "커피 결제 횟수 근거가 있는지 확인한다",
                               "hypothesis": hypothesis("count", "unavailable", "커피")},
    "점심값이 비싸진 거야?": {"queries": [], "reason": "점심만의 건당 평균 근거가 있는지 확인한다",
                          "hypothesis": hypothesis("average", "slot:lunch")},
    "왜 식비가 늘었어?": {"queries": [{"by": "time_slot", "focus": None}], "reason": "늘어난 금액이 어느 시간대에 몰렸는지 전체 순위를 본다",
                      "hypothesis": None},
    "건당 금액이 올라서야?": {"queries": [], "reason": "첫 결과의 건당 평균 주장으로 답할 수 있다",
                       "hypothesis": hypothesis("average", "scope")},
    "배달 때문이야?": {"queries": [{"by": "merchant", "focus": "배달"}], "reason": "배달앱 결제가 있었는지 찾아본다",
                   "hypothesis": hypothesis("amount", "online:배달앱")},
}
# 앞선 보완과 대상·지표 조회를 화면에서 재현하는 고정 질문.
for question, hyp in (
    ("점심을 더 자주 먹어서 그래?", hypothesis("count", "slot:lunch")),
    ("주말마다 쇼핑해서 그런가?", hypothesis("day_average", "day:weekend")),
    ("밤에 많이 먹어서 그래?", hypothesis("amount", "slot:night")),
    ("새로 생긴 가게에서 많이 사서 그래?", hypothesis("amount", "unavailable", "새로 생긴 가게")),
):
    SPENDING_SAID[question] = {"mine": True, "explain": True, "explain_kind": "change", "refers_to": "previous", "hypothesis": hyp}
    PLAN_SAID[question] = {"queries": [], "reason": "질문 대상의 비교 근거를 확인한다", "hypothesis": hyp}

ANSWER_SAID = {  # (문장 틀, 고를 주장) — {id} = 서버 절 자리표시(연결 말만, 26-16). 같은 조건이 여럿이면 마지막(추가 조회) 주장. key = 핵심 요인
    "왜 식비가 늘었어?": [("{id}", {"axis": "scope"}), ("특히 {id}", {"key": True}),
                      ("그다음으로 {id}", {"axis": "time_slot", "rank": 1})],
    "건당 금액이 올라서야?": [("{id}", {"axis": "average"}), ("반면 {id}", {"axis": "count"})],
    "배달 때문이야?": [("{id}", {"kind": "absent"}), ("{id}", {"axis": "scope"})],
}


def fake_plan_answer(system, user):
    """B 계획 · 답 가짜 응답. 정해 둔 질문이 아니면 호출 실패처럼 (계획 → 추가 조회 0개, 답 → 템플릿)"""
    data = json.loads(user)
    if system == spending_claims.PLAN_PROMPT:
        if data["question"] not in PLAN_SAID:
            raise TimeoutError("가짜 계획: 모르는 질문")
        return json.dumps(PLAN_SAID[data["question"]], ensure_ascii=False)
    if data["question"] not in ANSWER_SAID:
        # 고정 해석 시나리오의 답도 실제 서버가 요구한 근거만 선택한다.
        if data["question"] not in PLAN_SAID:
            raise TimeoutError("가짜 답: 모르는 질문")
        ids = list(dict.fromkeys(g[0] for g in data.get("must", []) if g))[:4]
        return json.dumps({"sentences": [{"text": "{" + cid + "}", "claims": [cid]} for cid in ids]})
    sentences = []
    for text, want in ANSWER_SAID[data["question"]]:
        # 전체 증감도 필수 근거다. 핵심 요인은 전체(scope)를 제외한 첫 필수 묶음이다.
        scopes = {c["id"] for c in data["claims"] if c["axis"] == "scope"}
        key = next((g for g in data.get("must", []) if set(g) - scopes), [])
        found = [c for c in data["claims"] if (c["id"] in key if want.get("key") else
                                               all(c.get(k) == v for k, v in want.items()))]
        if found:
            claim = found[-1]
            sentences.append({"text": text.format(id="{" + claim["id"] + "}", subject=claim["subject"]), "claims": [claim["id"]]})
    return json.dumps({"sentences": sentences}, ensure_ascii=False)


class FakeReading(RoleFakeLLM):
    """가짜 해석: 정해 둔 문장만 이해한다(SAID). 모르는 문장은 해석 실패 → 라우터 실패 문구. 설명 LLM 은 없다(템플릿)"""
    def __init__(self):
        super().__init__({**SAID, **EXTRA_SAID})
        self.missed = 0

    def __call__(self, system, user, timeout=None):  # timeout: 실제 해석기와 같은 모양 (가짜는 기다리지 않음)
        if system == spending_agent_loop.PROMPT:
            self.calls.append({"role": "fake_judge"})
            return fake_judge(user)
        from budgets.adjustment_core import interpretation as budget_reading
        from budgets.adjustment_core.financial_conversation import DIALOGUE_PROMPT, FEEDBACK_PROMPT
        if system == FEEDBACK_PROMPT:
            self.calls.append({"role": "budget_feedback"})
            return fake_feedback(user)
        if system in (budget_reading.PROMPT, DIALOGUE_PROMPT):
            payload = json.loads(user)
            text, hinted = payload["text"], (payload.get("hints") or {}).get("category")
            intents = self.said.get(text, {}).get("intents", [])
            other = text in SPENDING_SAID or bool(intents) and "budget_adjust" not in intents
            pointing = text in BUDGET_POINTING
            if text not in BUDGET_SAID and not other and not pointing:
                self.missed += 1
                raise TimeoutError("가짜 예산 해석: 모르는 문장")
            self.calls.append({"role": "budget"})
            reading = {"mine": not other, "requests": BUDGET_SAID.get(text, []), "scope": None,
                       "clear_targets": [], "unlock": None, "approval": None, "cancel": False,
                       "reason": None, "unclear": [], "handoff": None, "handoff_category": None}
            if text in SPENDING_SAID:  # 조회 질문 → 소비 분석 + 물어본 카테고리 (가리키는 말이면 조정 중인 항목)
                goal = (payload.get("previous") or {}).get("requests") or [{}]
                reading.update(handoff="spending", handoff_category=_asked_category(text, goal[0].get("category")))
            if pointing:  # "거기서 빼줘": 방금 본 카테고리(hints)를 재원으로. 본 게 없으면 대상을 되묻게 한다
                reading.update(sources=[hinted] if hinted else [], destinations=[], excluded=[], funding_scope=None,
                               action=None, use_available=None, reference="previous",
                               unclear=[] if hinted else ["어느 항목에서 가져올지 알려 주세요."])
            if system == DIALOGUE_PROMPT:
                reading = {"reading": reading, "reply": "조정할 때 꼭 유지하고 싶은 예산이나 원하는 방법이 있나요?"}
            return json.dumps(reading, ensure_ascii=False)
        if system in (spending_claims.PLAN_PROMPT, spending_claims.ANSWER_PROMPT):
            self.calls.append({"role": "fake_plan" if system == spending_claims.PLAN_PROMPT else "fake_answer"})
            return fake_plan_answer(system, user)
        text = json.loads(user)["text"]
        if text in SPENDING_SAID:  # 라우터에는 소비 요청으로, 소비 해석에는 소비 형식 그대로
            self.calls.append({**json.loads(user), "role": "fake"})
            data = SPENDING_SAID[text] if system == spending_reading.PROMPT else {"intents": ["spending"]}
            return json.dumps(data, ensure_ascii=False)
        if text not in self.said:
            self.missed += 1
        return super().__call__(system, user)
