import { useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import type { Api, Artifact, Person, Visibility } from '../lib/api'
import { EMAIL_RE, nameFromEmail } from '../lib/format'
import { Icon } from './Icons'
import { Modal } from './Modal'

type Mode = 'private' | 'people' | 'organisation'

function initialMode(a: Artifact): Mode {
  if (a.visibility === 'shared') return 'organisation'
  return (a.shared_with?.length ?? 0) > 0 ? 'people' : 'private'
}

export function ShareDialog({ artifact, api, canPublishOrg, publisherGroup, maxPeople, onClose, onSaved }: {
  artifact: Artifact
  api: Api
  canPublishOrg: boolean
  publisherGroup: string
  maxPeople: number
  onClose: () => void
  onSaved: (a: Artifact) => void
}) {
  const [mode, setMode] = useState<Mode>(initialMode(artifact))
  const [people, setPeople] = useState<string[]>(artifact.shared_with ?? [])
  const [q, setQ] = useState('')
  const [suggestions, setSuggestions] = useState<Person[]>([])
  const [names, setNames] = useState<Record<string, string>>({})
  const [hi, setHi] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const orgBlocked = !canPublishOrg
    ? `Only members of the "${publisherGroup}" group can share with the whole organisation.`
    : artifact.sensitive ? 'This artifact is flagged sensitive: organisation-wide sharing is disabled.' : null

  // Debounced lookup in the directory of people who already signed in.
  useEffect(() => {
    const needle = q.trim()
    if (needle.length < 2) { setSuggestions([]); return }
    let alive = true
    const t = window.setTimeout(async () => {
      try {
        const found = await api.people(needle)
        if (!alive) return
        setNames((prev) => ({ ...prev, ...Object.fromEntries(found.map((p) => [p.email, p.name])) }))
        setSuggestions(found.filter((p) => !people.includes(p.email) && p.email !== artifact.owner).slice(0, 6))
        setHi(0)
      } catch { /* suggestions are a convenience */ }
    }, 200)
    return () => { alive = false; window.clearTimeout(t) }
  }, [q, api, people, artifact.owner])

  const raw = q.trim().toLowerCase()
  const rawInvite = EMAIL_RE.test(raw) && !people.includes(raw) && raw !== artifact.owner
    && !suggestions.some((s) => s.email === raw) ? raw : null
  const options = useMemo(() => [...suggestions.map((s) => s.email), ...(rawInvite ? [rawInvite] : [])],
    [suggestions, rawInvite])

  const add = (email: string) => {
    setError(null)
    if (people.length >= maxPeople) { setError(`At most ${maxPeople} people.`); return }
    setPeople((p) => [...p, email.toLowerCase()])
    setQ(''); setSuggestions([])
    if (mode === 'private') setMode('people')
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, options.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
    else if (e.key === 'Enter' && options.length) { e.preventDefault(); add(options[Math.min(hi, options.length - 1)]) }
  }

  const save = async () => {
    setBusy(true); setError(null)
    try {
      const visibility: Visibility = mode === 'organisation' ? 'shared' : 'private'
      const shared_with = mode === 'private' ? [] : people
      onSaved(await api.share(artifact.id, { visibility, shared_with }))
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sharing failed.')
    } finally { setBusy(false) }
  }

  const copy = () => {
    navigator.clipboard?.writeText(artifact.url).catch(() => {})
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  return (
    <Modal title={`Share "${artifact.title}"`} onClose={onClose}>
      <fieldset className="share-modes">
        <legend className="sr-only">Who can open this artifact</legend>
        <label className={'share-mode' + (mode === 'private' ? ' active' : '')}>
          <input type="radio" name="mode" checked={mode === 'private'} onChange={() => setMode('private')} />
          {Icon.lock}<span><b>Private</b><small>Only you</small></span>
        </label>
        <label className={'share-mode' + (mode === 'people' ? ' active' : '')}>
          <input type="radio" name="mode" checked={mode === 'people'} onChange={() => setMode('people')} />
          {Icon.people}<span><b>Specific people</b><small>You and the people listed below</small></span>
        </label>
        <label className={'share-mode' + (mode === 'organisation' ? ' active' : '') + (orgBlocked ? ' disabled' : '')}
          title={orgBlocked ?? undefined}>
          <input type="radio" name="mode" checked={mode === 'organisation'} disabled={!!orgBlocked}
            onChange={() => setMode('organisation')} />
          {Icon.globe}<span><b>Whole organisation</b><small>{orgBlocked ?? 'Anyone signed in, with the link'}</small></span>
        </label>
      </fieldset>

      {mode !== 'private' && (
        <div className="share-people">
          <div className="combo">
            <input
              type="search"
              placeholder="Add people by name or email"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKey}
              role="combobox"
              aria-expanded={options.length > 0}
              aria-autocomplete="list"
              aria-label="Add people"
            />
            {options.length > 0 && (
              <ul className="combo-menu" role="listbox">
                {options.map((email, i) => (
                  <li key={email}>
                    <button type="button" role="option" aria-selected={i === hi} className={i === hi ? 'active' : ''}
                      onMouseEnter={() => setHi(i)} onClick={() => add(email)}>
                      <span className="avatar small">{(names[email] || email)[0].toUpperCase()}</span>
                      <span className="combo-id">
                        <span className="combo-name">{email === rawInvite ? `Invite ${email}` : names[email] || nameFromEmail(email)}</span>
                        <span className="combo-email">{email === rawInvite ? 'Not in the directory yet' : email}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="people-count">{people.length} / {maxPeople} people</div>
          <ul className="people-list">
            {people.map((e) => (
              <li key={e}>
                <span className="avatar small">{(names[e] || e)[0].toUpperCase()}</span>
                <span className="combo-id">
                  <span className="combo-name">{names[e] || nameFromEmail(e)}</span>
                  <span className="combo-email">{e}</span>
                </span>
                <button type="button" className="btn ghost small" onClick={() => setPeople((p) => p.filter((x) => x !== e))}
                  aria-label={`Remove ${e}`}>Remove</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {error && <p className="error">{error}</p>}
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={copy}>{Icon.link} {copied ? 'Link copied' : 'Copy link'}</button>
        <span className="spacer" />
        <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="button" className="btn primary" onClick={save} disabled={busy}>Save</button>
      </div>
    </Modal>
  )
}
