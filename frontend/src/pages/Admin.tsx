import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AppBar } from '../components/AppBar'
import { Icon } from '../components/Icons'
import { Modal } from '../components/Modal'
import type { AdminArtifact, AdminFilters, AdminStats, ModerationInput } from '../lib/api'
import { describeModeration, formatBytes, timeAgo } from '../lib/format'
import { useApi } from '../lib/useApi'

type Action = { kind: 'withdraw' | 'invites' | 'flag' | 'unflag' | 'delete'; artifact: AdminArtifact }

const ACTIONS: Record<Action['kind'], { title: string; verb: string; explain: string; input?: ModerationInput }> = {
  withdraw: { title: 'Withdraw organisation-wide sharing', verb: 'Withdraw', input: { withdraw_org: true },
    explain: 'The organisation link stops working. Invited people keep their access.' },
  invites: { title: 'Remove every invited person', verb: 'Remove', input: { clear_invites: true },
    explain: 'Everyone the owner invited loses access. Organisation-wide sharing is unchanged.' },
  flag: { title: 'Flag as sensitive', verb: 'Flag', input: { sensitive: true },
    explain: 'A sensitive artifact cannot be shared with the whole organisation; an existing organisation link is withdrawn.' },
  unflag: { title: 'Remove the sensitive flag', verb: 'Unflag', input: { sensitive: false },
    explain: 'The owner may share it with the whole organisation again.' },
  delete: { title: 'Delete artifact', verb: 'Delete',
    explain: 'The artifact and all its versions are deleted for everyone. This cannot be undone.' },
}

function Stat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
      {hint && <span className="stat-hint">{hint}</span>}
    </div>
  )
}

