"use client"

import * as React from "react"
import Image from "next/image"
import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth-context"
import type { LoginIn } from "@/lib/types"
import { cn } from "@/lib/utils"

// Side login — the petrol rail that leads into the app. Wired to the EXISTING auth flow
// (useAuth().login → POST /auth/login → /auth/me). No auth logic changes here; on success it
// routes into the dashboard. Honest error state on bad credentials — never a silent pass.

export function SideLogin({ className }: { className?: string }) {
  const router = useRouter()
  const { login } = useAuth()
  const [email, setEmail] = React.useState("")
  const [password, setPassword] = React.useState("")
  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    const creds: LoginIn = { email, password }
    const ok = await login(creds)
    setSubmitting(false)
    if (ok) {
      router.push("/")
    } else {
      setError("Invalid email or password.")
    }
  }

  return (
    <div
      className={cn(
        "flex h-full flex-col justify-center bg-primary px-8 py-12 text-primary-foreground sm:px-12",
        className,
      )}
    >
      <div className="mx-auto w-full max-w-sm">
        {/* Brand chip */}
        <div className="mb-10 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-white">
            <Image src="/vigil-mark.png" alt="" width={26} height={26} />
          </span>
          <span className="text-lg font-semibold tracking-tight">VIGIL</span>
        </div>

        <h2 className="text-2xl font-semibold tracking-tight">Sign in</h2>
        <p className="mt-1.5 text-sm text-primary-foreground/70">
          Enter the operational dashboard — triage, risk explanations, and monitoring.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="side-email" className="text-sm font-medium text-primary-foreground/90">
              Email
            </label>
            <input
              id="side-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@sponsor.org"
              className="w-full rounded-lg border border-white/15 bg-white/5 px-3 py-2.5 text-sm text-white placeholder:text-white/40 transition-colors focus:border-transparent focus:outline-none focus:ring-2 focus:ring-status-calm/70"
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="side-password"
              className="text-sm font-medium text-primary-foreground/90"
            >
              Password
            </label>
            <input
              id="side-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full rounded-lg border border-white/15 bg-white/5 px-3 py-2.5 text-sm text-white placeholder:text-white/40 transition-colors focus:border-transparent focus:outline-none focus:ring-2 focus:ring-status-calm/70"
            />
          </div>

          {error && (
            <div
              role="alert"
              data-testid="side-login-error"
              className="rounded-md bg-status-risk/15 px-3 py-2 text-sm text-primary-foreground"
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className={cn(
              "w-full rounded-lg bg-white px-4 py-2.5 text-sm font-medium text-primary transition-colors",
              "hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-60",
              "focus:outline-none focus:ring-2 focus:ring-status-calm focus:ring-offset-2 focus:ring-offset-primary",
            )}
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="mt-6 text-xs leading-relaxed text-primary-foreground/55">
          Local technical app. Credentials are verified against the scoped backend. The public Guide
          on this page shares no login, data, or endpoints with the dashboard.
        </p>
      </div>
    </div>
  )
}
