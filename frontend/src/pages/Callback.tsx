import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { takeReturnTo, useAuth } from '../lib/auth'

export function Callback() {
  const { state, completeCallback } = useAuth()
  const navigate = useNavigate()
  const done = useRef(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (state.phase === 'loading' || done.current) return
    done.current = true
    completeCallback().then(() => navigate(takeReturnTo(), { replace: true }))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [state.phase, completeCallback, navigate])
  return <main className="page narrow"><p className={error ? 'error' : 'muted'}>{error ?? 'Signing you in…'}</p></main>
}
