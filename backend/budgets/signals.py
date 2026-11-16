"""budgets 신호 (11/13).
예산이 승인 상태로 저장되면 그 주기의 쿠션 배정액을 '보호 중인 쿠션' 기록에 맞춘다 (R27, cushion.record_approved_cushion).
일반 승인(api.budget_approve) · 대화형 조정(adjustment_service) · 분배 실패 복구(design_recovery_plans)가 모두
budget.status = "approved" 후 save 하므로 여기 한 곳에서 처리한다 — 승인 경로마다 따로 부르지 않는다 (검수 P1).
같은 예산은 한 번만 반영하고(request_key) 같은 주기는 차액만 남겨서, 여러 번 save 돼도 결과가 같다"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Budget


@receiver(post_save, sender=Budget, dispatch_uid="budgets.record_cushion_on_approve")
def record_cushion_on_approve(sender, instance, created=False, raw=False, **kwargs):
    # 처음부터 approved 로 만드는 경우(시드 · 테스트)는 항목이 아직 없어 차액 계산이 맞지 않으므로 건너뛴다.
    # 실제 승인은 항목을 먼저 만든 초안 · 검수 예산을 approved 로 바꿔 save 한다
    if raw or created or instance.status != "approved" or instance.approved_at is None:
        return
    from .cushion import record_approved_cushion
    record_approved_cushion(instance)
