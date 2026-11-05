# Live Voice Advisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenRouter as a chat provider, Deepgram text-to-speech for advisor answers, and a hands-free live conversation mode with course-code read-back and barge-in.

**Architecture:** Voice reuses the existing pipeline unchanged: `/advisor/listen` (Deepgram live STT) → `POST /advisor/chat` (engine answer → optional rephrase → `check_rephrasing`) → new deterministic speakable pass → new `POST /advisor/speak` (Deepgram Aura) → browser playback. The live loop is a frontend state machine; the backend adds only a read-back gate on live turns.

**Tech Stack:** FastAPI, stdlib HTTP (`_http_json` pattern), pytest; React + Vite, vitest, oxlint.

**Spec:** `docs/superpowers/specs/2026-11-04-live-voice-advisor-design.md`

## Global Constraints

- The model never computes degree progress; voice adds no path for model text to bypass `check_rephrasing`.
- Nothing in `app/catalog/` or `app/audit/` imports voice/LLM code (`tests/test_no_llm_in_engine.py` stays green).
- Links come only from the curated YAML; never from model output.
- TTS provider: Deepgram Aura, using existing `deepgram_api_key` / `deepgram_base_url`.
- Read-back threshold: the existing 0.6 confidence warning value.
- Dictation mode stays "never auto-sent"; `tests/test_voice_to_advisor.py::test_listening_never_asks_the_advisor` stays green unchanged (the listen route still never calls the advisor; auto-send lives in the frontend live mode only).
- Works with no LLM key (engine answer spoken as-is).
- Backend check: `cd backend && .venv/Scripts/python -m pytest -q`. Frontend check: `cd frontend && npm run lint && npm test && npm run build`.

## Review Focus

1. Student speaks over the advisor's very first word: playback must stop, and the half-heard question must not be answered twice. (Task 8 test)
2. TTS call fails or no Deepgram key: the reply text still shows, the loop goes back to listening, with no crash. (Task 4 + Task 7 tests)
3. Empty or silence-only turn (`UtteranceEnd` with no words): no chat call, keep listening. (Task 7 test)
4. Reply contains a link or long course list: the spoken text has no raw URL and no markdown symbols. (Task 3 test)
5. Student answers "yes" to "Did you mean COSC 480?": the original question is re-asked with the confirmed code, not "yes". (Task 5 + Task 7 tests)

---

### Task 1: OpenRouter chat provider

**Files:**
- Modify: `backend/app/config.py` (`llm_provider` Literal, new settings, `chat_api_key`, `chat_model`)
- Modify: `backend/app/llm/chat.py` (new class, `get_chat_provider`)
- Test: `backend/tests/test_chat_providers.py`

**Interfaces:**
- Produces: `OpenRouterChatProvider(api_key: str, model: str, base_url: str, timeout: float)`, `provider_id = "openrouter"`; settings `openrouter_api_key: str = ""`, `openrouter_base_url: str = "https://openrouter.ai/api/v1"`, `openrouter_chat_model: str = "meta-llama/llama-3.3-70b-instruct:free"`, `openrouter_timeout_seconds: float = 45.0`.

- [ ] **Step 1: Failing tests**: following the file's existing replay-server pattern:
  - `test_openrouter_unavailable_without_key`: `available() == (False, "OPENROUTER_API_KEY is not set")`
  - `test_openrouter_posts_openai_shape`: against the replay server, the request goes to `{base}/chat/completions` with `Authorization: Bearer <key>`, messages = system + turns, and `complete()` returns the stripped `choices[0].message.content`.
  - `test_openrouter_empty_completion_raises`: an empty content raises `ProviderUnavailable`.
  - `test_get_chat_provider_openrouter`: `llm_provider="openrouter"` → instance of `OpenRouterChatProvider`.
- [ ] **Step 2: Run** `pytest tests/test_chat_providers.py -q`. Expect FAIL (import error).
- [ ] **Step 3: Implement.** Use the stdlib `_http_json` (as Gemini/Ollama do, no SDK dependency). Add `"openrouter"` to the `chat_api_key` and `chat_model` maps.
- [ ] **Step 4: Run** the full backend suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(llm): add OpenRouter chat provider`.

### Task 2: Curated links on out-of-scope answers

**Files:**
- Create: `backend/data/advisor_links.yaml` (or the repo's existing data dir; match where catalog YAML lives)
- Modify: `backend/app/advisor/answers.py` (attach links), `backend/app/advisor/chat.py` (`AdvisorReply.links`), chat schema `ChatResponse.links`
- Test: `backend/tests/test_advisor_links.py`

**Interfaces:**
- Produces: `Link(name: str, url: str)` dataclass; `links_for(intent: Intent) -> list[Link]`; `AdvisorReply.links: list[Link] = []`; JSON `ChatResponse.links: list[{"name","url"}]`.
- YAML shape: `links: [{name: "the Registrar's Office", url: "...", intents: [unknown]}, ...]`. Seed with: Registrar, the Computer Science department, the undergraduate catalog. Verify each URL loads before committing.

- [ ] **Step 1: Failing tests**:
  - `test_unknown_question_gets_curated_links`: `deterministic_answer(facts, "can I get a parking permit?")` has intent `UNKNOWN`, and `links` is a non-empty subset of the YAML entries.
  - `test_known_intent_has_no_links`: a `CREDITS` question gets `links == []`.
  - `test_links_survive_rephrase`: an `ask()` with a fake provider keeps the same `links` (links never come from model text).
  - `test_yaml_urls_are_https`: every URL starts with `https://`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.** Load YAML once (`functools.cache`). Links attach in the engine answer and pass through `ask()` untouched.
