# src/app/services/escalation/engine.py
from app.core.logging import get_logger

logger = get_logger(__name__)


class EscalationEngine:
    """
    Движок принятия решений: нужен ли здесь живой человек (Соня).
    """

    # Интенты, которые НЕЛЬЗЯ доверять ИИ (медицина и негатив)
    ESCALATION_INTENTS = {"health", "complaint"}

    # Прямые триггеры в тексте (расширили для покрытия разных формулировок)
    ESCALATION_KEYWORDS = [
        "хочу софи",
        "позови софи",
        "зови софи",
        "позови мастер",
        "хочу мастер",
        "зови мастер",
        "ты бот",
        "ты туп",
        "ты не настоящ",
        "оператор",
        "связь с человек",
        "живой человек",
        "менеджер",
        "администратор",
        "руководств",
        "жалобн книг",
    ]

    @classmethod
    def should_escalate(cls, intent: str, text: str) -> tuple[bool, str]:
        text_lower = text.lower()

        # 1. Проверка по интенту (медицина и жалобы — всегда к человеку)
        if intent in cls.ESCALATION_INTENTS:
            return True, f"auto_escalation_{intent}"

        # 2. Проверка по прямым триггерам в тексте
        if any(kw in text_lower for kw in cls.ESCALATION_KEYWORDS):
            return True, "user_requested_human"

        return False, ""