import { useCallback, useEffect, useRef, useState } from 'react'
import { askAdvisor, getAdvisorHealth, resetConversation } from '../api'
import { Alert, Button, Card, Spinner } from './ui'
import { useLiveTranscription } from './useLiveTranscription'

/**
 * The conversational advisor.
 *
 * Two things about this panel are deliberate and worth not "cleaning up":
 *
 * 1. Every answer is labelled with where it came from. `source: "engine"` means
 *    the text was assembled by the deterministic degree engine; `"engine+llm"`
 *    means a language model rephrased it. A student should never have to guess
 *    which one they are reading, because the trustworthiness differs.
 *
 * 2. A missing model is NOT an error state. The backend answers from the engine
 *    either way, so an unavailable provider shows as a quiet note, not a red
 *    banner - the answers are still correct and still worth reading.
 */

const CONVERSATION_ID = 'default'

/** Below this Deepgram confidence the student is asked to check the text. */
const LOW_CONFIDENCE = 0.6

const VOICE_MESSAGES = {
  empty: "Didn't catch that — try again.",
  lowConfidence: 'Check this — I may have misheard.',
}

/** Speech is added after anything already typed, never in place of it. */
function mergeDraft(current, spoken) {
  const typed = current.trim()
  return typed ? `${typed} ${spoken}` : spoken
}

