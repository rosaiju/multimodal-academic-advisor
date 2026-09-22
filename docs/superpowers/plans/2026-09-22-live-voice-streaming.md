# Live Voice Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Words appear in the advisor's question box while the student speaks, and recording ends by itself about 1.5 s after they stop, with course codes converted live.

**Architecture:** The browser streams `MediaRecorder` chunks over a WebSocket to a new backend route, `/advisor/listen`. That route relays them to Deepgram's live API through a `websockets` client in `app/speech/live.py`, and sends back the running transcript: finished phrases plus the current partial, catalog-checked for course codes. The session ends on Deepgram's `UtteranceEnd`, a client stop, or a limit. The record-then-upload hook is removed from the UI.

**Tech Stack:** FastAPI WebSockets, `websockets` 14+ asyncio client/server (already installed via `uvicorn[standard]`), React 19 + Vite 8, pytest with loopback fakes.

**Spec:** `docs/superpowers/specs/2026-09-22-live-voice-streaming-design.md`

## Global Constraints

- Branch: `feature/voice-input`. Baseline: backend **674 passed**, `ruff check .` and `black --check .` clean; frontend lint shows only pre-existing `ReviewStep.jsx` warnings; `npm run build` clean.
- The Deepgram key only ever appears in the `Authorization: Token <key>` header of the backend→Deepgram handshake. Never in a URL, a message to the browser, or a log line.
- The access token travels in the first WebSocket **message** (`{"type":"start","token":…}`), never in the URL.
- Deepgram live query parameters (verbatim): `model=<deepgram_model>`, `smart_format=true`, `mip_opt_out=true`, `interim_results=true`, `endpointing=300`, `utterance_end_ms=1500`, plus one `keyterm` per catalog term.
- Route limits (module constants in `app/routers/voice.py`): `START_TIMEOUT_SECONDS = 5.0`, `MAX_SESSION_SECONDS = 30.0`, `NO_SPEECH_SECONDS = 8.0`, `FINISH_GRACE_SECONDS = 2.0`. The audio cap is `settings.max_audio_bytes`.
- Browser → server messages: `start`, binary audio, `stop`. Server → browser messages: `ready`, `transcript`, `done`, `error` with `reason` of `unauthorized` or `unavailable`. Exact shapes are in the spec's Protocol table.
- Course codes are rewritten only via `normalize_course_mentions`, so only when the result is a catalog course.
- Never auto-send. Voice failures never block typing. Deepgram's error text is logged, never forwarded.
- `app/audit/` and `app/catalog/` must not import `app.speech`, which the existing guard test enforces.
- User-facing strings (reuse `VOICE_MESSAGES` in `AdvisorChat.jsx`), verbatim:
  - unavailable: `Voice input isn't available right now — you can still type.`
  - empty: `Didn't catch that — try again.`
  - low confidence (`confidence < 0.6`): `Check this — I may have misheard.`
  - mic blocked: `Microphone access was blocked. You can still type your question.`
  - recorder failed to start: `Recording could not start in this browser. You can still type your question.`

## Review Focus

