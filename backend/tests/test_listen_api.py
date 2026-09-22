"""WS /advisor/listen: sign-in, relaying, and every way a session ends.

Deepgram is replaced by a scripted fake here; test_speech_live.py covers the
real connection. The timing constants are shrunk so limits trip in milliseconds.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.config import get_settings
from app.routers import voice
from app.speech.deepgram import SpeechError
from app.speech.live import Final, Partial, UtteranceEnd

WAIT_FOR_FINISH = "wait_for_finish"
WAIT_FOREVER = "wait_forever"


class ScriptedLive:
    """Stands in for DeepgramLive. `script` items are events, float sleeps, or markers."""

    def __init__(self, script=(), *, connect_error: SpeechError | None = None):
        self.script = list(script)
        self.connect_error = connect_error
        self.keyterms: list[str] = []
        self.audio: list[bytes] = []
        self.finished = asyncio.Event()
        self.closed = False

    async def connect(self, keyterms=(), *, settings=None):
        if self.connect_error:
            raise self.connect_error
        self.keyterms = list(keyterms)

    async def send_audio(self, chunk):
        self.audio.append(chunk)

    async def finish(self):
        self.finished.set()

    async def events(self):
        for step in self.script:
            if step == WAIT_FOR_FINISH:
                await self.finished.wait()
            elif step == WAIT_FOREVER:
                await asyncio.Event().wait()
            elif isinstance(step, float):
                await asyncio.sleep(step)
            else:
                yield step

    async def close(self):
        self.closed = True


@pytest.fixture
def voice_on(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(voice, "START_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(voice, "NO_SPEECH_SECONDS", 0.3)
    monkeypatch.setattr(voice, "MAX_SESSION_SECONDS", 1.0)
    monkeypatch.setattr(voice, "FINISH_GRACE_SECONDS", 0.3)
    yield
    get_settings.cache_clear()


def use(monkeypatch, fake: ScriptedLive) -> ScriptedLive:
    monkeypatch.setattr(voice, "DeepgramLive", lambda: fake)
    return fake


def token_of(client) -> str:
    return client.headers["Authorization"].split(" ", 1)[1]


def converse(client, *, token=None, after_ready=()):
    """Run one session; return every message the server sent, in order."""
    received = []
    with client.websocket_connect("/advisor/listen") as ws:
        ws.send_json({"type": "start", "token": token if token is not None else token_of(client)})
        while True:
            message = ws.receive_json()
            received.append(message)
            if message["type"] == "ready":
                for action in after_ready:
                    if isinstance(action, bytes):
                        ws.send_bytes(action)
                    else:
                        ws.send_json(action)
            if message["type"] in ("done", "error"):
                return received


class TestSignIn:
    def test_a_bad_token_is_unauthorized(self, client, voice_on, monkeypatch) -> None:
        fake = use(monkeypatch, ScriptedLive())
        assert converse(client, token="not-a-token") == [
            {"type": "error", "reason": "unauthorized"}
        ]
        assert fake.keyterms == []

    def test_no_start_message_in_time_is_unauthorized(self, client, voice_on, monkeypatch) -> None:
        use(monkeypatch, ScriptedLive())
        with client.websocket_connect("/advisor/listen") as ws:
            assert ws.receive_json() == {"type": "error", "reason": "unauthorized"}

    def test_audio_before_start_is_unauthorized(self, client, voice_on, monkeypatch) -> None:
        use(monkeypatch, ScriptedLive())
        with client.websocket_connect("/advisor/listen") as ws:
            ws.send_bytes(b"audio first")
            assert ws.receive_json() == {"type": "error", "reason": "unauthorized"}

    def test_a_token_for_a_deleted_account_is_unauthorized(
        self, client, voice_on, monkeypatch, tmp_path
    ) -> None:
        use(monkeypatch, ScriptedLive())
        token = token_of(client)
        monkeypatch.setenv("USER_DIR", str(tmp_path / "empty-accounts"))
        get_settings.cache_clear()
        assert converse(client, token=token) == [{"type": "error", "reason": "unauthorized"}]


class TestUnavailable:
    def test_no_key_is_unavailable(self, client, monkeypatch) -> None:
        use(monkeypatch, ScriptedLive())
        assert converse(client) == [{"type": "error", "reason": "unavailable"}]

    def test_deepgram_refusing_is_unavailable_without_its_text(
        self, client, voice_on, monkeypatch
    ) -> None:
        use(
            monkeypatch,
            ScriptedLive(
                connect_error=SpeechError("unavailable", "Invalid credentials secret-project")
            ),
        )
        received = converse(client)
        assert received == [{"type": "error", "reason": "unavailable"}]
        assert "secret-project" not in str(received)


class TestSession:
    def test_live_text_then_done_on_utterance_end(self, client, voice_on, monkeypatch) -> None:
        fake = use(
            monkeypatch,
            ScriptedLive(
                [
                    Partial("Can I take computer science two forty"),
                    Final("Can I take Computer Science two forty three?", 0.93),
                    UtteranceEnd(),
                ]
            ),
        )
        received = converse(client, after_ready=[b"chunk-1", b"chunk-2"])
        assert received == [
            {"type": "ready"},
            {"type": "transcript", "text": "Can I take computer science two forty"},
            {"type": "transcript", "text": "Can I take COSC 243?"},
            {"type": "done", "text": "Can I take COSC 243?", "confidence": 0.93},
        ]
        assert fake.finished.is_set()
        assert fake.closed

    def test_keyterms_come_from_the_catalog(self, client, voice_on, monkeypatch) -> None:
        fake = use(monkeypatch, ScriptedLive([Final("hi", 0.9), UtteranceEnd()]))
        converse(client)
        assert "COSC 241" in fake.keyterms

    def test_audio_reaches_deepgram_in_order(self, client, voice_on, monkeypatch) -> None:
        fake = use(monkeypatch, ScriptedLive([0.1, Final("hi", 0.9), UtteranceEnd()]))
        converse(client, after_ready=[b"a", b"b", b"c"])
        assert fake.audio == [b"a", b"b", b"c"]

    def test_utterance_end_before_speech_does_not_end_it(
        self, client, voice_on, monkeypatch
    ) -> None:
        use(monkeypatch, ScriptedLive([UtteranceEnd(), Final("still here", 0.9), UtteranceEnd()]))
        received = converse(client)
        assert received[-1] == {"type": "done", "text": "still here", "confidence": 0.9}

    def test_client_stop_ends_it_with_late_finals(self, client, voice_on, monkeypatch) -> None:
        use(
            monkeypatch,
            ScriptedLive([Partial("hello"), WAIT_FOR_FINISH, Final("hello there", 0.8)]),
        )
        received = converse(client, after_ready=[b"x", {"type": "stop"}])
        assert received[-1] == {"type": "done", "text": "hello there", "confidence": 0.8}

    def test_no_speech_ends_it_empty(self, client, voice_on, monkeypatch) -> None:
        use(monkeypatch, ScriptedLive([WAIT_FOR_FINISH]))
        started = time.monotonic()
        received = converse(client)
        assert received == [{"type": "ready"}, {"type": "done", "text": "", "confidence": 0.0}]
        assert time.monotonic() - started < 2.0

    def test_time_limit_ends_with_text_so_far(self, client, voice_on, monkeypatch) -> None:
        use(monkeypatch, ScriptedLive([Partial("a very long"), WAIT_FOREVER]))
        received = converse(client)
        assert received[-1] == {"type": "done", "text": "a very long", "confidence": 0.0}

    def test_audio_limit_ends_it(self, client, voice_on, monkeypatch) -> None:
        monkeypatch.setenv("MAX_AUDIO_BYTES", "3")
        get_settings.cache_clear()
        fake = use(monkeypatch, ScriptedLive([WAIT_FOR_FINISH]))
        received = converse(client, after_ready=[b"ab", b"cd"])
        assert received[-1]["type"] == "done"
        assert fake.audio == [b"ab"]

    def test_deepgram_dropping_ends_it_with_text_so_far(
        self, client, voice_on, monkeypatch
    ) -> None:
        use(monkeypatch, ScriptedLive([Final("Can I take", 0.9)]))  # then the stream ends
        received = converse(client)
        assert received[-1] == {"type": "done", "text": "Can I take", "confidence": 0.9}

    def test_browser_leaving_closes_deepgram(self, client, voice_on, monkeypatch) -> None:
        fake = use(monkeypatch, ScriptedLive([WAIT_FOREVER]))
        with client.websocket_connect("/advisor/listen") as ws:
            ws.send_json({"type": "start", "token": token_of(client)})
            assert ws.receive_json() == {"type": "ready"}
        deadline = time.monotonic() + 2.0
        while not fake.closed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert fake.closed
