import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { Badge, ErrorBox, Loading, PageHeader } from '../components/ui'
import { formatDay, formatINR, titleCase } from '../format'

export function WeddingsPage() {
  const weddings = useQuery({
    queryKey: ['weddings'],
    queryFn: () => unwrap(api.GET('/api/v1/weddings')),
  })

  return (
    <>
      <PageHeader title="Weddings" subtitle="All weddings your company is planning" />
      <ErrorBox error={weddings.error} />
      {weddings.isPending && <Loading />}
      <div className="grid cards">
        {weddings.data?.map((w) => (
          <Link key={w.id} to={`/weddings/${w.id}`} className="card wedding-card">
            <div className="wedding-card-top">
              <h2>{w.name}</h2>
              {w.is_synthetic && <Badge>synthetic</Badge>}
            </div>
            <p className="muted">{w.couple_names}</p>
            <dl className="facts">
              <div>
                <dt>City</dt>
                <dd>{w.city}</dd>
              </div>
              <div>
                <dt>Dates</dt>
                <dd>
                  {formatDay(w.start_date)} – {formatDay(w.end_date)}
                </dd>
              </div>
              <div>
                <dt>Budget</dt>
                <dd>{formatINR(w.total_budget)}</dd>
              </div>
              <div>
                <dt>Status</dt>
                <dd>{titleCase(w.status)}</dd>
              </div>
            </dl>
          </Link>
        ))}
        {weddings.data?.length === 0 && <p className="muted">No weddings yet.</p>}
      </div>
    </>
  )
}
