# Live voice streaming for the advisor — design

Date: 2026-09-22 · Branch: `feature/voice-input` · Status: awaiting review
Builds on: `2026-09-22-voice-input-design.md` (record-then-upload voice input)

## Why

Voice input works, but the student speaks, clicks stop, then waits for the
whole transcript. For the demo, words should appear in the question box *while
the student talks*, and the recording should end by itself when they pause.

Success: in the demo, the student clicks the mic once, says "Can I take
computer science two forty three?", sees the words appear as they speak, and
about 1.5 s after they stop the box reads `Can I take COSC 243?` and recording
has ended. They press Ask.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Transport | Backend relay: browser ⇄ `/advisor/listen` (WebSocket) ⇄ Deepgram live `wss /v1/listen` | Key stays server-side; auth, keyterms, privacy flags and course-code conversion stay on the server |
| Rejected | Browser → Deepgram direct with a temporary token | Client would choose keyterms/privacy flags; course-code conversion would need a JS port; much harder to test |
| Ending a session | Auto-stop at the first utterance end (`utterance_end_ms=1500`) after speech; a click also stops it | One click in the demo; bounds stream length and cost |
| Upload path in the UI | Removed. `POST /advisor/transcribe` stays in the backend, unused by the UI | One voice path to maintain |
| Auto-send | Never (unchanged) | The student reviews the text, then presses Ask |
| Editing while streaming | Not supported: the input is read-only until the session ends | Keeps the live text and typed text from fighting |

Unchanged rules from the first design: the key never leaves the server; every
Deepgram request carries `mip_opt_out=true`; course codes are rewritten only when
the result is a catalog course; every voice failure leaves typing working;
Deepgram's error text is logged, never forwarded; `app/audit/` and
`app/catalog/` may not import `app.speech`.

## Protocol: browser ⇄ backend

WebSocket at `/advisor/listen` (`/api/advisor/listen` through the Vite proxy).
Text frames are JSON; audio frames are binary.

| Direction | Message | Meaning |
|---|---|---|
| → | `{"type": "start", "token": "<access token>"}` | Must be the first frame, within 5 s. The token is never put in the URL. |
| ← | `{"type": "ready"}` | Authenticated and connected to Deepgram. The browser starts sending audio only after this. |
| → | binary | `MediaRecorder` chunks (browser default container, e.g. webm/opus), timeslice 250 ms |
| ← | `{"type": "transcript", "text": "…"}` | The **whole** text so far: finished phrases plus the current partial phrase, course codes already converted. The client replaces what it shows. |
| → | `{"type": "stop"}` | The student clicked the mic to stop early. |
| ← | `{"type": "done", "text": "…", "confidence": 0.93}` | The final text. The server then closes the socket. |
| ← | `{"type": "error", "reason": "unauthorized" \| "unavailable"}` | The server then closes the socket. |

## Backend

### `app/speech/live.py` (new)

**`LiveTranscript`**: pure, no I/O.

- `add_partial(text)`: replaces the current partial phrase.
- `add_final(text, confidence)`: appends a finished phrase and clears the partial.
- `text(catalog_codes) -> str`: finished phrases plus the partial, joined with single spaces and stripped, then passed through `normalize_course_mentions`. Conversion runs on the combined string, so a code split across a final and a partial ("computer science two forty" + "three") still converts.
- `confidence -> float`: mean of the finals' confidences, or `0.0` with none.
- `heard_speech -> bool`: true once any final or partial had non-empty text.

**`DeepgramLive`**: one Deepgram streaming connection, built on the `websockets` library. It is already installed through `uvicorn[standard]`, and `pyproject.toml` declares it explicitly.

- `async connect(keyterms, *, settings)`: opens `wss://<deepgram_base_url host>/v1/listen` with the query parameters below and the header `Authorization: Token <key>`. The base URL is converted `https→wss`, `http→ws` so tests can point it at a local fake.
  - `model=<deepgram_model>`
  - `smart_format=true`
  - `mip_opt_out=true`
  - `interim_results=true`
  - `endpointing=300`
  - `utterance_end_ms=1500`
  - one `keyterm` per term
