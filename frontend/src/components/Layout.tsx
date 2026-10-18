import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet, useParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { useAuth } from '../auth'
import { titleCase } from '../format'

const WEDDING_TABS = [
  { to: '', label: 'Overview', end: true },
  { to: 'vendors', label: 'Vendors' },
  { to: 'guests', label: 'Guests' },
  { to: 'timeline', label: 'Timeline' },
  { to: 'budget', label: 'Budget' },
  { to: 'approvals', label: 'Approvals' },
  { to: 'activity', label: 'Agent activity' },
]

export function Layout() {
  const { me, logout } = useAuth()
  const { weddingId } = useParams()
  const wedding = useQuery({
    queryKey: ['wedding', weddingId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}', {
          params: { path: { wedding_id: weddingId! } },
        }),
      ),
    enabled: Boolean(weddingId),
  })

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            ✦
          </span>
          Wedding&nbsp;AI
        </div>
        <nav aria-label="Main">
          <NavLink to="/weddings" end>
            Weddings
          </NavLink>
          <NavLink to="/approvals" end>
            All approvals
          </NavLink>
          <NavLink to="/activity" end>
            All agent activity
          </NavLink>
          {weddingId && (
            <div className="subnav">
              <div className="subnav-title" title={wedding.data?.name}>
                {wedding.data?.name ?? '…'}
              </div>
              {WEDDING_TABS.map((t) => (
                <NavLink key={t.label} to={`/weddings/${weddingId}/${t.to}`} end={t.end}>
                  {t.label}
                </NavLink>
              ))}
            </div>
          )}
        </nav>
        {me && (
          <div className="whoami">
            <div className="whoami-name">{me.full_name}</div>
            <div className="muted small">
              {titleCase(me.role)} · {me.organization_name}
            </div>
            <button type="button" className="link" onClick={logout}>
              Sign out
            </button>
          </div>
        )}
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
