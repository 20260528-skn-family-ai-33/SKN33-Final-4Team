"""3단계 평가: 질문 · 정답 파일로 검색을 채점한다. 키워드 없이 한 번, 사람이 뽑은 키워드로 한 번 본다

정답은 묶음 목록이다. 같은 정책의 재등록 번호는 한 묶음에 넣고, 묶음 안 번호가 하나라도 나오면 그 묶음을 찾은 것으로 본다.
"""
import json
from dataclasses import dataclass
from datetime import date

from . import reading as reading_module
from . import search
from .llm import cached
from .models import Policy
from .reading import Need, ReadFailed, Reading
from .regions import REGIONS, find_region

# 없음 = 맞는 정책이 없는 질문 (정답 묶음 []). 이사 = 문장 속 지역이 사는 곳과 다른 질문 (region 은 최종 검색 지역)
KINDS = ("구체", "막연", "두고민", "없음", "지역", "이사")
FIELDS = {"id", "kind", "query", "region", "keywords", "answers", "note"}
MODES = (("벡터만", False), ("키워드", True))
AUTO = "자동"  # 4단계 질문 읽기(LLM)가 뽑은 검색 문장 · 키워드. 목표는 '키워드'(사람이 뽑음) 점수에 가까워지는 것


class EvalFileError(ValueError):
    def __init__(self, problems):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Question:
    id: str
    kind: str
    query: str
    region: str  # 지역 코드
    keywords: list[str]
    answers: list[set[str]]  # 정답 묶음

    @property
    def draft(self):
        """정답을 아직 안 적은 질문. 채점에서 뺀다"""
        return self.kind != "없음" and not self.answers


