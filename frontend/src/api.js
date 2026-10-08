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

/**
 * The access token, held in memory only.
 *
 * Deliberately NOT localStorage. A token in localStorage is readable by any
 * script that ends up on the page, and it outlives the tab - a shared lab
 * machine would keep someone signed in for whoever sat down next. The cost is
 * that a refresh signs you out, which for a browser demo is the right trade.
 */
let accessToken = null

export function setAccessToken(token) {
  accessToken = token
}

export function clearAccessToken() {
  accessToken = null
}

export function hasAccessToken() {
  return accessToken !== null
}

/** The in-memory token, for the voice socket's first message. Never put in a URL. */
export function getAccessToken() {
  return accessToken
}

async function request(path, options = {}) {
  const headers = { ...(options.headers ?? {}) }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`
  options = { ...options, headers }
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
/*
 * No student_id: the backend takes it from the access token. A client that could
 * name the record it wrote to could write to someone else's.
 */
export function confirmCourses({ programId, sourceName, extractor, items }) {
  return request('/ingest/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
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

// ---- advisor ----

/**
 * GET /advisor/health - whether a chat model is usable right now.
 *
 * Asked on load so the chat panel can say up front that it is running on the
 * degree engine alone, rather than letting a student discover it mid-question.
 */
export const getAdvisorHealth = () => request('/advisor/health')

/**
 * POST /advisor/chat
 *
 * No student id: the backend takes it from the token, the same rule
 * `confirmCourses` follows. `conversationId` groups turns into one session and
 * is scoped server-side to the signed-in student.
 */
export function askAdvisor({ message, conversationId = 'default', useLlm = true }) {
  return request('/advisor/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      use_llm: useLlm,
    }),
  })
}

/**
 * POST /advisor/simulate - audit a COPY of your record with hypothetical courses.
 * Read-only on the server: the stored record is never written.
 */
export const simulateCourses = ({ courses, mode = 'same_term' }) =>
  request('/advisor/simulate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ courses, mode }),
  })

/** GET /advisor/unlocks/{code} - what a course needs, unlocks, and where you stand. */
export const getUnlocks = (code) => request(`/advisor/unlocks/${encodeURIComponent(code)}`)

/** DELETE /advisor/chat/{id} - forget one conversation. */
export const resetConversation = (conversationId = 'default') =>
  request(`/advisor/chat/${encodeURIComponent(conversationId)}`, { method: 'DELETE' })

/**
 * WS /advisor/listen - live voice input.
 *
 * The token is sent as the first message once the socket opens, never in this
 * URL: URLs end up in server and proxy logs. See useLiveTranscription.
 */
export function openLiveTranscription() {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return new WebSocket(`${scheme}://${window.location.host}${BASE}/advisor/listen`)
}

// ---- auth ----

/** POST /auth/register - creates the account AND signs in. */
export async function register({ email, password, name }) {
  const body = await request('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, name: name || null }),
  })
  setAccessToken(body.access_token)
  return body.user
}

/** POST /auth/login */
export async function login({ email, password }) {
  const body = await request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  setAccessToken(body.access_token)
  return body.user
}

/** GET /auth/me - how the app learns its own student id. It never invents one. */
export const getMe = () => request('/auth/me')

/** Local only. The token is stateless, so there is nothing to tell the server. */
export function logout() {
  clearAccessToken()
}
