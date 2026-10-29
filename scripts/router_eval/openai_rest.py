"""라우터 평가용 OpenAI Responses API 어댑터 — 표준 라이브러리만 쓴다 (SDK · 의존성 추가 없음)

- router_core 의 complete(system, user) -> str 자리에 끼운다 (ResponsesClient.complete)
- 생성 요청은 보내기 **전에** 시도로 센다. 실패한 시도도 상한(max_calls)에 포함. 자동 재시도 · 다른 모델 · 설정 전환 없음
- 같은 조건에서 되풀이될 오류(인증 · 권한 · 모델 · 요청 설정 · 한도)가 나면 fatal 로 표시 → 남은 요청을 보내지 않는다
- 키 · 헤더는 어디에도 기록하지 않는다. attempts 에는 결과 종류 · 상태 · 사용량 · 시간만 남는다
- 근거(공식 문서 검색, 2026-10-29): gpt-6-luna, POST /v1/responses, Structured Outputs 지원,
  reasoning.effort = none · low · medium(기본) · high · xhigh · max, 최대 출력 128,000,
  max_output_tokens 는 추론 토큰 포함, 잘리면 status=incomplete + incomplete_details.reason, 추론 시 temperature 등은 보내지 않음
"""
import json
import time
import urllib.error
import urllib.request

URL = "https://api.openai.com/v1/responses"
# 같은 조건이면 다시 보내도 똑같이 실패할 오류 → 남은 호출을 쓰지 않고 중단
FATAL = {"auth", "permission", "not_found", "bad_request", "rate_limit"}
HTTP_KINDS = {400: "bad_request", 401: "auth", 403: "permission", 404: "not_found", 429: "rate_limit"}


class CapReached(Exception):
    """호출 상한 또는 앞선 fatal 오류 때문에 보내지 않은 요청"""


class ResponseError(Exception):
    """보낸 요청이 쓸 수 있는 텍스트 없이 끝남. kind 로 원인 구분"""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


class ResponsesClient:
    def __init__(self, model, api_key, max_calls, timeout=30, max_output_tokens=2000, reasoning_effort="low",
                 schema=None, opener=urllib.request.urlopen, on_attempt=None):
        self.model, self.api_key, self.max_calls = model, api_key, max_calls
        self.timeout, self.max_output_tokens, self.reasoning_effort = timeout, max_output_tokens, reasoning_effort
        self.schema, self.opener, self.on_attempt = schema, opener, on_attempt
        self.attempts = []   # 보낸 요청마다 {"kind", "status", "detail", "usage", "latency_ms"}
        self.blocked = 0     # 상한 · fatal 때문에 보내지 않은 요청 수
        self.fatal = None    # 첫 fatal 오류 종류

    def body(self, system, user):
        body = {"model": self.model, "instructions": system, "input": user, "store": False,
                "max_output_tokens": self.max_output_tokens, "reasoning": {"effort": self.reasoning_effort}}
        # schema = 스키마 하나 또는 지시문 → (이름, 스키마) 를 돌려주는 함수 (역할별 해석: 라우터 · 소비 지시문마다 다른 스키마)
        name, schema = self.schema(system) if callable(self.schema) else ("router_interpretation", self.schema)
        if schema:
            body["text"] = {"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}}
        return body

    def complete(self, system, user):
        if self.fatal or len(self.attempts) >= self.max_calls:
            self.blocked += 1
            raise CapReached()
        record = {"kind": None, "status": None, "detail": None, "usage": None, "latency_ms": None}
        self.attempts.append(record)  # 보내기 전에 센다
        if self.on_attempt:
            self.on_attempt()  # 실행을 다시 해도 줄지 않는 호출 장부
        request = urllib.request.Request(
            URL, data=json.dumps(self.body(system, user)).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with self.opener(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as error:
            record["kind"] = HTTP_KINDS.get(error.code, "server" if error.code >= 500 else "http_other")
            record["status"] = error.code
            record["detail"] = _error_detail(error)
            self._fail(record)
        except Exception as error:  # 시간 초과 · 연결 실패 · 응답이 JSON 아님
            record["kind"], record["detail"] = "network", type(error).__name__
            self._fail(record)
        finally:
            record["latency_ms"] = round((time.monotonic() - started) * 1000)
        record["usage"], record["status"] = data.get("usage"), data.get("status")  # 실패 응답이어도 사용량은 남긴다
        texts, refusals = [], []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue  # reasoning 등
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refusals.append(part.get("refusal", ""))
        if data.get("status") == "incomplete":
            record["kind"] = "incomplete"
            record["detail"] = (data.get("incomplete_details") or {}).get("reason")  # 예: max_output_tokens
            self._fail(record)
        if data.get("status") not in (None, "completed"):
            record["kind"], record["detail"] = "response_status", data.get("status")
            self._fail(record)
        if refusals:
            record["kind"] = "refusal"
            self._fail(record)
        if not "".join(texts).strip():
            record["kind"] = "empty_output"
            self._fail(record)
        record["kind"] = "ok"
        return "".join(texts)

    def _fail(self, record):
        if record["kind"] in FATAL:
            self.fatal = record["kind"]
        raise ResponseError(record["kind"])


def _error_detail(error):
    """오류 본문에서 종류 · 코드 · 문제 칸만 (메시지 전문은 남기지 않는다)"""
    try:
        body = json.loads(error.read()).get("error") or {}
    except Exception:
        return None
    return {k: body.get(k) for k in ("type", "code", "param")}