export function Admin() {
  const api = useApi()
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [rows, setRows] = useState<{ artifacts: AdminArtifact[]; total: number } | null>(null)
  const [filters, setFilters] = useState<AdminFilters>({ q: '', visibility: '', sensitive: '' })
  const [action, setAction] = useState<Action | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    setError(null)
    api.admin.stats().then(setStats).catch((e) => setError(e.message))
    api.admin.artifacts(filters).then(setRows).catch((e) => setError(e.message))
  }, [api, filters])
  useEffect(reload, [reload])

  const confirmAction = async () => {
    if (!action) return
    setBusy(true); setError(null)
    try {
      const spec = ACTIONS[action.kind]
      if (action.kind === 'delete') await api.admin.remove(action.artifact.id, note.trim())
      else await api.admin.moderate(action.artifact.id, { ...spec.input, note: note.trim() })
      setAction(null); setNote(''); reload()
    } catch (e) { setError(e instanceof Error ? e.message : 'Action failed.') }
    finally { setBusy(false) }
  }

  const open = (kind: Action['kind'], artifact: AdminArtifact) => { setNote(''); setAction({ kind, artifact }) }
  const shared = stats?.by_visibility.shared ?? 0

  return (
    <>
      <AppBar />
      <main className="page wide">
        <div className="page-head"><h1>Administration</h1></div>
        <p className="lede small">Every artifact in the hub, as metadata. Bodies of private artifacts stay private:
          you open an artifact only when it is shared with you or with the organisation. Owners see each action and its note.</p>

        {error && <p className="error">{error}</p>}

        {stats && (
          <section className="stats" aria-label="Totals">
            <Stat label="Artifacts" value={stats.artifacts} hint={formatBytes(stats.bytes)} />
            <Stat label="Owners" value={stats.owners} />
            <Stat label="Organisation-wide" value={shared} hint={`${stats.artifacts - shared} private or invite-only`} />
            <Stat label="Sensitive" value={stats.sensitive} />
            <Stat label="Published via MCP" value={stats.by_via.mcp ?? 0} hint={`${stats.by_via.web ?? 0} from the web`} />
          </section>
        )}

        <form className="admin-filters" role="search" onSubmit={(e) => { e.preventDefault(); reload() }}>
          <div className="search">
            {Icon.search}
            <input type="search" value={filters.q} placeholder="Title or owner email" aria-label="Filter by title or owner"
              onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
          </div>
          <select value={filters.visibility} aria-label="Visibility"
            onChange={(e) => setFilters({ ...filters, visibility: e.target.value as AdminFilters['visibility'] })}>
            <option value="">Any visibility</option>
            <option value="shared">Organisation-wide</option>
            <option value="private">Private or invite-only</option>
          </select>
          <select value={filters.sensitive} aria-label="Sensitive"
            onChange={(e) => setFilters({ ...filters, sensitive: e.target.value as AdminFilters['sensitive'] })}>
            <option value="">Sensitive or not</option>
            <option value="true">Sensitive only</option>
            <option value="false">Not sensitive</option>
          </select>
        </form>

        {rows === null ? <p className="muted">Loading…</p> : rows.artifacts.length === 0 ? (
          <div className="empty"><p className="muted">No artifact matches.</p></div>
        ) : (
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr><th>Artifact</th><th>Owner</th><th>Sharing</th><th className="num">Size</th><th>Updated</th><th><span className="sr-only">Actions</span></th></tr>
              </thead>
              <tbody>
                {rows.artifacts.map((a) => (
                  <tr key={a.id}>
                    <td>
                      {a.visibility === 'shared'
                        ? <Link to={`/artifacts/${a.id}`} className="admin-title">{a.title}</Link>
                        : <span className="admin-title">{a.title}</span>}
                      <span className="muted small"> {a.kind} · v{a.current_version} · via {a.created_via === 'mcp' ? 'MCP' : 'web'}</span>
                      {a.moderation && <span className="muted small admin-mod">Last action: {describeModeration(a.moderation.action)} by {a.moderation.by}, {timeAgo(a.moderation.at)}</span>}
                    </td>
                    <td className="admin-owner">{a.owner_name || a.owner}{a.owner_name && <span className="muted small">{a.owner}</span>}</td>
                    <td>
                      <span className="admin-badges">
                        {a.visibility === 'shared' ? <span className="tag">{Icon.globe} Organisation</span> : <span className="tag">{Icon.lock} Private</span>}
                        {a.shared_with_count > 0 && <span className="tag">{Icon.people} {a.shared_with_count}</span>}
                        {a.sensitive && <span className="tag sensitive">Sensitive</span>}
                      </span>
                    </td>
                    <td className="num">{formatBytes(a.size)}</td>
                    <td className="muted small">{timeAgo(a.updated_at)}</td>
                    <td className="admin-actions">
                      {a.visibility === 'shared' && <button type="button" className="btn ghost small" onClick={() => open('withdraw', a)}>Make private</button>}
                      {a.shared_with_count > 0 && <button type="button" className="btn ghost small" onClick={() => open('invites', a)}>Remove invites</button>}
                      {a.sensitive
                        ? <button type="button" className="btn ghost small" onClick={() => open('unflag', a)}>Unflag</button>
                        : <button type="button" className="btn ghost small" onClick={() => open('flag', a)}>Flag sensitive</button>}
                      <button type="button" className="btn ghost small danger" onClick={() => open('delete', a)} aria-label={`Delete ${a.title}`}>{Icon.trash}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.total > rows.artifacts.length && <p className="muted small">Showing {rows.artifacts.length} of {rows.total}. Narrow the filters to see the rest.</p>}
          </div>
        )}
      </main>

      {action && (
        <Modal title={ACTIONS[action.kind].title} onClose={() => setAction(null)}>
          <form onSubmit={(e) => { e.preventDefault(); confirmAction() }}>
            <p><strong>{action.artifact.title}</strong> <span className="muted">by {action.artifact.owner}</span></p>
            <p className="muted small">{ACTIONS[action.kind].explain}</p>
            <label className="field">
              <span>Note {action.kind === 'delete' ? 'for the audit log' : 'for the owner'} (optional)</span>
              <input autoFocus value={note} maxLength={500} onChange={(e) => setNote(e.target.value)}
                placeholder="Why this action was taken" />
            </label>
            <div className="modal-actions"><span className="spacer" />
              <button type="button" className="btn" onClick={() => setAction(null)}>Cancel</button>
              <button type="submit" className={'btn primary' + (action.kind === 'delete' ? ' danger-fill' : '')} disabled={busy}>
                {busy ? 'Working…' : ACTIONS[action.kind].verb}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  )
}
