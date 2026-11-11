"""
오래 걸리는 작업 공용 도구 + GET /api/tasks/<id>/ (docs/api/chat.md '오래 걸리는 작업 진행 확인')

- 작업 하나 = chat.AsyncTask 한 줄. 다시 불러오기(finance_sync)가 처음 쓰고, 챗봇 예산 조정 · 추천 예산도 같이 쓴다.
- 1차는 작업을 요청 안에서 끝까지 처리하고 응답만 작업 모양(taskId · status)으로 준다 (10/27 나은 결정).
  나중에 느려지면 run() 부분만 뒤에서 돌리게 바꾸면 되고, API · 화면은 그대로다.
- 11/09: 카드 업종 조회로 카드 불러오기가 1분을 넘겨 온보딩이 멈춰 보였다 (멘토 추천: 상태값을 비동기로).
  다시 불러오기 · 카드 첫 연결의 내역 불러오기는 run_later 로 뒤(스레드)에서 돌리고 바로 답한다. 화면은 taskId 로 진행을 본다.
  · 별도 작업 서버(큐)가 없어서 웹 서버 프로세스의 스레드로 돈다. 서버가 재시작되면 그 작업은 기한(TASK_LEASE) 뒤 stale 로 끝난다
    (아래 R20 만료 처리). 카드번호 · 카드 비밀번호는 이 스레드의 메모리에만 있고 저장하지 않는다.
  · 테스트(settings.TASKS_INLINE)는 지금처럼 요청 안에서 끝까지 돈다 (테스트 트랜잭션 밖 스레드는 자료를 못 본다).
- 같은 회원 · 같은 종류의 작업이 이미 진행 중이면 새로 시작하지 않고 그 작업을 돌려준다 (start_or_reuse).

실행 권한 (R20, 11/06 · 보완) — 서버가 중간에 죽으면 running 기록이 남아 철회 · 탈퇴(check_idle)를 계속 막던 문제
- 조회 기록(SyncRun) 하나는 시작 후 SYNC_LEASE 안에서만 결과를 저장할 수 있다 (연장 없음).
- 작업(AsyncTask)은 lease_expires_at 까지만 새 조회를 시작하고 저장하고 끝낼 수 있다. 연장은 그 작업이 '자기' 조회를
  시작할 때(start_run)와 자기 조회를 저장할 때(claim_run)만, 그것도 그 순간 아직 유효할 때만 한다. 조회는 sync_run.task 로
  작업에 묶인다 → 다른 작업 · 작업 없는 조회 · 다른 회원의 조회는 연장하지 못하고, 기간이 지난 작업은 새 조회로 되살아나지 못한다.
- 유효 = 상태가 queued/running 이고 기간 안. 상태만 보지 않는다 (정리 전이라 running 으로 남은 만료 작업도 막는다).
  확인 지점: 새 조회 시작 · 저장 직전(회원 → 연결 → 조회 → 작업 잠금 뒤) · 작업 시작(run) · 작업 완료(run).
- lease_expires_at 이 빈 옛 기록은 시작(없으면 생성) 시각 + TASK_LEASE 까지만 유효하고 연장하지 않는다. task 가 빈 조회는 어떤 작업도 연장하지 않는다.
- 만료 처리(expire_stale)는 회원 잠금 아래 기간이 지난 것 · 만료된 작업의 조회를 failed(stale) 로 바꾼다. 철회 · 탈퇴는 이것을 따로 커밋한 뒤
  check_idle 로 진행 중 작업을 본다 (뒤에서 409 가 나도 만료 처리는 남음).
- 끝내기(run · 각 저장 함수의 실패 기록)는 아직 진행 중인 기록만 바꾼다 → 만료 · 삭제된 기록을 되살리거나 성공으로 뒤집지 않는다.
- 철회 · 탈퇴 동안 privacy_busy 가 나는 시간: 멈춘 작업은 '자기' 마지막 연장 뒤 TASK_LEASE(10분), 멈춘 조회는 시작 뒤 SYNC_LEASE(10분)까지.
  살아서 기관을 계속 부르는 정상 작업은 끝날 때까지 막는다 (의도된 동작).
"""
import hashlib
import json
import logging
import threading
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat.models import AsyncTask
from finance.models import SyncRun

logger = logging.getLogger(__name__)

