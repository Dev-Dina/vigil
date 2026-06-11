"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/lib/auth-context"
import type { LoginIn } from "@/lib/types"

// Real login: POST /auth/login -> TokenOut, then GET /auth/me -> MeOut resolves
// scope into the auth context (see lib/auth-context.tsx). Bad credentials surface an
// honest error state — never a silent pass.

export default function LoginPage() {
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
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-foreground">
            <svg
              width="20"
              height="20"
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
          <h1 className="text-xl font-semibold tracking-tight text-foreground">
            Sign in to Vigil
          </h1>
          <p className="text-center text-sm text-muted-foreground">
            Clinical-trial retention intelligence
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="email" className="text-sm font-medium text-foreground">
              Email
            </label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@sponsor.org"
              className="border-border bg-background"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="text-sm font-medium text-foreground">
              Password
            </label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="border-border bg-background"
            />
          </div>

          {error && (
            <p
              role="alert"
              className="text-center text-sm text-destructive"
              data-testid="login-error"
            >
              {error}
            </p>
          )}

          <Button type="submit" disabled={submitting} className="w-full">
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        <p className="mt-6 text-center text-[11px] text-muted-foreground">
          Local technical app. Credentials are verified against the scoped backend.
        </p>
      </div>
    </div>
  )
}
