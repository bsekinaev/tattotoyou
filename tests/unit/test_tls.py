"""Регрессионные тесты безопасности исходящих TLS-соединений."""

import importlib
import ssl
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.tls import create_verified_ssl_context


def test_default_tls_context_requires_certificate_and_hostname_verification() -> None:
    context = create_verified_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_custom_ca_bundle_is_passed_to_ssl_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ca_bundle = tmp_path / "gigachat-ca.pem"
    ca_bundle.write_text("test certificate placeholder", encoding="utf-8")
    context = Mock(spec=ssl.SSLContext)
    create_default_context = Mock(return_value=context)
    monkeypatch.setattr(ssl, "create_default_context", create_default_context)

    result = create_verified_ssl_context(ca_bundle)

    assert result is context
    create_default_context.assert_called_once_with(cafile=str(ca_bundle.resolve()))
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_missing_custom_ca_bundle_fails_fast(tmp_path: Path) -> None:
    missing_bundle = tmp_path / "missing.pem"

    with pytest.raises(ValueError, match="Файл CA bundle не существует"):
        create_verified_ssl_context(missing_bundle)


@pytest.mark.asyncio
async def test_gigachat_client_passes_verified_context_to_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gigachat_module = importlib.import_module("app.services.ai.gigachat_client")
    ssl_context = Mock(spec=ssl.SSLContext)
    tls_factory = Mock(return_value=ssl_context)
    http_client = Mock()
    http_client.aclose = AsyncMock()
    http_client_factory = Mock(return_value=http_client)
    monkeypatch.setattr(gigachat_module, "create_verified_ssl_context", tls_factory)
    monkeypatch.setattr(gigachat_module.httpx, "AsyncClient", http_client_factory)

    client = gigachat_module.GigaChatClient()

    tls_factory.assert_called_once_with(gigachat_module.settings.gigachat_ca_bundle)
    assert http_client_factory.call_args.kwargs["verify"] is ssl_context
    await client.close()
    http_client.aclose.assert_awaited_once()