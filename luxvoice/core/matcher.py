"""Сопоставление распознанной фразы с командами.

Задача: по строке, которую выдало распознавание речи, выбрать команду.
Речь распознаётся с ошибками, поэтому точного сравнения мало. Здесь
используется каскад оценок:

  1. точное совпадение нормализованной фразы — 100;
  2. совпадение после удаления служебных слов — 95;
  3. фраза содержится в сказанном — зависит от доли совпадения;
  4. сказанное содержится во фразе (частичная команда) — ниже;
  5. нечёткое сравнение по расстоянию Левенштейна на уровне слов;
  6. плюс контекст: команда подходит активному окну;
  7. плюс приоритет: ручной вес, свежесть, частота использования.

Числительные приводятся к цифрам («пятьдесят» → «50»), порядок слов
учитывается нестрого, «ё» и «е» считаются одной буквой.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from luxvoice.core.model import Command
from luxvoice.core.store import CommandStore, normalize_phrase

log = logging.getLogger(__name__)

# --- Синонимы ---------------------------------------------------------------

# Группы взаимозаменяемых слов. Внутри группы все слова считаются
# одним и тем же: «открой ютуб» и «запусти youtube» — одна команда.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    # Запуск
    ("открой", "открыть", "запусти", "запустить", "включи", "включить", "старт",
     "стартуй", "запуск", "открывай", "врубай", "open", "launch", "start", "run"),
    # Закрытие и остановка
    ("закрой", "закрыть", "выключи", "выключить", "останови", "остановить",
     "стоп", "прекрати", "завершить", "заверши", "убери", "close", "stop", "quit"),
    # Изменение значения
    ("сделай", "сделать", "поставь", "поставить", "установи", "установить",
     "задай", "выставь", "постав", "set", "make"),
    # Громкость вверх
    ("громче", "громчее", "прибавь", "прибавить", "увеличь", "увеличить",
     "подними", "поднять", "повысь", "повысить", "louder", "up"),
    # Громкость вниз
    ("тише", "потише", "убавь", "убавить", "уменьши", "уменьшить",
     "снизь", "снизить", "опусти", "опустить", "quieter", "down"),
    # Свернуть и показать
    ("сверни", "свернуть", "покажи", "показать", "открой", "выведи", "minimize",
     "show"),
    # Смена
    ("смени", "сменить", "переключи", "переключить", "поменяй", "поменять",
     "перейди", "switch", "change"),
    # Поиск
    ("найди", "найти", "поищи", "поиск", "ищи", "search", "find"),
    # Медиа
    ("включи", "воспроизведи", "играй", "плей", "play", "resume"),
    ("пауза", "паузу", "приостанови", "pause"),
    ("дальше", "следующий", "следующая", "пропусти", "next", "skip"),
    ("назад", "предыдущий", "предыдущая", "prev", "previous"),
    # Вежливые формы
    ("пожалуйста", "пжлст", "пжл", "плиз", "please"),
)

# Карта «слово → каноническая форма»
_SYNONYM_MAP: dict[str, str] = {}
for _group in _SYNONYM_GROUPS:
    for _word in _group:
        # Первое слово группы — каноническая форма.
        _SYNONYM_MAP.setdefault(_word, _group[0])


def apply_synonyms(text: str) -> str:
    """Заменить слова синонимами: «запусти ютуб» → «открой ютуб»."""
    if not text:
        return text
    out = []
    for token in text.split():
        out.append(_SYNONYM_MAP.get(token, token))
    return " ".join(out)


# --- Транслитерация названий ------------------------------------------------

# Распознавание часто выдаёт латиницу вместо кириллицы и наоборот.
# Приводим частые названия сервисов к одному виду.
_TRANSLIT: dict[str, str] = {
    "youtube": "ютуб", "ютюб": "ютуб", "youtub": "ютуб",
    "telegram": "телеграм", "телега": "телеграм", "tg": "телеграм",
    "spotify": "спотифай", "спотифи": "спотифай",
    "chrome": "хром", "хром": "хром",
    "discord": "дискорд", "steam": "стим",
    "whatsapp": "ватсап", "вотсап": "ватсап",
    "vscode": "вс код", "code": "вс код",
    "figma": "фигма", "photoshop": "фотошоп", "blender": "блендер",
    "obs": "обс", "word": "ворд", "excel": "эксель", "powerpoint": "поверпоинт",
    "notion": "ноушн", "chatgpt": "чат гпт", "claude": "клод",
    "minecraft": "майнкрафт", "майн": "майнкрафт",
    "browser": "браузер", "explorer": "проводник", "terminal": "терминал",
    "google": "гугл", "yandex": "яндекс", "mail": "почта",
}


def apply_translit(text: str) -> str:
    """Привести латинские названия к кириллическому виду."""
    if not text:
        return text
    out = []
    for token in text.split():
        out.append(_TRANSLIT.get(token, token))
    return " ".join(out)


def canonicalize(text: str, *, digits: bool = True, synonyms: bool = True) -> str:
    """Полное приведение фразы к сравнимому виду."""
    result = normalize_phrase(text)
    if digits:
        result = words_to_digits(result)
    result = apply_translit(result)
    if synonyms:
        result = apply_synonyms(result)
    return result


# --- Числительные -----------------------------------------------------------

_UNITS = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "twenty": 20, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100,
}
_NUMBER_WORDS = {**_UNITS, **_TENS, "half": 50, "половина": 50}

# --- Служебные слова --------------------------------------------------------

# Слова, которые можно добавлять к любой команде, не меняя её смысла.
DEFAULT_FILLER = (
    "пожалуйста", "давай", "давайте", "быстро", "ну", "ок", "окей", "эй",
    "please", "по", "быстрее", "ка", "же", "вот", "это", "мне", "мне бы",
    "будь добр", "если можно", "срочно", "please",
)

# Слова-связки для составных команд.
DEFAULT_CHAIN_WORDS = ("и", "затем", "потом", "после этого", "а также", "then", "and")


def words_to_digits(text: str) -> str:
    """«громкость пятьдесят» → «громкость 50», «двадцать пять» → «25»."""
    tokens = text.split()
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _TENS:
            value = _TENS[token]
            # Следующая единица добавляется: «двадцать пять» → 25.
            if index + 1 < len(tokens) and tokens[index + 1] in _UNITS:
                value += _UNITS[tokens[index + 1]]
                index += 2
            else:
                index += 1
            out.append(str(value))
            continue
        if token in _UNITS:
            out.append(str(_UNITS[token]))
            index += 1
            continue
        out.append(token)
        index += 1
    return " ".join(out)


def strip_filler(text: str, filler: Iterable[str]) -> str:
    """Удалить служебные слова и одиночные буквы."""
    if not filler:
        return text
    phrases = sorted({f.strip().lower() for f in filler if f and f.strip()},
                     key=len, reverse=True)
    result = f" {text} "
    for word in phrases:
        if " " in word:
            result = result.replace(f" {word} ", " ")
        else:
            result = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", " ", result)
    # Одиночные согласные — почти всегда артефакт распознавания.
    result = re.sub(r"\s+[бвгджзклмнпрстфхцчшщ]\s+", " ", result)
    return " ".join(result.split())


# --- Расстояние между строками ---------------------------------------------


def levenshtein(a: str, b: str, limit: int = 100) -> int:
    """Расстояние редактирования с ранним выходом при превышении limit."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > limit:
        return limit + 1

    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        best = i
        for j, char_b in enumerate(b, 1):
            cost = 0 if char_a == char_b else 1
            value = min(
                previous[j] + 1,        # удаление
                current[j - 1] + 1,     # вставка
                previous[j - 1] + cost,  # замена
            )
            current.append(value)
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous = current
    return previous[-1]


