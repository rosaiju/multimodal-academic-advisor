import { useEffect, useState } from 'react'
import { Alert, Spinner } from './components/ui'
import UploadStep from './components/UploadStep'
import ReviewStep from './components/ReviewStep'
import Dashboard from './components/Dashboard'
import AdvisorChat from './components/AdvisorChat'
import SignIn from './components/SignIn'
import { deleteRecord, getHealth, getPrograms, getRecord, logout } from './api'

/**
 * COSC 490 Multimodal Academic Advisor.
 *
 * Three steps, matching the backend's own shape: upload (stores nothing), review
 * and confirm (the only way data becomes trusted), then the audit and plan.
 *
 * Nothing here knows a student id until the server issues one. `user` is null
 * until sign-in, and every student-scoped call uses `user.student_id`, so there
 * is no id for the frontend to guess, type, or get wrong.
 */

const STEPS = [
  { id: 'upload', label: 'Upload transcript' },
  { id: 'review', label: 'Review & confirm' },
  { id: 'dashboard', label: 'Progress & advice' },
  { id: 'advisor', label: 'Ask the advisor' },
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

  const activeIndex = STEPS.findIndex((s) => s.id === step)

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
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-4">
          <div>
            <h1 className="font-semibold tracking-tight">Academic Advisor</h1>
            <p className="text-xs text-slate-500">
              Morgan State University · Computer Science · COSC 490
            </p>
          </div>
          {/*
            flex-wrap, not a fixed row: the fourth step ("Ask the advisor") pushed
            this nav to 438px at a 400px viewport and scrolled the whole page
            sideways. Chips wrap onto a second line on a phone instead.
          */}
          <nav className="flex flex-wrap items-center justify-end gap-1.5 text-xs">
            {user && (
              <div className="mr-3 flex items-center gap-2 border-r border-slate-200 pr-3">
                <span className="max-w-40 truncate text-slate-600" title={user.email}>
                  {user.name || user.email}
                </span>
                <button
                  type="button"
                  onClick={signOut}
                  className="font-medium text-slate-900 underline underline-offset-2"
                >
                  Sign out
                </button>
              </div>
            )}
            {user &&
              STEPS.map((s, i) => (
                <div key={s.id} className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => canVisit(s.id) && setStep(s.id)}
                    disabled={!canVisit(s.id)}
                    aria-current={i === activeIndex ? 'step' : undefined}
                    className={`rounded-full px-2.5 py-1 font-medium transition ${
                      i === activeIndex
                        ? 'bg-slate-900 text-white'
                        : canVisit(s.id)
                          ? 'text-slate-600 hover:bg-slate-100'
                          : 'cursor-not-allowed text-slate-300'
                    }`}
                  >
                    {i + 1}. {s.label}
                  </button>
                  {i < STEPS.length - 1 && <span className="text-slate-300">›</span>}
                </div>
              ))}
          </nav>
        </div>
      </header>

      <main className="px-6 py-8">
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
