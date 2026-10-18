import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { ErrorBox } from '../components/ui'

const DEMO_USERS = [
  'planner@shubh-vivah.example',
  'admin@shubh-vivah.example',
  'viewer@shubh-vivah.example',
  'client.jaipur@shubh-vivah.example',
  'planner@royal-knots.example',
]

export function LoginPage() {
  const { me, login } = useAuth()
  const location = useLocation()
  const [email, setEmail] = useState(DEMO_USERS[0])
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  if (me) {
    const from = (location.state as { from?: string } | null)?.from ?? '/weddings'
    return <Navigate to={from} replace />
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      <form className="card login-card" onSubmit={onSubmit} aria-label="Sign in">
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden>
            ✦
          </span>
          Wedding AI
        </div>
        <p className="muted">Planning workspace for wedding companies</p>
        <label>
          Email
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            list="demo-users"
            required
          />
          <datalist id="demo-users">
            {DEMO_USERS.map((u) => (
              <option key={u} value={u} />
            ))}
          </datalist>
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <ErrorBox error={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="muted small">
          Seeded demo users share the password <code>Password123!</code> (synthetic data only).
        </p>
      </form>
    </div>
  )
}
