import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../api/client'
import { useAuth } from '../auth'
import { ErrorBox, Loading, PageHeader, RsvpBadge } from '../components/ui'
import { titleCase } from '../format'

const PAGE = 50

function ImportReport({ report }: { report: Schemas['GuestImportReport'] }) {
  return (
    <div className="card import-report" role="status">
      <strong>
        Imported {report.imported} of {report.total_rows} rows
      </strong>
      <span className="muted">
        {' '}
        · {report.groups_detected} family groups · {report.duplicates_in_file} duplicates in file ·{' '}
        {report.duplicates_existing} already invited
      </span>
      {report.errors.length > 0 && (
        <table className="table compact">
          <thead>
            <tr>
              <th>Row</th>
              <th>Field</th>
              <th>Problem</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {report.errors.slice(0, 50).map((e, i) => (
              <tr key={i}>
                <td>{e.row}</td>
                <td>{e.field}</td>
                <td>{e.message}</td>
                <td className="muted">{e.value ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function GuestsPage() {
  const { weddingId } = useParams()
  const qc = useQueryClient()
  const { can } = useAuth()
  const [search, setSearch] = useState('')
  const [side, setSide] = useState<Schemas['GuestSide'] | ''>('')
  const [status, setStatus] = useState<Schemas['RsvpStatus'] | ''>('')
  const [offset, setOffset] = useState(0)

  const events = useQuery({
    queryKey: ['events', weddingId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}/events', {
          params: { path: { wedding_id: weddingId! } },
        }),
      ),
  })
  const guests = useQuery({
    queryKey: ['guests', weddingId, search, side, status, offset],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}/guests', {
          params: {
            path: { wedding_id: weddingId! },
            query: {
              search: search || undefined,
              side: side || undefined,
              rsvp_status: status || undefined,
              limit: PAGE,
              offset,
            },
          },
        }),
      ),
    placeholderData: (prev) => prev,
  })

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return unwrap(
        api.POST('/api/v1/weddings/{wedding_id}/guests/import', {
          params: { path: { wedding_id: weddingId! } },
          // openapi-fetch passes FormData through untouched.
          body: body as unknown as { file: string },
          bodySerializer: (b) => b as unknown as FormData,
        }),
      )
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['guests', weddingId] }),
  })

  const rsvp = useMutation({
    mutationFn: (v: { guestId: string; eventId: string; status: Schemas['RsvpStatus'] }) =>
      unwrap(
        api.PUT('/api/v1/weddings/{wedding_id}/guests/{guest_id}/rsvp', {
          params: { path: { wedding_id: weddingId!, guest_id: v.guestId } },
          body: { event_id: v.eventId, status: v.status, plus_ones: 0 },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['guests', weddingId] }),
  })

  const eventList = events.data ?? []
  const total = guests.data?.total ?? 0

  return (
    <>
      <PageHeader
        title="Guests"
        subtitle="Import lists, track RSVPs per event. Phone numbers are stored encrypted."
        actions={
          can('guest:write') && (
            <label className="button">
              Import CSV / Excel
              <input
                type="file"
                accept=".csv,.xlsx"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) upload.mutate(f)
                  e.target.value = ''
                }}
              />
            </label>
          )
        }
      />
      <ErrorBox error={upload.error ?? rsvp.error ?? guests.error} />
      {upload.isPending && <Loading label="Importing…" />}
      {upload.data && <ImportReport report={upload.data} />}

      <div className="filters">
        <input
          aria-label="Search guests"
          placeholder="Search name or family"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value)
            setOffset(0)
          }}
        />
        <select
          aria-label="Side"
          value={side}
          onChange={(e) => {
            setSide(e.target.value as typeof side)
            setOffset(0)
          }}
        >
          <option value="">Both sides</option>
          <option value="bride">Bride</option>
          <option value="groom">Groom</option>
          <option value="both">Common</option>
        </select>
        <select
          aria-label="RSVP status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as typeof status)
            setOffset(0)
          }}
        >
          <option value="">Any RSVP</option>
          <option value="pending">Pending</option>
          <option value="accepted">Accepted</option>
          <option value="maybe">Maybe</option>
          <option value="declined">Declined</option>
        </select>
        <span className="muted small">{total} guests</span>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Family</th>
              <th>Side</th>
              <th>Lang</th>
              {eventList.map((ev) => (
                <th key={ev.id}>{ev.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {guests.data?.items.map((g) => (
              <tr key={g.id}>
                <td>
                  {g.name}
                  {g.is_vip && <span title="VIP"> ★</span>}
                  <div className="muted small">{g.phone ?? g.email}</div>
                </td>
                <td className="muted">{g.group_label ?? '–'}</td>
                <td>{titleCase(g.side)}</td>
                <td>{g.language}</td>
                {eventList.map((ev) => {
                  const r = g.rsvps?.find((x) => x.event_id === ev.id)
                  if (!r) return <td key={ev.id}>–</td>
                  return (
                    <td key={ev.id}>
                      {can('rsvp:submit') ? (
                        <select
                          aria-label={`RSVP ${g.name} ${ev.name}`}
                          className={`rsvp rsvp-${r.status}`}
                          value={r.status}
                          onChange={(e) =>
                            rsvp.mutate({
                              guestId: g.id,
                              eventId: ev.id,
                              status: e.target.value as Schemas['RsvpStatus'],
                            })
                          }
                        >
                          <option value="pending">pending</option>
                          <option value="accepted">accepted</option>
                          <option value="maybe">maybe</option>
                          <option value="declined">declined</option>
                        </select>
                      ) : (
                        <RsvpBadge status={r.status} />
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button type="button" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
          Previous
        </button>
        <span className="muted small">
          {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE, total)} of {total}
        </span>
        <button
          type="button"
          disabled={offset + PAGE >= total}
          onClick={() => setOffset(offset + PAGE)}
        >
          Next
        </button>
      </div>
    </>
  )
}
