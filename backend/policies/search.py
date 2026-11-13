"""정책 검색: 지역 · 마감으로 거른 뒤 벡터 순위와 키워드 순위를 합친다. 자격 판단은 하지 않는다 (5단계 eligibility.py)"""
from collections import defaultdict
from dataclasses import dataclass, replace

from django.db.models import Q
from pgvector.django import CosineDistance

from .llm import cached, embedder
from .models import Policy
from .regions import AREA_MATCH, NATIONWIDE

LIMIT = 30  # 5단계가 자격으로 거르고도 3개가 남도록 넉넉히 넘긴다
RRF_K = 60  # 순위 합치기 상수. 거리와 키워드는 단위가 달라 점수 대신 순위를 더한다
NATIONWIDE_CODES = 200  # 지역 코드가 이만큼 넘으면 전국 정책으로 본다 (실제 데이터는 251 · 256개)


@dataclass
class Hit:
    policy: Policy
    score: float
    distance: float | None  # 임베딩이 아직 없으면 None
    keywords: list[str]  # 이 정책에서 찾은 키워드
    apply_state: str  # open · always · upcoming · unknown
    business_ended: bool  # 사업 종료일이 지났다. 후보에서 빼지 않고 표시만 한다
    nationwide: bool


def candidates(region_code, today):
    """지역에 해당하고 신청이 끝나지 않은 활성 정책. 전국 · 시도 · 시 · 시군구 어느 단계든 받는다
    (시도 · 시면 그 안 어느 시군구 대상 정책이든 — 카드의 '지역' 줄로 대상이 보인다)"""
    not_over = Q(apply_type__in=("always", "unknown")) | Q(apply_type="specific", last_apply_end__gte=today)
    if region_code == NATIONWIDE:
        return Policy.objects.filter(not_over, is_active=True)
    if region_code not in AREA_MATCH:
        raise ValueError(f"지역 표에 없는 코드예요: {region_code}")
    return Policy.objects.filter(not_over, is_active=True, zip_codes__overlap=AREA_MATCH[region_code])


def _apply_state(policy, today):
    if policy.apply_type != "specific":
        return policy.apply_type
    day = today.isoformat()
    return "open" if any(p["start"] <= day <= p["end"] for p in policy.periods) else "upcoming"


def _business_ended(policy, today):
    end = (policy.raw.get("bizPrdEndYmd") or "").strip()
    return len(end) == 8 and end.isdigit() and end < today.strftime("%Y%m%d")


def _keyword_ranks(pool, keyword):
    """이름 · 키워드 칸 일치가 본문 일치보다 앞선다. 같은 칸끼리는 순서를 지어낼 근거가 없어 같은 순위다"""
    head = set(pool.filter(Q(name__icontains=keyword) | Q(raw__plcyKywdNm__icontains=keyword)).values_list("pk", flat=True))
    body = set(pool.filter(search_text__icontains=keyword).values_list("pk", flat=True)) - head
    return {**dict.fromkeys(head, 1), **dict.fromkeys(body, len(head) + 1)}


def search(query, region_code, today, *, keywords=(), limit=LIMIT, embed=None):
    """점수 높은 순의 Hit 목록. 맞는 정책이 없으면 []. 임베딩 실패는 그대로 올린다(EmbedFailed, 추천 없음과 다르다)"""
    pool = candidates(region_code, today)
    vector = (embed or embedder()[1])([query])[0]
    distances = dict(pool.filter(embedding__isnull=False).annotate(distance=CosineDistance("embedding", vector))
                     .order_by("distance", "pk").values_list("pk", "distance"))
    scores = {pk: 1 / (RRF_K + rank) for rank, pk in enumerate(distances, 1)}
    matched = defaultdict(list)
    for keyword in dict.fromkeys(word.strip() for word in keywords if word.strip()):
        for pk, rank in _keyword_ranks(pool, keyword).items():
            scores[pk] = scores.get(pk, 0) + 1 / (RRF_K + rank)
            matched[pk].append(keyword)
    top = sorted(scores, key=lambda pk: (-scores[pk], pk))[:limit]
    policies = Policy.objects.in_bulk(top)
    return [Hit(policy=policies[pk], score=scores[pk], distance=distances.get(pk), keywords=matched[pk],
                apply_state=_apply_state(policies[pk], today), business_ended=_business_ended(policies[pk], today),
                nationwide=len(policies[pk].zip_codes) > NATIONWIDE_CODES) for pk in top]


def search_needs(needs, region_code, today, *, limit=LIMIT, embed=None):
    """필요(query · keywords)마다 따로 검색해 순위를 RRF 로 합친다 (4단계).
    두 고민을 한 문장으로 임베딩하면 벡터가 둘 사이 어딘가를 가리켜 한쪽이 묻히기 때문이다. 필요 하나면 search() 와 같다"""
    if len(needs) == 1:
        return search(needs[0].query, region_code, today, keywords=needs[0].keywords, limit=limit, embed=embed)
    embed = cached(embed or embedder()[1])
    embed([need.query for need in needs])  # 한 번에 미리 부른다
    scores, best = {}, {}
    for need in needs:
        for rank, hit in enumerate(search(need.query, region_code, today, keywords=need.keywords, limit=limit,
                                          embed=embed), 1):
            pk = hit.policy.pk
            scores[pk] = scores.get(pk, 0) + 1 / (RRF_K + rank)
            if pk in best:  # 여러 필요에서 나오면 가장 가까운 거리와 찾은 키워드를 모두 남긴다
                old = best[pk]
                distance = min((d for d in (old.distance, hit.distance) if d is not None), default=None)
                best[pk] = replace(old, distance=distance, keywords=list(dict.fromkeys(old.keywords + hit.keywords)))
            else:
                best[pk] = hit
    top = sorted(scores, key=lambda pk: (-scores[pk], pk))[:limit]
    return [replace(best[pk], score=scores[pk]) for pk in top]
