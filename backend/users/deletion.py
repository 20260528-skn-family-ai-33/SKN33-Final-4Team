"""동의 철회 · 회원 탈퇴의 삭제 실행과 실패 복구 (R21)

외부(CODEF) 인증 삭제와 우리 DB 삭제는 한 트랜잭션으로 되돌릴 수 없다. 그래서 단계를 PrivacyDeletion 에 남기고 이어서 한다.
  1) 접수 (api_privacy, 짧은 트랜잭션): 확인 · 비밀번호 · 진행 중 작업 검사 → 기록 생성 커밋
     - 범위 철회: 동의 철회 + purge_scope + 연결 해제까지 이 트랜잭션에서 한다 → 삭제 대기 중에도 그 범위 자료가
       조회 · 집계 · 분석에 쓰이지 않는다. 재시도에 필요한 것(회원 행의 connectedId 암호문 · provider_kinds)은 남는다.
     - 탈퇴: 로그인 · 일반 이용 차단(is_active=False) — 회원 행의 connectedId 가 외부 삭제에 필요해 회원 삭제는 4) 에서
  2) 외부 삭제 (트랜잭션 · 행 잠금 밖): codef_privacy 가 목록을 다시 읽어 남은 대상만 지우고 다시 읽어 확인한다
     → 타임아웃 · 상태 저장 실패 뒤의 재시도도 목록 재조회로 이미 지운 것은 건너뛴다
  3) 외부 삭제 확인 기록 (local_pending)
  4) 마무리 + 완료 기록 (한 트랜잭션): 탈퇴는 user.delete · 세션 삭제, 철회는 남은 connectedId 정리

실행 권한 (중복 실행 방지, 두 겹)
- 회원별 advisory lock(codef_account_lock, 기다리지 않음): 살아 있는 실행이 있으면 다른 실행(요청 · 명령)은 외부 삭제를 시작하지 않는다.
  연결이 끊기면(프로세스 종료) DB 가 바로 풀어 준다.
- 실행 토큰(lease_token) + 만료(lease_expires_at): 권한을 가져간 실행만 단계 · 실패 · 해제를 기록한다 → 만료 뒤 B 가 이어받은 뒤에
  늦게 끝난 A 가 B 의 권한을 비우거나 상태를 덮지 않는다. 만료는 화면의 '처리 중 / 재시도 필요' 구분에도 쓴다.
재시도: 같은 철회 요청을 다시 보내거나(사용자), privacy_deletion_retry 명령(운영 · 탈퇴 뒤 로그아웃 상태, 자동 스케줄 없음 — 수동 실행).
"""
import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException

from config import tasks
from finance import codef_privacy, connected_id
from finance.api_new_connection import codef_account_lock
from finance.models import Connection
from .models import Consent, PrivacyDeletion
from .privacy import PrivacyBusy, check_idle

logger = logging.getLogger(__name__)

LEASE = timedelta(minutes=5)  # 외부 삭제(목록 2번 + 삭제 1번) 한 번에 충분한 시간. 넘기면 다른 실행이 이어받는다
OPEN = (PrivacyDeletion.Status.PROVIDER_PENDING, PrivacyDeletion.Status.LOCAL_PENDING)
KEEP_COMPLETED = timedelta(days=30)  # 완료 기록 보관 기간 (privacy_deletion_retry 가 지난 것을 지운다)


class PrivacyDeleting(APIException):
    status_code = 409
    default_code = "privacy_deleting"
    default_detail = "이전 동의 철회의 데이터 삭제를 마무리하는 중이에요. 삭제가 끝난 뒤 다시 시도해 주세요."


def provider_kinds(user, scope):
    """접수 시점(철회 전)의 동의로 지울 CODEF 업권을 정한다. 은행 인증은 계좌 · 대출 조회가 공유한다"""
    if scope == "all":
        return None
    active = set(Consent.objects.filter(user=user, purpose="finance_inquiry", revoked_at__isnull=True)
                 .exclude(product_scope=scope).values_list("product_scope", flat=True))
    if scope in {"bank", "loan"} and active.intersection({"bank", "loan"}):
        return []
    return ["CD"] if scope == "card" else ["BK"]


def open_deletions(user_id):
    return PrivacyDeletion.objects.filter(user_id=user_id, status__in=OPEN)


def blocked_scopes(user_id):
    """끝나지 않은 삭제가 막는 동의 범위 → 재동의 · 재연결로 삭제 대상(같은 connectedId 의 업권)이 바뀌지 않게"""
    scopes = set()
    for row in open_deletions(user_id):
        kinds = row.provider_kinds
        scopes.add(row.scope)
        if row.scope == "all" or kinds is None or "BK" in kinds:
            scopes |= {"bank", "loan"}
        if row.scope == "all" or kinds is None or "CD" in kinds:
            scopes.add("card")
    return scopes


def state(row):
    """화면 안내용: None(완료 · 없음) · processing(실행 권한이 살아 있음) · retry_required(실패 · 중단 → 다시 시도 가능)"""
    if row is None or row.status == PrivacyDeletion.Status.COMPLETED:
        return None
    if row.lease_expires_at and row.lease_expires_at > timezone.now():
        return "processing"
    return "retry_required"  # 오류 기록 없이 프로세스가 죽은 경우도 권한이 만료되면 여기