def token_similarity(a: str, b: str, max_distance: int = 2) -> float:
    """Похожесть двух слов: 1.0 — одинаковые, 0 — совсем разные."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # Одно слово — начало другого: «громк» и «громкость».
    if a.startswith(b) or b.startswith(a):
        shorter, longer = (a, b) if len(a) < len(b) else (b, a)
        if len(shorter) >= 3:
            return 0.75 + 0.25 * (len(shorter) / len(longer))
    distance = levenshtein(a, b, max_distance)
    if distance > max_distance:
        return 0.0
    return max(0.0, 1.0 - distance / max(len(a), len(b)))


def phrase_similarity(left: Sequence[str], right: Sequence[str],
                      max_distance: int = 2) -> float:
    """Похожесть двух фраз как наборов слов.

    Учитывает и порядок (выравнивание), и состав (пересечение),
    берётся лучшая из двух оценок.
    """
    if not left or not right:
        return 0.0

    # --- Оценка по выравниванию: похоже ли слово на слово ---
    # Динамическое программирование по последовательностям.
    rows, cols = len(left), len(right)
    best = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            score = token_similarity(left[i - 1], right[j - 1], max_distance)
            best[i][j] = max(
                best[i - 1][j],        # слово пропущено
                best[i][j - 1],        # лишнее слово
                best[i - 1][j - 1] + score,
            )
    aligned = best[rows][cols]

    # --- Оценка по составу: сколько слов совпало ---
    matched = 0.0
    used = [False] * cols
    for word in left:
        best_score = 0.0
        best_index = -1
        for index, other in enumerate(right):
            if used[index]:
                continue
            score = token_similarity(word, other, max_distance)
            if score > best_score:
                best_score, best_index = score, index
        if best_index >= 0 and best_score >= 0.7:
            used[best_index] = True
            matched += best_score

    coverage = matched / max(len(left), len(right))
    # Выравнивание учитывает порядок, состав — устойчив к перестановке.
    return max(coverage, aligned / max(len(left), len(right)))


# --- Результат --------------------------------------------------------------


@dataclass
class MatchResult:
    command: Command | None
    phrase: str = ""
    score: float = 0.0
    reason: str = ""
    alternatives: list[tuple[Command, float, str]] = field(default_factory=list)
    requires_prefix: bool = False

    @property
    def found(self) -> bool:
        return self.command is not None

    def confidence(self) -> str:
        if self.score >= 95:
            return "точно"
        if self.score >= 85:
            return "уверенно"
        if self.score >= 70:
            return "вероятно"
        return "слабо"


# --- Сопоставитель ----------------------------------------------------------


class Matcher:
    """Поиск команды по распознанной фразе."""

    def __init__(self, store: CommandStore, settings=None) -> None:
        self._store = store
        self._settings = settings
        self._cache: dict[str, list[tuple[str, tuple[str, ...], Command]]] = {}
        self._cache_stamp = 0.0
        self._filler: tuple[str, ...] = DEFAULT_FILLER
        self._chain_words: tuple[str, ...] = DEFAULT_CHAIN_WORDS

    # --- Настройки --------------------------------------------------------

    def set_settings(self, settings) -> None:
        self._settings = settings
        self.refresh()

    def refresh(self) -> None:
        """Перестроить подготовленные фразы (после правки команд или настроек)."""
        prepared: list[tuple[str, tuple[str, ...], Command]] = []
        for command in self._store.enabled_commands():
            for phrase in command.all_phrases():
                normalized = canonicalize(phrase)
                if not normalized:
                    continue
                prepared.append((normalized, tuple(normalized.split()), command))
        self._prepared = prepared

        # Индекс по значимым словам: «ютуб» находит «открой ютуб».
        keyword_index: dict[str, set[str]] = {}
        for normalized, tokens, command in prepared:
            for token in tokens:
                if len(token) < 3 or token in _SYNONYM_MAP:
                    continue
                keyword_index.setdefault(token, set()).add(command.id)
        self._keyword_index = keyword_index
        self._cache_stamp = 0.0

        filler = list(DEFAULT_FILLER)
        if self._settings is not None:
            extra_filler = self._settings.items("match.extra_filler")
            filler.extend(extra_filler)
            self._filler = tuple(dict.fromkeys(w for w in filler if w))
            chain = self._settings.items("chain.words") or list(DEFAULT_CHAIN_WORDS)
            self._chain_words = tuple(w.lower() for w in chain)
        else:
            self._filler = DEFAULT_FILLER
            self._chain_words = tuple(DEFAULT_CHAIN_WORDS)

        log.debug("Подготовлено фраз: %d, ключевых слов: %d",
                  len(prepared), len(keyword_index))

    def _keyword_candidates(self, tokens: Sequence[str]) -> set[str]:
        """Команды, у которых встречается значимое слово из сказанного."""
        found: set[str] = set()
        for token in tokens:
            if len(token) < 3:
                continue
            found.update(self._keyword_index.get(token, ()))
        return found

    # --- Настройки поиска -------------------------------------------------

    @property
    def threshold(self) -> float:
        if self._settings is None:
            return 72.0
        return float(self._settings.number("match.threshold", 72))

    @property
    def prefix_threshold(self) -> float:
        if self._settings is None:
            return 70.0
        return float(self._settings.number("match.prefix_threshold", 70))

    @property
    def max_distance(self) -> int:
        if self._settings is None:
            return 2
        return int(self._settings.number("match.fuzzy_distance", 2))

    @property
    def fuzzy(self) -> bool:
        if self._settings is None:
            return True
        return self._settings.flag("match.fuzzy", True)

    # --- Обращение --------------------------------------------------------

    def prefixes(self) -> list[str]:
        """Список вариантов обращения, включая частые ошибки распознавания."""
        if self._settings is None:
            return ["джарвис"]
        raw = self._settings.text("stt.prefix", "джарвис")
        aliases = self._settings.text("stt.prefix_aliases", "")
        items: list[str] = []
        for chunk in (raw, aliases):
            for part in chunk.replace(";", ",").split(","):
                text = normalize_phrase(part)
                if text and text not in items:
                    items.append(text)
        return items or ["джарвис"]

    def strip_prefix(self, text: str) -> tuple[str, bool]:
        """Отделить обращение от команды.

        Возвращает (остаток фразы, было ли обращение). Обращение может
        стоять в начале или в конце: «джарвис громче» и «громче джарвис».
        """
        normalized = normalize_phrase(text)
        if not normalized:
            return "", False

        for prefix in self.prefixes():
            tokens = normalized.split()
            prefix_tokens = prefix.split()

            # В начале фразы.
            if len(tokens) >= len(prefix_tokens):
                head = tokens[:len(prefix_tokens)]
                similarity = phrase_similarity(head, prefix_tokens, 1)
                if similarity >= self.prefix_threshold / 100:
                    return " ".join(tokens[len(prefix_tokens):]), True

            # В конце фразы.
            if len(tokens) > len(prefix_tokens):
                tail = tokens[-len(prefix_tokens):]
                similarity = phrase_similarity(tail, prefix_tokens, 1)
                if similarity >= self.prefix_threshold / 100:
                    return " ".join(tokens[:-len(prefix_tokens)]), True

            # Одиночное совпадение внутри — слабый сигнал, но обращение
            # часто распознаётся как отдельное слово в середине.
            if len(prefix_tokens) == 1:
                for index, token in enumerate(tokens):
                    if token_similarity(token, prefix, 1) >= 0.8:
                        rest = tokens[:index] + tokens[index + 1:]
                        return " ".join(rest), True

        return normalized, False

    # --- Разбор связок ----------------------------------------------------

    def split_chain(self, text: str) -> list[str]:
        """Разделить фразу на несколько команд по словам-связкам."""
        if not self._chain_words:
            return [text]
        normalized = normalize_phrase(text)
        if not normalized:
            return []

        parts: list[str] = []
        current: list[str] = []
        tokens = normalized.split()
        index = 0
        while index < len(tokens):
            # Проверяем связки от длинных к коротким.
            matched = False
            for connector in sorted(self._chain_words, key=lambda c: -len(c.split())):
                length = len(connector.split())
                if index + length <= len(tokens):
                    chunk = " ".join(tokens[index:index + length])
                    if chunk == normalize_phrase(connector):
                        if current:
                            parts.append(" ".join(current))
                            current = []
                        index += length
                        matched = True
                        break
            if matched:
                continue
            current.append(tokens[index])
            index += 1

        if current:
            parts.append(" ".join(current))

        # Одиночные слова-связки в начале и конце отбрасываем.
        cleaned = [p.strip() for p in parts if p.strip()]
        return cleaned or [normalized]

    # --- Основной поиск ---------------------------------------------------

    def match(self, text: str, window_titles: Iterable[str] = (),
              require_prefix: bool | None = None) -> MatchResult:
        """Найти команду по фразе.

        window_titles — заголовки активных окон для контекстного поиска.
        require_prefix — принудительно требовать обращение (переопределяет настройку).
        """
        result = MatchResult(command=None)
        if not text or not text.strip():
            result.reason = "пустая фраза"
            return result

        raw = normalize_phrase(text)
        if self._settings is not None and self._settings.flag("match.normalize_numbers", True):
            raw = words_to_digits(raw)

        prefix_mode = (self._settings.flag("stt.prefix_mode", False)
                       if self._settings is not None else False)
        if require_prefix is not None:
            prefix_mode = require_prefix

        remainder, had_prefix = self.strip_prefix(raw)
        result.requires_prefix = prefix_mode and not had_prefix
        if prefix_mode and not had_prefix:
            result.reason = "нужен префикс"
            # Даже без обращения указываем, что команда нашлась бы —
            # интерфейс показывает подсказку «скажите обращение».
            silent = self._search(raw, window_titles, ignore_prefix=True)
            result.alternatives = silent.alternatives
            return result

        search_text = remainder if had_prefix else raw
        if not search_text.strip():
            # Сказали только обращение — это вызов ассистента, не команда.
            result.reason = "только обращение"
            return result

        return self._search(search_text, window_titles, had_prefix=had_prefix)

    def _search(self, text: str, window_titles: Iterable[str],
                had_prefix: bool = False, ignore_prefix: bool = False) -> MatchResult:
        normalized = canonicalize(text)

        stripped = strip_filler(normalized, self._filler)
        # Основной вариант и вариант без служебных слов.
        variants = [normalized]
        if stripped and stripped != normalized:
            variants.append(stripped)
        # Вариант без замены синонимов — если замена мешает совпадению.
        plain = normalize_phrase(text)
        if self._settings is not None and self._settings.flag(
                "match.normalize_numbers", True):
            plain = words_to_digits(plain)
        if plain and plain not in variants:
            variants.append(plain)

        tokens_cache: dict[str, list[str]] = {v: v.split() for v in variants}

        # Отсев кандидатов по словам, плюс команды с совпавшим ключевым словом.
        candidates = self._store.candidates_for_words(
            normalized.split() + stripped.split()
        )
        by_keyword = self._keyword_candidates(normalized.split())
        if by_keyword:
            known = {c.id for c in candidates}
            extra = [self._store.get(cid) for cid in by_keyword - known]
            candidates = candidates + [c for c in extra if c is not None]
        if not candidates:
            candidates = self._store.enabled_commands()

        titles = [normalize_phrase(t) for t in window_titles if t]
        try:
            context_bonus = float(self._settings.number("match.context_bonus", 25)) \
                if self._settings is not None else 25.0
        except (TypeError, ValueError):
            context_bonus = 25.0
        use_context = (self._settings.flag("match.window_context", True)
                       if self._settings is not None else True)

        scored: list[tuple[float, Command, str, str]] = []

        for command in candidates:
            if not command.enabled:
                continue
            if len(command.actions) == 0:
                # Команда без действий — заготовка, не выполняем.
                continue

            best_score = 0.0
            best_phrase = ""

            # Название команды — тоже кандидат на совпадение: «ютуб»
            # должно находить команду с названием «Ютуб».
            title_norm = canonicalize(command.title)
            title_tokens = tuple(title_norm.split()) if title_norm else ()

            for phrase in command.all_phrases():
                phrase_norm = canonicalize(phrase)
                if not phrase_norm:
                    continue
                phrase_tokens = phrase_norm.split()
                if not phrase_tokens:
                    continue
                # Вариант фразы без синонимической замены.
                phrase_plain = normalize_phrase(phrase)
                if self._settings is not None and self._settings.flag(
                        "match.normalize_numbers", True):
                    phrase_plain = words_to_digits(phrase_plain)

                score = 0.0
                for variant in variants:
                    variant_tokens = tokens_cache[variant]
                    phrase_variants = [phrase_norm]
                    if phrase_plain and phrase_plain != phrase_norm:
                        phrase_variants.append(phrase_plain)

                    for target in phrase_variants:
                        if not target:
                            continue
                        target_tokens = target.split()

                        # 1. Точное совпадение.
                        if variant == target:
                            score = 100.0
                            break

                        # 2. Совпадение без служебных слов.
                        stripped_phrase = strip_filler(target, self._filler)
                        if variant == stripped_phrase:
                            score = max(score, 97.0)
                            continue

                        if not self.fuzzy:
                            continue

                        # 3. Фраза целиком внутри сказанного.
                        if target in variant:
                            coverage = len(target_tokens) / max(len(variant_tokens), 1)
                            score = max(score, 80.0 + 18.0 * coverage)

                        # 4. Сказанное внутри фразы (недоговорили).
                        elif variant in target:
                            coverage = len(variant_tokens) / max(len(target_tokens), 1)
                            if len(variant_tokens) >= 2 or (
                                    len(variant_tokens) == 1 and len(variant) >= 4
                                    and len(variant) / max(len(target), 1) > 0.5):
                                score = max(score, 62.0 + 24.0 * coverage)

                        # 5. Нечёткое сравнение по словам.
                        similarity = phrase_similarity(
                            variant_tokens, target_tokens, self.max_distance)
                        if similarity >= 0.55:
                            score = max(score, 55.0 + 42.0 * similarity)

                    if score >= 100.0:
                        break

                if score > best_score:
                    best_score, best_phrase = score, phrase

            # --- Совпадение с названием команды ---
            if title_tokens and best_score < 92.0:
                title_similarity = 0.0
                for variant in variants:
                    variant_tokens = tokens_cache[variant]

                    # Название полностью совпало со сказанным.
                    for target in (title_norm, strip_filler(title_norm, self._filler)):
                        if not target:
                            continue
                        target_tokens = target.split()
                        if variant == target:
                            title_similarity = max(title_similarity, 1.0)
                            continue
                        if target in variant or variant in target:
                            coverage = min(len(variant_tokens), len(target_tokens)) / \
                                max(len(variant_tokens), len(target_tokens))
                            title_similarity = max(title_similarity, coverage)
                        if self.fuzzy and title_similarity < 0.8:
                            title_similarity = max(title_similarity, phrase_similarity(
                                variant_tokens, target_tokens, self.max_distance))

                # Название — более слабый сигнал, чем точная фраза,
                # и требует достаточно высокого сходства.
                if title_similarity >= 0.75:
                    score = 60.0 + 34.0 * title_similarity
                    if score > best_score:
                        best_score = score
                        best_phrase = command.title

            if best_score <= 0:
                continue

            # --- Контекст активного окна ---
            bonus = 0.0
            if use_context and titles:
                if self._context_matches(command, titles):
                    bonus = context_bonus

            # --- Приоритет и свежесть ---
            bonus += max(-20.0, min(20.0, float(command.weight) / 5.0))

            final = min(100.0, best_score + bonus)
            scored.append((final, command, best_phrase, "фраза"))

        if not scored:
            return MatchResult(command=None, reason="совпадений нет")

        scored.sort(key=lambda item: (-item[0], item[1].title.lower()))

        best_score, best_command, best_phrase, reason = scored[0]
        threshold = self.threshold
        # Обращение повышает доверие: команда вызвана явно.
        if had_prefix:
            threshold -= 8

        alternatives = [(cmd, sc, ph) for sc, cmd, ph, _ in scored[1:6]]

        if best_score < threshold:
            return MatchResult(
                command=None,
                phrase=best_phrase,
                score=best_score,
                reason=f"ниже порога ({best_score:.0f} < {threshold:.0f})",
                alternatives=alternatives,
            )

        return MatchResult(
            command=best_command,
            phrase=best_phrase,
            score=best_score,
            reason=reason,
            alternatives=alternatives,
        )

    def _context_matches(self, command: Command, titles: Sequence[str]) -> bool:
        """Подходит ли команда текущим окнам."""
        if not command.contexts:
            return False
        for pattern in command.contexts:
            needle = normalize_phrase(pattern)
            if not needle:
                continue
            for title in titles:
                if needle in title:
                    return True
        return False

    # --- Подсказки --------------------------------------------------------

    def suggest(self, text: str, limit: int = 5) -> list[tuple[Command, float]]:
        """Ближайшие команды — для строки «возможно, вы имели в виду»."""
        result = self._search(text, (), ignore_prefix=True)
        out: list[tuple[Command, float]] = []
        if result.command is not None:
            out.append((result.command, result.score))
        out.extend((cmd, score) for cmd, score, _ in result.alternatives)
        return out[:limit]

    # --- Поиск команд для ИИ ---------------------------------------------

    def best_for_request(self, request: str) -> MatchResult:
        """Расширенный поиск по смыслу для запросов нейросети.

        Порог ниже: нейросеть формулирует свободнее, чем человек
        задумывал команду, поэтому допускаем менее точное совпадение.
        """
        original = self.threshold
        try:
            if self._settings is not None:
                self._settings.set("match.threshold", max(45, int(original) - 20),
                                   save=False, notify=False)
            return self.match(request)
        finally:
            if self._settings is not None:
                self._settings.set("match.threshold", original, save=False, notify=False)


# --- Одиночный экземпляр ----------------------------------------------------

_matcher: Matcher | None = None


def get_matcher(store: CommandStore | None = None, settings=None) -> Matcher:
    global _matcher
    if _matcher is None:
        from luxvoice.core.store import get_store
        _matcher = Matcher(store or get_store(), settings)
        _matcher.refresh()
    elif store is not None and _matcher._store is not store:
        _matcher._store = store
        _matcher.refresh()
    return _matcher