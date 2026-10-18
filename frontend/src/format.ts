// Display helpers. Weddings are in India, so times show in IST and money in INR.

const inr = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

/** ₹12,50,000 style (Indian digit grouping). Accepts the API's decimal strings. */
export function formatINR(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '–'
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? inr.format(n) : '–'
}

/** Compact lakh/crore form used in tight table cells: ₹4.5L, ₹1.2Cr. */
export function formatLakh(value: string | number): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return '–'
  if (n >= 1e7) return `₹${trim(n / 1e7)}Cr`
  if (n >= 1e5) return `₹${trim(n / 1e5)}L`
  if (n >= 1e3) return `₹${trim(n / 1e3)}k`
  return `₹${n}`
}

function trim(n: number): string {
  return n.toFixed(1).replace(/\.0$/, '')
}

const timeFmt = new Intl.DateTimeFormat('en-IN', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'Asia/Kolkata',
})
const dayFmt = new Intl.DateTimeFormat('en-IN', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  timeZone: 'Asia/Kolkata',
})

export function formatTime(iso: string): string {
  return timeFmt.format(new Date(iso))
}

export function formatDay(iso: string): string {
  // Plain dates ("2026-12-10") are calendar days, not instants: avoid a UTC shift.
  const d = iso.length === 10 ? new Date(`${iso}T12:00:00+05:30`) : new Date(iso)
  return dayFmt.format(d)
}

/** IST calendar day key (YYYY-MM-DD) of an instant, for grouping timeline items by day. */
export function istDayKey(iso: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date(iso))
}

export function pct(fraction: number | null | undefined): string {
  return fraction === null || fraction === undefined ? '–' : `${Math.round(fraction * 100)}%`
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
