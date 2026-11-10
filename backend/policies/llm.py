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


def live_embed(texts):
    body = {"model": os.environ["EMBEDDING_MODEL"], "input": texts}
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"})
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
    return (os.environ["EMBEDDING_MODEL"], live_embed) if live else ("fake", fake_embed)