@dataclass
class Result:
    question: Question
    mode: str
    ranks: list[int | None]  # 묶음마다 처음 나온 순위 (못 찾으면 None)
    top_distance: float | None  # 1위 거리 ('맞는 정책 없음' 기준을 정할 재료)
    reading: Reading | None = None  # 자동 방식에서 읽은 결과
    read_error: str | None = None  # 자동 방식에서 읽기가 실패했으면 그 종류 (원래 문장으로 벡터만 검색했다)

    @property
    def first(self):
        found = [rank for rank in self.ranks if rank]
        return min(found) if found else None


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _question(item, problems):
    """한 질문의 형식 검사. 문제가 있으면 problems 에 적고 None"""
    label = item.get("id") if isinstance(item, dict) else None
    label = label if isinstance(label, str) and label else "(id 없음)"
    before = len(problems)
    if not isinstance(item, dict):
        problems.append(f"{label}: 질문은 {{...}} 모양이어야 해요")
        return None
    if extra := set(item) - FIELDS:
        problems.append(f"{label}: 모르는 칸 {sorted(extra)} (쓸 수 있는 칸: {sorted(FIELDS)})")
    if item.get("kind") not in KINDS:
        problems.append(f"{label}: kind 는 {' · '.join(KINDS)} 중 하나")
    if not isinstance(item.get("query"), str) or not item["query"].strip():
        problems.append(f"{label}: query(질문 문장)가 비었어요")
    region = None
    try:
        region = find_region(str(item.get("region", "")))
        if not REGIONS[region]["final"]:
            raise ValueError(f"시군구까지 고른 지역이 아니에요: {item['region']}")
    except ValueError as error:
        problems.append(f"{label}: {error}")
    keywords = item.get("keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(word, str) for word in keywords):
        problems.append(f"{label}: keywords 는 [\"단어\", ...] 모양")
    answers = item.get("answers")
    if not isinstance(answers, list) or not all(_strings(group) and group for group in answers):
        problems.append(f"{label}: answers 는 [[\"정책번호\", ...], ...] 모양 (재등록 번호는 같은 [] 안에)")
    elif item.get("kind") == "없음" and answers:
        problems.append(f"{label}: kind 가 없음이면 answers 는 []")
    if len(problems) > before:
        return None
    return Question(id=label, kind=item["kind"], query=item["query"].strip(), region=region,
                    keywords=[word.strip() for word in keywords if word.strip()],
                    answers=[{number.strip() for number in group} for group in answers])


def _check_answers(question, today, problems):
    """정답이 그 지역 · 그 날짜 후보에 들어 있어야 채점할 수 있다"""
    numbers = set().union(*question.answers) if question.answers else set()
    reachable = set(search.candidates(question.region, today).filter(pk__in=numbers).values_list("pk", flat=True))
    known = dict(Policy.objects.filter(pk__in=numbers - reachable).values_list("pk", "is_active"))
    for number in sorted(numbers - reachable):
        reason = "없는 번호" if number not in known else "비활성" if not known[number] else "이 지역 · 기준일 후보가 아님 (지역이 다르거나 마감)"
        problems.append(f"{question.id}: 정답 {number} → {reason}")


def load(text):
    """평가 파일 → (기준일, 질문 목록). 문제는 한꺼번에 모아 EvalFileError 로 알린다"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise EvalFileError([f"JSON 형식 오류: {error.lineno}번째 줄 {error.colno}번째 칸 ({error.msg})"])
    problems = []
    try:
        today = date.fromisoformat(data.get("date", ""))
    except (AttributeError, TypeError, ValueError):
        raise EvalFileError(["date 칸에 기준일을 YYYY-MM-DD 로 적어 주세요"])
    items = data.get("questions")
    if not isinstance(items, list) or not items:
        raise EvalFileError(["questions 칸에 질문 목록을 적어 주세요"])
    questions, seen = [], set()
    for item in items:
        question = _question(item, problems)
        if question is None:
            continue
        if question.id in seen:
            problems.append(f"{question.id}: id 가 겹쳐요")
        seen.add(question.id)
        _check_answers(question, today, problems)
        questions.append(question)
    if problems:
        raise EvalFileError(problems)
    return today, questions


def _result(question, mode, hits, **extra):
    order = {hit.policy.pk: rank for rank, hit in enumerate(hits, 1)}
    ranks = [min((order[n] for n in group if n in order), default=None) for group in question.answers]
    return Result(question, mode, ranks, hits[0].distance if hits else None, **extra)


def run(questions, today, embed, limit=search.LIMIT, complete=None):
    """질문 × 방식의 Result 목록. complete(LLM 호출기)를 주면 '자동' 방식도 본다. 정답 미정 질문은 뺀다.
    임베딩 실패(EmbedFailed)는 그대로 올린다. 읽기 실패는 그 질문만 원래 문장으로 검색하고 read_error 에 남긴다"""
    questions = [q for q in questions if not q.draft]
    embed = cached(embed)
    readings = {}
    if complete:
        for question in questions:
            try:
                readings[question.id] = reading_module.read(question.query, complete)
            except ReadFailed as error:
                readings[question.id] = error
    needs = {}
    for question in questions:
        got = readings.get(question.id)
        needs[question.id] = reading_module.needs_for(got, question.query) if isinstance(got, Reading) \
            else [Need(question.query)]
    embed([q.query for q in questions] + [n.query for q in questions for n in needs[q.id]])  # 한 번에 미리 부른다
    results = []
    for question in questions:
        for mode, use_keywords in MODES:
            hits = search.search(question.query, question.region, today, embed=embed, limit=limit,
                                 keywords=question.keywords if use_keywords else ())
            results.append(_result(question, mode, hits))
        if complete:
            hits = search.search_needs(needs[question.id], question.region, today, embed=embed, limit=limit)
            got = readings[question.id]
            results.append(_result(question, AUTO, hits, reading=got if isinstance(got, Reading) else None,
                                   read_error=None if isinstance(got, Reading) else str(got)))
    return results


def modes(results):
    """결과에 들어 있는 방식 이름 (벡터만 · 키워드 · 자동 순서)"""
    return [mode for mode in [m for m, _ in MODES] + [AUTO] if any(r.mode == mode for r in results)]


def summary(results, mode):
    """정답 있는 질문만 센다. hit3 · hit10 · found(후보 안) · mrr 은 비율, groups 는 찾은 묶음 비율"""
    rows = [r for r in results if r.mode == mode and r.question.answers]
    if not rows:
        return None
    count = len(rows)
    groups = sum(len(r.ranks) for r in rows)
    return {
        "count": count,
        "hit3": sum(bool(r.first and r.first <= 3) for r in rows) / count,
        "hit10": sum(bool(r.first and r.first <= 10) for r in rows) / count,
        "found": sum(r.first is not None for r in rows) / count,
        "mrr": sum(1 / r.first for r in rows if r.first) / count,
        "groups": sum(rank is not None for r in rows for rank in r.ranks) / groups,
    }
