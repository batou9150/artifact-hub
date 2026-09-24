import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArtifactFrame } from '../components/ArtifactFrame'
import { DevBanner } from '../components/AppBar'
import { Icon } from '../components/Icons'
import { Modal } from '../components/Modal'
import { ShareDialog } from '../components/ShareDialog'
import { VersionsPanel } from '../components/VersionsPanel'
import type { Artifact } from '../lib/api'
import { useAuth } from '../lib/auth'
import { timeAgo } from '../lib/format'
import { useApi } from '../lib/useApi'

type Load = { phase: 'loading' } | { phase: 'unavailable' } | { phase: 'ready'; artifact: Artifact; src: string }

function visibilityLabel(a: Artifact): { icon: React.ReactNode; label: string } {
  if (a.visibility === 'shared') return { icon: Icon.globe, label: 'Organisation' }
  if ((a.shared_with?.length ?? 0) > 0) return { icon: Icon.people, label: `${a.shared_with!.length} people` }
  if (a.access === 'invited') return { icon: Icon.people, label: 'Shared with you' }
  return { icon: Icon.lock, label: 'Private' }
}

export function Viewer() {
  const { id = '' } = useParams()
  const api = useApi()
  const { state } = useAuth()
  const navigate = useNavigate()
  const [load, setLoad] = useState<Load>({ phase: 'loading' })
  const [panel, setPanel] = useState<null | 'versions' | 'details'>(null)
  const [sharing, setSharing] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [preview, setPreview] = useState<{ n: number; src: string } | null>(null)
  const [navigatedAway, setNavigatedAway] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const me = state.phase === 'signedIn' ? state.me : null
  const config = state.phase === 'signedIn' ? state.config : null

  const refresh = useCallback(async () => {
    try {
      const r = await api.get(id)
      setLoad({ phase: 'ready', artifact: r.artifact, src: r.render_url })
      return r.artifact
    } catch {
      setLoad({ phase: 'unavailable' })
      return null
    }
  }, [api, id])

  useEffect(() => {
    refresh().then((a) => { if (a && !a.can_manage) api.recordView(a.id) })
  }, [refresh, api])

  const showPreview = async (n: number | null) => {
    setNavigatedAway(false)
    if (n === null) { setPreview(null); return }
    try { setPreview({ n, src: await api.versionRender(id, n) }) } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  if (load.phase === 'loading') return <><DevBanner /><div className="viewer"><p className="center muted">Loading…</p></div></>
  if (load.phase === 'unavailable') {
    return (
      <><DevBanner />
        <div className="viewer">
          <div className="center">
            <p>This artifact is not available.</p>
            <p className="muted small">It may not exist, or it has not been shared with you.</p>
            <Link to="/" className="btn">Back to artifacts</Link>
          </div>
        </div>
      </>
    )
  }

  const a = load.artifact
  const vis = visibilityLabel(a)
  const doRename = async () => {
    const t = renameValue.trim()
    if (!t || t === a.title) { setRenaming(false); return }
    try { await api.rename(a.id, t); await refresh(); setRenaming(false) } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  const remove = async () => {
    if (!confirm(`Delete "${a.title}" and all its versions? This cannot be undone.`)) return
    try { await api.remove(a.id); navigate('/') } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  return (
    <>
      <DevBanner />
      <div className="viewer">
        <header className="viewer-head">
          <div className="viewer-left">
            <Link to="/" className="icon-btn" aria-label="Back to artifacts" title="Back to artifacts">{Icon.back}</Link>
            {a.can_manage ? (
              <button type="button" className="viewer-title editable" title="Rename"
                onClick={() => { setRenameValue(a.title); setRenaming(true) }}>{a.title}</button>
            ) : <span className="viewer-title">{a.title}</span>}
            <span className="chip" title="Current version">v{preview ? `${preview.n} (preview)` : a.current_version}</span>
            <span className="viewer-meta">
              {a.can_manage ? 'Yours' : `by ${a.owner_name || a.owner}`} · {vis.icon} {vis.label} · updated {timeAgo(a.updated_at)}
              {a.can_manage && <> · <span title="Distinct people who opened it (you excluded)">{Icon.eye} {a.viewers_count ?? 0} viewer{(a.viewers_count ?? 0) === 1 ? '' : 's'}</span></>}
            </span>
          </div>
          <div className="viewer-right">
            <button type="button" className={'btn ghost small' + (panel === 'details' ? ' pressed' : '')} onClick={() => setPanel(panel === 'details' ? null : 'details')}>Details</button>
            {a.can_manage && <>
              <button type="button" className={'btn ghost small' + (panel === 'versions' ? ' pressed' : '')} onClick={() => setPanel(panel === 'versions' ? null : 'versions')}>{Icon.clock} History</button>
              <Link to={`/artifacts/${a.id}/edit`} className="btn ghost small">{Icon.pencil} Edit</Link>
              <button type="button" className="btn primary small" onClick={() => setSharing(true)}>{vis.icon} Share</button>
            </>}
            {!a.can_manage && <button type="button" className="btn ghost small" onClick={() => navigator.clipboard?.writeText(a.url)}>{Icon.link} Copy link</button>}
            {a.can_delete && <button type="button" className="btn ghost small danger" onClick={remove} aria-label="Delete">{Icon.trash}</button>}
          </div>
        </header>
        {error && <p className="error banner">{error}</p>}
        {navigatedAway && (
          <p className="warning banner">This artifact navigated its frame away from its own content. What you see below is no longer the published artifact.
            <button type="button" className="btn ghost small" onClick={() => { setNavigatedAway(false); refresh() }}>Reload artifact</button></p>
        )}
        <div className="viewer-body">
          <ArtifactFrame src={preview?.src ?? load.src} title={a.title} onNavigatedAway={() => setNavigatedAway(true)} />
          {panel === 'versions' && a.can_manage && (
            <VersionsPanel artifact={a} api={api} previewing={preview?.n ?? null} onPreview={showPreview}
              onRestored={() => { setPreview(null); refresh() }} onClose={() => { setPanel(null); setPreview(null) }} />
          )}
          {panel === 'details' && (
            <aside className="side-panel" aria-label="Details">
              <div className="side-head"><strong>Details</strong><button type="button" className="btn ghost small" onClick={() => setPanel(null)}>Close</button></div>
              <dl className="details">
                <dt>Owner</dt><dd>{a.owner_name ? `${a.owner_name} (${a.owner})` : a.owner}</dd>
                {a.description && <><dt>Description</dt><dd>{a.description}</dd></>}
                {a.source_question && <><dt>Original question</dt><dd>{a.source_question}</dd></>}
                {a.tags.length > 0 && <><dt>Tags</dt><dd>{a.tags.map((t) => <span className="tag" key={t}>{t}</span>)}</dd></>}
                {a.data_as_of && <><dt>Data as of</dt><dd>{a.data_as_of}</dd></>}
                {a.sources.length > 0 && <><dt>Sources</dt><dd>{a.sources.join(', ')}</dd></>}
                <dt>Type</dt><dd>{a.kind}</dd>
                <dt>Created</dt><dd>{new Date(a.created_at).toLocaleString()} via {a.created_via === 'mcp' ? 'MCP' : 'web'}</dd>
                {a.sensitive && <><dt>Sensitive</dt><dd>yes</dd></>}
              </dl>
            </aside>
          )}
        </div>
      </div>
      {sharing && config && me && (
        <ShareDialog artifact={a} api={api} canPublishOrg={me.is_publisher} publisherGroup={config.publisher_group}
          maxPeople={config.max_shared_with} onClose={() => setSharing(false)} onSaved={() => refresh()} />
      )}
      {renaming && (
        <Modal title="Rename artifact" onClose={() => setRenaming(false)}>
          <form onSubmit={(e) => { e.preventDefault(); doRename() }}>
            <input className="full" autoFocus value={renameValue} maxLength={200} onChange={(e) => setRenameValue(e.target.value)} aria-label="Title" />
            <p className="muted small">Renaming does not create a new version.</p>
            <div className="modal-actions"><span className="spacer" />
              <button type="button" className="btn" onClick={() => setRenaming(false)}>Cancel</button>
              <button type="submit" className="btn primary" disabled={!renameValue.trim()}>Save</button>
            </div>
          </form>
        </Modal>
      )}
    </>
  )
}
