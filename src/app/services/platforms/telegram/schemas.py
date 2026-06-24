from pydantic import BaseModel, ConfigDict, Field


class TelegramChat(BaseModel):
    id: int
    type: str
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


class TelegramUser(BaseModel):
    id: int
    is_bot: bool
    first_name: str
    last_name: str | None = None
    username: str | None = None


class TelegramMessage(BaseModel):
    # В Pydantic v2 используем ConfigDict для работы с алиасами
    model_config = ConfigDict(populate_by_name=True)

    message_id: int
    # В JSON от Telegram поле называется "from" (зарезервированное слово в Python)
    # Поэтому мы используем alias="from", а в коде обращаемся к from_user
    from_user: TelegramUser | None = Field(None, alias="from")
    chat: TelegramChat
    date: int
    text: str | None = None


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
