"use client"

import { useEffect, useMemo, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import {
  FluidTabs,
  GuardrailMix,
  MetricCard,
  PulseDivider,
  StatusDot,
  type StatusType,
} from "@/components/vigil"
import { getCost, getDrift, getMessages, getModels, getRegistry } from "@/lib/api"
import { getGuideCost, type GuideCost } from "@/lib/guide"
import { useAuth } from "@/lib/auth-context"
import { CAN_VIEW_LLMOPS, CAN_VIEW_MLOPS, PLATFORM_ROLES } from "@/lib/role-gates"
import type {
  CostReport,
  DriftPoint,
  MessageEventOut,
  ModelStatus,
  RegistryEntry,
} from "@/lib/types"
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

function usd(v: number): string {
  return `$${v.toFixed(4)}`
}

// =============================================================================
// Platform monitoring — two operator surfaces (Gate UI-OPS):
//   • MLOps  — the model lifecycle: drift (PSI/KS + breach alert), model registry, traffic.
//   • LLMOps — the LLM operations: app assistant cost + the ISOLATED Guide's own cost (two
//              separate sources), tokens, latency, and the guardrail/refusal mix.
// Both platform/auditor-gated (the API 403s others; this is the UX layer). Existing endpoints +
// components only; honest empty/zero/unreachable states everywhere.
// =============================================================================

export default function MonitoringPage() {
  const { me } = useAuth()
  const isPlatformRole = me?.role != null && (PLATFORM_ROLES as string[]).includes(me.role)
  // Per-view capability gates (Gate RBAC-OPS): an MLOps engineer sees only MLOps; an LLMOps
  // engineer only LLMOps; platform_admin + auditor see both. Server-side is the enforced gate.
  const canViewMlops = me?.role != null && (CAN_VIEW_MLOPS as string[]).includes(me.role)
  const canViewLlmops = me?.role != null && (CAN_VIEW_LLMOPS as string[]).includes(me.role)

  if (!isPlatformRole) {
    return (
      <div className="min-h-screen bg-background">
        <main className="mx-auto max-w-7xl px-6 py-16">
          <p className="text-muted-foreground">
            This view is available only to platform roles (platform admin, auditor, MLOps/LLMOps
            engineers).
          </p>
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Platform Monitoring
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Two operator surfaces — MLOps (model lifecycle) and LLMOps (LLM operations).
          </p>
        </div>

        <FluidTabs
          tabs={[
            {
              id: "mlops",
              label: "MLOps",
              content: canViewMlops ? (
                <MLOpsView />
              ) : (
                <ViewRestricted surface="MLOps" roles="platform admin, auditor, or an MLOps engineer" />
              ),
            },
            {
              id: "llmops",
              label: "LLMOps",
              content: canViewLlmops ? (
                <LLMOpsView />
              ) : (
                <ViewRestricted surface="LLMOps" roles="platform admin, auditor, or an LLMOps engineer" />
              ),
            },
          ]}
        />
      </main>
    </div>
  )
}

// Honest "not available to your role" treatment (never an error) for a platform-tier role that
// holds the OTHER operational view's capability but not this one (Gate RBAC-OPS).
function ViewRestricted({ surface, roles }: { surface: string; roles: string }) {
  return (
    <div className="rounded-lg border-[0.5px] border-border bg-card p-6">
      <p className="text-sm text-muted-foreground">
        The {surface} view isn&apos;t available to your role. It&apos;s shown to {roles}. The
        backend enforces this boundary regardless of what the UI displays.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MLOps — model lifecycle: drift (M1) + registry (M3) + champion/challenger/shadow traffic.
// Read-only. (trigger-drift-run / register / promote are existing platform actions, surfaced as a
// later guarded-action step — no buttons here.)
// ---------------------------------------------------------------------------

function MLOpsView() {
  const [models, setModels] = useState<ModelStatus[]>([])
  const [drift, setDrift] = useState<DriftPoint[]>([])
  const [registry, setRegistry] = useState<RegistryEntry[]>([])

  useEffect(() => {
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
  }, [])

  const champion = models.find((m) => m.role === "champion")
  const breached = drift.filter((d) => d.breached).length

  return (
    <>
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
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
        <h2 className="mb-4 text-lg font-semibold text-foreground">Model Traffic</h2>
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

      {/* Model registry (Gate M3) — GET /monitoring/registry. READ-ONLY governance surface. */}
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
                  <TableRow key={`${e.regime}-${e.model_version}`} className="border-b-[0.5px]">
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
    </>
  )
}

// ---------------------------------------------------------------------------
// LLMOps — LLM operations across BOTH surfaces: app assistant cost (GET /monitoring/cost) + the
// ISOLATED Guide's OWN cost (GET {GUIDE_URL}/metrics/cost, browser-direct, no JWT) shown as two
// clearly-separate sources, plus the guardrail/refusal mix (app message_events).
// ---------------------------------------------------------------------------

type CostState = "loading" | "ready" | "unreachable"

interface NormalizedRollup {
  label: string
  turns: number
  total_tokens: number
  total_cost: number
  avg_latency_ms: number
}

interface NormalizedCost {
  turns: number
  totalCost: number
  totalTokens: number
  promptTokens: number
  completionTokens: number
  avgLatencyMs: number
  rollups: NormalizedRollup[]
}

function LLMOpsView() {
  const [appCost, setAppCost] = useState<CostReport | null>(null)
  const [appState, setAppState] = useState<CostState>("loading")
  const [guideCost, setGuideCost] = useState<GuideCost | null>(null)
  const [guideState, setGuideState] = useState<CostState>("loading")
  const [messages, setMessages] = useState<MessageEventOut[]>([])

  useEffect(() => {
    // App assistant cost — through the app API (JWT-scoped, platform/auditor only).
    getCost()
      .then((r) => {
        setAppCost(r)
        setAppState("ready")
      })
      .catch(() => {
        setAppCost(null)
        setAppState("unreachable")
      })
    // Guide cost — browser-DIRECT to the isolated Guide (no JWT). Any failure (GuideError /
    // transport) → honest unreachable, never fabricated spend (mirrors the chat's handling).
    getGuideCost()
      .then((g) => {
        setGuideCost(g)
        setGuideState("ready")
      })
      .catch(() => {
        setGuideCost(null)
        setGuideState("unreachable")
      })
    // Guardrail/refusal mix is derived from the app's message_events (app surface only).
    getMessages({ limit: 200 })
      .then((p) => setMessages(p.items))
      .catch(() => setMessages([]))
  }, [])

  const appNormalized = useMemo<NormalizedCost | null>(() => {
    if (!appCost) return null
    const rollups = appCost.rollups ?? []
    const totalLatency = rollups.reduce((s, r) => s + r.total_latency_ms, 0)
    return {
      turns: appCost.total_turns,
      totalCost: appCost.total_cost,
      totalTokens: appCost.total_tokens,
      promptTokens: appCost.total_prompt_tokens,
      completionTokens: appCost.total_completion_tokens,
      avgLatencyMs: appCost.total_turns > 0 ? totalLatency / appCost.total_turns : 0,
      rollups: rollups.map((r) => ({
        label: `${r.surface} · ${r.llm_provider_model || "—"}`,
        turns: r.turns,
        total_tokens: r.total_tokens,
        total_cost: r.total_cost,
        avg_latency_ms: r.avg_latency_ms,
      })),
    }
  }, [appCost])

  const guideNormalized = useMemo<NormalizedCost | null>(() => {
    if (!guideCost) return null
    return {
      turns: guideCost.total_turns,
      totalCost: guideCost.total_cost,
      totalTokens: guideCost.total_tokens,
      promptTokens: guideCost.total_prompt_tokens,
      completionTokens: guideCost.total_completion_tokens,
      avgLatencyMs: guideCost.avg_latency_ms,
      rollups: (guideCost.rollups ?? []).map((r) => ({
        label: r.llm_provider_model || "—",
        turns: r.turns,
        total_tokens: r.total_tokens,
        total_cost: r.total_cost,
        avg_latency_ms: r.avg_latency_ms,
      })),
    }
  }, [guideCost])

  return (
    <>
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-foreground">LLM Cost — Two Separate Sources</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          The in-app assistant and the public Guide are <span className="font-medium">isolated</span>{" "}
          services with their own LLM spend and their own stores. Their costs are shown side by side,
          never merged — the Guide&apos;s figures are read browser-direct from its own endpoint.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <CostSourceCard
          title="App Assistant"
          subtitle="In-app, JWT-scoped — GET /monitoring/cost (message_events)."
          badge="app api"
          badgeClass="bg-status-calm/10 text-status-calm"
          state={appState}
          data={appNormalized}
        />
        <CostSourceCard
          title="Public Guide"
          subtitle="Isolated public service — GET /metrics/cost on the Guide&apos;s own sink (no JWT)."
          badge="isolated · :8080"
          badgeClass="bg-status-watch/10 text-status-watch"
          state={guideState}
          data={guideNormalized}
        />
      </div>

      <PulseDivider />

      <div className="mb-8">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Guardrail &amp; Refusal Mix</h2>
        <GuardrailMix events={messages} />
        <p className="mt-3 text-xs text-muted-foreground">
          Derived from the app&apos;s redacted message_events (app surface). The isolated Guide&apos;s
          own guardrail/topical-refusal metrics live in the Guide&apos;s own sink; surfacing them would
          need a Guide metrics endpoint (future).
        </p>
      </div>
    </>
  )
}

function CostSourceCard({
  title,
  subtitle,
  badge,
  badgeClass,
  state,
  data,
}: {
  title: string
  subtitle: string
  badge: string
  badgeClass: string
  state: CostState
  data: NormalizedCost | null
}) {
  return (
    <Card className="border-[0.5px] border-border shadow-sm">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium text-foreground">{title}</CardTitle>
          <Badge variant="outline" className={cn("font-mono text-[10px] uppercase", badgeClass)}>
            {badge}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </CardHeader>
      <CardContent>
        {state === "loading" && (
          <p className="py-4 text-center text-sm text-muted-foreground">Loading cost…</p>
        )}

        {state === "unreachable" && (
          <p className="py-4 text-center text-sm text-muted-foreground">
            {title === "Public Guide"
              ? "Guide metrics unreachable — the isolated Guide service isn't responding. No fabricated figures are shown."
              : "Cost is unavailable right now. No fabricated figures are shown."}
          </p>
        )}

        {state === "ready" && data && (
          <>
            <div className="mb-4 grid grid-cols-2 gap-3">
              <MetricCard label="Turns" value={data.turns} />
              <MetricCard label="Cost (USD)" value={usd(data.totalCost)} />
              <MetricCard
                label="Tokens"
                value={data.totalTokens.toLocaleString()}
                subtitle={`${data.promptTokens.toLocaleString()} prompt · ${data.completionTokens.toLocaleString()} completion`}
              />
              <MetricCard label="Avg Latency" value={`${Math.round(data.avgLatencyMs)}ms`} />
            </div>

            {data.totalCost === 0 && (
              <p className="mb-3 text-xs text-muted-foreground">
                Cost is $0.0000 — tokens are captured per turn, but no per-1k rate is configured for
                this source yet, so dollar cost is honestly zero.
              </p>
            )}

            {data.rollups.length === 0 ? (
              <p className="py-3 text-center text-sm text-muted-foreground">No turns recorded yet.</p>
            ) : (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  By model
                </p>
                {data.rollups.map((r) => (
                  <div
                    key={r.label}
                    className="flex items-center justify-between gap-2 font-mono text-xs"
                  >
                    <span className="truncate text-foreground">{r.label}</span>
                    <span className="shrink-0 text-muted-foreground">
                      {r.total_tokens.toLocaleString()} tok · {usd(r.total_cost)} ·{" "}
                      {Math.round(r.avg_latency_ms)}ms · {r.turns} turn{r.turns === 1 ? "" : "s"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}
