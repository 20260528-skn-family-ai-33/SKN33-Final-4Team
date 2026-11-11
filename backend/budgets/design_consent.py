"""예산 설계 전용 외부 전송 동의. 구현은 ai_consent 한 곳에 두고 조정 동의와 목적을 나눈다."""
from users import consent_documents as documents

from .ai_consent import ConsentUnavailable, PURPOSES, consent_state as _state, design_consent as ai_consent, has_consent

PURPOSE, HEALTH_PURPOSE = PURPOSES["design"]

__all__ = ["ConsentUnavailable", "PURPOSE", "HEALTH_PURPOSE", "ai_consent", "consent_state",
           "current_document", "has_current_consent"]


def current_document(purpose=PURPOSE):
    return documents.CURRENT[(purpose, None)]


def has_current_consent(user):
    return has_consent(user, "design")


def consent_state(user):
    return _state(user, "design")