ACTIVE = (AsyncTask.Status.QUEUED, AsyncTask.Status.RUNNING)
# 근거: easycodefpy 는 요청 타임아웃 · 재시도가 없고(requests.post 그대로), 기관 하나 = 조회 기록 하나에 호출이 계좌 · 달 수만큼 이어진다.
# 기관 하나의 정상 조회는 수십 초~몇 분이라 10분(기존 STALE_AFTER 와 같은 값)이면 넉넉하다. 넘기면 결과를 버리고 다시 불러오기로 받는다
SYNC_LEASE = timedelta(minutes=10)   # 조회 기록 하나가 결과를 저장할 수 있는 시간 (시작부터)
TASK_LEASE = timedelta(minutes=10)   # 작업이 자기 조회를 시작 · 저장한 뒤 이어서 일할 수 있는 시간
CARD_FIRST_LOAD = "card_first_load"   # 카드사 첫 연결 뒤 청구서 · 승인내역 불러오기 (연결마다 하나, 11/09)
SYNC_TASK_TYPES = {"finance_sync", CARD_FIRST_LOAD}  # 조회 기록을 만들 수 있는 작업 종류

# error_code → 화면 문구 (작업 종류마다 늘려 간다)
ERROR_MESSAGES = {
    "server_error": "잠시 후 다시 시도해 주세요.",
    "stale": "작업이 중간에 멈췄어요. 다시 시도해 주세요.",
}


def input_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def task_item(task):
    """chat.md '오래 걸리는 작업 진행 확인' 응답 모양"""
    error = None
    if task.status == AsyncTask.Status.FAILED:
        code = task.error_code or "server_error"
        error = {"code": code, "message": ERROR_MESSAGES.get(code, ERROR_MESSAGES["server_error"])}
    result = task.result if task.status == AsyncTask.Status.SUCCEEDED else None
    if task.task_type == 'budget_recommend' and isinstance(result, dict):
        result = {k: v for k, v in result.items() if k != '_design_handoff'}
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        "conversation_id": task.conversation_id,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "result": result,
        "error": error,
    }


class WorkExpired(Exception):
    """유효 기간이 지났거나 만료 · 취소 · 삭제된 작업의 늦은 결과 → 저장하지 않는다"""
    code = "stale"


def _deadline(task):
    return task.lease_expires_at or (task.started_at or task.created_at) + TASK_LEASE


def _valid_q(now):
    """DB 에서 '지금 유효한 작업' (상태 + 기간). 옛 기록(lease 없음)은 시작 · 생성 시각 기준. 권한을 주는 판단에는 쓰지 않는다 (잠금 전 조회용)"""
    return Q(status__in=ACTIVE) & (
        Q(lease_expires_at__gt=now)
        | Q(lease_expires_at__isnull=True, started_at__gt=now - TASK_LEASE)
        | Q(lease_expires_at__isnull=True, started_at__isnull=True, created_at__gt=now - TASK_LEASE)
    )


def _mark_stale(rows, now):
    return rows.update(status=AsyncTask.Status.FAILED, error_code="stale", completed_at=now)


# 시간 판단 규칙: 판단에 필요한 행을 모두 잠근 '뒤' 현재 시각을 구한다. 잠금을 기다리는 사이 기한이 지날 수 있어서다.

def _expire_stale(user):
    """회원 잠금을 잡은 트랜잭션 안에서 부른다. 진행 중 작업 행을 잠근 뒤의 시각으로 기간이 지난 작업 · 조회를 failed(stale) 로.
    → 판단에 쓴 시각"""
    active = list(AsyncTask.objects.select_for_update().filter(user=user, status__in=ACTIVE).order_by("pk"))
    now = timezone.now()
    _mark_stale(AsyncTask.objects.filter(pk__in=[task.pk for task in active if _deadline(task) <= now]), now)
    SyncRun.objects.filter(user=user, status="running").filter(
        Q(started_at__lte=now - SYNC_LEASE) | Q(task__isnull=False) & ~Q(task__status__in=ACTIVE),
    ).update(status="failed", error_code="stale", completed_at=now)
    return now


def expire_stale(user):
    """철회 · 탈퇴 전에 부른다. 따로 커밋해서, 뒤에서 다른 정상 작업 때문에 409 가 나도 만료 처리는 남는다"""
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        _expire_stale(user)


