import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, EngineBadge, Spinner } from './ui'
import { generateSchedule, validateSchedule } from '../api'

/**
 * Semester plan.
 *
 * Auto-generate asks the backend to lay the remaining required courses out by term.
 * Every edit after that goes back to the backend to be checked, so the rules a
 * student sees enforced are the same ones that built the plan - this component
 * contains none of them. A course can be dragged to another term, or moved with the
 * select on each card, which is the keyboard route.
 */

const CREDIT_CHOICES = [9, 12, 15, 18]

/**
 * Shown when the backend sends no caveats of its own. The catalog encodes only
 * part of the degree, so a plan without this line would read as a complete
 * route to graduation - which it is not.
 */
export const FALLBACK_CAVEAT =
  'This plan covers only the requirements currently encoded in the catalog, which is partial. ' +
  'The last planned term is not a graduation date. Check DegreeWorks and your advisor before registering.'

const termCredits = (term) => term.courses.reduce((sum, c) => sum + Number(c.credits), 0)

function toLayout(terms) {
  return terms.map((t) => ({ term: t.term, courses: t.courses.map((c) => c.code) }))
}

export default function SemesterPlanner({ studentId }) {
  const [creditTarget, setCreditTarget] = useState(15)
  const [includeSummer, setIncludeSummer] = useState(false)
  const [plan, setPlan] = useState(null)
  const [terms, setTerms] = useState([])
  const [issues, setIssues] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [checkError, setCheckError] = useState(null)
  const [dragging, setDragging] = useState(null)
  const checkSeq = useRef(0)

  const check = useCallback(
    async (next, target) => {
      const seq = ++checkSeq.current
      try {
        const result = await validateSchedule(studentId, toLayout(next), target)
        // A slower, older answer must not overwrite the answer to the latest edit.
        if (seq === checkSeq.current) {
          setIssues(result.issues)
          setCheckError(null)
        }
      } catch (err) {
        if (seq === checkSeq.current) setCheckError(err.message)
      }
    },
    [studentId],
  )

  async function generate() {
    setLoading(true)
    setError(null)
    setCheckError(null)
    try {
      const result = await generateSchedule(studentId, {
        credit_target: creditTarget,
        include_summer: includeSummer,
      })
      setPlan(result)
      setTerms(
        result.terms.map((t) => ({
          term: t.term,
          courses: t.courses.map((c) => ({
            code: c.course.code,
            title: c.course.title,
            credits: c.course.credits,
            reasons: c.reasons,
            warnings: c.warnings,
          })),
        })),
      )
      setIssues([])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    generate()
    // Plan once on arrival; later changes are the student's to trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function move(code, toTerm) {
    const course = terms.flatMap((t) => t.courses).find((c) => c.code === code)
    if (!course) return
    const next = terms
      .map((t) => ({ ...t, courses: t.courses.filter((c) => c.code !== code) }))
      .map((t) => (t.term === toTerm ? { ...t, courses: [...t.courses, course] } : t))
    setTerms(next)
    check(next, creditTarget)
  }

  function remove(code) {
    const next = terms.map((t) => ({ ...t, courses: t.courses.filter((c) => c.code !== code) }))
    setTerms(next)
    check(next, creditTarget)
  }

  /*
   * A course the catalog marks "offered irregularly" already carries that warning on
   * its card, so the validator's identical warning is hidden for it. Errors always show.
   */
  const errorsFor = (term, code, hasOwnWarnings) =>
    issues.filter(
      (i) =>
        i.term === term && i.course === code && (i.severity === 'error' || !hasOwnWarnings),
    )
  const termIssues = (term) => issues.filter((i) => i.term === term && !i.course)
  const errorCount = issues.filter((i) => i.severity === 'error').length

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <Card
        title="Semester plan"
        subtitle="Your remaining required courses, laid out by term. Drag a course, or use its move menu."
        action={
          <Button onClick={generate} disabled={loading}>
            {loading ? 'Planning…' : 'Re-generate'}
          </Button>
        }
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <label className="flex items-center gap-2">
            <span className="text-slate-600">Credits per term</span>
            <select
              value={creditTarget}
              onChange={(e) => setCreditTarget(Number(e.target.value))}
              className="rounded-md border border-slate-300 bg-white px-2 py-1"
            >
              {CREDIT_CHOICES.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={includeSummer}
              onChange={(e) => setIncludeSummer(e.target.checked)}
            />
            <span className="text-slate-600">Include summer terms</span>
          </label>
          <span className="text-xs text-slate-400">Applies when you re-generate.</span>
        </div>
        <p className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <EngineBadge />
          Prerequisites and every check on your edits come from the catalog, not a language
          model.
        </p>
      </Card>

      {error && (
        <Alert tone="error" title="Couldn't build the plan">
          <p>{error}</p>
          <Button variant="secondary" className="mt-3" onClick={generate} disabled={loading}>
            Try again
          </Button>
        </Alert>
      )}

      {checkError && (
        <Alert tone="warning" title="Couldn't check that change">
          <p>
            {checkError} Your layout is kept, but it has not been checked against the
            prerequisites.
          </p>
        </Alert>
      )}

      {loading && !plan && (
        <div className="flex justify-center py-10">
          <Spinner label="Planning your semesters…" />
        </div>
      )}

      {plan && (
        <>
          <Alert tone="warning" title="Read this before relying on the plan">
            <ul className="list-disc space-y-1 pl-5">
              {(plan.caveats?.length ? plan.caveats : [FALLBACK_CAVEAT]).map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </Alert>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label="Terms planned" value={terms.filter((t) => t.courses.length).length} />
            <Stat label="Courses planned" value={terms.reduce((n, t) => n + t.courses.length, 0)} />
            <Stat
              label="Last planned term"
              value={plan.last_planned_term ?? 'None'}
              note="Not a graduation date"
            />
          </div>

          {errorCount > 0 && (
            <div
              role="alert"
              className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
            >
              {errorCount} problem{errorCount === 1 ? '' : 's'} with this layout. Fix the courses marked in red.
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {terms.map((t) => {
              const total = termCredits(t)
              return (
                <section
                  key={t.term}
                  aria-label={t.term}
                  onDragOver={(e) => dragging && e.preventDefault()}
                  onDrop={() => {
                    if (dragging) move(dragging, t.term)
                    setDragging(null)
                  }}
                  className={`rounded-xl border bg-white p-3 shadow-sm ${
                    dragging ? 'border-dashed border-slate-400' : 'border-slate-200'
                  }`}
                >
                  <header className="mb-2 flex items-baseline justify-between">
                    <h3 className="text-sm font-semibold text-slate-900">{t.term}</h3>
                    <span
                      className={`text-xs ${total > creditTarget ? 'font-medium text-amber-700' : 'text-slate-500'}`}
                    >
                      {total} credits
                    </span>
                  </header>

                  {termIssues(t.term).map((i) => (
                    <p key={i.message} className="mb-2 text-xs text-amber-700">
                      {i.message}
                    </p>
                  ))}

                  <ul className="space-y-2">
                    {t.courses.map((c) => {
                      const own = errorsFor(t.term, c.code, c.warnings.length > 0)
                      const bad = own.some((i) => i.severity === 'error')
                      return (
                        <li
                          key={c.code}
                          draggable
                          onDragStart={() => setDragging(c.code)}
                          onDragEnd={() => setDragging(null)}
                          className={`cursor-grab rounded-lg border p-2 text-sm ${
                            bad ? 'border-red-300 bg-red-50' : 'border-slate-200 bg-slate-50'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <span className="font-medium text-slate-900">{c.code}</span>
                              <span className="ml-1 text-xs text-slate-500">{c.credits} cr</span>
                              <p className="text-xs text-slate-600">{c.title}</p>
                            </div>
                            <button
                              type="button"
                              onClick={() => remove(c.code)}
                              aria-label={`Remove ${c.code} from the plan`}
                              className="rounded px-1 text-slate-400 hover:text-slate-900"
                            >
                              ×
                            </button>
                          </div>
                          {c.reasons[0] && (
                            <p className="mt-1 text-xs text-slate-500">{c.reasons[0]}</p>
                          )}
                          {c.warnings.map((w) => (
                            <p key={w} className="mt-1 text-xs text-amber-700">
                              {w}
                            </p>
                          ))}
                          {own.map((i) => (
                            <p
                              key={i.message}
                              className={`mt-1 text-xs ${i.severity === 'error' ? 'text-red-700' : 'text-amber-700'}`}
                            >
                              {i.message}
                            </p>
                          ))}
                          <label className="mt-2 block text-xs text-slate-500">
                            <span className="sr-only">Move {c.code} to</span>
                            <select
                              value={t.term}
                              onChange={(e) => move(c.code, e.target.value)}
                              className="w-full rounded border border-slate-300 bg-white px-1 py-0.5"
                            >
                              {terms.map((o) => (
                                <option key={o.term} value={o.term}>
                                  {o.term === t.term ? `In ${o.term}` : `Move to ${o.term}`}
                                </option>
                              ))}
                            </select>
                          </label>
                        </li>
                      )
                    })}
                    {t.courses.length === 0 && (
                      <li className="rounded-lg border border-dashed border-slate-300 p-3 text-center text-xs text-slate-400">
                        Drop a course here
                      </li>
                    )}
                  </ul>
                </section>
              )
            })}
          </div>

          {terms.length === 0 && (
            <Alert tone="info" title="Nothing left to plan">
              <p>
                Every requirement the catalog currently encodes is already met or in progress.
              </p>
            </Alert>
          )}

          {plan.unscheduled.length > 0 && (
            <Card title="Couldn't be placed" subtitle="Talk to an advisor about these.">
              <ul className="space-y-1 text-sm">
                {plan.unscheduled.map((u) => (
                  <li key={u.course.code}>
                    <span className="font-medium">{u.course.code}</span>
                    <span className="text-slate-500"> · {u.reason}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </div>
  )
}

function Stat({ label, value, note }) {
  return (
    <div className="rounded-lg bg-white px-4 py-3 shadow-sm ring-1 ring-slate-200">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-900">{value}</div>
      {note && <div className="text-xs text-slate-400">{note}</div>}
    </div>
  )
}
