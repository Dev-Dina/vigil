import { cn } from "../../lib/utils"
import { RiskBandBadge, type RiskBand } from "./risk-band-badge"
import { RiskTrendSparkline } from "./risk-trend-sparkline"

export interface WatchlistRow {
  id: string
  participantLabel: string
  riskBand: RiskBand
  riskScore: number
  trendScores: number[]
  sponsorClass?: string
  lastVisitLabel?: string
  marker?: "synthetic" | "none"
}

interface WatchlistTableProps {
  rows: WatchlistRow[]
  className?: string
}

export function WatchlistTable({ rows, className }: WatchlistTableProps) {
  if (rows.length === 0) {
    return (
      <div
        className={cn(
          "rounded-lg border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground",
          className
        )}
      >
        No watchlist rows
      </div>
    )
  }

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-border bg-card shadow-sm",
        className
      )}
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border bg-muted/30">
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-muted-foreground">
                Participant
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-muted-foreground">
                Band
              </th>
              <th className="px-4 py-3 text-right text-xs font-medium uppercase text-muted-foreground">
                Score
              </th>
              <th className="px-4 py-3 text-center text-xs font-medium uppercase text-muted-foreground">
                Trend
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-muted-foreground">
                Sponsor class
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium uppercase text-muted-foreground">
                Last visit
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                className="border-b border-border last:border-b-0 hover:bg-muted/30"
              >
                <td className="px-4 py-3">
                  <span className="font-mono text-sm font-medium text-foreground">
                    {row.participantLabel}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <RiskBandBadge band={row.riskBand} marker={row.marker} />
                </td>
                <td className="px-4 py-3 text-right">
                  <span className="font-mono text-sm font-semibold text-foreground">
                    {row.riskScore}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="flex justify-center">
                    <RiskTrendSparkline scores={row.trendScores} />
                  </div>
                </td>
                <td className="px-4 py-3 text-sm text-muted-foreground">
                  {row.sponsorClass ?? "—"}
                </td>
                <td className="px-4 py-3 text-sm text-muted-foreground">
                  {row.lastVisitLabel ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
