import { useEffect, useState } from 'react'
import { Alert, Spinner } from './components/ui'
import UploadStep from './components/UploadStep'
import ReviewStep from './components/ReviewStep'
import Dashboard from './components/Dashboard'
import AdvisorChat from './components/AdvisorChat'
import Explorer from './components/Explorer'
import SemesterPlanner from './components/SemesterPlanner'
import CourseCompare from './components/CourseCompare'
import Deadlines from './components/Deadlines'
import SignIn from './components/SignIn'
import { deleteRecord, getHealth, getPrograms, getRecord, logout } from './api'

/**
 * COSC 490 Multimodal Academic Advisor.
 *
 * Two steps to get coursework in, matching the backend's own shape: upload
 * (stores nothing), then review and confirm (the only way data becomes trusted).
 * After that, a set of tools over the confirmed record: progress, the advisor,
 * the semester plan, what-if, course comparison and deadlines.
 *
 * Nothing here knows a student id until the server issues one. `user` is null
 * until sign-in, and every student-scoped call uses `user.student_id`, so there
 * is no id for the frontend to guess, type, or get wrong.
 */

/*
 * Two groups, because they are two different things. Getting coursework in is a
 * one-time sequence (upload, then review); everything after that is a set of
 * tools over the same confirmed record, used in any order. Numbering all eight as
 * one long sequence implied a student had to walk through every screen in turn.
 */
const SETUP_STEPS = [
  { id: 'upload', label: 'Upload transcript' },
  { id: 'review', label: 'Review & confirm' },
]

const TOOLS = [
  { id: 'dashboard', label: 'Progress' },
  { id: 'advisor', label: 'Ask the advisor' },
  { id: 'schedule', label: 'Semester plan' },
  { id: 'explore', label: 'What-if & unlocks' },
  { id: 'compare', label: 'Compare courses' },
  { id: 'deadlines', label: 'Deadlines' },
]

