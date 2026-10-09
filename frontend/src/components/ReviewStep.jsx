import { useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  ConfidenceBadge,
  ProvenanceBadge,
  RowStatusBadge,
  Spinner,
} from './ui'
import { confirmCourses } from '../api'

/** A field as the student currently sees it: their edit if any, else what was read. */
function currentValue(row, rowEdits, field) {
  return rowEdits?.[field] ?? row[field] ?? ''
}

/**
 * Step 2 - review and confirm.
 *
 * This screen is the point of the whole provenance design. Every row arrives
 * tagged "Not yet confirmed" and stays that way until the student ticks it.
 *
 * Rows come in three kinds, and conflating them is what made this screen
 * unusable on a real audit:
 *
 *   - CONFIRMABLE. Everything the engine needs is present. An in-progress course
 *     is one of these: "IP" is a status, not a missing grade, so it can be
 *     confirmed as-is and the engine's NON_PASSING rule keeps it from satisfying
 *     anything until a real grade lands.
 *   - NEEDS A FIELD. The extractor could not read a term, grade or credit value.
 *     The student can supply it, and until they do the row stays unticked.
 *   - NOT A COURSE. A summary line such as "TRANSFER OF 24 CREDITS" totalling
 *     credit that is itemised separately. No edit turns it into coursework, so
 *     its checkbox is disabled rather than merely unticked, matching the backend,
 *     which refuses it however it is edited.
 *
 * Only confirmable rows are ticked by default, so one unusable row cannot block
 * the other sixty-two. There is still deliberately no "accept everything" button,
 * matching the backend, which has no bulk-accept helper for the same reason.
 */
