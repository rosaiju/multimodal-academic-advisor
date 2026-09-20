/**
 * Backend client.
 *
 * Every function here maps to a real FastAPI endpoint - there is NO mock data in
 * this application. If the backend is not running the UI says so plainly rather
 * than showing invented coursework, which would defeat the point of a system
 * built around telling verified data apart from guesses.
 *
 * Requests go to /api/* and Vite proxies them to the backend in development.
 */

const BASE = '/api'

async function request(path, options = {}) {
  let response
  try {
    response = await fetch(`${BASE}${path}`, options)
  } catch {
    throw new Error(
      'Cannot reach the backend. Start it with:  uvicorn app.main:app --reload',
    )
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      /* non-JSON error body; the status line is all we have */
    }
    const error = new Error(detail)
    error.status = response.status
    throw error
  }
  return response.json()
}

/** GET /health - also reports which transcript formats are currently readable. */
export const getHealth = () => request('/health')

/** GET /programs - every degree the engine can audit against. */
export const getPrograms = () => request('/programs')

/**
 * POST /ingest/transcript
 * Returns extracted rows. Stores NOTHING - the response carries stored: false.
 */
export function uploadTranscript(file, programId) {
  const form = new FormData()
  form.append('file', file)
  if (programId) form.append('program_id', programId)
  return request('/ingest/transcript', { method: 'POST', body: form })
}

/**
 * POST /ingest/confirm
 * `items` are { extracted, term?, grade?, credits? } - the extracted row goes
 * back verbatim and any correction travels beside it, so the record keeps both.
 */
export function confirmCourses({ studentId, programId, sourceName, extractor, items }) {
  return request('/ingest/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      student_id: studentId,
      program_id: programId,
      source_name: sourceName,
      extractor,
      courses: items,
    }),
  })
}

/** GET /students/{id}/audit - the full degree audit. */
export const getAudit = (studentId) => request(`/students/${studentId}/audit`)

/** GET /students/{id}/audit/summary - headline numbers plus the coverage caveat. */
export const getAuditSummary = (studentId) => request(`/students/${studentId}/audit/summary`)

/** GET /students/{id}/plan - gaps, recommendations, blocked courses. */
export const getPlan = (studentId, limit = 8) =>
  request(`/students/${studentId}/plan?limit=${limit}`)

/** GET /students/{id}/record - confirmed coursework with its audit trail. */
export const getRecord = (studentId) => request(`/students/${studentId}/record`)

/** DELETE /students/{id}/record - used by the demo reset button. */
export const deleteRecord = (studentId) =>
  request(`/students/${studentId}/record`, { method: 'DELETE' })
