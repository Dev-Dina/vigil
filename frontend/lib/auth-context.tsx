"use client"

// FAKE auth context — Phase 3. NO real auth, NO token storage, NO crypto.
// Just enough state to navigate the stubbed app and gate chrome.
// TODO(phase4): wire to scoped auth client (POST /auth/login -> TokenOut,
// GET /auth/me -> MeOut, POST /auth/logout, POST /auth/refresh).

import * as React from "react"
import { STUB_ME } from "./stubs"
import type { LoginIn, MeOut } from "./types"

interface AuthContextValue {
  me: MeOut | null
  // Stubbed: ignores credentials, sets the fake session, resolves true.
  login: (creds: LoginIn) => Promise<boolean>
  logout: () => void
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Default to "logged in" so the stubbed app is navigable without real auth.
  const [me, setMe] = React.useState<MeOut | null>(STUB_ME)

  const login = React.useCallback(async (_creds: LoginIn): Promise<boolean> => {
    // TODO(phase4): POST /auth/login -> store token; then GET /auth/me -> setMe
    setMe(STUB_ME)
    return true
  }, [])

  const logout = React.useCallback(() => {
    // TODO(phase4): POST /auth/logout (revoke jti in Redis), clear token
    setMe(null)
  }, [])

  const value = React.useMemo(() => ({ me, login, logout }), [me, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>")
  return ctx
}
