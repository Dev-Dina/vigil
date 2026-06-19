"use client"

import Image from "next/image"
import {
  FluidTabs,
  type FluidTab,
  GuideChat,
  PulseDivider,
  SideLogin,
  StatusDot,
  type StatusType,
} from "@/components/vigil"

// Public landing page (BARE route — see app-chrome.tsx). The entry point to Vigil:
//   • Brand hero (the real logo lockup)
//   • Fluid tabs explaining the project (built on the existing tokens)
//   • A PUBLIC chat wired to the isolated Guide (lib/guide.ts) — no login, no tenant data
//   • A side login (petrol rail) wired to the existing auth flow → leads into the app
// The scope-bound in-app AssistantPanel never mounts here (this route is bare).

// ---- tab content ---------------------------------------------------------

const TRIAGE_BANDS: { status: StatusType; label: string; copy: string }[] = [
  { status: "calm", label: "Calm", copy: "On track — retention signals look healthy." },
  { status: "watch", label: "Watch", copy: "Emerging risk — worth a closer look." },
  { status: "risk", label: "Risk", copy: "Needs triage — elevated dropout risk." },
]

function OverviewTab() {
  return (
    <div className="space-y-6">
      <p className="max-w-2xl text-[15px] leading-relaxed text-foreground">
        Vigil is a clinical-trial <span className="font-medium">retention intelligence</span>{" "}
        platform. It surfaces and explains dropout risk early, ranks the cohort for triage, and
        grounds every flag in its reason — so coordinators can intervene before a participant
        disengages, with the why in hand.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {TRIAGE_BANDS.map((b) => (
          <div
            key={b.status}
            className="rounded-xl border-[0.5px] border-border bg-card p-4 shadow-sm"
          >
            <div className="flex items-center gap-2">
              <StatusDot status={b.status} pulse={b.status === "risk"} />
              <span className="text-sm font-medium text-foreground">{b.label}</span>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{b.copy}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

const STEPS: { n: string; title: string; copy: string }[] = [
  {
    n: "01",
    title: "Surface",
    copy: "A deep-learning signal scores dropout risk early from engagement, visit, and adherence patterns.",
  },
  {
    n: "02",
    title: "Rank",
    copy: "The cohort is ordered for triage so the highest-risk participants surface first.",
  },
  {
    n: "03",
    title: "Explain",
    copy: "Every flag carries its contributing factors and a grounded, cited rationale.",
  },
]

function HowItWorksTab() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {STEPS.map((s) => (
        <div key={s.n} className="rounded-xl border-[0.5px] border-border bg-card p-5 shadow-sm">
          <span className="font-mono text-xs font-medium uppercase tracking-wide text-status-calm">
            {s.n}
          </span>
          <h4 className="mt-2 text-base font-semibold text-foreground">{s.title}</h4>
          <p className="mt-1.5 text-sm text-muted-foreground">{s.copy}</p>
        </div>
      ))}
    </div>
  )
}

const SURFACES: { name: string; copy: string }[] = [
  { name: "Dashboard", copy: "Trial-level risk overview with the highest-risk participants." },
  { name: "Triage", copy: "The ranked worklist — work the cohort top-down." },
  { name: "At-Risk", copy: "The focused queue of participants that need attention now." },
  { name: "Cohort Health", copy: "Retention analytics across the scoped cohort." },
  { name: "Monitoring", copy: "Model traffic, cost, guardrails, and drift (platform roles)." },
]

function ProductTab() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {SURFACES.map((s) => (
        <div
          key={s.name}
          className="rounded-xl border-[0.5px] border-border bg-card p-4 shadow-sm transition-colors hover:border-foreground/20"
        >
          <h4 className="text-sm font-semibold text-foreground">{s.name}</h4>
          <p className="mt-1 text-sm text-muted-foreground">{s.copy}</p>
        </div>
      ))}
    </div>
  )
}

const TABS: FluidTab[] = [
  { id: "overview", label: "Overview", content: <OverviewTab /> },
  { id: "how", label: "How it works", content: <HowItWorksTab /> },
  { id: "product", label: "The product", content: <ProductTab /> },
]

// ---- page ----------------------------------------------------------------

export default function WelcomePage() {
  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[1fr_minmax(360px,440px)]">
      {/* Left: brand + tabs + public Guide chat */}
      <main className="px-6 py-12 sm:px-10 lg:px-14 lg:py-16">
        <div className="mx-auto max-w-3xl">
          {/* Hero */}
          <Image
            src="/vigil-logo.png"
            alt="Vigil — Clinical Trial Retention Intelligence"
            width={240}
            height={240}
            priority
            className="-ml-2 h-auto w-[200px] sm:w-[228px]"
          />
          <h1 className="mt-6 text-3xl font-semibold leading-tight tracking-tight text-foreground sm:text-4xl">
            Surface risk early. Triage the cohort.
            <br className="hidden sm:block" /> Explain every flag.
          </h1>
          <p className="mt-4 max-w-xl text-base leading-relaxed text-muted-foreground">
            Retention intelligence for clinical trials — ranking the cohort for triage and grounding
            every flag in its reason.
          </p>

          <PulseDivider className="my-10" />

          {/* Fluid tabs */}
          <FluidTabs tabs={TABS} />

          {/* Public Guide chat */}
          <section className="mt-14">
            <div className="mb-4">
              <span className="font-mono text-xs font-medium uppercase tracking-wide text-status-calm">
                Public guide
              </span>
              <h2 className="mt-1 text-lg font-semibold text-foreground">Ask about Vigil</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Grounded in approved public documents only. No login, no participant data — this chat
                is served by the isolated Guide service, never the in-app assistant.
              </p>
            </div>
            <GuideChat />
          </section>
        </div>
      </main>

      {/* Right: side login rail (existing auth flow) */}
      <aside className="lg:sticky lg:top-0 lg:h-screen">
        <SideLogin className="min-h-[420px] lg:min-h-screen" />
      </aside>
    </div>
  )
}
