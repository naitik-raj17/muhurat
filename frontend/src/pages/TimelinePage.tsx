import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { useAuth } from '../auth'
import { ActorBadge, Badge, ErrorBox, Loading, PageHeader } from '../components/ui'
import { formatDay, formatTime, titleCase } from '../format'
import { groupByDay } from '../timeline'

export function TimelinePage() {
  const { weddingId } = useParams()
  const qc = useQueryClient()
  const { can } = useAuth()
  const timeline = useQuery({
    queryKey: ['timeline', weddingId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}/timeline', {
          params: { path: { wedding_id: weddingId! } },
        }),
      ),
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ['timeline', weddingId] })
  const draft = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/weddings/{wedding_id}/timeline/draft', {
          params: { path: { wedding_id: weddingId! } },
          body: { replace_existing: true },
        }),
      ),
    onSuccess: refresh,
  })
  const lock = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/weddings/{wedding_id}/timeline/lock', {
          params: { path: { wedding_id: weddingId! } },
        }),
      ),
    onSuccess: refresh,
  })

  const conflicts = timeline.data?.conflicts ?? []
  const conflictIds = new Map<string, string>()
  for (const c of conflicts) for (const id of c.item_ids) conflictIds.set(id, c.severity)
  const titleOf = new Map(timeline.data?.items.map((i) => [i.id, i.title]))

  return (
    <>
      <PageHeader
        title="Timeline"
        subtitle="Drafted from event templates with buffers; conflicts are checked on every view."
        actions={
          can('timeline:write') && (
            <>
              <button type="button" onClick={() => draft.mutate()} disabled={draft.isPending}>
                {draft.isPending ? 'Drafting…' : 'Redraft (keeps locked)'}
              </button>
              <button type="button" className="primary" onClick={() => lock.mutate()}>
                Lock timeline
              </button>
            </>
          )
        }
      />
      <ErrorBox error={timeline.error ?? draft.error ?? lock.error} />
      {timeline.isPending && <Loading />}

      {conflicts.length > 0 && (
        <section className="card conflicts" aria-label="Conflicts">
          <h2>
            {conflicts.length} conflict{conflicts.length === 1 ? '' : 's'}
          </h2>
          <ul className="list">
            {conflicts.map((c, i) => (
              <li key={i} data-testid="conflict">
                <Badge tone={c.severity === 'error' ? 'bad' : 'warn'}>{titleCase(c.kind)}</Badge>{' '}
                {c.message}
                {c.suggestion && (
                  <div className="muted small">
                    Suggested fix: shift “{titleOf.get(String(c.suggestion.item_id)) ?? 'item'}” by{' '}
                    {String(c.suggestion.shift_minutes)} min ({String(c.suggestion.reason)})
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {timeline.data && timeline.data.items.length === 0 && (
        <p className="muted">No timeline yet. Draft one from the wedding's events.</p>
      )}
      {groupByDay(timeline.data?.items ?? []).map(([day, items]) => (
        <section key={day} className="day">
          <h2>{formatDay(day)}</h2>
          <ol className="timeline">
            {items.map((i) => (
              <li
                key={i.id}
                className={`tl-item tl-${i.kind} ${conflictIds.has(i.id) ? `tl-conflict tl-${conflictIds.get(i.id)}` : ''}`}
                data-testid="timeline-item"
              >
                <div className="tl-time">
                  {formatTime(i.start_at)}–{formatTime(i.end_at)}
                </div>
                <div className="tl-body">
                  <strong>{i.title}</strong>
                  <div className="muted small">
                    {i.location}
                    {i.vendor_category ? ` · ${titleCase(i.vendor_category)}` : ''}
                    {i.vendor_id ? ' (booked)' : ''}
                    {i.buffer_after_minutes ? ` · ${i.buffer_after_minutes} min buffer` : ''}
                  </div>
                </div>
                <div className="tl-tags">
                  <Badge tone={i.status === 'locked' ? 'good' : 'neutral'}>{i.status}</Badge>
                  <ActorBadge actor={i.created_by_actor} />
                </div>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </>
  )
}
