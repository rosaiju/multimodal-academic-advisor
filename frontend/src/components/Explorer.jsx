import { useState } from 'react'
import { getUnlocks, simulateCourses } from '../api'
import { Alert, Button, Card, Spinner } from './ui'

/**
 * What-if simulator and course unlock explorer.
 *
 * Everything shown here is computed by the degree engine on the server. The
 * what-if audits an in-memory COPY of the record; nothing on this screen can
 * change what is stored, and the banner says so.
 */

const STATUS_STYLE = {
  completed: 'bg-emerald-50 text-emerald-800 ring-emerald-600/20',
  in_progress: 'bg-sky-50 text-sky-800 ring-sky-600/20',
  eligible: 'bg-amber-50 text-amber-900 ring-amber-600/30',
  blocked: 'bg-slate-100 text-slate-600 ring-slate-500/20',
}
const STATUS_LABEL = {
  completed: 'completed',
  in_progress: 'in progress',
  eligible: 'you can take it',
  blocked: 'blocked',
}

export default function Explorer({ hasRecord, onGoToUpload }) {
  if (!hasRecord) {
    return (
      <div className="mx-auto max-w-3xl">
        <Alert tone="warning" title="No confirmed coursework yet">
          <p>The explorer works from your confirmed record. Upload a transcript first.</p>
          <Button variant="secondary" className="mt-3" onClick={onGoToUpload}>
            Upload a transcript
          </Button>
        </Alert>
      </div>
    )
  }
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <WhatIf />
      <Unlocks />
    </div>
  )
}

/** "COSC 220, cosc-281" -> ['COSC220', 'COSC281']. Only the shape; the server checks the catalog. */
function parseCodes(text) {
  const found = text.match(/[A-Za-z]{2,5}[\s-]?\d{3}/g) ?? []
  return found.map((c) => c.replace(/[\s-]/g, '').toUpperCase())
}

function WhatIf() {
  const [text, setText] = useState('')
  const [mode, setMode] = useState('same_term')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const codes = [...new Set(parseCodes(text))]

  async function run(event) {
    event.preventDefault()
    if (codes.length === 0 || busy) return
    setBusy(true)
    setError(null)
    try {
      setResult(await simulateCourses({ courses: codes, mode }))
    } catch (err) {
      setResult(null)
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="What if I take…?"
      subtitle="Try courses on a temporary copy of your record. Nothing is saved or changed."
    >
      <form onSubmit={run} className="space-y-3">
        <div className="flex gap-2">
          <label className="sr-only" htmlFor="whatif-courses">
            Courses to try
          </label>
          <input
            id="whatif-courses"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="COSC 220, COSC 281"
            maxLength={200}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900"
          />
          <Button type="submit" disabled={busy || codes.length === 0}>
            Run what-if
          </Button>
        </div>
        <fieldset className="flex flex-wrap gap-4 text-sm text-slate-700">
          <legend className="sr-only">How to treat these courses</legend>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="whatif-mode"
              checked={mode === 'same_term'}
              onChange={() => setMode('same_term')}
            />
            Together, next term
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="whatif-mode"
              checked={mode === 'completed'}
              onChange={() => setMode('completed')}
            />
            Once I have passed all of them
          </label>
        </fieldset>
        {codes.length > 0 && (
          <p className="text-xs text-slate-500">Trying: {codes.join(', ')}</p>
        )}
      </form>

      {busy && (
        <div className="mt-4">
          <Spinner label="Re-running the audit on a copy…" />
        </div>
      )}
      {error && (
        <div className="mt-4">
          <Alert tone="error" title="Could not run the what-if">
            <p>{error}</p>
          </Alert>
        </div>
      )}
      {result && !busy && <WhatIfResult result={result} />}
    </Card>
  )
}

function Stat({ label, before, after }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-sm font-semibold text-slate-900">
        {before} <span className="font-normal text-slate-400">→</span> {after}
      </p>
    </div>
  )
}

function CodeList({ items, empty }) {
  if (items.length === 0) return <p className="text-sm text-slate-500">{empty}</p>
  return (
    <ul className="flex flex-wrap gap-2">
      {items.map((item) => (
        <li
          key={item.key}
          className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-800"
          title={item.title}
        >
          {item.label}
        </li>
      ))}
    </ul>
  )
}

