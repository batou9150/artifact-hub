import { API_BASE } from './config'

export type Kind = 'html' | 'markdown' | 'text'
export type Visibility = 'private' | 'shared'
export type Access = 'owner' | 'invited' | 'organisation' | 'none'

export type Artifact = {
  id: string
  owner: string
  owner_name: string
  title: string
  kind: Kind
  visibility: Visibility
  current_version: number
  size: number
  description: string
  source_question: string
  tags: string[]
  sources: string[]
  data_as_of: string
  sensitive: boolean
  created_at: string
  updated_at: string
  created_via: string
  url: string
  access: Access
  can_manage: boolean
  can_delete: boolean
  shared_with?: string[]      // owner only
  viewers_count?: number      // owner only
  view_count?: number         // owner only
  moderation?: Moderation     // owner only: last administrator action
  warnings?: string[]
}

export type Moderation = { by: string; action: string; note: string; at: string }

export type AdminArtifact = Pick<Artifact, 'id' | 'owner' | 'owner_name' | 'title' | 'kind' | 'visibility' |
  'current_version' | 'size' | 'description' | 'tags' | 'sensitive' | 'created_at' | 'updated_at' | 'created_via' |
  'url'> & { view_count?: number; shared_with_count: number; moderation?: Moderation }

export type AdminStats = {
  artifacts: number
  bytes: number
  owners: number
  sensitive: number
  by_visibility: Record<string, number>
  by_kind: Record<string, number>
  by_via: Record<string, number>
  top_owners: { email: string; artifacts: number }[]
}

export type AdminFilters = { q?: string; owner?: string; visibility?: '' | Visibility; sensitive?: '' | 'true' | 'false' }
export type ModerationInput = { withdraw_org?: boolean; clear_invites?: boolean; sensitive?: boolean; note?: string }

export type Version = { n: number; size: number; author: string; created_at: string }
export type SearchHit = Pick<Artifact, 'id' | 'title' | 'kind' | 'owner' | 'owner_name' | 'description' |
  'source_question' | 'tags' | 'visibility' | 'current_version' | 'updated_at' | 'url' | 'access'> & { score: number }
export type Person = { email: string; name: string }

export type MetadataInput = {
  description?: string
  source_question?: string
  tags?: string[]
  sensitive?: boolean
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export type TokenSource = () => Promise<string | null>

export function createApi(getToken: TokenSource) {
  async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = await getToken()
    if (!token) throw new ApiError(401, 'Your session has expired. Sign in again.')
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(init.headers || {}) },
    })
    if (!res.ok) {
      let detail = `Request failed (${res.status}).`
      try { detail = (await res.json()).detail || detail } catch { /* keep default */ }
      if (res.status === 404) detail = 'This artifact is not available.'
      throw new ApiError(res.status, detail)
    }
    return res.json() as Promise<T>
  }
  const json = (body: unknown) => JSON.stringify(body)

  return {
    mine: () => call<{ artifacts: Artifact[] }>('/api/artifacts/mine').then((r) => r.artifacts),
    sharedWithMe: () => call<{ artifacts: Artifact[] }>('/api/artifacts/shared').then((r) => r.artifacts),
    search: (q: string) =>
      call<{ results: SearchHit[] }>(`/api/artifacts/search?q=${encodeURIComponent(q)}&limit=20`).then((r) => r.results),
    get: (id: string) => call<{ artifact: Artifact; render_url: string }>(`/api/artifacts/${id}`),
    body: (id: string) => call<{ body: string; kind: Kind; version: number }>(`/api/artifacts/${id}/body`),
    create: (input: { title: string; kind: Kind; body: string } & MetadataInput) =>
      call<{ artifact: Artifact }>('/api/artifacts', { method: 'POST', body: json(input) }).then((r) => r.artifact),
    update: (id: string, input: { body?: string; title?: string } & MetadataInput) =>
      call<{ artifact: Artifact }>(`/api/artifacts/${id}`, { method: 'PATCH', body: json(input) }).then((r) => r.artifact),
    rename: (id: string, title: string) =>
      call<{ artifact: Artifact }>(`/api/artifacts/${id}`, { method: 'PATCH', body: json({ title }) }).then((r) => r.artifact),
    share: (id: string, input: { visibility?: Visibility; shared_with?: string[] }) =>
      call<{ artifact: Artifact }>(`/api/artifacts/${id}/sharing`, { method: 'PUT', body: json(input) }).then((r) => r.artifact),
    versions: (id: string) => call<{ versions: Version[] }>(`/api/artifacts/${id}/versions`).then((r) => r.versions),
    versionRender: (id: string, n: number) =>
      call<{ render_url: string }>(`/api/artifacts/${id}/versions/${n}/render`).then((r) => r.render_url),
    revert: (id: string, version: number) =>
      call<{ artifact: Artifact }>(`/api/artifacts/${id}/revert`, { method: 'POST', body: json({ version }) }).then((r) => r.artifact),
    remove: (id: string) => call<{ ok: boolean }>(`/api/artifacts/${id}`, { method: 'DELETE' }),
    recordView: (id: string) => call<{ ok: boolean }>(`/api/artifacts/${id}/views`, { method: 'POST' }).catch(() => null),
    admin: {
      stats: () => call<AdminStats>('/api/admin/stats'),
      artifacts: (f: AdminFilters = {}) => {
        const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString()
        return call<{ artifacts: AdminArtifact[]; total: number }>(`/api/admin/artifacts${qs ? `?${qs}` : ''}`)
      },
      moderate: (id: string, input: ModerationInput) =>
        call<{ artifact: AdminArtifact }>(`/api/admin/artifacts/${id}/moderate`, { method: 'POST', body: json(input) })
          .then((r) => r.artifact),
      remove: (id: string, note = '') =>
        call<{ ok: boolean }>(`/api/admin/artifacts/${id}?note=${encodeURIComponent(note)}`, { method: 'DELETE' }),
    },
    people: (q: string) => call<{ people: Person[] }>(`/api/people?q=${encodeURIComponent(q)}`).then((r) => r.people),
  }
}

export type Api = ReturnType<typeof createApi>
