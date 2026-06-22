import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Whole days between an ISO datetime and now (>= 0). Drives "Days Enrolled". */
export function daysSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000))
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
