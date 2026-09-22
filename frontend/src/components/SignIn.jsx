import { useState } from 'react'
import { Alert, Button, Card, Spinner } from './ui'
import { login, register } from '../api'

/**
 * Sign in or create an account.
 *
 * There is no student-id field, and that absence is the point. The id is issued
 * by the server and learned from GET /auth/me; a form that let someone type one
 * would let them type somebody else's.
 */
export default function SignIn({ onSignedIn }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const registering = mode === 'register'

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const user = registering
        ? await register({ email, password, name })
        : await login({ email, password })
      onSignedIn(user)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-md">
      <Card
        title={registering ? 'Create an account' : 'Sign in'}
        subtitle={
          registering
            ? 'Your coursework is stored against this account and no other.'
            : 'Your degree progress is private to your account.'
        }
      >
        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-slate-700">Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none"
              placeholder="you@morgan.edu"
            />
          </label>

          {registering && (
            <label className="block">
              <span className="text-sm font-medium text-slate-700">
                Name <span className="font-normal text-slate-400">(optional)</span>
              </span>
              <input
                type="text"
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none"
              />
            </label>
          )}

          <label className="block">
            <span className="text-sm font-medium text-slate-700">Password</span>
            <input
              type="password"
              required
              minLength={registering ? 10 : undefined}
              autoComplete={registering ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-900 focus:outline-none"
            />
            {registering && (
              <span className="mt-1 block text-xs text-slate-500">
                At least 10 characters. A few words you will remember beats a short password
                with punctuation in it.
              </span>
            )}
          </label>

          {error && (
            <Alert tone="error" title={registering ? 'Could not register' : 'Could not sign in'}>
              {error}
            </Alert>
          )}

          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => {
                setMode(registering ? 'login' : 'register')
                setError(null)
              }}
              className="text-sm font-medium text-slate-700 underline underline-offset-2"
            >
              {registering ? 'I already have an account' : 'Create an account'}
            </button>
            <div className="flex items-center gap-3">
              {busy && <Spinner label={registering ? 'Creating…' : 'Signing in…'} />}
              <Button type="submit" disabled={busy}>
                {registering ? 'Create account' : 'Sign in'}
              </Button>
            </div>
          </div>
        </form>
      </Card>

      <p className="mt-4 text-center text-xs text-slate-500">
        Signing out, or refreshing this page, ends the session. The access token is kept in memory
        only — never in browser storage, where any script on the page could read it.
      </p>
    </div>
  )
}
