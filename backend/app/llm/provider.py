"""Provider-neutral access to a vision-capable model.

The team applied for both Anthropic and OpenAI student credits and will use
whichever lands, so nothing above this file names a vendor. `get_vision_provider()`
reads one setting and returns something satisfying `VisionProvider`.

The interface is deliberately narrow: hand it a document and a prompt, get text
back. No tool use, no conversation, no streaming. A wider surface would invite the
rest of the codebase to depend on model behaviour, and the one architectural rule
this project rests on is that the model never decides anything about a degree.

*** Nothing in app/catalog/, app/audit/ or app/ingestion/ may import this. ***
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Document types a vision model can read. PDFs are sent natively where supported.
VISION_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}
)


class ProviderError(RuntimeError):
    """The provider is unavailable, unconfigured, or returned nothing usable."""


@runtime_checkable
class VisionProvider(Protocol):
    """Reads a document and returns the model's raw text response."""

    name: str

    def read_document(
        self,
        data: bytes,
        *,
        media_type: str,
        prompt: str,
        max_tokens: int = 4096,
    ) -> str: ...


class AnthropicVisionProvider:
    """Anthropic implementation. Imported lazily so the package stays optional."""

    def __init__(self, api_key: str, model: str) -> None:
        self.name = model
        self._api_key = api_key
        self._model = model

    def read_document(
        self,
        data: bytes,
        *,
        media_type: str,
        prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderError(
                "the 'anthropic' package is not installed; "
                'install the llm extra: pip install -e ".[llm]"'
            ) from exc

        import base64

        encoded = base64.standard_b64encode(data).decode("ascii")
        kind = "document" if media_type == "application/pdf" else "image"
        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": kind,
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return "".join(block.text for block in message.content if block.type == "text")


class OpenAIVisionProvider:
    """OpenAI implementation. Imported lazily so the package stays optional."""

    def __init__(self, api_key: str, model: str) -> None:
        self.name = model
        self._api_key = api_key
        self._model = model

    def read_document(
        self,
        data: bytes,
        *,
        media_type: str,
        prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ProviderError(
                "the 'openai' package is not installed; "
                'install the llm extra: pip install -e ".[llm]"'
            ) from exc

        import base64

        encoded = base64.standard_b64encode(data).decode("ascii")
        client = OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
        )
        return response.choices[0].message.content or ""


class OllamaVisionProvider:
    """A local vision model through Ollama (e.g. qwen2.5vl:3b). No key, no egress.

    Images only: Ollama does not take PDFs, so `supports_pdf` is False and the
    extractor does not offer PDFs to it. Output is requested as JSON
    (`format: "json"`) at temperature 0. Like every vision reader, what comes back
    is UNVERIFIED and goes through the student's review screen.
    """

    supports_pdf = False

    def __init__(self, base_url: str, model: str, timeout: float = 600.0) -> None:
        self.name = model
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def read_document(
        self,
        data: bytes,
        *,
        media_type: str,
        prompt: str,
        max_tokens: int = 4096,
    ) -> str:
        if media_type == "application/pdf":
            raise ProviderError(
                "the local vision model reads images, not PDFs. Upload a photo or "
                "screenshot of the transcript, or a plain-text transcript."
            )
        import base64

        # Imported here: app.llm.chat imports ProviderError from this module.
        from app.llm.chat import _http_json

        payload = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.standard_b64encode(data).decode("ascii")],
                }
            ],
        }
        reply = _http_json(f"{self._base}/api/chat", payload, timeout=self._timeout)
        message = reply.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("the local vision model returned nothing")
        return content


def get_vision_provider(settings: Settings | None = None) -> VisionProvider:
    """Build the configured provider, or raise saying exactly what is missing.

    VISION_PROVIDER picks one explicitly; empty keeps the original rule (follow
    LLM_PROVIDER: anthropic, otherwise openai).
    """
    settings = settings or get_settings()

    if settings.vision_provider == "ollama":
        return OllamaVisionProvider(
            settings.ollama_base_url,
            settings.ollama_vision_model,
            settings.ollama_vision_timeout_seconds,
        )
    if settings.vision_provider == "openai":
        if not settings.openai_api_key:
            raise ProviderError(
                "OPENAI_API_KEY is not set, so scanned transcripts cannot be read. "
                "Plain-text transcripts still work without it."
            )
        return OpenAIVisionProvider(settings.openai_api_key, settings.openai_vision_model)

    if settings.vision_provider == "anthropic" or settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set, so scanned transcripts cannot be read. "
                "Plain-text transcripts still work without it."
            )
        return AnthropicVisionProvider(settings.anthropic_api_key, settings.anthropic_vision_model)

    if not settings.openai_api_key:
        raise ProviderError(
            "OPENAI_API_KEY is not set, so scanned transcripts cannot be read. "
            "Plain-text transcripts still work without it."
        )
    return OpenAIVisionProvider(settings.openai_api_key, settings.openai_vision_model)
