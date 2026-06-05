"use client"

import { usePathname } from "next/navigation"
import { AppNav } from "./app-nav"
import { AssistantPanel } from "./assistant-panel"

// Routes that render WITHOUT the authenticated chrome (no nav, no assistant).
// The isolation invariant: the assistant must never mount on /login.
const BARE_ROUTES = ["/login"]

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
