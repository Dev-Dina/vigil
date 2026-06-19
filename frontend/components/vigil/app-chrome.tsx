"use client"

import { usePathname } from "next/navigation"
import { AppNav } from "./app-nav"
import { AssistantPanel } from "./assistant-panel"

// Routes that render WITHOUT the authenticated chrome (no nav, no assistant).
// The isolation invariant: the scope-bound assistant must never mount on /login or on the
// public landing page (/welcome) — that page carries ONLY the isolated public Guide chat.
const BARE_ROUTES = ["/login", "/welcome"]

export function AppChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const bare = BARE_ROUTES.some((r) => pathname === r || pathname.startsWith(r + "/"))

  if (bare) return <>{children}</>

  return (
    <>
      <AppNav />
      {children}
      <AssistantPanel />
    </>
  )
}
