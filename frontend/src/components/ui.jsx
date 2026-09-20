/**
 * Shared presentation pieces.
 *
 * The provenance and confidence badges are not decoration. The project
 * requirement is that a student can always tell verified catalog data from
 * something a model read off a document, so those labels appear wherever the
 * underlying data carries them.
 */

export function Card({ title, subtitle, action, children, className = '' }) {
  return (
    <section
      className={`rounded-xl border border-slate-200 bg-white shadow-sm ${className}`}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            {title && <h2 className="font-semibold text-slate-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

const PROVENANCE = {
  verified: {
    label: 'From official catalog',
    className: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  },
  student_confirmed: {
    label: 'Confirmed by you',
    className: 'bg-sky-50 text-sky-700 ring-sky-600/20',
  },
  unverified_extraction: {
    label: 'Not yet confirmed',
    className: 'bg-amber-50 text-amber-800 ring-amber-600/30',
  },
  ai_suggested: {
    label: 'AI suggestion — verify with your advisor',
    className: 'bg-violet-50 text-violet-700 ring-violet-600/20',
  },
}

export function ProvenanceBadge({ value }) {
  const meta = PROVENANCE[value] ?? {
    label: value,
    className: 'bg-slate-100 text-slate-600 ring-slate-500/20',
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${meta.className}`}
    >
      {meta.label}
    </span>
  )
}

const CONFIDENCE = {
  high: { label: 'Read exactly', className: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' },
  medium: { label: 'Check this', className: 'bg-amber-50 text-amber-800 ring-amber-600/30' },
  low: { label: 'Incomplete', className: 'bg-rose-50 text-rose-700 ring-rose-600/20' },
}

export function ConfidenceBadge({ value }) {
  const meta = CONFIDENCE[value] ?? CONFIDENCE.low
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${meta.className}`}
    >
      {meta.label}
    </span>
  )
}

const STATUS = {
  satisfied: { label: 'Complete', className: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20' },
  in_progress: { label: 'In progress', className: 'bg-sky-50 text-sky-700 ring-sky-600/20' },
  unmet: { label: 'Not started', className: 'bg-slate-100 text-slate-600 ring-slate-500/20' },
  needs_advisor: {
    label: 'Needs advisor',
    className: 'bg-violet-50 text-violet-700 ring-violet-600/20',
  },
}

export function StatusBadge({ value }) {
  const meta = STATUS[value] ?? STATUS.unmet
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${meta.className}`}
    >
      {meta.label}
    </span>
  )
}

export function Button({ variant = 'primary', className = '', ...props }) {
  const styles = {
    primary:
      'bg-slate-900 text-white hover:bg-slate-800 disabled:bg-slate-300 disabled:text-slate-500',
    secondary:
      'bg-white text-slate-700 ring-1 ring-inset ring-slate-300 hover:bg-slate-50 disabled:text-slate-400',
    ghost: 'text-slate-600 hover:bg-slate-100 disabled:text-slate-400',
  }
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed ${styles[variant]} ${className}`}
      {...props}
    />
  )
}

export function Alert({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-sky-200 bg-sky-50 text-sky-900',
    warning: 'border-amber-200 bg-amber-50 text-amber-900',
    error: 'border-rose-200 bg-rose-50 text-rose-900',
  }
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${tones[tone]}`}>
      {title && <p className="font-semibold">{title}</p>}
      <div className={title ? 'mt-1' : ''}>{children}</div>
    </div>
  )
}

/** Circular progress. Percent is against the WHOLE degree, not encoded blocks. */
export function ProgressRing({ percent, size = 132 }) {
  const stroke = 11
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const clamped = Math.max(0, Math.min(100, percent))
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          strokeWidth={stroke}
          className="fill-none stroke-slate-200"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - clamped / 100)}
          className="fill-none stroke-slate-900 transition-[stroke-dashoffset] duration-700"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-semibold tabular-nums text-slate-900">
          {clamped.toFixed(1)}%
        </span>
        <span className="text-xs text-slate-500">complete</span>
      </div>
    </div>
  )
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="flex items-center gap-3 text-sm text-slate-500">
      <span className="size-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
      {label}
    </div>
  )
}

export function EmptyState({ children }) {
  return <p className="py-6 text-center text-sm text-slate-500">{children}</p>
}
