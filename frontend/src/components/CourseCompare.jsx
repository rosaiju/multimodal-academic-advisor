import { useEffect, useState } from 'react'
import { Alert, Card, EngineBadge, Spinner } from './ui'
import { compareCourses, getCatalogCourses } from '../api'

/**
 * Compare 2 to 4 courses for this student.
 *
 * The backend answers every question shown here (can I take it, does it count
 * toward anything I still need, what does it unlock), from the catalog and the
 * confirmed record. Nothing is ranked or recommended: the differences are stated
 * and the choice is the student's.
 */

const MAX = 4
const STATE = {
  passed: { text: 'Already passed', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' },
  in_progress: { text: 'Taking now', cls: 'bg-sky-50 text-sky-700 ring-sky-600/20' },
  eligible_now: { text: 'You can take it now', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' },
  blocked: { text: 'Prerequisites needed', cls: 'bg-amber-50 text-amber-800 ring-amber-600/30' },
}

export default function CourseCompare({ studentId, programId }) {
  const [courses, setCourses] = useState(null)
  const [picked, setPicked] = useState([])
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [catalogError, setCatalogError] = useState(null)

  useEffect(() => {
    if (!programId) return
    let cancelled = false
    getCatalogCourses(programId)
      .then((list) => !cancelled && setCourses(list))
      .catch((e) => !cancelled && setCatalogError(e.message))
    return () => {
      cancelled = true
    }
  }, [programId])

  useEffect(() => {
    if (picked.length < 2) return
    let cancelled = false
    compareCourses(studentId, picked)
      .then((r) => {
        if (cancelled) return
        setResult(r)
        setError(null)
      })
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [studentId, picked])

  // Only show an answer that is for exactly the courses picked right now.
  const current =
    result && picked.length >= 2 && result.courses.map((c) => c.code).join() === picked.join()
      ? result
      : null
  const available = (courses ?? []).filter((c) => !picked.includes(c.code))

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <Card
        title="Compare courses"
        subtitle="Pick two to four courses to see how they differ for you."
        action={<EngineBadge />}
      >
        {catalogError && (
          <div className="mb-3">
            <Alert tone="error" title="Couldn't load the course list">
              <p>{catalogError}</p>
            </Alert>
          </div>
        )}
        {!courses && !catalogError && <Spinner label="Loading the catalog…" />}
        <div className="flex flex-wrap items-center gap-2">
          {picked.map((code) => (
            <span
              key={code}
              className="inline-flex items-center gap-1 rounded-full bg-slate-900 px-3 py-1 text-sm text-white"
            >
              {code}
              <button
                type="button"
                aria-label={`Remove ${code}`}
                onClick={() => setPicked(picked.filter((c) => c !== code))}
                className="text-slate-300 hover:text-white"
              >
                ×
              </button>
            </span>
          ))}
          {courses && picked.length < MAX && (
            <label className="block w-full text-sm sm:w-auto">
              <span className="sr-only">Add a course</span>
              <select
                value=""
                onChange={(e) => e.target.value && setPicked([...picked, e.target.value])}
                className="w-full rounded-md border border-slate-300 bg-white px-2 py-1 sm:w-auto sm:max-w-md"
              >
                <option value="">Add a course…</option>
                {available.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.code} · {c.title}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {courses && picked.length < 2 && (
          <p className="mt-3 text-sm text-slate-500">Choose at least two courses to compare.</p>
        )}
      </Card>

      {error && (
        <Alert tone="error" title="Couldn't compare those">
          <p>{error}</p>
        </Alert>
      )}
      {!current && !error && picked.length >= 2 && (
        <div className="flex justify-center py-8">
          <Spinner label="Comparing…" />
        </div>
      )}

      {current && (
        <>
          {current.differences.length > 0 && (
            <Card title="Where they differ">
              <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
                {current.differences.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </Card>
          )}

          <div
            className="grid gap-3"
            style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}
          >
            {current.courses.map((c) => {
              const state = STATE[c.state]
              return (
                <section
                  key={c.code}
                  aria-label={c.code}
                  className="rounded-xl border border-slate-200 bg-white p-4 text-sm shadow-sm"
                >
                  <h3 className="font-semibold text-slate-900">
                    {c.code} <span className="font-normal text-slate-500">{c.credits} cr</span>
                  </h3>
                  <p className="text-slate-600">{c.title}</p>
                  <span
                    className={`mt-2 inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${state.cls}`}
                  >
                    {state.text}
                  </span>

                  <dl className="mt-3 space-y-2">
                    <Row label="Prerequisites">
                      {c.prerequisites.length ? c.prerequisites.join(', ') : 'None'}
                      {c.missing_prerequisites.length > 0 && (
                        <span className="block text-amber-700">
                          Still needed: {c.missing_prerequisites.join(', ')}
                        </span>
                      )}
                    </Row>
                    <Row label="Counts toward">
                      {c.serves.length === 0
                        ? 'No encoded requirement'
                        : c.serves.map((s) => (
                            <span key={s.block_id} className="block">
                              {s.name}{' '}
                              <span className={s.still_open ? 'text-emerald-700' : 'text-slate-400'}>
                                {s.still_open ? '(still open)' : '(already met)'}
                              </span>
                            </span>
                          ))}
                    </Row>
                    <Row label="Unlocks">{c.unlocks.length ? c.unlocks.join(', ') : 'Nothing further'}</Row>
                    <Row label="Road to it">
                      {c.remaining_chain} term{c.remaining_chain === 1 ? '' : 's'} of prerequisites
                    </Row>
                    <Row label="Offered">
                      {c.terms_assumed ? 'Not recorded in the catalog' : c.terms_offered.join(', ')}
                    </Row>
                  </dl>
                  {c.warnings.map((w) => (
                    <p key={w} className="mt-2 text-xs text-amber-700">
                      {w}
                    </p>
                  ))}
                </section>
              )
            })}
          </div>

          {current.caveats.length > 0 && (
            <Alert tone="warning" title="Keep in mind">
              <ul className="list-disc space-y-1 pl-5">
                {current.caveats.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </Alert>
          )}
        </>
      )}
    </div>
  )
}

function Row({ label, children }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="text-slate-700">{children}</dd>
    </div>
  )
}
