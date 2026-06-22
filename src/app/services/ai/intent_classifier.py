from app.core.logging import get_logger

logger = get_logger(__name__)


class IntentClassifier:
    """
    Быстрый классификатор намерений на основе ключевых слов.

    Архитектурное решение: используем подстроки (substrings) вместо точных совпадений,
    чтобы покрыть разные падежи и спряжения русского языка без подключения
    тяжёлых NLP-библиотек (pymorphy2/spaCy). Это компромисс для MVP.
    """

    INTENTS = {
        "pricing": [
            "цен",
            "стоимост",
            "сколько стоит",
            "прайс",
            "дорого",
            "бюджет",
            "рубл",
            "тысяч",
            " cheaper",
            "дешевл",
        ],
        "booking": [
            "записа",
            "свободн",
            "слот",
            "времени",
            "когда можно",
            "прийт",
            "забронир",
            "очередь",
        ],
        "aftercare": [
            "уход",
            "заживлен",
            "чем мазать",
            "корочк",
            "мыл",
            "маз",
            "пантенол",
            "компрес",
            "пластыр",
            "ухажива",
        ],
        "health": [
            "диабет",
            "беремен",
            "противопоказан",
            "аллерг",
            "болезн",
            "кожн",
            "псориаз",
            "экзем",
            "простуд",
            "температур",
            "давлен",
            "лекарств",
            "антибиотик",
        ],
        "complaint": [
            "недовол",
            "проблем",
            "жалоб",
            "плохо",
            "испорт",
            "крив",
            "не нрави",
            "ужасн",
            "кошмар",
            "вернуть деньг",
            "руку",
        ],
        "portfolio": ["работ", "портфолио", "сти", "пример", "фото", "галере", "эскиз", "образц"],
    }

    @classmethod
    def classify(cls, text: str) -> str:
        text_lower = text.lower()
        for intent, keywords in cls.INTENTS.items():
            if any(keyword in text_lower for keyword in keywords):
                logger.info("intent_classified", intent=intent, text_snippet=text[:30])
                return intent

        logger.debug("intent_ambiguous", text_snippet=text[:30])
        return "ambiguous"