1. **`UtteranceEnd` needs audio to keep flowing after the speaker stops.** Probed Sep 22: over a continuous PCM stream it arrived about 1 s after the last final. When a WAV file simply ran out, it never came. If a browser's recorder stalls after speech, the session must still end, via the 30 s limit, and keep the text. → Task 3 test `test_time_limit_ends_with_text_so_far`; Task 5 live check streams continuous PCM.
2. **The backend restarts or the network drops mid-sentence.** The student must keep what they already saw and get the unavailable note. → Task 4 harness scenario "socket closes before done keeps heard text".
3. **Stop is clicked before the server says `ready`,** for example during a slow Deepgram handshake. The session must cancel cleanly, release the mic, and never start recording afterwards. → Task 4 harness scenario "stop before ready cancels".
4. **Safari records `audio/mp4` fragments.** Deepgram's live decoding of fragmented mp4 is unverified. Chrome and Edge (webm/opus) are the supported demo browsers. A Safari failure must degrade to the unavailable note, not hang. → Task 4 Step 7 manual check 7 (if a Mac is available); documented in Task 5.
5. **A long pause mid-sentence (over 1.5 s) ends the session early.** That is the chosen behaviour (option B). The student can click the mic again and keep going: the new text is appended after what is in the box. → Task 4 Step 7 manual check 4.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `backend/pyproject.toml` | modify | Declare `websockets>=14` |
| `backend/app/speech/live.py` | create | `LiveTranscript`, live events, `DeepgramLive` (the only code talking to Deepgram's live API) |
| `backend/app/auth/dependencies.py` | modify | Extract `user_for_token` from `current_user` |
| `backend/app/routers/voice.py` | create | `WS /advisor/listen`: auth, relay, session limits |
| `backend/app/main.py` | modify | Register the voice router |
| `backend/tests/test_live_transcript.py` | create | `LiveTranscript` + message parsing |
| `backend/tests/test_speech_live.py` | create | `DeepgramLive` against a loopback fake Deepgram |
| `backend/tests/test_listen_api.py` | create | The route through FastAPI's WebSocket test client |
| `frontend/vite.config.js` | modify | `ws: true` on the `/api` proxy |
| `frontend/src/api.js` | modify | `getAccessToken`, `openLiveTranscription`; remove `transcribeAudio` |
| `frontend/src/components/useLiveTranscription.js` | create | Mic + socket + recorder lifecycle |
| `frontend/src/components/useVoiceRecorder.js` | delete | Replaced |
| `frontend/src/components/AdvisorChat.jsx` | modify | Live text in the box, mic toggles listening |
| `docs/advisor.md`, `DEMO_GUIDE.md`, `PROJECT_STATUS.md` | modify | Document streaming and the live check |

---

### Task 1: `LiveTranscript` and live message parsing

**Files:**
- Create: `backend/app/speech/live.py` (pure parts only in this task)
- Test: `backend/tests/test_live_transcript.py`

**Interfaces:**
- Consumes: `normalize_course_mentions(text, catalog_codes) -> str` from `app.speech.course_codes`
- Produces:
  - `@dataclass(frozen=True) class Partial: text: str`
  - `@dataclass(frozen=True) class Final: text: str; confidence: float`
  - `@dataclass(frozen=True) class UtteranceEnd`
  - `LiveEvent = Partial | Final | UtteranceEnd`
  - `def parse_live_message(raw: str | bytes) -> LiveEvent | None`
  - `class LiveTranscript` with `add_partial(text)`, `add_final(text, confidence)`, `text(catalog_codes) -> str`, the property `confidence -> float` and the property `heard_speech -> bool`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_live_transcript.py`:

```python
"""What the student sees while they talk, and how Deepgram's messages are read.

Pure logic - no sockets. The message shapes are the ones Deepgram's live API
returned on Sep 22 2026 (see test_speech_live.py for the wire itself).
"""

from __future__ import annotations

import json

from app.speech.live import Final, LiveTranscript, Partial, UtteranceEnd, parse_live_message

CODES = ["UNIV101", "COSC243", "COSC241"]


def results(text: str, *, is_final: bool, confidence: float = 0.9) -> str:
    return json.dumps(
        {
            "type": "Results",
            "is_final": is_final,
            "speech_final": is_final,
            "channel": {"alternatives": [{"transcript": text, "confidence": confidence}]},
        }
    )


class TestLiveTranscript:
    def test_starts_empty(self) -> None:
        transcript = LiveTranscript()
        assert transcript.text(CODES) == ""
        assert transcript.heard_speech is False
        assert transcript.confidence == 0.0

    def test_a_new_partial_replaces_the_old_one(self) -> None:
        transcript = LiveTranscript()
        transcript.add_partial("Can I")
        transcript.add_partial("Can I take")
        assert transcript.text(CODES) == "Can I take"

    def test_finals_accumulate_and_clear_the_partial(self) -> None:
        transcript = LiveTranscript()
        transcript.add_partial("Can I")
        transcript.add_final("Can I take", 0.9)
        transcript.add_partial("University")
        assert transcript.text(CODES) == "Can I take University"
        transcript.add_final("University one zero one?", 0.8)
        assert transcript.text(CODES) == "Can I take UNIV 101?"

    def test_codes_convert_across_a_final_and_a_partial(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("Computer Science two forty", 0.9)
        transcript.add_partial("three")
        assert transcript.text(CODES) == "COSC 243"

    def test_an_unknown_code_is_shown_as_heard(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("computer science nine ninety nine", 0.9)
        assert transcript.text(CODES) == "computer science nine ninety nine"

    def test_confidence_is_the_mean_of_the_finals(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("a", 0.9)
        transcript.add_final("b", 0.5)
        transcript.add_partial("c")
        assert transcript.confidence == 0.7

    def test_an_empty_final_is_not_speech(self) -> None:
        transcript = LiveTranscript()
        transcript.add_final("   ", 0.0)
        assert transcript.heard_speech is False
        assert transcript.confidence == 0.0

    def test_a_partial_counts_as_speech(self) -> None:
        transcript = LiveTranscript()
        transcript.add_partial("Can")
        assert transcript.heard_speech is True


class TestParseLiveMessage:
    def test_interim_result_is_a_partial(self) -> None:
        assert parse_live_message(results("Can I take", is_final=False)) == Partial("Can I take")

    def test_final_result_is_a_final(self) -> None:
        event = parse_live_message(results("Can I take COSC 241?", is_final=True, confidence=0.97))
        assert event == Final("Can I take COSC 241?", 0.97)

    def test_empty_final_is_ignored(self) -> None:
        assert parse_live_message(results("", is_final=True)) is None

    def test_utterance_end(self) -> None:
        raw = json.dumps({"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 2.4})
        assert parse_live_message(raw) == UtteranceEnd()

    def test_other_message_types_are_ignored(self) -> None:
        assert parse_live_message(json.dumps({"type": "Metadata", "request_id": "x"})) is None
        assert parse_live_message(json.dumps({"type": "SpeechStarted"})) is None

    def test_malformed_messages_are_ignored(self) -> None:
        assert parse_live_message("not json") is None
        assert parse_live_message(json.dumps({"type": "Results"})) is None
        assert parse_live_message(b"\x00\x01") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `backend/`): `.venv/Scripts/python -m pytest tests/test_live_transcript.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'app.speech.live'`

- [ ] **Step 3: Create `backend/app/speech/live.py` with the pure parts**

```python
"""Deepgram live transcription: what the student sees while they talk.

Two halves. `LiveTranscript` and `parse_live_message` are pure and decide what
the question box shows. `DeepgramLive` is the one connection to Deepgram's
live API.

The box shows finished phrases plus the phrase still being heard, and course
codes are converted on the combined text - so "computer science two forty" plus
a partial "three" becomes "COSC 243" as soon as the last word lands, and only
because COSC 243 is in the catalog.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.speech.course_codes import normalize_course_mentions

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Partial:
    """Deepgram's current guess at the phrase being spoken. Replaced, not kept."""

    text: str


@dataclass(frozen=True)
class Final:
    """A finished phrase. Kept."""

    text: str
    confidence: float


@dataclass(frozen=True)
class UtteranceEnd:
    """Deepgram heard no words for utterance_end_ms: the speaker has paused."""


LiveEvent = Partial | Final | UtteranceEnd


def parse_live_message(raw: str | bytes) -> LiveEvent | None:
    """One Deepgram live message as an event, or None for anything we ignore."""
    try:
        message = json.loads(raw)
        kind = message.get("type")
        if kind == "UtteranceEnd":
            return UtteranceEnd()
        if kind != "Results":
            return None
        best = message["channel"]["alternatives"][0]
        text = str(best.get("transcript") or "")
        if message.get("is_final"):
            return Final(text, float(best.get("confidence") or 0.0)) if text.strip() else None
        return Partial(text)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        log.warning("ignoring unexpected Deepgram live message: %r", raw[:200])
        return None


@dataclass
class LiveTranscript:
    """Finished phrases plus the current partial, as the student should see them."""

    _finals: list[str] = field(default_factory=list)
    _confidences: list[float] = field(default_factory=list)
    _partial: str = ""

    def add_partial(self, text: str) -> None:
        self._partial = text.strip()

    def add_final(self, text: str, confidence: float) -> None:
        text = text.strip()
        if text:
            self._finals.append(text)
            self._confidences.append(confidence)
        self._partial = ""

    def text(self, catalog_codes: Iterable[str]) -> str:
        joined = " ".join([*self._finals, self._partial]).strip()
        return normalize_course_mentions(joined, catalog_codes)

    @property
    def confidence(self) -> float:
        if not self._confidences:
            return 0.0
        return sum(self._confidences) / len(self._confidences)

    @property
    def heard_speech(self) -> bool:
        return bool(self._finals or self._partial)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_live_transcript.py -q`
Expected: all pass. If `test_confidence_is_the_mean_of_the_finals` fails on float rounding (`0.7000000000000001`), change that assertion to `pytest.approx(0.7)` and add `import pytest`. That is a test-precision fix, not a code change.

- [ ] **Step 5: Full suite, lint, commit**

Run: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Expected: all pass (674 + new), clean.

```bash
git add backend/app/speech/live.py backend/tests/test_live_transcript.py
git commit -m "Add the live transcript model and Deepgram live message parsing"
```

---

### Task 2: `DeepgramLive` connection

**Files:**
- Modify: `backend/app/speech/live.py` (append)
- Modify: `backend/pyproject.toml` (dependencies)
- Test: `backend/tests/test_speech_live.py`

**Interfaces:**
- Consumes: `Settings` fields `deepgram_api_key`, `deepgram_model`, `deepgram_base_url`, `deepgram_timeout_seconds`; `SpeechError` from `app.speech.deepgram`; `parse_live_message` and `LiveEvent` from Task 1
- Produces:
  - `def live_url(settings: Settings, keyterms: Sequence[str]) -> str`
  - `class DeepgramLive` with these async methods:
    - `connect(keyterms: Sequence[str] = (), *, settings: Settings | None = None) -> None`, which raises `SpeechError("unavailable", …)`
    - `send_audio(chunk: bytes) -> None`
    - `finish() -> None`
    - `events() -> AsyncIterator[LiveEvent]`
    - `close() -> None`

- [ ] **Step 1: Declare the dependency**

In `backend/pyproject.toml`, add after the `"pyjwt>=2.10",` line in `dependencies`:

```toml
    # Deepgram's live API is a WebSocket. Already installed via uvicorn[standard];
    # declared because app/speech/live.py imports it directly.
    "websockets>=14",
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_speech_live.py`:

```python
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
                DeepgramLive().connect(settings=settings_for("http://127.0.0.1:9", deepgram_api_key=""))
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_speech_live.py -q`
Expected: collection error, `ImportError: cannot import name 'DeepgramLive' from 'app.speech.live'`

- [ ] **Step 4: Append `DeepgramLive` to `backend/app/speech/live.py`**

Add these imports to the existing import block (keep it sorted as ruff's `I` rule requires):

```python
import urllib.parse
from collections.abc import AsyncIterator, Iterable, Sequence

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from app.config import Settings, get_settings
from app.speech.deepgram import SpeechError
```

(`from collections.abc import Iterable` becomes the three-name import above.)

Append at the end of the file:

```python


def live_url(settings: Settings, keyterms: Sequence[str]) -> str:
    """The Deepgram live endpoint for these settings. The key is NOT in it."""
    base = settings.deepgram_base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base.removeprefix("https://")
    elif base.startswith("http://"):
        base = "ws://" + base.removeprefix("http://")
    params = [
        ("model", settings.deepgram_model),
        ("smart_format", "true"),
        # Students' questions about their records are not training data.
        ("mip_opt_out", "true"),
        # Partial phrases are what make the text appear while the student talks;
        # utterance_end_ms also requires them.
        ("interim_results", "true"),
        ("endpointing", "300"),
        # 1.5 s without words ends the session - the "stop when I pause" choice.
        ("utterance_end_ms", "1500"),
    ]
    params += [("keyterm", term) for term in keyterms]
    return f"{base}/v1/listen?{urllib.parse.urlencode(params)}"


class DeepgramLive:
    """One Deepgram live session. Every failure to connect is SpeechError("unavailable");
    a connection that drops later simply ends `events()`.
    """

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None

    async def connect(
        self, keyterms: Sequence[str] = (), *, settings: Settings | None = None
    ) -> None:
        settings = settings or get_settings()
        if not settings.deepgram_api_key:
            raise SpeechError("unavailable", "no Deepgram key is configured")
        try:
            self._ws = await connect(
                live_url(settings, keyterms),
                additional_headers={"Authorization": f"Token {settings.deepgram_api_key}"},
                open_timeout=settings.deepgram_timeout_seconds,
            )
        except (InvalidHandshake, OSError, TimeoutError) as exc:
            # The handshake response can name the account; it stays in the log.
            log.warning("Deepgram live connection failed: %s", exc)
            raise SpeechError("unavailable", "Deepgram live connection failed") from exc

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(chunk)
        except ConnectionClosed:
            pass  # events() ends on its own; the session winds down from there

    async def finish(self) -> None:
        """Ask Deepgram to flush what it has and close."""
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except ConnectionClosed:
            pass

    async def events(self) -> AsyncIterator[LiveEvent]:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = parse_live_message(raw)
                if event is not None:
                    yield event
        except ConnectionClosed as exc:
            log.warning("Deepgram live connection dropped: %s", exc)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_speech_live.py tests/test_live_transcript.py -q`
Expected: all pass. The refused-port test takes about 2 s on Windows, which is expected.

- [ ] **Step 6: Full suite, lint, commit**

Run: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Expected: all pass, clean. If black reformats `live.py` or the test file, run `.venv/Scripts/black <file>` and re-check.

```bash
git add backend/pyproject.toml backend/app/speech/live.py backend/tests/test_speech_live.py
git commit -m "Add a Deepgram live connection

Key in the handshake header only, mip_opt_out and interim results on, 1.5 s
utterance end. A failed handshake becomes SpeechError('unavailable') without
Deepgram's text; a dropped connection just ends the event stream."
```

---

### Task 3: `/advisor/listen` WebSocket route

**Files:**
- Modify: `backend/app/auth/dependencies.py` (extract `user_for_token`)
- Create: `backend/app/routers/voice.py`
- Modify: `backend/app/main.py` (import + `include_router`)
- Test: `backend/tests/test_listen_api.py`

**Interfaces:**
- Consumes: `DeepgramLive`, `LiveTranscript`, `Partial`, `Final`, `UtteranceEnd` (Tasks 1–2); `SpeechError`; `spoken_course_codes(programs)`; `registry.list_programs()`; `user_store(settings) -> UserStore`; `student_id_from_token`, `InvalidToken`; `UserStoreError`
- Produces:
  - `def user_for_token(token: str, settings: Settings, store: UserStore) -> User | None` in `app.auth.dependencies`
  - `WS /advisor/listen` implementing the spec's protocol
  - module constants `START_TIMEOUT_SECONDS`, `MAX_SESSION_SECONDS`, `NO_SPEECH_SECONDS`, `FINISH_GRACE_SECONDS` in `app.routers.voice`, which tests monkeypatch
  - the module attribute `app.routers.voice.DeepgramLive`, which tests replace

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_listen_api.py`:

```python
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

    def test_utterance_end_before_speech_does_not_end_it(self, client, voice_on, monkeypatch) -> None:
        use(monkeypatch, ScriptedLive([UtteranceEnd(), Final("still here", 0.9), UtteranceEnd()]))
        received = converse(client)
        assert received[-1] == {"type": "done", "text": "still here", "confidence": 0.9}

    def test_client_stop_ends_it_with_late_finals(self, client, voice_on, monkeypatch) -> None:
        use(monkeypatch, ScriptedLive([Partial("hello"), WAIT_FOR_FINISH, Final("hello there", 0.8)]))
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

    def test_deepgram_dropping_ends_it_with_text_so_far(self, client, voice_on, monkeypatch) -> None:
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_listen_api.py -q`
Expected: collection error, `ImportError: cannot import name 'voice' from 'app.routers'`

- [ ] **Step 3: Extract `user_for_token` in `backend/app/auth/dependencies.py`**

Replace the whole `current_user` function with:

```python
def user_for_token(token: str, settings: Settings, store: UserStore) -> User | None:
    """The account behind a token, or None for every kind of failure alike.

    Shared by `current_user` and the voice WebSocket, which carries its token in
    a message rather than a header. One function, so both paths accept exactly
    the same tokens.
    """
    try:
        student_id = student_id_from_token(token, secret=settings.jwt_signing_secret)
    except InvalidToken:
        return None
    try:
        return store.get(student_id)
    except UserStoreError:
        # A corrupt account file is our problem, not a hint to hand out.
        return None


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[UserStore, Depends(user_store)],
) -> User:
    """The account behind this request, or 401.

    Every failure - no header, malformed token, expired token, or a token whose
    account has since been deleted - produces the same 401 with the same message.
    Distinguishing them tells an attacker which half of a guess was right.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED
    user = user_for_token(credentials.credentials, settings, store)
    if user is None:
        raise _UNAUTHENTICATED
    return user
```

Run: `.venv/Scripts/python -m pytest tests/test_auth.py -q`
Expected: all pass. The refactor does not change behaviour.

- [ ] **Step 4: Create `backend/app/routers/voice.py`**

```python
"""WS /advisor/listen - live voice input, relayed to Deepgram.

The browser never talks to Deepgram and never sees the key. It signs in with
its first message (a token in a URL ends up in logs), streams audio, and gets
back the whole transcript so far after every change, course codes already
converted. The session ends when Deepgram says the student paused, when the
student clicks stop, or at a limit - and always with a `done` carrying whatever
was heard, so nothing the student saw is thrown away.

Voice is an input channel only: nothing here asks the advisor anything.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket

from app.auth.dependencies import user_for_token, user_store
from app.catalog.registry import registry
from app.config import Settings, get_settings
from app.speech.deepgram import SpeechError
from app.speech.keyterms import spoken_course_codes
from app.speech.live import DeepgramLive, Final, LiveTranscript, Partial, UtteranceEnd

log = logging.getLogger(__name__)

router = APIRouter(prefix="/advisor", tags=["advisor"])

#: Seconds the client has to send its `start` message.
START_TIMEOUT_SECONDS = 5.0
#: Hard ceiling on one session, from `ready`.
MAX_SESSION_SECONDS = 30.0
#: A session that hears nothing for this long ends empty ("Didn't catch that").
NO_SPEECH_SECONDS = 8.0
#: After asking Deepgram to finish, how long to wait for its last phrases.
FINISH_GRACE_SECONDS = 2.0
_WATCHDOG_TICK_SECONDS = 0.05


async def _send(websocket: WebSocket, payload: dict) -> bool:
    """Send, reporting False instead of raising when the browser has gone."""
    try:
        await websocket.send_json(payload)
        return True
    except Exception:  # any send failure means the browser has gone
        return False


async def _close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:  # already closed is fine
        pass


async def _fail(websocket: WebSocket, reason: str) -> None:
    await _send(websocket, {"type": "error", "reason": reason})
    await _close(websocket)


async def _signed_in(websocket: WebSocket, settings: Settings) -> bool:
    """True if the first message is a `start` carrying a valid token, in time."""
    try:
        message = await asyncio.wait_for(websocket.receive(), START_TIMEOUT_SECONDS)
        frame = json.loads(message.get("text") or "")
        token = frame.get("token") if frame.get("type") == "start" else None
    except (TimeoutError, ValueError, AttributeError):
        return False
    if not isinstance(token, str) or not token:
        return False
    return user_for_token(token, settings, user_store(settings)) is not None


@router.websocket("/listen")
async def listen(websocket: WebSocket) -> None:
    await websocket.accept()
    settings = get_settings()
    if not await _signed_in(websocket, settings):
        await _fail(websocket, "unauthorized")
        return
    if not settings.deepgram_api_key:
        await _fail(websocket, "unavailable")
        return

    programs = registry.list_programs()
    catalog_codes = [course.code for program in programs for course in program.courses]
    live = DeepgramLive()
    try:
        await live.connect(spoken_course_codes(programs), settings=settings)
    except SpeechError:
        await _fail(websocket, "unavailable")
        return
    try:
        await _relay(websocket, live, catalog_codes, settings.max_audio_bytes)
    finally:
        await live.close()


async def _relay(
    websocket: WebSocket, live: DeepgramLive, catalog_codes: list[str], max_audio_bytes: int
) -> None:
    transcript = LiveTranscript()
    if not await _send(websocket, {"type": "ready"}):
        return

    async def from_browser() -> str:
        received = 0
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return "disconnected"
            chunk = message.get("bytes")
            if chunk is not None:
                received += len(chunk)
                if received > max_audio_bytes:
                    return "audio_limit"
                await live.send_audio(chunk)
            elif message.get("text"):
                try:
                    if json.loads(message["text"]).get("type") == "stop":
                        return "stop"
                except (ValueError, AttributeError):
                    pass

    async def from_deepgram() -> str:
        async for event in live.events():
            if isinstance(event, UtteranceEnd):
                if transcript.heard_speech:
                    return "utterance_end"
                continue
            if isinstance(event, Partial):
                transcript.add_partial(event.text)
            elif isinstance(event, Final):
                transcript.add_final(event.text, event.confidence)
            text = transcript.text(catalog_codes)
            if not await _send(websocket, {"type": "transcript", "text": text}):
                return "disconnected"
        return "deepgram_closed"

    async def watchdog() -> str:
        loop = asyncio.get_running_loop()
        started = loop.time()
        while True:
            await asyncio.sleep(_WATCHDOG_TICK_SECONDS)
            elapsed = loop.time() - started
            if elapsed >= MAX_SESSION_SECONDS:
                return "time_limit"
            if not transcript.heard_speech and elapsed >= NO_SPEECH_SECONDS:
                return "no_speech"

    upstream = asyncio.create_task(from_browser())
    downstream = asyncio.create_task(from_deepgram())
    timer = asyncio.create_task(watchdog())
    finished, _ = await asyncio.wait(
        {upstream, downstream, timer}, return_when=asyncio.FIRST_COMPLETED
    )
    reason = next(iter(finished)).result()
    upstream.cancel()
    timer.cancel()
    if reason == "disconnected":
        downstream.cancel()
        return

    # Let Deepgram flush the phrase it was still working on before answering.
    await live.finish()
    if not downstream.done():
        await asyncio.wait({downstream}, timeout=FINISH_GRACE_SECONDS)
        downstream.cancel()
    log.info("voice session ended: %s", reason)
    await _send(
        websocket,
        {
            "type": "done",
            "text": transcript.text(catalog_codes),
            "confidence": round(transcript.confidence, 3),
        },
    )
    await _close(websocket)
```

- [ ] **Step 5: Register the router in `backend/app/main.py`**

Add after `from app.routers import ingest as ingest_router`:

```python
from app.routers import voice as voice_router
```

Add after the `app.include_router(chat_router.router)` line:

```python
app.include_router(voice_router.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_listen_api.py tests/test_auth.py -q`
Expected: all pass.

If `test_a_token_for_a_deleted_account_is_unauthorized` still passes the token, check that `user_store(settings)` is built from the per-request `settings`. Changing `USER_DIR` in the test must point the route at the empty directory.

- [ ] **Step 7: Full suite, lint, commit**

Run: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Expected: all pass, clean.

```bash
git add backend/app/auth/dependencies.py backend/app/routers/voice.py backend/app/main.py backend/tests/test_listen_api.py
git commit -m "Add WS /advisor/listen: live voice relayed to Deepgram

The token comes in the first message, not the URL. Every session ends with
'done' carrying what was heard - on a pause, a stop, a limit or a dropped
Deepgram connection. Sign-in now shares one token check with the HTTP routes."
```

---

### Task 4: Frontend: stream into the question box

**Files:**
- Modify: `frontend/vite.config.js`
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/useLiveTranscription.js`
- Delete: `frontend/src/components/useVoiceRecorder.js`
- Modify: `frontend/src/components/AdvisorChat.jsx`

**Interfaces:**
- Consumes: `WS /api/advisor/listen` and the spec's protocol; `health.voice_available`
- Produces:
  - `getAccessToken(): string | null`
  - `openLiveTranscription(): WebSocket`
  - `useLiveTranscription({ onDone }) -> { supported, listening, liveText, error, start, stop }`, where `onDone({ text, confidence })` is called at most once per session

There is no frontend test runner. Verification is lint, build, a throwaway in-browser harness (Step 5) that must fail first, and manual checks (Step 7).

- [ ] **Step 1: Enable WebSocket proxying in `frontend/vite.config.js`**

In the `'/api'` proxy entry, add `ws: true,` after `changeOrigin: true,`. The existing `rewrite` applies to WebSocket upgrades too.

- [ ] **Step 2: Update `frontend/src/api.js`**

2a. After `hasAccessToken`, add:

```js

/** The in-memory token, for the voice socket's first message. Never put in a URL. */
export function getAccessToken() {
  return accessToken
}
```

2b. Replace the whole `transcribeAudio` block, from its doc comment through its closing brace, with:

```js
/**
 * WS /advisor/listen - live voice input.
 *
 * The token is sent as the first message once the socket opens, never in this
 * URL: URLs end up in server and proxy logs. See useLiveTranscription.
 */
export function openLiveTranscription() {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return new WebSocket(`${scheme}://${window.location.host}${BASE}/advisor/listen`)
}
```

- [ ] **Step 3: Create `frontend/src/components/useLiveTranscription.js`**

```js
import { useCallback, useEffect, useRef, useState } from 'react'
import { getAccessToken, openLiveTranscription } from '../api'

const UNAVAILABLE = "Voice input isn't available right now — you can still type."
const MIC_BLOCKED = 'Microphone access was blocked. You can still type your question.'
const RECORDER_FAILED =
  'Recording could not start in this browser. You can still type your question.'
/** How often the recorder hands over audio. Small enough to feel live. */
const CHUNK_MS = 250

/**
 * Live voice input: mic -> backend socket -> Deepgram, text back as it is heard.
 *
 * `liveText` is the whole transcript so far and is replaced on every update.
 * `onDone({ text, confidence })` runs at most once per session: when the
 * backend says `done`, or - if the connection fails after words were heard -
 * with those words, so nothing the student saw is lost.
 *
 * Carried over from the record-then-upload hook's review fixes:
 * - `mountedRef`: a panel closed while the permission prompt is open must not
 *   start recording afterwards.
 * - `startingRef`: a double click cannot open two sessions.
 * - Tracks are released on every ending, including unmount, so the browser's
 *   recording indicator always goes away.
 */
export function useLiveTranscription({ onDone }) {
  const supported =
    typeof window !== 'undefined' &&
    typeof window.MediaRecorder !== 'undefined' &&
    typeof window.WebSocket !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia)

  const [listening, setListening] = useState(false)
  const [liveText, setLiveText] = useState('')
  const [error, setError] = useState(null)
  const socketRef = useRef(null)
  const recorderRef = useRef(null)
  const streamRef = useRef(null)
  const liveTextRef = useRef('')
  const startingRef = useRef(false)
  const mountedRef = useRef(true)
  const onDoneRef = useRef(onDone)

  useEffect(() => {
    onDoneRef.current = onDone
  }, [onDone])

  const release = useCallback(() => {
    const recorder = recorderRef.current
    recorderRef.current = null
    if (recorder) {
      recorder.ondataavailable = null
      if (recorder.state !== 'inactive') recorder.stop()
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    const socket = socketRef.current
    socketRef.current = null
    if (socket) {
      socket.onopen = null
      socket.onmessage = null
      socket.onclose = null
      socket.onerror = null
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
        socket.close()
      }
    }
    liveTextRef.current = ''
    if (mountedRef.current) {
      setListening(false)
      setLiveText('')
    }
  }, [])

  /** End the session. `result` from `done`; otherwise keep any heard text. */
  const finish = useCallback(
    (result, message) => {
      const heard = liveTextRef.current
      release()
      if (!mountedRef.current) return
      if (message) setError(message)
      if (result) onDoneRef.current?.(result)
      else if (heard) onDoneRef.current?.({ text: heard, confidence: 0 })
    },
    [release],
  )

  const beginRecording = useCallback(
    (socket, stream) => {
      try {
        const recorder = new MediaRecorder(stream)
        recorder.ondataavailable = (event) => {
          if (event.data.size > 0 && socket.readyState === WebSocket.OPEN) socket.send(event.data)
        }
        recorderRef.current = recorder
        recorder.start(CHUNK_MS)
      } catch {
        finish(null, RECORDER_FAILED)
      }
    },
    [finish],
  )

  const start = useCallback(async () => {
    if (!supported || socketRef.current || startingRef.current) return
    startingRef.current = true
    setError(null)
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      startingRef.current = false
      setError(MIC_BLOCKED)
      return
    }
    startingRef.current = false
    if (!mountedRef.current) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    streamRef.current = stream

    let socket
    try {
      socket = openLiveTranscription()
    } catch {
      finish(null, UNAVAILABLE)
      return
    }
    socketRef.current = socket
    setListening(true)

    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'start', token: getAccessToken() }))
    }
    socket.onmessage = (event) => {
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type === 'ready') beginRecording(socket, stream)
      else if (message.type === 'transcript') {
        liveTextRef.current = message.text
        setLiveText(message.text)
      } else if (message.type === 'done') {
        finish({ text: message.text, confidence: message.confidence }, null)
      } else if (message.type === 'error') finish(null, UNAVAILABLE)
    }
    // Closing without `done` - server restart, network drop, error.
    socket.onclose = () => finish(null, UNAVAILABLE)
  }, [supported, finish, beginRecording])

  const stop = useCallback(() => {
    const socket = socketRef.current
    if (!socket) return
    if (socket.readyState === WebSocket.OPEN && recorderRef.current) {
      socket.send(JSON.stringify({ type: 'stop' }))
    } else {
      finish(null, null) // not recording yet: cancel outright
    }
  }, [finish])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      release()
    }
  }, [release])

  return { supported, listening, liveText, error, start, stop }
}
```

- [ ] **Step 4: Rewire `frontend/src/components/AdvisorChat.jsx`**

4a. In the imports, change the api import to `import { askAdvisor, getAdvisorHealth, resetConversation } from '../api'`, and replace `import { useVoiceRecorder } from './useVoiceRecorder'` with `import { useLiveTranscription } from './useLiveTranscription'`.

4b. Replace everything from `const [transcribing, setTranscribing] = useState(false)` through the closing `}` of `function toggleRecording() { … }` with:

```js
  const [voiceNote, setVoiceNote] = useState(null)
  /** What was typed before the mic was clicked; live text is shown after it. */
  const typedBeforeListening = useRef('')

  const handleVoiceDone = useCallback(({ text, confidence }) => {
    const spoken = text.trim()
    if (!spoken) {
      setVoiceNote(VOICE_MESSAGES.empty)
      return
    }
    setDraft(mergeDraft(typedBeforeListening.current, spoken))
    setVoiceNote(confidence < LOW_CONFIDENCE ? VOICE_MESSAGES.lowConfidence : null)
    input.current?.focus()
  }, [])

  const voice = useLiveTranscription({ onDone: handleVoiceDone })
  const voiceOn = Boolean(health?.voice_available)
  const voiceBusy = voice.listening

  function toggleListening() {
    setVoiceNote(null)
    if (voice.listening) {
      voice.stop()
    } else {
      typedBeforeListening.current = draft
      voice.start()
    }
  }
