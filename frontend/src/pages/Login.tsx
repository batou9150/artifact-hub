import { AppBar } from '../components/AppBar'
import { useAuth } from '../lib/auth'

export function Login() {
  const { state, signIn } = useAuth()
  if (state.phase !== 'signedOut') return null
  const cfg = state.config
  return (
    <>
      <AppBar />
      <main className="page narrow">
        <h1>Sign in</h1>
        <p className="lede">Publish HTML, Markdown or text artifacts, share them with colleagues and find analyses that already exist.</p>
        {cfg.auth_mode === 'oidc' ? (
          <button type="button" className="btn primary" onClick={() => signIn()}>Sign in with your organisation account</button>
        ) : (
          <div className="dev-login">
            <p className="muted">Pick a fake identity (dev auth mode):</p>
            <ul className="dev-users">
              {cfg.dev_users?.map((u) => (
                <li key={u.email}>
                  <button type="button" className="dev-user" onClick={() => signIn(u)}>
                    <span className="avatar">{u.name[0]}</span>
                    <span className="combo-id">
                      <span className="combo-name">{u.name}</span>
                      <span className="combo-email">{u.email}{u.groups.length ? ` · ${u.groups.join(', ')}` : ''}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </main>
    </>
  )
}
