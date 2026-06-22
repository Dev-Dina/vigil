import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Whole days between an ISO datetime and now (>= 0). Drives "Days Enrolled".
 *  Returns null for a missing/invalid date so callers render "—" instead of "NaN". */
export function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return null
  return Math.max(0, Math.floor((Date.now() - t) / 86_400_000))
}

/** The UI risk status (drives the dot/dial/arc color). */
export type RiskStatus = "calm" | "watch" | "risk"

/** Map the AUTHORITATIVE backend risk_band → a UI status. Always prefer this over re-thresholding a
 *  score: a participant's band is the source of truth and the display must never contradict it. */
export function statusFromBand(band: string | null | undefined): RiskStatus | null {
  if (band === "high") return "risk"
  if (band === "medium") return "watch"
  if (band === "low") return "calm"
  return null
}

/** Fallback for components that have only a 0–100 score and no band (e.g. a cohort MEAN). Cuts are
 *  ALIGNED to the backend bands (>0.6 high / >0.3 medium ⇒ >60 / >30) so they cannot contradict a
 *  participant's authoritative band. */
export function statusFromScore(score: number): RiskStatus {
  if (score > 60) return "risk"
  if (score > 30) return "watch"
  return "calm"
}

/** A compact form of the internal UUID, used ONLY as a fallback when no coded_ref is available
 *  (non-identity roles). Never the primary label when a coded_ref exists. */
export function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id
}

/** The primary, concise participant label: the human-readable coded_ref (e.g. "A-0001") when
 *  present (identity roles), else a compact UUID fallback. The full UUID stays the routing/API key —
 *  this is presentation only and never invents a coded_ref the caller isn't entitled to see. */
export function participantLabel(codedRef: string | null | undefined, id: string): string {
  return codedRef ?? shortId(id)
}
