import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { payloadFields } from '../agents'
import { api, unwrap, type Schemas } from '../api/client'
import type { operations } from '../api/schema'
import { useAuth } from '../auth'
import { ActorBadge, Badge, ErrorBox, Loading, PageHeader } from '../components/ui'
import { formatDay, formatTime, titleCase } from '../format'

type ApprovalStatus = NonNullable<
  NonNullable<operations['list_approvals']['parameters']['query']>['status']
>

export function ApprovalStatusBadge({ status }: { status: string }) {
  const tone = status === 'approved' ? 'good' : status === 'rejected' ? 'bad' : 'warn'
  return <Badge tone={tone}>{status}</Badge>
}

/** Key/value view of what the agent proposed (the approval payload). */
export function PayloadView({ payload }: { payload: Record<string, unknown> }) {
  const fields = payloadFields(payload)
  if (fields.length === 0) return null
  return (
    <dl className="kv">
      {fields.map(([k, v]) => (
        <div key={k}>
          <dt>{titleCase(k)}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function ApprovalCard({
  approval,
  canDecide,
}: {
  approval: Schemas['ApprovalOut']
  canDecide: boolean
}) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const decide = useMutation({
    mutationFn: (decision: 'approve' | 'reject') =>
      unwrap(
        decision === 'approve'
          ? api.POST('/api/v1/approvals/{approval_id}/approve', {
              params: { path: { approval_id: approval.id } },
              body: { note },
            })
          : api.POST('/api/v1/approvals/{approval_id}/reject', {
              params: { path: { approval_id: approval.id } },
              body: { note },
            }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['approvals'] })
      void qc.invalidateQueries({ queryKey: ['agent-actions'] })
    },
  })

  return (
    <article className="card approval" aria-label={approval.summary}>
      <div className="approval-head">
        <div>
          <ActorBadge actor="ai" /> <strong>{approval.summary}</strong>
          <div className="muted small">
            {titleCase(approval.kind)} · requested {formatDay(approval.created_at)}{' '}
            {formatTime(approval.created_at)}
          </div>
        </div>
        <ApprovalStatusBadge status={approval.status} />
      </div>
      <PayloadView payload={approval.payload} />
      {approval.status === 'pending' && !canDecide && (
        <p className="muted small">
          Needs a user with <code>{approval.required_permission}</code> to decide.
        </p>
      )}
      {approval.status === 'pending' && canDecide && (
        <div className="approval-actions">
          <input
            aria-label="Decision note"
            placeholder="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button
            type="button"
            className="primary"
            disabled={decide.isPending}
            onClick={() => decide.mutate('approve')}
          >
            Approve
          </button>
          <button type="button" disabled={decide.isPending} onClick={() => decide.mutate('reject')}>
            Reject
          </button>
        </div>
      )}
      {approval.status !== 'pending' && (
        <div className="muted small">
          <ActorBadge actor="human" /> {approval.status}
          {approval.decided_at &&
            ` on ${formatDay(approval.decided_at)} ${formatTime(approval.decided_at)}`}
          {approval.decision_note && ` · “${approval.decision_note}”`}
        </div>
      )}
      <ErrorBox error={decide.error} />
    </article>
  )
}

export function ApprovalsPage() {
  const { weddingId } = useParams()
  const { can } = useAuth()
  const [status, setStatus] = useState<ApprovalStatus>('pending')

  const approvals = useQuery({
    queryKey: ['approvals', weddingId ?? null, status],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/approvals', {
          params: { query: { wedding_id: weddingId, status } },
        }),
      ),
  })

  return (
    <>
      <PageHeader
        title="Approvals"
        subtitle="Agent actions outside their limits wait here for a person to decide. Nothing runs until approved."
      />
      <div className="filters">
        <select
          aria-label="Approval status"
          value={status}
          onChange={(e) => setStatus(e.target.value as typeof status)}
        >
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        {approvals.data && <span className="muted small">{approvals.data.length} requests</span>}
      </div>
      <ErrorBox error={approvals.error} />
      {approvals.isPending && <Loading />}
      {approvals.data?.length === 0 && <p className="muted">Nothing to review.</p>}
      <div className="stack">
        {approvals.data?.map((a) => (
          <ApprovalCard
            key={a.id}
            approval={a}
            // Out-of-limits requests need a stronger permission (org admin) than routine ones.
            canDecide={can('approval:decide') && can(a.required_permission)}
          />
        ))}
      </div>
    </>
  )
}
