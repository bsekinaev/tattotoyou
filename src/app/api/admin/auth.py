"""Authentication dependency for administration endpoints."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings

ADMIN_API_KEY_HEADER = "X-Admin-Key"
admin_api_key_header = APIKeyHeader(
    name=ADMIN_API_KEY_HEADER,
    auto_error=False,
    description="Private API key for administration endpoints.",
)


def is_valid_admin_api_key(provided_key: str | None, expected_key: str) -> bool:
    """Return whether the supplied admin API key matches the configured key.

    ``hmac.compare_digest`` prevents content-dependent comparison timing for
    keys of the same type. Missing keys are rejected before comparison.
    """
    if not provided_key:
        return False
    return hmac.compare_digest(provided_key, expected_key)


async def require_admin(
    provided_key: Annotated[str | None, Security(admin_api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Reject requests that do not provide the configured administration key."""
    expected_key = settings.admin_api_key.get_secret_value()
    if is_valid_admin_api_key(provided_key, expected_key):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin credentials",
        headers={"WWW-Authenticate": "ApiKey"},
    )