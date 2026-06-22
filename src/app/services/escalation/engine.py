from app.core.logging import get_logger

logger = get_logger(__name__)


class EscalationEngine:
    ESCALATION_INTENTS = {"health", "complaint"}

    ESCALATION_KEYWORDS = [
        "хочу софию", "позови мастера", "ты бот", "ты тупая",
        "оператор", "связь с человеком", "живой человек"
    ]

    @classmethod
    def should_escalate(cls, intent: str, text: str) -> tuple[bool, str]:
        text_lower = text.lower()

        if intent in cls.ESCALATION_INTENTS:
            return True, f"auto_escalation_{intent}"

        if any(kw in text_lower for kw in cls.ESCALATION_KEYWORDS):
            return True, "user_requested_human"

        return False, ""