```

(`const input = useRef(null)` stays just above this block. The old `const [voiceNote, …]` line was inside the replaced range, so it is re-declared here once.)

4c. In the `<input id="advisor-question" …>` element, replace `value={draft}` with:

```jsx
            value={voice.listening ? mergeDraft(typedBeforeListening.current, voice.liveText) : draft}
            readOnly={voice.listening}
```

and replace `disabled={busy || transcribing}` with `disabled={busy}`.

4d. Replace the `<MicButton … />` element with:

```jsx
            <MicButton
              listening={voice.listening}
              disabled={busy || !voice.supported}
              onClick={toggleListening}
            />
```

4e. In the voice note paragraph, replace both occurrences of `recorder.error` with `voice.error`, and `!recorder.supported` with `!voice.supported`.

4f. Replace the whole `MicButton` function at the end of the file with:

```jsx
/** Click to start listening; click again to stop early. It also stops on a pause. */
function MicButton({ listening, disabled, onClick }) {
  const label = listening ? 'Stop listening' : 'Ask by voice'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled && !listening}
      aria-pressed={listening}
      aria-label={label}
      title={label}
      className={`inline-flex h-9 w-9 shrink-0 items-center justify-center self-center rounded-full ring-1 ring-inset transition disabled:cursor-not-allowed disabled:opacity-50 ${
        listening
          ? 'animate-pulse bg-rose-600 text-white ring-rose-600'
          : 'bg-white text-slate-700 ring-slate-300 hover:bg-slate-50'
      }`}
    >
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <rect x="9" y="3" width="6" height="12" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
      </svg>
    </button>
  )
}
```

4g. Delete the old hook: `git rm frontend/src/components/useVoiceRecorder.js`.

4h. Run (from `frontend/`): `npm run lint && npm run build`
Expected: no new warnings (only the pre-existing `ReviewStep.jsx` ones), build succeeds. `grep -rn "useVoiceRecorder\|transcribeAudio\|transcribing\|recorder\." src/` should return nothing.

- [ ] **Step 5: Hook harness: write it, watch it fail, then pass**

Create `frontend/live-harness.html` (throwaway, never committed):

```html
<!doctype html>
<html><body><pre id="results">running…</pre>
<script type="module">
import React from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { useLiveTranscription } from '/src/components/useLiveTranscription.js'
import { setAccessToken } from '/src/api.js'

