"""Регрессионный набор реальных формулировок клиентов студии."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.ai.intent_classifier import IntentClassifier

CASES_PATH = Path(__file__).parents[1] / "fixtures" / "intent_cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=lambda case: f"{case['expected']}:{case['text'][:40]}",
)
def test_intent_regression_dataset(case: dict[str, str]) -> None:
    result = IntentClassifier.classify_detailed(case["text"])
    assert result.intent == case["expected"], (
        f"text={case['text']!r}; expected={case['expected']}; "
        f"actual={result.intent}; scores={dict(result.scores)}; "
        f"matched={result.matched_rules}"
    )


def test_regression_dataset_has_product_scale() -> None:
    assert len(CASES) >= 150
    intents = {case["expected"] for case in CASES}
    assert intents == {
        "pricing",
        "booking",
        "booking_change",
        "aftercare",
        "health",
        "complaint",
        "portfolio",
        "collaboration",
        "ambiguous",
    }
