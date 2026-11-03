"""설명 경로 공통 문장 검사. 사실·방향은 서버의 완성 문장 안에만 둔다."""
import re

CONNECTIVES = ("그다음으로", "그리고", "그런데", "이어서", "다만", "반면", "특히", "한편", "먼저", "또한", "또")
MAX_CLAUSES = 2


def validate(text, ids, known, prefix="C"):
    if not ids or any(not isinstance(i, str) or i not in known for i in ids):
        return ["claim_id"]
    pattern = rf"\{{({prefix}\d+)\}}"
    marks = re.findall(pattern, text)
    why = []
    if not marks:
        why.append("no_clause")
    if len(marks) > MAX_CLAUSES:
        why.append("too_many_clauses")
    if set(marks) != set(ids) or len(ids) != len(set(ids)) or re.search(rf"\{{(?!{prefix}\d+\}})", text):
        why.append("placeholder")
    bare = re.sub(pattern, " ", text)
    if re.search(r"[0-9%]", bare):
        why.append("number")
    # 단정·완곡한 부정·동기·평가도 허용 목록 하나로 차단한다. 별도의 금지어 예외를 늘리지 않는다.
    if not re.fullmatch(r"(?:\s|[,\.?!~·…()]|" + "|".join(CONNECTIVES) + r")*", bare):
        why.append("free_text")
    return why
