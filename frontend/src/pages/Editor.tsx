import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { AppBar } from '../components/AppBar'
import { Icon } from '../components/Icons'
import type { Artifact, Kind } from '../lib/api'
import { useAuth } from '../lib/auth'
import { formatBytes } from '../lib/format'
import { useApi } from '../lib/useApi'

const KINDS: { key: Kind; label: string }[] = [
  { key: 'html', label: 'HTML (self-contained, scripts allowed)' },
  { key: 'markdown', label: 'Markdown' },
  { key: 'text', label: 'Plain text' },
]

const bytes = (s: string) => new TextEncoder().encode(s).length
const parseTags = (s: string) => s.split(',').map((t) => t.trim()).filter(Boolean)

export function Editor() {
  const { id } = useParams()
  const editing = !!id
  const api = useApi()
  const { state } = useAuth()
  const navigate = useNavigate()
  const cap = state.phase === 'signedIn' ? state.config.max_body_bytes : 800_000

  const [original, setOriginal] = useState<{ art: Artifact; body: string } | null>(null)
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState<Kind>('html')
  const [body, setBody] = useState('')
  const [description, setDescription] = useState('')
  const [question, setQuestion] = useState('')
  const [tags, setTags] = useState('')
  const [sensitive, setSensitive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])

  useEffect(() => {
    if (!id) return
    let alive = true
    Promise.all([api.get(id), api.body(id)]).then(([meta, b]) => {
      if (!alive) return
      const a = meta.artifact
      if (!a.can_manage) { setError('Only the owner can edit this artifact.'); return }
      setOriginal({ art: a, body: b.body })
      setTitle(a.title); setKind(a.kind); setBody(b.body); setDescription(a.description)
      setQuestion(a.source_question); setTags(a.tags.join(', ')); setSensitive(a.sensitive)
    }).catch((e) => alive && setError(e.message))
    return () => { alive = false }
  }, [id, api])

  const size = useMemo(() => bytes(body), [body])
  const over = size > cap

  const save = async () => {
    setBusy(true); setError(null); setWarnings([])
    try {
      let art: Artifact
      if (!editing) {
        art = await api.create({ title, kind, body, description, source_question: question, tags: parseTags(tags), sensitive })
      } else {
        const o = original!
        const patch: Parameters<typeof api.update>[1] = {}
        if (body !== o.body) patch.body = body
        if (title !== o.art.title) patch.title = title
        if (description !== o.art.description) patch.description = description
        if (question !== o.art.source_question) patch.source_question = question
        if (tags !== o.art.tags.join(', ')) patch.tags = parseTags(tags)
        if (sensitive !== o.art.sensitive) patch.sensitive = sensitive
        art = Object.keys(patch).length ? await api.update(id!, patch) : o.art
      }
      if (art.warnings?.length) { setWarnings(art.warnings); setOriginal({ art, body }); return }
      navigate(`/artifacts/${art.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed.')
    } finally { setBusy(false) }
  }

  return (
    <>
      <AppBar />
      <main className="page">
        <div className="page-head">
          <Link to={editing ? `/artifacts/${id}` : '/'} className="btn ghost small">{Icon.back} Back</Link>
          <h1>{editing ? 'Edit artifact' : 'New artifact'}</h1>
          <span className="spacer" />
        </div>
        <form className="editor" onSubmit={(e) => { e.preventDefault(); save() }}>
          <label className="field">
            <span>Title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required autoFocus={!editing} />
          </label>
          <label className="field">
            <span>Type</span>
            <select value={kind} onChange={(e) => setKind(e.target.value as Kind)} disabled={editing}>
              {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Content {editing && <small className="muted">(saving a change creates version {(original?.art.current_version ?? 0) + 1})</small>}</span>
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={18} spellCheck={false}
              placeholder={kind === 'html' ? '<!doctype html>… inline every script, style and dataset: the sandbox has no network access' : ''} required />
            <small className={over ? 'error' : 'muted'}>{formatBytes(size)} of {formatBytes(cap)}</small>
          </label>
          <details className="meta-fields" open={editing || undefined}>
            <summary>Description and search metadata</summary>
            <label className="field">
              <span>Description</span>
              <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} maxLength={2000} />
            </label>
            <label className="field">
              <span>Original question</span>
              <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={2000}
                placeholder="The question this artifact answers (used by search)" />
            </label>
            <label className="field">
              <span>Tags <small className="muted">(comma separated)</small></span>
              <input value={tags} onChange={(e) => setTags(e.target.value)} />
            </label>
            <label className="check">
              <input type="checkbox" checked={sensitive} onChange={(e) => setSensitive(e.target.checked)} />
              Contains sensitive data (cannot be shared with the whole organisation)
            </label>
          </details>
          {warnings.map((w) => <p key={w} className="warning">{w} <Link to={`/artifacts/${original?.art.id}`}>Open it anyway</Link></p>)}
          {error && <p className="error">{error}</p>}
          <div className="form-actions">
            <button type="submit" className="btn primary" disabled={busy || over || !title.trim() || !body}>
              {busy ? 'Saving…' : editing ? 'Save' : 'Create (private)'}
            </button>
          </div>
        </form>
      </main>
    </>
  )
}