export default function AdvisorChat({ hasRecord, onGoToUpload }) {
  const [health, setHealth] = useState(null)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const scroller = useRef(null)
  const input = useRef(null)
  const [voiceNote, setVoiceNote] = useState(null)

  const handleVoiceDone = useCallback(({ text, confidence }) => {
    const spoken = text.trim()
    if (!spoken) {
      setVoiceNote(VOICE_MESSAGES.empty)
      return
    }
    // The input is read-only while listening, so `current` is what was typed first.
    setDraft((current) => mergeDraft(current, spoken))
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
      voice.start()
    }
  }

  useEffect(() => {
    getAdvisorHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  // Keep the newest turn in view as the conversation grows.
  useEffect(() => {
    const node = scroller.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages, busy])

  async function send(text) {
    const question = (text ?? draft).trim()
    if (!question || busy) return
    setError(null)
    setDraft('')
    setVoiceNote(null)
    setMessages((prev) => [...prev, { role: 'user', text: question }])
    setBusy(true)
    try {
      const reply = await askAdvisor({ message: question, conversationId: CONVERSATION_ID })
      setMessages((prev) => [...prev, { role: 'advisor', ...reply }])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function clear() {
    setMessages([])
    setError(null)
    try {
      await resetConversation(CONVERSATION_ID)
    } catch {
      /* the panel is already clear; the server copy is only history */
    }
  }

  const suggestions = health?.suggested_questions ?? []

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Card
        title="Ask the advisor"
        subtitle="Questions about your own degree progress, answered from your confirmed record."
        action={
          messages.length > 0 && (
            <Button variant="ghost" onClick={clear} disabled={busy}>
              Clear
            </Button>
          )
        }
      >
        {health?.degraded && (
          <div className="mb-4">
            <Alert tone="info" title="Running on the degree engine">
              <p>
                No language model is configured ({health.reason}), so answers are written
                by the deterministic engine rather than phrased by a model.{' '}
                <strong>The numbers are identical either way</strong> — they are computed
                from your record and the catalog, never generated.
              </p>
            </Alert>
          </div>
        )}

        {!hasRecord && (
          <div className="mb-4">
            <Alert tone="warning" title="No confirmed coursework yet">
              <p>
                The advisor can only speak to coursework you have confirmed. Upload a
                transcript first and it will have something to work from.
              </p>
              {onGoToUpload && (
                <Button variant="secondary" className="mt-3" onClick={onGoToUpload}>
                  Upload a transcript
                </Button>
              )}
            </Alert>
          </div>
        )}

        <div
          ref={scroller}
          className="max-h-[26rem] space-y-4 overflow-y-auto"
          aria-live="polite"
          aria-busy={busy}
        >
          {messages.length === 0 && !busy && (
            <p className="py-6 text-center text-sm text-slate-500">
              Ask a question below, or pick one of the suggestions.
            </p>
          )}

          {messages.map((message, i) =>
            message.role === 'user' ? (
              <div key={i} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2.5 text-sm text-white">
                  {message.text}
                </p>
              </div>
            ) : (
              <AdvisorTurn key={i} reply={message} />
            ),
          )}

          {busy && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm bg-slate-100 px-4 py-3">
                <Spinner label="Checking your record…" />
              </div>
            </div>
          )}
        </div>

        {error && (
          <div className="mt-4">
            <Alert tone="error" title="Could not reach the advisor">
              <p>{error}</p>
            </Alert>
          </div>
        )}

        {suggestions.length > 0 && messages.length === 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {suggestions.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => send(question)}
                disabled={busy || voiceBusy}
                className="rounded-full border border-slate-300 px-3 py-1.5 text-xs text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
              >
                {question}
              </button>
            ))}
          </div>
        )}

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
            value={voice.listening ? mergeDraft(draft, voice.liveText) : draft}
            readOnly={voice.listening}
            onChange={(event) => {
              setDraft(event.target.value)
              setVoiceNote(null)
            }}
            placeholder="What should I take next semester?"
            disabled={busy}
            maxLength={2000}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900 disabled:bg-slate-50"
          />
          {voiceOn && (
            <MicButton
              listening={voice.listening}
              disabled={busy || !voice.supported}
              onClick={toggleListening}
            />
          )}
          <Button type="submit" disabled={busy || voiceBusy || !draft.trim()}>
            Ask
          </Button>
        </form>

        {voiceOn && voice.listening && (
          <p className="mt-2 text-xs font-medium text-rose-700" role="status" aria-live="polite">
            Listening… it stops when you pause, or click the mic to stop.
          </p>
        )}
        {voiceOn && !voice.listening && (voiceNote || voice.error || !voice.supported) && (
          <p className="mt-2 text-xs text-slate-500" role="status">
            {voiceNote ??
              voice.error ??
              'This browser cannot record audio. You can still type your question.'}
          </p>
        )}
        {voiceOn && (
          <p className="mt-2 text-xs text-slate-400">
            Voice input sends your audio to Deepgram&apos;s speech service to turn it into
            text. It is not saved, and nothing goes to the advisor until you press Ask.
          </p>
        )}
      </Card>

      <p className="text-center text-xs text-slate-400">
        The advisor cannot clear you to graduate. Degree requirements are computed from
        the encoded catalog, which is still partial — confirm with your advisor.
      </p>
    </div>
  )
}

/** One advisor answer, with its provenance label and any notice. */
function AdvisorTurn({ reply }) {
  const fromModel = reply.source === 'engine+llm'
  return (
    <div className="flex justify-start">
      <div className="max-w-[92%] space-y-2">
        <div className="rounded-2xl rounded-bl-sm bg-slate-100 px-4 py-3">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
            {reply.answer}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-1">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
              fromModel
                ? 'bg-violet-50 text-violet-700 ring-violet-600/20'
                : 'bg-emerald-50 text-emerald-700 ring-emerald-600/20'
            }`}
          >
            {fromModel ? `Phrased by ${reply.model ?? 'a model'}` : 'Computed by the engine'}
          </span>

          {reply.grounded === false && (
            <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/30">
              Outside the catalog
            </span>
          )}

          {reply.citations?.length > 0 && (
            <span className="text-xs text-slate-500">
              Based on: {reply.citations.slice(0, 6).join(', ')}
            </span>
          )}
        </div>

        {reply.notice && <p className="px-1 text-xs italic text-slate-400">{reply.notice}</p>}
      </div>
    </div>
  )
}

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
