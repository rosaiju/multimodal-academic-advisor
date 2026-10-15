"""OpenAI speech-to-text against a loopback fake; no network, no real key."""

from __future__ import annotations

import asyncio
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.speech import registry
from app.speech.base import Final, SpeechError
from app.speech.openai_stt import OpenAILive, transcribe

KEY = "sk-test-key"
AUDIO = b"\x1aE\xdf\xa3fake-webm"


class Fake(BaseHTTPRequestHandler):
    status = 200
    body: dict = {}
    captured: dict = {}

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        Fake.captured = {"path": self.path, "auth": self.headers["Authorization"], "body": raw}
        payload = json.dumps(Fake.body).encode()
        self.send_response(Fake.status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def openai():
    httpd = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    Fake.status, Fake.body, Fake.captured = 200, {"text": "COSC two forty three"}, {}
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def cfg(base: str, **kw) -> Settings:
    return Settings(
        speech_provider="openai",
        openai_api_key=KEY,
        openai_base_url=base,
        openai_stt_timeout_seconds=5,
        **kw,
    )


def test_request_shape_and_key_stays_in_header(openai) -> None:
    result = transcribe(AUDIO, "audio/webm", ["COSC 243"], settings=cfg(openai))
    sent = Fake.captured
    assert sent["path"] == "/v1/audio/transcriptions"
    assert sent["auth"] == f"Bearer {KEY}"
    assert KEY.encode() not in sent["body"]
    assert b"COSC 243" in sent["body"] and AUDIO in sent["body"]
    assert result.text == "COSC two forty three"
    assert result.confidence == 0.0  # no logprobs in the reply


def test_confidence_comes_from_logprobs(openai) -> None:
    Fake.body = {"text": "hi", "logprobs": [{"logprob": math.log(0.9)}, {"logprob": math.log(0.9)}]}
    assert transcribe(AUDIO, "audio/webm", settings=cfg(openai)).confidence == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("status", "kind"), [(400, "unreadable"), (401, "unavailable"), (429, "unavailable")]
)
def test_http_errors_never_leak_vendor_text(openai, status, kind) -> None:
    Fake.status, Fake.body = status, {"error": {"message": "org-secret-name"}}
    with pytest.raises(SpeechError) as exc:
        transcribe(AUDIO, "audio/webm", settings=cfg(openai))
    assert exc.value.kind == kind
    assert "org-secret-name" not in str(exc.value)


def test_bad_shape_and_missing_key(openai) -> None:
    Fake.body = {"nope": 1}
    with pytest.raises(SpeechError):
        transcribe(AUDIO, "audio/webm", settings=cfg(openai))
    with pytest.raises(SpeechError):
        transcribe(AUDIO, "audio/webm", settings=Settings(openai_api_key=""))


def test_registry_selects_openai(openai) -> None:
    settings = cfg(openai)
    assert isinstance(registry.create_live_provider(settings), OpenAILive)
    assert registry.availability(settings)[0] is True
    assert registry.availability(Settings(speech_provider="openai", openai_api_key=""))[0] is False
    assert registry.transcribe(AUDIO, "audio/webm", settings=settings).text


def test_buffered_session_answers_once_on_finish(openai) -> None:
    async def run():
        live = OpenAILive()
        await live.connect(["COSC 243"], settings=cfg(openai))
        await live.send_audio(AUDIO[:5])
        await live.send_audio(AUDIO[5:])
        await live.finish()
        return [e async for e in live.events()]

    events = asyncio.run(run())
    assert events == [Final("COSC two forty three", 0.0)]
    assert AUDIO in Fake.captured["body"]
