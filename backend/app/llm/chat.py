"""Provider-neutral conversational access to a chat model.

Sibling of `provider.py`, which does vision. The same rule applies, and it is the
rule the whole project rests on:

*** Nothing in app/catalog/, app/audit/ or app/ingestion/ may import this. ***

A chat provider here is a phrasing engine and nothing more. It is handed a block
of facts that `app/advisor/facts.py` computed from the degree engine, and asked to
say them in English. It is never asked what a student still needs, because it does
not know and cannot be made to know reliably.

Four providers are supported. Anthropic and OpenAI use their SDKs, imported lazily
so neither is a hard dependency. Gemini and Ollama are plain HTTP over stdlib
`urllib`, deliberately: Gemini's REST surface is small enough that an SDK would be
a dependency for no gain, and Ollama exists precisely for the case where nothing
can be installed and no credits exist. Adding a package in order to talk to the
offline fallback would defeat the point of having one.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.config import Settings, get_settings
from app.llm.provider import ProviderError

logger = logging.getLogger(__name__)

Role = Literal["user", "assistant"]

LLM_EXTRA_HINT = 'install the llm extra: pip install -e ".[llm]"'


@dataclass(frozen=True)
class ChatTurn:
    """One turn of conversation.

    Deliberately not a pydantic model: this crosses into the provider layer, and
    the wire schema belongs to the router.
    """

    role: Role
    content: str


class ProviderUnavailable(ProviderError):
    """The provider cannot be reached, is unconfigured, or is out of quota.

    Separate from ProviderError because the advisor treats it as "fall back to the
    deterministic answer", not "something broke". An exhausted quota mid-demo must
    degrade to a correct plain answer, never to an error page.
    """


@runtime_checkable
class ChatProvider(Protocol):
    name: str
    provider_id: str

    def available(self) -> tuple[bool, str]:
        """(usable, reason). Cheap: no network call for key-based providers."""
        ...

    def complete(self, *, system: str, messages: list[ChatTurn], max_tokens: int = 800) -> str: ...


def _http_json(url: str, payload: dict, *, timeout: float, headers: dict | None = None) -> dict:
    """POST JSON, get JSON. Raises ProviderUnavailable with a readable reason."""
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if exc.code in (401, 403):
            raise ProviderUnavailable(
                f"provider rejected the credential ({exc.code}): {detail}"
            ) from exc
        if exc.code == 429:
            raise ProviderUnavailable(
                f"provider quota exhausted or rate limited (429): {detail}"
            ) from exc
        if exc.code >= 500:
            raise ProviderUnavailable(f"provider is failing ({exc.code}): {detail}") from exc
        raise ProviderUnavailable(f"provider rejected the request ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderUnavailable(f"cannot reach the provider: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderUnavailable("the model did not respond in time") from exc
    except json.JSONDecodeError as exc:
        raise ProviderUnavailable(f"provider returned a non-JSON body: {exc}") from exc


class AnthropicChatProvider:
    provider_id = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self.name = model
        self._api_key = api_key
        self._model = model

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "ANTHROPIC_API_KEY is not set"
        return True, "configured"

    def complete(self, *, system: str, messages: list[ChatTurn], max_tokens: int = 800) -> str:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderUnavailable(
                f"the 'anthropic' package is not installed; {LLM_EXTRA_HINT}"
            ) from exc
        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            message = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": t.role, "content": t.content} for t in messages],
            )
        except Exception as exc:  # noqa: BLE001 - the SDK raises a wide family
            raise ProviderUnavailable(f"anthropic call failed: {exc}") from exc
        return "".join(b.text for b in message.content if b.type == "text").strip()


class OpenAIChatProvider:
    provider_id = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.name = model
        self._api_key = api_key
        self._model = model

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "OPENAI_API_KEY is not set"
        return True, "configured"

    def complete(self, *, system: str, messages: list[ChatTurn], max_tokens: int = 800) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ProviderUnavailable(
                f"the 'openai' package is not installed; {LLM_EXTRA_HINT}"
            ) from exc
        try:
            client = OpenAI(api_key=self._api_key)
            response = client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    *({"role": t.role, "content": t.content} for t in messages),
                ],
            )
        except Exception as exc:  # noqa: BLE001 - the SDK raises a wide family
            raise ProviderUnavailable(f"openai call failed: {exc}") from exc
        return (response.choices[0].message.content or "").strip()


class GeminiChatProvider:
    """Google Gemini over the REST API, using stdlib HTTP only.

    The key travels in the `x-goog-api-key` header rather than the query string,
    so it cannot end up in a proxy or server access log.
    """

    provider_id = "gemini"
    API = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str, timeout: float = 45.0) -> None:
        self.name = model
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def available(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "GEMINI_API_KEY is not set"
        return True, "configured"

    def complete(self, *, system: str, messages: list[ChatTurn], max_tokens: int = 800) -> str:
        ok, reason = self.available()
        if not ok:
            raise ProviderUnavailable(reason)
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                # Gemini calls the assistant role "model".
                {
                    "role": "model" if t.role == "assistant" else "user",
                    "parts": [{"text": t.content}],
                }
                for t in messages
            ],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
        }
        data = _http_json(
            f"{self.API}/{self._model}:generateContent",
            payload,
            timeout=self._timeout,
            headers={"x-goog-api-key": self._api_key},
        )
        candidates = data.get("candidates") or []
        if not candidates:
            blocked = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderUnavailable(
                f"gemini returned no candidates (blockReason={blocked})"
                if blocked
                else "gemini returned no candidates"
            )
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            raise ProviderUnavailable("gemini returned an empty completion")
        return text


class OllamaChatProvider:
    """A model running locally. No key, no quota, no network egress.

    `available()` genuinely asks the server, because "is ollama running" has no
    answer in configuration - and a demo that claims a local model is ready when
    nothing is listening is worse than one that says so plainly.
    """

    provider_id = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 60.0) -> None:
        self.name = model
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def available(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self._base}/api/tags", timeout=2.0) as response:
                tags = json.loads(response.read().decode())
        except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
            return False, f"no Ollama server at {self._base} ({exc})"
        names = [m.get("name", "") for m in tags.get("models", [])]
        if not names:
            return False, f"Ollama is running at {self._base} but has no models pulled"
        # Ollama reports "llama3.1:latest" for a model pulled as "llama3.1".
        wanted = self._model.split(":")[0]
        if not any(n == self._model or n.split(":")[0] == wanted for n in names):
            return False, (
                f"Ollama is running but {self._model!r} is not pulled "
                f"(available: {', '.join(names[:5])})"
            )
        return True, f"local model {self._model}"

    def complete(self, *, system: str, messages: list[ChatTurn], max_tokens: int = 800) -> str:
        payload = {
            "model": self._model,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
            "messages": [
                {"role": "system", "content": system},
                *({"role": t.role, "content": t.content} for t in messages),
            ],
        }
        data = _http_json(f"{self._base}/api/chat", payload, timeout=self._timeout)
        text = ((data.get("message") or {}).get("content") or "").strip()
        if not text:
            raise ProviderUnavailable("ollama returned an empty completion")
        return text


def get_chat_provider(settings: Settings | None = None) -> ChatProvider:
    """Build the configured chat provider.

    Never raises for a missing key - ask `available()` for that, so a caller can
    degrade to the deterministic answer rather than crash.
    """
    settings = settings or get_settings()
    provider = settings.llm_provider
    if provider == "anthropic":
        return AnthropicChatProvider(settings.anthropic_api_key, settings.anthropic_chat_model)
    if provider == "openai":
        return OpenAIChatProvider(settings.openai_api_key, settings.openai_chat_model)
    if provider == "gemini":
        return GeminiChatProvider(settings.gemini_api_key, settings.gemini_chat_model)
    return OllamaChatProvider(
        settings.ollama_base_url, settings.ollama_chat_model, settings.ollama_timeout_seconds
    )
