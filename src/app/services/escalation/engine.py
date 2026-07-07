"""Правила безопасной передачи диалога живому мастеру."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.core.logging import get_logger
from app.services.ai.intent_classifier import IntentResult
from app.services.ai.prompt_injection import PromptInjectionDetector

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    should_escalate: bool
    reason: str = ""
    risk_level: str = "normal"


class EscalationEngine:
    """Детерминированный safety-gate перед вызовом внешнего ИИ."""

    ESCALATION_INTENTS: Final[set[str]] = {
        "health",
        "complaint",
        "booking_change",
        "collaboration",
    }

    HUMAN_REQUEST_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\b(?:позов\w*|зов\w*|хочу\s+поговорить|связ\w*|переключ\w*)\s+"
        r"(?:с\s+)?(?:софи\w*|сон\w*|мастер\w*|оператор\w*|человек\w*|администратор\w*)\b"
        r"|\b(?:живой\s+человек|ты\s+(?:туп\w*\s+)?бот|жалобн\w*\s+книг\w*)\b"
    )
    MINOR_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\bмне\s+(?:[0-9]|1[0-7])\s*(?:лет|года|год)?\b"
        r"|\b(?:нет\s+18|несовершеннолет\w*|без\s+родител\w*|мама\s+разрешил\w*)\b"
    )
    SENSITIVE_PLACEMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"\b(?:на\s+)?(?:лиц\w*|век\w*|глаз\w*|губ\w*|язык\w*|генитал\w*|интимн\w*)\b"
    )
    SMALL_TALK_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^(?:привет|здравствуй\w*|добрый\s+(?:день|вечер|утро)|спасибо|ок(?:ей)?|понятно|как\s+дела)[!?.\s]*$"
    )

    @classmethod
    def evaluate(
        cls,
        result: IntentResult,
        text: str,
        *,
        is_vip: bool = False,
        low_confidence_threshold: float = 0.6,
    ) -> EscalationDecision:
        normalized = text.casefold().replace("ё", "е").strip()

        injection = PromptInjectionDetector.inspect(text)
        if injection.detected:
            return cls._decision("prompt_injection_detected", "high")

        if cls.HUMAN_REQUEST_PATTERN.search(normalized):
            return cls._decision("user_requested_human", "medium")

        if is_vip:
            return cls._decision("vip_client", "medium")

        if result.intent in cls.ESCALATION_INTENTS:
            return cls._decision(f"auto_escalation_{result.intent}", "high")

        if cls.MINOR_PATTERN.search(normalized):
            return cls._decision("minor_client", "high")

        if cls.SENSITIVE_PLACEMENT_PATTERN.search(normalized):
            return cls._decision("sensitive_placement", "high")

        substantive_unknown = (
            len(normalized) >= 24 or len(normalized.split()) >= 5
        ) and not cls.SMALL_TALK_PATTERN.fullmatch(normalized)
        if result.confidence < low_confidence_threshold and substantive_unknown:
            return cls._decision("low_intent_confidence", "medium")

        return EscalationDecision(False)

    @classmethod
    def should_escalate(
        cls,
        intent: str,
        text: str,
        *,
        confidence: float = 1.0,
        is_vip: bool = False,
        low_confidence_threshold: float = 0.6,
    ) -> tuple[bool, str]:
        """Совместимый wrapper для старых вызовов и тестов."""
        decision = cls.evaluate(
            IntentResult(intent=intent, confidence=confidence, matched_rules=(), scores={}),
            text,
            is_vip=is_vip,
            low_confidence_threshold=low_confidence_threshold,
        )
        return decision.should_escalate, decision.reason

    @staticmethod
    def _decision(reason: str, risk_level: str) -> EscalationDecision:
        logger.warning("escalation_rule_matched", reason=reason, risk_level=risk_level)
        return EscalationDecision(True, reason, risk_level)
