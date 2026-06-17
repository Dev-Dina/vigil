"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { cn } from "@/lib/utils"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES } from "@/lib/role-gates"

// `platformOnly` links (monitoring/cost/observability) are platform/auditor-only — the API 403s
// others; hiding them from the nav is the UX layer of that gate (defence-in-depth, not the guard).
const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/triage", label: "Triage" },
  { href: "/monitoring", label: "Monitoring", platformOnly: true },
  { href: "/costs", label: "Costs", platformOnly: true },
  { href: "/observability", label: "Observability", platformOnly: true },
]

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/"
  return pathname.startsWith(href)
}

export function AppNav() {
  const pathname = usePathname()
  const router = useRouter()
  const { me, logout } = useAuth()

  const handleLogout = () => {
    logout()
    router.push("/login")
  }

  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)
  const navItems = NAV.filter((item) => !item.platformOnly || isPlatformRole)

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b-[0.5px] border-border bg-card px-6">
      <div className="flex items-center gap-8">
        <Link href="/" className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-foreground">
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="text-card"
            >
              <path
                d="M8 1L2 4V8C2 11.3137 4.68629 14 8 14C11.3137 14 14 11.3137 14 8V4L8 1Z"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                fill="none"
              />
              <path
                d="M8 5V8L10 10"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <span className="text-lg font-semibold tracking-tight text-foreground">Vigil</span>
        </Link>

        <nav className="flex items-center gap-6">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "text-sm font-medium transition-colors",
                isActive(pathname, item.href)
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>

      <div className="flex items-center gap-4">
        {me && (
          <span className="font-mono text-xs text-muted-foreground">{me.role}</span>
        )}
        <button
          onClick={handleLogout}
          className="text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          Sign out
        </button>
        <Avatar className="h-8 w-8 border-[0.5px] border-border">
          <AvatarFallback className="bg-muted text-xs text-muted-foreground">
            {me?.user_id.slice(-2).toUpperCase() ?? "??"}
          </AvatarFallback>
        </Avatar>
      </div>
    </header>
  )
}
