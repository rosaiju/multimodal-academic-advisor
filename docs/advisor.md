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
         |                 keyword intent routing, prose assembled from facts
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

**Not yet verified:** a human voice through the browser to real Deepgram, and
Safari or Firefox.

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
