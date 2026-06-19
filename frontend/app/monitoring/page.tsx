"use client"

import { useEffect, useState } from "react"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { MetricCard, PulseDivider, StatusDot, type StatusType } from "@/components/vigil"
import { getDrift, getModels, getRegistry } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import { PLATFORM_ROLES } from "@/lib/role-gates"
import type { DriftPoint, ModelStatus, RegistryEntry } from "@/lib/types"
import { cn } from "@/lib/utils"

function healthStatus(h: ModelStatus["health"]): StatusType {
  if (h === "healthy") return "calm"
  if (h === "degraded") return "watch"
  return "risk"
}

// Registry status → on-brand badge tone. champion = calm (live), challenger/shadow = watch
// (candidate), retired = muted (history). Read-only projection of the M3 catalog status.
function registryStatusClass(status: string): string {
  if (status === "champion") return "bg-status-calm/10 text-status-calm"
  if (status === "challenger" || status === "shadow") return "bg-status-watch/10 text-status-watch"
  return "bg-muted text-muted-foreground"
}

function formatMetrics(metrics: Record<string, unknown>): string {
  const entries = Object.entries(metrics)
  if (entries.length === 0) return "—"
  return entries
    .map(([k, v]) => `${k}=${typeof v === "number" ? v : String(v)}`)
    .join(" · ")
}

