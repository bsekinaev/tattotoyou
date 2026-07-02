from pydantic import BaseModel, ConfigDict, Field

TELEGRAM_NAME_MAX_LENGTH = 128
TELEGRAM_USERNAME_MAX_LENGTH = 64
TELEGRAM_CHAT_TYPE_MAX_LENGTH = 32
TELEGRAM_TEXT_MAX_LENGTH = 4096


class TelegramChat(BaseModel):
    id: int
    type: str = Field(min_length=1, max_length=TELEGRAM_CHAT_TYPE_MAX_LENGTH)
    first_name: str | None = Field(default=None, max_length=TELEGRAM_NAME_MAX_LENGTH)
    last_name: str | None = Field(default=None, max_length=TELEGRAM_NAME_MAX_LENGTH)
    username: str | None = Field(default=None, max_length=TELEGRAM_USERNAME_MAX_LENGTH)


class TelegramUser(BaseModel):
    id: int
    is_bot: bool
    first_name: str = Field(min_length=1, max_length=TELEGRAM_NAME_MAX_LENGTH)
    last_name: str | None = Field(default=None, max_length=TELEGRAM_NAME_MAX_LENGTH)
    username: str | None = Field(default=None, max_length=TELEGRAM_USERNAME_MAX_LENGTH)


class TelegramMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_id: int = Field(ge=0)
    from_user: TelegramUser | None = Field(None, alias="from")
    chat: TelegramChat
    date: int = Field(ge=0)
    text: str | None = Field(default=None, max_length=TELEGRAM_TEXT_MAX_LENGTH)


class TelegramUpdate(BaseModel):
    update_id: int = Field(ge=0)
    message: TelegramMessage | None = None
