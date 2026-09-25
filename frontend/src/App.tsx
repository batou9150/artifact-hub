import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './lib/auth'
import { Admin } from './pages/Admin'
import { Callback } from './pages/Callback'
import { Editor } from './pages/Editor'
import { Gallery } from './pages/Gallery'
import { Login } from './pages/Login'
import { Viewer } from './pages/Viewer'

export function App() {
  const { state } = useAuth()
  if (state.phase === 'loading') return <main className="page narrow"><p className="muted">Loading…</p></main>
  if (state.phase === 'error') {
    return <main className="page narrow"><h1>Artifact Hub</h1><p className="error">{state.message}</p></main>
  }
  return (
    <Routes>
      <Route path="/callback" element={<Callback />} />
      {state.phase === 'signedOut' ? (
        <Route path="*" element={<Login />} />
      ) : (
        <>
          <Route path="/" element={<Gallery />} />
          <Route path="/new" element={<Editor />} />
          <Route path="/artifacts/:id" element={<Viewer />} />
          <Route path="/artifacts/:id/edit" element={<Editor />} />
          {state.me.is_admin && <Route path="/admin" element={<Admin />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </>
      )}
    </Routes>
  )
}
