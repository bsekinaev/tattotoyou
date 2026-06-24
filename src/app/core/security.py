"""Security helpers shared by ingress and administration layers."""

from __future__ import annotations

import hmac


def secrets_match(provided: str | None, expected: str) -> bool:
    """Compare a provided secret with the expected value in constant time."""
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)