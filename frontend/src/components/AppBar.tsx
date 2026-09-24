import { Link } from 'react-router-dom'
import { useAuth } from '../lib/auth'

export function DevBanner() {
  const { state } = useAuth()
  const mode = state.phase === 'signedIn' || state.phase === 'signedOut' ? state.config.auth_mode : null
  if (mode !== 'dev') return null
  return (
    <div className="dev-banner" role="status">
      DEV AUTH MODE: fake identities, no real sign-in. Never expose this configuration.
    </div>
  )
}

export function AppBar({ children }: { children?: React.ReactNode }) {
  const { state, signOut } = useAuth()
  return (
    <>
      <DevBanner />
      <header className="appbar">
        <Link to="/" className="brand"><span className="brand-mark" aria-hidden="true" />Artifact Hub</Link>
        <div className="appbar-center">{children}</div>
        {state.phase === 'signedIn' && (
          <div className="appbar-user">
            <span className="avatar" title={state.me.email}>{(state.me.name || state.me.email)[0].toUpperCase()}</span>
            <span className="appbar-id">
              <span className="appbar-name">{state.me.name || state.me.email}</span>
              <span className="appbar-roles">
                {state.me.is_admin ? 'admin' : state.me.is_publisher ? 'publisher' : 'member'}
              </span>
            </span>
            <button type="button" className="btn ghost small" onClick={signOut}>Sign out</button>
          </div>
        )}
      </header>
    </>
  )
}
