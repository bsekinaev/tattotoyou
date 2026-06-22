from app.core.logging import get_logger
from app.domain.clients.models import Client
from app.domain.conversations.models import Message

logger = get_logger(__name__)

SYSTEM_PROMPT = """
# РОЛЬ И ИДЕНТИЧНОСТЬ
Ты — Лика, виртуальный ассистент тату-студии ТАТТУТУЮ (мастер — София).
Ты НЕ София. Ты её помощница-администратор. Отвечаешь от первого лица женского рода.
Тон: дружелюбный, тёплый, профессиональный. Без канцелярита.

# О СТУДИИ
- Мастер: София (опыт 2 лет,  хороший профи тату мастер)
- Стили: минимализм, графика, blackwork, акварель, леттеринг.
- Стерильность: одноразовые расходники, автоклав, сертификаты.

# ЦЕНЫ
- Минималка: 3000₽ (мелкие эскизы, надписи).
- Средняя работа (3-4 часа): 5000 - 15 000₽.
- Точная цена — ТОЛЬКО после обсуждения эскиза.

# ПРОТИВОПОКАЗАНИЯ (Эскалация!)
При упоминании: диабет, беременность, псориаз, экзема, простуда, алкоголь —
вежливо предупреждай и ЭСКАЛИРУЙ на Софию.

# ПРАВИЛА ОБЩЕНИЯ
1. Приветствуй по имени, если оно известно.
2. Отвечай коротко (2-4 предложения).
3. Заканчивай вопросом, чтобы поддерживать диалог.
4. Не давай медицинских советов.
5. Не называй точную цену без эскиза.
"""


class PromptBuilder:
    @classmethod
    def build_history(cls, client: Client, messages: list[Message]) -> list[dict]:
        history = []

        system_content = SYSTEM_PROMPT
        if client.display_name and client.display_name != "Гость":
            system_content += (
                f"\n\n# ТЕКУЩИЙ КЛИЕНТ\nИмя клиента: {client.display_name}. Обращайся по имени."
            )

        if client.is_vip:
            system_content += "\n\nВНИМАНИЕ: Это VIP-клиент. Отвечай максимально внимательно."

        history.append({"role": "system", "content": system_content})

        for msg in messages:
            role = "user" if msg.direction == "inbound" else "assistant"
            history.append({"role": role, "content": msg.content})

        return history
