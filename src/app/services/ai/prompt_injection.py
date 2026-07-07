"""Локальный детектор очевидных попыток изменить системные инструкции LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PromptInjectionResult:
    detected: bool
    reason: str = ""
    matched_pattern: str = ""


class PromptInjectionDetector:
    """Консервативный детектор high-signal шаблонов prompt injection."""

    PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
        (
            "override_instructions",
            re.compile(
                r"\b(?:игнорируй|забудь|отмени)\s+(?:все\s+)?(?:предыдущие\s+)?"
                r"(?:инструкции|правила|указания)\b"
            ),
        ),
        (
            "reveal_system_prompt",
            re.compile(r"\b(?:покажи|раскрой|выведи|напечатай)\s+(?:системн\w*\s+)?промпт\b"),
        ),
        (
            "system_role_injection",
            re.compile(r"(?:^|\s)(?:system|developer|assistant)\s*:\s*", re.IGNORECASE),
        ),
        (
            "secret_exfiltration",
            re.compile(
                r"\b(?:покажи|выведи|раскрой)\s+(?:api\s*key|токен\w*|парол\w*|секрет\w*)\b"
            ),
        ),
        (
            "jailbreak",
            re.compile(
                r"\b(?:jailbreak|дан\s+режим|режим\s+dan|developer\s+mode)\b", re.IGNORECASE
            ),
        ),
        (
            "role_override",
            re.compile(
                r"\b(?:притворись|представь)\s*,?\s*что\s+ты\s+(?:не\s+бот|администратор|система)\b"
            ),
        ),
    )

    @classmethod
    def inspect(cls, text: str) -> PromptInjectionResult:
        normalized = text.casefold().replace("ё", "е")
        for reason, pattern in cls.PATTERNS:
            match = pattern.search(normalized)
            if match:
                return PromptInjectionResult(
                    detected=True,
                    reason=reason,
                    matched_pattern=match.group(0)[:120],
                )
        return PromptInjectionResult(detected=False)
