"""Choose the speech provider from configuration (SPEECH_PROVIDER)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.config import Settings, get_settings
from app.speech import deepgram
from app.speech.base import LiveSpeechProvider, SpeechError, Transcript
from app.speech.live import DeepgramLive

#: name -> factory. Adding a vendor is one entry here plus its module.
_FACTORIES: dict[str, Callable[[], LiveSpeechProvider]] = {
    "deepgram": DeepgramLive,
}


#: name -> one-shot transcription (POST /advisor/transcribe and the benchmark tool).
_BATCH: dict[str, Callable[..., Transcript]] = {
    "deepgram": deepgram.transcribe,
}


def known_providers() -> list[str]:
    return sorted(_FACTORIES)


def provider_name(settings: Settings | None = None) -> str:
    return (settings or get_settings()).speech_provider.strip().lower()


def availability(settings: Settings | None = None) -> tuple[bool, str]:
    """Whether the configured provider can be used right now, and why not."""
    settings = settings or get_settings()
    name = provider_name(settings)
    factory = _FACTORIES.get(name)
    if factory is None:
        return False, f"unknown speech provider {name!r}"
    return type(factory()).availability(settings)


def create_live_provider(settings: Settings | None = None) -> LiveSpeechProvider:
    """A fresh, unconnected session for the configured provider."""
    settings = settings or get_settings()
    factory = _FACTORIES.get(provider_name(settings))
    if factory is None:
        raise SpeechError("unavailable", "unknown speech provider")
    return factory()


def transcribe(
    audio: bytes,
    mime_type: str,
    keyterms: Sequence[str] = (),
    *,
    settings: Settings | None = None,
) -> Transcript:
    """One-shot transcription with the configured provider. Raises SpeechError."""
    settings = settings or get_settings()
    batch = _BATCH.get(provider_name(settings))
    if batch is None:
        raise SpeechError("unavailable", "unknown speech provider")
    return batch(audio, mime_type, keyterms, settings=settings)
