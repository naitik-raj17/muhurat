import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../api/client'
import { useAuth } from '../auth'
import { ActorBadge, Badge, ErrorBox, Loading, PageHeader } from '../components/ui'
import { formatINR, formatLakh, titleCase } from '../format'

const CATEGORIES: Schemas['VendorCategory'][] = [
  'venue',
  'caterer',
  'decorator',
  'photographer',
  'makeup',
  'mehendi_artist',
  'dj',
  'pandit',
]

type SearchQuery = {
  q: string
  category: Schemas['VendorCategory'] | ''
  city: string
  budget_max: string
  event_date: string
}

export function VendorExplanation({ ex }: { ex: Schemas['MatchExplanation'] }) {
  return (
    <div className="why">
      <div className="why-summary">{ex.summary}</div>
      <ul className="why-list">
        {(ex.matched_styles ?? []).length > 0 && <li>Style: {ex.matched_styles?.join(', ')}</li>}
        {(ex.matched_terms ?? []).length > 0 && (
          <li>Matched words: {ex.matched_terms?.join(', ')}</li>
        )}
        <li>
          Price: {ex.price_fit}
          {ex.price_vs_budget_pct !== null && ex.price_vs_budget_pct !== undefined
            ? ` (starts at ${ex.price_vs_budget_pct}% of budget)`
            : ''}
        </li>
        <li>Availability: {ex.availability}</li>
        <li>Rating: {ex.rating_note}</li>
      </ul>
    </div>
  )
}