- [ ] **Step 4: Run** the full suite, including `test_no_llm_in_engine.py`. Expect PASS.
- [ ] **Step 5: Commit** `feat(advisor): curated links for out-of-scope questions`.

### Task 3: Speakable pass

**Files:**
- Create: `backend/app/speech/speakable.py`
- Test: `backend/tests/test_speakable.py`

**Interfaces:**
- Produces: `to_speech(text: str, links: Sequence[Link] = ()) -> str`. Pure, deterministic, no model.

- [ ] **Step 1: Failing tests**:
  - `test_course_code_spoken`: `"Take COSC 241."` → contains `"C O S C 2 41"` (letters spaced, number split so Aura reads it naturally; pin whatever exact form you choose here)
  - `test_bullets_flattened`: `"- COSC 241\n- MATH 141"` has no `-` or newline-bullet, and the items are joined with commas and "and".
  - `test_markdown_stripped`: `**`, `#`, and backticks are removed.
  - `test_url_replaced_by_name`: text containing a link's URL → the URL is absent and `link.name` is present; links not in the text are appended as `"You can check the Registrar's Office."`
  - `test_idempotent`: `to_speech(to_speech(x)) == to_speech(x)` for a sample answer.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement** with regexes.
- [ ] **Step 4: Run.** Expect PASS.
- [ ] **Step 5: Commit** `feat(speech): deterministic speakable text pass`.

### Task 4: Deepgram TTS + `/advisor/speak`

**Files:**
- Create: `backend/app/speech/tts.py`
- Modify: `backend/app/config.py` (`deepgram_tts_model: str = "aura-2-thalia-en"`), `backend/app/routers/voice.py` (or `chat.py`, wherever the `/advisor` router lives) (new route)
- Test: `backend/tests/test_speech_tts.py`, `backend/tests/test_speak_api.py`

**Interfaces:**
- Produces: `DeepgramTTS(settings).synthesize(text: str) -> bytes` (mp3), raising `ProviderUnavailable` on no key or HTTP failure; `POST /advisor/speak` body `{"text": str, "links": [...]}`, auth required, returns `audio/mpeg` of `to_speech(text, links)`; 503 `{"detail": "..."}` when TTS is unavailable; 400 on empty text; 413 when text > 2000 chars.

- [ ] **Step 1: Failing tests**:
  - `test_tts_posts_to_speak_endpoint` (replay server): POST `{base}/v1/speak?model=aura-2-thalia-en&encoding=mp3`, `Authorization: Token <key>`, body `{"text": ...}`; returns the response bytes.
  - `test_tts_without_key_unavailable`.
  - `test_speak_route_returns_audio`: with `DeepgramTTS.synthesize` monkeypatched, `200`, `content-type: audio/mpeg`, and synthesize received the `to_speech` output (no URL in it).
  - `test_speak_route_requires_auth` → 401. `test_speak_route_503_without_key`. `test_speak_route_rejects_empty_and_huge`.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.** Use stdlib `urllib` for raw bytes (no JSON).
- [ ] **Step 4: Run** the full suite. Expect PASS.
- [ ] **Step 5: Commit** `feat(speech): Deepgram Aura TTS and /advisor/speak`.

### Task 5: Live-turn read-back gate

**Files:**
- Modify: chat request schema (`ChatRequest.mode: Literal["text","live"] = "text"`, `ChatRequest.confidence: float | None = None`), `backend/app/routers/chat.py::chat`, `backend/app/speech/clarify.py`
- Test: `backend/tests/test_live_readback.py`

**Interfaces:**
- Consumes: `suggest_clarifications`, `normalize_course_mentions`, catalog codes as the existing `/transcribe` route gets them.
- Produces: `readback_question(text: str, confidence: float, catalog_codes) -> Clarification | None`. Returns a question when `confidence < 0.6` and the text mentions a course, or when it mentions a course-like code not in the catalog, or when there's an ambiguous bare number (existing `suggest_clarifications`). `ChatResponse.clarification: {"spoken","candidates","question"} | None`. When it is set, `answer == clarification.question`, `source == "readback"`, and the advisor is NOT called.
- Normal (confident, known code) live answers start with the code heard: the route prefixes `"For COSC 480: "` when the question names exactly one catalog course and the answer doesn't already start with it.

