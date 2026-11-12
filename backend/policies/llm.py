"""임베딩 호출기. embedder() → (모델 이름, embed(texts) → 벡터 목록). 가짜/실제 전환은 라우터의 CHAT_LLM 과 같다"""
import hashlib
import json
import os
import random
import urllib.error
import urllib.request

from django.conf import settings

DIMENSIONS = 1536
URL = "https://api.openai.com/v1/embeddings"


class EmbedFailed(Exception):
    """이번 호출이 실패했다. 키는 메시지에 넣지 않는다"""


def fake_embed(texts):
    """같은 문장 = 같은 벡터. 네트워크 없이 테스트한다"""
    def vector(text):
        rng = random.Random(hashlib.sha256(text.encode()).digest())
        return [rng.uniform(-1, 1) for _ in range(DIMENSIONS)]
    return [vector(text) for text in texts]


def _embedding_env(name):
    """설정 누락도 임베딩 실패로 처리한다. 실제 값은 오류에 담지 않는다."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise EmbedFailed(f"{name} 설정이 필요해요")
    return value


def live_embed(texts):
    body = {"model": _embedding_env("EMBEDDING_MODEL"), "input": texts}
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {_embedding_env('OPENAI_API_KEY')}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)["data"]
    except urllib.error.HTTPError as error:
        raise EmbedFailed(f"HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
        raise EmbedFailed(type(error).__name__) from None
    return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]


def cached(embed):
    """같은 문장을 두 번 임베딩하지 않는다 (채점의 두 방식, 필요 여러 개를 한 번에 미리 부를 때)"""
    memo = {}

    def run(texts):
        missing = [text for text in dict.fromkeys(texts) if text not in memo]
        if missing:
            memo.update(zip(missing, embed(missing)))
        return [memo[text] for text in texts]
    return run


def embedder(live=None):
    if live is None:
        live = getattr(settings, "CHAT_LLM", "fake") == "live"
    return (_embedding_env("EMBEDDING_MODEL"), live_embed) if live else ("fake", fake_embed)


def chain(system, complete, parser=None):
    """LangChain 체인: 프롬프트(system + 사용자 입력 {text}) | LLM | 출력 파서. 읽기 · 행동 고르기가 쓴다.
    LLM 자리는 라우터와 같은 호출기 complete(system, user) -> str 를 Runnable 로 감싼다 (팀 메모: langchain-core 만, ChatOpenAI 없음).
    system 은 템플릿으로 읽지 않는다 — 프롬프트 속 JSON 예시의 { } 를 변수로 오해하지 않게"""
    from langchain_core.messages import SystemMessage
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda

    prompt = ChatPromptTemplate.from_messages([SystemMessage(content=system), ("human", "{text}")])
    model = RunnableLambda(lambda value: complete(*(message.content for message in value.to_messages())))
    return prompt | model | (parser or StrOutputParser())


def responses(name, schema, max_output_tokens, live=None):
    """체인의 LLM 자리에 넣을 complete(system, user) 또는 None. 가짜 모드(CHAT_LLM=fake) · 키 없음은 None → 부르는 쪽이 규칙으로.
    schema = 답 모양(Pydantic 모델의 JSON 스키마). OpenAI 가 처음부터 이 모양으로 쓰게 한다"""
    if live is None:
        live = getattr(settings, "CHAT_LLM", "fake") == "live"
    key, model = os.environ.get("OPENAI_API_KEY"), os.environ.get("LLM_MODEL")
    if not live or not key or not model:
        return None
    from chat.llm.openai_rest import SERVICE_STOP, ResponsesClient

    return ResponsesClient(model, key, None, max_output_tokens=max_output_tokens, schema=lambda system: (name, schema),
                           stop_on=SERVICE_STOP).complete
