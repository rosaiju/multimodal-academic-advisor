import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { askAdvisor, getAdvisorHealth, resetConversation } from '../api'
import { useSpeaker } from './useSpeaker'
import { Alert, Button, Card, Spinner } from './ui'
import { useLiveTranscription } from './useLiveTranscription'
import { typingMs, useVoiceConversation } from './useVoiceConversation'

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
 *
 * Typed questions, dictation (mic) and the voice conversation (waveform) all
 * share one composer and one thread, so a spoken turn reads like a typed one.
 */

const CONVERSATION_ID = 'default'

/** Below this Deepgram confidence the student is asked to check the text. */
const LOW_CONFIDENCE = 0.6

const SPEECH_VENDORS = { deepgram: 'Deepgram', openai: 'OpenAI' }

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
  const speaker = useSpeaker()
  const [speakingIndex, setSpeakingIndex] = useState(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const scroller = useRef(null)
  const input = useRef(null)
  const [voiceNote, setVoiceNote] = useState(null)
  //: "Did you mean COSC 459?" offers for bare numbers heard. Never applied by
  //: themselves: the student clicks one, or ignores them.
  const [clarifications, setClarifications] = useState([])

  const handleVoiceDone = useCallback(({ text, confidence, clarifications: heard = [] }) => {
    const spoken = text.trim()
    if (!spoken) {
      setVoiceNote(VOICE_MESSAGES.empty)
      return
    }
    // The input is read-only while listening, so `current` is what was typed first.
    setDraft((current) => mergeDraft(current, spoken))
    setClarifications(heard)
    setVoiceNote(confidence < LOW_CONFIDENCE ? VOICE_MESSAGES.lowConfidence : null)
    input.current?.focus()
  }, [])

  const voice = useLiveTranscription({ onDone: handleVoiceDone })
  //: Voice conversation mode: its turns land in this same thread.
  const live = useVoiceConversation({ conversationId: CONVERSATION_ID, setTurns: setMessages })
  const liveOn = live.state !== 'idle'
  const voiceOn = Boolean(health?.voice_available)
  const canTalk = voiceOn && hasRecord && live.supported
  const voiceBusy = voice.listening || liveOn
  const waiting = busy || live.state === 'thinking'

  /** Swap the heard words for the code the student chose. Their words, their choice. */
  function applyClarification(offer, candidate) {
    setDraft((current) => current.replace(offer.spoken, candidate))
    setClarifications((current) => current.filter((c) => c !== offer))
    input.current?.focus()
  }

  function toggleListening() {
    setVoiceNote(null)
    setClarifications([])
    if (voice.listening) {
      voice.stop()
    } else {
      voice.start()
    }
  }

  function startLive() {
    speaker.stop()
    setVoiceNote(null)
    setClarifications([])
    live.start()
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
  }, [messages, waiting, live.liveText])

  async function send(text) {
    const question = (text ?? draft).trim()
    if (!question || busy) return
    setError(null)
    setDraft('')
    setVoiceNote(null)
    setClarifications([])
    setMessages((prev) => [...prev, { role: 'user', text: question }])
    setBusy(true)
    try {
      const reply = await askAdvisor({ message: question, conversationId: CONVERSATION_ID })
      setMessages((prev) => [...prev, { role: 'advisor', reply, revealMs: typingMs(reply.answer) }])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function clear() {
    speaker.stop()
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
            <Button variant="ghost" onClick={clear} disabled={busy || liveOn}>
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
          className="nice-scroll max-h-[max(28rem,calc(100vh-22rem))] space-y-5 overflow-y-auto pr-2"
          aria-live="polite"
          aria-busy={waiting}
        >
          {messages.length === 0 && !waiting && (
            <div className="py-8 text-center">
              <AdvisorAvatar className="mx-auto size-12" />
              <p className="mt-3 font-display text-lg font-bold text-brand-950">
                What would you like to know?
              </p>
              {suggestions.length > 0 && (
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  {suggestions.map((question) => (
                    <button
                      key={question}
                      type="button"
                      onClick={() => send(question)}
                      disabled={busy || voiceBusy}
                      className="rounded-full border border-brand-100 bg-brand-50/60 px-3.5 py-1.5 text-xs font-medium text-brand-800 transition hover:-translate-y-0.5 hover:border-brand-300 hover:bg-white hover:shadow-sm disabled:opacity-50"
                    >
                      {question}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {messages.map((message, i) =>
            message.role === 'user' ? (
              <UserBubble key={i} text={message.text} />
            ) : (
              <AdvisorTurn
                key={message.id ?? i}
                reply={message.reply}
                pending={message.pending}
                revealMs={message.revealMs}
                speaking={speaker.speaking && speakingIndex === i}
                onSpeak={
                  liveOn
                    ? undefined
                    : () => {
                        if (speaker.speaking && speakingIndex === i) return speaker.stop()
                        setSpeakingIndex(i)
                        speaker.speak(message.reply.answer, message.reply.links ?? [])
                      }
                }
              />
            ),
          )}

          {live.state === 'listening' && live.liveText && <UserBubble text={live.liveText} draft />}

          {waiting && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-tl-sm bg-white px-4 py-3 shadow-sm ring-1 ring-slate-200/80">
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

        <form
          className={`mt-5 flex items-center gap-1.5 rounded-2xl border bg-white py-1.5 pl-4 pr-1.5 shadow-sm transition ${
            liveOn
              ? 'border-brand-500 ring-4 ring-brand-100'
              : 'border-slate-300 focus-within:border-brand-500 focus-within:ring-4 focus-within:ring-brand-100'
          }`}
          onSubmit={(event) => {
            event.preventDefault()
            send()
          }}
        >
          {liveOn ? (
            <LiveStatus state={live.state} />
          ) : (
            <>
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
                placeholder={voice.listening ? 'Listening…' : 'Ask about your degree progress'}
                disabled={busy}
                maxLength={2000}
                className="min-w-0 flex-1 bg-transparent py-1.5 text-sm outline-none placeholder:text-slate-400 disabled:text-slate-400"
              />
            </>
          )}

          {voiceOn && !liveOn && (
            <IconButton
              label={voice.listening ? 'Stop listening' : 'Ask by voice'}
              pressed={voice.listening}
              disabled={busy || !voice.supported}
              onClick={toggleListening}
              className={
                voice.listening
                  ? 'animate-pulse bg-rose-600 text-white hover:bg-rose-700'
                  : 'text-slate-500 hover:bg-brand-50 hover:text-brand-700'
              }
            >
              <MicIcon />
            </IconButton>
          )}

          {liveOn ? (
            <IconButton
              label="End voice conversation"
              onClick={live.end}
              className="bg-gradient-to-b from-brand-600 to-brand-700 text-white shadow-sm hover:from-brand-500 hover:to-brand-600"
            >
              <CloseIcon />
            </IconButton>
          ) : draft.trim() || voice.listening || !canTalk ? (
            <IconButton
              type="submit"
              label="Ask"
              disabled={busy || voiceBusy || !draft.trim()}
              className="bg-gradient-to-b from-brand-600 to-brand-700 text-white shadow-sm hover:from-brand-500 hover:to-brand-600 disabled:from-slate-200 disabled:to-slate-200 disabled:text-slate-400 disabled:shadow-none"
            >
              <ArrowUpIcon />
            </IconButton>
          ) : (
            <IconButton
              label="Start voice conversation"
              disabled={busy}
              onClick={startLive}
              className="bg-gradient-to-b from-brand-600 to-brand-700 text-white shadow-sm hover:from-brand-500 hover:to-brand-600 disabled:from-slate-200 disabled:to-slate-200 disabled:text-slate-400 disabled:shadow-none"
            >
              <WaveformIcon />
            </IconButton>
          )}
        </form>

        {voiceOn && voice.listening && (
          <p className="mt-2 px-1 text-xs font-medium text-rose-700" role="status" aria-live="polite">
            Listening… it stops when you pause, or tap the mic to stop.
          </p>
        )}
        {voiceOn && !voice.listening && (voiceNote || voice.error || live.error) && (
          <p className="mt-2 px-1 text-xs text-slate-500" role="status">
            {voiceNote ?? voice.error ?? live.error}
          </p>
        )}
        {clarifications.length > 0 && !voice.listening && (
          <div className="mt-2 space-y-1.5 px-1" role="group" aria-label="Did you mean a course?">
            {clarifications.map((offer) => (
              <div key={offer.spoken} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-slate-600">
                  &ldquo;{offer.spoken}&rdquo; — {offer.question}
                </span>
                {offer.candidates.map((candidate) => (
                  <button
                    key={candidate}
                    type="button"
                    onClick={() => applyClarification(offer, candidate)}
                    className="rounded-full border border-slate-300 px-2.5 py-1 font-medium text-slate-800 hover:bg-slate-50"
                  >
                    {candidate}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() =>
                    setClarifications((current) => current.filter((c) => c !== offer))
                  }
                  className="text-slate-400 underline hover:text-slate-600"
                >
                  no, leave it
                </button>
              </div>
            ))}
          </div>
        )}
        {voiceOn && (
          <p className="mt-2 px-1 text-xs text-slate-400">
            {liveOn
              ? "Talk any time to interrupt. Course codes it isn't sure about are read back to you first."
              : `Voice is sent to ${SPEECH_VENDORS[health?.speech_provider] ?? 'a speech service'} to transcribe and isn't saved.`}
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

/** The advisor's avatar: the product mark, small. Decorative. */
function AdvisorAvatar({ className = '' }) {
  return (
    <span
      aria-hidden="true"
      className={`shrink-0 place-items-center rounded-full bg-gradient-to-br from-brand-700 to-brand-950 text-accent-300 shadow-sm ${
        className.includes('hidden') ? '' : 'grid'
      } ${className}`}
    >
      <svg viewBox="0 0 24 24" className="size-1/2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 9.5 12 5l10 4.5L12 14 2 9.5Z" />
        <path d="M6 11.6V16c0 1.2 2.7 2.8 6 2.8s6-1.6 6-2.8v-4.4" />
      </svg>
    </span>
  )
}

/** A student turn. `draft` is the words still being heard in a voice conversation. */
function UserBubble({ text, draft = false }) {
  return (
    <div className="flex justify-end">
      <p
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm px-4 py-2.5 text-sm ${
          draft
            ? 'bg-brand-100 text-brand-800'
            : 'bg-gradient-to-br from-brand-600 to-brand-800 text-white shadow-sm shadow-brand-900/20'
        }`}
      >
        {text}
      </p>
    </div>
  )
}

/**
 * Text that types itself out over `ms`, word by word. No `ms` (or 0) shows it
 * all at once - old turns, an interrupted reply, or a reply whose audio failed.
 */
function StreamedText({ text, ms }) {
  const words = useMemo(() => text.split(/(\s+)/), [text])
  const [shown, setShown] = useState(0)
  useEffect(() => {
    if (!ms) return undefined
    const started = Date.now()
    const timer = setInterval(() => {
      const count = Math.min(words.length, Math.ceil((words.length * (Date.now() - started)) / ms))
      setShown(count)
      if (count >= words.length) clearInterval(timer)
    }, 40)
    return () => clearInterval(timer)
  }, [ms, words])
  return <AnswerMarkdown text={words.slice(0, ms ? shown : words.length).join('')} />
}

/**
 * Engine answers lean on single line breaks (an "Options:" or "Warning:" line
 * under a bullet, a sentence after a list), which markdown would run together.
 * Keep every break, and end a list before an unindented line that follows it.
 */
function toMarkdown(text) {
  const BULLET = /^\s*(?:[-*+]|\d+[.)])\s/
  const out = []
  let inList = false
  for (const line of text.split('\n')) {
    const indented = /^\s+\S/.test(line)
    if (inList && line.trim() && !BULLET.test(line) && !indented) out.push('')
    if (BULLET.test(line)) inList = true
    else if (!indented) inList = false
    out.push(line)
  }
  return out.join('\n').replace(/([^\n])\n(?=[^\n])/g, '$1  \n')
}

const MARKDOWN_PARTS = {
  p: (props) => <p className="my-2 first:mt-0 last:mb-0" {...props} />,
  ul: (props) => <ul className="my-2 list-disc space-y-1 pl-5" {...props} />,
  ol: (props) => <ol className="my-2 list-decimal space-y-1 pl-5" {...props} />,
  strong: (props) => <strong className="font-semibold text-slate-900" {...props} />,
  h1: (props) => <p className="my-2 font-semibold text-slate-900" {...props} />,
  h2: (props) => <p className="my-2 font-semibold text-slate-900" {...props} />,
  h3: (props) => <p className="my-2 font-semibold text-slate-900" {...props} />,
  code: (props) => <code className="rounded bg-white px-1 py-0.5 text-[0.85em]" {...props} />,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-indigo-700 underline hover:text-indigo-900">
      {children}
    </a>
  ),
}

/** Markdown with no raw HTML: a model's text can format, never inject. */
function AnswerMarkdown({ text }) {
  return (
    <ReactMarkdown components={MARKDOWN_PARTS} skipHtml>
      {toMarkdown(text)}
    </ReactMarkdown>
  )
}

const PROVIDER_NAMES = {
  gemini: 'Gemini',
  anthropic: 'Claude',
  openai: 'OpenAI',
  ollama: 'the local model',
  openrouter: 'OpenRouter',
}

const TONES = {
  model: 'bg-violet-50 text-violet-700 ring-violet-600/20',
  engine: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  fallback: 'bg-amber-50 text-amber-800 ring-amber-600/30',
}

/**
 * Who wrote the text the student is reading - from the API's own fields, never
 * guessed: `source` says whether a model's answer was used, `fallback` why not.
 */
export function provenance(reply) {
  const who = PROVIDER_NAMES[reply.provider] ?? reply.model ?? 'a language model'
  if (reply.source === 'engine+llm') {
    return {
      kind: 'model',
      label: `Written by ${who} · checked by the engine`,
      title: `${reply.model ?? who} wrote this from the degree engine's verified facts. Every number, course code and eligibility statement was checked against the engine before it was shown.`,
    }
  }
  if (reply.fallback === 'check_failed') {
    return {
      kind: 'fallback',
      label: `Fallback · ${who}'s answer failed the fact check`,
      title: `${who} wrote an answer that did not match the degree engine, so it was discarded and you are reading the engine's own answer.`,
    }
  }
  if (reply.fallback === 'model_unavailable' || reply.fallback === 'model_error') {
    return {
      kind: 'fallback',
      label: `Fallback · ${who} unavailable`,
      title: `${who} could not answer, so you are reading the degree engine's own answer.`,
    }
  }
  return {
    kind: 'engine',
    label: 'Computed by the engine',
    title: 'Written by the deterministic degree engine; no language model wrote this text.',
  }
}

/** One advisor answer, with its provenance label, links, and any notice. */
export function AdvisorTurn({ reply, onSpeak, speaking = false, pending = false, revealMs }) {
  const origin = provenance(reply)
  if (pending) {
    // Waiting for the voice to start: the words appear as they are spoken.
    return (
      <div className="flex justify-start">
        <div className="rounded-2xl rounded-tl-sm bg-white px-4 py-3 shadow-sm ring-1 ring-slate-200/80">
          <Spinner label="Getting ready to speak…" />
        </div>
      </div>
    )
  }
  return (
    <div className="flex justify-start gap-2.5">
      <AdvisorAvatar className="mt-1 hidden size-8 sm:grid" />
      <div className="min-w-0 max-w-[92%] space-y-2">
        <div className="rounded-2xl rounded-tl-sm bg-white px-4 py-3 shadow-sm ring-1 ring-slate-200/80">
          <div className="text-sm leading-relaxed text-slate-800">
            <StreamedText text={reply.answer} ms={revealMs} />
          </div>
        </div>

        {reply.details && (
          // Voice: the reply above is the spoken summary; this is what it points to.
          <details open className="rounded-xl border border-slate-200 px-4 py-2.5">
            <summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-slate-700">
              Full answer
            </summary>
            <div className="mt-2 text-sm leading-relaxed text-slate-700">
              <AnswerMarkdown text={reply.details} />
            </div>
          </details>
        )}

        <div className="flex flex-wrap items-center gap-2 px-1">
          <span
            title={origin.title}
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[origin.kind]}`}
          >
            {origin.label}
          </span>

          {reply.understood_by && (
            <span
              className="text-xs text-slate-400"
              title={
                reply.understood_by === 'model'
                  ? 'A language model read your question and the conversation to choose which engine answer to give. The answer itself comes from the engine.'
                  : 'Your question was matched to an engine answer by keywords.'
              }
            >
              {reply.understood_by === 'model' ? 'Question read by the model' : 'Matched by keywords'}
            </span>
          )}

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

          {onSpeak && (
            <IconButton
              label={speaking ? 'Stop reading' : 'Read aloud'}
              pressed={speaking}
              onClick={onSpeak}
              className={`ml-auto h-7 w-7 ${
                speaking ? 'bg-slate-900 text-white' : 'text-slate-400 hover:bg-slate-100 hover:text-slate-700'
              }`}
            >
              {speaking ? <StopIcon /> : <SpeakerIcon />}
            </IconButton>
          )}
        </div>

        {reply.links?.length > 0 && (
          <ul className="space-y-1 px-1 text-sm">
            {reply.links.map((link) => (
              <li key={link.url}>
                <a
                  href={link.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-indigo-700 underline hover:text-indigo-900"
                >
                  {link.name}
                </a>
              </li>
            ))}
          </ul>
        )}

        {reply.notice && <p className="px-1 text-xs italic text-slate-400">{reply.notice}</p>}
      </div>
    </div>
  )
}

const LIVE_LABELS = {
  listening: 'Listening…',
  thinking: 'Checking your record…',
  speaking: 'Speaking — talk to interrupt',
}

/** Fills the composer during a voice conversation: what the advisor is doing now. */
function LiveStatus({ state }) {
  const moving = state === 'listening' || state === 'speaking'
  return (
    <div className="flex min-w-0 flex-1 items-center gap-3 py-1.5" role="status" aria-live="polite">
      <span className="flex h-5 items-center gap-0.5" aria-hidden="true">
        {[0, 1, 2, 3, 4].map((bar) => (
          <span
            key={bar}
            className={`w-1 rounded-full ${state === 'listening' ? 'bg-rose-500' : 'bg-brand-600'} ${
              moving ? 'animate-pulse' : 'opacity-40'
            }`}
            style={{ height: `${[8, 14, 20, 14, 8][bar]}px`, animationDelay: `${bar * 120}ms` }}
          />
        ))}
      </span>
      <span className="truncate text-sm text-slate-600">{LIVE_LABELS[state]}</span>
    </div>
  )
}

/** A round icon button; the label is its accessible name and tooltip. */
function IconButton({ label, pressed, className = '', type = 'button', children, ...props }) {
  return (
    <button
      type={type}
      aria-label={label}
      aria-pressed={pressed}
      title={label}
      className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition disabled:cursor-not-allowed ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}

const ICON = {
  viewBox: '0 0 24 24',
  className: 'h-4 w-4',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
}

function MicIcon() {
  return (
    <svg {...ICON}>
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  )
}

function WaveformIcon() {
  return (
    <svg {...ICON}>
      <path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4" />
    </svg>
  )
}

function ArrowUpIcon() {
  return (
    <svg {...ICON}>
      <path d="M12 19V5M5 12l7-7 7 7" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg {...ICON}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  )
}

function SpeakerIcon() {
  return (
    <svg {...ICON}>
      <path d="M11 5 6 9H3v6h3l5 4V5z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg {...ICON} fill="currentColor" stroke="none">
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </svg>
  )
}
