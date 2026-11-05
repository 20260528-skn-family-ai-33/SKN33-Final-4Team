"""CODEF 인증수단 삭제. 원문 응답·connectedId를 저장하거나 로그로 남기지 않는다.

계약: https://github.com/codef-io/codef-python (계정 목록 조회 / 계정 삭제).
목록을 먼저 읽어 이미 지운 기관의 재요청도 안전하게 처리한다.
"""
import json

from . import codef


def registered_accounts(connected_id):
    raw = codef._client().get_account_list(codef._service_type(), {"connectedId": connected_id})
    body = json.loads(raw)
    data = body.get("data")
    # 마지막 인증수단을 삭제한 뒤 목록 조회는 성공 코드 대신 '연결된 계정 없음'을 반환할 수 있다.
    # 이 목록 API의 해당 코드 + 명시적인 빈 전체 목록에만 허용한다. 다른 오류는 삭제 완료로 보지 않는다.
    if (body.get("result", {}).get("code") == "CF-04014"
            and isinstance(data, dict) and data.get("accountList") == []
            and not data.get("hasNext") and not data.get("errorList")):
        return []
    data = codef._check(raw)
    rows = data.get("accountList")
    if not isinstance(rows, list) or data.get("hasNext"):
        raise ValueError("provider_account_list_incomplete")
    result = []
    for row in rows:
        item = {key: row.get(key) for key in ("countryCode", "businessType", "clientType", "loginType")}
        item["organization"] = row.get("organizationCode") or row.get("organization")
        if not all(isinstance(value, str) and value for value in item.values()):
            raise ValueError("provider_account_identity_invalid")
        result.append(item)
    return result


def delete_registered_accounts(connected_id, business_types=None):
    """None이면 회원의 인증수단 전체, 그 외에는 지정 업권만 삭제한다."""
    def wanted(item):
        return business_types is None or item["businessType"] in business_types

    targets = [item for item in registered_accounts(connected_id) if wanted(item)]
    if not targets:
        return
    data = codef._check(codef._client().delete_account(codef._service_type(), {
        "connectedId": connected_id, "accountList": targets,
    }))
    if data.get("errorList"):
        raise ValueError("provider_account_delete_partial")
    if any(wanted(item) for item in registered_accounts(connected_id)):
        raise ValueError("provider_account_delete_unconfirmed")
