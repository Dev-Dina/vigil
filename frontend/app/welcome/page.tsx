"use client"

import Image from "next/image"
import {
  Activity,
  ArrowRight,
  ListOrdered,
  MessageSquareText,
  Network,
  Radar,
  ShieldCheck,
  Sparkles,
} from "lucide-react"
import { GuideChat, SideLogin, StatusDot, type StatusType } from "@/components/vigil"

// Public landing page (/welcome) — BARE route (see app-chrome.tsx): no nav, no scope-bound assistant.
// Redesigned hero + value sections, an ECG/vitals "monitoring" motif, and the two existing wirings
// PRESERVED: the public Guide chat (lib/guide.ts, browser-direct :8080, no JWT) and the side login
// (useAuth → /auth/login). Honest, assistive framing throughout — Vigil SURFACES and EXPLAINS risk.

// ---- hero vitals motif ---------------------------------------------------
// A faint ECG/heart-rate trace that evokes monitoring. The base line is drawn at rest (the full
// static line reduced-motion users see); motion-safe draws it in once AND runs a bright pulse that
// continuously sweeps along the trace so it reads as a live monitor. pathLength normalises the
// dash units to 1400 so both the draw and the sweep are exact regardless of true path length.
const VITALS_PATH =
  "M0 60 H230 l18 -2 18 4 14 -40 16 78 18 -64 14 28 22 -2 H520 l16 -2 16 30 14 -70 16 60 16 -6 H760 l18 0 16 -34 14 64 16 -52 14 24 20 0 H1060 l18 -2 18 4 H1200"

function VitalsTrace() {
  return (
    <svg
      className="absolute inset-x-0 bottom-[14%] h-40 w-full opacity-[0.5]"
      viewBox="0 0 1200 120"
      fill="none"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {/* Base trace — full static line at rest; motion-safe draws it in once. */}
      <path
        d={VITALS_PATH}
        pathLength={1400}
        stroke="var(--color-status-calm)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={
          { strokeDasharray: 1400, strokeDashoffset: 0, "--vigil-dash": "1400" } as React.CSSProperties
        }
        className="motion-safe:animate-[vigil-draw_2.6s_ease-out_forwards]"
      />
      {/* Travelling pulse — a bright blip sweeping along the trace. Hidden at rest (opacity-0) so
          reduced-motion shows only the static base line; motion-safe reveals + animates it. */}
      <path
        d={VITALS_PATH}
        pathLength={1400}
        stroke="var(--color-status-calm)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ strokeDasharray: "90 1310" } as React.CSSProperties}
        className="opacity-0 [filter:drop-shadow(0_0_6px_var(--color-status-calm))] motion-safe:opacity-90 motion-safe:animate-[vigil-ecg_3s_linear_infinite]"
      />
    </svg>
  )
}

function HeroBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      {/* dot grid */}
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.06)_1px,transparent_1px)] [background-size:24px_24px]" />
      {/* teal glow */}
      <div className="absolute -left-32 top-[-10%] h-[460px] w-[460px] rounded-full bg-status-calm/25 blur-[120px] motion-safe:animate-[vigil-glow_9s_ease-in-out_infinite]" />
      {/* warm glow */}
      <div className="absolute -right-24 bottom-[-20%] h-[420px] w-[420px] rounded-full bg-status-watch/15 blur-[130px] motion-safe:animate-[vigil-glow_11s_ease-in-out_infinite]" />
      <VitalsTrace />
      {/* monitor scan sweep */}
      <div className="absolute inset-y-0 left-0 w-40 bg-gradient-to-r from-transparent via-status-calm/10 to-transparent blur-md motion-safe:animate-[vigil-scan_8s_linear_infinite]" />
      {/* status-triad hairline at the base */}
      <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-status-calm via-status-watch to-status-risk opacity-70" />
    </div>
  )
}

function LiveChip() {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 text-xs font-medium text-white/80 backdrop-blur-sm">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full rounded-full bg-status-calm motion-safe:animate-[vigil-ring_2.4s_ease-out_infinite]" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-status-calm" />
      </span>
      Clinical-trial retention intelligence
    </span>
  )
}

// ---- pillars (Surface / Rank / Explain) ----------------------------------
const PILLARS: { n: string; icon: typeof Radar; status: StatusType; title: string; copy: string }[] = [
  {
    n: "01",
    icon: Radar,
    status: "risk",
    title: "Surface",
    copy: "A deep-learning signal reads engagement, visit, and adherence patterns to surface dropout risk early — before a participant quietly disengages.",
  },
  {
    n: "02",
    icon: ListOrdered,
    status: "watch",
    title: "Rank",
    copy: "The cohort is ordered for triage, so the participants most worth a coordinator's attention rise to the top of the worklist.",
  },
  {
    n: "03",
    icon: MessageSquareText,
    status: "calm",
    title: "Explain",
    copy: "Every flag carries its contributing factors and a grounded, cited rationale — the why in hand, so teams can act with judgment.",
  },
]

