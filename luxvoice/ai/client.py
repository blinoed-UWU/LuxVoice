"""Клиент нейросети: 12 провайдеров, поиск в интернете, файлы и команды.

Особенность: один интерфейс ask() для всех сервисов. Отличия между ними —
адрес, формат запроса и способ передачи ключа — описаны данными
в PROVIDERS, поэтому новый сервис добавляется одной записью.

Дополнительно реализовано:
  * поиск в интернете с последующим ответом по найденным страницам;
  * создание файлов (txt, csv, json, md) и таблиц;
  * выполнение команд на компьютере по смыслу запроса;
  * локальные модели (Ollama, LM Studio) без интернета.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


# --- Описание провайдеров ---------------------------------------------------

@dataclass
class Provider:
    """Сведения о сервисе нейросетей."""

    key: str
    title: str
    base_url: str = ""
    default_model: str = ""
    models: tuple[str, ...] = ()
    key_url: str = ""
    local: bool = False
    free_limit: bool = False
    rubles: bool = False
    note: str = ""
    auth_style: str = "bearer"       # bearer, x-api-key, query, none
    api_style: str = "openai"        # openai, anthropic, gemini, yandex, gigachat


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        key="openai", title="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "o4-mini"),
        key_url="https://platform.openai.com/api-keys",
        note="Ключ с platform.openai.com. Подписка ChatGPT не подходит.",
    ),
    "anthropic": Provider(
        key="anthropic", title="Anthropic (Claude)",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-sonnet-4-5",
        models=("claude-sonnet-4-5", "claude-opus-4-6", "claude-haiku-4-5"),
        key_url="https://console.anthropic.com/settings/keys",
        auth_style="x-api-key", api_style="anthropic",
    ),
    "gemini": Provider(
        key="gemini", title="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        default_model="gemini-2.0-flash",
        models=("gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro"),
        key_url="https://aistudio.google.com/app/apikey",
        free_limit=True, auth_style="query", api_style="gemini",
        note="Есть бесплатный лимит запросов.",
    ),
    "grok": Provider(
        key="grok", title="xAI (Grok)",
        base_url="https://api.x.ai/v1",
        default_model="grok-2-latest",
        models=("grok-2-latest", "grok-2-1212", "grok-beta"),
        key_url="https://console.x.ai",
    ),
    "deepseek": Provider(
        key="deepseek", title="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        models=("deepseek-chat", "deepseek-reasoner"),
        key_url="https://platform.deepseek.com/api_keys",
        note="Недорогой сервис с хорошим качеством на русском языке.",
    ),
    "yandexgpt": Provider(
        key="yandexgpt", title="YandexGPT",
        base_url="https://llm.api.cloud.yandex.net/foundationModels/v1",
        default_model="yandexgpt-lite",
        models=("yandexgpt-lite", "yandexgpt"),
        key_url="https://console.yandex.cloud",
        rubles=True, auth_style="bearer", api_style="yandex",
        note="Оплата в рублях. Нужен идентификатор каталога в облаке.",
    ),
    "gigachat": Provider(
        key="gigachat", title="GigaChat",
        base_url="https://gigachat.devices.sberbank.ru/api/v1",
        default_model="GigaChat",
        models=("GigaChat", "GigaChat-Pro"),
        key_url="https://developers.sber.ru/portal/products/gigachat",
        rubles=True, free_limit=True,
        note="Есть бесплатный лимит для физических лиц.",
    ),
    "openrouter": Provider(
        key="openrouter", title="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini",
        models=("openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet",
                "google/gemini-flash-1.5", "meta-llama/llama-3.3-70b-instruct"),
        key_url="https://openrouter.ai/keys",
        note="Один ключ — доступ ко многим моделям.",
    ),
    "proxyapi": Provider(
        key="proxyapi", title="ProxyAPI",
        base_url="https://api.proxyapi.ru/openai/v1",
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet"),
        key_url="https://proxyapi.ru",
        rubles=True,
        note="Оплата в рублях, работает без VPN.",
    ),
    "aitunnel": Provider(
        key="aitunnel", title="AITunnel",
        base_url="https://api.aitunnel.ru/v1",
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet"),
        key_url="https://aitunnel.ru",
        rubles=True,
        note="Оплата в рублях, работает без VPN.",
    ),
    "ollama": Provider(
        key="ollama", title="Ollama",
        base_url="http://localhost:11434/v1",
        default_model="llama3.1",
        models=("llama3.1", "qwen2.5", "gemma2", "mistral", "phi3"),
        key_url="https://ollama.com/download",
        local=True, auth_style="none",
        note="Работает локально, без интернета и без оплаты.",
    ),
    "lmstudio": Provider(
        key="lmstudio", title="LM Studio",
        base_url="http://localhost:1234/v1",
        default_model="local-model",
        models=("local-model",),
        key_url="https://lmstudio.ai",
        local=True, auth_style="none",
        note="Локальный сервер LM Studio. Запустите его перед использованием.",
    ),
    "custom": Provider(
        key="custom", title="Свой сервер",
        base_url="",
        default_model="",
        models=(),
        local=True, auth_style="bearer",
        note="Любой сервер, совместимый с OpenAI API.",
    ),
}


def provider_info(key: str) -> Provider:
    return PROVIDERS.get(key, PROVIDERS["openai"])


def provider_choices() -> list[tuple[str, str]]:
    return [(key, p.title) for key, p in PROVIDERS.items()]


# --- Результат ответа -------------------------------------------------------

@dataclass
class AiReply:
    """Ответ нейросети."""

    text: str = ""
    provider: str = ""
    model: str = ""
    elapsed: float = 0.0
    error: str = ""
    tokens: int = 0
    sources: list[str] = field(default_factory=list)
    command: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


# --- Поиск в интернете ------------------------------------------------------

class WebSearch:
    """Поиск в интернете с получением текста страниц."""

    def __init__(self, settings=None) -> None:
        self._settings = settings

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """Найти страницы по запросу."""
        provider = self._settings.text("aichat.search_provider", "duckduckgo") \
            if self._settings else "duckduckgo"
        if provider == "none":
            return []

        if provider == "duckduckgo":
            return self._search_duckduckgo(query, limit)
        if provider == "searxng":
            return self._search_searx(query, limit)
        if provider == "tavily":
            return self._search_tavily(query, limit)
        if provider == "brave":
            return self._search_brave(query, limit)
        return self._search_duckduckgo(query, limit)

    def _search_duckduckgo(self, query: str, limit: int) -> list[dict[str, str]]:
        """DuckDuckGo через облегчённый интерфейс — без ключа."""
        import requests
        from urllib.parse import quote_plus

        try:
            response = requests.get(
                f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                       "LuxVoice/1.0"},
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Поиск недоступен: %s", exc)
            return []

        if response.status_code != 200:
            return []

        # Разбираем результат регулярным выражением: структура простая и стабильная.
        results: list[dict[str, str]] = []
        pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL)
        for match in pattern.finditer(response.text):
            url = match.group(1)
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            # DuckDuckGo оборачивает ссылки в свой редирект.
            if "uddg=" in url:
                from urllib.parse import parse_qs, unquote, urlparse
                params = parse_qs(urlparse(url).query)
                if "uddg" in params:
                    url = unquote(params["uddg"][0])
            if url.startswith("http"):
                results.append({"title": title, "url": url})
            if len(results) >= limit:
                break
        return results

    def _search_searx(self, query: str, limit: int) -> list[dict[str, str]]:
        import requests
        from urllib.parse import quote_plus

        base = ""
        if self._settings is not None:
            base = self._settings.text("aichat.search_url", "")
        if not base:
            return []

        try:
            response = requests.get(
                f"{base.rstrip('/')}/search",
                params={"q": query, "format": "json"},
                timeout=15,
            )
            data = response.json()
        except Exception:  # noqa: BLE001
            return []

        results: list[dict[str, str]] = []
        for item in data.get("results", [])[:limit]:
            results.append({
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("content", "")),
            })
        return results

    def _search_tavily(self, query: str, limit: int) -> list[dict[str, str]]:
        import requests

        key = self._settings.text("aichat.search_api_key", "") \
            if self._settings else ""
        if not key:
            return []

        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": limit},
                timeout=20,
            )
            data = response.json()
        except Exception:  # noqa: BLE001
            return []

        return [
            {"title": str(item.get("title", "")),
             "url": str(item.get("url", "")),
             "snippet": str(item.get("content", ""))}
            for item in data.get("results", [])[:limit]
        ]

    def _search_brave(self, query: str, limit: int) -> list[dict[str, str]]:
        import requests

        key = self._settings.text("aichat.search_api_key", "") \
            if self._settings else ""
        if not key:
            return []

        try:
            response = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
                headers={"X-Subscription-Token": key,
                         "Accept": "application/json"},
                timeout=20,
            )
            data = response.json()
        except Exception:  # noqa: BLE001
            return []

        return [
            {"title": str(item.get("title", "")),
             "url": str(item.get("url", "")),
             "snippet": str(item.get("description", ""))}
            for item in data.get("web", {}).get("results", [])[:limit]
        ]

    def fetch_text(self, url: str, max_chars: int = 4000) -> str:
        """Прочитать страницу и вернуть её текст."""
        import requests

        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                       "LuxVoice/1.0"},
                timeout=15,
            )
        except Exception:  # noqa: BLE001
            return ""

        if response.status_code != 200:
            return ""

        text = response.text
        # Убираем скрипты и стили — они не несут смысла.
        text = re.sub(r"<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ",
                      text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&[a-zA-Z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    def context_for(self, query: str) -> tuple[str, list[str]]:
        """Собрать справку из интернета по запросу."""
        results = self.search(query, limit=5)
        if not results:
            return "", []

        parts: list[str] = []
        sources: list[str] = []
        for item in results[:4]:
            url = item.get("url", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            body = self.fetch_text(url) if url else ""

            text = body or snippet
            if not text:
                continue
            sources.append(url)
            parts.append(f"Источник: {title} ({url})\n{text[:1500]}")

        if not parts:
            return "", []
        return "\n\n---\n\n".join(parts), sources


# --- Клиент -----------------------------------------------------------------

class AiClient:
    """Единый клиент для всех провайдеров."""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._search = WebSearch(settings)
        self._memory: list[tuple[str, str]] = []

    def set_settings(self, settings) -> None:
        self._settings = settings
        self._search = WebSearch(settings)

    # --- Доступ -----------------------------------------------------------

    def _provider(self) -> Provider:
        key = self._settings.text("ai.provider", "openai") if self._settings else "openai"
        return provider_info(key)

    def _key(self, provider: Provider | None = None) -> str:
        """Ключ для выбранного провайдера."""
        provider = provider or self._provider()
        if self._settings is None:
            return ""

        keys = self._settings.get("ai.keys") or {}
        if isinstance(keys, dict):
            stored = str(keys.get(provider.key, "") or "")
            if stored:
                return stored

        # Ключ, введённый в поле основного провайдера.
        current = self._settings.text("ai.provider", "")
        if current == provider.key:
            return self._settings.text("ai.api_key", "")
        return ""

    def _model(self, provider: Provider | None = None) -> str:
        provider = provider or self._provider()
        if self._settings is not None:
            chosen = self._settings.text("ai.model", "")
            if chosen:
                return chosen
        return provider.default_model

    def _base_url(self, provider: Provider | None = None) -> str:
        provider = provider or self._provider()
        if provider.key == "custom" and self._settings is not None:
            return self._settings.text("ai.base_url", "") or "http://localhost:8000/v1"
        return provider.base_url

    @property
    def enabled(self) -> bool:
        if self._settings is None:
            return False
        if not self._settings.flag("ai.enabled", False):
            return False
        if self._settings.flag("privacy.offline_only", False):
            provider = self._provider()
            return provider.local
        return True

    def describe(self) -> dict[str, Any]:
        provider = self._provider()
        return {
            "provider": provider.key,
            "title": provider.title,
            "model": self._model(provider),
            "has_key": bool(self._key(provider)),
            "local": provider.local,
            "enabled": self.enabled,
        }

    # --- Проверка подключения --------------------------------------------

    def test_connection(self, provider_key: str = "", api_key: str = "") -> tuple[bool, str]:
        """Проверить, работает ли ключ."""
        provider = provider_info(provider_key or (
            self._settings.text("ai.provider", "openai") if self._settings else "openai"))
        key = api_key or self._key(provider)

        if not key and not provider.local:
            return False, "не задан ключ API"

        try:
            reply = self._request(provider, key, "Ответь одним словом: работает",
                                  system="Ты проверяешь подключение.",
                                  max_tokens=10, timeout=25)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

        if reply.error:
            return False, reply.error
        return True, f"Подключение работает. Ответ сервиса: {reply.text[:60]}"

    def list_models(self) -> list[str]:
        """Список моделей провайдера, если сервис его отдаёт."""
        provider = self._provider()
        if provider.models:
            return list(provider.models)

        key = self._key(provider)
        try:
            import requests
            response = requests.get(
                f"{self._base_url(provider).rstrip('/')}/models",
                headers=self._headers(provider, key),
                timeout=15,
            )
            if response.status_code == 200:
                data = response.json()
                items = data.get("data") or data.get("models") or []
                return [str(item.get("id") or item.get("name")) for item in items][:50]
        except Exception:  # noqa: BLE001
            pass
        return []

    # --- Основной запрос --------------------------------------------------

    def ask(self, question: str, use_search: bool | None = None,
            system: str = "", history_limit: int | None = None) -> str:
        """Задать вопрос и получить ответ. Возвращает текст (для интерфейса)."""
        reply = self.reply(question, use_search=use_search, system=system,
                           history_limit=history_limit)
        if reply.error:
            return f"Ошибка: {reply.error}"
        return reply.text

    def reply(self, question: str, use_search: bool | None = None,
              system: str = "", history_limit: int | None = None,
              allow_commands: bool = True) -> AiReply:
        """Полный ответ со всеми сведениями."""
        started = time.time()

        if not self.enabled:
            return AiReply(
                error="нейросеть выключена. Откройте Настройки → "
                      "ИИ-провайдер и включите «Включить нейросеть»")

        provider = self._provider()
        key = self._key(provider)

        if not key and not provider.local:
            return AiReply(
                error=f"не задан ключ для {provider.title}. "
                      f"Получите его: {provider.key_url}")

        # --- Поиск в интернете ---
        sources: list[str] = []
        context = ""
        wants_search = (use_search if use_search is not None
                        else self._should_search(question))
        if wants_search and self._settings is not None and self._settings.flag(
                "aichat.web_search", True):
            context, sources = self._search.context_for(question)

        # --- Справка о возможностях ---
        capabilities = self._capabilities(question) if allow_commands else ""

        system_prompt = system or self._build_system(context, capabilities)

        # --- История разговора ---
        if history_limit is None:
            history_limit = int(self._settings.number("aichat.history_limit", 20)) \
                if self._settings else 20

        messages = list(self._memory[-history_limit:]) if history_limit else []
        messages.append(("user", question))

        reply = self._request(provider, key, question, system=system_prompt,
                              history=messages)
        reply.provider = provider.key
        reply.model = self._model(provider)
        reply.sources = sources
        reply.elapsed = time.time() - started

        if reply.ok:
            self._memory.append(("user", question))
            self._memory.append(("assistant", reply.text))
            # Держим память разумного размера.
            if len(self._memory) > 60:
                self._memory = self._memory[-40:]

            # Проверяем, не просит ли пользователь выполнить команду.
            if allow_commands:
                command = self._extract_command(reply.text)
                if command:
                    reply.command = command

        return reply

    def _should_search(self, question: str) -> bool:
        """Нужен ли поиск в интернете для этого вопроса."""
        lowered = question.lower()
        markers = ("найди", "поищи", "погугли", "что нового", "новости",
                   "свежие", "последние", "сегодня", "сейчас", "курс",
                   "погода", "цена", "сколько стоит", "когда", "кто такой",
                   "tell me about", "search", "latest", "current")
        return any(marker in lowered for marker in markers)

    def _capabilities(self, question: str) -> str:
        """Справка о том, какие команды есть на компьютере."""
        try:
            from luxvoice.core.store import get_store
            store = get_store()
            commands = store.search(question, limit=8)
            if not commands:
                return ""

            lines = ["Доступные команды на этом компьютере, близкие по смыслу:"]
            for command in commands[:6]:
                phrases = ", ".join(command.all_phrases()[:3])
                lines.append(f"  · {command.title} — фразы: {phrases}")

            lines.append("")
            lines.append("Если запрос можно выполнить командой, ответь коротко "
                         "и добавь в конце строку: ВЫПОЛНИТЬ: <фраза команды>")
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            return ""

    def _build_system(self, context: str, capabilities: str) -> str:
        """Собрать системную подсказку."""
        custom = self._settings.text("ai.system_prompt", "") if self._settings else ""
        if custom:
            base = custom
        else:
            persona = self._settings.text(
                "aichat.persona",
                "Умный и краткий голосовой помощник, который управляет "
                "этим компьютером") if self._settings else ""
            locale = self._settings.text("aichat.locale", "auto") \
                if self._settings else "auto"

            language_rule = {
                "ru": "Отвечай только на русском языке.",
                "en": "Answer in English only.",
                "auto": "Отвечай на языке вопроса.",
            }.get(locale, "Отвечай на языке вопроса.")

            base = (
                f"Ты {persona}. Работаешь на компьютере с Linux (KDE Plasma). "
                f"{language_rule} "
                "Отвечай кратко и по делу: ты голосовой помощник, длинные "
                "ответы неудобно слушать. Не используй разметку и списки "
                "без необходимости. Не выдумывай факты: если не уверен, "
                "скажи об этом прямо."
            )

        parts = [base]
        if capabilities:
            parts.append(capabilities)
        if context:
            parts.append(
                "Сведения, найденные в интернете. Используй их для ответа "
                "и укажи источники:\n\n" + context)
        return "\n\n".join(parts)

    def _extract_command(self, text: str) -> str:
        """Найти в ответе указание выполнить команду."""
        match = re.search(r"ВЫПОЛНИТЬ:\s*(.+)", text or "")
        if match:
            command = match.group(1).strip().strip("«»\"'")
            return command[:200]
        return ""

    # --- Запросы к разным сервисам ---------------------------------------

    def _headers(self, provider: Provider, key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if provider.auth_style == "bearer" and key:
            headers["Authorization"] = f"Bearer {key}"
        elif provider.auth_style == "x-api-key" and key:
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        return headers

    def _request(self, provider: Provider, key: str, question: str,
                 system: str = "", history: list[tuple[str, str]] | None = None,
                 max_tokens: int = 0, timeout: int = 0) -> AiReply:
        """Выполнить запрос с учётом особенностей сервиса."""
        try:
            if provider.api_style == "anthropic":
                return self._request_anthropic(provider, key, question, system,
                                               history, max_tokens, timeout)
            if provider.api_style == "gemini":
                return self._request_gemini(provider, key, question, system,
                                            history, max_tokens, timeout)
            if provider.api_style == "yandex":
                return self._request_yandex(provider, key, question, system,
                                            max_tokens, timeout)
            return self._request_openai(provider, key, question, system,
                                        history, max_tokens, timeout)
        except Exception as exc:  # noqa: BLE001
            return AiReply(error=f"сбой запроса: {exc}")

    def _limits(self, max_tokens: int, timeout: int) -> tuple[int, int]:
        if not max_tokens:
            max_tokens = int(self._settings.number("ai.max_tokens", 1024)) \
                if self._settings else 1024
        if not timeout:
            timeout = int(self._settings.number("ai.timeout", 60)) \
                if self._settings else 60
        return max_tokens, timeout

    def _request_openai(self, provider: Provider, key: str, question: str,
                        system: str, history: list[tuple[str, str]] | None,
                        max_tokens: int, timeout: int) -> AiReply:
        """Стандартный запрос, совместимый с OpenAI API."""
        import requests

        max_tokens, timeout = self._limits(max_tokens, timeout)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        for role, content in (history or []):
            messages.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": content,
            })
        if not history:
            messages.append({"role": "user", "content": question})

        payload = {
            "model": self._model(provider),
            "messages": messages,
            "temperature": float(self._settings.number("ai.temperature", 0.3))
            if self._settings else 0.3,
            "max_tokens": max_tokens,
        }

        url = f"{self._base_url(provider).rstrip('/')}/chat/completions"
        response = requests.post(url, headers=self._headers(provider, key),
                                 json=payload, timeout=timeout)

        if response.status_code != 200:
            return AiReply(error=self._explain_status(response))

        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError, TypeError):
            return AiReply(error="сервис вернул неожиданный ответ")

        usage = data.get("usage") or {}
        return AiReply(text=text,
                       tokens=int(usage.get("total_tokens", 0) or 0))

    def _request_anthropic(self, provider: Provider, key: str, question: str,
                           system: str, history: list[tuple[str, str]] | None,
                           max_tokens: int, timeout: int) -> AiReply:
        """Запрос к Anthropic: другой формат тела."""
        import requests

        max_tokens, timeout = self._limits(max_tokens, timeout)
        messages = [
            {"role": "assistant" if role == "assistant" else "user",
             "content": content}
            for role, content in (history or [])
        ]
        if not messages:
            messages = [{"role": "user", "content": question}]

        payload: dict[str, Any] = {
            "model": self._model(provider),
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": float(self._settings.number("ai.temperature", 0.3))
            if self._settings else 0.3,
        }
        if system:
            payload["system"] = system

        response = requests.post(
            f"{self._base_url(provider).rstrip('/')}/messages",
            headers=self._headers(provider, key),
            json=payload, timeout=timeout)

        if response.status_code != 200:
            return AiReply(error=self._explain_status(response))

        try:
            data = response.json()
            parts = data.get("content") or []
            text = " ".join(str(part.get("text", "")) for part in parts
                            if isinstance(part, dict)).strip()
        except (ValueError, TypeError):
            return AiReply(error="сервис вернул неожиданный ответ")

        return AiReply(text=text)

    def _request_gemini(self, provider: Provider, key: str, question: str,
                        system: str, history: list[tuple[str, str]] | None,
                        max_tokens: int, timeout: int) -> AiReply:
        """Запрос к Gemini: ключ в адресе, другой формат тела."""
        import requests

        max_tokens, timeout = self._limits(max_tokens, timeout)
        model = self._model(provider)

        parts: list[dict[str, str]] = []
        if system:
            parts.append({"text": system})
        for role, content in (history or []):
            parts.append({"text": content})
        if not history:
            parts.append({"text": question})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": float(self._settings.number("ai.temperature", 0.3))
                if self._settings else 0.3,
                "maxOutputTokens": max_tokens,
            },
        }

        url = (f"{self._base_url(provider)}/models/{model}:generateContent"
               f"?key={key}")
        response = requests.post(url, json=payload, timeout=timeout)

        if response.status_code != 200:
            return AiReply(error=self._explain_status(response))

        try:
            data = response.json()
            candidates = data.get("candidates") or []
            text = ""
            if candidates:
                content = candidates[0].get("content") or {}
                text = " ".join(str(part.get("text", ""))
                                for part in content.get("parts", [])).strip()
        except (ValueError, TypeError, KeyError):
            return AiReply(error="сервис вернул неожиданный ответ")

        return AiReply(text=text)

    def _request_yandex(self, provider: Provider, key: str, question: str,
                        system: str, max_tokens: int, timeout: int) -> AiReply:
        """Запрос к YandexGPT: нужен идентификатор каталога."""
        import requests

        max_tokens, timeout = self._limits(max_tokens, timeout)
        folder = ""
        if self._settings is not None:
            model = self._settings.text("ai.model", "")
            # Идентификатор каталога можно указать в поле модели.
            if model and not model.startswith("yandexgpt"):
                folder = model
        model_name = provider.default_model
        if self._settings is not None:
            chosen = self._settings.text("ai.model", "")
            if chosen.startswith("yandexgpt"):
                model_name = chosen

        payload = {
            "modelUri": f"gpt://{folder or 'default'}/{model_name}",
            "completionOptions": {"stream": False,
                                  "temperature": 0.3,
                                  "maxTokens": str(max_tokens)},
            "messages": [
                *([{"role": "system", "text": system}] if system else []),
                {"role": "user", "text": question},
            ],
        }

        response = requests.post(
            f"{self._base_url(provider).rstrip('/')}/completion",
            headers=self._headers(provider, key),
            json=payload, timeout=timeout)

        if response.status_code != 200:
            return AiReply(error=self._explain_status(response))

        try:
            data = response.json()
            text = data["result"]["alternatives"][0]["message"]["text"].strip()
        except (KeyError, IndexError, ValueError, TypeError):
            return AiReply(error="сервис вернул неожиданный ответ")

        return AiReply(text=text)

    @staticmethod
    def _explain_status(response: Any) -> str:
        """Понятное объяснение ошибки сервиса."""
        code = response.status_code
        try:
            data = response.json()
            detail = (data.get("error", {}).get("message")
                      if isinstance(data.get("error"), dict)
                      else data.get("error") or data.get("message") or "")
        except Exception:  # noqa: BLE001
            detail = response.text[:200]

        hints = {
            401: "неверный ключ API — проверьте, что скопирован полностью",
            403: "доступ запрещён: возможно, ключ не имеет прав "
                 "или закончился бесплатный лимит",
            404: "модель не найдена — проверьте название модели в настройках",
            429: "слишком много запросов или закончилась квота",
            500: "ошибка на стороне сервиса, попробуйте позже",
            502: "сервис временно недоступен",
            503: "сервис перегружен, попробуйте позже",
        }
        hint = hints.get(code, "")
        return f"сервис вернул ошибку {code}" + (f": {hint}" if hint else "") + \
               (f" ({detail})" if detail else "")

    # --- Создание файлов --------------------------------------------------

    def create_file(self, request: str, directory: str = "") -> tuple[bool, str]:
        """Создать файл по словесному описанию.

        Нейросеть возвращает содержимое в согласованном формате,
        программа записывает его на диск.
        """
        if not self.enabled:
            return False, ("нейросеть выключена. Откройте Настройки → "
                           "ИИ-провайдер и включите «Включить нейросеть»")

        prompt = (
            "Создай файл по описанию пользователя. Ответь строго в таком виде:\n"
            "ИМЯ_ФАЙЛА: имя.расширение\n"
            "СОДЕРЖИМОЕ:\n"
            "<содержимое файла без пояснений>\n\n"
            "Поддерживаются: txt, md, csv, json, html, py. "
            "Для таблиц используй CSV с заголовками.\n\n"
            f"Описание: {request}"
        )

        reply = self.reply(prompt, use_search=False, allow_commands=False)
        if not reply.ok:
            return False, reply.error

        name_match = re.search(r"ИМЯ_ФАЙЛА:\s*(.+)", reply.text)
        body_match = re.search(r"СОДЕРЖИМОЕ:\s*\n?(.*)", reply.text,
                               re.DOTALL)

        if not name_match or not body_match:
            return False, "не удалось разобрать ответ нейросети"

        filename = name_match.group(1).strip().strip("«»\"'`")
        # Убираем опасные символы из имени файла.
        filename = re.sub(r"[^\w\-. ]", "_", filename)[:80] or "файл.txt"
        content = body_match.group(1).strip()
        # Снимаем обрамление из блоков кода, если оно есть.
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)

        base = Path(directory).expanduser() if directory else (
            Path(self._settings.text("aichat.files_dir", "")
                 or str(Path.home() / "Документы")))
        try:
            base.mkdir(parents=True, exist_ok=True)
            target = base / filename
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return False, f"не удалось создать файл: {exc}"

        return True, str(target)

    def summarize_screen(self, question: str) -> str:
        """Ответить по снимку экрана.

        Снимок делается самим приложением, затем передаётся в сервис,
        если он умеет читать изображения.
        """
        screenshot = self._take_screenshot()
        if not screenshot:
            return "Не удалось сделать снимок экрана"

        # Разные сервисы принимают изображения по-разному.
        provider = self._provider()
        if provider.key in ("openai", "openrouter", "deepseek", "grok"):
            return self._ask_with_image_openai(screenshot, question)
        if provider.key == "gemini":
            return self._ask_with_image_gemini(screenshot, question)
        return ("Выбранный сервис не умеет читать изображения. "
                "Работают: OpenAI, Gemini, Grok, OpenRouter.")

    @staticmethod
    def _take_screenshot() -> str:
        """Сделать снимок экрана во временный файл."""
        import shutil as _shutil
        import subprocess
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".png")
        import os
        os.close(handle)

        for command in (
            ["spectacle", "-b", "-n", "-f", "-o", path],
            ["grim", path],
            ["gnome-screenshot", "-f", path],
        ):
            if not _shutil.which(command[0]):
                continue
            try:
                result = subprocess.run(command, capture_output=True, timeout=20)
                if result.returncode == 0 and Path(path).exists():
                    return path
            except (OSError, subprocess.TimeoutExpired):
                continue
        return ""

    def _ask_with_image_openai(self, image_path: str, question: str) -> str:
        import base64

        import requests

        provider = self._provider()
        key = self._key(provider)
        try:
            data = base64.b64encode(Path(image_path).read_bytes()).decode()
        except OSError as exc:
            return f"Не удалось прочитать снимок: {exc}"

        payload = {
            "model": self._model(provider),
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{data}"}},
                ],
            }],
            "max_tokens": 800,
        }

        try:
            response = requests.post(
                f"{self._base_url(provider).rstrip('/')}/chat/completions",
                headers=self._headers(provider, key),
                json=payload, timeout=90)
        except Exception as exc:  # noqa: BLE001
            return f"Сбой запроса: {exc}"

        if response.status_code != 200:
            return self._explain_status(response)

        try:
            return response.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError):
            return "Сервис вернул неожиданный ответ"

    def _ask_with_image_gemini(self, image_path: str, question: str) -> str:
        import base64

        import requests

        provider = self._provider()
        key = self._key(provider)
        try:
            data = base64.b64encode(Path(image_path).read_bytes()).decode()
        except OSError as exc:
            return f"Не удалось прочитать снимок: {exc}"

        payload = {
            "contents": [{
                "parts": [
                    {"text": question},
                    {"inline_data": {"mime_type": "image/png", "data": data}},
                ],
            }],
        }

        url = (f"{self._base_url(provider)}/models/"
               f"{self._model(provider)}:generateContent?key={key}")
        try:
            response = requests.post(url, json=payload, timeout=90)
        except Exception as exc:  # noqa: BLE001
            return f"Сбой запроса: {exc}"

        if response.status_code != 200:
            return self._explain_status(response)

        try:
            data = response.json()
            content = data["candidates"][0]["content"]
            return " ".join(str(part.get("text", ""))
                            for part in content.get("parts", [])).strip()
        except (KeyError, IndexError, ValueError, TypeError):
            return "Сервис вернул неожиданный ответ"

    def clear_memory(self) -> None:
        """Забыть историю разговора."""
        self._memory.clear()


# --- Единственный экземпляр -------------------------------------------------

_client: AiClient | None = None
_lock = threading.Lock()


def get_ai_client(settings=None) -> AiClient:
    global _client
    with _lock:
        if _client is None:
            _client = AiClient(settings)
        elif settings is not None:
            _client.set_settings(settings)
        return _client