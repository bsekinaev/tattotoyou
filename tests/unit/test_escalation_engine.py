import pytest

from app.services.escalation.engine import EscalationEngine


class TestEscalationEngine:
    @pytest.mark.parametrize(
        "intent,text,should_escalate,reason",
        [
            # Медицинские и негативные интенты всегда эскалируются
            ("health", "у меня диабет", True, "auto_escalation_health"),
            ("complaint", "вы всё испортили", True, "auto_escalation_complaint"),
            # Обычные интенты не эскалируются
            ("pricing", "сколько стоит?", False, ""),
            ("booking", "хочу записаться", False, ""),
            ("aftercare", "чем мазать?", False, ""),
            # Прямые триггеры в тексте переопределяют интент
            ("ambiguous", "позови мастера", True, "user_requested_human"),
            ("ambiguous", "ты тупой бот", True, "user_requested_human"),
            ("ambiguous", "связь с оператором", True, "user_requested_human"),
            ("pricing", "цена? и позови софию", True, "user_requested_human"),
        ],
    )
    def test_should_escalate(self, intent, text, should_escalate, reason):
        result, res_reason = EscalationEngine.should_escalate(intent, text)
        assert result == should_escalate
        assert res_reason == reason