function Pillars() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {PILLARS.map((p, i) => {
        const Icon = p.icon
        return (
          <div
            key={p.n}
            style={{ animationDelay: `${i * 90}ms` }}
            className="group relative overflow-hidden rounded-2xl border-[0.5px] border-border bg-card p-6 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-md motion-safe:animate-[vigil-rise_0.6s_ease-out_both]"
          >
            <div className="flex items-center justify-between">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/[0.04] text-foreground transition-colors group-hover:bg-primary/[0.07]">
                <Icon className="h-5 w-5" strokeWidth={1.75} />
              </span>
              <span className="font-mono text-xs font-medium tracking-widest text-muted-foreground">
                {p.n}
              </span>
            </div>
            <div className="mt-5 flex items-center gap-2">
              <StatusDot status={p.status} pulse={p.status === "risk"} />
              <h3 className="text-lg font-semibold tracking-tight text-foreground">{p.title}</h3>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{p.copy}</p>
          </div>
        )
      })}
    </div>
  )
}

// ---- feature grid --------------------------------------------------------
const FEATURES: { icon: typeof Network; title: string; copy: string }[] = [
  {
    icon: Network,
    title: "Multi-tenant by design",
    copy: "Sponsors and CROs share one platform without sharing data. Every tenant boundary is enforced by the database, not by application code.",
  },
  {
    icon: ShieldCheck,
    title: "An isolated public Guide",
    copy: "This page's chatbot is a separate, structurally-isolated service — its own credentials and network, no route to the dashboard, participant data, or models.",
  },
  {
    icon: Sparkles,
    title: "Assistive, explained AI",
    copy: "Grounded answers and risk explanations cite their source. The assistant describes the model's signal in plain language — it never invents clinical causation.",
  },
  {
    icon: Activity,
    title: "MLOps & observability",
    copy: "Champion/challenger model routing, drift detection, cost and guardrail telemetry — the operational story behind every score, visible to platform roles.",
  },
]

function Features() {
  return (
    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-2xl border-[0.5px] border-border bg-border sm:grid-cols-2">
      {FEATURES.map((f) => {
        const Icon = f.icon
        return (
          <div
            key={f.title}
            className="group bg-card p-7 transition-colors hover:bg-secondary"
          >
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground transition-transform duration-300 group-hover:scale-105">
              <Icon className="h-5 w-5" strokeWidth={1.75} />
            </span>
            <h3 className="mt-4 text-base font-semibold tracking-tight text-foreground">{f.title}</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{f.copy}</p>
          </div>
        )
      })}
    </div>
  )
}

// ---- risk language band --------------------------------------------------
const BANDS: { status: StatusType; label: string; copy: string }[] = [
  { status: "calm", label: "Calm", copy: "On track — retention signals look healthy." },
  { status: "watch", label: "Watch", copy: "Emerging risk — worth a closer look." },
  { status: "risk", label: "Risk", copy: "Needs triage — elevated dropout risk, surfaced early." },
]

function RiskLanguage() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {BANDS.map((b) => (
        <div
          key={b.status}
          className="flex items-start gap-3 rounded-xl border-[0.5px] border-border bg-card p-4 shadow-sm"
        >
          <span className="mt-0.5">
            <StatusDot status={b.status} pulse={b.status === "risk"} />
          </span>
          <div>
            <span className="text-sm font-semibold text-foreground">{b.label}</span>
            <p className="mt-0.5 text-sm text-muted-foreground">{b.copy}</p>
          </div>
        </div>
      ))}
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-xs font-medium uppercase tracking-[0.2em] text-status-calm">
      {children}
    </span>
  )
}

