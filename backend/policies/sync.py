"""정책 적재 · 임베딩. 적재는 검증을 통과한 전체 목록만 한 트랜잭션으로 공개하고, 임베딩은 그 밖에서 바뀐 것만 한다"""
import hashlib
import logging

from django.db import transaction
from django.utils import timezone

from .llm import EmbedFailed
from .models import Policy, PolicySync
from .normalize import policy_fields
from .regions import KNOWN_CODES

log = logging.getLogger(__name__)
MAX_DROP = 0.2  # 직전 성공보다 이만큼 넘게 줄면 수집이 잘못된 것으로 본다
BATCH = 100
UPDATE_FIELDS = [f.name for f in Policy._meta.concrete_fields if f.name not in ("policy_no", "embedding", "embedded_hash")]


class SyncRejected(Exception):
    """받은 목록을 공개하면 안 된다. 기존 데이터는 그대로 둔다"""


def _validate(rows):
    if not isinstance(rows, list) or not rows:
        raise SyncRejected("정책 목록이 비어 있어요.")
    numbers = [(row.get("plcyNo") or "").strip() for row in rows]
    if not all(numbers):
        raise SyncRejected("정책번호가 없는 행이 있어요.")
    if len(set(numbers)) != len(numbers):
        raise SyncRejected("정책번호가 겹치는 행이 있어요.")
    last = PolicySync.objects.filter(status=PolicySync.Status.OK).order_by("-finished_at").first()
    if last and len(rows) < last.received * (1 - MAX_DROP):
        raise SyncRejected(f"직전 {last.received}건보다 너무 줄었어요: {len(rows)}건")


def load(rows, *, source, data_date):
    """전체 목록을 공개한다. 실패하면 이력만 남기고 SyncRejected 를 다시 던진다"""
    sync = PolicySync(source=source, data_date=data_date, status=PolicySync.Status.FAILED,
                      received=len(rows) if isinstance(rows, list) else 0)
    try:
        _validate(rows)
        fields = [policy_fields(row) for row in rows]
    except (SyncRejected, ValueError, TypeError, AttributeError) as error:
        sync.error = str(error)
        sync.finished_at = timezone.now()
        sync.save()
        raise SyncRejected(str(error)) from error

    unknown = {code for item in fields for code in item["zip_codes"]} - KNOWN_CODES
    if unknown:
        log.warning("지역 표에 없는 코드: %s", sorted(unknown))
    with transaction.atomic():
        known = dict(Policy.objects.values_list("policy_no", "content_hash"))
        changed = [Policy(**item) for item in fields if known.get(item["policy_no"]) != item["content_hash"]]
        Policy.objects.bulk_create(changed, batch_size=500, update_conflicts=True,
                                   unique_fields=["policy_no"], update_fields=UPDATE_FIELDS)
        numbers = [item["policy_no"] for item in fields]
        Policy.objects.filter(policy_no__in=numbers, is_active=False).update(is_active=True)
        deactivated = Policy.objects.exclude(policy_no__in=numbers).filter(is_active=True).update(is_active=False)
        sync.status, sync.changed, sync.deactivated = PolicySync.Status.OK, len(changed), deactivated
        sync.unknown_regions, sync.finished_at = len(unknown), timezone.now()
        sync.save()
    return sync


def renormalize(*, save=True):
    """저장된 원본(raw)으로 정리 칸을 다시 만든다. 정리 규칙(normalize.py)을 고쳤을 때 쓴다.
    load() 는 원본이 바뀐 정책만 다시 정리하므로, 규칙만 바뀌면 이 함수가 필요하다. 활성 여부 · 임베딩은 건드리지 않는다
    (임베딩 문장이 바뀐 정책은 embedded_hash 가 맞지 않게 되어 다음 embed_changed 가 다시 만든다).
    → {칸 이름: 바뀐 정책 수}, 바뀐 정책 [(정책, {칸: (전, 후)})]"""
    changed, counts = [], {}
    now = timezone.now()
    for policy in Policy.objects.order_by("pk"):
        fresh = policy_fields(policy.raw)
        diff = {name: (getattr(policy, name), value) for name, value in fresh.items()
                if name != "policy_no" and getattr(policy, name) != value}
        if diff:
            for name, (_, value) in diff.items():
                setattr(policy, name, value)
                counts[name] = counts.get(name, 0) + 1
            policy.updated_at = now  # bulk_update 는 auto_now 를 채우지 않는다
            changed.append((policy, diff))
    if save and changed:
        names = sorted({name for _, diff in changed for name in diff})
        with transaction.atomic():
            Policy.objects.bulk_update([policy for policy, _ in changed], names + ["updated_at"], batch_size=500)
    return counts, changed


def embed_key(model, text):
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def embed_changed(model, embed):
    """임베딩 문장이나 모델이 바뀐 활성 정책만 다시 만든다 → (성공 수, 남은 수). 실패하면 멈추고 남은 건 다음 실행이 한다"""
    todo = [p for p in Policy.objects.filter(is_active=True).only("policy_no", "embed_text", "embedded_hash").order_by("pk")
            if p.embedded_hash != embed_key(model, p.embed_text)]
    done = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        try:
            vectors = embed([p.embed_text for p in batch])
        except EmbedFailed as error:
            log.warning("임베딩 실패: %s", error)
            break
        for policy, vector in zip(batch, vectors):
            policy.embedding, policy.embedded_hash = vector, embed_key(model, policy.embed_text)
        Policy.objects.bulk_update(batch, ["embedding", "embedded_hash"])
        done += len(batch)
    return done, len(todo) - done