- A refused, failed or rejected handshake raises `SpeechError("unavailable")`.
- `async send_audio(chunk: bytes)`
- `async finish()`: sends `{"type": "CloseStream"}`.
- `async events()`: async iterator yielding:
  - `Partial(text)`: a `Results` message with `is_final: false`.
  - `Final(text, confidence)`: a `Results` message with `is_final: true`. Empty text is ignored.
  - `UtteranceEnd()`: an `UtteranceEnd` message.
  - Other message types, such as `Metadata` or `SpeechStarted`, are ignored.
  - The iterator ends when Deepgram closes the connection, including after `CloseStream`.
  - A connection that drops without a clean close also just ends the iterator, and the route treats that like a normal end.
- `async close()`: closes the socket, safe to call twice.

### `app/auth/dependencies.py` (refactor)

Extract the body of `current_user` into
`user_for_token(token: str, settings: Settings, store: UserStore) -> User | None`.
`current_user` calls it and raises its usual 401 on `None`. The WebSocket route
calls it directly. Behaviour of every HTTP route is unchanged.

### `app/routers/voice.py` (new): `@router.websocket("/advisor/listen")`

Registered in `app/main.py`.

1. Accept the socket. Wait up to **5 s** for a text frame `{"type": "start", "token": …}`. A timeout, a wrong frame or a token for which `user_for_token` returns `None` gives `error: unauthorized`, then the socket closes.
2. No Deepgram key → `error: unavailable`, then close. Otherwise build the keyterms and catalog codes from `registry`, connect `DeepgramLive`, and send `ready`. A connect failure gives `error: unavailable`, then close.
3. Run two tasks until the session ends:
   - **Upstream:** binary frames go to `send_audio`, counting bytes. `{"type": "stop"}` ends the session. A browser disconnect ends it with no reply.
   - **Downstream:** each `Partial` or `Final` updates `LiveTranscript` and sends `transcript` with `text(catalog_codes)`. An `UtteranceEnd` after `heard_speech` ends the session.
4. The session also ends at any of these limits (constants in `voice.py`):
   - 30 s from `ready` (`MAX_SESSION_SECONDS`)
   - 8 s from `ready` without speech (`NO_SPEECH_SECONDS`)
   - more than `settings.max_audio_bytes` of audio in total
5. Ending:
   1. Send `finish()`.
   2. Wait up to **2 s** for Deepgram's remaining finals, applying them.
   3. Send `done` with the final text and confidence.
   4. Close both sockets.

   If Deepgram dropped mid-session, `done` carries whatever was received. If the browser is already gone, only the Deepgram socket is closed.

`routers/chat.py` keeps `POST /advisor/transcribe` and `voice_available` on
`/advisor/health` unchanged.

## Frontend

**`vite.config.js`**: the `/api` proxy gains `ws: true`.

**`api.js`**:
- Add `getAccessToken()`, which returns the in-memory token.
- Add `openLiveTranscription()`, which returns `new WebSocket(<ws or wss>://<host>/api/advisor/listen)`.
- Remove `transcribeAudio`.

**`frontend/src/components/useLiveTranscription.js`** (new; replaces `useVoiceRecorder.js`, which is deleted)

`useLiveTranscription({ onDone }) -> { supported, listening, liveText, error, start, stop }`

- `supported` requires `MediaRecorder`, `getUserMedia` and `WebSocket`.
- `start()`:
  1. Call `getUserMedia({audio: true})`, keeping the review fixes: a `mountedRef` check after the permission prompt, a `startingRef` guard against double starts, and the tracks released if the recorder cannot start.
  2. Open the socket and send `start` with the token.
  3. On `ready`, start `MediaRecorder` with a 250 ms timeslice and send each non-empty chunk.
