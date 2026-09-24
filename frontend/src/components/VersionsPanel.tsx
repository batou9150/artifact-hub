import { useEffect, useState } from 'react'
import type { Api, Artifact, Version } from '../lib/api'
import { formatBytes, timeAgo } from '../lib/format'

// Owner-only history: preview any prior version in the sandbox, restore it as a
// NEW current version (history is never rewritten).
export function VersionsPanel({ artifact, api, previewing, onPreview, onRestored, onClose }: {
  artifact: Artifact
  api: Api
  previewing: number | null
  onPreview: (n: number | null) => void
  onRestored: (a: Artifact) => void
  onClose: () => void
}) {
  const [versions, setVersions] = useState<Version[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.versions(artifact.id).then((v) => alive && setVersions(v)).catch((e) => alive && setError(String(e.message || e)))
    return () => { alive = false }
  }, [api, artifact.id, artifact.current_version])

  const restore = async (n: number) => {
    setBusy(true); setError(null)
    try { onRestored(await api.revert(artifact.id, n)); onPreview(null) }
    catch (e) { setError(e instanceof Error ? e.message : 'Restore failed.') }
    finally { setBusy(false) }
  }

  return (
    <aside className="side-panel" aria-label="Version history">
      <div className="side-head">
        <strong>Version history</strong>
        <button type="button" className="btn ghost small" onClick={onClose}>Close</button>
      </div>
      {versions === null ? <p className="muted">Loading…</p> : (
        <ul className="versions">
          {[...versions].reverse().map((v) => {
            const current = v.n === artifact.current_version
            const selected = previewing === v.n || (previewing === null && current)
            return (
              <li key={v.n} className={selected ? 'selected' : ''}>
                <button type="button" className="version-main" onClick={() => onPreview(current ? null : v.n)}>
                  <span className="version-n">Version {v.n}{current && <em> current</em>}</span>
                  <span className="muted small">{timeAgo(v.created_at)} · {formatBytes(v.size)}</span>
                </button>
                {!current && (
                  <button type="button" className="btn small" disabled={busy} onClick={() => restore(v.n)}>Restore</button>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {error && <p className="error">{error}</p>}
    </aside>
  )
}