const results = []
const check = (name, ok) => results.push(`${ok ? 'PASS' : 'FAIL'} ${name}`)
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms))
setAccessToken('harness-token')

let resolvePrompt
navigator.mediaDevices.getUserMedia = () => new Promise((res) => { resolvePrompt = res })
const makeStream = () => {
  const tracks = [{ stopped: false, stop() { this.stopped = true } }]
  return { tracks, getTracks: () => tracks }
}
let recorders = []
window.MediaRecorder = class {
  constructor() { this.state = 'inactive'; recorders.push(this) }
  start() { this.state = 'recording' }
  stop() { this.state = 'inactive' }
}
let sockets = []
class FakeSocket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; sockets.push(this) }
  open() { this.readyState = 1; this.onopen?.() }
  send(data) { this.sent.push(data) }
  serverSays(msg) { this.onmessage?.({ data: JSON.stringify(msg) }) }
  serverCloses() { this.readyState = 3; this.onclose?.() }
  close() { this.readyState = 3; this.closedByClient = true }
}
window.WebSocket = FakeSocket

let api, done
function Probe() { api = useLiveTranscription({ onDone: (r) => done.push(r) }); return null }
const mount = () => {
  const root = createRoot(document.createElement('div'))
  flushSync(() => root.render(React.createElement(Probe)))
  return root
}
const reset = () => { recorders = []; sockets = []; done = [] }