// ---- page ----------------------------------------------------------------
export default function WelcomePage() {
  return (
    <div className="min-h-screen scroll-smooth bg-background">
      {/* ============================ HERO ============================ */}
      <section className="relative isolate overflow-hidden bg-primary text-primary-foreground">
        <HeroBackground />

        {/* top bar */}
        <header className="relative z-10 mx-auto flex max-w-6xl items-center justify-between px-6 py-6 sm:px-10">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-white">
              <Image src="/vigil-mark.png" alt="" width={22} height={22} />
            </span>
            <span className="text-lg font-semibold tracking-tight">VIGIL</span>
          </div>
          <a
            href="#signin"
            className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 text-sm font-medium text-white/90 transition-colors hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-status-calm/70"
          >
            Sign in
          </a>
        </header>

        {/* hero content */}
        <div className="relative z-10 mx-auto max-w-6xl px-6 pb-28 pt-12 sm:px-10 sm:pb-36 sm:pt-20">
          <div className="max-w-3xl">
            <LiveChip />
            <h1 className="mt-7 text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
              Surface risk early.
              <br />
              Triage the cohort.{" "}
              <span className="bg-gradient-to-r from-status-calm via-status-calm to-status-watch bg-clip-text text-transparent">
                Explain every flag.
              </span>
            </h1>
            <p className="mt-6 max-w-xl text-pretty text-lg leading-relaxed text-white/70">
              Vigil is a retention-intelligence platform for clinical trials. It surfaces and explains
              participant dropout risk early, ranks the cohort for triage, and grounds every flag in
              its reason — so coordinators can intervene before a participant disengages.
            </p>

            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <a
                href="#signin"
                className="group inline-flex items-center justify-center gap-2 rounded-xl bg-white px-6 py-3 text-sm font-semibold text-primary shadow-sm transition-all hover:bg-white/90 focus:outline-none focus:ring-2 focus:ring-status-calm focus:ring-offset-2 focus:ring-offset-primary"
              >
                Enter the dashboard
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </a>
              <a
                href="#guide"
                className="inline-flex items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-6 py-3 text-sm font-semibold text-white/90 backdrop-blur-sm transition-colors hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-status-calm/70"
              >
                <MessageSquareText className="h-4 w-4" />
                Ask the Guide
              </a>
            </div>

            {/* honest micro-line */}
            <p className="mt-10 max-w-xl text-xs leading-relaxed text-white/60">
              A demonstration on a clearly-labelled synthetic cohort calibrated to real clinical-trial
              registry statistics — it proves the method, not clinical prediction. No real patient data.
            </p>
          </div>
        </div>
      </section>

      {/* ====================== HOW IT WORKS ====================== */}
      <section className="mx-auto max-w-6xl px-6 py-20 sm:px-10 sm:py-28">
        <div className="mb-10 max-w-2xl">
          <SectionLabel>How it works</SectionLabel>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            Retention intelligence, made legible
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            Three moves, in order — surface the risk, rank the cohort, and explain the flag. Vigil is
            assistive: it puts the signal and its reasons in front of people, who decide what to do.
          </p>
        </div>
        <Pillars />
      </section>

      {/* ====================== BUILT FOR TRIALS ====================== */}
      <section className="border-y-[0.5px] border-border bg-secondary/40">
        <div className="mx-auto max-w-6xl px-6 py-20 sm:px-10 sm:py-28">
          <div className="mb-10 max-w-2xl">
            <SectionLabel>The platform</SectionLabel>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
              Built for the way trials are run
            </h2>
            <p className="mt-3 text-base leading-relaxed text-muted-foreground">
              Sponsors, CROs, and sites on one platform — with hard tenant isolation, an assistive
              explained-AI layer, and the operational machinery to keep every model honest.
            </p>
          </div>
          <Features />
        </div>
      </section>

      {/* ====================== RISK LANGUAGE ====================== */}
      <section className="mx-auto max-w-6xl px-6 py-20 sm:px-10 sm:py-24">
        <div className="mb-8 max-w-2xl">
          <SectionLabel>A shared risk language</SectionLabel>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            Calm, watch, risk
          </h2>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">
            One consistent triage vocabulary across every surface, so a glance tells a coordinator
            where to look first.
          </p>
        </div>
        <RiskLanguage />
      </section>

      {/* ====================== TRY IT / SIGN IN ====================== */}
      <section className="border-t-[0.5px] border-border bg-secondary/40">
        <div className="mx-auto grid max-w-6xl grid-cols-1 gap-10 px-6 py-20 sm:px-10 sm:py-28 lg:grid-cols-[1.05fr_0.95fr] lg:items-start">
          {/* Guide chat */}
          <div id="guide" className="scroll-mt-20">
            <SectionLabel>Public guide</SectionLabel>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
              Ask Vigil&apos;s assistant
            </h2>
            <p className="mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
              Grounded in approved public documents only, with citations. No login, no participant
              data — this chat is served by the isolated Guide service, never the in-app assistant.
            </p>
            <div className="mt-6">
              {/* Wiring UNCHANGED: GuideChat → lib/guide.ts → POST :8080/ask (no JWT). */}
              <GuideChat />
            </div>
          </div>

          {/* Sign in */}
          <div id="signin" className="scroll-mt-20">
            <div className="overflow-hidden rounded-2xl border-[0.5px] border-border shadow-sm">
              {/* Wiring UNCHANGED: SideLogin → useAuth().login → /auth/login → /auth/me. */}
              <SideLogin className="min-h-[480px]" />
            </div>
          </div>
        </div>
      </section>

      {/* ====================== FOOTER ====================== */}
      <footer className="border-t-[0.5px] border-border bg-background">
        <div className="mx-auto max-w-6xl px-6 py-10 sm:px-10">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary">
                <Image src="/vigil-mark.png" alt="" width={18} height={18} />
              </span>
              <span className="text-sm font-semibold tracking-tight text-foreground">VIGIL</span>
            </div>
            <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
              Retention intelligence for clinical trials — assistive, not predictive: Vigil surfaces
              and explains dropout risk so teams intervene. A demonstration on synthetic data
              calibrated to real registry statistics; no real patient data, no clinical validation
              claim. The public Guide on this page shares no login, data, or endpoints with the
              dashboard.
            </p>
          </div>
        </div>
      </footer>
    </div>
  )
}
