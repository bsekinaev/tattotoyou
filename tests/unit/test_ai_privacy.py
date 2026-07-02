"""Проверка минимизации персональных данных перед внешним AI API."""

from unittest.mock import MagicMock

import pytest

from app.domain.clients.models import Client
from app.domain.conversations.models import Message
from app.services.ai.privacy import (
    EMAIL_PLACEHOLDER,
    PHONE_PLACEHOLDER,
    minimize_external_ai_text,
)
from app.services.ai.prompt_builder import PromptBuilder


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Напишите на sonya@example.com", f"Напишите на {EMAIL_PLACEHOLDER}"),
        ("Мой номер +7 (999) 123-45-67", f"Мой номер {PHONE_PLACEHOLDER}"),
        ("Телефон 8 999 123 45 67", f"Телефон {PHONE_PLACEHOLDER}"),
        ("Цена 5000 - 15000 рублей", "Цена 5000 - 15000 рублей"),
        ("Дата 24.06.2026", "Дата 24.06.2026"),
    ],
)
def test_minimize_external_ai_text(source: str, expected: str) -> None:
    assert minimize_external_ai_text(source) == expected


def test_prompt_builder_masks_history_without_mutating_database_message() -> None:
    original = "Свяжитесь со мной: client@example.com, +7 999 111-22-33"
    client = MagicMock(spec=Client)
    client.display_name = "Гость"
    client.is_vip = False

    message = MagicMock(spec=Message)
    message.direction = "inbound"
    message.content = original

    history = PromptBuilder.build_history(client, [message])

    assert history[-1]["content"] == (
        f"Свяжитесь со мной: {EMAIL_PLACEHOLDER}, {PHONE_PLACEHOLDER}"
    )
    assert message.content == original


def test_faq_context_is_also_minimized() -> None:
    client = MagicMock(spec=Client)
    client.display_name = "Гость"
    client.is_vip = False

    history = PromptBuilder.build_with_faq(
        client,
        [],
        [
            {
                "question": "Позвонить +7 999 111-22-33?",
                "answer": "Напишите на studio@example.com",
            }
        ],
    )

    assert PHONE_PLACEHOLDER in history[0]["content"]
    assert EMAIL_PLACEHOLDER in history[0]["content"]
    assert "studio@example.com" not in history[0]["content"]
