# Voice Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A student can click a mic in the "Ask the advisor" panel, speak a question, and have Deepgram's transcript fill the question box for review before sending.

**Architecture:** The browser records a short clip with `MediaRecorder` and posts it to a new authenticated route, `POST /advisor/transcribe`. That route forwards it to Deepgram's pre-recorded API through a small `urllib` client in a new `app/speech/` package, biased toward catalog course codes via `keyterm`. The key never leaves the server, audio is never written to disk, and the transcript is never auto-sent.

**Tech Stack:** FastAPI + pydantic-settings + stdlib `urllib` (backend, no new dependency); React 19 + Vite + Tailwind 4 (frontend, no new dependency); pytest with a loopback `HTTPServer` fake.

**Spec:** `docs/superpowers/specs/2026-09-22-voice-input-design.md`

## Global Constraints

- **No new dependencies**, backend or frontend. HTTP to Deepgram uses `urllib`, as `app/llm/chat.py` does for Gemini and Ollama.
- The Deepgram key is sent only as the header `Authorization: Token <key>`. It never appears in a URL, a response body, a log line or the frontend.
- Every Deepgram request carries `mip_opt_out=true`, `smart_format=true` and `model=<deepgram_model>` (default `nova-3`).
- Audio is held in memory only. Nothing is written to disk.
- The transcript is **never auto-sent**. It fills the input, and the student presses Ask.
- Voice failures never block typing.
- User-facing strings (verbatim):
  - unavailable: `Voice input isn't available right now — you can still type.`
  - unreadable: `Couldn't read that recording — try again.`
  - empty: `Didn't catch that — try again.`
  - low confidence: `Check this — I may have misheard.`
- Low-confidence threshold: `confidence < 0.6`.
- Limits: `max_audio_bytes = 2 * 1024 * 1024`, client auto-stop at 30 seconds, `deepgram_timeout_seconds = 20.0`.
- `app/audit/` and `app/catalog/` must never import `app.speech`.
- Backend must stay `ruff check .` and `black --check .` clean (line length 100). Frontend must stay `npm run lint` and `npm run build` clean.
- **Before starting:** `backend/app/config.py` must match `HEAD`, with no hardcoded key. Real keys belong in `backend/.env`. Baseline with no keys anywhere is **604 passed**. With any provider key in the environment or in `backend/.env`, 7 ingestion tests fail until Task 0 lands.

## Review Focus

