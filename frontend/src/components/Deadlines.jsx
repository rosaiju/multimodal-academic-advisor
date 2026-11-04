import { useEffect, useState } from 'react'
import { Alert, Button, Card, Spinner } from './ui'
import { downloadDeadlinesCalendar, getDeadlines } from '../api'

/**
 * Dates a student plans around.
 *
 * The dates come from a sourced calendar file on the backend, and each links to the
 * Morgan page it was read from. A deadline the file doesn't have is listed as "not
 * yet sourced" with where to look, never left out, so a gap can't read as "nothing
 * is due". The calendar download carries a one-day-before reminder on every event.
 */

const STATE = {
  open: { text: 'Open now', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' },
  upcoming: { text: 'Upcoming', cls: 'bg-sky-50 text-sky-700 ring-sky-600/20' },
  past: { text: 'Past', cls: 'bg-slate-100 text-slate-500 ring-slate-500/20' },
}

const fmt = (iso) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })

function countdown(d) {
  if (d.state === 'past') return null
  if (d.state === 'open') {
    return d.days_away === 0 ? 'Closes today' : `Closes in ${d.days_away} day${d.days_away === 1 ? '' : 's'}`
  }
  return d.days_away === 0 ? 'Today' : `In ${d.days_away} day${d.days_away === 1 ? '' : 's'}`
}

export default function Deadlines({ studentId }) {
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    getDeadlines(studentId)
      .then((r) => !cancelled && setReport(r))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [studentId])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await downloadDeadlinesCalendar(studentId)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!report && !error) {
    return (
      <div className="flex justify-center py-16">
        <Spinner label="Loading your dates…" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      {error && (
        <Alert tone="error" title="Something went wrong">
          <p>{error}</p>
        </Alert>
      )}

      {report && (
        <>
          <Card
            title="Deadlines"
            subtitle={
              report.standing
                ? `Registration dates shown for: ${report.standing.label}`
                : 'Dates for all students'
            }
            action={
              <Button variant="secondary" onClick={save} disabled={saving}>
                {saving ? 'Preparing…' : 'Add to my calendar'}
              </Button>
            }
          >
            <p className="text-sm text-slate-500">
              The calendar file gives each date a reminder the day before, so your own calendar
              app does the reminding.
            </p>
          </Card>

          <ul className="space-y-3">
            {report.deadlines.map((d) => {
              const s = STATE[d.state]
              const left = countdown(d)
              return (
                <li
                  key={d.id}
                  className={`rounded-xl border bg-white p-4 shadow-sm ${
                    d.state === 'past' ? 'border-slate-200 opacity-70' : 'border-slate-300'
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <h3 className="font-semibold text-slate-900">
                        {d.title}
                        {d.is_yours && (
                          <span className="ml-2 rounded bg-slate-900 px-1.5 py-0.5 text-xs font-medium text-white">
                            Your group
                          </span>
                        )}
                      </h3>
                      <p className="text-sm text-slate-700">
                        {fmt(d.date)}
                        {d.end_date && ` to ${fmt(d.end_date)}`}
                      </p>
                    </div>
                    <div className="text-right">
                      <span
                        className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${s.cls}`}
                      >
                        {s.text}
                      </span>
                      {left && (
                        <p className={`mt-1 text-xs ${d.soon ? 'font-medium text-amber-700' : 'text-slate-500'}`}>
                          {left}
                        </p>
                      )}
                    </div>
                  </div>
                  {d.detail && <p className="mt-1 text-sm text-slate-600">{d.detail}</p>}
                  <a
                    href={d.source}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-block text-xs text-slate-500 underline underline-offset-2"
                  >
                    Source on morgan.edu
                  </a>
                </li>
              )
            })}
          </ul>

          {report.not_yet_sourced.length > 0 && (
            <Alert tone="warning" title="Dates we don't have yet">
              <ul className="space-y-2">
                {report.not_yet_sourced.map((n) => (
                  <li key={n.title}>
                    <span className="font-medium">{n.title}.</span> {n.where_to_look}{' '}
                    <a
                      href={n.source}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="underline underline-offset-2"
                    >
                      Open the page
                    </a>
                  </li>
                ))}
              </ul>
            </Alert>
          )}

          <ul className="list-disc space-y-1 pl-5 text-xs text-slate-500">
            {report.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
            <li>Calendar read from Morgan's pages on {fmt(report.calendar_retrieved)}.</li>
          </ul>
        </>
      )}
    </div>
  )
}
