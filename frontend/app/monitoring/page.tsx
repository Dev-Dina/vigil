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
import { MetricCard, PulseDivider, StatusDot, type StatusType } from "@/components/vigil"
import { getDrift, getModels } from "@/lib/stubs"
import type { DriftPoint, ModelStatus } from "@/lib/types"
import { cn } from "@/lib/utils"

function healthStatus(h: ModelStatus["health"]): StatusType {
  if (h === "healthy") return "calm"
  if (h === "degraded") return "watch"
  return "risk"
}

export default function MonitoringPage() {
  const [models, setModels] = useState<ModelStatus[]>([])
  const [drift, setDrift] = useState<DriftPoint[]>([])

  useEffect(() => {
    // TODO(phase4/5): wire to GET /monitoring/models
    getModels().then((page) => setModels(page.items))
    // TODO(phase4/5): wire to GET /monitoring/drift
    getDrift().then((page) => setDrift(page.items))
  }, [])

  const champion = models.find((m) => m.role === "champion")
  const breached = drift.filter((d) => d.breached).length

  return (
    <div className="min-h-screen bg-background">
      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Model Monitoring
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Champion / challenger health, regime, and drift signals
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

        {/* Drift signals — GET /monitoring/drift (DriftPoint) */}
        <div className="mb-8">
          <h2 className="mb-4 text-lg font-semibold text-foreground">Drift Signals</h2>
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
                  <TableRow key={`${d.model_name}-${d.metric}-${i}`} className="border-b-[0.5px]">
                    <TableCell className="font-mono text-sm text-foreground">
                      {d.model_name}
                    </TableCell>
                    <TableCell className="font-mono text-sm text-muted-foreground">
                      {d.metric}
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-right font-mono text-sm",
                        d.breached ? "text-status-risk" : "text-foreground",
                      )}
                    >
                      {d.value.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-muted-foreground">
                      {d.threshold.toFixed(2)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <StatusDot status={d.breached ? "risk" : "calm"} size="sm" />
                        <span className="text-sm text-foreground">
                          {d.breached ? "Breached" : "OK"}
                        </span>
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
        </div>
      </main>
    </div>
  )
}
