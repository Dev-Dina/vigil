"use client"

import { Fragment, useCallback, useEffect, useState } from "react"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { MetricCard, PulseDivider } from "@/components/vigil"
import { getMessages, type MessageQuery } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES } from "@/lib/role-gates"
import type { MessageEventOut } from "@/lib/types"

// The admin/observability page (specs/observability.md § Admin observability). PLATFORM/AUDITOR
// ONLY. Four duties over GET /monitoring/messages: inspect messages, verify guardrails fired,
// debug retrieval, show redaction. Shows ONLY redacted fields + coded ids — there is no raw
// content (none exists) and no identifiable participant data.

const SELECT_CLASS =
  "h-9 rounded-md border-[0.5px] border-border bg-card px-2 text-sm text-foreground"

export default function ObservabilityPage() {
  const { me } = useAuth()
  const [rows, setRows] = useState<MessageEventOut[]>([])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filters, setFilters] = useState<MessageQuery>({ limit: 100 })

  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)

  const load = useCallback(() => {
    getMessages(filters)
      .then((page) => setRows(page.items))
      .catch(() => setRows([]))
  }, [filters])

  useEffect(() => {
    if (!isPlatformRole) return
    load()
  }, [isPlatformRole, load])

  if (!isPlatformRole) {
    return (
      <div className="min-h-screen bg-background">
        <main className="mx-auto max-w-7xl px-6 py-16">
          <p className="text-muted-foreground">
            This view is available only to platform and auditor roles.
          </p>
        </main>
      </div>
    )
  }

  const blocked = rows.filter((r) => r.guardrail_decision === "blocked").length

  const setFilter = (key: keyof MessageQuery, value: string) =>
    setFilters((f) => ({ ...f, [key]: value || undefined }))

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Observability
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Redacted message events — inspect turns, verify guardrails fired, debug retrieval, and
            confirm redaction. Coded ids only; no raw or identifiable content exists.
          </p>
        </div>

        <div className="mb-6 grid grid-cols-3 gap-4">
          <MetricCard label="Events Shown" value={rows.length || "—"} />
          <MetricCard
            label="Guardrail Blocks"
            value={blocked}
            status={blocked > 0 ? "watch" : "calm"}
          />
          <MetricCard
            label="Surfaces"
            value={new Set(rows.map((r) => r.surface)).size || "—"}
          />
        </div>

        {/* Filters — surface / guardrail_decision / status / role / conversation_id */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <select
            className={SELECT_CLASS}
            value={filters.surface ?? ""}
            onChange={(e) => setFilter("surface", e.target.value)}
            aria-label="Filter by surface"
          >
            <option value="">All surfaces</option>
            <option value="local_assistant">local_assistant</option>
            <option value="public_guide">public_guide</option>
          </select>
          <select
            className={SELECT_CLASS}
            value={filters.guardrail_decision ?? ""}
            onChange={(e) => setFilter("guardrail_decision", e.target.value)}
            aria-label="Filter by guardrail decision"
          >
            <option value="">All decisions</option>
            <option value="allowed">allowed</option>
            <option value="blocked">blocked</option>
          </select>
          <select
            className={SELECT_CLASS}
            value={filters.status ?? ""}
            onChange={(e) => setFilter("status", e.target.value)}
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            <option value="ok">ok</option>
            <option value="refused">refused</option>
            <option value="error">error</option>
          </select>
          <input
            className={`${SELECT_CLASS} w-48`}
            placeholder="conversation_id"
            value={filters.conversation_id ?? ""}
            onChange={(e) => setFilter("conversation_id", e.target.value)}
            aria-label="Filter by conversation id"
          />
          <input
            className={`${SELECT_CLASS} w-40`}
            placeholder="role"
            value={filters.role_or_guest_scope ?? ""}
            onChange={(e) => setFilter("role_or_guest_scope", e.target.value)}
            aria-label="Filter by role"
          />
        </div>

        <PulseDivider />

        <div className="mt-6 overflow-hidden rounded-lg border-[0.5px] border-border">
          <Table>
            <TableHeader>
              <TableRow className="border-b-[0.5px] bg-background hover:bg-background">
                <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Time
                </TableHead>
                <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Surface
                </TableHead>
                <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Role
                </TableHead>
                <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Route / Agent
                </TableHead>
                <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Guardrail
                </TableHead>
                <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Status
                </TableHead>
                <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Redacted message
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-8 text-center text-sm text-muted-foreground">
                    No message events match.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((r) => {
                  const isOpen = expanded.has(r.id)
                  return (
                    <Fragment key={r.id}>
                      <TableRow
                        className="cursor-pointer border-b-[0.5px]"
                        onClick={() => toggle(r.id)}
                      >
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {r.ts.slice(0, 19).replace("T", " ")}
                        </TableCell>
                        <TableCell className="font-mono text-sm text-foreground">
                          {r.surface}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {r.role_or_guest_scope}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {r.route_or_agent || "—"}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              r.guardrail_decision === "blocked" ? "destructive" : "outline"
                            }
                          >
                            {r.guardrail_decision}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground">
                          {r.status}
                        </TableCell>
                        <TableCell className="max-w-md truncate text-sm text-foreground">
                          {r.redacted_user_msg || "—"}
                        </TableCell>
                      </TableRow>
                      {isOpen && (
                        <TableRow className="border-b-[0.5px] bg-muted/30">
                          <TableCell colSpan={7} className="space-y-3 py-4">
                            {/* Show redaction: ONLY redacted text is stored/shown — no raw exists. */}
                            <div>
                              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                Redacted user message
                              </p>
                              <p className="mt-1 font-mono text-sm text-foreground">
                                {r.redacted_user_msg || "—"}
                              </p>
                            </div>
                            <div>
                              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                Redacted assistant message
                              </p>
                              <p className="mt-1 font-mono text-sm text-foreground">
                                {r.redacted_assistant_msg || "—"}
                              </p>
                            </div>
                            <div className="flex gap-6 font-mono text-xs text-muted-foreground">
                              <span>model: {r.llm_provider_model || "—"}</span>
                              <span>latency: {r.latency_ms}ms</span>
                              <span>cost: ${r.token_cost_estimate.toFixed(4)}</span>
                              <span>request_id: {r.request_id}</span>
                            </div>
                            {/* Debug retrieval: the citation refs that grounded the turn. */}
                            <div>
                              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                Retrieved chunks ({r.retrieved_chunks.length})
                              </p>
                              {r.retrieved_chunks.length === 0 ? (
                                <p className="mt-1 text-xs text-muted-foreground">
                                  No retrieval for this turn.
                                </p>
                              ) : (
                                <ul className="mt-1 space-y-1">
                                  {r.retrieved_chunks.map((c, i) => (
                                    <li key={i} className="font-mono text-xs text-foreground">
                                      {[c.source_type, c.source_id, c.locator]
                                        .filter(Boolean)
                                        .join(" · ") || JSON.stringify(c)}
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
      </main>
    </div>
  )
}
