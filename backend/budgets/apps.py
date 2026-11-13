from django.apps import AppConfig


class BudgetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "budgets"

    def ready(self):
        from . import signals  # noqa: F401 — 예산 승인 때 보호 중인 쿠션 기록 (signals.py)