// 1. Happy path: start message carries the token, text streams, done hands over.
{
  reset(); const root = mount()
  const p = api.start(); const stream = makeStream(); resolvePrompt(stream); await p
  const s = sockets[0]; s.open()
  check('happy: start message carries token, not URL',
    JSON.parse(s.sent[0]).token === 'harness-token' && !s.url.includes('harness-token'))
  s.serverSays({ type: 'ready' }); await tick()
  check('happy: recorder starts only after ready', recorders.length === 1 && recorders[0].state === 'recording')
  s.serverSays({ type: 'transcript', text: 'Can I take COSC' }); await tick()
  check('happy: live text shown', api.liveText === 'Can I take COSC')
  s.serverSays({ type: 'done', text: 'Can I take COSC 243?', confidence: 0.9 }); await tick()
  check('happy: onDone once with final text', done.length === 1 && done[0].text === 'Can I take COSC 243?')
  check('happy: mic released and not listening', stream.tracks[0].stopped && !api.listening)
  flushSync(() => root.unmount())
}

// 2. Socket closes before done: keeps the heard text and reports unavailable.
{
  reset(); const root = mount()
  const p = api.start(); const stream = makeStream(); resolvePrompt(stream); await p
  const s = sockets[0]; s.open(); s.serverSays({ type: 'ready' })
  s.serverSays({ type: 'transcript', text: 'Can I take' }); await tick()
  s.serverCloses(); await tick()
  check('dropped: heard text handed over', done.length === 1 && done[0].text === 'Can I take')
  check('dropped: unavailable shown', typeof api.error === 'string' && api.error.includes("isn't available"))
  check('dropped: mic released', stream.tracks[0].stopped)
  flushSync(() => root.unmount())
}