export default function ReviewStep({ extraction, programId, onConfirmed, onBack }) {
  const rows = extraction.extraction.courses
  const [edits, setEdits] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const valueOf = (index, field) => currentValue(rows[index], edits[index], field)

  /**
   * Why each row cannot be confirmed, or null. Recomputed as the student types,
   * so filling a gap clears the block immediately.
   *
   * The backend sends `blocking_reason` for the row as EXTRACTED; this reapplies
   * the same rule to the edited values. A placeholder stays blocked regardless -
   * that is not a missing field the student can fill.
   */
  const blockers = useMemo(() => {
    return rows.map((row, i) => {
      if (row.is_placeholder) return row.blocking_reason
      const missing = ['term', 'grade', 'credits'].filter(
        (f) => currentValue(row, edits[i], f) === '',
      )
      if (missing.length === 0) return null
      return `Missing ${missing.join(', ')}. Fill this in from your transcript, or untick the row.`
    })
  }, [rows, edits])

  const [selected, setSelected] = useState(
    () => new Set(rows.map((_, i) => i).filter((i) => !rows[i].blocking_reason)),
  )

  const blockedSelection = [...selected].filter((i) => blockers[i] !== null).sort((a, b) => a - b)
  const excludedCount = rows.length - selected.size

  function edit(index, field, value) {
    setEdits((prev) => ({ ...prev, [index]: { ...prev[index], [field]: value } }))
  }

  function toggle(index) {
    if (rows[index].is_placeholder) return
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  function untickBlocked() {
    setSelected((prev) => {
      const next = new Set(prev)
      blockedSelection.forEach((i) => next.delete(i))
      return next
    })
  }

  function selectAllConfirmable() {
    setSelected(new Set(rows.map((_, i) => i).filter((i) => blockers[i] === null)))
  }

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      const items = [...selected].map((i) => {
        const item = { extracted: rows[i] }
        // Only send a field when the student actually changed it, so the record
        // can tell a correction apart from an accurate read.
        for (const field of ['term', 'grade', 'credits']) {
          const edited = edits[i]?.[field]
          if (edited !== undefined && String(edited) !== String(rows[i][field] ?? '')) {
            item[field] = field === 'credits' ? String(edited) : edited
          }
        }
        return item
      })
      await confirmCourses({
        programId,
        sourceName: extraction.extraction.source_name,
        extractor: extraction.extraction.extractor,
        items,
      })
      onConfirmed()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function rowStatus(row, index) {
    if (row.is_placeholder) return 'placeholder'
    if (blockers[index]) return 'needs_field'
    if (row.in_progress) return 'in_progress'
    if (row.transfer) return 'transfer'
    return null
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <Card
        title={`We read ${rows.length} course${rows.length === 1 ? '' : 's'}`}
        subtitle={extraction.summary}
        action={
          <Button variant="ghost" onClick={onBack}>
            Upload a different file
          </Button>
        }
      >
        {extraction.extraction.warnings.length > 0 && (
          <div className="mb-4 space-y-2">
            {extraction.extraction.warnings.map((w, i) => (
              <Alert key={i} tone="warning">
                {w}
              </Alert>
            ))}
          </div>
        )}

        {rows.length === 0 ? (
          <Alert tone="warning" title="No courses were found">
            Try a plain-text transcript, or a PDF exported from your student portal.
          </Alert>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="w-10 py-2"></th>
                  <th className="py-2 pr-3">Course</th>
                  <th className="py-2 pr-3">Term</th>
                  <th className="py-2 pr-3">Grade</th>
                  <th className="py-2 pr-3">Credits</th>
                  <th className="py-2">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map((row, i) => {
                  const status = rowStatus(row, i)
                  const blocked = blockers[i] !== null
                  return (
                    <tr
                      key={i}
                      className={`${selected.has(i) ? '' : 'opacity-45'} ${
                        blocked && selected.has(i) ? 'bg-rose-50/40' : ''
                      }`}
                    >
                      <td className="py-3">
                        <input
                          type="checkbox"
                          checked={selected.has(i)}
                          disabled={row.is_placeholder}
                          onChange={() => toggle(i)}
                          title={
                            row.is_placeholder
                              ? 'This row is not a course and cannot be added'
                              : undefined
                          }
                          className="size-4 rounded border-slate-300 accent-slate-900 disabled:cursor-not-allowed disabled:opacity-40"
                        />
                      </td>
                      <td className="py-3 pr-3">
                        <div className="font-medium text-slate-900">{row.code}</div>
                        {row.title && <div className="text-xs text-slate-500">{row.title}</div>}
                        {row.institution && (
                          <div className="text-xs text-slate-400">{row.institution}</div>
                        )}
                      </td>
                      {['term', 'grade', 'credits'].map((field) => (
                        <td key={field} className="py-3 pr-3">
                          <input
                            value={valueOf(i, field)}
                            disabled={row.is_placeholder}
                            onChange={(e) => edit(i, field, e.target.value)}
                            placeholder={field === 'term' ? 'e.g. Fall 2024' : '—'}
                            className={`w-full min-w-24 rounded-md border px-2 py-1 text-sm focus:outline-none disabled:bg-slate-50 disabled:text-slate-400 ${
                              valueOf(i, field) === '' && !row.is_placeholder
                                ? 'border-rose-300 bg-rose-50 focus:border-rose-500'
                                : 'border-slate-300 focus:border-slate-900'
                            }`}
                          />
                        </td>
                      ))}
                      <td className="py-3">
                        <div className="flex flex-col items-start gap-1">
                          <RowStatusBadge value={status} />
                          <ConfidenceBadge value={row.confidence} />
                          <ProvenanceBadge value={row.provenance} />
                        </div>
                        {blockers[i] && (
                          <p className="mt-1 text-xs font-medium text-rose-700">{blockers[i]}</p>
                        )}
                        {row.issues?.length > 0 && (
                          <ul className="mt-1 space-y-0.5 text-xs text-slate-500">
                            {row.issues.map((issue, k) => (
                              <li key={k}>• {issue}</li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {error && (
        <Alert tone="error" title="Could not save">
          {error}
        </Alert>
      )}

      {blockedSelection.length > 0 && (
        <Alert
          tone="warning"
          title={`${blockedSelection.length} ticked row${
            blockedSelection.length === 1 ? '' : 's'
          } cannot be confirmed yet`}
        >
          <ul className="mt-1 space-y-1">
            {blockedSelection.map((i) => (
              <li key={i}>
                <span className="font-semibold">{rows[i].code}</span>
                {rows[i].line_number ? (
                  <span className="text-xs text-slate-500"> (line {rows[i].line_number})</span>
                ) : null}{' '}
                — {blockers[i]}
              </li>
            ))}
          </ul>
          <p className="mt-2">
            Fix the highlighted fields, or untick those rows and confirm the rest. We will not
            guess a grade or term, because that decides whether a requirement counts.
          </p>
          <div className="mt-2">
            <Button variant="ghost" onClick={untickBlocked}>
              Untick {blockedSelection.length} blocking row
              {blockedSelection.length === 1 ? '' : 's'}
            </Button>
          </div>
        </Alert>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-5 py-4">
        <div className="text-sm text-slate-600">
          <span className="font-semibold text-slate-900">{selected.size}</span> of {rows.length}{' '}
          selected. Only what you confirm counts toward your degree.
          {excludedCount > 0 && (
            <button
              type="button"
              onClick={selectAllConfirmable}
              className="ml-2 text-sm font-medium text-slate-900 underline underline-offset-2"
            >
              Select every confirmable row
            </button>
          )}
        </div>
        <div className="flex items-center gap-3">
          {busy && <Spinner label="Saving…" />}
          <Button
            onClick={confirm}
            disabled={busy || selected.size === 0 || blockedSelection.length > 0}
          >
            Confirm {selected.size} course{selected.size === 1 ? '' : 's'}
          </Button>
        </div>
      </div>
    </div>
  )
}
