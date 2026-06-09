"use client"

// Auth context wired to real backend (Phase 4).
// POST /auth/login → store access_token in memory + localStorage for persistence.
// GET /auth/me on mount (if token exists) → hydrate `me`.
// POST /auth/logout → clear token.

import * as React from "react"
import type { LoginIn, MeOut } from "./types"

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : "http://localhost:8000"

const TOKEN_KEY = "vigil_access_token"

interface AuthContextValue {
  me: MeOut | null
  token: string | null
  login: (creds: LoginIn) => Promise<boolean>
  logout: () => void
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = React.useState<string | null>(() => {
    if (typeof window === "undefined") return null
    return localStorage.getItem(TOKEN_KEY)
  })
  const [me, setMe] = React.useState<MeOut | null>(null)

  // Hydrate me on mount if a stored token exists.
  React.useEffect(() => {
    if (!token) return
    fetch(`${API_BASE}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => {
        if (!r.ok) {
          // Token stale or revoked — clear it.
          setToken(null)
          localStorage.removeItem(TOKEN_KEY)
          return null
        }
        return r.json() as Promise<MeOut>
      })
      .then((data) => {
        if (data) setMe(data)
      })
      .catch(() => {
        // Network failure — keep token, me stays null; UI shows loading state.
      })
    // Only run on mount (token from localStorage).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const login = React.useCallback(async (creds: LoginIn): Promise<boolean> => {
    const r = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: creds.email, password: creds.password }),
    })
    if (!r.ok) return false
    const tokenOut = (await r.json()) as { access_token: string }
    const accessToken = tokenOut.access_token
    localStorage.setItem(TOKEN_KEY, accessToken)
    setToken(accessToken)

    const meRes = await fetch(`${API_BASE}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    if (meRes.ok) {
      setMe((await meRes.json()) as MeOut)
    }
    return true
  }, [])

  const logout = React.useCallback(() => {
    if (token) {
      // Fire-and-forget — revoke jti in Redis.
      fetch(`${API_BASE}/api/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {})
    }
    setToken(null)
    setMe(null)
    localStorage.removeItem(TOKEN_KEY)
  }, [token])

  const value = React.useMemo(
    () => ({ me, token, login, logout }),
    [me, token, login, logout],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>")
  return ctx
}
