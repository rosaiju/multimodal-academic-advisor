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
      className={`rounded-2xl border border-slate-200/80 bg-white shadow-[var(--shadow-card)] ${className}`}
    >
      {(title || action) && (
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-slate-100 px-5 py-4 sm:px-6">
          <div>
            {title && (
              <h2 className="text-[1.05rem] font-bold tracking-tight text-slate-900">{title}</h2>
            )}
            {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="px-5 py-4 sm:px-6 sm:py-5">{children}</div>
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
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${meta.className}`}
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
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${meta.className}`}
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
      'bg-gradient-to-b from-brand-600 to-brand-700 text-white shadow-sm shadow-brand-900/20 hover:from-brand-500 hover:to-brand-600 disabled:from-slate-300 disabled:to-slate-300 disabled:text-slate-500 disabled:shadow-none',
    secondary:
      'bg-white text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50 hover:ring-slate-400 disabled:text-slate-400',
    ghost: 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:text-slate-400',
  }
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition active:scale-[0.98] disabled:cursor-not-allowed disabled:active:scale-100 ${styles[variant]} ${className}`}
      {...props}
    />
  )
}

export function Alert({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-sky-200 border-l-sky-500 bg-sky-50/90 text-sky-950',
    warning: 'border-amber-200 border-l-amber-500 bg-amber-50/90 text-amber-950',
    error: 'border-rose-200 border-l-rose-500 bg-rose-50/90 text-rose-950',
  }
  return (
    <div className={`rounded-xl border border-l-4 px-4 py-3 text-sm leading-relaxed ${tones[tone]}`}>
      {title && <p className="font-semibold">{title}</p>}
      <div className={title ? 'mt-1' : ''}>{children}</div>
    </div>
  )
}

/** Circular progress. Percent is against the WHOLE degree, not encoded blocks. */
/**
 * A ring showing progress toward ONE named quantity.
 *
 * `caption` is required and has no default on purpose. This used to read
 * "complete" whatever it was measuring, so a student who had earned more credit
 * than the degree requires saw a full circle over the word "complete" and read it
 * as a finished degree. What the ring measures has to be said next to the number.
 *
 * `percent` may exceed 100: the arc caps, the printed figure does not, because
 * rounding 115% down to 100% is the same lie in smaller type.
 */
export function ProgressRing({ percent, caption, detail, size = 148 }) {
  const stroke = 12
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const clamped = Math.max(0, Math.min(100, percent))
  const shown = Math.max(0, percent)
  return (
    <figure className="m-0 flex w-full max-w-60 flex-col items-center gap-2 sm:w-auto">
      <div className="relative shrink-0" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <defs>
            <linearGradient id="ring-gradient" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="var(--color-brand-500)" />
              <stop offset="100%" stopColor="var(--color-accent-400)" />
            </linearGradient>
          </defs>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            strokeWidth={stroke}
            className="fill-none stroke-brand-100"
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - clamped / 100)}
            stroke="url(#ring-gradient)"
            className="fill-none transition-[stroke-dashoffset] duration-1000 ease-out"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center px-3 text-center">
          <span className="font-display text-3xl font-extrabold tabular-nums text-brand-900">
            {shown.toFixed(0)}%
          </span>
          {/* Inside the ring there is room for two short words at phone width. */}
          <span className="text-[0.65rem] font-medium uppercase leading-tight tracking-wide text-slate-500">
            of credits
          </span>
        </div>
      </div>
      <figcaption className="text-balance text-center text-xs leading-snug text-slate-600">
        <span className="font-medium text-slate-900">{caption}</span>
        {detail && <span className="mt-0.5 block text-slate-500">{detail}</span>}
      </figcaption>
    </figure>
  )
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="flex items-center gap-3 text-sm text-slate-500">
      <span className="size-4 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" />
      {label}
    </div>
  )
}

export function EmptyState({ children }) {
  return <p className="py-6 text-center text-sm text-slate-500">{children}</p>
}

const ROW_STATUS = {
  in_progress: {
    label: 'In progress',
    className: 'bg-sky-50 text-sky-700 ring-sky-600/20',
  },
  placeholder: {
    label: 'Not a course',
    className: 'bg-slate-100 text-slate-600 ring-slate-500/20',
  },
  needs_field: {
    label: 'Needs a field',
    className: 'bg-rose-50 text-rose-700 ring-rose-600/20',
  },
  transfer: {
    label: 'Transfer',
    className: 'bg-violet-50 text-violet-700 ring-violet-600/20',
  },
}

/** A small marker for what KIND of row this is, beside the confidence badge. */
export function RowStatusBadge({ value }) {
  const meta = ROW_STATUS[value]
  if (!meta) return null
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${meta.className}`}
    >
      {meta.label}
    </span>
  )
}

/**
 * "Computed by the engine" - the provenance mark for screens whose facts come
 * from the deterministic degree engine (catalog + confirmed record), with no
 * language model involved. Same wording as the advisor chat's engine label, so
 * a student learns one phrase for "this was calculated, not generated".
 */
export function EngineBadge({ children = 'Computed by the engine' }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-600/20">
      {children}
    </span>
  )
}
