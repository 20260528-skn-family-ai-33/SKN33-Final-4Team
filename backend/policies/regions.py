"""시군구 표 (data/regions.json). 법정동 코드 5자리 표로 만들었고, 폐지된 인천 구 코드는 새 구의 match 에 넣었다"""
import json
from pathlib import Path

_data = json.loads((Path(__file__).parent / "data" / "regions.json").read_text(encoding="utf-8"))
REGIONS = {r["code"]: r for r in _data["regions"]}
KNOWN_CODES = set(REGIONS) | set(_data["retired"])
