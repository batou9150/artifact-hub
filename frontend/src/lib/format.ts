export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 60) return 'just now'
  const m = s / 60
  if (m < 60) return `${Math.floor(m)} min ago`
  const h = m / 60
  if (h < 24) return `${Math.floor(h)} h ago`
  const d = h / 24
  if (d < 30) return `${Math.floor(d)} d ago`
  return new Date(t).toLocaleDateString()
}

export function nameFromEmail(email: string): string {
  const local = email.split('@')[0] || email
  return local.split(/[._-]+/).filter(Boolean).map((p) => p[0].toUpperCase() + p.slice(1)).join(' ')
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const MODERATION_LABELS: Record<string, string> = {
  withdrew_org_share: 'withdrew organisation-wide sharing',
  cleared_invites: 'removed every invited person',
  flagged_sensitive: 'flagged it sensitive',
  unflagged_sensitive: 'removed the sensitive flag',
}

/** "withdrew_org_share,cleared_invites" -> "withdrew organisation-wide sharing and removed every invited person" */
export function describeModeration(action: string): string {
  const parts = action.split(',').filter(Boolean).map((a) => MODERATION_LABELS[a] ?? a.replace(/_/g, ' '))
  return parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0] ?? ''
}
