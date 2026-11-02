"""개발 평가용 교통 적요 규칙. 정답 라벨·확신·검토필요 열은 사용하지 않는다."""
import re

from scripts.spending_classifier.common import normalize_name


RULE_VERSION = "transport_descriptions_v1_20261026"
EXACT_NAMES = {
    "티머니시외버스모바일결제": "intercity_bus_mobile",
    "시외버스승차권": "intercity_bus_ticket",
    "철도승차권발매": "rail_ticket",
    "카카오페이택시": "kakao_taxi",
    "전국고속버스운송사업조합": "express_bus_association",
}


def transport_rule(name, non_consumption):
    """반환값은 규칙 ID 또는 빈 문자열. 소비 여부가 불명확하면 적용하지 않는다."""
    if non_consumption != "N":
        return ""
    normalized = normalize_name(name)
    # 문자열에 상태가 명시된 경우에도 확정 소비로 간주하지 않는다.
    if any(word in normalized for word in ("가승인", "승인취소", "취소", "환불", "충전")):
        return ""
    if re.fullmatch(r"(?:[1-9]|1[0-2])월교통카드이용", normalized):
        return "monthly_transit_charge"
    if re.fullmatch(r"이동의즐거움택시[0-9]+", normalized):
        return "taxi_payment_processor"
    return EXACT_NAMES.get(normalized, "")
