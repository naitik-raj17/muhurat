// Agent activity helpers shared by the activity page and its tests.
import type { Schemas } from './api/client'

/** Only executed, reversible actions can be undone (the API enforces the same rule). */
export function canUndo(action: Pick<Schemas['AgentActionOut'], 'reversible' | 'status'>): boolean {
  return action.reversible && action.status === 'executed'
}

/**
 * Readable fields of an approval payload. The agent's `args` and the policy's `facts` are
 * flattened one level; internal ids, the tool name (already the approval kind) and empty
 * values are left out.
 */
export function payloadFields(payload: Record<string, unknown>): [string, string][] {
  const flat: [string, unknown][] = []
  for (const [k, v] of Object.entries(payload)) {
    if (k === 'tool') continue
    if (v && typeof v === 'object' && !Array.isArray(v)) flat.push(...Object.entries(v))
    else flat.push([k, v])
  }
  return flat
    .filter(([k, v]) => {
      if (k === 'id' || k.endsWith('_id')) return false
      return v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0)
    })
    .map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)])
}
