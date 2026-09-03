import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { api, onUnauthorized, tokenStore } from '../lib/api'
import type { AuthContextPayload, TokenResponse, User } from '../lib/types'

interface AuthState {
  user: User | null
  demoMode: boolean
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (input: RegisterInput) => Promise<void>
  logout: () => Promise<void>
  refreshContext: () => Promise<void>
}

export interface RegisterInput {
  email: string
  username: string
  password: string
  full_name?: string
}

const AuthCtx = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [demoMode, setDemoMode] = useState(false)
  const [loading, setLoading] = useState(true)
  const queryClient = useQueryClient()

  const loadContext = useCallback(async () => {
    if (!tokenStore.access()) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      const context = await api.get<AuthContextPayload>('/api/v1/auth/me')
      setUser(context.user)
      setDemoMode(context.demo_mode)
    } catch {
      tokenStore.clear()
      setUser(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadContext()
  }, [loadContext])

  // A failed refresh means the session is unrecoverable; drop straight to login.
  useEffect(
    () =>
      onUnauthorized(() => {
        setUser(null)
        queryClient.clear()
      }),
    [queryClient],
  )

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await api.anonymousPost<TokenResponse>('/api/v1/auth/login', {
        email,
        password,
      })
      tokenStore.set(tokens)
      queryClient.clear()
      setLoading(true)
      await loadContext()
    },
    [loadContext, queryClient],
  )

  const register = useCallback(
    async (input: RegisterInput) => {
      await api.anonymousPost<User>('/api/v1/auth/register', input)
      await login(input.email, input.password)
    },
    [login],
  )

  const logout = useCallback(async () => {
    try {
      await api.post('/api/v1/auth/logout')
    } catch {
      /* logging out locally is what matters */
    }
    tokenStore.clear()
    setUser(null)
    queryClient.clear()
  }, [queryClient])

  const value = useMemo<AuthState>(
    () => ({ user, demoMode, loading, login, register, logout, refreshContext: loadContext }),
    [user, demoMode, loading, login, register, logout, loadContext],
  )

  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
