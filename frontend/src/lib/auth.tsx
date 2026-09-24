import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'
import { API_BASE, loadConfig, type DevUser, type HubConfig } from './config'

// Two sign-in paths behind one interface:
//  * oidc: authorization code + PKCE against the configured IdP (Entra ID, Okta,
//    Google...) with oidc-client-ts; tokens live in sessionStorage of the app
//    origin, which artifact code can never read (it runs in an opaque origin);
//  * dev:  a picker of fake identities; the API accepts `Bearer dev:<email>` only
//    when it runs with AUTH_MODE=dev. The UI shows a permanent banner.

export type Me = { email: string; name: string; is_publisher: boolean; is_admin: boolean; auth_mode: string }

type AuthState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'signedOut'; config: HubConfig }
  | { phase: 'signedIn'; config: HubConfig; me: Me }

type AuthApi = {
  state: AuthState
  getToken: () => Promise<string | null>
  signIn: (devUser?: DevUser) => Promise<void>
  signOut: () => Promise<void>
  completeCallback: () => Promise<void>
}

const AuthContext = createContext<AuthApi | null>(null)
const DEV_KEY = 'artifact-hub.dev-user'

function safeGet(key: string): string | null {
  try { return sessionStorage.getItem(key) } catch { return null }
}
function safeSet(key: string, value: string | null) {
  try { value === null ? sessionStorage.removeItem(key) : sessionStorage.setItem(key, value) } catch { /* ignore */ }
}

let manager: UserManager | null = null
function userManager(config: HubConfig): UserManager {
  if (!manager && config.oidc) {
    manager = new UserManager({
      authority: config.oidc.issuer,
      client_id: config.oidc.client_id,
      redirect_uri: `${window.location.origin}/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: 'code', // PKCE is always on in oidc-client-ts for the code flow
      scope: config.oidc.scopes,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      automaticSilentRenew: false,
    })
  }
  return manager!
}

function tokenOf(config: HubConfig, user: User | null): string | null {
  if (!user || user.expired) return null
  return config.oidc?.ui_token === 'id' ? user.id_token ?? null : user.access_token
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ phase: 'loading' })
  const [config, setConfig] = useState<HubConfig | null>(null)

  const getToken = useCallback(async (): Promise<string | null> => {
    if (!config) return null
    if (config.auth_mode === 'dev') {
      const email = safeGet(DEV_KEY)
      return email ? `dev:${email}` : null
    }
    return tokenOf(config, await userManager(config).getUser())
  }, [config])

  const refreshMe = useCallback(async (cfg: HubConfig, token: string | null) => {
    if (!token) { setState({ phase: 'signedOut', config: cfg }); return }
    const me = await fetch(`${API_BASE}/api/me`, { headers: { Authorization: `Bearer ${token}` } })
    if (!me.ok) { setState({ phase: 'signedOut', config: cfg }); return }
    setState({ phase: 'signedIn', config: cfg, me: await me.json() })
  }, [])

  useEffect(() => {
    let alive = true
    loadConfig()
      .then(async (cfg) => {
        if (!alive) return
        setConfig(cfg)
        if (window.location.pathname === '/callback') { setState({ phase: 'signedOut', config: cfg }); return }
        const token = cfg.auth_mode === 'dev'
          ? (safeGet(DEV_KEY) ? `dev:${safeGet(DEV_KEY)}` : null)
          : tokenOf(cfg, await userManager(cfg).getUser())
        await refreshMe(cfg, token)
      })
      .catch((e) => alive && setState({ phase: 'error', message: e instanceof Error ? e.message : String(e) }))
    return () => { alive = false }
  }, [refreshMe])

  const signIn = useCallback(async (devUser?: DevUser) => {
    if (!config) return
    if (config.auth_mode === 'dev') {
      if (!devUser) return
      safeSet(DEV_KEY, devUser.email)
      await refreshMe(config, `dev:${devUser.email}`)
      return
    }
    safeSet('artifact-hub.return-to', window.location.pathname + window.location.search)
    await userManager(config).signinRedirect()
  }, [config, refreshMe])

  const signOut = useCallback(async () => {
    if (!config) return
    if (config.auth_mode === 'dev') {
      safeSet(DEV_KEY, null)
      setState({ phase: 'signedOut', config })
      return
    }
    await userManager(config).removeUser()
    setState({ phase: 'signedOut', config })
  }, [config])

  const completeCallback = useCallback(async () => {
    if (!config || config.auth_mode !== 'oidc') return
    const user = await userManager(config).signinRedirectCallback()
    await refreshMe(config, tokenOf(config, user))
  }, [config, refreshMe])

  const api = useMemo<AuthApi>(() => ({ state, getToken, signIn, signOut, completeCallback }),
    [state, getToken, signIn, signOut, completeCallback])
  return <AuthContext.Provider value={api}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}

export function takeReturnTo(): string {
  const v = safeGet('artifact-hub.return-to') || '/'
  safeSet('artifact-hub.return-to', null)
  return v
}
