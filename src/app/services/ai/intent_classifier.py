from app.core.logging import get_logger

logger = get_logger(__name__)


class IntentClassifier:
    INTENTS = {
        "pricing": ["цена", "стоимость", "сколько стоит", "прайс", "дорого", "бюджет"],
        "booking": ["записаться", "свободные часы", "когда можно", "слот", "время", "запись"],
        "aftercare": ["уход", "заживление", "чем мазать", "корочки", "мыть"],
        "health": ["диабет", "беременность", "противопоказания", "аллергия", "болезнь", "кожа"],
        "complaint": ["недовольна", "проблема", "жалоба", "плохо", "испортить", "кривая"],
        "portfolio": ["работы", "портфолио", "стили", "примеры", "фото"],
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