export function VendorsPage() {
  const { weddingId } = useParams()
  const qc = useQueryClient()
  const { can } = useAuth()
  const wedding = useQuery({
    queryKey: ['wedding', weddingId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}', { params: { path: { wedding_id: weddingId! } } }),
      ),
  })
  const [form, setForm] = useState<SearchQuery>({
    q: '',
    category: 'decorator',
    city: '',
    budget_max: '',
    event_date: '',
  })
  const [query, setQuery] = useState<SearchQuery | null>(null)
  const city = query?.city || wedding.data?.city || ''

  const results = useQuery({
    queryKey: ['vendor-search', query, city],
    enabled: query !== null && Boolean(city),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/vendors/search', {
          params: {
            query: {
              q: query!.q,
              category: query!.category || undefined,
              city,
              budget_max: query!.budget_max ? Number(query!.budget_max) : undefined,
              event_date: query!.event_date || undefined,
              styles: query!.q ? undefined : wedding.data?.styles,
              limit: 15,
            },
          },
        }),
      ),
  })

  const shortlist = useQuery({
    queryKey: ['shortlist', weddingId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}/shortlist', {
          params: { path: { wedding_id: weddingId! } },
        }),
      ),
  })
  const onShortlist = new Set(shortlist.data?.map((s) => s.vendor_id))

  const add = useMutation({
    mutationFn: (m: Schemas['VendorMatch']) =>
      unwrap(
        api.POST('/api/v1/weddings/{wedding_id}/shortlist', {
          params: { path: { wedding_id: weddingId! } },
          body: {
            vendor_id: m.vendor.id,
            notes: '',
            score: m.score,
            explanation: m.explanation as unknown as Record<string, unknown>,
          },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['shortlist', weddingId] }),
  })

  const decide = useMutation({
    mutationFn: (v: { id: string; status: Schemas['ShortlistStatus'] }) =>
      unwrap(
        api.PATCH('/api/v1/weddings/{wedding_id}/shortlist/{entry_id}', {
          params: { path: { wedding_id: weddingId!, entry_id: v.id } },
          body: { status: v.status },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['shortlist', weddingId] })
      qc.invalidateQueries({ queryKey: ['overview', weddingId] })
    },
  })

  function onSearch(e: FormEvent) {
    e.preventDefault()
    setQuery({ ...form })
  }

  return (
    <>
      <PageHeader
        title="Vendors"
        subtitle="AI recommends with reasons; final selection is always a person's decision."
      />
      <form className="card search-form" onSubmit={onSearch} aria-label="Vendor search">
        <label className="grow">
          What are you looking for?
          <input
            value={form.q}
            placeholder="e.g. royal floral mandap, शाही सजावट"
            onChange={(e) => setForm({ ...form, q: e.target.value })}
          />
        </label>
        <label>
          Category
          <select
            value={form.category}
            onChange={(e) =>
              setForm({ ...form, category: e.target.value as SearchQuery['category'] })
            }
          >
            <option value="">Any</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {titleCase(c)}
              </option>
            ))}
          </select>
        </label>
        <label>
          City
          <input
            value={form.city}
            placeholder={wedding.data?.city}
            onChange={(e) => setForm({ ...form, city: e.target.value })}
          />
        </label>
        <label>
          Budget (₹)
          <input
            type="number"
            min={1}
            value={form.budget_max}
            onChange={(e) => setForm({ ...form, budget_max: e.target.value })}
          />
        </label>
        <label>
          Date
          <input
            type="date"
            value={form.event_date}
            onChange={(e) => setForm({ ...form, event_date: e.target.value })}
          />
        </label>
        <button type="submit" className="primary">
          Search
        </button>
      </form>

      <div className="grid vendor-layout">
        <section aria-label="Search results">
          <ErrorBox error={results.error ?? add.error} />
          {results.isFetching && <Loading label="Searching…" />}
          {results.data && (
            <p className="muted small">
              {results.data.results.length} shown of {results.data.total_candidates} in {city}
              {Object.keys(results.data.filtered_out).length > 0 &&
                ` · filtered out: ${Object.entries(results.data.filtered_out)
                  .map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`)
                  .join(', ')}`}
              {' · '}retrieval: {results.data.mode}
            </p>
          )}
          {results.data?.results.map((m) => (
            <article key={m.vendor.id} className="card vendor" data-testid="vendor-result">
              <div className="vendor-head">
                <div>
                  <h3>{m.vendor.name}</h3>
                  <div className="muted small">
                    {titleCase(m.vendor.category)} · {m.vendor.city} ·{' '}
                    {formatLakh(m.vendor.price_min)}–{formatLakh(m.vendor.price_max)} · ★{' '}
                    {m.vendor.rating.toFixed(1)}
                  </div>
                </div>
                <div className="vendor-score" title="Match score">
                  {Math.round(m.score * 100)}
                </div>
              </div>
              <p className="vendor-desc" lang={m.vendor.description_lang}>
                {m.vendor.description}
              </p>
              <VendorExplanation ex={m.explanation} />
              {can('shortlist:write') && (
                <button
                  type="button"
                  disabled={onShortlist.has(m.vendor.id) || add.isPending}
                  onClick={() => add.mutate(m)}
                >
                  {onShortlist.has(m.vendor.id) ? 'On shortlist' : 'Add to shortlist'}
                </button>
              )}
            </article>
          ))}
          {query === null && <p className="muted">Search to see recommendations.</p>}
        </section>

        <section className="card" aria-label="Shortlist">
          <h2>Shortlist</h2>
          <ErrorBox error={shortlist.error ?? decide.error} />
          {shortlist.isPending && <Loading />}
          <ul className="list shortlist">
            {shortlist.data?.map((s) => (
              <li key={s.id} data-testid="shortlist-entry">
                <div>
                  <strong>{s.vendor?.name ?? s.vendor_id}</strong>{' '}
                  <Badge
                    tone={
                      s.status === 'selected' ? 'good' : s.status === 'rejected' ? 'bad' : 'neutral'
                    }
                  >
                    {s.status}
                  </Badge>{' '}
                  <ActorBadge actor={s.added_by_actor} />
                </div>
                <span className="muted small">
                  {s.vendor ? titleCase(s.vendor.category) : ''}
                  {s.quoted_price ? ` · quote ${formatINR(s.quoted_price)}` : ''}
                </span>
                <div className="row-actions">
                  {can('vendor:select') && s.status !== 'selected' && (
                    <button
                      type="button"
                      className="primary small"
                      onClick={() => decide.mutate({ id: s.id, status: 'selected' })}
                    >
                      Select vendor
                    </button>
                  )}
                  {can('shortlist:write') && s.status !== 'rejected' && (
                    <button
                      type="button"
                      className="small"
                      onClick={() => decide.mutate({ id: s.id, status: 'rejected' })}
                    >
                      Reject
                    </button>
                  )}
                </div>
              </li>
            ))}
            {shortlist.data?.length === 0 && <li className="muted">Nothing shortlisted yet.</li>}
          </ul>
        </section>
      </div>
    </>
  )
}
