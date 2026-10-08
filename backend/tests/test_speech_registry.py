"""The provider registry: selection, availability and one-shot dispatch."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.speech import registry
from app.speech.base import LiveSpeechProvider, SpeechError
from app.speech.live import DeepgramLive


def test_deepgram_is_the_default() -> None:
    assert Settings().speech_provider == "deepgram"
    assert isinstance(registry.create_live_provider(Settings()), DeepgramLive)
    assert isinstance(DeepgramLive(), LiveSpeechProvider)


def test_provider_name_is_case_and_space_insensitive() -> None:
    assert registry.provider_name(Settings(speech_provider=" Deepgram ")) == "deepgram"


def test_availability_follows_the_key() -> None:
    assert registry.availability(Settings(deepgram_api_key="")) == (
        False,
        "no Deepgram key is configured",
    )
    assert registry.availability(Settings(deepgram_api_key="k"))[0] is True


def test_unknown_provider_is_unavailable_not_a_crash() -> None:
    settings = Settings(speech_provider="nope")
    assert registry.availability(settings)[0] is False
    with pytest.raises(SpeechError):
        registry.create_live_provider(settings)
    with pytest.raises(SpeechError):
        registry.transcribe(b"x", "audio/webm", settings=settings)
