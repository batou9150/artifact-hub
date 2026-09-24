import { describe, expect, it } from 'vitest'
import { EMAIL_RE, formatBytes, nameFromEmail } from './format'

describe('format helpers', () => {
  it('derives a display name from an email', () => {
    expect(nameFromEmail('jane.doe@example.com')).toBe('Jane Doe')
  })
  it('formats byte sizes', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(800_000)).toBe('781.3 KB')
  })
  it('validates emails loosely', () => {
    expect(EMAIL_RE.test('a@b.co')).toBe(true)
    expect(EMAIL_RE.test('not an email')).toBe(false)
  })
})
