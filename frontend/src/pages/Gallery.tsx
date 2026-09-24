import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { AppBar } from '../components/AppBar'
import { Icon, KindIcon } from '../components/Icons'
import { ShareDialog } from '../components/ShareDialog'
import type { Artifact, SearchHit } from '../lib/api'
import { useAuth } from '../lib/auth'
import { timeAgo } from '../lib/format'
import { useApi } from '../lib/useApi'

type Tab = 'mine' | 'shared'

function accessBadge(a: { access: string; visibility: string; shared_with?: string[] }) {
  if (a.access === 'invited') return { icon: Icon.people, label: 'Shared with you' }
  if (a.access === 'organisation') return { icon: Icon.globe, label: 'Organisation' }
  if (a.visibility === 'shared') return { icon: Icon.globe, label: 'Organisation' }
  if ((a.shared_with?.length ?? 0) > 0) return { icon: Icon.people, label: `${a.shared_with!.length} people` }
  return { icon: Icon.lock, label: 'Private' }
}

export function Gallery() {
  const api = useApi()
  const { state } = useAuth()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab: Tab = params.get('tab') === 'shared' ? 'shared' : 'mine'
  const [mine, setMine] = useState<Artifact[] | null>(null)
  const [shared, setShared] = useState<Artifact[] | null>(null)
  const [query, setQuery] = useState(params.get('q') ?? '')
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [menu, setMenu] = useState<string | null>(null)
  const [sharing, setSharing] = useState<Artifact | null>(null)
  const [error, setError] = useState<string | null>(null)

  const me = state.phase === 'signedIn' ? state.me : null
  const config = state.phase === 'signedIn' ? state.config : null

  const reload = useCallback(() => {
    api.mine().then(setMine).catch((e) => setError(e.message))
    api.sharedWithMe().then(setShared).catch((e) => setError(e.message))
  }, [api])
  useEffect(reload, [reload])

  useEffect(() => {
    if (!menu) return
    const close = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest?.('.card-menu')) setMenu(null) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [menu])

  const runSearch = useCallback(async (q: string) => {
    const needle = q.trim()
    setParams((p) => { needle ? p.set('q', needle) : p.delete('q'); return p }, { replace: true })
    if (!needle) { setHits(null); return }
    setSearching(true); setError(null)
    try { setHits(await api.search(needle)) } catch (e) { setError(e instanceof Error ? e.message : 'Search failed.') }
    finally { setSearching(false) }
  }, [api, setParams])

  // Restore a deep-linked search (?q=...) once, on first render.
  const [initialQuery] = useState(params.get('q'))
  useEffect(() => { if (initialQuery) runSearch(initialQuery) }, [initialQuery, runSearch])

  const act = async (fn: () => Promise<unknown>) => {
    setMenu(null); setError(null)
    try { await fn(); reload() } catch (e) { setError(e instanceof Error ? e.message : 'Action failed.') }
  }

  const list = useMemo(() => (tab === 'mine' ? mine : shared), [tab, mine, shared])

  return (
    <>
      <AppBar />
      <main className="page">
        <div className="page-head">
          <h1>Artifacts</h1>
          <Link to="/new" className="btn primary">{Icon.plus} New artifact</Link>
        </div>

        <form className="search" role="search" onSubmit={(e) => { e.preventDefault(); runSearch(query) }}>
          {Icon.search}
          <input type="search" value={query} onChange={(e) => { setQuery(e.target.value); if (!e.target.value) runSearch('') }}
            placeholder="Find a similar analysis: describe the question in your own words" aria-label="Search artifacts" />
          <button type="submit" className="btn small" disabled={searching}>{searching ? 'Searching…' : 'Search'}</button>
        </form>

        {error && <p className="error">{error}</p>}

        {hits !== null ? (
          <section aria-label="Search results">
            <div className="section-head">
              <h2>{hits.length ? `${hits.length} similar artifact${hits.length > 1 ? 's' : ''}` : 'No similar artifact found'}</h2>
              <button type="button" className="btn ghost small" onClick={() => { setQuery(''); runSearch('') }}>Clear search</button>
            </div>
            <p className="muted small">Ranked by similarity of title, description and original question. Only artifacts you can open are listed.</p>
            <ul className="results">
              {hits.map((h) => {
                const badge = accessBadge(h)
                return (
                  <li key={h.id}>
                    <Link to={`/artifacts/${h.id}`} className="result">
                      <span className="result-title">{h.title}</span>
                      {h.description && <span className="result-desc">{h.description}</span>}
                      {h.source_question && <span className="result-q">“{h.source_question}”</span>}
                      <span className="card-foot">{badge.icon}<span>{badge.label}</span><span>·</span>
                        <span>{h.owner_name || h.owner}</span><span>·</span><span>{timeAgo(h.updated_at)}</span>
                        <span className="score" title="Similarity score">{Math.round(h.score * 100)}%</span></span>
                    </Link>
                  </li>
                )
              })}
            </ul>
          </section>
        ) : (
          <>
            <div className="tabs" role="tablist">
              <button type="button" role="tab" aria-selected={tab === 'mine'} className={'tab' + (tab === 'mine' ? ' active' : '')}
                onClick={() => setParams({})}>My artifacts {mine && <span className="count">{mine.length}</span>}</button>
              <button type="button" role="tab" aria-selected={tab === 'shared'} className={'tab' + (tab === 'shared' ? ' active' : '')}
                onClick={() => setParams({ tab: 'shared' })}>Shared with me {shared && <span className="count">{shared.length}</span>}</button>
            </div>

            {list === null ? <p className="muted">Loading…</p> : list.length === 0 ? (
              <div className="empty">
                {tab === 'mine'
                  ? <><p>No artifact yet.</p><p className="muted">Paste one with “New artifact”, or publish from an MCP client ({config?.mcp_url}).</p></>
                  : <p className="muted">Nothing has been shared with you yet.</p>}
              </div>
            ) : (
              <ul className="grid">
                {list.map((a) => {
                  const badge = accessBadge(a)
                  return (
                    <li className="card" key={a.id}>
                      <Link to={`/artifacts/${a.id}`} className="card-main">
                        <span className="card-preview"><KindIcon kind={a.kind} /></span>
                        <span className="card-body">
                          <span className="card-title">{a.title}</span>
                          {a.description && <span className="card-desc">{a.description}</span>}
                          <span className="card-foot">
                            {badge.icon}<span>{badge.label}</span><span>·</span><span>v{a.current_version}</span>
                            <span>·</span><span>{timeAgo(a.updated_at)}</span>
                            {tab === 'shared' && <><span>·</span><span>{a.owner_name || a.owner}</span></>}
                            {a.can_manage && (a.viewers_count ?? 0) > 0 && (
                              <span className="views" title="Distinct viewers">{Icon.eye}{a.viewers_count}</span>
                            )}
                          </span>
                        </span>
                      </Link>
                      <div className="card-menu">
                        <button type="button" className={'icon-btn' + (menu === a.id ? ' open' : '')} aria-label="Actions"
                          aria-haspopup="menu" aria-expanded={menu === a.id} onClick={() => setMenu((m) => (m === a.id ? null : a.id))}>
                          {Icon.dots}
                        </button>
                        {menu === a.id && (
                          <div className="dropdown" role="menu">
                            <button type="button" role="menuitem" onClick={() => { navigator.clipboard?.writeText(a.url); setMenu(null) }}>{Icon.link} Copy link</button>
                            {a.can_manage && <>
                              <button type="button" role="menuitem" onClick={() => navigate(`/artifacts/${a.id}/edit`)}>{Icon.pencil} Edit</button>
                              <button type="button" role="menuitem" onClick={() => { setSharing(a); setMenu(null) }}>{Icon.people} Share…</button>
                              {a.visibility === 'shared'
                                ? <button type="button" role="menuitem" onClick={() => act(() => api.share(a.id, { visibility: 'private' }))}>{Icon.lock} Stop organisation sharing</button>
                                : me?.is_publisher && !a.sensitive && (
                                  <button type="button" role="menuitem" onClick={() => act(() => api.share(a.id, { visibility: 'shared' }))}>{Icon.globe} Share with organisation</button>
                                )}
                            </>}
                            {a.can_delete && <>
                              <div className="sep" />
                              <button type="button" role="menuitem" className="danger"
                                onClick={() => { if (confirm(`Delete "${a.title}" and all its versions?`)) act(() => api.remove(a.id)); else setMenu(null) }}>
                                {Icon.trash} Delete
                              </button>
                            </>}
                          </div>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </>
        )}
      </main>
      {sharing && config && me && (
        <ShareDialog artifact={sharing} api={api} canPublishOrg={me.is_publisher} publisherGroup={config.publisher_group}
          maxPeople={config.max_shared_with} onClose={() => setSharing(null)} onSaved={() => reload()} />
      )}
    </>
  )
}