def _lock_task(user, task_id):
    """작업 행 잠금 (소유자 · 종류 · 진행 중 상태). 없으면 WorkExpired. 기간은 호출자가 잠금 뒤 시각으로 본다"""
    task = AsyncTask.objects.select_for_update().filter(
        pk=task_id, user=user, task_type__in=SYNC_TASK_TYPES, status__in=ACTIVE,
    ).first()
    if task is None:
        raise WorkExpired("작업이 만료 · 취소되어 계속하지 않았어요.")
    return task


def _renew(task, now):
    if _deadline(task) <= now:
        raise WorkExpired("작업 실행 권한 기한이 지나 계속하지 않았어요.")
    AsyncTask.objects.filter(pk=task.pk).update(lease_expires_at=now + TASK_LEASE)


def start_run(user, task_id=None, **fields):
    """새 조회 기록(running)을 만든다. 작업이 시킨 조회면 작업이 지금 유효할 때만 만들고 그 작업을 연장한다.
    잠금: 회원 → 작업 → (그 뒤 시각으로 판단). 짧게 끝내고 외부 조회는 이 트랜잭션 밖에서"""
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        if task_id is not None:
            task = _lock_task(user, task_id)
            _renew(task, timezone.now())
        return SyncRun.objects.create(user=user, task_id=task_id, status="running", **fields)


def claim_run(run, task_id=None):
    """조회 결과 저장 트랜잭션 안(회원 → 연결 잠금 뒤)에서 부른다. 조회 → 작업 행을 잠근 뒤의 시각으로
    이 조회 · 작업이 아직 저장할 권한이 있는지 확인하고, 있으면 작업을 연장. 없으면 WorkExpired
    (호출자의 실패 처리가 아직 running 인 기록만 failed(stale) 로 바꾼다)"""
    row = SyncRun.objects.select_for_update().filter(pk=run.pk, status="running").first()
    if row is None or row.task_id != task_id:
        raise WorkExpired("조회 기록이 만료 · 삭제되어 저장하지 않았어요.")
    task = _lock_task(row.user_id, task_id) if task_id is not None else None
    now = timezone.now()
    if row.started_at <= now - SYNC_LEASE:
        raise WorkExpired("조회 유효 기간이 지나 저장하지 않았어요.")
    if task is not None:
        _renew(task, now)


def task_valid(task_id):
    """잠금 없는 미리 보기 (남은 기관을 부를지 정할 때만). 실제 권한은 start_run · claim_run 이 잠금 뒤에 다시 본다"""
    return AsyncTask.objects.filter(_valid_q(timezone.now()), pk=task_id).exists()


def finish_run(run_id, **fields):
    """조회 기록 끝내기 — 아직 running 인 기록만 (만료 · 삭제된 기록은 그대로)"""
    return SyncRun.objects.filter(pk=run_id, status="running").update(completed_at=timezone.now(), **fields)


def start_or_reuse(user, task_type, hash_value, reuse_within=None, conversation=None, key_prefix=None):
    """(작업, 새로 만들었는지)
    - 같은 회원 · 같은 종류가 진행 중이면 그 작업 (새로 안 만듦)
    - reuse_within 이 있으면, 같은 입력으로 그 시간 안에 끝난 작업도 그대로 돌려줌
    - 아니면 running 으로 새로 만든다. 판단과 생성은 회원 행을 잠그고 한 번에 한다 (잠금 순서: 회원 먼저)"""
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        now = _expire_stale(user)   # 회원 · 진행 중 작업을 잠근 뒤의 시각. 재사용 판단 · 새 기한도 이 시각 기준
        mine = AsyncTask.objects.filter(user=user, task_type=task_type)
        active = mine.filter(status__in=ACTIVE).order_by("-created_at").first()
        if active is not None:
            return active, False
        if reuse_within is not None:
            recent = mine.filter(
                status=AsyncTask.Status.SUCCEEDED, input_hash=hash_value, completed_at__gte=now - reuse_within,
            ).order_by("-completed_at").first()
            if recent is not None:
                return recent, False
        task = AsyncTask.objects.create(
            user=user, conversation=conversation, task_type=task_type, input_hash=hash_value,
            request_key=f"{key_prefix or task_type}:{uuid.uuid4()}",
            status=AsyncTask.Status.RUNNING, started_at=now, lease_expires_at=now + TASK_LEASE,
        )
    return task, True


