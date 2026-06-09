"use client"

// Demo loop (dashboard.md § Demo loop).
// Visible ONLY when NEXT_PUBLIC_DEMO_MODE === "true".
// Inject synthetic events → trigger scoring → poll until done → refresh cohort → alert.

import { useState } from "react"
import type { CohortRow, CohortSummary } from "@/lib/types"
import { injectEvents, getScoringJob, getCohort, getCohortSummary, ApiError } from "@/lib/api"

interface DemoLoopProps {
  trialId: string
  onRefresh: (rows: CohortRow[], summary: CohortSummary) => void
}

interface DemoAlert {
  message: string
  newHighCount: number
}

export function DemoLoop({ trialId, onRefresh }: DemoLoopProps) {
  const isDemoMode = process.env.NEXT_PUBLIC_DEMO_MODE === "true"
  const demoKey = process.env.NEXT_PUBLIC_DEMO_SECRET ?? ""

  const [status, setStatus] = useState<"idle" | "injecting" | "polling" | "done" | "failed">(
    "idle",
  )
  const [alert, setAlert] = useState<DemoAlert | null>(null)
  const [alertDismissed, setAlertDismissed] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  if (!isDemoMode) return null

  const handleInject = async () => {
    setStatus("injecting")
    setAlert(null)
    setAlertDismissed(false)
    setErrorMessage(null)

    // Snapshot pre-inject high-risk count for escalation comparison.
    let preHighIds: Set<string> = new Set()
    try {
      const pre = await getCohort({ trial_id: trialId })
      preHighIds = new Set(
        pre.items.filter((r) => r.risk_band === "high").map((r) => r.participant_id),
      )
    } catch {
      // Non-fatal; proceed without baseline.
    }

    try {
      await injectEvents({ trial_id: trialId, events: [], demo_mode_key: demoKey })
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : String(e)
      setErrorMessage(`Inject failed: ${msg}`)
      setStatus("failed")
      return
    }

    // Poll scoring/jobs — inject_events triggers scoring internally; we poll the job.
    // The scoring router returns a job_id from trigger_scoring — but inject_events returns 204.
    // Poll GET /cohort every 2 s for up to 30 s to detect rescore completion.
    setStatus("polling")
    let attempts = 0
    const maxAttempts = 15
    const poll = async () => {
      if (attempts >= maxAttempts) {
        setStatus("failed")
        setErrorMessage("Scoring did not complete within the polling window.")
        return
      }
      attempts++
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const [newPage, newSummary] = await Promise.all([
          getCohort({ trial_id: trialId }),
          getCohortSummary({ trial_id: trialId }),
        ])
        const postHighIds = new Set(
          newPage.items.filter((r) => r.risk_band === "high").map((r) => r.participant_id),
        )
        const newlyHigh = [...postHighIds].filter((id) => !preHighIds.has(id))

        onRefresh(newPage.items, newSummary)
        setStatus("done")

        if (newlyHigh.length > 0) {
          setAlert({
            newHighCount: newlyHigh.length,
            message: `${newlyHigh.length} participant(s) newly flagged HIGH risk in trial ${trialId}. Data: synthetic — scores are method demonstrations only.`,
          })
        }
      } catch {
        // Retry.
        setTimeout(poll, 0)
      }
    }
    setTimeout(poll, 0)
  }

  return (
    <div className="rounded-md border border-amber-400/40 bg-amber-50/10 px-4 py-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-amber-600">
        Demo Mode
      </p>

      {alert && !alertDismissed && (
        <div className="mb-3 flex items-start justify-between gap-3 rounded-md border border-amber-500 bg-amber-100/20 px-3 py-2">
          <p className="text-sm text-amber-800">{alert.message}</p>
          <button
            onClick={() => setAlertDismissed(true)}
            className="shrink-0 text-xs text-amber-600 hover:text-amber-900"
            aria-label="Dismiss alert"
          >
            Dismiss
          </button>
        </div>
      )}

      {errorMessage && (
        <p className="mb-2 text-xs text-red-600">{errorMessage}</p>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={handleInject}
          disabled={status === "injecting" || status === "polling"}
          className="rounded-md bg-amber-600 px-3 py-1.5 text-sm text-white transition-colors hover:bg-amber-700 disabled:opacity-50"
        >
          {status === "injecting"
            ? "Injecting…"
            : status === "polling"
              ? "Scoring in progress…"
              : "Inject Synthetic Events"}
        </button>
        {status === "done" && (
          <span className="text-xs text-muted-foreground">Rescore complete.</span>
        )}
      </div>
    </div>
  )
}