// 3. Stop before ready cancels: no recording, mic released, no onDone.
{
  reset(); const root = mount()
  const p = api.start(); const stream = makeStream(); resolvePrompt(stream); await p
  sockets[0].open()
  api.stop(); await tick()
  sockets[0].serverSays({ type: 'ready' }); await tick()
  check('early stop: no recorder started', recorders.length === 0)
  check('early stop: mic released, socket closed', stream.tracks[0].stopped && sockets[0].closedByClient)
  check('early stop: nothing handed over', done.length === 0 && !api.listening)
  flushSync(() => root.unmount())
}

// 4. Unmount during the permission prompt: never opens a socket, releases the mic.
{
  reset(); const root = mount()
  const p = api.start()
  flushSync(() => root.unmount())
  const stream = makeStream(); resolvePrompt(stream); await p
  check('unmount in prompt: no socket', sockets.length === 0)
  check('unmount in prompt: mic released', stream.tracks[0].stopped)
}

// 5. Double start opens one session.
{
  reset(); const root = mount()
  const p1 = api.start(); const p2 = api.start()
  const stream = makeStream(); resolvePrompt(stream); await Promise.all([p1, p2])
  check('double start: one socket', sockets.length === 1)
  flushSync(() => root.unmount())
}

document.getElementById('results').textContent = results.join('\n')
</script></body></html>
```

**RED first:** before Step 3's file exists (or with it temporarily renamed), open `http://localhost:5173/live-harness.html` with the dev server running. The module import fails, so the page stays on `running…`. That is the failing state. With the hook in place, reload the page.
Expected: every line `PASS` (16 checks). Fix the hook, not the harness, for any `FAIL`. Then delete `frontend/live-harness.html`.

