"""Deepgram Aura text-to-speech, against a local server speaking its shape."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.speech.base import SpeechError
from app.speech.tts import synthesize


class Speak(BaseHTTPRequestHandler):
    status = 200
    captured: dict = {}

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).captured = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "json": json.loads(raw),
        }
        body = b"ID3-fake-mp3" if type(self).status == 200 else b'{"err":"no"}'
        self.send_response(type(self).status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def base():
    httpd = HTTPServer(("127.0.0.1", 0), Speak)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    Speak.status, Speak.captured = 200, {}
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def settings(base: str, key: str = "dg-key") -> Settings:
    return Settings(deepgram_api_key=key, deepgram_base_url=base)


def test_tts_posts_to_speak_endpoint(base) -> None:
    audio = synthesize("Hello there.", settings=settings(base))
    assert audio == b"ID3-fake-mp3"
    assert Speak.captured["path"] == "/v1/speak?model=aura-2-thalia-en&encoding=mp3"
    assert Speak.captured["headers"]["authorization"] == "Token dg-key"
    assert Speak.captured["json"] == {"text": "Hello there."}


def test_tts_without_key_unavailable(base) -> None:
    with pytest.raises(SpeechError) as exc:
        synthesize("hi", settings=settings(base, key=""))
    assert exc.value.kind == "unavailable"


def test_tts_http_error_unavailable(base) -> None:
    Speak.status = 401
    with pytest.raises(SpeechError):
        synthesize("hi", settings=settings(base))
