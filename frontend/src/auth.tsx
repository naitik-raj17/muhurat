import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken, setToken, unwrap, type Schemas } from './api/client'

type Me = Schemas['MeOut']

interface AuthState {
  me: Me | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  can: (permission: string) => boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const [token, setTokenState] = useState<string | null>(getToken)

  const meQuery = useQuery({
    queryKey: ['me', token],
    queryFn: () => unwrap(api.GET('/api/v1/auth/me')),
    enabled: token !== null,
    retry: false,
  })

  const logout = useCallback(() => {
    setToken(null)
    setTokenState(null)
    qc.clear()
  }, [qc])

  useEffect(() => {
    window.addEventListener('wedding-ai:logout', logout)
    return () => window.removeEventListener('wedding-ai:logout', logout)
  }, [logout])

  const login = useCallback(async (email: string, password: string) => {
    const res = await unwrap(api.POST('/api/v1/auth/login', { body: { email, password } }))
    setToken(res.access_token)
    setTokenState(res.access_token)
  }, [])

  const value = useMemo<AuthState>(() => {
    const me = token ? (meQuery.data ?? null) : null
    return {
      me,
      loading: token !== null && meQuery.isPending,
      login,
      logout,
      can: (p) => me?.permissions.includes(p) ?? false,
    }
  }, [token, meQuery.data, meQuery.isPending, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
