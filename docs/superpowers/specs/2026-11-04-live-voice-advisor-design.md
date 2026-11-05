# Live voice advisor — design

**Date:** 2026-11-04 · **Status:** design, awaiting approval · **Demo:** 2026-11-05

## Intent

A student talks to the advisor the way they would talk to ChatGPT's or Claude's
voice mode: "Do I have any classes left? How do I graduate early — should I take
summer classes?" The advisor answers out loud, the conversation keeps going
hands-free, and the student can cut in mid-sentence.

Why: the project is a *multimodal* advisor. Voice input (dictation into the chat
box via Deepgram live STT) exists; the advisor still only answers in text. A
spoken back-and-forth completes the voice modality and is the centerpiece of the
Nov 5 presentation and Progress Report 3.

## The non-negotiable

Spoken answers are held to the same rule as text answers: **the model never
computes degree progress.** Voice goes through the existing advisor pipeline
unchanged (`docs/advisor.md`): facts → engine-written answer → optional model
rephrase → `check_rephrasing` (discard on doubt). Voice adds no new path for the
model to state facts.

When the advisor cannot answer (out of scope, unknown), it says so and points to
a real resource. Links come from a fixed, git-tracked list we control (registrar,
department, catalog pages) — never URLs the model produces. Links are spoken as a
short name ("the registrar's page") and shown clickable on screen.

## Goals

- OpenRouter as a fifth chat provider (alongside anthropic, openai, gemini, ollama).
- Text-to-speech for advisor answers (Deepgram Aura).
- A live conversation mode: hands-free turn-taking with interruption (barge-in).
- Every spoken turn also appears as text on screen (student's words + reply + links).

## Non-goals

- Model tool-calling. The engine answers; the model only rephrases. Unchanged.
- Deepgram Voice Agent or any hosted all-in-one agent (decided: build ourselves
  for control).
- Voice cloning, voice selection UI, multilingual voice.
- Mobile/native apps; this is the existing React web app.

## Anti-goals (failure even if it "works")

- The voice says something the text pipeline would have discarded.
- The model invents a link, course, or count.
- The advisor keeps talking over the student after they start speaking.
- The advisor "hears" its own voice from the speakers and answers itself.

## Groundwork (before the phases)

**G1. OpenRouter provider.** Add `"openrouter"` to `llm_provider` in
`app/config.py` with `openrouter_api_key`, `openrouter_base_url`
(`https://openrouter.ai/api/v1`), `openrouter_chat_model`. Implement it like the
existing providers in `app/llm/chat.py` (it is OpenAI-compatible
`/chat/completions`). Since the model only rephrases and `check_rephrasing`
guards the output, free models are safe to use; a bad rephrase falls back to the
engine's own answer. Record which free models pass the existing
rephrase/consistency tests.

**G2. Text-to-speech.** A small TTS interface in `app/speech/` with one
implementation: **Deepgram Aura TTS** (user's call: the key is already
configured and free tier covers the demo). OpenRouter TTS is a later option. A backend
endpoint streams audio for a given advisor reply; the chat UI gets a "speak"
control on replies. Speak in sentence-sized chunks so audio starts fast.

## Live conversation phases

Each phase is demoable on its own.

1. **One spoken turn.** Press a button, speak, hear the answer. Reuses the
   existing live STT (`app/speech/live.py`, `useLiveTranscription.js`), sends the
   final transcript through the advisor, plays the TTS reply, shows both on
   screen. Proves the full loop.
2. **Hands-free back-and-forth.** No button per turn. End of speech (Deepgram
   `UtteranceEnd`, already parsed) triggers the answer; after playback it listens
   again until the student ends the session.
3. **Interruption.** If the student speaks while the advisor is talking, playback
   stops immediately and the advisor listens. Must not trigger on its own voice:
   use browser echo cancellation on the mic (`getUserMedia` constraints) as the
   first line; builder decides whether more is needed (e.g. require real
   transcript words, not just energy, before barging in).
4. **Stress testing** (after Report 3): noise, long sessions, dropped
   connections, slow/free models, latency measurements.

## Constraints

- Works with no LLM key (engine answer is spoken as-is); TTS needs a key.
- Existing tests (`test_no_llm_in_engine.py` etc.) stay green; voice code must not
  import into `app/catalog/` or `app/audit/`.
- All student data fictional.

## Left to the builder

Endpoint shapes and WebSocket vs HTTP for audio, audio format (pcm vs mp3),
chunking strategy, frontend component structure, exact barge-in heuristic, which
OpenRouter models/voice to default to, and test approach — following the
existing patterns (replay/fake servers, ScriptedLive-style fakes).

## Settled after builder check

- **What is spoken:** the final checked text, passed through a deterministic
  "speakable" pass (plain code, no model): course codes read out ("COSC 241" →
  "C-O-S-C two forty-one"), bullet lists flattened, links replaced by their
  names. Sits after `check_rephrasing`, so no new path for the model to state
  facts.
- **Out-of-scope + links:** reuse the engine's existing refusal path in
  `answers.py`; attach links from a git-tracked YAML of curated Morgan State
  pages, in the facts layer (so text answers get them too). Builder verifies each
  URL resolves.
- **History:** a live session is one conversation id and uses the existing
  history store, so follow-ups work.
- **Demo scope:** phases 1–3 live on Nov 5 (user's call).
- **Auto-send:** live mode sends transcripts without a manual check (needed for
  hands-free); dictation mode keeps "never auto-sent" and its test. The test is
  narrowed to dictation mode, and live mode gets its own tests.
- **Course-code read-back (user's call):** when a turn names a course, the
  advisor says the code it heard back ("For COSC 480, ..."). If confidence is
  below the existing 0.6 threshold, or the code isn't in the catalog, it asks
  "Did you mean COSC 480?" and waits instead of answering.

## Decisions made by Claude (check these)

- Links from a fixed curated list, spoken by name and shown on screen.
- Browser echo cancellation as the first defense for barge-in.
