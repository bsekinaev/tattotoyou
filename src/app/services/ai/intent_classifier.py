"""Детерминированная классификация намерений клиента для MVP."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IntentRule:
    """Одно объяснимое правило классификатора."""

    label: str
    pattern: re.Pattern[str]
    weight: int


@dataclass(frozen=True, slots=True)
class IntentResult:
    """Результат классификации с уверенностью и объяснением."""

    intent: str
    confidence: float
    matched_rules: tuple[str, ...]
    scores: Mapping[str, int]


class IntentClassifier:
    """Быстрый rule-based классификатор без ложных совпадений по частям слов.

    Правила используют границы слов и устойчивые фразы. Метод ``classify``
    сохранён для обратной совместимости, а продуктовый код использует
    ``classify_detailed``.
    """

    INTENT_PRIORITY: Final[tuple[str, ...]] = (
        "health",
        "complaint",
        "booking_change",
        "collaboration",
        "pricing",
        "booking",
        "aftercare",
        "portfolio",
    )

    RULES: Final[dict[str, tuple[IntentRule, ...]]] = {
        "pricing": (
            IntentRule("сколько стоит", re.compile(r"\bсколько\s+(?:это\s+)?стоит\b"), 5),
            IntentRule("во сколько обойдется", re.compile(r"\bво\s+сколько\s+обойд\w*\b"), 5),
            IntentRule("какая цена", re.compile(r"\bкакая\s+цена\b"), 5),
            IntentRule("узнать стоимость", re.compile(r"\b(?:узнать|рассчитать)\s+стоимость\b"), 5),
            IntentRule("цена", re.compile(r"\bцен(?:а|у|ы|е|ой|ник|ники)\b"), 3),
            IntentRule("стоимость", re.compile(r"\bстоимост\w*\b"), 3),
            IntentRule("прайс", re.compile(r"\bпрайс\w*\b"), 3),
            IntentRule("бюджет", re.compile(r"\bбюджет\w*\b"), 2),
            IntentRule("рубли", re.compile(r"\b(?:рубл\w*|тысяч\w*)\b"), 2),
            IntentRule("дорого/дешево", re.compile(r"\b(?:дорог\w*|дешев\w*)\b"), 2),
        ),
        "booking": (
            IntentRule("хочу записаться", re.compile(r"\b(?:хочу|можно|как)\s+записа\w*\b"), 5),
            IntentRule(
                "когда можно прийти", re.compile(r"\bкогда\s+можно\s+(?:прийти|приехать)\b"), 5
            ),
            IntentRule(
                "свободные даты", re.compile(r"\bсвободн\w*\s+(?:дат\w*|окн\w*|врем\w*)\b"), 5
            ),
            IntentRule("забронировать", re.compile(r"\bзаброни\w*\b"), 4),
            IntentRule("запись", re.compile(r"\bзапи(?:с|ш)\w*\b"), 3),
            IntentRule(
                "свободно в день", re.compile(r"\bсвободн\w*(?:\s+ли)?\s+(?:в|на)\s+\w+\b"), 4
            ),
            IntentRule(
                "попасть на сеанс",
                re.compile(
                    r"\b(?:как\s+попасть|можно\s+прийти\s+(?:сегодня|завтра|вечером|утром|днем|"
                    r"в\s+\w+|на\s+\w+))\b"
                ),
                4,
            ),
            IntentRule("есть окно", re.compile(r"\bесть\s+окн\w*\b"), 4),
            IntentRule("слот", re.compile(r"\bслот\w*\b"), 3),
            IntentRule("есть места", re.compile(r"\bесть\s+(?:места|окна)\b"), 4),
        ),
        "booking_change": (
            IntentRule(
                "перенести запись", re.compile(r"\bперенес\w*\s+(?:запис\w*|сеанс\w*|дат\w*)\b"), 6
            ),
            IntentRule(
                "отменить запись", re.compile(r"\bотмен\w*.{0,24}\b(?:запис\w*|сеанс\w*)\b"), 6
            ),
            IntentRule(
                "запись отменить", re.compile(r"\b(?:запис\w*|сеанс\w*).{0,24}\bотмен\w*\b"), 6
            ),
            IntentRule("не смогу прийти", re.compile(r"\bне\s+смог\w*\s+(?:прийти|приехать)\b"), 6),
            IntentRule("изменить дату", re.compile(r"\bизмен\w*\s+(?:дат\w*|врем\w*)\b"), 5),
            IntentRule("перенести время", re.compile(r"\bперенес\w*\s+врем\w*\b"), 6),
            IntentRule("сдвинуть запись", re.compile(r"\bсдвин\w*.{0,20}\bзапис\w*\b"), 6),
            IntentRule("не успеваю", re.compile(r"\bне\s+успева\w*.{0,30}\bврем\w*\b"), 6),
            IntentRule("опоздаю", re.compile(r"\bопозда\w*\b"), 4),
            IntentRule("перенос/отмена", re.compile(r"\b(?:перенос|отмена)\b"), 4),
        ),
        "aftercare": (
            IntentRule("чем мазать", re.compile(r"\bчем\s+маз\w*\b"), 5),
            IntentRule("как ухаживать", re.compile(r"\bкак\s+ухажива\w*\b"), 5),
            IntentRule("можно мочить", re.compile(r"\bможно\s+(?:ли\s+)?мочить\b"), 5),
            IntentRule(
                "сколько нельзя мочить",
                re.compile(r"\b(?:сколько\s+времени|как\s+долго).*\b(?:не\s+)?мочить\b"),
                6,
            ),
            IntentRule("сколько заживает", re.compile(r"\bсколько\s+зажива\w*\b"), 5),
            IntentRule("после тату", re.compile(r"\bпосле\s+(?:тату\w*|сеанс\w*)\b"), 3),
            IntentRule("уход", re.compile(r"\bуход\w*\b"), 3),
            IntentRule("заживление", re.compile(r"\bзажив\w*\b"), 3),
            IntentRule("корочки", re.compile(r"\bкорочк\w*\b"), 3),
            IntentRule("пленка", re.compile(r"\bпленк\w*\b"), 3),
            IntentRule("пантенол", re.compile(r"\bпантенол\w*\b"), 3),
            IntentRule("мазать кремом", re.compile(r"\bмаз\w*.{0,20}\bкрем\w*\b"), 5),
            IntentRule("промывать", re.compile(r"\bпромыв\w*\b"), 4),
        ),
        "health": (
            IntentRule("диабет", re.compile(r"\bдиабет\w*\b"), 6),
            IntentRule("беременность", re.compile(r"\bберемен\w*\b"), 6),
            IntentRule("аллергия", re.compile(r"\bаллерг\w*\b"), 5),
            IntentRule("противопоказания", re.compile(r"\bпротивопоказ\w*\b"), 6),
            IntentRule("псориаз/экзема", re.compile(r"\b(?:псориаз\w*|экзем\w*)\b"), 6),
            IntentRule("антибиотики", re.compile(r"\bантибиотик\w*\b"), 5),
            IntentRule("лекарства", re.compile(r"\bлекарств\w*\b"), 4),
            IntentRule("давление", re.compile(r"\bдавлен\w*\b"), 4),
            IntentRule("температура", re.compile(r"\bтемператур\w*\b"), 4),
            IntentRule("простуда", re.compile(r"\bпростуд\w*\b"), 5),
            IntentRule("инфекция", re.compile(r"\b(?:инфекц\w*|гно\w*|воспален\w*)\b"), 6),
            IntentRule("эпилепсия", re.compile(r"\bэпилепс\w*\b"), 6),
            IntentRule("вич/гепатит", re.compile(r"\b(?:вич|гепатит\w*)\b"), 6),
            IntentRule(
                "разжижение крови", re.compile(r"\b(?:антикоагулянт\w*|разжиж\w*\s+кров\w*)\b"), 6
            ),
        ),
        "complaint": (
            IntentRule("испортили", re.compile(r"\b(?:вы\s+)?испорт\w*\b"), 6),
            IntentRule(
                "вернуть деньги", re.compile(r"\b(?:верн\w*|возврат\w*).{0,20}\bден\w*\b"), 6
            ),
            IntentRule("плохо сделали", re.compile(r"\bплохо\s+(?:сделал\w*|набил\w*)\b"), 6),
            IntentRule(
                "не нравится результат",
                re.compile(
                    r"\b(?:не\s+нрав\w*.{0,24}(?:результат\w*|тату\w*|работ\w*)|(?:результат\w*|тату\w*|работ\w*).{0,24}не\s+нрав\w*)\b"
                ),
                6,
            ),
            IntentRule(
                "кривая тату",
                re.compile(
                    r"\b(?:крив\w*.{0,20}(?:тату\w*|лини\w*|работ\w*)|(?:тату\w*|лини\w*|работ\w*).{0,20}крив\w*)\b"
                ),
                5,
            ),
            IntentRule("жалоба", re.compile(r"\bжалоб\w*\b"), 5),
            IntentRule("претензия", re.compile(r"\bпретензи\w*\b"), 5),
            IntentRule("недовольный", re.compile(r"\bнедовол\w*\b"), 5),
            IntentRule("ужас/кошмар", re.compile(r"\b(?:ужасн\w*|кошмар\w*)\b"), 4),
        ),
        "portfolio": (
            IntentRule(
                "покажи работы", re.compile(r"\bпокаж\w*\s+(?:свои\s+|ваши\s+)?работ\w*\b"), 6
            ),
            IntentRule("примеры работ", re.compile(r"\bпример\w*\s+работ\w*\b"), 6),
            IntentRule("работы в стиле", re.compile(r"\bработ\w*\s+в\s+стил\w*\b"), 6),
            IntentRule("фото работ", re.compile(r"\bфото\s+(?:ваших\s+|своих\s+)?работ\w*\b"), 6),
            IntentRule("портфолио", re.compile(r"\bпортфолио\b"), 5),
            IntentRule("галерея", re.compile(r"\bгалере\w*\b"), 4),
            IntentRule(
                "где посмотреть",
                re.compile(r"\bгде\s+посмотр\w*.{0,24}\b(?:работ\w*|эскиз\w*)\b"),
                5,
            ),
            IntentRule(
                "посмотреть работы",
                re.compile(r"\b(?:посмотр\w*|увид\w*).{0,24}\b(?:работ\w*|эскиз\w*)\b"),
                5,
            ),
            IntentRule("какие работы", re.compile(r"\bкакие\s+работ\w*.{0,30}\bдела\w*\b"), 5),
            IntentRule("примеры цветных работ", re.compile(r"\bпример\w*.{0,30}\bработ\w*\b"), 5),
        ),
        "collaboration": (
            IntentRule("сотрудничество", re.compile(r"\bсотруднич\w*\b"), 6),
            IntentRule("реклама/бартер", re.compile(r"\b(?:реклам\w*|бартер\w*)\b"), 6),
            IntentRule("коллаборация", re.compile(r"\bколлаб\w*\b"), 6),
            IntentRule("партнерство", re.compile(r"\bпартнер\w*\b"), 5),
            IntentRule("вакансия", re.compile(r"\bваканси\w*\b"), 5),
            IntentRule("аренда места", re.compile(r"\bаренд\w*\s+мест\w*\b"), 6),
            IntentRule(
                "устроиться мастером", re.compile(r"\bустро\w*\s+(?:к\s+вам\s+)?мастер\w*\b"), 6
            ),
            IntentRule("ищете мастера", re.compile(r"\bищ\w*.{0,24}\b(?:тату\s+)?мастер\w*\b"), 6),
            IntentRule("услуги продвижения", re.compile(r"\bуслуг\w*\s+продвиж\w*\b"), 6),
        ),
    }

    @classmethod
    def classify(cls, text: str) -> str:
        """Вернуть только название интента для обратной совместимости."""
        return cls.classify_detailed(text).intent

    @classmethod
    def classify_detailed(cls, text: str) -> IntentResult:
        normalized = cls._normalize(text)
        scores: dict[str, int] = dict.fromkeys(cls.INTENT_PRIORITY, 0)
        matched: dict[str, list[str]] = {intent: [] for intent in cls.INTENT_PRIORITY}

        for intent, rules in cls.RULES.items():
            for rule in rules:
                if rule.pattern.search(normalized):
                    scores[intent] += rule.weight
                    matched[intent].append(rule.label)

        ranked = sorted(
            cls.INTENT_PRIORITY,
            key=lambda intent: (-scores[intent], cls.INTENT_PRIORITY.index(intent)),
        )
        top_intent = ranked[0]
        top_score = scores[top_intent]
        second_score = scores[ranked[1]]

        if top_score == 0:
            result = IntentResult(
                intent="ambiguous",
                confidence=0.0,
                matched_rules=(),
                scores=MappingProxyType(scores),
            )
            logger.debug("intent_ambiguous", confidence=0.0)
            return result

        confidence = cls._confidence(top_score, second_score)
        result = IntentResult(
            intent=top_intent,
            confidence=confidence,
            matched_rules=tuple(matched[top_intent]),
            scores=MappingProxyType(scores),
        )
        logger.info(
            "intent_classified",
            intent=result.intent,
            confidence=result.confidence,
            matched_rules=result.matched_rules,
        )
        return result

    @staticmethod
    def _normalize(text: str) -> str:
        value = text.casefold().replace("ё", "е")
        value = re.sub(r"[^\w\s@+-]", " ", value, flags=re.UNICODE)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _confidence(top_score: int, second_score: int) -> float:
        base = min(0.58 + top_score * 0.065, 0.98)
        if second_score == 0:
            return round(base, 2)

        margin = top_score - second_score
        if margin <= 0:
            return 0.5
        if margin == 1:
            return min(round(base, 2), 0.58)
        if margin == 2:
            return min(round(base, 2), 0.68)
        return min(round(base, 2), 0.9)
