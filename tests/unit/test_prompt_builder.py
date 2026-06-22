import pytest
from unittest.mock import MagicMock
from app.services.ai.prompt_builder import PromptBuilder
from app.domain.clients.models import Client
from app.domain.conversations.models import Message


class TestPromptBuilder:
    def _mock_client(self, name="Гость", is_vip=False):
        client = MagicMock(spec=Client)
        client.display_name = name
        client.is_vip = is_vip
        return client

    def _mock_message(self, direction, content):
        msg = MagicMock(spec=Message)
        msg.direction = direction
        msg.content = content
        return msg

    def test_build_history_injects_client_name(self):
        client = self._mock_client("Александр")
        history = PromptBuilder.build_history(client, [])
        assert "Имя клиента: Александр" in history[0]["content"]
        assert history[0]["role"] == "system"

    def test_build_history_vip_flag(self):
        client = self._mock_client("Мария", is_vip=True)
        history = PromptBuilder.build_history(client, [])
        assert "VIP-клиент" in history[0]["content"]

    def test_build_history_guest_no_name_injection(self):
        client = self._mock_client("Гость")
        history = PromptBuilder.build_history(client, [])
        assert "Имя клиента:" not in history[0]["content"]

    def test_build_history_formats_messages_correctly(self):
        client = self._mock_client()
        messages = [
            self._mock_message("inbound", "Привет"),
            self._mock_message("outbound", "Здравствуйте!"),
        ]
        history = PromptBuilder.build_history(client, messages)

        # System prompt всегда первый
        assert history[0]["role"] == "system"
        # Inbound -> user, Outbound -> assistant
        assert history[1] == {"role": "user", "content": "Привет"}
        assert history[2] == {"role": "assistant", "content": "Здравствуйте!"}