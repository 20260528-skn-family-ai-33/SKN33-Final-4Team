"""
API 공통 도구
- 파이썬은 이름을 snake_case(living_used)로, 자바스크립트는 camelCase(livingUsed)로 쓴다.
- 그래서 리액트에 보내기 직전에 이름만 바꿔 준다. (값은 그대로)
"""


def _camel_key(key):
    head, *rest = key.split("_")
    return head + "".join(word.title() for word in rest)


def camel(data):
    """dict·list 안의 모든 키를 camelCase로 바꾼다. 예: {"living_used": 1} → {"livingUsed": 1}"""
    if isinstance(data, dict):
        return {_camel_key(k) if isinstance(k, str) else k: camel(v) for k, v in data.items()}
    if isinstance(data, list):
        return [camel(v) for v in data]
    return data
