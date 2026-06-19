"use client"

import * as React from "react"
import { Send } from "lucide-react"
import { cn } from "@/lib/utils"
import { askGuide, GuideError, type GuideCitation } from "@/lib/guide"

// Public Guide chat — the general "ask about Vigil" surface on the landing page.
// Wired to the ISOLATED public Guide via lib/guide.ts (POST :8080/ask, NO auth, NO tenant data).
// This is NOT the in-app scope-bound assistant (that lives post-login inside the app and carries
// the JWT). Honest states throughout: refusals render the guardrail refusal; transport failures
// render an "unreachable" notice — never a fabricated answer.

interface ChatMessage {
  id: string
  role: "user" | "guide"
  content: string
  pending?: boolean
  refused?: boolean
  isError?: boolean
  citations?: GuideCitation[]
}

function CitationChips({ citations }: { citations: GuideCitation[] }) {
  if (citations.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {citations.map((c, i) => (
        <span
          key={`${c.source_id}-${c.locator}-${i}`}
          className="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 font-mono text-[11px] text-muted-foreground"
          title={c.source_type}
        >
          {c.source_id}
          <span className="text-border">·</span>
          {c.locator}
        </span>
      ))}
    </div>
  )
}

const SUGGESTIONS = [
  "What does Vigil do?",
  "How does it predict dropout?",
  "How is participant data protected?",
]

export function GuideChat({ className }: { className?: string }) {
  const [messages, setMessages] = React.useState<ChatMessage[]>([])
  const [input, setInput] = React.useState("")
  const [sending, setSending] = React.useState(false)
  const endRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const ask = React.useCallback(
    async (raw: string) => {
      const question = raw.trim()
      if (!question || sending) return
      setSending(true)
      setInput("")

      const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: "user", content: question }
      const pendingId = `g-${Date.now()}`
      const pendingMsg: ChatMessage = {
        id: pendingId,
        role: "guide",
        content: "Consulting approved docs…",
        pending: true,
      }
      setMessages((prev) => [...prev, userMsg, pendingMsg])

      try {
        const res = await askGuide(question)
        const refused = res.status === "refused" || res.guardrail_decision === "blocked"
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId
              ? {
                  ...m,
                  content: res.answer,
                  pending: false,
                  refused,
                  citations: res.citations,
                }
              : m,
          ),
        )
      } catch (e) {
        const content =
          e instanceof GuideError
            ? e.message
            : "Something went wrong reaching the guide. Please try again."
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId ? { ...m, content, pending: false, isError: true } : m,
          ),
        )
      } finally {
        setSending(false)
      }
    },
    [sending],
  )

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void ask(input)
    }
  }

  const empty = messages.length === 0

  return (
    <div
      className={cn(
        "flex flex-col overflow-hidden rounded-xl border-[0.5px] border-border bg-card shadow-sm",
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-status-calm opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-status-calm" />
          </span>
          <h3 className="text-sm font-medium text-foreground">Ask the Guide</h3>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
          public · no login
        </span>
      </div>

      {/* Messages */}
      <div className="flex max-h-[360px] min-h-[260px] flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
        {empty ? (
          <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
            <span className="relative mb-4 flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-status-calm opacity-50" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-status-calm" />
            </span>
            <p className="text-sm text-muted-foreground">
              Ask anything about Vigil — what it does, how it predicts dropout, how it stays safe.
              Answers are grounded in approved public documents and cited; the Guide refuses anything
              it can&apos;t ground.
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void ask(s)}
                  className="rounded-full border-[0.5px] border-border bg-background px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-lg rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex flex-col gap-1.5">
                {m.refused && (
                  <span className="inline-flex w-fit items-center gap-1 rounded bg-status-watch/10 px-2 py-0.5 text-[11px] font-medium text-status-watch">
                    Refused by guardrail
                  </span>
                )}
                <div
                  className={cn(
                    "max-w-[95%] rounded-lg rounded-bl-sm border px-3 py-2 text-sm shadow-sm",
                    m.isError
                      ? "border-destructive/30 bg-destructive/5 text-foreground"
                      : "border-border bg-card text-foreground",
                    m.pending && "animate-pulse text-muted-foreground",
                  )}
                >
                  {m.content}
                  {m.citations && <CitationChips citations={m.citations} />}
                </div>
              </div>
            ),
          )
        )}
        <div ref={endRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border p-3">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Ask about Vigil…"
            className={cn(
              "min-h-[40px] max-h-[120px] flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2",
              "text-sm text-foreground placeholder:text-muted-foreground",
              "focus:outline-none focus:ring-1 focus:ring-ring",
            )}
          />
          <button
            type="button"
            onClick={() => void ask(input)}
            disabled={!input.trim() || sending}
            aria-label="Send"
            className={cn(
              "flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-primary-foreground",
              "transition-colors hover:bg-primary/90",
              "disabled:cursor-not-allowed disabled:opacity-40",
              "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
            )}
          >
            <Send className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
