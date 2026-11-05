# COSC 490 — Progress Report 3

**Project:** Multimodal Academic Advisor
**Repository:** github.com/rosaiju/multimodal-academic-advisor (private)
**Period:** September 22 – November 4, 2026 · **State reported:** `main` at `5249737`
plus branch `feature/live-voice-advisor` (live voice advisor, presented November 5)

## What the system does

A student uploads a transcript, confirms every extracted row, and a deterministic
Python engine audits that record against the Morgan State 2026-2028 Computer
Science B.S. catalog. A conversational advisor answers questions about the audit
by text or by voice, and can now hold a spoken conversation: the student asks
"what do I have left, and should I take summer classes?" out loud and hears the
answer.

The commitment is unchanged: the language model never computes degree progress.
The engine writes every answer; a model may only rephrase it, and a consistency
check discards any rephrasing that changes a credit figure, course, eligibility
statement or refusal. Voice follows the same path, so a spoken answer can never
say something the text answer would not.

## Verified progress since Report 1

`main` has 29 new commits and 7 merged pull requests (#7–#11, #14, #15). **908 backend tests pass**
on `main`; the voice-advisor branch adds 28 more backend tests and 16 frontend tests
(936 backend, 27 frontend, lint and build clean), plus a fresh-context code review whose 4 serious findings were fixed.

- **Live model verified.** Report 1's largest open claim, that no language model
  had ever been called, is closed. Ollama (`qwen2.5:7b`, `llama3.2:3b`) ran live
  on October 6. The first live run showed the course-code guard was not enough,
  which led to `app/advisor/consistency.py`, a full fact check on every
  rephrasing. About 2 of 6 live rephrasings pass it; every rejection was read and
  was a real departure from the engine.
- **Voice input.** Deepgram `nova-3` live streaming: words appear as the student
  speaks, catalog course codes are sent as keyterms, and bare numbers ("four
  fifty nine") are offered back as "Did you mean COSC 459?". Checked with a human
  voice in Chrome against real Deepgram.
- **What-if simulator and unlock explorer.** "What if I take COSC 241 and MATH
  141?" audits a copy of the record without writing it; the explorer shows what a
  course needs and unlocks.
- **Catalog.** General-education requirements encoded (PR #14).
- **Frontend tests.** Vitest suite added (PR #15).
- **Live voice advisor (new, this period):** see below.

## Live voice advisor

Built on `feature/live-voice-advisor`, each piece with tests:

- **OpenRouter** as a fifth model option (one key, many free models). Weak free
  models are safe here because the model only rephrases and is fact-checked.
- **Text-to-speech** through Deepgram Aura (`POST /advisor/speak`), verified
  against the real Deepgram API. A deterministic pass makes answers speakable:
  course codes spelled out, lists turned into sentences, URLs replaced by names.
- **Phase 1–2: spoken turns, then hands-free.** Ask, hear the answer, and keep
  talking without pressing a button.
- **Phase 3: interruption.** Talking over the advisor stops it and it listens.
  Browser echo cancellation keeps it from hearing itself.
- **Course-code read-back.** When unsure ("COSC two forty-one" at low
  confidence, or a code not in the catalog) it asks "Did you mean COSC 241?"
  before answering, so a misheard course is never answered aloud as fact.
- **Curated links.** When it can't answer, it points to the Registrar, the CS
  department or the catalog, from a fixed list. No model ever supplies a URL.

## Team progress

Of the 29 commits on `main` this period, 15 are by Rohan Sainju, 11 by SawcyD
(voice input, speech providers, simulator), 2 by Sameer Shiwakoti (general
education catalog) and 1 by Joshua (frontend tests). All four members now
contribute through pull requests.

## Remaining work

- **Phase 4, stress testing** of the voice advisor: noisy rooms, long sessions,
  dropped connections, slow free models, and latency measurements.
- Anthropic, OpenAI and Gemini have still never been called live (no keys);
  OpenRouter is tested only against a local replay server until a key is set.
- The catalog is still partial; open questions await department adjudication.
- Safari and Firefox are untested for voice. Nothing is deployed.
