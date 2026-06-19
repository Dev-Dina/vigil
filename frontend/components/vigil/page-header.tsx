import { cn } from "@/lib/utils"

// Unified interior page header — the landing page's eyebrow + confident title aesthetic, applied
// across the authenticated app so the surfaces read as one considered product. Presentation only;
// carries no data or behavior. A status-triad hairline accent ties it to the brand.
export function PageHeader({
  eyebrow,
  title,
  subtitle,
  action,
  className,
}: {
  eyebrow?: string
  title: string
  subtitle?: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn("mb-8", className)}>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          {eyebrow && (
            <span className="font-mono text-xs font-medium uppercase tracking-[0.2em] text-status-calm">
              {eyebrow}
            </span>
          )}
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">{title}</h1>
          {subtitle && (
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      <div className="mt-5 h-px w-full bg-gradient-to-r from-status-calm/40 via-border to-transparent" />
    </div>
  )
}