- [ ] **Step 1: Failing tests**:
  - `test_low_confidence_course_asks_back`: `mode="live"`, `confidence=0.4`, `"can I take COSC 480"` → `clarification.question == "Did you mean COSC 480?"` and the advisor `ask` is never called (monkeypatched spy).
  - `test_unknown_code_asks_back`: `"COSC 999"` at 0.95 → a clarification.
  - `test_confident_known_course_answers_with_code_prefix`: `"what does COSC 241 need"` at 0.95 → no clarification, and the answer starts with `"For COSC 241"`.
  - `test_text_mode_unchanged`: the same request without `mode` → identical to the current behavior.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite, including the untouched `test_voice_to_advisor.py`. Expect PASS.
- [ ] **Step 5: Commit** `feat(voice): course-code read-back for live turns`.

### Task 6: Frontend: speak button on replies

**Files:**
- Modify: `frontend/src/api.js` (`speakText({text, links}) -> Promise<Blob>`), `frontend/src/components/AdvisorChat.jsx`
- Create: `frontend/src/components/useSpeaker.js`: `useSpeaker() -> { speak(text, links): Promise<void>, stop(): void, speaking: boolean, error: string|null }`. Plays via a single `Audio` element with an object URL; `stop()` pauses and revokes immediately.
- Test: `frontend/src/components/useSpeaker.test.jsx`, and render links as clickable `<a target="_blank" rel="noreferrer">` under replies

- [ ] **Step 1: Failing tests**: `speak` sets `speaking` true, then false on `ended`; `stop()` mid-play → `speaking` false and `pause` called; a 503 → `error` set and no throw; the reply's links render as anchors.
- [ ] **Step 2: Run** `npm test`. Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** lint + test + build. Expect PASS.
- [ ] **Step 5: Commit** `feat(ui): speak advisor replies aloud`.

### Task 7: Live mode, phases 1 and 2 (one turn, then hands-free)

**Files:**
- Create: `frontend/src/components/useVoiceConversation.js`, `frontend/src/components/LiveVoicePanel.jsx`
- Modify: `frontend/src/components/useLiveTranscription.js` (expose `onPartial(text)` and `onUtteranceEnd`; add `getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}})`), `AdvisorChat.jsx` (a "Talk" toggle that opens the panel), `api.js::askAdvisor` (pass `mode`, `confidence`)
- Test: `frontend/src/components/useVoiceConversation.test.jsx`

**Interfaces:**
- Consumes: `useLiveTranscription`, `askAdvisor`, `useSpeaker`.
- Produces: `useVoiceConversation({ conversationId, handsFree }) -> { state: "idle"|"listening"|"thinking"|"speaking", turns: Array<{role, text, links?}>, start(), end() }`.
- Transitions: `listening` →(done with words)→ `thinking` → `askAdvisor({mode:"live", confidence})` → `speaking` → on audio end: `handsFree ? listening : idle`. Done with no words → stay `listening`. Pending clarification: the next utterance, if it's yes-like (`/^(yes|yeah|yep|correct|right)\b/i`), re-sends the *original* question with `clarification.spoken` replaced by `candidates[0]`; otherwise it's treated as a new question.
- Phase 1 = `handsFree:false` (push the button per turn); phase 2 = `handsFree:true`. Panel: a big button, a state label, and turn bubbles with links.

- [ ] **Step 1: Failing tests** (mock the three deps): one push-to-talk turn goes idle→listening→thinking→speaking→idle; hands-free returns to `listening`; an empty utterance makes no `askAdvisor` call; a speak error still appends the text turn and returns to listening; a clarification followed by "yeah" re-asks `"can I take COSC 480"` with the candidate.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** lint + test + build. Expect PASS. Manual check: one real spoken turn against the dev server.
- [ ] **Step 5: Commit** `feat(ui): live voice conversation (push-to-talk + hands-free)`.

### Task 8: Phase 3: barge-in

**Files:**
- Modify: `useVoiceConversation.js` (keep listening during `speaking`), test file

**Interfaces:**
- Rule: while `speaking`, a partial transcript with ≥ 2 word characters' worth of real words (`/\w{2,}/`) → `speaker.stop()`, state `listening`, drop the current reply's remaining audio; that utterance becomes the next question. Energy alone never triggers (echo safety beyond `echoCancellation`).
- Any in-flight `askAdvisor` for a superseded turn is ignored when it returns (turn id check).

- [ ] **Step 1: Failing tests**: a partial "wait" during speaking → `stop` called and state `listening`; a partial "" or "a" doesn't stop playback; a stale reply arriving after barge-in isn't spoken or appended twice.
- [ ] **Step 2: Run.** Expect FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** lint + test + build, then the full backend suite. Manual check on laptop speakers: the advisor doesn't interrupt itself.
- [ ] **Step 5: Commit** `feat(ui): interrupt the advisor by speaking`.

Phase 4 (stress testing) is out of scope for this plan.
