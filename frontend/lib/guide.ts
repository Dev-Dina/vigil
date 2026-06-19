// Public Guide client — the ISOLATION BOUNDARY in code.
//
// This module talks ONLY to the isolated public Guide service (the document-only chatbot that
// explains Vigil from approved docs). It is deliberately SEPARATE from lib/api.ts:
//   - It NEVER reads or sends a JWT / Authorization header.
//   - It NEVER imports lib/api.ts, auth-context, or any tenant-scoped surface.
//   - It targets the Guide's own host (NEXT_PUBLIC_GUIDE_URL), never the app API (:8000).
// The Guide shares no credentials, DB, or endpoints with the real app (see /specs/isolation.md).
// The landing-page chat is wired here, NOT to the in-app scope-bound AssistantPanel.

const GUIDE_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_GUIDE_URL ?? "http://localhost:8080")
    : "http://localhost:8080"

// Response shape mirrors guide/app.py AskOut: { answer, citations, guardrail_decision, status }.
export interface GuideCitation {
  source_type: string
  source_id: string
  locator: string
}

export interface GuideAnswer {
  answer: string
  citations: GuideCitation[]
  // "allowed" | "blocked"
  guardrail_decision: string
  // "ok" | "refused"
  status: string
}

export class GuideError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "GuideError"
  }
}

// POST {GUIDE_BASE}/ask  body: { question }  ->  GuideAnswer
// No auth header by design. A transport/HTTP failure throws GuideError so the caller can show an
// honest "guide unreachable" state — never a fabricated answer.
export async function askGuide(question: string): Promise<GuideAnswer> {
  let resp: Response
  try {
    resp = await fetch(`${GUIDE_BASE}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    })
  } catch {
    throw new GuideError("The public guide isn't reachable right now.")
  }
  if (!resp.ok) {
    throw new GuideError(`The guide returned an error (${resp.status}).`)
  }
  return (await resp.json()) as GuideAnswer
}
