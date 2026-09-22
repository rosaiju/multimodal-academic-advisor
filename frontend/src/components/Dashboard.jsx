import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  EmptyState,
  ProgressRing,
  ProvenanceBadge,
  Spinner,
  StatusBadge,
} from './ui'
import { getAuditSummary, getPlan } from '../api'

/**
 * Step 3 - degree progress and advice.
 *
 * Two backend calls: /audit/summary for the headline numbers and /plan for gaps,
 * recommendations and blocked courses. No data is computed in the browser; the
 * deterministic engine decides everything and this screen renders it.
 *
 * The coverage caveat is rendered next to the percentage rather than tucked away,
 * because a bare "9.2% complete" invites exactly the misreading that line exists
 * to prevent.
 */
export default function Dashboard({ studentId, onAddMore, onReset, onAskAdvisor }) {
  const [summary, setSummary] = useState(null)
  const [plan, setPlan] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getAuditSummary(studentId), getPlan(studentId, 8)])
      .then(([s, p]) => {
        if (!cancelled) {
          setSummary(s)
          setPlan(p)
        }
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [studentId])

  if (error) {
    return (
      <div className="mx-auto max-w-2xl">
        <Alert tone="error" title="Could not load your progress">
          {error}
        </Alert>
      </div>
    )
  }
  if (!summary || !plan) {
    return (
      <div className="flex justify-center py-16">
        <Spinner label="Computing your degree audit…" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      {/* ---- progress ---- */}
      <Card
        action={
          <div className="flex flex-wrap gap-2">
            {onAskAdvisor && (
              <Button onClick={onAskAdvisor}>Ask the advisor</Button>
            )}
            <Button variant="secondary" onClick={onAddMore}>
              Add another transcript
            </Button>
            <Button variant="ghost" onClick={onReset}>
              Start over
            </Button>
          </div>
        }
      >
        <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-start">
          {/*
            The ring measures CREDIT EARNED against credit the degree requires, and
            its caption says exactly that. It is deliberately not a degree-completion
            figure: while requirements are missing from the catalog there is no honest
            one, and the backend returns null rather than a number that reads as one.

            A full ring here means "you have earned the credit hours", which is a
            necessary and very much not sufficient condition for graduating.
          */}
          <ProgressRing
            percent={summary.credit_progress_percent}
            caption={`${summary.credits_earned} of ${summary.credits_required} required credits earned`}
            detail={
              summary.progress_is_partial
                ? 'Credit hours only — not degree completion'
                : undefined
            }
          />

          <div className="flex-1">
            <h2 className="text-lg font-semibold text-slate-900">Credit progress</h2>
            <p className="text-sm text-slate-500">
              {summary.program_id} · catalog {summary.catalog_year}
            </p>

            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ['Credits earned', `${summary.credits_earned} / ${summary.credits_required}`],
                ['In progress', `${summary.credits_in_progress} cr`],
                ['Applied to encoded requirements', `${summary.credits_applied} cr`],
                ['Not in our catalog', `${summary.credits_outside_catalog} cr`],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg bg-slate-50 px-3 py-2">
                  <div className="text-lg font-semibold tabular-nums text-slate-900">{value}</div>
                  <div className="text-xs text-slate-500">{label}</div>
                </div>
              ))}
            </div>

            {summary.progress_is_partial && (
              <div className="mt-4">
                <Alert tone="warning" title="We cannot tell you how complete your degree is">
                  <p>
                    Our catalog encodes only part of this degree, so there is no honest
                    percentage to show. <strong>Do not read the figures above as degree
                    completion.</strong> They describe credit on your record, not requirements met.
                  </p>
                  <p className="mt-2">
                    Of the requirements we have encoded,{' '}
                    <strong>
                      {summary.satisfied} of{' '}
                      {summary.satisfied +
                        summary.in_progress +
                        summary.unmet +
                        summary.needs_advisor}
                    </strong>{' '}
                    are satisfied ({summary.encoded_requirements_percent}%) — a share of what we
                    model, not of your degree.
                  </p>
                  <p className="mt-2 text-xs">{summary.coverage}</p>
                  <p className="mt-2 text-xs">
                    Your official DegreeWorks audit remains the authority. Check it, and your
                    advisor, before making registration decisions.
                  </p>
                </Alert>
              </div>
            )}

            {Number(summary.credits_outside_catalog) > 0 && (
              <p className="mt-3 text-xs text-slate-500">
                {summary.credits_outside_catalog} credits on your record are courses our catalog
                does not list — mostly transfer work. They are counted in your credits earned, but
                we cannot say which requirements they satisfy; only the registrar can.
              </p>
            )}
          </div>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* ---- recommendations ---- */}
        <Card
          title="Recommended next"
          subtitle="Courses you can take now, ordered by how much they unblock"
          className="lg:col-span-2"
        >
          {plan.under_way?.length > 0 && (
            <div className="mb-4 rounded-lg bg-sky-50 px-4 py-3 ring-1 ring-inset ring-sky-600/20">
              <div className="text-sm font-medium text-sky-900">
                Already under way — not recommended below
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {plan.under_way.map((course) => (
                  <span
                    key={course.code}
                    title={course.title}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-sm text-sky-900 ring-1 ring-inset ring-sky-600/20"
                  >
                    <span className="font-medium">{course.code}</span>
                    <span className="text-sky-700/70">{course.credits} cr</span>
                  </span>
                ))}
              </div>
              <p className="mt-2 text-xs text-sky-800">
                You are enrolled in these now, so they are left out of the suggestions. They do not
                count toward a requirement until a final grade lands.
              </p>
            </div>
          )}

          {plan.recommended.length === 0 ? (
            <EmptyState>Nothing to recommend yet — confirm some coursework first.</EmptyState>
          ) : (
            <ul className="space-y-3">
              {plan.recommended.map((rec) => (
                <li
                  key={rec.course.code}
                  className="rounded-lg border border-slate-200 p-4 transition hover:border-slate-300"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-slate-900">{rec.course.code}</span>
                        <span className="text-slate-400">·</span>
                        <span className="text-slate-700">{rec.course.title}</span>
                      </div>
                      <div className="mt-0.5 text-xs text-slate-500">
                        {rec.course.credits} credits
                        {rec.unlocks_count > 0 && ` · unlocks ${rec.unlocks_count} later course(s)`}
                      </div>
                    </div>
                    <ProvenanceBadge value={rec.provenance} />
                  </div>

                  {/* Why this course - the requirement the professor will ask about */}
                  <ul className="mt-3 space-y-1">
                    {rec.reasons.map((reason, i) => (
                      <li key={i} className="flex gap-2 text-sm text-slate-600">
                        <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-slate-400" />
                        {reason}
                      </li>
                    ))}
                  </ul>

                  {rec.warnings.map((w, i) => (
                    <p key={i} className="mt-2 text-xs text-amber-700">
                      ⚠ {w}
                    </p>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* ---- remaining requirements ---- */}
        <Card title="Remaining requirements" subtitle="What still stands between you and the degree">
          {plan.gaps.length === 0 ? (
            <EmptyState>Every encoded requirement is satisfied.</EmptyState>
          ) : (
            <ul className="space-y-3">
              {plan.gaps.map((gap) => (
                <li key={gap.block_id} className="rounded-lg bg-slate-50 px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <span className="font-medium text-slate-900">{gap.name}</span>
                    <StatusBadge value={gap.status} />
                  </div>
                  {gap.courses_still_needed > 0 && (
                    <p className="mt-1 text-sm text-slate-600">
                      {gap.courses_still_needed} more course
                      {gap.courses_still_needed === 1 ? '' : 's'} needed
                    </p>
                  )}
                  {gap.options.length > 0 && (
                    <p className="mt-1 text-xs text-slate-500">
                      Options: {gap.options.slice(0, 6).map((o) => o.code).join(', ')}
                      {gap.options.length > 6 && ` +${gap.options.length - 6} more`}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* ---- blocked ---- */}
        <Card
          title="Not yet available"
          subtitle="These count toward your degree, but prerequisites come first"
        >
          {plan.blocked.length === 0 ? (
            <EmptyState>Nothing is blocked — everything remaining is open to you.</EmptyState>
          ) : (
            <ul className="space-y-3">
              {plan.blocked.map((entry) => (
                <li key={entry.course.code} className="rounded-lg bg-slate-50 px-4 py-3">
                  <div className="font-medium text-slate-900">
                    {entry.course.code} · {entry.course.title}
                  </div>
                  <p className="mt-1 text-sm text-rose-700">
                    Needs first: {entry.missing_prerequisites.join(', ')}
                  </p>
                  {entry.remaining_chain > 1 && (
                    <p className="mt-0.5 text-xs text-slate-500">
                      {entry.remaining_chain} terms of prerequisites still ahead
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* ---- completed ---- */}
        <Card
          title="Completed coursework"
          subtitle={`${plan.completed.length} course(s) · ${plan.completed_credits} credits`}
          className="lg:col-span-2"
        >
          {plan.completed.length === 0 ? (
            <EmptyState>No confirmed coursework yet.</EmptyState>
          ) : (
            <div className="flex flex-wrap gap-2">
              {plan.completed.map((course) => (
                <span
                  key={course.code}
                  title={course.title}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-1.5 text-sm text-emerald-900 ring-1 ring-inset ring-emerald-600/20"
                >
                  <span className="font-medium">{course.code}</span>
                  <span className="text-emerald-700/70">{course.credits} cr</span>
                </span>
              ))}
            </div>
          )}
        </Card>
      </div>

      {plan.caveats.map((caveat, i) => (
        <Alert key={i} tone="info">
          {caveat}
        </Alert>
      ))}
    </div>
  )
}
