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


# 사람들이 줄여 부르는 시도 이름. 광주광역시는 전남과 합쳐져 표에 '전남광주통합특별시'(12)만 있다
SHORT = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "광주": "전남광주통합특별시",
         "광주광역시": "전남광주통합특별시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
         "강원": "강원특별자치도", "강원도": "강원특별자치도", "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도",
         "전라북도": "전북특별자치도", "전남": "전남광주통합특별시", "전라남도": "전남광주통합특별시", "경북": "경상북도",
         "경남": "경상남도", "제주": "제주특별자치도", "제주도": "제주특별자치도"}
_SUFFIX = ("특별자치시", "특별자치도", "특별시", "광역시", "시", "군", "구", "도")


def _bare(word):
    """'해운대구' → '해운대', '수원시' → '수원'. 한 글자가 남으면 그대로 ('중구' 는 '중구')"""
    for suffix in _SUFFIX:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


def place_regions(text):
    """문장 속 지명 → 해당할 수 있는 지역 코드 목록 (넓은 곳 먼저). 시도만 말했으면 시도 하나, '중구'처럼 여러 곳이면 모두.
    하나로 좁히거나 사는 곳과 다른지 묻는 버튼은 6단계가 한다 (4단계는 읽기만)"""
    words = [SHORT.get(word, word) for word in (text or "").split()]
    if not words:
        return []
    *upper, last = words

    def has(parts, word):
        return any(_bare(part) == _bare(word) for part in parts)

    found = [r for r in REGIONS.values() if all(has(r["name"].split(), word) for word in words)]
    # '수원'이면 수원시 아래 구까지 다 맞는다 → 이름 끝 단위가 말한 이름인 곳만 ('수원' → 경기도 수원시)
    found = [r for r in found if _bare(r["name"].split()[-1]) == _bare(last)] or found
    return [r["code"] for r in sorted(found, key=lambda r: (len(r["name"].split()), r["code"]))]