def _claim(row):
    """실행 권한이 비었거나 만료된 미완료 기록만 가져간다. → 이 실행의 토큰 (못 가져가면 None)"""
    now, token = timezone.now(), uuid.uuid4()
    taken = PrivacyDeletion.objects.filter(pk=row.pk, status__in=OPEN).filter(
        Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now),
    ).update(lease_expires_at=now + LEASE, lease_token=token, attempts=F("attempts") + 1, attempted_at=now)
    return token if taken else None


def _mine(row, token):
    """이 실행이 아직 권한을 가진 기록 (만료됐어도 아무도 이어받지 않았으면 이 실행 것)"""
    return PrivacyDeletion.objects.filter(pk=row.pk, lease_token=token, status__in=OPEN)


class LeaseLost(Exception):
    """다른 실행이 권한을 이어받음 → 이 실행은 아무것도 기록하지 않고 멈춘다"""


def _delete_provider(row):
    if row.provider_kinds == []:
        return  # 공유 은행 인증은 남은 동의가 쓴다
    user = get_user_model().objects.get(pk=row.user_id)
    cid = connected_id.load(user)
    if cid:
        codef_privacy.delete_registered_accounts(cid, None if row.provider_kinds is None else set(row.provider_kinds))


def _finish_local(row, token):
    User = get_user_model()
    found = User.objects.filter(pk=row.user_id).first()
    if found is not None and row.scope == "all":
        tasks.expire_stale(found)  # 멈춘 작업 기록이 정리를 계속 막지 않게 (R20)
    with transaction.atomic():
        user = User.objects.select_for_update(no_key=True).filter(pk=row.user_id).first()
        if _mine(row, token).filter(status=PrivacyDeletion.Status.LOCAL_PENDING).select_for_update().first() is None:
            raise LeaseLost()
        done = dict(status=PrivacyDeletion.Status.COMPLETED, completed_at=timezone.now(), lease_expires_at=None,
                    lease_token=None, failed_step=None, error_code=None)
        if row.scope == "all":
            if user is not None:
                list(Connection.objects.select_for_update().filter(user=user).order_by("pk"))
                check_idle(user)
                user_id = str(user.pk)
                # 모든 회원 소유 모델이 CASCADE로 모이므로 그 안의 RESTRICT 참조도 함께 삭제된다.
                user.delete()
                for session in Session.objects.all().iterator():
                    if session.get_decoded().get("_auth_user_id") == user_id:
                        session.delete()
            # 겹쳐 접수된 범위 철회도 탈퇴의 전체 삭제에 포함되므로 함께 완료
            open_deletions(row.user_id).update(**done)
            return
        PrivacyDeletion.objects.filter(pk=row.pk).update(**done)  # 로컬 자료는 접수 때 이미 정리됨
        if user is not None and not open_deletions(row.user_id).exists() and not Consent.objects.filter(
                user=user, purpose="finance_inquiry", revoked_at__isnull=True).exists():
            # 모든 조회 동의가 철회되고 외부 인증도 모두 지웠으면 더 쓸 일이 없는 connectedId 암호문을 지운다
            User.objects.filter(pk=user.pk).update(codef_connected_id_ciphertext=None, codef_connected_at=None)


def run(row):
    """실행 권한을 가져온 경우에만 남은 단계를 이어서 한다. → 최신 기록 (다른 실행이 진행 중이면 그대로)"""
    with codef_account_lock(SimpleNamespace(pk=row.user_id), wait=False) as locked:
        token = _claim(row) if locked else None
        if token is not None:
            _run(row, token)
    row.refresh_from_db()
    return row


def _run(row, token):
    row.refresh_from_db()
    step, error = "provider", None
    try:
        if row.status == PrivacyDeletion.Status.PROVIDER_PENDING:
            _delete_provider(row)
            if not _mine(row, token).filter(status=PrivacyDeletion.Status.PROVIDER_PENDING).update(
                    status=PrivacyDeletion.Status.LOCAL_PENDING, provider_deleted_at=timezone.now()):
                raise LeaseLost()
        step = "local"
        _finish_local(row, token)
    except LeaseLost:
        logger.warning("삭제 실행 권한을 다른 실행이 이어받아 멈춤 deletion=%s", row.pk)
        return
    except PrivacyBusy:
        error = "privacy_busy"
    except connected_id.ConnectedIdUnreadable:
        error = "connected_id_unreadable"
    except Exception as exc:
        # 외부 예외 원문에는 인증 · 기관 자료가 있을 수 있어 종류만 남긴다
        logger.warning("삭제 단계 실패 deletion=%s step=%s type=%s", row.pk, step, type(exc).__name__)
        error = "provider_unavailable" if step == "provider" else "local_cleanup_failed"
    if error:
        try:
            _mine(row, token).update(lease_expires_at=None, lease_token=None, failed_step=step, error_code=error)
        except Exception:  # 기록도 못 하면 권한 만료 뒤 재조회로 이어간다
            logger.warning("삭제 실패 기록 실패 deletion=%s", row.pk)


def retry_open(limit=None):
    """운영 재처리: 끝나지 않고 실행 권한이 비었거나 만료된 기록만. 중복 · 동시 실행해도 회원 잠금 + _claim 이 하나만 통과시킨다"""
    now = timezone.now()
    rows = PrivacyDeletion.objects.filter(status__in=OPEN).filter(
        Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now),
    ).order_by("created_at")[:limit]
    return [run(row) for row in rows]


def purge_completed():
    return PrivacyDeletion.objects.filter(status=PrivacyDeletion.Status.COMPLETED,
                                          completed_at__lt=timezone.now() - KEEP_COMPLETED).delete()[0]
