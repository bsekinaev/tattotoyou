import pytest
from app.services.ai.intent_classifier import IntentClassifier

class TestIntentClassifier:
    @pytest.mark.parametrize("text,expected", [
        ("Сколько стоит тату?", "pricing"),
        ("какая цена на рукав", "pricing"),
        ("Хочу записаться на сеанс", "booking"),
        ("когда можно прийти?", "booking"),
        ("чем мазать тату?", "aftercare"),
        ("как ухаживать за корочками", "aftercare"),
        ("У меня диабет, можно тату?", "health"),
        ("беременность и тату", "health"),
        ("вы испортили мне руку!", "complaint"),
        ("недовольна работой", "complaint"),
        ("покажи свои работы", "portfolio"),
        ("Привет, как дела?", "ambiguous"),
        ("абракадабра 123", "ambiguous"),
    ])
    def test_classify_known_intents(self, text, expected):
        assert IntentClassifier.classify(text) == expected

    def test_case_insensitive(self):
        assert IntentClassifier.classify("СКОЛЬКО СТОИТ?") == "pricing"
        assert IntentClassifier.classify("у МЕНЯ ДИАБЕТ") == "health"

    def test_partial_match(self):
        # Ключевое слово внутри длинного предложения
        assert IntentClassifier.classify("Подскажите, а сколько стоит забить всю спину?") == "pricing"