export default function MonitoringPage() {
  const { me } = useAuth()
  const [models, setModels] = useState<ModelStatus[]>([])
  const [drift, setDrift] = useState<DriftPoint[]>([])
  const [registry, setRegistry] = useState<RegistryEntry[]>([])

  // Platform/auditor only (the API 403s others — this gate is UX). Non-platform sees a message.
  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)

  useEffect(() => {
    if (!isPlatformRole) return
    // GET /monitoring/models — champion/challenger/shadow projection (routing_state).
    getModels()
      .then((page) => setModels(page.items))
      .catch(() => setModels([]))
    // GET /monitoring/drift — real computed PSI/KS points (Gate M1); honest-empty by data state.
    getDrift()
      .then((page) => setDrift(page.items))
      .catch(() => setDrift([]))
    // GET /monitoring/registry — registered offline-validated versions (Gate M3); honest-empty.
    getRegistry()
      .then((page) => setRegistry(page.items))
      .catch(() => setRegistry([]))
  }, [isPlatformRole])

  const champion = models.find((m) => m.role === "champion")
  const breached = drift.filter((d) => d.breached).length

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

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Model Monitoring
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Champion / challenger health, regime, drift signals, and the model registry
          </p>
        </div>

        <div className="mb-6 grid grid-cols-4 gap-4">
          <MetricCard
            label="Champion"
            value={champion?.version ?? "—"}
            status={champion ? healthStatus(champion.health) : undefined}
          />
          <MetricCard label="Models Tracked" value={models.length || "—"} />
          <MetricCard
            label="Drift Breaches"
            value={breached}
            status={breached > 0 ? "risk" : "calm"}
          />
          <MetricCard
            label="Champion Health"
            value={champion?.health ?? "—"}
            status={champion ? healthStatus(champion.health) : undefined}
          />
        </div>

        <PulseDivider />

        {/* Champion / challenger table — GET /monitoring/models (ModelStatus) */}
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold text-foreground">Models</h2>
          <div className="overflow-hidden rounded-lg border-[0.5px] border-border">
            <Table>
              <TableHeader>
                <TableRow className="border-b-[0.5px] bg-background hover:bg-background">
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Model
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Version
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Role
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Regime
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Health
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Promoted
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {models.map((m) => (
                  <TableRow key={`${m.model_name}-${m.version}`} className="border-b-[0.5px]">
                    <TableCell className="font-mono text-sm text-foreground">
                      {m.model_name}
                    </TableCell>
                    <TableCell className="font-mono text-sm text-muted-foreground">
                      {m.version}
                    </TableCell>
                    <TableCell className="text-sm capitalize text-foreground">{m.role}</TableCell>
                    <TableCell className="font-mono text-sm text-muted-foreground">
                      {m.regime}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <StatusDot status={healthStatus(m.health)} size="sm" />
                        <span className="text-sm capitalize text-foreground">{m.health}</span>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-sm text-muted-foreground">
                      {m.promoted_at ? m.promoted_at.slice(0, 10) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>

        <PulseDivider />

        {/* Drift signals — GET /monitoring/drift (DriftPoint). Real computed PSI/KS (Gate M1);
            HONEST-EMPTY by data state until the detector evaluates a window. NOT a fabricated chart. */}
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold text-foreground">Drift Signals</h2>
          {drift.length === 0 ? (
            <div className="rounded-lg border-[0.5px] border-border bg-card p-6">
              <p className="text-sm text-muted-foreground">
                No drift points computed yet. Real PSI/KS drift is computed by the scheduled detector;
                until a window is evaluated this surface stays honestly empty rather than showing
                fabricated signals.
              </p>
            </div>
          ) : (
          <div className="overflow-hidden rounded-lg border-[0.5px] border-border">
            <Table>
              <TableHeader>
                <TableRow className="border-b-[0.5px] bg-background hover:bg-background">
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Model
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Metric
                  </TableHead>
                  <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Value
                  </TableHead>
                  <TableHead className="text-right text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Threshold
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Status
                  </TableHead>
                  <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Observed
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {drift.map((d, i) => (
                  <TableRow key={`${d.model_name}-${d.metric}-${d.distribution}-${i}`} className="border-b-[0.5px]">
                    <TableCell className="font-mono text-sm text-foreground">
                      <div className="flex flex-wrap items-center gap-2">
                        <span>{d.model_name}</span>
                        <span className="text-xs text-muted-foreground">{d.distribution}</span>
                        {d.synthetic && (
                          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900">
                            SYNTHETIC
                          </span>
                        )}
                        {d.constructed_demo && (
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
                            CONSTRUCTED DEMO
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-sm uppercase text-muted-foreground">
                      {d.metric}
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-right font-mono text-sm",
                        d.breached ? "text-status-risk" : "text-foreground",
                      )}
                    >
                      {d.value.toFixed(3)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-muted-foreground">
                      {d.threshold.toFixed(2)}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <StatusDot status={d.breached ? "risk" : "calm"} size="sm" />
                          <span className="text-sm text-foreground">
                            {d.breached ? "Breached" : "OK"}
                          </span>
                        </div>
                        {d.breached && (
                          <span className="font-mono text-[10px] font-semibold text-status-risk">
                            ML engineer alerted
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-sm text-muted-foreground">
                      {d.ts.slice(0, 10)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          )}
          {drift.some((d) => d.breached) && (
            <p className="mt-3 text-xs text-muted-foreground">
              A breach enqueues a PII-free alert to the ML engineer (email + structured log) and
              marks the drift point notified. Per-point alert-delivery state isn&apos;t exposed by the
              API yet; the badge reflects breach state.
            </p>
          )}
        </div>

        <PulseDivider />

        {/* Model registry (Gate M3) — GET /monitoring/registry. READ-ONLY governance surface:
            registered offline-validated versions + their supplied metrics/provenance. Registration
            enters challenger/shadow only; promotion to champion is a separate audited action (not
            available from this read-only view). Honest-empty until a version is registered. */}
        <div className="mb-8">
          <h2 className="mb-1 text-lg font-semibold text-foreground">Model Registry</h2>
          <p className="mb-4 text-sm text-muted-foreground">
            Offline-validated versions registered as challenger/shadow, with their supplied
            validation metrics and provenance — the informed-decision surface before a governed
            promotion.
          </p>
          {registry.length === 0 ? (
            <div className="rounded-lg border-[0.5px] border-border bg-card p-6">
              <p className="text-sm text-muted-foreground">
                No model versions registered yet. This surface stays honestly empty until a validated
                version is registered (the registry starts empty by design).
              </p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-lg border-[0.5px] border-border">
              <Table>
                <TableHeader>
                  <TableRow className="border-b-[0.5px] bg-background hover:bg-background">
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Version
                    </TableHead>
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Regime
                    </TableHead>
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Status
                    </TableHead>
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Provenance
                    </TableHead>
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Validation Metrics
                    </TableHead>
                    <TableHead className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Registered
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {registry.map((e) => (
                    <TableRow
                      key={`${e.regime}-${e.model_version}`}
                      className="border-b-[0.5px]"
                    >
                      <TableCell className="font-mono text-sm text-foreground">
                        {e.model_version}
                      </TableCell>
                      <TableCell className="font-mono text-sm text-muted-foreground">
                        {e.regime}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={cn(
                            "font-mono text-[10px] uppercase",
                            registryStatusClass(e.status),
                          )}
                        >
                          {e.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-[180px] truncate font-mono text-xs text-muted-foreground">
                        {e.provenance}
                      </TableCell>
                      <TableCell className="max-w-xs truncate font-mono text-xs text-muted-foreground">
                        {formatMetrics(e.metrics)}
                      </TableCell>
                      <TableCell className="font-mono text-sm text-muted-foreground">
                        {e.registered_at.slice(0, 10)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
          <p className="mt-3 text-xs text-muted-foreground">
            Read-only. Promotion to champion (and full promotion history) is a separate audited
            action, not exposed on this view.
          </p>
        </div>
      </main>
    </div>
  )
}
