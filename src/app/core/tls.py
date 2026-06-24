"""Вспомогательные функции TLS для исходящих HTTPS-интеграций."""

import ssl
from pathlib import Path


def create_verified_ssl_context(ca_bundle: Path | None = None) -> ssl.SSLContext:
    """Создать TLS-контекст с обязательной проверкой сертификата и имени хоста.

    Args:
        ca_bundle: Необязательный путь к PEM-файлу центра сертификации.
            Если путь не задан, используются системные доверенные сертификаты.

    Returns:
        SSL-контекст с минимально допустимой версией TLS 1.2.

    Raises:
        ValueError: Если настроенный PEM-файл не существует.
    """
    if ca_bundle is not None:
        bundle_path = ca_bundle.expanduser().resolve()
        if not bundle_path.is_file():
            raise ValueError(f"Файл CA bundle не существует: {bundle_path}")
        context = ssl.create_default_context(cafile=str(bundle_path))
    else:
        context = ssl.create_default_context()

    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context