- On `transcript`, set `liveText`.
- On `done`:
  1. Stop the recorder and release the tracks.
  2. Call `onDone({ text, confidence })`.
  3. Reset.
- On `error`, or the socket closing before `done`:
  1. Stop and release everything.
  2. Set `error` to the unavailable message. If `liveText` was non-empty, call `onDone` with it first, so heard text is not lost.
- `stop()` sends `{"type": "stop"}` and keeps listening for `done`.
- On unmount, close the socket, stop the recorder and release the tracks. No callback runs after unmount.

**`AdvisorChat.jsx`**
- The mic toggles `start` and `stop`.
- While listening:
  - the input shows `mergeDraft(typedBeforeListening, liveText)` and is read-only
  - the button is red and pulsing, labelled "Stop listening"
  - Ask and the suggestion chips are disabled
- On `onDone`:
  - Empty text shows "Didn't catch that — try again."
  - Otherwise the draft becomes `mergeDraft(typedBeforeListening, text)`, the input is focused, and the low-confidence hint shows below 0.6.
- The existing messages (`VOICE_MESSAGES`) are reused.

## Error handling

| Situation | Backend | Student sees |
|---|---|---|
| No `start` in 5 s, malformed first frame, bad or expired token | `error: unauthorized`, close | "Voice input isn't available right now — you can still type." |
| No Deepgram key | Health: `voice_available: false` | No mic button |
| Deepgram refuses or is unreachable (key, credit, rate limit, network) | `error: unavailable`, close | Same message |
| Deepgram drops mid-session | `done` with the text so far | That text, editable |
| Browser disconnects | Deepgram socket closed immediately | — |
| 8 s without speech | `done`, empty text | "Didn't catch that — try again." |
| 30 s or audio over `max_audio_bytes` | `done` with the text so far | That text, editable |
| Mic denied, or no `MediaRecorder`, `getUserMedia` or `WebSocket` | — | Existing inline notes; mic disabled |
| Confidence < 0.6 | normal `done` | Text plus "Check this — I may have misheard." |

## Testing

- **`tests/test_live_transcript.py`**: `LiveTranscript` accumulation, partial replacement, conversion across a final/partial boundary, mean confidence, `heard_speech`.
- **`tests/test_speech_live.py`**: `DeepgramLive` against a local fake Deepgram WebSocket server (`websockets.serve` on a loopback port). Checks:
  - query parameters, including `interim_results`, `endpointing`, `utterance_end_ms`, `mip_opt_out` and `keyterm`
  - the key is in the header, not the URL
  - audio frames arrive in order
  - `CloseStream` is sent on `finish()`
  - message → event mapping, with other message types ignored
  - a rejected handshake (401) or refused port raises `unavailable`
  - an abrupt drop ends `events()`
- **`tests/test_listen_api.py`**: the route through FastAPI's WebSocket test client, with `DeepgramLive` replaced by a scripted fake. Checks:
  - unauthorized cases: no start frame, bad token, timeout
  - no key → `unavailable`
  - normal session: `ready`, growing `transcript`, then `done` with converted codes on `UtteranceEnd`
  - client `stop`
  - no-speech limit and byte limit
  - Deepgram connect failure → `unavailable`
  - Deepgram's error text never reaches the client
  - `current_user` behaviour unchanged, since the existing auth tests stay green
- **Frontend**: no test runner. A throwaway in-browser harness with a fake `getUserMedia`, `MediaRecorder` and `WebSocket` checks the hook's lifecycle, then a manual run with a real voice.
- **Live**: stream the synthesized WAV files through real Deepgram via `DeepgramLive`. Confirm `UtteranceEnd` arrives and the final text converts. Record the result in `PROJECT_STATUS.md`.

## Out of scope

- Editing the text while it is streaming
- Several utterances per session (it stops at the first pause)
- Spoken replies
- Automatic fallback to record-then-upload when WebSockets are blocked
- Per-user rate limiting
