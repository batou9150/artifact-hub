import { useMemo } from 'react'
import { useAuth } from './auth'
import { createApi } from './api'

export function useApi() {
  const { getToken } = useAuth()
  return useMemo(() => createApi(getToken), [getToken])
}
