import { useMemo, useState } from 'react'
import { Alert, Button, Card, ConfidenceBadge, ProvenanceBadge, Spinner } from './ui'
import { confirmCourses } from '../api'

/**
 * Step 2 - review and confirm.
 *
 * This screen is the point of the whole provenance design. Every row arrives
 * tagged "Not yet confirmed" and stays that way until the student ticks it. A
 * row missing a term, grade or credit value cannot be confirmed at all until
 * they supply it - the backend rejects it rather than guessing, and the UI makes
 * the missing field the obvious thing to fix.
 *
 * There is deliberately no "accept everything" button, matching the backend,
 * which has no bulk-accept helper for the same reason.
 */
export default function ReviewStep({ extraction, studentId, programId, onConfirmed, onBack }) {
  const rows = extraction.extraction.courses
  const [selected, setSelected] = useState(() => new Set(rows.map((_, i) => i)))
  const [edits, setEdits] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const valueOf = (index, field) => edits[index]?.[field] ?? rows[index][field] ?? ''

  const incomplete = useMemo(() => {
    const bad = new Set()
    rows.forEach((_, i) => {
      if (!valueOf(i, 'term') || !valueOf(i, 'grade') || valueOf(i, 'credits') === '') bad.add(i)
    })
    return bad
  }, [rows, edits])

  const blockedSelection = [...selected].filter((i) => incomplete.has(i))

  function edit(index, field, value) {
    setEdits((prev) => ({ ...prev, [index]: { ...prev[index], [field]: value } }))
  }

  function toggle(index) {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(index) ? next.delete(index) : next.add(index)
      return next
    })
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
        studentId,
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
                {rows.map((row, i) => (
                  <tr key={i} className={selected.has(i) ? '' : 'opacity-45'}>
                    <td className="py-3">
                      <input
                        type="checkbox"
                        checked={selected.has(i)}
                        onChange={() => toggle(i)}
                        className="size-4 rounded border-slate-300 accent-slate-900"
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
                          onChange={(e) => edit(i, field, e.target.value)}
                          placeholder={field === 'term' ? 'e.g. Fall 2024' : '—'}
                          className={`w-full min-w-24 rounded-md border px-2 py-1 text-sm focus:outline-none ${
                            valueOf(i, field) === ''
                              ? 'border-rose-300 bg-rose-50 focus:border-rose-500'
                              : 'border-slate-300 focus:border-slate-900'
                          }`}
                        />
                      </td>
                    ))}
                    <td className="py-3">
                      <div className="flex flex-col items-start gap-1">
                        <ConfidenceBadge value={row.confidence} />
                        <ProvenanceBadge value={row.provenance} />
                      </div>
                      {row.issues?.length > 0 && (
                        <ul className="mt-1 space-y-0.5 text-xs text-slate-500">
                          {row.issues.map((issue, k) => (
                            <li key={k}>• {issue}</li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
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
        <Alert tone="warning" title="Some selected rows are missing information">
          Fill in the highlighted fields before confirming. We will not guess a grade or
          term, because that decides whether a requirement counts.
        </Alert>
      )}

      <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-5 py-4">
        <div className="text-sm text-slate-600">
          <span className="font-semibold text-slate-900">{selected.size}</span> of {rows.length}{' '}
          selected. Only what you confirm counts toward your degree.
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