- [ ] **Step 6: Commit**

```bash
git add frontend/vite.config.js frontend/src/api.js frontend/src/components/useLiveTranscription.js frontend/src/components/AdvisorChat.jsx
git commit -m "Stream voice into the question box as the student speaks

The mic opens a socket to /advisor/listen, the token goes in the first message,
and the box shows the transcript as it grows. It stops by itself on a pause.
The record-then-upload hook is gone from the UI."
```

(`git rm` in 4g already staged the deletion.)

- [ ] **Step 7: Manual check in Chrome (real voice)**

Restart the backend and the frontend, sign in, confirm a transcript, then open **Ask the advisor**:

1. Click the mic and say *"Can I take computer science two forty three?"*. Words appear while you speak. About 1.5 s after you stop, the button stops pulsing and the box reads `Can I take COSC 243?`. Nothing was sent.
2. Type `Also,` then use the mic. The live text appears after `Also,`.
3. Click the mic and say nothing. After about 8 s you see `Didn't catch that — try again.`
4. Say half a question, pause 2 s, then continue. The session ends at the pause. Click the mic again and finish: the new words are appended.
5. Click the mic and click it again mid-sentence. It stops and keeps what was heard.
6. While listening, stop the backend (mprocs). The heard text stays in the box and the unavailable note shows.
7. (If a Mac is available) Try Safari. Either it streams, or it shows the unavailable note without hanging.