def start(user, task_type, hash_value):
    """새 작업을 항상 만든다 (running). 기관마다 따로 도는 작업용 — start_or_reuse 는 회원 · 종류마다 하나만 돌린다"""
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        now = _expire_stale(user)
        return AsyncTask.objects.create(
            user=user, task_type=task_type, input_hash=hash_value, request_key=f"{task_type}:{uuid.uuid4()}",
            status=AsyncTask.Status.RUNNING, started_at=now, lease_expires_at=now + TASK_LEASE,
        )


def active_task(user, task_types):
    """지금 진행 중인(유효한) 작업 중 가장 최근 것. 화면이 '불러오는 중'을 보여 줄 때만 쓴다 (권한 판단에 쓰지 않음)"""
    return AsyncTask.objects.filter(_valid_q(timezone.now()), user=user, task_type__in=task_types).order_by("-created_at").first()


def _start_allowed(task):
    """작업 행을 잠근 뒤의 시각으로 시작해도 되는지. 진행 중인데 기한이 지났으면 그 자리에서 failed(stale)"""
    with transaction.atomic():
        row = AsyncTask.objects.select_for_update().filter(pk=task.pk, status__in=ACTIVE).first()
        if row is None:
            return False
        now = timezone.now()
        if _deadline(row) > now:
            return True
        _mark_stale(AsyncTask.objects.filter(pk=row.pk), now)
        return False


def run(task, work):
    """작업 실행 (1차: 지금 이 요청 안에서). work() 의 반환값이 result. 예외면 failed(server_error)
    - 시작할 때 작업이 유효하지 않으면(상태 · 기간) work() 를 부르지 않는다 (진행 중인데 기한이 지났으면 failed(stale))
    - 끝낼 때도 같은 기준(작업 행을 잠근 뒤의 시각): 기간 안이면 결과 기록, 지났으면 failed(stale), 이미 끝났거나 지워졌으면 그대로
      (실행 중 만료돼도 금융 데이터는 각 저장 경로의 claim_run 이 막는다)"""
    if _start_allowed(task):
        try:
            fields = {"status": AsyncTask.Status.SUCCEEDED, "result": work()}
        except Exception:
            logger.exception("작업 실패 task=%s type=%s", task.task_id, task.task_type)
            fields = {"status": AsyncTask.Status.FAILED, "error_code": "server_error"}
        with transaction.atomic():
            row = AsyncTask.objects.select_for_update().filter(pk=task.pk, status__in=ACTIVE).first()
            now = timezone.now()
            if row is None:
                logger.warning("이미 끝났거나 지워진 작업의 늦은 결과는 버림 task=%s", task.task_id)
            elif _deadline(row) > now:
                AsyncTask.objects.filter(pk=row.pk).update(completed_at=now, **fields)
            else:
                logger.warning("기간이 지난 작업의 늦은 결과는 버림 task=%s", task.task_id)
                _mark_stale(AsyncTask.objects.filter(pk=row.pk), now)
    try:
        task.refresh_from_db()
    except AsyncTask.DoesNotExist:  # 철회 · 탈퇴로 지워짐
        task.status, task.error_code, task.result = AsyncTask.Status.FAILED, "stale", None
    return task


def run_later(task, work):
    """작업을 뒤(스레드)에서 run 하고 바로 돌아온다 (11/09). 결과 · 실패는 run 이 작업 행에 남기고 화면은 GET /api/tasks/<id>/ 로 본다.
    settings.TASKS_INLINE(테스트)이면 지금 이 요청 안에서 끝까지 돈다"""
    if getattr(settings, "TASKS_INLINE", False):
        return run(task, work)

    def target():
        try:
            run(task, work)
        except Exception:   # run 이 work 예외를 받아 두므로 여기는 작업 행 기록 자체가 실패한 경우뿐
            logger.exception("작업 스레드 실패 task=%s type=%s", task.task_id, task.task_type)
        finally:
            connections.close_all()   # 스레드마다 생긴 DB 연결을 남기지 않는다

    threading.Thread(target=target, name=f"task-{task.task_id}", daemon=True).start()
    return task


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def task_detail(request, task_id):
    """GET /api/tasks/<id>/ — 작업 진행 확인. 없는 작업 · 남의 작업은 404"""
    task = AsyncTask.objects.filter(user=request.user, pk=task_id).first()
    if task is None:
        raise Http404
    return Response(task_item(task))
