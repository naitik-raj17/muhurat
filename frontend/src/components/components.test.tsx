import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Schemas } from '../api/client'
import { ApiError } from '../api/client'
import { canUndo, payloadFields } from '../agents'
import { groupByDay } from '../timeline'
import { PayloadView } from '../pages/ApprovalsPage'
import { TierBadge } from '../pages/ActivityPage'
import { VendorExplanation } from '../pages/VendorsPage'
import { ActorBadge, ErrorBox, Meter } from './ui'

describe('ActorBadge', () => {
  it('labels AI and human actions distinctly', () => {
    render(
      <>
        <ActorBadge actor="ai" />
        <ActorBadge actor="human" />
      </>,
    )
    expect(screen.getByText('AI')).toHaveClass('badge-ai')
    expect(screen.getByText('Human')).toHaveClass('badge-human')
  })
})

describe('ErrorBox', () => {
  it('shows problem details including field errors', () => {
    const err = new ApiError({
      type: 'x',
      title: 'Validation failed',
      status: 422,
      detail: 'The request did not match the expected schema.',
      errors: [{ loc: ['body', 'email'], msg: 'not an email' }],
    })
    render(<ErrorBox error={err} />)
    expect(screen.getByRole('alert')).toHaveTextContent('email: not an email')
  })

  it('renders nothing without an error', () => {
    const { container } = render(<ErrorBox error={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('Meter', () => {
  it('clamps and flags alerts', () => {
    render(<Meter fraction={1.4} alert />)
    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuenow', '100')
    expect(meter).toHaveClass('meter-alert')
  })
})

describe('VendorExplanation', () => {
  it('explains why a vendor was recommended', () => {
    const ex: Schemas['MatchExplanation'] = {
      matched_terms: ['mandap'],
      matched_styles: ['royal'],
      price_fit: 'fully within budget',
      price_vs_budget_pct: 40,
      availability: 'available on 2026-12-11',
      rating_note: '4.5/5 from 100 reviews',
      language_match: null,
      retrieval: {},
      summary: 'Matches style royal; fully within budget.',
    }
    render(<VendorExplanation ex={ex} />)
    expect(screen.getByText(/Style: royal/)).toBeInTheDocument()
    expect(screen.getByText(/starts at 40% of budget/)).toBeInTheDocument()
    expect(screen.getByText(/available on 2026-12-11/)).toBeInTheDocument()
  })
})

describe('groupByDay', () => {
  it('groups by IST day in start order', () => {
    const item = (id: string, start: string) =>
      ({ id, start_at: start }) as unknown as Schemas['TimelineItemOut']
    const days = groupByDay([
      item('b', '2026-12-10T20:00:00Z'),
      item('a', '2026-12-10T05:00:00Z'),
      item('c', '2026-12-11T04:00:00Z'),
    ])
    expect(days.map(([d, items]) => [d, items.map((i) => i.id)])).toEqual([
      ['2026-12-10', ['a']],
      ['2026-12-11', ['b', 'c']],
    ])
  })
})

describe('canUndo', () => {
  it('allows undo only for executed, reversible actions', () => {
    expect(canUndo({ reversible: true, status: 'executed' })).toBe(true)
    expect(canUndo({ reversible: true, status: 'rolled_back' })).toBe(false)
    expect(canUndo({ reversible: true, status: 'pending_approval' })).toBe(false)
    expect(canUndo({ reversible: false, status: 'executed' })).toBe(false)
  })
})

describe('TierBadge', () => {
  it('marks Tier 3 as prohibited and Tier 2 as bounded', () => {
    render(
      <>
        <TierBadge tier="tier2" />
        <TierBadge tier="tier3" />
      </>,
    )
    expect(screen.getByText('Tier 2')).toHaveClass('badge-warn')
    expect(screen.getByText('Tier 3')).toHaveClass('badge-bad')
  })
})

describe('payloadFields', () => {
  it('flattens args and facts, dropping ids, the tool name and empty values', () => {
    expect(
      payloadFields({
        tool: 'shift_timeline_item',
        args: { item_id: 'x', shift_minutes: 3600, reason: 'Venue asked' },
        facts: { notes: [], shift_hours: 60, price_change_fraction: null },
      }),
    ).toEqual([
      ['shift_minutes', '3600'],
      ['reason', 'Venue asked'],
      ['shift_hours', '60'],
    ])
  })
})

describe('PayloadView', () => {
  it('shows what the agent proposed', () => {
    render(<PayloadView payload={{ shift_hours: 72, limit_hours: 48 }} />)
    expect(screen.getByText('Shift Hours')).toBeInTheDocument()
    expect(screen.getByText('72')).toBeInTheDocument()
  })
})
