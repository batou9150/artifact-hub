// Where the API lives. In the production image the SPA is served by the API
// service itself (same origin, empty base). In local development the Vite dev
// server runs on :5173 and the API on :8080 (override with VITE_API_BASE).
export const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? 'http://localhost:8080' : '')

export type DevUser = { email: string; name: string; groups: string[] }

export type HubConfig = {
  auth_mode: 'dev' | 'oidc'
  publisher_group: string
  max_body_bytes: number
  max_shared_with: number
  api_base: string
  mcp_url: string
  oidc?: { issuer: string; client_id: string; scopes: string; ui_token: 'access' | 'id' }
  dev_users?: DevUser[]
}

export async function loadConfig(): Promise<HubConfig> {
  const res = await fetch(`${API_BASE}/api/config`)
  if (!res.ok) throw new Error(`Cannot reach the API (${res.status}).`)
  return res.json()
}
