import type { ReactNode } from 'react'
import { errorMessage } from '../api/client'

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <div className="error-box" role="alert">
      {errorMessage(error)}
    </div>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p className="muted" aria-busy="true">
      {label}
    </p>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}

type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'ai' | 'human'

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

/** Every action in the UI says whether a person or the AI did it. */
export function ActorBadge({ actor }: { actor: string }) {
  if (actor === 'ai') return <Badge tone="ai">AI</Badge>
  if (actor === 'human') return <Badge tone="human">Human</Badge>
  return <Badge>System</Badge>
}

export function RsvpBadge({ status }: { status: string }) {
  const tone: Tone =
    status === 'accepted'
      ? 'good'
      : status === 'declined'
        ? 'bad'
        : status === 'maybe'
          ? 'warn'
          : 'neutral'
  return <Badge tone={tone}>{status}</Badge>
}

export function Meter({ fraction, alert }: { fraction: number | null; alert?: boolean }) {
  const width = Math.min(100, Math.max(0, (fraction ?? 0) * 100))
  return (
    <div
      className={`meter ${alert ? 'meter-alert' : ''}`}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(width)}
    >
      <div className="meter-fill" style={{ width: `${width}%` }} />
    </div>
  )
}
