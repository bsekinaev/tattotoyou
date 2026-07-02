"""Минимизация персональных данных перед обращением к внешнему AI API."""

from __future__ import annotations

import re

_EMAIL_PATTERN = re.compile(
    r"(?i)(?<![\w.+-])[a-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_PHONE_CANDIDATE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")

EMAIL_PLACEHOLDER = "[EMAIL СКРЫТ]"
PHONE_PLACEHOLDER = "[ТЕЛЕФОН СКРЫТ]"


def minimize_external_ai_text(text: str) -> str:
    """Скрыть email и телефонные номера перед передачей внешнему провайдеру.

    Оригинальный текст остаётся в собственной базе данных. Телефонным номером
    считается кандидат, содержащий от 10 до 15 цифр; это уменьшает риск
    маскирования цен, дат и коротких идентификаторов.
    """

    without_emails = _EMAIL_PATTERN.sub(EMAIL_PLACEHOLDER, text)

    def replace_phone(match: re.Match[str]) -> str:
        candidate = match.group(0)
        digits = re.sub(r"\D", "", candidate)
        if 10 <= len(digits) <= 15:
            return PHONE_PLACEHOLDER
        return candidate

    return _PHONE_CANDIDATE_PATTERN.sub(replace_phone, without_emails)
