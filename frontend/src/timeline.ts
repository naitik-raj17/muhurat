import type { Schemas } from './api/client'
import { istDayKey } from './format'

/** Group items by IST calendar day, keeping start-time order. */
export function groupByDay(items: Schemas['TimelineItemOut'][]) {
  const days = new Map<string, Schemas['TimelineItemOut'][]>()
  for (const item of [...items].sort((a, b) => a.start_at.localeCompare(b.start_at))) {
    const key = istDayKey(item.start_at)
    days.set(key, [...(days.get(key) ?? []), item])
  }
  return [...days.entries()]
}
