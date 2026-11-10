"""시군구 표 (data/regions.json). 법정동 코드 5자리 표로 만들었고, 폐지된 인천 구 코드는 새 구의 match 에 넣었다"""
import json
from pathlib import Path

_data = json.loads((Path(__file__).parent / "data" / "regions.json").read_text(encoding="utf-8"))
REGIONS = {r["code"]: r for r in _data["regions"]}
KNOWN_CODES = set(REGIONS) | set(_data["retired"])


def find_region(text):
    """코드나 이름 일부 → 코드. 이름은 시군구 하나로 좁혀져야 한다 (확인 명령 · 평가 파일용)"""
    if text in REGIONS:
        return text
    found = [r for r in REGIONS.values() if r["final"] and text in r["name"]]
    if len(found) != 1:
        names = ", ".join(f"{r['code']} {r['name']}" for r in found[:10])
        raise ValueError(f"지역을 하나로 정할 수 없어요: {text} ({names or '없음'})")
    return found[0]["code"]
