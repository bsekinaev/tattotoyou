from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Базовый класс для всех ORM-моделей.

    Содержит общие поля, которые есть почти в каждой таблице:
    - created_at: когда запись создана
    - updated_at: когда запись обновлялась

    Эти поля автоматически заполняются через server_default и onupdate.
    """

    # Общее поле created_at для всех наследников
    # server_default=func.now() — значение проставляет сама БД (надёжнее, чем Python)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # updated_at обновляется автоматически при каждом UPDATE
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
