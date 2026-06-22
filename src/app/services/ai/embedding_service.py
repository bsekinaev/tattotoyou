"""
Сервис генерации эмбеддингов для семантического поиска.
Используем sentence-transformers для локальной генерации векторов.
"""
import asyncio
from functools import lru_cache
from sentence_transformers import SentenceTransformer
from app.core.logging import get_logger

logger = get_logger(__name__)

# Мультиязычная модель, хорошо работает с русским
# Размерность: 384, размер ~80MB, inference ~5ms на CPU
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    """Ленивая загрузка модели (один раз за процесс)."""
    logger.info("loading_embedding_model", model=_MODEL_NAME)
    return SentenceTransformer(_MODEL_NAME)


async def generate_embedding(text: str) -> list[float]:
    """
    Генерирует нормализованный вектор для текста.
    sentence-transformers синхронный, поэтому оборачиваем в asyncio.to_thread
    чтобы не блокировать event loop.
    """
    model = get_embedding_model()
    # Запускаем синхронный encode в отдельном потоке
    embedding = await asyncio.to_thread(
        model.encode,
        text,
        normalize_embeddings=True,
    )
    return embedding.tolist()