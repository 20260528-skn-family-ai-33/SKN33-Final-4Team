"""온통청년 원본 한 건 → Policy 칸. DB · 외부 호출 없는 순수 함수"""
import hashlib
import html
import json
import re
from datetime import date

from . import codes

PERSONAL_FIELDS = ("sprvsnInstPicNm", "operInstPicNm")  # 담당자 이름: 서비스에 쓰지 않아 저장하지 않는다
VOLATILE_FIELDS = ("inqCnt",)  # 조회수: 바뀌어도 정책이 바뀐 게 아니다
PERIOD = re.compile(r"^(\d{8})\s*~\s*(\d{8})$")
AGE_RANGE = re.compile(r"(\d{2})\s*[~∼～-]\s*(\d{2})\s*세")
TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def _clean(row, key):
    text = TAG.sub(" ", html.unescape(row.get(key) or ""))
    return re.sub(r"\s+", " ", text).strip()


def _code_list(row, key):
    return [c.strip() for c in (row.get(key) or "").split(",") if c.strip()]


def _day(text):
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def periods(text):
    """'20261001 ~ 20261031\\N20261201 ~ 20261231' → 기간 목록. 하나라도 잘못되면 None (모름)"""
    found = []
    for part in (text or "").split("\\N"):
        if not part.strip():
            continue
        match = PERIOD.match(part.strip())
        if not match:
            return None
        try:
            start, end = _day(match[1]), _day(match[2])
        except ValueError:
            return None
        if end < start:
            return None
        found.append((start, end))
    return found or None


def age_range(row):
    """(최소, 최대). 나이 제한 플래그는 믿을 수 없어 숫자만 본다. 숫자가 없거나 원문과 어긋나면 (None, None)"""
    low, high = (int((row.get(key) or "0").strip() or 0) for key in ("sprtTrgtMinAge", "sprtTrgtMaxAge"))
    if not low and not high:
        return None, None
    stated = {(int(a), int(b)) for a, b in AGE_RANGE.findall(row.get("addAplyQlfcCndCn") or "")}
    if stated and (low, high) not in stated:
        return None, None
    return low or None, high or None


def domains(row):
    found = []
    for name in _code_list(row, "lclsfNm"):
        domain = codes.DOMAIN.get(name)
        if domain and domain not in found:
            found.append(domain)
    if any(word in _clean(row, "plcyNm") + _clean(row, "plcyKywdNm") for word in codes.ASSET_WORDS):
        found.append(codes.ASSET)
    return found


def _links(row, *keys):
    return [url for url in (_clean(row, key) for key in keys) if url.startswith(("http://", "https://"))]


def embed_text(row):
    """임베딩 문장: 무엇을 지원하는지만 넣는다. 자격 · 지역 · 기관은 코드가 판단하므로 넣지 않는다"""
    explain, support = _clean(row, "plcyExplnCn"), _clean(row, "plcySprtCn")
    middle = list(dict.fromkeys(_code_list(row, "mclsfNm")))
    lines = [f"정책명: {_clean(row, 'plcyNm')}", f"분류: {' · '.join(domains(row) + middle)}",
             f"키워드: {_clean(row, 'plcyKywdNm')}", f"설명: {explain}"]
    if support != explain:
        lines.append(f"지원 내용: {support}")
    return "\n".join(lines)


def content_hash(raw):
    kept = {k: v for k, v in raw.items() if k not in VOLATILE_FIELDS}
    return hashlib.sha256(json.dumps(kept, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def policy_fields(row):
    """원본 한 건 → Policy 칸 dict"""
    raw = {k: v for k, v in row.items() if k not in PERSONAL_FIELDS}
    apply_type = codes.APPLY_TYPE.get((row.get("aplyPrdSeCd") or "").strip(), "unknown")
    found = periods(row.get("aplyYmd")) if apply_type == "specific" else None
    if apply_type == "specific" and found is None:
        apply_type = "unknown"
    low, high = age_range(row)
    links = _links(row, "aplyUrlAddr")
    income_note = _clean(row, "earnEtcCn")
    return {
        "policy_no": row["plcyNo"].strip(),
        "raw": raw,
        "content_hash": content_hash(raw),
        "name": _clean(row, "plcyNm"),
        "domains": domains(row),
        "apply_type": apply_type,
        "periods": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in found or []],
        "last_apply_end": max(e for _, e in found) if found else None,
        "min_age": low,
        "max_age": high,
        "zip_codes": _code_list(row, "zipCd"),
        "job_codes": _code_list(row, "jobCd"),
        "school_codes": _code_list(row, "schoolCd"),
        "major_codes": _code_list(row, "plcyMajorCd"),
        "special_codes": _code_list(row, "sbizCd"),
        "marriage_code": (row.get("mrgSttsCd") or "").strip(),
        "income_code": (row.get("earnCndSeCd") or "").strip(),
        "income_note": "" if income_note == "-" else income_note,
        "apply_url": links[0] if links else "",
        "ref_urls": _links(row, "refUrlAddr1", "refUrlAddr2"),
        "search_text": " ".join(filter(None, (_clean(row, key) for key in (
            "plcyNm", "plcyKywdNm", "plcyExplnCn", "plcySprtCn", "addAplyQlfcCndCn")))),
        "embed_text": embed_text(row),
    }
