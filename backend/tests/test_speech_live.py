"""DeepgramLive against a local WebSocket server speaking Deepgram's live shapes.

Verifies what we send (query, header, audio, CloseStream) and that every way the
connection can fail becomes SpeechError("unavailable") or a quietly ended event
stream. A real Deepgram session was checked by hand on Sep 22 2026 - see
PROJECT_STATUS.md.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse

import pytest
from websockets.asyncio.server import serve

from app.config import Settings
from app.speech.deepgram import SpeechError
from app.speech.live import DeepgramLive, Final, Partial, UtteranceEnd, live_url

KEY = "dg-live-test-key-0123456789"


def settings_for(base_url: str, **overrides) -> Settings:
    values = {"deepgram_api_key": KEY, "deepgram_base_url": base_url, "deepgram_timeout_seconds": 3}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def results(text: str, *, is_final: bool, confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "type": "Results",
            "is_final": is_final,
            "speech_final": is_final,
            "channel": {"alternatives": [{"transcript": text, "confidence": confidence}]},
        }
    )


class FakeDeepgram:
    """Records one session; replies with `script` once CloseStream arrives."""

    def __init__(self, script: list[str] | None = None, *, reply_early: list[str] | None = None):
        self.script = script or []
        self.reply_early = reply_early or []
        self.path = ""
        self.headers: dict[str, str] = {}
        self.frames: list[str | bytes] = []

    async def handler(self, connection) -> None:
        self.path = connection.request.path
        self.headers = {k.lower(): v for k, v in connection.request.headers.raw_items()}
        for message in self.reply_early:
            await connection.send(message)
        async for frame in connection:
            self.frames.append(frame)
            if isinstance(frame, str) and json.loads(frame).get("type") == "CloseStream":
                for message in self.script:
                    await connection.send(message)
                await connection.send(json.dumps({"type": "Metadata"}))
                return


async def run_session(fake: FakeDeepgram, *, audio=(b"one", b"two"), keyterms=("COSC 241",)):
    async with serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        live = DeepgramLive()
        await live.connect(keyterms, settings=settings_for(f"http://127.0.0.1:{port}"))
        for chunk in audio:
            await live.send_audio(chunk)
        await live.finish()
        events = [event async for event in live.events()]
        await live.close()
        return events


class TestLiveUrl:
    def test_https_becomes_wss(self) -> None:
        url = live_url(settings_for("https://api.deepgram.com"), [])
        assert url.startswith("wss://api.deepgram.com/v1/listen?")

    def test_http_becomes_ws(self) -> None:
        assert live_url(settings_for("http://127.0.0.1:9"), []).startswith("ws://127.0.0.1:9/")

    def test_query_parameters(self) -> None:
        url = live_url(settings_for("https://api.deepgram.com"), ["COSC", "COSC 241"])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert query["model"] == ["nova-3"]
        assert query["smart_format"] == ["true"]
        assert query["mip_opt_out"] == ["true"]
        assert query["interim_results"] == ["true"]
        assert query["endpointing"] == ["300"]
        assert query["utterance_end_ms"] == ["1500"]
        assert query["keyterm"] == ["COSC", "COSC 241"]

    def test_key_is_never_in_the_url(self) -> None:
        assert KEY not in live_url(settings_for("https://api.deepgram.com"), ["COSC"])


class TestSession:
    def test_key_travels_in_the_handshake_header(self) -> None:
        fake = FakeDeepgram()
        asyncio.run(run_session(fake))
        assert fake.headers["authorization"] == f"Token {KEY}"
        assert KEY not in fake.path

    def test_audio_is_forwarded_in_order_then_closestream(self) -> None:
        fake = FakeDeepgram()
        asyncio.run(run_session(fake, audio=(b"a", b"b", b"c")))
        assert fake.frames[:3] == [b"a", b"b", b"c"]
        assert json.loads(fake.frames[3]) == {"type": "CloseStream"}

    def test_messages_become_events(self) -> None:
        fake = FakeDeepgram(
            [
                results("Can I", is_final=False),
                results("Can I take COSC 241?", is_final=True, confidence=0.95),
                json.dumps({"type": "SpeechStarted"}),
                json.dumps({"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 1.0}),
            ]
        )
        events = asyncio.run(run_session(fake))
        assert events == [
            Partial("Can I"),
            Final("Can I take COSC 241?", 0.95),
            UtteranceEnd(),
        ]

    def test_an_abrupt_drop_just_ends_the_events(self) -> None:
        async def dropper(connection) -> None:
            await connection.send(results("Can I", is_final=False))
            await asyncio.sleep(0.1)  # let it flush: abort() discards unsent bytes
            connection.transport.abort()

        async def scenario():
            async with serve(dropper, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                live = DeepgramLive()
                await live.connect(settings=settings_for(f"http://127.0.0.1:{port}"))
                events = [event async for event in live.events()]
                await live.close()
                return events

        assert asyncio.run(scenario()) == [Partial("Can I")]

    def test_close_twice_is_safe(self) -> None:
        async def scenario():
            fake = FakeDeepgram()
            async with serve(fake.handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                live = DeepgramLive()
                await live.connect(settings=settings_for(f"http://127.0.0.1:{port}"))
                await live.close()
                await live.close()

        asyncio.run(scenario())


class TestConnectFailures:
    def test_rejected_key_is_unavailable(self) -> None:
        async def scenario():
            fake = FakeDeepgram()

            def reject(connection, request):
                return connection.respond(401, "Invalid credentials for project secret-project\n")

            async with serve(fake.handler, "127.0.0.1", 0, process_request=reject) as server:
                port = server.sockets[0].getsockname()[1]
                await DeepgramLive().connect(settings=settings_for(f"http://127.0.0.1:{port}"))

        with pytest.raises(SpeechError) as caught:
            asyncio.run(scenario())
        assert caught.value.kind == "unavailable"
        assert "secret-project" not in str(caught.value)

    def test_refused_port_is_unavailable(self) -> None:
        with pytest.raises(SpeechError) as caught:
            asyncio.run(DeepgramLive().connect(settings=settings_for("http://127.0.0.1:9")))
        assert caught.value.kind == "unavailable"

    def test_no_key_is_unavailable_without_connecting(self) -> None:
        with pytest.raises(SpeechError) as caught:
            asyncio.run(
                DeepgramLive().connect(
                    settings=settings_for("http://127.0.0.1:9", deepgram_api_key="")
                )
            )
        assert caught.value.kind == "unavailable"

    def test_methods_before_connect_are_harmless(self) -> None:
        async def scenario():
            live = DeepgramLive()
            await live.send_audio(b"x")
            await live.finish()
            assert [event async for event in live.events()] == []
            await live.close()

        asyncio.run(scenario())
