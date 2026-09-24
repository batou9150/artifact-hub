import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { ArtifactFrame, FRAME_SANDBOX } from './ArtifactFrame'

afterEach(cleanup)

describe('ArtifactFrame', () => {
  it('sandboxes with allow-scripts only, never allow-same-origin', () => {
    const { container } = render(<ArtifactFrame src="http://api.test/a/x?t=1" title="t" />)
    const frame = container.querySelector('iframe')!
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin')
    expect(FRAME_SANDBOX).toBe('allow-scripts')
    expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer')
  })

  it('renders a crafted title as text, not markup', () => {
    const evil = '<img src=x onerror=alert(1)>'
    const { container } = render(<ArtifactFrame src="about:blank" title={evil} />)
    expect(container.querySelector('iframe')!.getAttribute('title')).toBe(evil)
    expect(container.querySelector('img')).toBeNull()
  })
})
