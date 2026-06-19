"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

// Fluid tabs — a smooth, on-brand tabbed section built on the existing tokens.
// "Fluid" = a ref-measured active-indicator that slides (transition-all) plus a panel cross-fade
// via tw-animate-css (animate-in fade-in slide-in-from-bottom). No animation library.

export interface FluidTab {
  id: string
  label: string
  content: React.ReactNode
}

export function FluidTabs({
  tabs,
  className,
}: {
  tabs: FluidTab[]
  className?: string
}) {
  const [active, setActive] = React.useState<string>(tabs[0]?.id ?? "")
  const listRef = React.useRef<HTMLDivElement>(null)
  const btnRefs = React.useRef<Record<string, HTMLButtonElement | null>>({})
  const [indicator, setIndicator] = React.useState<{ left: number; width: number }>({
    left: 0,
    width: 0,
  })

  // Measure the active tab and position the sliding indicator. Runs before paint
  // (useLayoutEffect) so there is no first-frame jump; re-measures on resize.
  const measure = React.useCallback(() => {
    const el = btnRefs.current[active]
    const list = listRef.current
    if (!el || !list) return
    const elRect = el.getBoundingClientRect()
    const listRect = list.getBoundingClientRect()
    setIndicator({ left: elRect.left - listRect.left, width: elRect.width })
  }, [active])

  React.useLayoutEffect(() => {
    measure()
  }, [measure])

  React.useEffect(() => {
    window.addEventListener("resize", measure)
    return () => window.removeEventListener("resize", measure)
  }, [measure])

  const activeTab = tabs.find((t) => t.id === active) ?? tabs[0]
  if (!activeTab) return null

  return (
    <div className={className}>
      <div
        ref={listRef}
        role="tablist"
        aria-label="About Vigil"
        className="relative flex items-center gap-1 overflow-x-auto border-b border-border"
      >
        {tabs.map((t) => {
          const selected = t.id === active
          return (
            <button
              key={t.id}
              ref={(node) => {
                btnRefs.current[t.id] = node
              }}
              role="tab"
              type="button"
              aria-selected={selected}
              onClick={() => setActive(t.id)}
              className={cn(
                "relative whitespace-nowrap px-4 py-2.5 text-sm font-medium transition-colors",
                "focus:outline-none focus-visible:text-foreground",
                selected ? "text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          )
        })}
        <span
          aria-hidden
          className="pointer-events-none absolute -bottom-px h-0.5 rounded-full bg-status-calm transition-all duration-300 ease-out"
          style={{ left: indicator.left, width: indicator.width }}
        />
      </div>

      <div className="pt-6">
        <div
          // key forces a fresh mount per tab so the enter animation replays
          key={activeTab.id}
          role="tabpanel"
          className="animate-in fade-in slide-in-from-bottom-2 duration-300 ease-out"
        >
          {activeTab.content}
        </div>
      </div>
    </div>
  )
}
