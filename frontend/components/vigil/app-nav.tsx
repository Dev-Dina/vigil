"use client"

import Image from "next/image"
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
  { href: "/at-risk", label: "At-Risk" },
  { href: "/cohort-health", label: "Cohort Health" },
  { href: "/monitoring", label: "Monitoring", platformOnly: true },
  { href: "/costs", label: "Costs", platformOnly: true },
  { href: "/observability", label: "Observability", platformOnly: true },
]

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/"
  return pathname.startsWith(href)
}

// Readable avatar initials derived from the user's ROLE (MeOut carries no email): two-word roles
// → first letters (platform_admin → PA, mlops_engineer → ME); single-word → first two chars
// (coordinator → CO, auditor → AU). Presentation only — no new data is read.
function roleInitials(role?: string): string {
  if (!role) return "—"
  const words = role.split("_").filter(Boolean)
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase()
  return role.slice(0, 2).toUpperCase()
}

function humanizeRole(role?: string): string {
  if (!role) return ""
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
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
        <Link href="/" className="flex items-center gap-2.5">
          <Image src="/vigil-mark.png" alt="Vigil" width={28} height={28} priority />
          <span className="text-lg font-semibold tracking-tight text-foreground">Vigil</span>
        </Link>

        <nav className="flex items-center gap-1">
          {navItems.map((item) => {
            const active = isActive(pathname, item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  active
                    ? "text-foreground"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                {item.label}
                {active && (
                  <span className="absolute inset-x-3 -bottom-[11px] h-0.5 rounded-full bg-gradient-to-r from-status-calm to-status-calm/40" />
                )}
              </Link>
            )
          })}
        </nav>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={handleLogout}
          className="rounded-md px-2 py-1 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
        >
          Sign out
        </button>
        <div className="flex items-center gap-2.5 rounded-full border-[0.5px] border-border bg-card py-1 pl-1 pr-3">
          <Avatar className="h-8 w-8">
            <AvatarFallback className="bg-primary text-xs font-semibold text-primary-foreground">
              {roleInitials(me?.role)}
            </AvatarFallback>
          </Avatar>
          <span className="hidden text-xs font-medium leading-tight text-foreground sm:block">
            {humanizeRole(me?.role)}
          </span>
        </div>
      </div>
    </header>
  )
}
