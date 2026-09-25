import { describe, expect, it } from 'vitest'
import { describeModeration } from './format'

describe('describeModeration', () => {
  it('joins known actions in plain words', () => {
    expect(describeModeration('withdrew_org_share,cleared_invites'))
      .toBe('withdrew organisation-wide sharing and removed every invited person')
    expect(describeModeration('flagged_sensitive')).toBe('flagged it sensitive')
  })
  it('keeps unknown actions readable', () => {
    expect(describeModeration('something_new')).toBe('something new')
  })
})
