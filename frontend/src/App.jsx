import { useEffect, useState } from 'react'
import { Alert, Spinner } from './components/ui'
import UploadStep from './components/UploadStep'
import ReviewStep from './components/ReviewStep'
import Dashboard from './components/Dashboard'
import { deleteRecord, getHealth, getPrograms, getRecord } from './api'

/**
 * COSC 490 Multimodal Academic Advisor.
 *
 * Three steps, matching the backend's own shape: upload (stores nothing), review
 * and confirm (the only way data becomes trusted), then the audit and plan.
 *
 * There is no authentication yet, so the student id is fixed for the demo. That
 * is a known gap, flagged in PR #3 rather than papered over here.
 */
const DEMO_STUDENT_ID = 'demo-student'

const STEPS = [
  { id: 'upload', label: 'Upload transcript' },
  { id: 'review', label: 'Review & confirm' },
  { id: 'dashboard', label: 'Progress & advice' },
]

export default function App() {
  const [step, setStep] = useState('upload')
  const [health, setHealth] = useState(null)
  const [programs, setPrograms] = useState([])
  const [programId, setProgramId] = useState('')
  const [extraction, setExtraction] = useState(null)
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
        // Returning student: skip straight to their dashboard.
        return getRecord(DEMO_STUDENT_ID)
          .then(() => setStep('dashboard'))
          .catch(() => {})
      })
      .catch((err) => setBootError(err.message))
      .finally(() => setBooting(false))
  }, [])

  async function reset() {
    try {
      await deleteRecord(DEMO_STUDENT_ID)
    } catch {
      /* nothing stored yet - fine */
    }
    setExtraction(null)
    setStep('upload')
  }

  const activeIndex = STEPS.findIndex((s) => s.id === step)

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
          <nav className="flex items-center gap-1.5 text-xs">
            {STEPS.map((s, i) => (
              <div key={s.id} className="flex items-center gap-1.5">
                <span
                  className={`rounded-full px-2.5 py-1 font-medium ${
                    i === activeIndex
                      ? 'bg-slate-900 text-white'
                      : i < activeIndex
                        ? 'bg-emerald-50 text-emerald-700'
                        : 'text-slate-400'
                  }`}
                >
                  {i + 1}. {s.label}
                </span>
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

        {!booting && !bootError && (
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
                studentId={DEMO_STUDENT_ID}
                programId={programId}
                onConfirmed={() => setStep('dashboard')}
                onBack={() => setStep('upload')}
              />
            )}

            {step === 'dashboard' && (
              <Dashboard
                studentId={DEMO_STUDENT_ID}
                onAddMore={() => setStep('upload')}
                onReset={reset}
              />
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
