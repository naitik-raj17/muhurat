import { describe, expect, it } from 'vitest'
import { formatINR, formatLakh, formatTime, istDayKey, pct, titleCase } from './format'

describe('format', () => {
  it('uses Indian digit grouping for rupees', () => {
    expect(formatINR('1250000.00')).toBe('₹12,50,000')
    expect(formatINR(null)).toBe('–')
  })

  it('compacts to lakh and crore', () => {
    expect(formatLakh(450000)).toBe('₹4.5L')
    expect(formatLakh('12000000')).toBe('₹1.2Cr')
    expect(formatLakh(8000)).toBe('₹8k')
  })

  it('shows times and days in IST', () => {
    expect(formatTime('2026-12-10T13:30:00Z')).toBe('19:00')
    // 20:00 UTC is already the next day in India.
    expect(istDayKey('2026-12-10T20:00:00Z')).toBe('2026-12-11')
  })

  it('formats percentages and labels', () => {
    expect(pct(0.954)).toBe('95%')
    expect(pct(null)).toBe('–')
    expect(titleCase('mehendi_artist')).toBe('Mehendi Artist')
  })
})
