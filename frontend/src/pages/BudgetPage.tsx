import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { useAuth } from '../auth'
import { Badge, ErrorBox, Loading, Meter, PageHeader, Stat } from '../components/ui'
import { formatINR, pct, titleCase } from '../format'

export function BudgetPage() {
  const { weddingId } = useParams()
  const qc = useQueryClient()
  const { can } = useAuth()
  const budget = useQuery({
    queryKey: ['budget', weddingId],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/weddings/{wedding_id}/budget', {
          params: { path: { wedding_id: weddingId! } },
        }),
      ),
  })
  const [form, setForm] = useState({ category: '', description: '', planned: '', actual: '' })
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/weddings/{wedding_id}/budget/items', {
          params: { path: { wedding_id: weddingId! } },
          body: {
            category: form.category.trim().toLowerCase(),
            description: form.description,
            planned_amount: form.planned || '0',
            actual_amount: form.actual || '0',
          },
        }),
      ),
    onSuccess: () => {
      setForm({ category: '', description: '', planned: '', actual: '' })
      qc.invalidateQueries({ queryKey: ['budget', weddingId] })
      qc.invalidateQueries({ queryKey: ['overview', weddingId] })
    },
  })

  if (budget.isPending) return <Loading />
  if (budget.error) return <ErrorBox error={budget.error} />
  const b = budget.data
  const alerts = b.categories.filter((c) => c.alert)

  function onAdd(e: FormEvent) {
    e.preventDefault()
    add.mutate()
  }

  return (
    <>
      <PageHeader
        title="Budget"
        subtitle={`Alerts fire when a category reaches ${pct(b.alert_threshold)} of its plan.`}
      />
      <section className="grid stats">
        <Stat label="Total budget" value={formatINR(b.summary.total_budget)} />
        <Stat label="Planned" value={formatINR(b.summary.planned)} />
        <Stat label="Spent" value={formatINR(b.summary.actual)} />
        <Stat label="Remaining" value={formatINR(b.summary.remaining)} />
      </section>
      {alerts.length > 0 && (
        <div className="alert-box" role="alert">
          Over threshold: {alerts.map((a) => titleCase(a.category)).join(', ')}
        </div>
      )}
      <section className="card">
        <h2>By category</h2>
        <table className="table">
          <thead>
            <tr>
              <th>Category</th>
              <th className="num">Planned</th>
              <th className="num">Spent</th>
              <th>Used</th>
            </tr>
          </thead>
          <tbody>
            {b.categories.map((c) => (
              <tr key={c.category}>
                <td>
                  {titleCase(c.category)} {c.alert && <Badge tone="bad">alert</Badge>}
                </td>
                <td className="num">{formatINR(c.planned)}</td>
                <td className="num">{formatINR(c.actual)}</td>
                <td className="meter-cell">
                  <Meter fraction={c.used_fraction} alert={c.alert} />
                  <span className="small muted">{pct(c.used_fraction)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {can('budget:write') && (
        <form className="card inline-form" onSubmit={onAdd} aria-label="Add budget item">
          <h2>Add expense line</h2>
          <input
            aria-label="Category"
            placeholder="Category (e.g. attire)"
            required
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value })}
          />
          <input
            aria-label="Description"
            placeholder="Description"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
          <input
            aria-label="Planned"
            type="number"
            min={0}
            placeholder="Planned ₹"
            value={form.planned}
            onChange={(e) => setForm({ ...form, planned: e.target.value })}
          />
          <input
            aria-label="Spent"
            type="number"
            min={0}
            placeholder="Spent ₹"
            value={form.actual}
            onChange={(e) => setForm({ ...form, actual: e.target.value })}
          />
          <button type="submit" className="primary" disabled={add.isPending}>
            Add
          </button>
          <ErrorBox error={add.error} />
        </form>
      )}
    </>
  )
}
