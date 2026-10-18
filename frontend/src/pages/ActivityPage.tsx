import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { canUndo } from '../agents'
import { api, unwrap, type Schemas } from '../api/client'
import type { operations } from '../api/schema'
import { useAuth } from '../auth'
import { ActorBadge, Badge, ErrorBox, Loading, PageHeader } from '../components/ui'
import { formatDay, formatTime, titleCase } from '../format'

type ActionStatus = NonNullable<
  NonNullable<operations['list_agent_actions']['parameters']['query']>['status']
>

const STATUS_TONE: Record<string, 'good' | 'warn' | 'bad' | 'neutral'> = {
  executed: 'good',
  pending_approval: 'warn',
  blocked: 'bad',
  failed: 'bad',
  rejected: 'bad',
  rolled_back: 'neutral',
}

export function TierBadge({ tier }: { tier: string }) {
  const tone = tier === 'tier3' ? 'bad' : tier === 'tier2' ? 'warn' : 'neutral'
  return <Badge tone={tone}>{tier.replace('tier', 'Tier ')}</Badge>
}

function StateDiff({ action }: { action: Schemas['AgentActionOut'] }) {
  if (!action.before_state && !action.after_state) return null
  return (
    <details>
      <summary className="small">Before / after</summary>
      <div className="diff">
        <pre aria-label="Before">{JSON.stringify(action.before_state, null, 2)}</pre>
        <pre aria-label="After">{JSON.stringify(action.after_state, null, 2)}</pre>
      </div>
    </details>
  )
}

export function ActivityPage() {
  const { weddingId } = useParams()
  const { can } = useAuth()
  const qc = useQueryClient()
  const [status, setStatus] = useState<ActionStatus | ''>('')

  const actions = useQuery({
    queryKey: ['agent-actions', weddingId ?? null, status],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/agent-actions', {
          params: { query: { wedding_id: weddingId, status: status || undefined } },
        }),
      ),
  })

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['agent-actions'] })
    void qc.invalidateQueries({ queryKey: ['approvals'] })
  }

  const run = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST('/api/v1/weddings/{wedding_id}/agents/run', {
          params: { path: { wedding_id: weddingId! } },
          body: { agents: [] },
        }),
      ),
    onSuccess: refresh,
  })

  const undo = useMutation({
    mutationFn: (actionId: string) =>
      unwrap(
        api.POST('/api/v1/agent-actions/{action_id}/undo', {
          params: { path: { action_id: actionId } },
          body: { reason: 'Undone from the activity log' },
        }),
      ),
    onSuccess: refresh,
  })

  return (
    <>
      <PageHeader
        title="Agent activity"
        subtitle="Every action an agent took or tried, with its autonomy tier. Tier 2 actions can be undone."
        actions={
          weddingId &&
          can('agent:run') && (
            <button
              type="button"
              className="primary"
              disabled={run.isPending}
              onClick={() => run.mutate()}
            >
              {run.isPending ? 'Running agents…' : 'Run agents now'}
            </button>
          )
        }
      />
      <ErrorBox error={actions.error ?? run.error ?? undo.error} />
      {run.data && (
        <div className="card" role="status">
          Agents finished:{' '}
          {Object.entries(run.data.summary)
            .map(([k, v]) => `${v} ${titleCase(k).toLowerCase()}`)
            .join(' · ') || 'no actions needed'}
        </div>
      )}
      <div className="filters">
        <select
          aria-label="Action status"
          value={status}
          onChange={(e) => setStatus(e.target.value as typeof status)}
        >
          <option value="">Any status</option>
          <option value="executed">Executed</option>
          <option value="pending_approval">Waiting for approval</option>
          <option value="blocked">Blocked</option>
          <option value="rolled_back">Undone</option>
          <option value="rejected">Rejected</option>
          <option value="failed">Failed</option>
        </select>
        {actions.data && <span className="muted small">{actions.data.length} actions</span>}
      </div>
      {actions.isPending && <Loading />}
      {actions.data?.length === 0 && <p className="muted">No agent activity yet.</p>}
      {actions.data && actions.data.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>By</th>
                <th>Action</th>
                <th>Tier</th>
                <th>Status</th>
                <th>Reason</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {actions.data.map((a) => (
                <tr key={a.id}>
                  <td className="small">
                    {formatDay(a.created_at)}
                    <div className="muted">{formatTime(a.created_at)}</div>
                  </td>
                  <td>
                    <ActorBadge actor={a.actor_type} />
                    {a.approved_by && (
                      <div className="small">
                        <ActorBadge actor="human" /> approved
                      </div>
                    )}
                  </td>
                  <td>
                    <strong>{titleCase(a.action)}</strong>
                    <div className="muted small">{titleCase(a.agent)} agent</div>
                    <StateDiff action={a} />
                  </td>
                  <td>
                    <TierBadge tier={a.tier} />
                  </td>
                  <td>
                    <Badge tone={STATUS_TONE[a.status] ?? 'neutral'}>
                      {titleCase(a.status).toLowerCase()}
                    </Badge>
                    {a.rolled_back_at && (
                      <div className="small">
                        <ActorBadge actor="human" /> undid
                      </div>
                    )}
                  </td>
                  <td className="small">{a.reason}</td>
                  <td>
                    {canUndo(a) && can('agent:rollback') && (
                      <button
                        type="button"
                        className="small"
                        disabled={undo.isPending}
                        onClick={() => undo.mutate(a.id)}
                      >
                        Undo
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
