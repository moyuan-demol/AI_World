import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { api, TOKEN_KEY, USER_KEY } from '../api/client.js'

const AuthContext = createContext(null)

function readStoredUser() {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch (error) {
    return null
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [user, setUser] = useState(readStoredUser)
  const [loading, setLoading] = useState(() => Boolean(localStorage.getItem(TOKEN_KEY)))

  const persist = useCallback((data) => {
    localStorage.setItem(TOKEN_KEY, data.access_token)
    localStorage.setItem(USER_KEY, JSON.stringify(data.user))
    setToken(data.access_token)
    setUser(data.user)
  }, [])

  const login = useCallback(
    async (username, password) => {
      const response = await api.post('/auth/login', { username, password })
      persist(response.data)
      return response.data
    },
    [persist],
  )

  const register = useCallback(
    async (username, password, email) => {
      const response = await api.post('/auth/register', { username, password, email: email || null })
      persist(response.data)
      return response.data
    },
    [persist],
  )

  const demoLogin = useCallback(async () => {
    const response = await api.post('/auth/demo-login')
    persist(response.data)
    return response.data
  }, [persist])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    setToken(null)
    setUser(null)
  }, [])

  useEffect(() => {
    if (!token) {
      setLoading(false)
      return undefined
    }
    let cancelled = false
    api
      .get('/auth/me')
      .then((response) => {
        if (!cancelled) {
          setUser(response.data)
          localStorage.setItem(USER_KEY, JSON.stringify(response.data))
        }
      })
      .catch(() => {
        /* interceptor handles 401 */
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token])

  const value = useMemo(
    () => ({ token, user, loading, login, register, demoLogin, logout }),
    [token, user, loading, login, register, demoLogin, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth 必须在 AuthProvider 内部使用')
  }
  return context
}