export default function App() {
  const [step, setStep] = useState('upload')
  const [health, setHealth] = useState(null)
  const [programs, setPrograms] = useState([])
  const [programId, setProgramId] = useState('')
  const [user, setUser] = useState(null)
  const [extraction, setExtraction] = useState(null)
  //: Whether this account has confirmed coursework. Drives both the step chips
  //: and the advisor's "upload something first" notice, so the two cannot
  //: disagree about whether there is a record.
  const [hasRecord, setHasRecord] = useState(false)
  const [bootError, setBootError] = useState(null)
  const [booting, setBooting] = useState(true)

  useEffect(() => {
    Promise.all([getHealth(), getPrograms()])
      .then(([h, p]) => {
        setHealth(h)
        setPrograms(p)
        // Prefer the real Morgan catalog over the demo program.
        const morgan = p.find((x) => x.program_id.startsWith('morgan')) ?? p[0]
        if (morgan) setProgramId(morgan.program_id)
      })
      .catch((err) => setBootError(err.message))
      .finally(() => setBooting(false))
  }, [])

  /** Signed in: find out whether they already have coursework on record. */
  function afterSignIn(account) {
    setUser(account)
    getRecord(account.student_id)
      .then(() => {
        setHasRecord(true)
        setStep('dashboard')
      })
      .catch(() => {
        setHasRecord(false)
        setStep('upload')
      })
  }

  function signOut() {
    logout()
    setUser(null)
    setExtraction(null)
    setHasRecord(false)
    setStep('upload')
  }

  async function reset() {
    if (!user) return
    try {
      await deleteRecord(user.student_id)
    } catch {
      /* nothing stored yet - fine */
    }
    setExtraction(null)
    setHasRecord(false)
    setStep('upload')
  }

  /**
   * Which steps the chips may jump to.
   *
   * Review needs an extraction to review, and the dashboard and advisor need a
   * record to talk about. Letting a chip navigate to a step with nothing behind
   * it produces an empty screen the student has to back out of.
   */
  function canVisit(id) {
    if (id === 'upload') return true
    if (id === 'review') return Boolean(extraction)
    return hasRecord
  }

  return (
    <div className="min-h-screen text-slate-900">
      <header className="z-30 sm:sticky sm:top-0">
        <div className="bg-gradient-to-r from-brand-950 via-brand-900 to-brand-800 text-white shadow-lg shadow-brand-950/20">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <div className="flex items-center gap-3">
              <BrandMark />
              <div>
                <h1 className="text-[1.05rem] font-extrabold leading-tight tracking-tight">
                  Academic Advisor
                </h1>
                <p className="text-xs text-brand-200">
                  Morgan State University · Computer Science · COSC 490
                </p>
              </div>
            </div>
            {user && (
              <div className="flex items-center gap-3 text-xs">
                <span
                  aria-hidden="true"
                  className="grid size-8 place-items-center rounded-full bg-gradient-to-br from-accent-300 to-accent-500 font-bold text-brand-950 ring-2 ring-white/20"
                >
                  {initials(user.name || user.email)}
                </span>
                <span className="max-w-40 truncate text-brand-100" title={user.email}>
                  {user.name || user.email}
                </span>
                <button
                  type="button"
                  onClick={signOut}
                  className="rounded-lg px-2.5 py-1.5 font-semibold text-white ring-1 ring-inset ring-white/25 transition hover:bg-white/10"
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>

        {/*
          One scrolling row rather than wrapping chips: eight wrapped chips took
          three lines on a phone and pushed the page content below the fold. The
          row scrolls sideways inside itself at 375px; the page never does.
        */}
        {user && (
          <div className="border-b border-slate-200/80 bg-white/85 backdrop-blur-md">
          <nav
            aria-label="Main"
            className="mx-auto flex max-w-6xl items-center gap-1 overflow-x-auto px-4 py-2 text-sm [scrollbar-width:thin] sm:px-6"
          >
            <NavGroup label="Add coursework">
              {SETUP_STEPS.map((s, i) => (
                <NavItem
                  key={s.id}
                  label={`${i + 1}. ${s.label}`}
                  active={step === s.id}
                  enabled={canVisit(s.id)}
                  hint={s.id === 'review' ? 'Upload a transcript first' : undefined}
                  onClick={() => setStep(s.id)}
                />
              ))}
            </NavGroup>
            <span aria-hidden="true" className="mx-2 h-5 w-px shrink-0 bg-slate-300/70" />
            <NavGroup label="Your degree">
              {TOOLS.map((s) => (
                <NavItem
                  key={s.id}
                  label={s.label}
                  active={step === s.id}
                  enabled={canVisit(s.id)}
                  hint="Confirm some coursework first"
                  onClick={() => setStep(s.id)}
                />
              ))}
            </NavGroup>
          </nav>
          </div>
        )}
      </header>

      <main className="px-4 py-6 sm:px-6 sm:py-10">
        {booting && (
          <div className="flex justify-center py-16">
            <Spinner label="Connecting to the advising engine…" />
          </div>
        )}

        {!booting && bootError && (
          <div className="mx-auto max-w-2xl">
            <Alert tone="error" title="Backend unavailable">
              <p>{bootError}</p>
              <p className="mt-2">
                Start it from the <code className="font-mono">backend/</code> folder:
                <br />
                <code className="mt-1 block rounded bg-white/60 px-2 py-1 font-mono text-xs">
                  uvicorn app.main:app --reload
                </code>
              </p>
            </Alert>
          </div>
        )}

        {/*
          The gate. Nothing student-facing renders until an account exists,
          because until then there is no student id to render it for.
        */}
        {!booting && !bootError && !user && <SignIn onSignedIn={afterSignIn} />}

        {!booting && !bootError && user && (
          <>
            {step === 'upload' && (
              <UploadStep
                programs={programs}
                programId={programId}
                onProgramChange={setProgramId}
                health={health}
                onExtracted={(result) => {
                  setExtraction(result)
                  setStep('review')
                }}
              />
            )}

            {step === 'review' && extraction && (
              <ReviewStep
                extraction={extraction}
                programId={programId}
                onConfirmed={() => {
                  setHasRecord(true)
                  setStep('dashboard')
                }}
                onBack={() => setStep('upload')}
              />
            )}

            {step === 'dashboard' && (
              <Dashboard
                studentId={user.student_id}
                onAddMore={() => setStep('upload')}
                onReset={reset}
                onAskAdvisor={() => setStep('advisor')}
              />
            )}

            {step === 'advisor' && (
              <AdvisorChat hasRecord={hasRecord} onGoToUpload={() => setStep('upload')} />
            )}

            {step === 'explore' && (
              <Explorer hasRecord={hasRecord} onGoToUpload={() => setStep('upload')} />
            )}

            {step === 'schedule' && <SemesterPlanner studentId={user.student_id} />}

            {step === 'compare' && (
              <CourseCompare studentId={user.student_id} programId={programId} />
            )}

            {step === 'deadlines' && <Deadlines studentId={user.student_id} />}
          </>
        )}
      </main>

      <footer className="mx-auto max-w-6xl px-6 pb-10 pt-2 text-center text-xs text-slate-400">
        Degree progress is computed by a deterministic rules engine, never by a language
        model. All data shown comes from the backend.
      </footer>
    </div>
  )
}

/** A labelled cluster of nav buttons. The label is for screen readers and wide screens. */
function NavGroup({ label, children }) {
  return (
    <div role="group" aria-label={label} className="flex shrink-0 items-center gap-1">
      <span className="mr-1 hidden text-xs font-medium uppercase tracking-wide text-slate-400 2xl:inline">
        {label}
      </span>
      {children}
    </div>
  )
}

/** Initials for the account chip: "Walkthrough Student" -> "WS", an email -> "W". */
function initials(name) {
  const words = String(name).split('@')[0].split(/[\s._-]+/).filter(Boolean)
  return (words.slice(0, 2).map((w) => w[0]).join('') || '?').toUpperCase()
}

/** The product mark: an open book under a mortarboard, drawn inline (no image request). */
function BrandMark() {
  return (
    <span
      aria-hidden="true"
      className="grid size-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-accent-300 to-accent-500 shadow-md shadow-black/20"
    >
      <svg viewBox="0 0 24 24" className="size-6 text-brand-950" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 9.5 12 5l10 4.5L12 14 2 9.5Z" />
        <path d="M6 11.6V16c0 1.2 2.7 2.8 6 2.8s6-1.6 6-2.8v-4.4" />
        <path d="M22 9.5v5" />
      </svg>
    </span>
  )
}

function NavItem({ label, active, enabled, hint, onClick }) {
  return (
    <button
      type="button"
      onClick={() => enabled && onClick()}
      disabled={!enabled}
      title={enabled ? undefined : hint}
      aria-current={active ? 'page' : undefined}
      className={`shrink-0 whitespace-nowrap rounded-full px-3.5 py-1.5 text-xs font-semibold transition ${
        active
          ? 'bg-gradient-to-b from-brand-600 to-brand-700 text-white shadow-sm shadow-brand-900/25'
          : enabled
            ? 'text-slate-600 hover:bg-brand-50 hover:text-brand-800'
            : 'cursor-not-allowed text-slate-300'
      }`}
    >
      {label}
    </button>
  )
}
