"""POST /advisor/transcribe and the voice flag on /advisor/health.

Deepgram itself is replaced by a stub here: test_speech_deepgram.py covers the
wire format. These tests cover what the route promises - who may call it, what
it refuses before spending a Deepgram call, and that nothing Deepgram says about
the account ever reaches the client.
"""

from __future__ import annotations

import pytest

from app.catalog.registry import registry
from app.config import get_settings
from app.routers import chat as chat_router
from app.speech.deepgram import SpeechError, Transcript
from app.speech.keyterms import spoken_course_codes

AUDIO = b"\x1aE\xdf\xa3 pretend webm bytes"


@pytest.fixture
def voice_on(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_deepgram(monkeypatch):
    """Replaces transcribe() in the router; records every call it gets."""
    calls: list[dict] = []
    outcome: dict = {"result": Transcript(text="What can I take after COSC 241?", confidence=0.93)}

    def fake(audio, mime_type, keyterms=(), *, settings=None):
        calls.append({"audio": audio, "mime_type": mime_type, "keyterms": list(keyterms)})
        if isinstance(outcome["result"], Exception):
            raise outcome["result"]
        return outcome["result"]

    monkeypatch.setattr(chat_router, "transcribe", fake)
    return calls, outcome


def upload(client, data=AUDIO, content_type="audio/webm"):
    return client.post(
        "/advisor/transcribe", files={"audio": ("question.webm", data, content_type)}
    )


class TestAccess:
    def test_requires_a_token(self, anon_client, voice_on, fake_deepgram) -> None:
        response = upload(anon_client)
        assert response.status_code == 401
        assert fake_deepgram[0] == []


class TestHappyPath:
    def test_returns_the_transcript(self, client, voice_on, fake_deepgram) -> None:
        response = upload(client)
        assert response.status_code == 200
        assert response.json() == {"text": "What can I take after COSC 241?", "confidence": 0.93}

    def test_forwards_the_audio_bytes(self, client, voice_on, fake_deepgram) -> None:
        upload(client)
        assert fake_deepgram[0][0]["audio"] == AUDIO

    def test_codec_parameters_are_accepted_and_stripped(
        self, client, voice_on, fake_deepgram
    ) -> None:
        response = upload(client, content_type="audio/webm;codecs=opus")
        assert response.status_code == 200
        assert fake_deepgram[0][0]["mime_type"] == "audio/webm"

    def test_keyterms_come_from_the_catalog(self, client, voice_on, fake_deepgram) -> None:
        upload(client)
        terms = fake_deepgram[0][0]["keyterms"]
        assert "COSC 241" in terms
        assert "COSC" in terms

    def test_silence_is_a_200_with_empty_text(self, client, voice_on, fake_deepgram) -> None:
        fake_deepgram[1]["result"] = Transcript(text="", confidence=0.0)
        response = upload(client)
        assert response.status_code == 200
        assert response.json()["text"] == ""


class TestRefusedBeforeDeepgram:
    def test_no_key_is_503(self, client, fake_deepgram, monkeypatch) -> None:
        monkeypatch.setenv("DEEPGRAM_API_KEY", "")
        get_settings.cache_clear()
        response = upload(client)
        assert response.status_code == 503
        assert fake_deepgram[0] == []

    def test_non_audio_is_415(self, client, voice_on, fake_deepgram) -> None:
        response = upload(client, data=b"%PDF-1.7", content_type="application/pdf")
        assert response.status_code == 415
        assert fake_deepgram[0] == []

    def test_oversized_is_413(self, client, voice_on, fake_deepgram, monkeypatch) -> None:
        monkeypatch.setenv("MAX_AUDIO_BYTES", "16")
        get_settings.cache_clear()
        response = upload(client, data=b"x" * 17)
        assert response.status_code == 413
        assert fake_deepgram[0] == []

    def test_empty_upload_is_422_without_calling_deepgram(
        self, client, voice_on, fake_deepgram
    ) -> None:
        response = upload(client, data=b"")
        assert response.status_code == 422
        assert fake_deepgram[0] == []


class TestDeepgramFailures:
    def test_unavailable_is_503(self, client, voice_on, fake_deepgram) -> None:
        fake_deepgram[1]["result"] = SpeechError("unavailable", "Deepgram returned HTTP 402")
        response = upload(client)
        assert response.status_code == 503
        assert response.json()["detail"] == chat_router.VOICE_UNAVAILABLE

    def test_unreadable_is_422(self, client, voice_on, fake_deepgram) -> None:
        fake_deepgram[1]["result"] = SpeechError("unreadable", "Deepgram returned HTTP 400")
        response = upload(client)
        assert response.status_code == 422
        assert response.json()["detail"] == chat_router.VOICE_UNREADABLE

    def test_error_text_never_reaches_the_client(self, client, voice_on, fake_deepgram) -> None:
        fake_deepgram[1]["result"] = SpeechError(
            "unavailable", "Invalid credentials for project secret-project dg-test-key"
        )
        response = upload(client)
        assert "secret-project" not in response.text
        assert "dg-test-key" not in response.text


class TestHealth:
    def test_voice_off_without_a_key(self, client, monkeypatch) -> None:
        monkeypatch.setenv("DEEPGRAM_API_KEY", "")
        get_settings.cache_clear()
        assert client.get("/advisor/health").json()["voice_available"] is False

    def test_voice_on_with_a_key(self, client, voice_on) -> None:
        body = client.get("/advisor/health").json()
        assert body["voice_available"] is True
        assert "dg-test-key" not in str(body)


class TestSpokenCourseCodes:
    @pytest.fixture(autouse=True)
    def _catalog(self):
        # The app's lifespan loads the catalog; these tests run without a client.
        if not registry.is_loaded:
            registry.load(get_settings().catalog_dir)

    def test_splits_subject_from_number(self) -> None:
        terms = spoken_course_codes(registry.list_programs())
        assert "COSC 241" in terms
        assert "COSC241" not in terms

    def test_includes_each_subject_once_and_no_duplicates(self) -> None:
        terms = spoken_course_codes(registry.list_programs())
        assert "COSC" in terms and "MATH" in terms
        assert len(terms) == len(set(terms))

    def test_stays_within_a_sane_keyterm_budget(self) -> None:
        # Deepgram caps keyterm prompting by token count. ~65 codes + 11 subjects
        # is comfortably inside it; this fails loudly if a catalog import balloons.
        assert len(spoken_course_codes(registry.list_programs())) <= 150