function WhatIfResult({ result }) {
  const moved = result.changed_blocks.filter(
    (b) => !result.newly_satisfied_blocks.some((s) => s.block_id === b.block_id),
  )
  return (
    <div className="mt-5 space-y-4">
      <Alert tone="info" title="This is a simulation">
        <p>Your stored record was not changed. These figures come from the degree engine.</p>
      </Alert>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Stat
          label="Credits earned"
          before={result.before.credits_earned}
          after={result.after.credits_earned}
        />
        <Stat
          label="Credits still needed"
          before={result.before.credits_remaining}
          after={result.after.credits_remaining}
        />
        <Stat
          label="Credit progress"
          before={`${result.before.credit_progress_percent}%`}
          after={`${result.after.credit_progress_percent}%`}
        />
      </div>

      <Section title="Applied to the simulation">
        <CodeList
          empty="No course could be applied."
          items={result.applied.map((c) => ({
            key: c.code,
            label: `${c.code} · ${c.credits} cr`,
            title: c.title,
          }))}
        />
      </Section>

      {result.skipped.length > 0 && (
        <Section title="Not simulated">
          <ul className="space-y-1 text-sm text-slate-700">
            {result.skipped.map((s) => (
              <li key={s.code}>
                <span className="font-medium">{s.code}</span> — {s.detail}
                {s.missing_prerequisites.length > 0 &&
                  ` (missing ${s.missing_prerequisites.join(', ')})`}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Requirements newly satisfied">
        <CodeList
          empty="None."
          items={result.newly_satisfied_blocks.map((b) => ({ key: b.block_id, label: b.name }))}
        />
      </Section>
      {moved.length > 0 && (
        <Section title="Requirements that move forward">
          <CodeList items={moved.map((b) => ({ key: b.block_id, label: b.name }))} />
        </Section>
      )}

      <Section title="Newly eligible courses">
        <CodeList
          empty="No new courses become eligible."
          items={result.newly_unlocked.map((c) => ({
            key: c.code,
            label: c.code,
            title: c.title,
          }))}
        />
      </Section>

      {result.still_blocked.length > 0 && (
        <Section title="Still blocked">
          <ul className="space-y-1 text-sm text-slate-700">
            {result.still_blocked.slice(0, 6).map((b) => (
              <li key={b.course.code}>
                <span className="font-medium">{b.course.code}</span> — needs{' '}
                {b.missing_prerequisites.join(', ')}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {result.warnings.map((w) => (
        <p key={w} className="text-xs text-slate-500">
          {w}
        </p>
      ))}
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h3>
      {children}
    </div>
  )
}

function Unlocks() {
  const [text, setText] = useState('')
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function lookup(code) {
    setBusy(true)
    setError(null)
    try {
      setReport(await getUnlocks(code))
    } catch (err) {
      setReport(null)
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function submit(event) {
    event.preventDefault()
    const [code] = parseCodes(text)
    if (code && !busy) lookup(code)
  }

  return (
    <Card
      title="What does a course unlock?"
      subtitle="Follow the prerequisite graph: what a course needs, what it opens, and where you stand."
    >
      <form onSubmit={submit} className="flex gap-2">
        <label className="sr-only" htmlFor="unlock-course">
          Course
        </label>
        <input
          id="unlock-course"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="COSC 241"
          maxLength={20}
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-900"
        />
        <Button type="submit" disabled={busy || parseCodes(text).length === 0}>
          Explore
        </Button>
      </form>

      {busy && (
        <div className="mt-4">
          <Spinner label="Walking the prerequisite graph…" />
        </div>
      )}
      {error && (
        <div className="mt-4">
          <Alert tone="error" title="Could not look that up">
            <p>{error}</p>
          </Alert>
        </div>
      )}
      {report && !busy && !report.known && (
        <div className="mt-4">
          <Alert tone="warning" title={`${report.code} is not in the encoded catalog`}>
            <p>I will not guess what an unknown course needs or unlocks.</p>
          </Alert>
        </div>
      )}
      {report?.known && !busy && <UnlockReport report={report} onPick={lookup} />}
    </Card>
  )
}

function StatusPill({ status }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_STYLE[status] ?? STATUS_STYLE.blocked}`}
    >
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

function UnlockReport({ report, onPick }) {
  return (
    <div className="mt-5 space-y-5">
      <div>
        <p className="text-sm font-semibold text-slate-900">
          {report.course.code} · {report.course.title}
        </p>
        <p className="mt-1 text-sm text-slate-600">
          For you: <StatusPill status={report.status} />
        </p>
      </div>

      <Section title="What it needs">
        {report.prerequisite_tree.children.length === 0 ? (
          <p className="text-sm text-slate-500">No prerequisites.</p>
        ) : (
          <ul role="tree" aria-label="Prerequisite tree">
            <TreeNode node={report.prerequisite_tree} root />
          </ul>
        )}
      </Section>

      <Section title="What it directly unlocks">
        {report.direct_unlocks.length === 0 ? (
          <p className="text-sm text-slate-500">
            Not a prerequisite for any course in the encoded catalog.
          </p>
        ) : (
          <ul className="space-y-2">
            {report.direct_unlocks.map((entry) => (
              <UnlockRow key={entry.course.code} entry={entry} onPick={onPick} />
            ))}
          </ul>
        )}
      </Section>

      {report.downstream_unlocks.length > 0 && (
        <Section title="Further down the chain">
          <ul className="space-y-2">
            {report.downstream_unlocks.map((entry) => (
              <UnlockRow key={entry.course.code} entry={entry} onPick={onPick} />
            ))}
          </ul>
        </Section>
      )}

      <p className="text-xs text-slate-500">
        From the catalog&apos;s prerequisite graph. It does not know which sections run in
        a given term.
      </p>
    </div>
  )
}

function UnlockRow({ entry, onPick }) {
  let note = 'you can take it now'
  if (entry.status === 'completed') note = 'already completed'
  else if (entry.status === 'in_progress') note = 'in progress now'
  else if (entry.missing_prerequisites.length > 0 && entry.missing_after.length === 0)
    note = 'this course is all you are missing'
  else if (entry.missing_prerequisites.length > 0)
    note = `would still need ${entry.missing_after.join(', ')}`
  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <button
        type="button"
        onClick={() => onPick(entry.course.code)}
        className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-2 hover:decoration-slate-900"
      >
        {entry.course.code}
      </button>
      <span className="text-slate-600">{entry.course.title}</span>
      <StatusPill status={entry.status} />
      <span className="text-xs text-slate-500">{note}</span>
      {entry.depth > 1 && (
        <span className="text-xs text-slate-400">via {entry.path.slice(0, -1).join(' → ')}</span>
      )}
    </li>
  )
}

/**
 * A prerequisite tree as nested lists: the course at the top, what it needs
 * beneath, and so on down to the courses with no prerequisites. An "any of" node
 * means one of its children is enough. Plain nested <ul> so it works at any
 * width and reads correctly in a screen reader, with no graph library.
 */
function TreeNode({ node, root = false }) {
  if (node.any_of) {
    return (
      <li role="treeitem" aria-expanded="true" className="mt-1.5">
        <span className="text-xs font-medium italic text-slate-500">any one of</span>
        <ul className="ml-4 border-l border-dashed border-slate-300 pl-3">
          {node.children.map((child, i) => (
            <TreeNode key={child.code ?? `any-${i}`} node={child} />
          ))}
        </ul>
      </li>
    )
  }
  return (
    <li role="treeitem" aria-expanded={node.children.length > 0} className="mt-1.5">
      <span className={`inline-flex flex-wrap items-center gap-2 ${root ? 'font-semibold' : ''}`}>
        <span className="text-sm text-slate-900">{node.code}</span>
        {node.title && <span className="text-xs text-slate-500">{node.title}</span>}
        {!root && node.status && <StatusPill status={node.status} />}
      </span>
      {node.children.length > 0 && (
        <ul className="ml-4 border-l border-slate-300 pl-3">
          {node.children.map((child, i) => (
            <TreeNode key={child.code ?? `any-${i}`} node={child} />
          ))}
        </ul>
      )}
    </li>
  )
}
