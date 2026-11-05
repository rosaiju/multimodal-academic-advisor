# The conversational advisor

How a student asks "what should I take next semester?" and gets an answer that
cannot be wrong about their degree.

## The problem this design solves

A language model asked directly about degree requirements will answer
confidently and sometimes wrongly. It will invent a course number, miscount
credits, or reassure a student they can graduate. For a chatbot that is
embarrassing. For a system a student uses to decide what to register for, it is
the whole risk.

The project's rule is that **the model never computes degree progress**. The
advisor implements that rule in four steps.

## The four steps

```
      question
         |
         v
  1. build facts  ......  app/advisor/facts.py
         |                 runs run_audit() + build_plan() - the SAME calls the
         |                 dashboard makes, on the same stored record
         v
  2. answer it    ......  app/advisor/answers.py
         |                 route chosen by keywords, or by a model reading the
         |                 question + recent turns (understanding.py, optional);
         |                 prose assembled from facts either way
         |                 NO MODEL INVOLVED - this answer is already correct
         v
  3. rephrase?    ......  app/advisor/chat.py + app/llm/chat.py
         |                 only if a provider is configured AND reachable.
         |                 The model is given the facts and the prepared answer
         |                 and asked to reword it. It is never asked what the
         |                 student needs.
         v
  4. check it     ......  app/advisor/consistency.py::check_rephrasing
         |                 does the reply still say what step 2 said - same
         |                 credit figures, eligible courses, blockers, in-progress
         |                 status, catalog caveat, refusal? Any doubt: DISCARD
         |                 the model's answer and ship step 2's.
         v
      reply + a label saying which of the two you are reading
```

**Step 1** is why the chat and the dashboard can never disagree. They are not two
implementations of "what does this student still need" - they are one function
called twice.

**Step 2** is why the demo works with no API key, no credits and no internet.
Both student credit applications were still pending when this was built, and a
capstone that cannot be demonstrated because of someone else's quota is not
finished. Every question in docs/demo-reference.md is answered correctly with no provider
configured at all.

**Step 4** is the part that would be easy to skip. A model that says "take
COSC 499" about a course that does not exist has produced exactly the failure
this architecture exists to prevent, and annotating it with a caveat is not
enough - a student reads the sentence, not the footnote. So the answer is thrown
away and the deterministic one is shown instead, with a note saying why.

Checking only for invented course codes turned out not to be enough. The first
live run (below) produced replies that named only real courses and were still
wrong. `check_rephrasing()` rejects a reply that:

| Check | Live failure it was written for |
|---|---|
| mentions a course code not in the facts, the engine's answer or the question | (the original guard) |
| drops a course code the engine named - eligible courses, blockers, missing prerequisites, in-progress courses (an unmet block's `Options:` menu may be shortened) | "COSC 352 and 354 are blocked because they require COSC 220" - 354 also needs COSC 241 |
| drops a headline total or any figure stated as credits or a percentage | "You need 105 more credits" with the 15 earned and 120 required gone |
| states a digit the engine never produced | "you need 101 more" when the engine said 105 |
| uses the progress percentage without saying it is progress | "That's 12.5% of the total credits required" |
| calls a blocked course available, or an eligible one blocked | |
| calls an in-progress course completed | |
| implies the student can graduate when the engine says not | |
| gives a course another course's title | "COSC241 Computer Organization and Architecture" (COSC 243's title) |
| drops the partial-catalog warning | most rejections in practice |
| answers a question the engine declined | |

Every check is a narrow pattern, not an understanding of English. It cannot
prove a reply right; it catches the specific ways rephrasing has been seen to go
wrong, and errs towards rejecting, because a rejected reply costs the student
nothing - they get the engine's answer. Matching is by context, not by value:
an earlier draft confused "4 requirement blocks" with "4 credits in progress"
and threw away a correct answer.

## What the model is allowed to see

`render_facts()` builds the entire permitted universe of academic fact: credits
earned, required and remaining (pre-computed, so the model never does
arithmetic), every requirement block and its status, confirmed coursework,
recommendations with the engine's own reasons, blocked courses with their
missing prerequisites, courses in progress (marked as not counted), and the
catalog coverage caveat.

