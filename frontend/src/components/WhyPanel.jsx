import { useEffect, useState } from 'react'
import { Spinner } from './ui'
import { explainBlock } from '../api'

/**
 * "Why is this still open?" for one requirement.
 *
 * Everything shown is computed by the backend from the catalog and the confirmed
 * record. The wording of each reason comes from fixed templates, so a reason can be
 * checked against the transcript line by line.
 */
export default function WhyPanel({ studentId, blockId }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    explainBlock(studentId, blockId)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e.message))
    return () => {
      cancelled = true
    }
  }, [studentId, blockId])

  if (error) return <p className="mt-2 text-sm text-rose-700">{error}</p>
  if (!data) return <Spinner label="Working it out…" />

  return (
    <div className="mt-3 space-y-3 border-t border-slate-200 pt-3 text-sm">
      <p className="font-medium text-slate-900">{data.headline}</p>
      <p className="text-slate-600">
        <span className="font-medium">The requirement:</span> {data.requirement}
      </p>

      {data.counted.length > 0 && (
        <div>
          <p className="font-medium text-slate-800">Counted so far</p>
          <ul className="mt-1 space-y-0.5 text-slate-600">
            {data.counted.map((c) => (
              <li key={c.code}>
                {c.code} · {c.title} · {c.grade} ({c.term})
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.not_counted.length > 0 && (
        <div>
          <p className="font-medium text-slate-800">On your record but not counted</p>
          <ul className="mt-1 space-y-0.5 text-slate-600">
            {data.not_counted.map((n) => (
              <li key={n.code}>
                <span className="font-medium">{n.code}</span> · {n.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.options.length > 0 && (
        <div>
          <p className="font-medium text-slate-800">What could finish it</p>
          <ul className="mt-1 space-y-1 text-slate-600">
            {data.options.slice(0, 8).map((o) => (
              <li key={o.code}>
                <span className="font-medium">{o.code}</span> · {o.title}{' '}
                {o.in_progress ? (
                  <span className="text-sky-700">You're taking it now</span>
                ) : o.eligible_now ? (
                  <span className="text-emerald-700">You can take it now</span>
                ) : (
                  <span className="text-amber-700">
                    Needs {o.missing_prerequisites.join(', ')} first
                  </span>
                )}
                {o.warnings.map((w) => (
                  <span key={w} className="block text-xs text-amber-700">
                    {w}
                  </span>
                ))}
              </li>
            ))}
            {data.options.length > 8 && (
              <li className="text-xs text-slate-500">+{data.options.length - 8} more</li>
            )}
          </ul>
        </div>
      )}

      {data.next_step && (
        <p className="rounded-md bg-emerald-50 px-3 py-2 text-emerald-900">
          <span className="font-medium">Next step:</span> {data.next_step}
        </p>
      )}
      {data.note && <p className="text-xs text-slate-500">{data.note}</p>}
    </div>
  )
}
