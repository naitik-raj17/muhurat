import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { ErrorBox, Loading, Meter, PageHeader, Stat } from '../components/ui'
import { formatDay, formatINR, formatTime, titleCase } from '../format'

export function OverviewPage() {
  const { weddingId } = useParams()
  const overview = useQuery({
    queryKey: ['overview', weddingId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}/overview', {
          params: { path: { wedding_id: weddingId! } },
        }),
      ),
  })
  if (overview.isPending) return <Loading />
  if (overview.error) return <ErrorBox error={overview.error} />
  const o = overview.data
  const w = o.wedding
  const responded =
    (o.rsvp_counts.accepted ?? 0) + (o.rsvp_counts.declined ?? 0) + (o.rsvp_counts.maybe ?? 0)
  const totalRsvps = responded + (o.rsvp_counts.pending ?? 0)
  const spent = Number(o.budget.actual) / Number(o.budget.total_budget)

  return (
    <>
      <PageHeader
        title={w.name}
        subtitle={`${w.couple_names} · ${w.city} · ${formatDay(w.start_date)} – ${formatDay(w.end_date)}`}
      />
      <section className="grid stats">
        <Stat label="Guests" value={o.guest_count} hint={`${o.wedding.expected_guests} expected`} />
        <Stat
          label="RSVPs answered"
          value={totalRsvps ? `${Math.round((responded / totalRsvps) * 100)}%` : '–'}
          hint={`${o.rsvp_counts.pending ?? 0} invitations pending`}
        />
        <Stat
          label="Vendors"
          value={`${o.selected_vendor_count} selected`}
          hint={`${o.shortlist_count} on shortlist`}
        />
        <Stat
          label="Spent"
          value={formatINR(o.budget.actual)}
          hint={`of ${formatINR(o.budget.total_budget)}`}
        />
      </section>
      <section className="card">
        <h2>Budget used</h2>
        <Meter fraction={spent} alert={spent >= 0.9} />
        <p className="muted small">
          {formatINR(o.budget.remaining)} remaining · {formatINR(o.budget.planned)} planned
        </p>
      </section>
      <div className="grid two">
        <section className="card">
          <h2>Events</h2>
          <ul className="list">
            {o.events.map((e) => (
              <li key={e.id}>
                <strong>{e.name}</strong>
                <span className="muted">
                  {formatDay(e.start_at)}, {formatTime(e.start_at)}–{formatTime(e.end_at)} ·{' '}
                  {e.venue_name}
                </span>
              </li>
            ))}
          </ul>
        </section>
        <section className="card">
          <h2>Upcoming</h2>
          <ul className="list">
            {o.upcoming.map((t, i) => (
              <li key={i}>
                <strong>{t.title}</strong>
                <span className="muted">
                  {titleCase(t.kind)}
                  {t.due ? ` · ${formatDay(t.due)} ${formatTime(t.due)}` : ''}
                </span>
              </li>
            ))}
            {o.upcoming.length === 0 && <li className="muted">Nothing due.</li>}
          </ul>
        </section>
      </div>
    </>
  )
}