---

### Task 5: Live check and docs

**Files:**
- Modify: `docs/advisor.md` (the "Voice input" section)
- Modify: `DEMO_GUIDE.md` (voice step, limitation 3)
- Modify: `PROJECT_STATUS.md` (voice bullet)

**Interfaces:**
- Consumes: everything above; `backend/.env` `DEEPGRAM_API_KEY`
- Produces: documentation only

- [ ] **Step 1: Live check through `DeepgramLive` with a continuous stream**

Run from `backend/` (it uses the scratchpad's synthesized `q1.wav`, sent as raw PCM so the stream behaves like a mic):

```bash
.venv/Scripts/python - <<'EOF'
import asyncio, time, wave, random, struct
from pathlib import Path
from app.config import get_settings
from app.catalog.registry import registry
from app.speech.keyterms import spoken_course_codes
from app.speech.live import DeepgramLive, Final, LiveTranscript, Partial, UtteranceEnd

WAV = Path(r"C:\Users\sawcy\AppData\Local\Temp\claude\D--Development-Vscode-MultiModel\0051bb1e-a811-4d02-98d9-8855e19892cd\scratchpad\voicecheck\q1.wav")
s = get_settings(); registry.load(s.catalog_dir); programs = registry.list_programs()
codes = [c.code for p in programs for c in p.courses]
with wave.open(str(WAV)) as w:
    rate, pcm = w.getframerate(), w.readframes(w.getnframes())
# 5 s of faint room noise after the speech, like a mic that keeps listening.
data = pcm + b"".join(struct.pack("<h", random.randint(-60, 60)) for _ in range(rate * 5))

# Raw PCM must declare its encoding (the browser's webm does not). Patch the URL
# builder for this script only; DeepgramLive.connect looks it up at call time.
import app.speech.live as live_module
_original_url = live_module.live_url
live_module.live_url = lambda st, kt: _original_url(st, kt) + f"&encoding=linear16&sample_rate={rate}"

async def main():
    live = DeepgramLive()
    await live.connect(spoken_course_codes(programs), settings=s)
    tr, t = LiveTranscript(), time.time()
    async def feed():
        step = rate * 2 // 4
        for i in range(0, len(data), step):
            await live.send_audio(data[i:i + step]); await asyncio.sleep(0.25)
    feeder = asyncio.create_task(feed())
    async for ev in live.events():
        if isinstance(ev, Partial): tr.add_partial(ev.text)
        elif isinstance(ev, Final): tr.add_final(ev.text, ev.confidence)
        print(f"{time.time()-t:5.2f}s {type(ev).__name__:12} {tr.text(codes)!r}")
        if isinstance(ev, UtteranceEnd) and tr.heard_speech: break
    feeder.cancel(); await live.finish(); await live.close()
asyncio.run(main())
EOF
```

Why raw PCM: a WAV file's fixed length makes Deepgram close the stream when the file's data runs out, before `UtteranceEnd` can fire. This was seen in the Sep 22 probe.
Expected: partials grow, the final reads `Can I take UNIV 101 or COSC 243?`, and `UtteranceEnd` arrives about 1–2 s after the final (probe: final at about 4.8 s, `UtteranceEnd` at about 5.8 s). Record the output.

- [ ] **Step 2: Update `docs/advisor.md`**

Replace the "Flow" bullet and the "Never auto-sent" bullet of the Voice input section with:

```markdown
- **Flow (live):** browser mic → `MediaRecorder` chunks every 250 ms → WebSocket
  `/advisor/listen` (token in the first message, never the URL) → backend relay
  (`app/routers/voice.py`) → Deepgram live `wss /v1/listen` (`nova-3`,
  `smart_format`, `mip_opt_out`, `interim_results`, `endpointing=300`,
  `utterance_end_ms=1500`). The box shows finished phrases plus the phrase still
  being heard, course codes converted, and the session ends 1.5 s after the
  student stops talking (or on a click, 30 s, 8 s of silence, or 2 MB).
- **Never auto-sent.** When the session ends, the text stays in the box for the
  student to check and edit. Below 0.6 average confidence the panel asks them to
  check it.
- `POST /advisor/transcribe` (record-then-upload) remains in the backend but the
  UI no longer uses it.
- **Browsers:** Chrome and Edge (webm/opus) are supported. Safari's mp4 chunks are
  unverified; if Deepgram cannot decode them the student sees the unavailable note.
```

- [ ] **Step 3: Update `DEMO_GUIDE.md`**

Replace the voice step added under "Step 4 — Ask the advisor" with:

```markdown
**Voice (needs `DEEPGRAM_API_KEY`, use Chrome).** Click the mic once and say
*"Can I take computer science two forty three?"*. The words appear as you speak,
and 1.5 s after you stop, the box reads **"Can I take COSC 243?"** - converted
because COSC 243 is in the catalog. Point out that it was **not** sent: the
student checks it first, the same rule as the transcript review screen. Then
press Ask.
```

Replace limitation 3 with:

```markdown
3. **Voice is verified in Chrome with a synthesized voice.** Live streaming was
   checked against real Deepgram; a human speaker and other browsers should be
   tried before presenting. Safari is unverified.
```

- [ ] **Step 4: Update `PROJECT_STATUS.md`**

In the voice bullet, add a sentence after "Deepgram accepted all 76 keyterms.":

```markdown
  **Live streaming (Sep 22 2026):** words stream into the box through
  `WS /advisor/listen`; the session ends on Deepgram's UtteranceEnd ~1.5 s after
  speech. Live check: <paste Step 1's final text and UtteranceEnd timing>.
```

Paste the real Step 1 output. If Step 1 was not run, write "not yet live-checked" instead.

- [ ] **Step 5: Final verification and commit**

Run from `backend/`: `.venv/Scripts/python -m pytest -q && .venv/Scripts/ruff check . && .venv/Scripts/black --check .`
Run from `frontend/`: `npm run lint && npm run build`
Expected: all green.

```bash
git add docs/advisor.md DEMO_GUIDE.md PROJECT_STATUS.md
git commit -m "Document live voice streaming and record the live check"
```
