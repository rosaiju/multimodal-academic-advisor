import { useRef, useState } from 'react'
import { Alert, Button, Card, Spinner } from './ui'
import { uploadTranscript } from '../api'

/**
 * Step 1 - upload a transcript.
 *
 * Uploading stores nothing; the backend response carries stored: false and this
 * screen says so before the student picks a file, so nobody is surprised that
 * their coursework has not been saved yet.
 */
export default function UploadStep({ programs, programId, onProgramChange, health, onExtracted }) {
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef(null)

  const scansOff = health && !health.accepts_scanned_transcripts

  async function submit() {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const result = await uploadTranscript(file, programId)
      onExtracted(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function choose(selected) {
    if (!selected) return
    setFile(selected)
    setError(null)
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <Card
        title="Upload your transcript"
        subtitle="Nothing is saved yet. You will review everything we read before it counts."
      >
        <label className="mb-4 block">
          <span className="mb-1.5 block text-sm font-medium text-slate-700">Degree program</span>
          <select
            value={programId}
            onChange={(e) => onProgramChange(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-slate-900 focus:outline-none"
          >
            {programs.map((p) => (
              <option key={p.program_id} value={p.program_id}>
                {p.program} — {p.institution} ({p.catalog_year})
              </option>
            ))}
          </select>
        </label>

        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            choose(e.dataTransfer.files?.[0])
          }}
          onClick={() => inputRef.current?.click()}
          className={`cursor-pointer rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
            dragging ? 'border-slate-900 bg-slate-50' : 'border-slate-300 hover:border-slate-400'
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            accept=".txt,.csv,.md,.pdf,.png,.jpg,.jpeg"
            onChange={(e) => choose(e.target.files?.[0])}
          />
          {file ? (
            <div>
              <p className="font-medium text-slate-900">{file.name}</p>
              <p className="mt-1 text-sm text-slate-500">
                {(file.size / 1024).toFixed(1)} KB — click to choose a different file
              </p>
            </div>
          ) : (
            <div>
              <p className="font-medium text-slate-700">Drop your transcript here</p>
              <p className="mt-1 text-sm text-slate-500">or click to browse</p>
              <p className="mt-3 text-xs text-slate-400">
                Text and PDF are read exactly. Images need the document reader.
              </p>
            </div>
          )}
        </div>

        <div className="mt-4 flex items-center justify-between">
          {busy ? <Spinner label="Reading your transcript…" /> : <span />}
          <Button onClick={submit} disabled={!file || busy}>
            Read transcript
          </Button>
        </div>
      </Card>

      {error && (
        <Alert tone="error" title="Could not read that file">
          {error}
        </Alert>
      )}

      {scansOff && (
        <Alert tone="warning" title="Scanned and photographed transcripts are unavailable">
          No document-reader API key is configured, so only text and PDF transcripts with a
          text layer can be read right now. Everything else in the demo works.
        </Alert>
      )}

      {health && (
        <p className="text-center text-xs text-slate-400">
          Readers available: {health.transcript_extractors.join(', ')}
        </p>
      )}
    </div>
  )
}
