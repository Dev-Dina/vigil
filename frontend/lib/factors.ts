// Display-layer grouping for the risk-explanation contributing factors (Gate DEMO-POLISH item 4).
//
// The model's occlusion attribution (vigil/workers/tasks.py) is honest but its input channels are
// correlated: `consecutive_missed` / `cumulative_missed` / `missed` are all "missed-visit" signals
// and `attended` is the exact complement of `missed`, so the raw list reads as several near-equal
// small bars. This groups the missed-visit channels into ONE dominant "Disengagement" factor, drops
// the `attended` mirror when `missed` is present, and marks the primary driver — WITHOUT changing any
// number: a grouped impact is the SUM of its real sub-contributions (surfaced as sub-features), and
// every other factor keeps its own real value. Nothing is invented or rescaled.

import type { FactorContribution } from "@/lib/types"

export interface DisplaySubFactor {
  name: string
  impact: number // |contribution| * 100, rounded
  description: string // "Increases risk" | "Decreases risk"
}

export interface DisplayFactor {
  name: string
  impact: number // |contribution| * 100; for a group, the SUM of the sub |contribution| * 100
  description: string // direction (net, for a group)
  primary: boolean // the largest-|impact| factor — the dominant driver
  grouped: boolean // true → impact is the grouped sum of `subs`
  subs?: DisplaySubFactor[]
}

// The correlated missed-visit channels collapsed into one "Disengagement" factor.
const MISSED_VISIT_CHANNELS = new Set(["consecutive_missed", "cumulative_missed", "missed"])

function humanize(tag: string): string {
  return tag.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

function direction(contribution: number): string {
  return contribution >= 0 ? "Increases risk" : "Decreases risk"
}

export function groupFactors(factors: FactorContribution[]): DisplayFactor[] {
  // Drop `attended` when `missed` is present — they are complements; showing both pads the list.
  const hasMissed = factors.some((f) => f.feature === "missed")
  const fs = factors.filter((f) => !(hasMissed && f.feature === "attended"))

  const missed = fs.filter((f) => MISSED_VISIT_CHANNELS.has(f.feature))
  const others = fs.filter((f) => !MISSED_VISIT_CHANNELS.has(f.feature))

  const out: DisplayFactor[] = []

  if (missed.length > 0) {
    const summed = missed.reduce((acc, f) => acc + Math.abs(f.contribution), 0)
    const net = missed.reduce((acc, f) => acc + f.contribution, 0)
    out.push({
      name: "Disengagement (missed visits)",
      impact: Math.round(summed * 100),
      description: direction(net),
      primary: false,
      grouped: true,
      subs: missed
        .slice()
        .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
        .map((f) => ({
          name: humanize(f.feature),
          impact: Math.round(Math.abs(f.contribution) * 100),
          description: direction(f.contribution),
        })),
    })
  }

  for (const o of others) {
    out.push({
      name: humanize(o.feature),
      impact: Math.round(Math.abs(o.contribution) * 100),
      description: direction(o.contribution),
      primary: false,
      grouped: false,
    })
  }

  out.sort((a, b) => b.impact - a.impact)
  if (out.length > 0) out[0].primary = true
  return out
}
