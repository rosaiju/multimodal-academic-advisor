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
  4. check it     ......  app/advisor/chat.py::_invented_codes
         |                 does the reply mention a course code that is not in
         |                 the catalog and not on the record? Then DISCARD the
         |                 model's answer and ship step 2's.
         v
      reply + a label saying which of the two you are reading
```

**Step 1** is why the chat and the dashboard can never disagree. They are not two
implementations of "what does this student still need" - they are one function
called twice.

**Step 2** is why the demo works with no API key, no credits and no internet.
Both student credit applications were still pending when this was built, and a
capstone that cannot be demonstrated because of someone else's quota is not
finished. Every question in DEMO_GUIDE.md is answered correctly with no provider
configured at all.

**Step 4** is the part that would be easy to skip. A model that says "take
COSC 499" about a course that does not exist has produced exactly the failure
this architecture exists to prevent, and annotating it with a caveat is not
enough - a student reads the sentence, not the footnote. So the answer is thrown
away and the deterministic one is shown instead, with a note saying why.

## What the model is allowed to see

`render_facts()` builds the entire permitted universe of academic fact: credits
earned, required and remaining (pre-computed, so the model never does
arithmetic), every requirement block and its status, confirmed coursework,
recommendations with the engine's own reasons, blocked courses with their
missing prerequisites, and the catalog coverage caveat.

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
- an empty completion, or a non-JSON body
- the model invented a course code

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

## What is NOT tested

The Gemini and Ollama HTTP paths are tested against a local server that speaks
their documented shapes (`tests/test_chat_providers.py`): request format, key
placement, role mapping, and every failure branch. That is not the same as
proving Google or a real Ollama daemon accepts them. **No live provider has ever
been called.** This machine has no key and no Ollama installed. The first person
with credentials should run through DEMO_GUIDE.md's five questions and confirm
`source` comes back as `engine+llm`.
