# Voice input for the advisor — design

Date: 2026-09-22 · Branch: `feature/voice-input` · Status: awaiting review

## Why

The project is a *multimodal* academic advisor, but today its only inputs are
text and documents. Voice was in the approved plan and is the cheapest way to
make the multimodal claim visible in the demo. Success: in the demo, a student
asks a question aloud, the transcript reads course codes correctly (e.g.
"COSC 241"), and the advisor answers it.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Speech-to-text | Deepgram, pre-recorded API (`POST /v1/listen`) | Team has a key; supports keyterm prompting |
| Interaction | Toggle: click to start, click to stop, auto-stop at 30 s | Simpler and more accessible than hold-to-talk |
| Streaming | Not now | Kept for later as a separate endpoint beside this one |
| Browser fallback (Web Speech API) | None | A key exists; a second engine is code for a problem we don't have |
| Auto-send | Never | The transcript fills the input; the student reviews, then presses Ask |

Voice is an **input channel only**. It changes nothing about how answers are
produced: the deterministic engine still computes every academic fact.

## Architecture

### Backend

**`app/speech/__init__.py`, `app/speech/deepgram.py`** (new package)

- `transcribe(audio: bytes, mime_type: str, keyterms: list[str], *, settings) -> Transcript`
- `Transcript` is a dataclass: `text: str`, `confidence: float`.
- `SpeechError(RuntimeError)` with a `kind` of `"unavailable"` (no key, 401,
  402, 403, 5xx, timeout, network) or `"unreadable"` (400: audio Deepgram
  could not decode).
- Plain `urllib`, matching the Gemini/Ollama providers in `app/llm/chat.py`.
  **No new dependency.**
- Request: `POST {deepgram_base_url}/v1/listen?model=<deepgram_model>&smart_format=true&mip_opt_out=true&keyterm=…`
  (one `keyterm` per term), header `Authorization: Token <key>`,
  `Content-Type: <mime_type>`, raw audio body.
- `mip_opt_out=true` opts out of Deepgram's Model Improvement Program, so
  students' audio is not retained to train models.
- Response: text and confidence from `results.channels[0].alternatives[0]`.
  A missing or malformed path, or a non-JSON body, raises
  `SpeechError("unavailable")`. An empty transcript is returned as `""`, not
  an error.
- Deepgram's error body is logged server-side and never propagated.

**`POST /advisor/transcribe`** in `app/routers/chat.py`

- Multipart upload, field `audio`. Requires `CurrentUser`. There is no
  `{student_id}` in the path, so nothing to authorise beyond being signed in.
- Checks, in order: key configured (else `503`), content type starts with
  `audio/` (else `415`), size ≤ `max_audio_bytes` (else `413`).
- Key terms: every catalog course code in spoken form (`COSC241` → `"COSC 241"`)
  plus each subject (`"COSC"`), built from the loaded catalog on each request. The catalogs
  hold 65 codes, well within Deepgram's keyterm limit.
- Audio is never saved; it exists for one request only. (Starlette buffers an
  upload part over 1 MB in a temp file for the request's duration; a 30-second
  question is ~0.5 MB. Amended after final review.)
- Response model `TranscribeResponse { text: str, confidence: float }`.
- Error mapping: `SpeechError("unavailable")` → `503`,
  `SpeechError("unreadable")` → `422`. Response details are fixed strings.

**`GET /advisor/health`**: `AdvisorHealth` gains `voice_available: bool`
(true when `deepgram_api_key` is set).

**`app/config.py`** additions:

```python
deepgram_api_key: str = ""
deepgram_model: str = "nova-3"
deepgram_base_url: str = "https://api.deepgram.com"   # overridden by tests
deepgram_timeout_seconds: float = 20.0
max_audio_bytes: int = 2 * 1024 * 1024
```

`.env.example` gains `DEEPGRAM_API_KEY=` with a one-line comment.

**Guard test**: `tests/test_no_llm_in_engine.py` adds `"speech"` to
`FORBIDDEN_ROOTS`, so the engine packages it walks may never import
`app.speech`.

### Frontend

**`api.js`**: `transcribeAudio(blob)` posts `FormData` with the blob as
`audio` (filename from its type, e.g. `question.webm`) and the auth token, and
returns `{ text, confidence }`.

**`AdvisorChat.jsx`**: a mic button between the input and **Ask**, rendered
only when `health.voice_available`.

- States: `idle` → `recording` (red, pulsing, `aria-pressed`, label "Stop
  recording") → `transcribing` (spinner) → `idle`.
- `navigator.mediaDevices.getUserMedia({ audio: true })` + `MediaRecorder` with
  the browser's default container (webm/opus in Chrome, mp4 in Safari).
  Deepgram detects the container.
- Auto-stop after 30 s. The mic tracks are stopped when recording ends and when
  the component unmounts, so the browser's recording indicator goes away.
- On success with non-empty text: set it as the draft and focus the input. If
  `confidence < 0.6`, show a small hint under the input: "Check this — I may
  have misheard."
- Disabled while the advisor is busy, and vice versa.

The recording logic lives in a small `useVoiceRecorder` hook
(`frontend/src/components/useVoiceRecorder.js`) so `AdvisorChat.jsx` only
handles UI state.

## Error handling

Every failure leaves typing working.

| Situation | Backend | Student sees |
|---|---|---|
| No Deepgram key | `503`; button hidden | No mic button |
| Key rejected / out of credit (401/402/403), Deepgram 5xx | `503` | "Voice input isn't available right now — you can still type." |
| Timeout / network down | `503` | Same |
| Deepgram can't decode audio (400) | `422` | "Couldn't read that recording — try again." |
| Not audio / too large | `415` / `413` | Same |
| Silence → empty transcript | `200`, `text: ""` | "Didn't catch that — try again." Input untouched. |
| Confidence < 0.6 | `200` | Text filled + misheard hint |
| Mic permission denied / no MediaRecorder | — | Inline note; mic button disabled |

## Testing

**`tests/test_speech_deepgram.py`**: a local `HTTPServer` fake, in the style of
`test_chat_providers.py`:

- the key is sent as `Authorization: Token …` and never appears in the URL
- `model`, `smart_format`, `mip_opt_out=true` and one `keyterm` per term are
  in the query string
- the request body is the audio bytes, with the given content type
- normal responses parse to text and confidence
- empty transcript → `""`
- 400 → `unreadable`; 401/402/403/500 → `unavailable`
- non-JSON and malformed-shape bodies → `unavailable`
- no key → `unavailable` without any network call

**Router tests** (added to `tests/test_advisor.py` or a new
`tests/test_transcribe_api.py`):

- unauthenticated → refused
- no key → `503`; wrong type → `415`; oversized → `413`
- `unavailable` / `unreadable` map to `503` / `422`
- Deepgram's error text never appears in any response body
- `/advisor/health` reports `voice_available` both ways
- key terms include `"COSC 241"`, taken from the loaded catalog

**Guard**: the `app.speech` import prohibition above.

**Manual**, with the real key: ask "What can I take after COSC 241?" aloud in
Chrome and confirm the transcript reads "COSC 241" and the advisor answers it.
Record the result in `PROJECT_STATUS.md`: it is the first call from this repo
to a live external service.

## Out of scope

- Live streaming transcription (future: a second endpoint)
- Spoken replies (text-to-speech)
- Voice anywhere outside the advisor panel
- Per-user rate limiting on transcription (auth plus the 2 MB and 30 s caps
  bound per-request cost; revisit if the app goes public)