1. **Browser MIME types with codec parameters.** Chrome labels the upload `audio/webm;codecs=opus`. The route must accept it (it starts with `audio/`) and forward the bare `audio/webm` to Deepgram. → Task 2 test `test_codec_parameters_are_accepted_and_stripped`.
2. **A zero-byte recording** (click start, click stop immediately). This must be refused as unreadable (`422`) without spending a Deepgram call. → Task 2 test `test_empty_upload_is_422_without_calling_deepgram`.
3. **Deepgram rate limiting (`429`) or a hung connection.** These must degrade to `503`, not to a 500 or a request that hangs for minutes. → Task 1 tests `test_rate_limit_is_unavailable` and `test_timeout_is_unavailable`.
4. **The student had already typed part of a question before speaking.** The transcript must be appended after a space, not replace what they typed. → Task 3 `mergeDraft` plus a manual check in Task 3 Step 6.
5. **Leaving the page, or double-clicking the mic, while recording.** The mic tracks must be released (the browser's red recording dot goes away), no second stream may open, and no transcript may arrive after unmount. → Task 3 hook (`startingRef`, unmount cleanup) plus a manual check in Task 3 Step 6.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `backend/tests/conftest.py` | modify | Blank every provider key per test, so a developer's `backend/.env` cannot change results |
| `backend/app/speech/__init__.py` | create | Package marker and short docstring |
| `backend/app/speech/deepgram.py` | create | `transcribe()`, `Transcript`, `SpeechError`: the only code that talks to Deepgram |
| `backend/app/speech/keyterms.py` | create | `spoken_course_codes(programs)`: catalog codes → Deepgram key terms |
| `backend/app/config.py` | modify | Five Deepgram/voice settings |
| `backend/app/routers/chat.py` | modify | `POST /advisor/transcribe`; `voice_available` on `/advisor/health` |
| `.env.example` | modify | `DEEPGRAM_API_KEY=` and model/timeout lines |
| `backend/tests/test_speech_deepgram.py` | create | Wire format and failure mapping against a loopback fake |
| `backend/tests/test_transcribe_api.py` | create | Route behaviour, limits, error mapping, health, key terms |
| `backend/tests/test_no_llm_in_engine.py` | modify | Add `"speech"` to `FORBIDDEN_ROOTS` |
| `frontend/src/api.js` | modify | `transcribeAudio(blob)` |
| `frontend/src/components/useVoiceRecorder.js` | create | MediaRecorder lifecycle: start, stop, auto-stop, release |
| `frontend/src/components/AdvisorChat.jsx` | modify | Mic button, transcribing state, notes, draft merge |
| `docs/advisor.md`, `PROJECT_STATUS.md`, `DEMO_GUIDE.md` | modify | Document voice input and the live check |

---

### Task 0: Isolate tests from local keys

Found while planning: `Settings` reads `backend/.env`, and so do the tests. With
`OPENAI_API_KEY` and `LLM_PROVIDER=openai` set anywhere, 7 tests in
`test_ingest_api.py`, `test_pdf_extractor.py` and `test_transcript_vision.py`
fail, because they assume no vision provider is configured. Anyone who sets up
keys for the demo, including for Deepgram, would see a red suite for reasons
unrelated to their change.

**Files:**
- Modify: `backend/tests/conftest.py` (new autouse fixture after `_clean_throttle`)

**Interfaces:**
- Produces: every test starts with `LLM_PROVIDER=anthropic` and empty `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` and `DEEPGRAM_API_KEY`. Tests that need a key set it themselves with `monkeypatch.setenv` plus `get_settings.cache_clear()`.

- [ ] **Step 1: Reproduce the failure**

Run (from `backend/`): `OPENAI_API_KEY=sk-fake LLM_PROVIDER=openai .venv/Scripts/python -m pytest -q`
Expected: `7 failed, 597 passed`

- [ ] **Step 2: Add the fixture to `backend/tests/conftest.py`**

Insert after the `_clean_throttle` fixture:

```python


#: Provider settings a developer's backend/.env may set. Environment variables
#: outrank the .env file, so blanking them here wins over whatever is on disk.
_PROVIDER_ENV = {
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_API_KEY": "",
    "OPENAI_API_KEY": "",
    "GEMINI_API_KEY": "",
    "DEEPGRAM_API_KEY": "",
}


@pytest.fixture(autouse=True)
def _no_real_provider_keys(monkeypatch):
    """No test sees the keys in a developer's backend/.env.

    Without this, putting a real key in .env for the demo turned seven ingestion
    tests red, because they assume no vision model is configured. A test that
    needs a provider sets one itself.
    """
    for name, value in _PROVIDER_ENV.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

- [ ] **Step 3: Verify both ways**

Run: `OPENAI_API_KEY=sk-fake LLM_PROVIDER=openai .venv/Scripts/python -m pytest -q`
Expected: `604 passed`

Run: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Expected: `604 passed`, clean. If a previously passing test now fails because it relied on a key from the environment, it was never isolated: make it set the key itself.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "Keep a developer's .env keys out of the test suite

A real OPENAI_API_KEY in backend/.env turned seven ingestion tests red, because
they assume no vision provider is configured. Every test now starts with the
provider keys blanked."
```

---

### Task 1: Deepgram client

**Files:**
- Create: `backend/app/speech/__init__.py`
- Create: `backend/app/speech/deepgram.py`
- Modify: `backend/app/config.py` (add settings after `max_tokens_per_session`)
- Modify: `backend/tests/test_no_llm_in_engine.py:19`
- Test: `backend/tests/test_speech_deepgram.py`

**Interfaces:**
- Consumes: `app.config.Settings`, `app.config.get_settings`
- Produces:
  - `Settings.deepgram_api_key: str`, `deepgram_model: str`, `deepgram_base_url: str`, `deepgram_timeout_seconds: float`, `max_audio_bytes: int`
  - `class SpeechError(RuntimeError)` with attribute `kind: Literal["unavailable", "unreadable"]`, constructor `SpeechError(kind, message)`
  - `@dataclass(frozen=True) class Transcript: text: str; confidence: float`
  - `def transcribe(audio: bytes, mime_type: str, keyterms: Sequence[str] = (), *, settings: Settings | None = None) -> Transcript`

- [ ] **Step 1: Add the settings to `backend/app/config.py`**

Insert directly after the `max_tokens_per_session` line:

```python

    # --- Voice input ---
    #: Deepgram speech-to-text. Empty means voice input is off: the mic button is
    #: hidden and typing works exactly as before. Voice is an input channel only;
    #: it never changes how an answer is computed.
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    #: Overridden by tests to point at a loopback fake.
    deepgram_base_url: str = "https://api.deepgram.com"
    #: Seconds. A spoken question is a few seconds of audio; a request still
    #: running after this is a network problem, not a long transcription.
    deepgram_timeout_seconds: float = 20.0
    #: About a minute of compressed speech. The client stops at 30 s; this is the
    #: server's own guard, not a policy about question length.
    max_audio_bytes: int = 2 * 1024 * 1024
```

- [ ] **Step 2: Add `"speech"` to the engine guard**

In `backend/tests/test_no_llm_in_engine.py`, change line 19:

```python
FORBIDDEN_ROOTS = {"llm", "advisor", "speech", "anthropic", "openai"}
```

- [ ] **Step 3: Write the failing tests**

Create `backend/tests/test_speech_deepgram.py`:

```python
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
            "channels": [
                {"alternatives": [{"transcript": transcript, "confidence": confidence}]}
            ]
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_speech_deepgram.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'app.speech'`

- [ ] **Step 5: Create the package**

`backend/app/speech/__init__.py`:

```python
"""Speech-to-text for the advisor's voice input.

An input channel only. Nothing here reasons about degrees, and the degree engine
(app/audit/, app/catalog/) may never import it - test_no_llm_in_engine.py
enforces that.
"""
```

`backend/app/speech/deepgram.py`:

```python
"""Deepgram pre-recorded transcription over plain HTTP.

`urllib`, deliberately, as for Gemini and Ollama in app/llm/chat.py: one POST and
one JSON path do not justify an SDK dependency.

Two rules this module keeps:

* The key travels only in the Authorization header. Never in the URL, where it
  would end up in proxy and server logs.
* Deepgram's own error text is logged here and never raised. It can name the
  account or project, and it goes nowhere near a client.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

SpeechErrorKind = Literal["unavailable", "unreadable"]


class SpeechError(RuntimeError):
    """Transcription failed.

    `unavailable`: the service cannot be used right now (no key, rejected key, no
    credit, rate limited, down, unreachable, or a reply we could not parse).
    `unreadable`: the service is fine but could not decode this recording.
    """

    def __init__(self, kind: SpeechErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: SpeechErrorKind = kind


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float


def _listen_url(settings: Settings, keyterms: Sequence[str]) -> str:
    params = [
        ("model", settings.deepgram_model),
        ("smart_format", "true"),
        # Opt out of Deepgram's Model Improvement Program: students' questions
        # about their own records are not training data.
        ("mip_opt_out", "true"),
    ]
    params += [("keyterm", term) for term in keyterms]
    base = settings.deepgram_base_url.rstrip("/")
    return f"{base}/v1/listen?{urllib.parse.urlencode(params)}"


def transcribe(
    audio: bytes,
    mime_type: str,
    keyterms: Sequence[str] = (),
    *,
    settings: Settings | None = None,
) -> Transcript:
    """Transcribe one recording. Raises SpeechError; never returns a partial result."""
    settings = settings or get_settings()
    if not settings.deepgram_api_key:
        raise SpeechError("unavailable", "no Deepgram key is configured")

    request = urllib.request.Request(
        _listen_url(settings, keyterms),
        data=audio,
        method="POST",
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": mime_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.deepgram_timeout_seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        log.warning("Deepgram returned HTTP %s: %s", exc.code, detail)
        kind: SpeechErrorKind = "unreadable" if exc.code == 400 else "unavailable"
        raise SpeechError(kind, f"Deepgram returned HTTP {exc.code}") from exc
    except OSError as exc:  # URLError, refused connections and timeouts are all OSError
        log.warning("Deepgram could not be reached: %s", exc)
        raise SpeechError("unavailable", "Deepgram could not be reached") from exc

    return _parse(raw)


def _parse(raw: bytes) -> Transcript:
    try:
        best = json.loads(raw)["results"]["channels"][0]["alternatives"][0]
        text = str(best.get("transcript") or "").strip()
        confidence = float(best.get("confidence") or 0.0)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        log.warning("Deepgram reply was not in the documented shape: %r", raw[:200])
        raise SpeechError("unavailable", "Deepgram's reply was not in the expected shape") from exc
    return Transcript(text=text, confidence=confidence)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_speech_deepgram.py tests/test_no_llm_in_engine.py -q`
Expected: all pass

- [ ] **Step 7: Run the full suite, lint and format**

Run: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Expected: `604 + (new) passed`, ruff and black clean. If black reports the new files, run `.venv/Scripts/black app/speech tests/test_speech_deepgram.py` and re-check.

- [ ] **Step 8: Commit**

```bash
git add backend/app/speech backend/app/config.py backend/tests/test_speech_deepgram.py backend/tests/test_no_llm_in_engine.py
git commit -m "Add a Deepgram speech-to-text client

Plain urllib like the Gemini and Ollama providers. The key only travels in the
Authorization header, mip_opt_out keeps students' audio out of model training,
and every failure becomes a SpeechError the route can map without leaking
Deepgram's error text. The engine guard now forbids importing app.speech."
```

---

### Task 2: Transcribe route, key terms and health flag

**Files:**
- Create: `backend/app/speech/keyterms.py`
- Modify: `backend/app/routers/chat.py` (imports; `AdvisorHealth`; `advisor_health`; new route after `reset_conversation`)
- Modify: `.env.example` (after the Ollama block)
- Test: `backend/tests/test_transcribe_api.py`

**Interfaces:**
- Consumes: `transcribe`, `SpeechError`, `Transcript` from Task 1; `Settings.deepgram_api_key`, `Settings.max_audio_bytes`; `app.catalog.registry.registry.list_programs() -> list[Program]`; `Program.courses: list[Course]`, `Course.code: str`; `CurrentUser` from `app.auth.dependencies`
- Produces:
  - `def spoken_course_codes(programs: Iterable[Program]) -> list[str]`
  - `POST /advisor/transcribe`: multipart field `audio`, response `{"text": str, "confidence": float}`, errors `413` / `415` / `422` / `503` with fixed `detail` strings
  - `GET /advisor/health` gains `"voice_available": bool`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_transcribe_api.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_transcribe_api.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'app.speech.keyterms'`

- [ ] **Step 3: Create `backend/app/speech/keyterms.py`**

```python
"""Catalog course codes, in the form a person says them, as Deepgram key terms.

A general speech model hears "COSC 241" as anything from "Kasich two forty-one"
to "cause C 241". Keyterm prompting biases it toward the exact strings the
advisor understands, and the catalog is the only honest source of those strings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.catalog.schema import Program

_CODE = re.compile(r"^([A-Z]+)(\d+[A-Z]*)$")


def spoken_course_codes(programs: Iterable[Program]) -> list[str]:
    """`COSC241` -> `COSC 241`, plus each subject on its own. Sorted, no duplicates."""
    subjects: set[str] = set()
    codes: set[str] = set()
    for program in programs:
        for course in program.courses:
            match = _CODE.match(course.code.upper())
            if match:
                subjects.add(match[1])
                codes.add(f"{match[1]} {match[2]}")
            else:
                codes.add(course.code)
    return sorted(subjects) + sorted(codes)
```

- [ ] **Step 4: Add the route and the health flag to `backend/app/routers/chat.py`**

Replace the import block's `from fastapi import APIRouter, HTTPException` line with:

```python
from fastapi import APIRouter, File, HTTPException, UploadFile
```

Add after `from app.llm.chat import get_chat_provider`:

```python
from app.speech.deepgram import SpeechError, transcribe
from app.speech.keyterms import spoken_course_codes
```

Add after `SUGGESTED_QUESTIONS = [...]`:

```python

#: Fixed strings. Deepgram's own error text can name the account or project, so
#: it is logged in app.speech and never forwarded.
VOICE_UNAVAILABLE = "Voice input isn't available right now."
VOICE_UNREADABLE = "That recording could not be read."
VOICE_NOT_AUDIO = "Upload an audio recording."
VOICE_TOO_LARGE = "That recording is too long. Keep questions under 30 seconds."
```

In `class AdvisorHealth`, add after `degraded: bool`:

```python
    #: True when a Deepgram key is set. The UI hides the mic button otherwise.
    voice_available: bool
```

In `advisor_health()`, add to the `AdvisorHealth(...)` call after `degraded=not available,`:

```python
        voice_available=bool(get_settings().deepgram_api_key),
```

Add after `class AdvisorHealth` (before `def _store`):

```python

class TranscribeResponse(BaseModel):
    text: str
    #: Deepgram's 0-1 confidence. The UI asks the student to check the text
    #: below 0.6; it never decides anything on its own.
    confidence: float
```

Append at the end of the file:

```python


@router.post("/transcribe", response_model=TranscribeResponse)
def transcribe_question(user: CurrentUser, audio: UploadFile = File(...)) -> TranscribeResponse:
    """Turn a spoken question into text for the student to review.

    Voice is an input channel only: this returns text for the question box and
    never asks the advisor anything itself. The audio is held in memory for the
    length of this request and never written to disk.

    `user` is unused beyond requiring a signed-in caller - an open endpoint
    would let anyone spend the team's Deepgram credit.
    """
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise HTTPException(status_code=503, detail=VOICE_UNAVAILABLE)

    # Browsers send parameters ("audio/webm;codecs=opus"). Deepgram detects the
    # codec itself, so only the bare type is forwarded.
    mime_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
    if not mime_type.startswith("audio/"):
        raise HTTPException(status_code=415, detail=VOICE_NOT_AUDIO)

    data = audio.file.read(settings.max_audio_bytes + 1)
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail=VOICE_TOO_LARGE)
    if not data:
        raise HTTPException(status_code=422, detail=VOICE_UNREADABLE)

    try:
        result = transcribe(
            data, mime_type, spoken_course_codes(registry.list_programs()), settings=settings
        )
    except SpeechError as exc:
        if exc.kind == "unreadable":
            raise HTTPException(status_code=422, detail=VOICE_UNREADABLE) from None
        raise HTTPException(status_code=503, detail=VOICE_UNAVAILABLE) from None

    return TranscribeResponse(text=result.text, confidence=result.confidence)
```

This is a plain `def`, like every other route here, so FastAPI runs it in a thread pool and the blocking `urllib` call does not stall the event loop. `from None` keeps Deepgram's message out of the exception chain as well as the response.

- [ ] **Step 5: Update `.env.example`**

Insert after the `OLLAMA_TIMEOUT_SECONDS=60` line:

```

# --- Voice input: Deepgram speech-to-text ---
# Optional. Empty = no mic button, and typing works exactly as before. The key
# stays on the server; the browser never sees it. Audio is sent with
# mip_opt_out=true so Deepgram does not keep it for model training.
DEEPGRAM_API_KEY=
DEEPGRAM_MODEL=nova-3
DEEPGRAM_TIMEOUT_SECONDS=20
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_transcribe_api.py tests/test_advisor.py tests/test_auth.py -q`
Expected: all pass. `test_auth.py` includes the live route-table test that checks every student route is protected; it must stay green.

- [ ] **Step 7: Run the full suite, lint and format**

Run: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Expected: all pass, clean.

- [ ] **Step 8: Commit**

```bash
git add backend/app/speech/keyterms.py backend/app/routers/chat.py backend/tests/test_transcribe_api.py .env.example
git commit -m "Add POST /advisor/transcribe and report voice on /advisor/health

Signed-in only, so nobody else can spend the Deepgram credit. Wrong type, oversize
and empty uploads are refused before any Deepgram call. Catalog course codes go
along as keyterms so 'COSC 241' is heard as a course. Deepgram's error text is
never forwarded."
```

---

### Task 3: Mic button in the advisor panel

**Files:**
- Modify: `frontend/src/api.js` (after `resetConversation`)
- Create: `frontend/src/components/useVoiceRecorder.js`
- Modify: `frontend/src/components/AdvisorChat.jsx`

**Interfaces:**
- Consumes: `POST /advisor/transcribe` → `{ text, confidence }`, and errors carrying `error.status`; `GET /advisor/health` → `voice_available`
- Produces:
  - `transcribeAudio(blob: Blob): Promise<{ text: string, confidence: number }>`
  - `useVoiceRecorder({ maxSeconds?: number, onRecorded: (blob: Blob) => void }): { supported: boolean, recording: boolean, error: string | null, start: () => Promise<void>, stop: () => void }`

There is no frontend test runner in this repo (CI runs `npm run test --if-present`). Verification is lint, build and the manual browser checks in Step 6. Do not add a test framework in this task.

- [ ] **Step 1: Add `transcribeAudio` to `frontend/src/api.js`**

Insert after the `resetConversation` export:

```js

/**
 * POST /advisor/transcribe - a spoken question, returned as text to REVIEW.
 *
 * The result goes into the question box, never straight to the advisor: a
 * misheard course code should be caught by the student, not answered.
 * The filename's extension is cosmetic; Deepgram detects the format itself.
 */
export function transcribeAudio(blob) {
  const form = new FormData()
  const extension = (blob.type.split('/')[1] ?? 'webm').split(';')[0] || 'webm'
  form.append('audio', blob, `question.${extension}`)
  return request('/advisor/transcribe', { method: 'POST', body: form })
}
```

- [ ] **Step 2: Create `frontend/src/components/useVoiceRecorder.js`**

```js
import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Records one clip from the microphone.
 *
 * Owns the whole MediaRecorder lifecycle so the chat panel only sees
 * `recording` and a finished Blob. Three things here are load-bearing:
 *
 * - The mic tracks are stopped when a recording ends AND on unmount. Otherwise
 *   the browser's red "recording" indicator stays on after the student is done.
 * - `startingRef` blocks a second start while the permission prompt is open, so
 *   a double-click cannot open two streams.
 * - On unmount `onstop` is detached before stopping, so a transcript can never
 *   arrive for a panel that no longer exists.
 */
export function useVoiceRecorder({ maxSeconds = 30, onRecorded }) {
  const supported =
    typeof window !== 'undefined' &&
    typeof window.MediaRecorder !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia)

  const [recording, setRecording] = useState(false)
  const [error, setError] = useState(null)
  const recorderRef = useRef(null)
  const streamRef = useRef(null)
  const timerRef = useRef(null)
  const startingRef = useRef(false)
  const onRecordedRef = useRef(onRecorded)

  useEffect(() => {
    onRecordedRef.current = onRecorded
  }, [onRecorded])

  const release = useCallback(() => {
    clearTimeout(timerRef.current)
    timerRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
  }, [])

  const stop = useCallback(() => {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') recorder.stop()
  }, [])

  const start = useCallback(async () => {
    if (!supported || recorderRef.current || startingRef.current) return
    startingRef.current = true
    setError(null)
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      startingRef.current = false
      setError('Microphone access was blocked. You can still type your question.')
      return
    }

    const recorder = new MediaRecorder(stream)
    const chunks = []
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    recorder.onstop = () => {
      const type = recorder.mimeType || 'audio/webm'
      release()
      setRecording(false)
      const blob = new Blob(chunks, { type })
      if (blob.size > 0) onRecordedRef.current?.(blob)
      else setError("Didn't catch that — try again.")
    }

    streamRef.current = stream
    recorderRef.current = recorder
    startingRef.current = false
    recorder.start()
    setRecording(true)
    timerRef.current = setTimeout(stop, maxSeconds * 1000)
  }, [supported, maxSeconds, release, stop])

  useEffect(
    () => () => {
      const recorder = recorderRef.current
      if (recorder) {
        recorder.onstop = null
        if (recorder.state !== 'inactive') recorder.stop()
      }
      release()
    },
    [release],
  )

  return { supported, recording, error, start, stop }
}
```

- [ ] **Step 3: Wire the mic into `frontend/src/components/AdvisorChat.jsx`**

3a. Replace the imports at the top with:

```js
import { useCallback, useEffect, useRef, useState } from 'react'
import { askAdvisor, getAdvisorHealth, resetConversation, transcribeAudio } from '../api'
import { Alert, Button, Card, Spinner } from './ui'
import { useVoiceRecorder } from './useVoiceRecorder'
```

3b. Add these module-level helpers after `const CONVERSATION_ID = 'default'`:

```js

/** Below this Deepgram confidence the student is asked to check the text. */
const LOW_CONFIDENCE = 0.6

const VOICE_MESSAGES = {
  unavailable: "Voice input isn't available right now — you can still type.",
  unreadable: "Couldn't read that recording — try again.",
  empty: "Didn't catch that — try again.",
  lowConfidence: 'Check this — I may have misheard.',
}

/** Speech is added after anything already typed, never in place of it. */
export function mergeDraft(current, spoken) {
  const typed = current.trim()
  return typed ? `${typed} ${spoken}` : spoken
}
```

3c. Inside `AdvisorChat`, after `const scroller = useRef(null)`, add:

```js
  const input = useRef(null)
  const [transcribing, setTranscribing] = useState(false)
  const [voiceNote, setVoiceNote] = useState(null)

  const handleRecorded = useCallback(async (blob) => {
    setTranscribing(true)
    try {
      const { text, confidence } = await transcribeAudio(blob)
      if (!text.trim()) {
        setVoiceNote(VOICE_MESSAGES.empty)
        return
      }
      setDraft((current) => mergeDraft(current, text.trim()))
      setVoiceNote(confidence < LOW_CONFIDENCE ? VOICE_MESSAGES.lowConfidence : null)
      input.current?.focus()
    } catch (err) {
      setVoiceNote(
        [413, 415, 422].includes(err.status)
          ? VOICE_MESSAGES.unreadable
          : VOICE_MESSAGES.unavailable,
      )
    } finally {
      setTranscribing(false)
    }
  }, [])

  const recorder = useVoiceRecorder({ onRecorded: handleRecorded })
  const voiceOn = Boolean(health?.voice_available)
  const voiceBusy = recorder.recording || transcribing

  function toggleRecording() {
    setVoiceNote(null)
    if (recorder.recording) recorder.stop()
    else recorder.start()
  }
```

3d. In `send()`, after `setDraft('')`, add:

```js
    setVoiceNote(null)
```

3e. Replace the whole `<form ...>...</form>` element with:

```jsx
        <form
          className="mt-4 flex gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            send()
          }}
        >
          <label className="sr-only" htmlFor="advisor-question">
            Your question
          </label>
          <input
            id="advisor-question"
            ref={input}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value)
              setVoiceNote(null)
            }}
            placeholder="What should I take next semester?"
            disabled={busy || transcribing}
            maxLength={2000}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 disabled:bg-slate-50"
          />
          {voiceOn && (
            <MicButton
              recording={recorder.recording}
              transcribing={transcribing}
              disabled={busy || transcribing || !recorder.supported}
              onClick={toggleRecording}
            />
          )}
          <Button type="submit" disabled={busy || voiceBusy || !draft.trim()}>
            Ask
          </Button>
        </form>

        {voiceOn && (voiceNote || recorder.error || !recorder.supported) && (
          <p className="mt-2 text-xs text-slate-500" role="status">
            {voiceNote ??
              recorder.error ??
              'This browser cannot record audio. You can still type your question.'}
          </p>
        )}
```

3f. Disable the suggestion chips while voice is busy. In the suggestion `<button>`, change `disabled={busy}` to:

```jsx
                disabled={busy || voiceBusy}
```

3g. Add the `MicButton` component at the end of the file, after `AdvisorTurn`:

```jsx

/** Toggle: click to start, click to stop. Red and pulsing while it listens. */
function MicButton({ recording, transcribing, disabled, onClick }) {
  const label = recording
    ? 'Stop recording'
    : transcribing
      ? 'Transcribing your question'
      : 'Ask by voice'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled && !recording}
      aria-pressed={recording}
      aria-label={label}
      title={label}
      className={`inline-flex h-9 w-9 shrink-0 items-center justify-center self-center rounded-full ring-1 ring-inset transition disabled:cursor-not-allowed disabled:opacity-50 ${
        recording
          ? 'animate-pulse bg-rose-600 text-white ring-rose-600'
          : 'bg-white text-slate-700 ring-slate-300 hover:bg-slate-50'
      }`}
    >
      {transcribing ? (
        <span className="size-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
      ) : (
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <rect x="9" y="3" width="6" height="12" rx="3" />
          <path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
        </svg>
      )}
    </button>
  )
}
```

The spinner is a bare span, not `Spinner` from `ui.jsx`, because that component always renders a text label, which does not fit a 36 px round button. It uses the same classes.

- [ ] **Step 4: Lint and build**

Run (from `frontend/`): `npm run lint && npm run build`
Expected: no lint errors, build succeeds. If oxlint flags `mergeDraft` as an export from a component file (react-refresh rule), remove the `export` keyword. Nothing imports it.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/components/useVoiceRecorder.js frontend/src/components/AdvisorChat.jsx
git commit -m "Add a mic to the advisor panel

Click to start, click to stop, auto-stop at 30 s. The transcript lands in the
question box after anything already typed and is never auto-sent. Every voice
failure is a quiet note under the input; typing keeps working. The mic is hidden
unless the backend reports voice_available."
```

- [ ] **Step 6: Manual browser check (Chrome)**

With `DEEPGRAM_API_KEY` set in `backend/.env`, start the app (`mprocs` from the repo root, or `uvicorn app.main:app --reload` in `backend/` plus `npm run dev` in `frontend/`), sign in, confirm a transcript, open **Ask the advisor**, and check:

1. The mic button is visible. With the key removed and the backend restarted, it is gone.
2. Click the mic, say *"What can I take after COSC 241?"*, click again. The box reads `COSC 241`, the input is focused, and nothing was sent.
3. Type `Also,` first, then speak. The result is `Also, <transcript>`.
4. Click the mic and stay silent for two seconds, then stop. You see `Didn't catch that — try again.`
5. Start recording, then switch to the Dashboard tab. The browser's red recording indicator disappears.
6. Double-click the mic quickly. Only one recording starts.
7. Block the microphone in site settings and click the mic. You see the "Microphone access was blocked" note, and typing still works.
8. Leave a recording running for 30 seconds. It stops by itself and transcribes.

Write down any check that fails and fix it before Task 4.

---

### Task 4: Live check and documentation

**Files:**
- Modify: `docs/advisor.md` (new section "Voice input", before "## What is NOT tested")
- Modify: `PROJECT_STATUS.md` ("No voice input" bullet under *Unfinished — significant*)
- Modify: `DEMO_GUIDE.md` (one voice step)

**Interfaces:**
- Consumes: everything above
- Produces: documentation only

- [ ] **Step 1: Add a "Voice input" section to `docs/advisor.md`**

Insert before `## What is NOT tested`:

```markdown
## Voice input

The mic in the advisor panel is an input channel only. It turns speech into text
for the question box and never asks the advisor anything itself.

- **Flow:** browser `MediaRecorder` → `POST /advisor/transcribe` (signed in) →
  `app/speech/deepgram.py` → Deepgram `/v1/listen` (`nova-3`, `smart_format`,
  `mip_opt_out=true`) → text in the box → the student presses Ask.
- **Key terms:** every catalog course code in spoken form ("COSC 241") is sent as
  a Deepgram `keyterm`, built by `app/speech/keyterms.py` from the loaded catalog.
- **Never auto-sent.** A misheard course code should be caught by the student,
  not answered. Below 0.6 confidence the panel asks them to check it.
- **Privacy:** audio lives in memory for one request. It is never written to
  disk, and Deepgram is told not to keep it for training.
- **Degrades like the model:** no `DEEPGRAM_API_KEY` means no mic button. A
  rejected key, no credit, a rate limit or a timeout means a quiet note, and
  typing still works. Deepgram's error text is logged, never shown.
- `app/audit/` and `app/catalog/` may not import `app.speech`
  (`test_no_llm_in_engine.py`).
```

- [ ] **Step 2: Run the live check with the real key**

Repeat Task 3 Step 6, check 2, against real Deepgram. Record the exact transcript Deepgram returned for *"What can I take after COSC 241?"* and whether the advisor answered it.

- [ ] **Step 3: Update `PROJECT_STATUS.md`**

Replace the `- **No voice input.** ...` bullet with the result of Step 2, for example:

```markdown
- ~~**No voice input.**~~ **Built (Sep 22 2026).** Mic in the advisor panel,
  Deepgram `nova-3` behind `POST /advisor/transcribe`, with catalog course codes
  as keyterms. **Live-verified on <date>:** "What can I take after COSC 241?" was
  transcribed as "<exact transcript>". This was the first call from this repo to
  a live external service. See [docs/advisor.md](docs/advisor.md#voice-input).
```

If the live check has not been run, say so plainly instead of claiming it.

- [ ] **Step 4: Add a voice step to `DEMO_GUIDE.md`**

Read `DEMO_GUIDE.md` first and add this step in its own style, right after the step that opens the advisor panel:

```markdown
**Voice (needs `DEEPGRAM_API_KEY`).** Click the mic, say *"What can I take after
COSC 241?"*, click again. Point out that the transcript lands in the box and is
**not** sent: the student checks it first, the same rule as the transcript
review screen. Then press Ask.
```

- [ ] **Step 5: Final verification**

Run from `backend/`: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Run from `frontend/`: `npm run lint && npm run build`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add docs/advisor.md PROJECT_STATUS.md DEMO_GUIDE.md
git commit -m "Document voice input and record the live Deepgram check"
```