The system prompt then forbids adding anything not in that block. Prompt rules
are not a security boundary, which is why step 4 exists as well.

## Question understanding (model-chosen routes)

Keyword routing reads one message on its own, so follow-ups failed. After "How
many credits am I missing?", the follow-up "It says I have earned 138 credits. So
I don't think I need anymore. Right?" got the identical credits answer.

`app/advisor/understanding.py` asks the configured chat model to read the
question plus the last four turns (each clipped to 240 characters) and reply with
strict JSON:

```json
{"intent": "remaining_requirements", "course_codes": [], "follow_up_of": "credits"}
```

Ollama is called with `format: "json"`, Gemini with
`responseMimeType: application/json`, both at temperature 0 and at most 80 tokens.
What the model may and may not do:

* It picks a **route** - one of the `Intent` values. It does not write an answer
  and never sees the student's record. `deterministic_answer(facts, question,
  intent, codes)` still builds every sentence from the engine, and any rephrasing
  still goes through `check_rephrasing`.
* `crisis`, `wellbeing` and `no_record` cannot be chosen by the model. Crisis and
  stress wording is routed by keywords before the model is asked.
* Course codes are kept only if the catalog has them **and** they appear in the
  question or recent turns. If the question names catalog courses itself, those
  are used and the model's list is ignored.
* Any failure (no provider, timeout, non-JSON, an intent outside the list) falls
  back to keywords. So does a model `unknown` when keywords found a route, and a
  confirmation question ("...right?") is never answered with credits alone.
* The reply carries `understood_by: "model" | "keywords"`, shown in the chat as
  "Question read by the model" or "Matched by keywords" beside the source label.
  It never changes the "Computed by the engine" / "Phrased by ..." label.

Settings: `ADVISOR_UNDERSTANDING=model|keywords` (default `model`, which only
takes effect when the chat provider is reachable), `ADVISOR_UNDERSTANDING_TIMEOUT_SECONDS`
(default 45), and `OLLAMA_UNDERSTANDING_MODEL` to route with a smaller model than
the one that rephrases. `use_llm: false` in a chat request skips it.

**Live, 2026-11-04, `qwen2.5:7b` on Ollama (laptop CPU).** Routing alone, eight
questions: 2.7-5.9 s each once the model was loaded (18.8 s for the first, cold).

| Question (with history where shown) | Keywords | Model |
|---|---|---|
| after a credits answer: "It says I have earned 138 credits. So I don't think I need anymore. Right?" | remaining_requirements | `credits` on the first run (now overridden, see above); `remaining_requirements` after the prompt fix |
| after "Can I take COSC 354?": "Why does this course not count yet?" | explain_requirement | explain_requirement, codes [COSC354] from history |
| after "What does COSC 220 unlock?": "and what about COSC 241?" | course_info | unlocks, [COSC241] |
| "Can you lay out the rest of my degree term by term?" | plan_semesters | plan_semesters |
| "Should I do COSC 241 or COSC 243 first?" | compare | compare |
| "If I bomb COSC 220 this semester what happens?" | course_info | what_if, [COSC220] |
| "am I on track or what" | progress | progress |
| "Who is the best professor for COSC 241?" | course_info | unknown -> keywords kept |

Full `ask()` (routing + rephrasing), six questions: 54-126 s each. Routes were
right every time (five by the model; the stress question by keywords, by design).
One rephrasing shipped (the "bomb COSC 220" what-if, faithful); five were
discarded by the consistency check - dropped catalog warning (3), a dropped "3
credits" figure, and COSC 241 given COSC 243's title in the semester plan - and
the engine's text was shown with the reason.

## Engine answers added for the demo (Nov 2026)

All built from engine output; a model may only rephrase them under the same check.

| Question | Intent | Engine source |
|---|---|---|
| "So I don't need any more, right?" | `remaining_requirements` | opens with an engine-decided "No, not yet" |
| "Can you help me plan my remaining semesters?" / "When can I graduate?" | `plan_semesters` | `audit/schedule.generate_schedule` (no graduation date given) |
| "Which is better for me, COSC 241 or COSC 243?" | `compare` | `audit/compare.compare_courses` (ranks nothing) |
| "Why does COSC 220 not count?" / "why is my supporting courses block not satisfied?" | `explain_requirement` | `audit/explain_audit.explain_all` |
| "What happens if I fail COSC 220?" | `what_if` | `audit/impact.explore_unlocks` - what stays blocked |
| "I'm overwhelmed and worried I won't graduate on time" | `wellbeing` | facts: credits, open blocks, eligible courses |

`answer_credits` no longer says "you still need 0" or "115% of the way" once the
credit total is met: it says the total is exceeded and lists the requirement
blocks still outstanding.

**Wellbeing.** Routed only on words the student wrote ("overwhelmed", "stressed",
or "worried"/"scared" together with graduating, failing or falling behind) -
nothing infers a mood. The reply acknowledges what they said, gives their real
standing, suggests their academic advisor and the campus counseling center, and
promises no graduation date. It names no office, phone number or URL, because none
has been verified for this project. Self-harm wording ("kill myself", "end my
life", ...) gets a fixed reply that points to 911 and 988 (the US Suicide &
Crisis Lifeline, call or text); that reply is never routed or rephrased by a model.

## Providers

| Provider | Needs | How it talks |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | SDK, imported lazily |
| `openai` | `OPENAI_API_KEY` | SDK, imported lazily |
| `gemini` | `GEMINI_API_KEY` | stdlib `urllib` |
| `ollama` | a local daemon | stdlib `urllib` |

Gemini and Ollama are deliberately plain HTTP rather than SDKs. Gemini's REST
surface is small enough that a dependency buys nothing, and Ollama exists for
the case where there are no credits and possibly no ability to install
anything - adding a package in order to reach the offline fallback would defeat
the point of having one. **No new dependency was added for any of this.**

`available()` is cheap for key-based providers (is the key set?) and genuinely
probes the server for Ollama, because "is Ollama running" has no answer in
configuration. When the configured model is not pulled, the reason names the
models that *are*.

**Gemini is blocked on credentials.** No `GEMINI_API_KEY` exists, so it has never
been called. Loopback tests (`test_chat_providers.py`, `test_ai_providers.py`)
pin the request shape (`systemInstruction`, `contents` with the `model` role,
`generationConfig`, `responseMimeType` for routing), response parsing, every
failure mode, and that the key travels only in the `x-goog-api-key` header, never
the URL.

### Reading transcript images locally (vision)

`VISION_PROVIDER=ollama` makes scanned or photographed transcripts readable with
no key: `OllamaVisionProvider` (`app/llm/provider.py`) sends the image and the
existing transcription prompt to Ollama with `format: "json"`.

```bash
ollama pull qwen2.5vl:3b        # ~3.2 GB
VISION_PROVIDER=ollama OLLAMA_VISION_MODEL=qwen2.5vl:3b uvicorn app.main:app
```

* Images only (PNG/JPG/...). PDFs still go to the deterministic PDF reader; a
  scanned PDF is not offered to the local model.
* Every row is `unverified_extraction`, at most MEDIUM confidence, and must be
  confirmed by the student on the review screen. Nothing is stored by an upload.
* If the model fails or times out (`OLLAMA_VISION_TIMEOUT_SECONDS`, default 600),
  the upload returns 503 with a generic message; text transcripts keep working.
* **Live, 2026-11-04:** `data/samples/demo_transcript.png` (the 7 courses of
  `demo_transcript.txt`, rendered as an image and marked as an unofficial demo for
  a fictional student) through `POST /ingest/transcript` with `qwen2.5vl:3b`:
  **7 of 7 rows correct** (code, term, grade, credits), 105 s on the laptop CPU.
  One clean, typed image is not evidence about phone photos or real scans.
* **Off by default** because of that latency; turn it on for a demo where a
  two-minute wait is acceptable.

## Every way this degrades

All of these produce a correct engine answer plus a `notice`, never an error:

- no key set
- key rejected (401/403)
- quota exhausted or rate limited (429)
- provider 5xx
- host unreachable, or request timed out
- a safety filter returned zero candidates
- an empty completion, a non-JSON body, or JSON in the wrong shape
- the connection dropped or the body was cut off mid-response
- any other exception from a provider (logged, never shown)
- a reasoning model's `<think>` block that never closed (a closed one is stripped)
- the reply failed `check_rephrasing()` - the notice names the first reason

The UI renders `notice` as a quiet italic line and labels the answer
"Computed by the engine". `GET /advisor/health` reports the state up front so
the panel can say so before the first question.

## Conversation history

In memory, keyed by `(student_id, conversation_id)` and capped at
`ADVISOR_HISTORY_TURNS` turns and 200 conversations, oldest evicted first.

Keyed by student as well as conversation so that guessing another student's
conversation id reveals nothing. Not persisted: a demo does not need chat
history to survive a restart, and writing every half-finished question to disk
beside the academic records is a worse trade than losing them.

A multi-worker deployment would need this in a shared store. It is a single
process today, and that is written down rather than pretended away.

## Voice input

The mic in the advisor panel is an input channel only. It turns speech into text
for the question box and never asks the advisor anything itself.

- **Flow (live):** browser mic → `MediaRecorder` chunks every 250 ms → WebSocket
  `/advisor/listen` (token in the first message, never the URL) → backend relay
  (`app/routers/voice.py`) → Deepgram live `wss /v1/listen` (`nova-3`,
  `smart_format`, `mip_opt_out`, `interim_results`, `endpointing=300`,
  `utterance_end_ms=1500`). The box shows finished phrases plus the phrase still
  being heard, course codes converted, and the session ends 1.5 s after the
  student stops talking (or on a click, 30 s, 8 s of silence, or 2 MB).
- **Key terms:** every catalog course code in spoken form ("COSC 241") is sent as
  a Deepgram `keyterm`, built by `app/speech/keyterms.py` from the loaded catalog.
- **Spoken numbers:** Deepgram writes "Computer Science two forty three" as
  words (its `numerals` option makes it "2 43"). `app/speech/course_codes.py`
  rewrites these as `COSC 243`, and only when that code is in the catalog. An
  unknown code is left exactly as heard, never guessed at.
- **Never auto-sent.** When the session ends, the text stays in the box for the
  student to check and edit - a live partial can be wrong for a moment (one test
  showed "COSC two 480s" before it settled on "COSC 241"). Below 0.6 average
  confidence the panel asks them to check it.
- `POST /advisor/transcribe` (record-then-upload) remains in the backend but the
  UI no longer uses it.
- **Browsers:** Chrome and Edge (webm/opus) are supported. Safari's mp4 chunks are
  unverified; if Deepgram cannot decode them the student sees the unavailable note.
- **Privacy:** audio is never saved. It exists for one request and is then
  discarded, and Deepgram is told not to keep it for training. One caveat: the
  web framework buffers any upload part over 1 MB in a temporary file for the
  length of the request. A 30-second question is about 0.5 MB, so it stays in
  memory in practice.
- **Degrades like the model:** no `DEEPGRAM_API_KEY` means no mic button. A
  rejected key, no credit, a rate limit or a timeout means a quiet note, and
  typing still works. Deepgram's error text is logged, never shown.
- `app/audit/` and `app/catalog/` may not import `app.speech`
  (`test_no_llm_in_engine.py`).
- **Into the advisor:** a finished transcript only fills the box. The student
  presses Ask, and the question then takes the same route as a typed one -
  `POST /advisor/chat`, the degree engine, and the rephrasing check.
  `tests/test_voice_to_advisor.py` asserts that a spoken and a typed question get
  the same answer, and that a voice session alone never reaches the advisor.
- **In the UI:** a "Listening..." status while recording, and a standing note
  that audio goes to Deepgram's speech service and nothing reaches the advisor
  until Ask is pressed.

### What has been verified, and how

| Check | How | Live Deepgram? |
|---|---|---|
| Deepgram accepts our requests and keyterms; live streaming works | SawcyD, Sep 22, synthesized speech (see PROJECT_STATUS.md) | **yes** |
| Relay, auth, limits, parsing, course-code conversion | backend tests with a scripted Deepgram fake | no |
| Voice -> chat -> engine answer | `test_voice_to_advisor.py`, scripted fake | no |
| Real browser: fake mic -> MediaRecorder -> relay -> UI box -> Ask -> answer, with live Ollama phrasing | Chromium's fake audio device, local fake Deepgram server (Oct 6) | no |
| Blocked mic, unsupported browser | browser, with `getUserMedia` / `MediaRecorder` overridden | no |
| **Human voice -> real Deepgram -> UI box -> Ask -> advisor** | Chrome on the dev laptop, Oct 6, two questions; backend log shows two `utterance_end` sessions, each followed by exactly one `/advisor/chat` (the silent session produced none). Both live `qwen2.5:7b` rephrasings were discarded by the consistency check and the engine's answers shown | **yes** |

**Not yet verified:** Safari or Firefox; accents or a noisy room; the 0.6
low-confidence note (both live questions transcribed cleanly).

## Speech providers

Voice goes through one vendor-neutral contract, `LiveSpeechProvider` in
`backend/app/speech/base.py`, and a registry (`speech/registry.py`) that picks the
implementation from `SPEECH_PROVIDER` (default `deepgram`). The relay in
`routers/voice.py`, the one-shot `POST /advisor/transcribe` route and
`GET /advisor/health` all talk to the registry and nothing else, so adding a vendor
is one class and one line in the registry.

| `SPEECH_PROVIDER` | Class | Behaviour |
|---|---|---|
| `deepgram` (default) | `DeepgramLive` | True streaming: words appear while the student talks |
| `openai` | `OpenAILive` | Buffers the recording, transcribes once on stop (`OPENAI_API_KEY`, `OPENAI_STT_MODEL`, default `gpt-4o-transcribe`). No live text, so the "no speech" timer is off for it |

Rules every provider keeps: keys stay on the server and are never sent to the
browser; a vendor's own error text is logged and never forwarded; and
**course-code normalization is not a provider concern** - providers return what
they heard and `app.speech.course_codes` rewrites it identically for all of them.
Authentication, the audio-size cap, the time limit, "nothing leaves the box until
the student presses Ask" and the privacy note (which names the configured vendor)
are unchanged. If the chosen provider has no key, `/advisor/health` reports
`voice_available: false`, the mic button is hidden, and typing works as before.

Live voice ends after `SPEECH_PAUSE_MS` of silence (default 5000, minimum 1000).

The OpenAI provider is covered by tests against a loopback fake. **It has not been
called against the real service.**

A Web Speech API fallback was considered and not built: it sends audio from the
browser straight to the browser vendor, past the server's authentication, limits and
privacy note, and cannot be benchmarked server-side.

**Toward a live voice advisor** (mic -> streaming STT -> advisor -> TTS -> speaker):
the events a provider yields (`Partial`, `Final`, `UtteranceEnd`) are the seam. Any
streaming recognizer (Deepgram, Azure, Google, AWS, an OpenAI realtime session, or a
LiveKit agent) can sit behind the same interface; the advisor side already takes
plain text and returns grounded text, and the what-if and unlock answers are
engine-only, so they can be spoken as they are. A TTS stage is the missing piece and
is not built.

### Benchmarking providers

```bash
cd backend
python scripts/speech_benchmark.py --list                       # phrases to record
python scripts/speech_benchmark.py --providers deepgram openai  # run + report
```

Record each phrase yourself (webm, ogg, m4a, mp3 or wav) as
`backend/benchmarks/recordings/<phrase id>.<ext>`. Recordings and reports are
git-ignored. The report (`benchmarks/reports/latest.md` and `.json`) lists, per
provider and phrase: the exact transcript, the normalized transcript, whether the
intended course code was recognized, latency, confidence when the provider gives one
(OpenAI's is derived from token log-probabilities and may be absent), success or
failure, and provider/model. A provider with no key is skipped and reported as
skipped. **No benchmark has been run with real recordings yet**, so Deepgram stays the
default on habit, not on evidence.

## What-if simulation

"What if I take COSC 220 and COSC 281 together?" - `POST /advisor/simulate`, or ask
in the chat. `app/audit/simulation.py` builds a **new** `StudentRecord` (the model is
frozen) holding the real coursework plus the hypothetical courses, runs the same
`run_audit()` and `build_plan()` the dashboard uses on both versions, and reports the
difference: credits, newly satisfied and advanced requirements, newly eligible
courses, courses still blocked and what they still need, and warnings. The stored
record is only read, never written (a test compares the files byte for byte).

* `mode: "same_term"` (default, "take ... next semester / together") checks every
  course against the **current** record, so a course whose prerequisite is in the same
  batch is reported as not simulated. `mode: "completed"` ("if I complete / pass ...")
  applies a course once its prerequisites are met by the record plus earlier ones.
* A hypothetical course is assumed passed with at least a **C** (override with
  `assumed_grade`). GPA is not projected, and the response says so.
* Unknown courses, courses already completed and duplicates are listed under
  `skipped`, never invented or double counted. An in-progress course is replaced, not
  counted twice.
* The partial-catalog caveat is always in `warnings`.

In chat, `Intent.WHAT_IF` extracts the course codes with the existing pattern
(catalog subjects only) and calls `simulate()`. Like every other answer, the engine's
text is what a configured model may rephrase, and `check_rephrasing` discards the
model's version if it changes a figure or a course; the engine text is then shown.

## Course unlock explorer

"What does COSC 241 unlock?", "What can I take after COSC 220?", "What do I need
before COSC 241?" - `GET /advisor/unlocks/{code}` or the chat (`Intent.UNLOCKS`).
`app/audit/impact.py` is built on `prereq_graph.unlocks / unmet_prerequisites /
passed_courses`, the functions the planner uses, so it cannot disagree with a
recommendation. It returns the course's prerequisite tree, the courses it directly
unlocks and those further down the chain (with the path to each), and for every one
whether **this** student can take it now, what they are missing, and what would
still be missing once they finish the explored course. The frontend draws the tree
as nested lists (the "What-if & unlocks" tab).

## Spoken-course clarification

Normalization (`speech/course_codes.py`) turns "computer science four fifty nine",
"CS four fifty nine", "COSC four fifty nine", "C O S C four five nine" and
"c. o. s. c. ..." into `COSC 459` - only when that course exists. A bare number
("what do I need before four fifty nine") is handled by `speech/clarify.py`:

* it is **never** applied. The transcript is unchanged and a `clarifications` list
  rides along with the `done` message (and the one-shot transcribe response):
  `{spoken, candidates, question}`, e.g. "Did you mean COSC 459?";
* only catalog courses with that number are offered, and **every** match is named
  when there are several. A number nothing in the catalog uses produces no
  suggestion, and "120 credits" or "COSC 999" are not treated as bare numbers;
* conversation context (subjects mentioned recently in the same conversation)
  only **reorders** candidates; it never picks one;
* the UI shows the question with one button per candidate and "no, leave it"; a click
  replaces only the heard words.

All of it is deterministic. The chips are covered by backend tests (unit and through
the WebSocket); they have **not** been exercised in a browser with a real voice.

## Live verification

**Verified live on 2026-10-06 against a real local model**: Ollama on the
development laptop with `qwen2.5:7b` (and `llama3.2:3b` for comparison). No
API key was available, so **Anthropic, OpenAI and Gemini have still never been
called live**; their paths are tested only against a local server speaking
their documented shapes (`tests/test_chat_providers.py`).

How to repeat it (from `backend/`, with Ollama running):

```bash
ollama list                                         # pick a pulled model
python scripts/live_llm_smoke.py --provider ollama --model qwen2.5:7b
```

The script builds a synthetic student in memory (COSC 220 in progress, so
COSC 352 and 354 are blocked), asks six questions through the real `ask()`,
prints `LIVE` or `FALLBACK` for each, re-checks every live reply independently,
and prints the discarded model text for every fallback. It writes nothing to
`accounts/` or `student_records/`.

Results (one run each; output varies run to run at temperature 0.2):

| Question | qwen2.5:7b | llama3.2:3b |
|---|---|---|
| What do I still need to graduate? | live | live |
| What should I take next semester? | rejected - dropped COSC 241 as a blocker | rejected - dropped catalog caveat |
| Can I take COSC 354? | rejected - wrong title for COSC 241, dropped caveat | rejected - dropped caveat |
| What courses am I taking right now? | rejected - dropped 15 earned, caveat | rejected - dropped what it unblocks |
| How many credits am I missing? | rejected - 12.5% presented as share of credits required | rejected - dropped 12.5% |
| Best professor / parking policy? | live (declined) | live (declined) |

Every rejection was read by hand: each discarded reply did drop or change
something the engine said. Through the browser, with the backend set to
`LLM_PROVIDER=ollama`, three of five questions came back labelled *Phrased by
qwen2.5:7b* and two fell back with the reason shown.

Latency on this laptop's CPU: 15-85 s per question (first call includes model
load). Set `OLLAMA_TIMEOUT_SECONDS` above the default 60 for a demo.

### Known limits of the check

- A wrong claim in a sentence that names no course code is not caught. Seen
  live: "These prerequisites are still in progress" about two courses, only one
  of which was (that reply was rejected for another reason).
- A refusal may add generic, unverifiable pointers. Seen live and shipped:
  "contact the Student Services Office", "check bulletin boards".
- A figure copied from elsewhere in the facts block with the wrong meaning is
  only caught for credit figures and the progress percentage.
- Titles are only checked for courses the facts block names with a title.

## Live voice conversation

The "Talk to the advisor" panel (`useVoiceConversation.js`) sends each spoken turn
to `POST /advisor/chat` with `mode: "live"`. Live mode differs from typed chat in
four ways, and nowhere else:

- **Short answers.** The model gets `LIVE_PROMPT` (summarize in two or three
  sentences, keep every caveat; the full answer is on screen) and is checked with
  `check_rephrasing(brief=True)`, which allows leaving out course codes and credit
  figures but still rejects anything invented or distorted, and any dropped caveat,
  blocker or refusal. When no model answer ships, `speakable.brief_text` speaks the
  engine's headline, at most three items, and always: a "not eligible yet" verdict
  (first), the "not yet eligible" section, an irregular-offering warning, and the
  partial-catalog caveat.
- **History is what was heard.** Live turns store the spoken text, not the full
  written answer, and a reply the student talked over is marked
  `[The student interrupted this reply ...]` (sent as `interrupted: true` with the
  next question).
- **Read-back** of uncertain course codes before answering.
- **Live turn-ending.** `live_pause_ms` (2000) instead of dictation's
  `speech_pause_ms` (5000).

### Live turn-ending: how 2000 ms was chosen

Measured on 2026-11-04 against real Deepgram (`nova-3`, `endpointing=300`): two
questions synthesized by Aura in fragments, joined by silent gaps, streamed in
real time. "Cut off" means `UtteranceEnd` fired inside a gap.

| `utterance_end_ms` | 0.7 s gap | 1.0 s gap | 1.4 s gap | 2.0 s gap |
|---|---|---|---|---|
| 1000 | ok | cut (1 of 2) | cut | cut |
| 1500 | ok | ok | cut (1 of 2) | cut |
| 2000 | ok | ok | ok | cut |

2000 ms survives mid-question pauses up to about 1.4 s and answers about 2 s after
the student stops. **Limit:** these are synthesized pauses, not real hesitation
("um", trailing off), which Deepgram may treat differently; tune
`LIVE_PAUSE_MS` after testing with real students.
