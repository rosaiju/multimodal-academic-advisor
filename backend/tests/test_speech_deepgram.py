"""The Deepgram client, against a local server speaking Deepgram's shapes.

Like test_chat_providers.py, this verifies the request we send and the reply we
parse match what Deepgram documents, and that every failure degrades to a
SpeechError. It does NOT prove Deepgram accepts them - only a real key can. The
manual check in DEMO_GUIDE.md does that.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.speech.deepgram import SpeechError, Transcript, transcribe

AUDIO = b"RIFF-not-really-audio-but-bytes-are-bytes"
KEY = "dg-test-key-0123456789"


class FakeDeepgram(BaseHTTPRequestHandler):
    """Captures the request, replies with whatever the test queued."""

    status = 200
    body: dict | str = {}
    delay = 0.0
    captured: dict = {}

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler's API
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        parsed = urllib.parse.urlsplit(self.path)
        type(self).captured = {
            "path": parsed.path,
            "raw_path": self.path,
            "query": urllib.parse.parse_qs(parsed.query),
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": raw,
        }
        if type(self).delay:
            time.sleep(type(self).delay)
        payload = type(self).body
        encoded = (json.dumps(payload) if isinstance(payload, dict) else payload).encode()
        try:
            self.send_response(type(self).status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client timed out and hung up, which is the point of that test

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def deepgram():
    httpd = HTTPServer(("127.0.0.1", 0), FakeDeepgram)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    FakeDeepgram.status, FakeDeepgram.body, FakeDeepgram.delay = 200, {}, 0.0
    FakeDeepgram.captured = {}
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def settings_for(base_url: str, **overrides) -> Settings:
    values = {
        "deepgram_api_key": KEY,
        "deepgram_base_url": base_url,
        "deepgram_timeout_seconds": 5.0,
    }
    values.update(overrides)
    return Settings(**values)


def reply(transcript: str, confidence: float = 0.97) -> dict:
    return {
        "metadata": {"request_id": "fake"},
        "results": {
            "channels": [{"alternatives": [{"transcript": transcript, "confidence": confidence}]}]
        },
    }


class TestRequestShape:
    def test_posts_to_v1_listen(self, deepgram) -> None:
        FakeDeepgram.body = reply("hello")
        transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert FakeDeepgram.captured["path"] == "/v1/listen"

    def test_key_is_a_token_header_and_never_in_the_url(self, deepgram) -> None:
        FakeDeepgram.body = reply("hello")
        transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert FakeDeepgram.captured["headers"]["authorization"] == f"Token {KEY}"
        assert KEY not in FakeDeepgram.captured["raw_path"]

    def test_query_carries_model_formatting_and_opt_out(self, deepgram) -> None:
        FakeDeepgram.body = reply("hello")
        transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        query = FakeDeepgram.captured["query"]
        assert query["model"] == ["nova-3"]
        assert query["smart_format"] == ["true"]
        assert query["mip_opt_out"] == ["true"]

    def test_one_keyterm_parameter_per_term(self, deepgram) -> None:
        FakeDeepgram.body = reply("hello")
        terms = ["COSC", "COSC 241", "MATH 241"]
        transcribe(AUDIO, "audio/webm", terms, settings=settings_for(deepgram))
        assert FakeDeepgram.captured["query"]["keyterm"] == terms

    def test_body_is_the_audio_with_its_content_type(self, deepgram) -> None:
        FakeDeepgram.body = reply("hello")
        transcribe(AUDIO, "audio/mp4", settings=settings_for(deepgram))
        assert FakeDeepgram.captured["body"] == AUDIO
        assert FakeDeepgram.captured["headers"]["content-type"] == "audio/mp4"


class TestParsing:
    def test_returns_text_and_confidence(self, deepgram) -> None:
        FakeDeepgram.body = reply("What can I take after COSC 241?", 0.91)
        result = transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert result == Transcript(text="What can I take after COSC 241?", confidence=0.91)

    def test_silence_is_an_empty_transcript_not_an_error(self, deepgram) -> None:
        FakeDeepgram.body = reply("", 0.0)
        result = transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert result.text == ""

    def test_surrounding_whitespace_is_trimmed(self, deepgram) -> None:
        FakeDeepgram.body = reply("  hello  ")
        result = transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert result.text == "hello"

    def test_non_json_body_is_unavailable(self, deepgram) -> None:
        FakeDeepgram.body = "<html>gateway error</html>"
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert caught.value.kind == "unavailable"

    def test_unexpected_shape_is_unavailable(self, deepgram) -> None:
        FakeDeepgram.body = {"results": {"channels": []}}
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert caught.value.kind == "unavailable"


class TestFailures:
    def test_undecodable_audio_is_unreadable(self, deepgram) -> None:
        FakeDeepgram.status, FakeDeepgram.body = 400, {"err_msg": "corrupt or unsupported data"}
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert caught.value.kind == "unreadable"

    @pytest.mark.parametrize("status", [401, 402, 403, 500, 503])
    def test_account_and_server_errors_are_unavailable(self, deepgram, status) -> None:
        FakeDeepgram.status, FakeDeepgram.body = status, {"err_msg": "nope"}
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert caught.value.kind == "unavailable"

    def test_rate_limit_is_unavailable(self, deepgram) -> None:
        FakeDeepgram.status, FakeDeepgram.body = 429, {"err_msg": "Too many requests"}
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert caught.value.kind == "unavailable"

    def test_timeout_is_unavailable(self, deepgram) -> None:
        FakeDeepgram.body, FakeDeepgram.delay = reply("late"), 1.0
        started = time.monotonic()
        with pytest.raises(SpeechError) as caught:
            transcribe(
                AUDIO,
                "audio/webm",
                settings=settings_for(deepgram, deepgram_timeout_seconds=0.2),
            )
        assert caught.value.kind == "unavailable"
        assert time.monotonic() - started < 1.0

    def test_unreachable_server_is_unavailable(self) -> None:
        # Port 9 (discard) on loopback: nothing listens, the connection is refused.
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for("http://127.0.0.1:9"))
        assert caught.value.kind == "unavailable"

    def test_no_key_is_unavailable_without_a_request(self, deepgram) -> None:
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram, deepgram_api_key=""))
        assert caught.value.kind == "unavailable"
        assert FakeDeepgram.captured == {}

    def test_deepgram_error_text_is_not_in_the_exception_message(self, deepgram) -> None:
        FakeDeepgram.status = 401
        FakeDeepgram.body = {"err_msg": "Invalid credentials for project secret-project"}
        with pytest.raises(SpeechError) as caught:
            transcribe(AUDIO, "audio/webm", settings=settings_for(deepgram))
        assert "secret-project" not in str(caught.value)
