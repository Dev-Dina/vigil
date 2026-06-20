"use client"

import * as React from "react"
import { X, Send, MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"
import { openConversation, postMessage, pollJob } from "@/lib/api"

interface ToolCall {
  text: string
  status: "running" | "complete"
}

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  // Honest states: a blocked turn renders its refusal content; an error is never a fake answer.
  blocked?: boolean
  isError?: boolean
  toolCalls?: ToolCall[]
  timestamp?: string
  // The routed agent (retention/report/operations) that handled this turn; null/undefined for a
  // refusal (no agent dispatched). Read straight off the AssistantTurn response — no client logic.
  agent?: string | null
}

// Honest label for the routed agent; falls back to the raw name for any future agent.
const AGENT_LABELS: Record<string, string> = {
  retention: "Retention agent",
  report: "Report agent",
  operations: "Operations agent",
}

function AgentBadge({ agent }: { agent: string }) {
  const label = AGENT_LABELS[agent] ?? `${agent} agent`
  return (
    <span className="inline-flex w-fit items-center gap-1.5 rounded-full border-[0.5px] border-status-calm/30 bg-status-calm/10 px-2 py-0.5 text-[11px] font-medium text-status-calm">
      <span className="h-1.5 w-1.5 rounded-full bg-status-calm" />
      {label}
    </span>
  )
}

function ToolCallChip({ tool }: { tool: ToolCall }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-background text-[11px] text-muted-foreground font-mono",
        tool.status === "running" && "animate-pulse"
      )}
    >
      <span
        className={cn(
          "w-1.5 h-1.5 rounded-full",
          tool.status === "running" ? "bg-status-watch" : "bg-status-calm"
        )}
      />
      {tool.text}
    </span>
  )
}

function UserMessage({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="min-w-0 max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-primary px-3.5 py-2.5 text-sm leading-relaxed text-primary-foreground">
        {content}
      </div>
    </div>
  )
}

function AssistantMessage({
  content,
  blocked,
  isError,
  toolCalls,
  agent,
}: {
  content: string
  blocked?: boolean
  isError?: boolean
  toolCalls?: ToolCall[]
  agent?: string | null
}) {
  // Parse content to render monospace numbers
  const renderContent = (text: string) => {
    // Match numbers including decimals and percentages
    const parts = text.split(/(\b\d+(?:\.\d+)?%?|\b(?:P-|ID:?\s*)\d+\b)/g)
    return parts.map((part, i) => {
      if (/^\d+(?:\.\d+)?%?$/.test(part) || /^(?:P-|ID:?\s*)\d+$/.test(part)) {
        return (
          <span key={i} className="font-mono text-foreground">
            {part}
          </span>
        )
      }
      return part
    })
  }

  return (
    <div className="flex flex-col gap-1.5">
      {/* Which agent handled the turn — shown for an answered turn (no agent on a refusal). */}
      {!blocked && agent && <AgentBadge agent={agent} />}
      {blocked && (
        <span className="inline-flex w-fit items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-status-watch/10 text-status-watch">
          Refused by guardrail
        </span>
      )}
      {toolCalls && toolCalls.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-1">
          {toolCalls.map((tool, i) => (
            <ToolCallChip key={i} tool={tool} />
          ))}
        </div>
      )}
      {content && (
        <div
          className={cn(
            "min-w-0 max-w-[92%] whitespace-pre-wrap break-words rounded-2xl rounded-bl-md border px-3.5 py-2.5 text-sm leading-relaxed shadow-sm",
            isError
              ? "border-status-risk/30 bg-status-risk/5 text-foreground"
              : "border-border bg-card text-foreground",
          )}
        >
          {renderContent(content)}
        </div>
      )}
    </div>
  )
}

interface AssistantPanelProps {
  className?: string
}

// Poll cadence + cap for the async turn (api.md: POST 202 -> poll GET /assistant/jobs/{id}).
const POLL_INTERVAL_MS = 600
const MAX_POLLS = 60 // ~36s before surfacing an honest timeout error

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function AssistantPanel({ className }: AssistantPanelProps) {
  const [isOpen, setIsOpen] = React.useState(false)
  const [messages, setMessages] = React.useState<Message[]>([])
  const [input, setInput] = React.useState("")
  const [sending, setSending] = React.useState(false)
  // Conversation id is opened lazily on first send and reused thereafter.
  const conversationIdRef = React.useRef<string | null>(null)
  const messagesEndRef = React.useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  React.useEffect(() => {
    if (isOpen) {
      scrollToBottom()
    }
  }, [isOpen, messages])

  // Async flow per api.md: POST a message -> 202 JobAccepted, then poll
  // GET /assistant/jobs/{job_id} until an AssistantTurn (the agent never runs inline).
  const handleSend = async () => {
    const content = input.trim()
    if (!content || sending) return
    setSending(true)
    setInput("")

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content,
      timestamp: new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    }
    // Placeholder assistant bubble showing the job is in flight.
    const pendingId = `pending-${Date.now()}`
    const pendingMessage: Message = {
      id: pendingId,
      role: "assistant",
      toolCalls: [{ text: "working…", status: "running" }],
      content: "",
    }
    setMessages((prev) => [...prev, userMessage, pendingMessage])

    try {
      // Reuse the conversation across turns; open one lazily on the first send.
      let conversationId = conversationIdRef.current
      if (!conversationId) {
        const conv = await openConversation()
        conversationId = conv.conversation_id
        conversationIdRef.current = conversationId
      }

      const job = await postMessage(conversationId, { content })

      // Poll until the turn completes (or we hit the cap → honest timeout).
      let turn = await pollJob(job.job_id, conversationId)
      let polls = 0
      while ("status" in turn) {
        if (polls >= MAX_POLLS) {
          throw new Error("assistant turn timed out")
        }
        await sleep(POLL_INTERVAL_MS)
        turn = await pollJob(job.job_id, conversationId)
        polls += 1
      }

      const finalTurn = turn // narrowed to AssistantTurn
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? {
                ...m,
                content: finalTurn.content,
                blocked: finalTurn.guardrail_decision === "blocked",
                agent: finalTurn.agent ?? null,
                toolCalls: undefined,
                timestamp: new Date(finalTurn.created_at).toLocaleTimeString([], {
                  hour: "numeric",
                  minute: "2-digit",
                }),
              }
            : m,
        ),
      )
    } catch {
      // Honest error state — never a fabricated answer.
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? {
                ...m,
                content: "Couldn't reach the assistant. Please try again.",
                isError: true,
                toolCalls: undefined,
              }
            : m,
        ),
      )
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      void handleSend()
    }
  }

  return (
    <>
      {/* Launcher Button */}
      <button
        onClick={() => setIsOpen(true)}
        className={cn(
          "fixed bottom-6 right-6 z-40 w-12 h-12 rounded-full bg-primary text-primary-foreground shadow-lg",
          "flex items-center justify-center",
          "hover:scale-105 active:scale-95 transition-transform duration-150",
          "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
          isOpen && "hidden",
          className
        )}
        aria-label="Open assistant"
      >
        <MessageSquare className="w-5 h-5" />
      </button>

      {/* Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-foreground/5 backdrop-blur-[1px] transition-opacity"
          onClick={() => setIsOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Panel */}
      <div
        className={cn(
          "fixed top-0 right-0 z-50 h-full w-[440px] max-w-full bg-card border-l border-border shadow-xl",
          "flex flex-col",
          "transition-transform duration-300 ease-out",
          isOpen ? "translate-x-0" : "translate-x-full"
        )}
        role="dialog"
        aria-label="Vigil Assistant"
        aria-hidden={!isOpen}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-card">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-status-calm" />
            <h2 className="text-sm font-medium text-foreground">Vigil Assistant</h2>
          </div>
          <button
            onClick={() => setIsOpen(false)}
            className="w-8 h-8 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            aria-label="Close assistant"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 bg-background">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center px-6 text-center">
              <span className="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/[0.05] text-foreground">
                <MessageSquare className="h-5 w-5" strokeWidth={1.75} />
              </span>
              <h3 className="text-sm font-semibold text-foreground">Ask the Vigil assistant</h3>
              <p className="mt-1.5 max-w-[16rem] text-sm leading-relaxed text-muted-foreground">
                Questions about participants, reports, or operations — within your scope. Answers are
                grounded and cited; it refuses anything it can&apos;t ground.
              </p>
            </div>
          ) : (
            messages.map((message) => (
              <div key={message.id}>
                {message.role === "user" ? (
                  <UserMessage content={message.content} />
                ) : (
                  <AssistantMessage
                    content={message.content}
                    blocked={message.blocked}
                    isError={message.isError}
                    toolCalls={message.toolCalls}
                    agent={message.agent}
                  />
                )}
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="p-4 border-t border-border bg-card">
          <div className="flex items-end gap-2">
            <div className="flex-1 relative">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about participants, reports, or operations..."
                className={cn(
                  "w-full min-h-[64px] max-h-[120px] px-3 py-2.5 pr-10",
                  "bg-background border border-border rounded-lg",
                  "text-sm text-foreground placeholder:text-muted-foreground",
                  "focus:outline-none focus:ring-1 focus:ring-ring",
                  "resize-none"
                )}
                rows={2}
              />
            </div>
            <button
              onClick={() => void handleSend()}
              disabled={!input.trim() || sending}
              className={cn(
                "w-10 h-10 flex items-center justify-center rounded-lg",
                "bg-primary text-primary-foreground",
                "hover:bg-primary/90 transition-colors",
                "disabled:opacity-40 disabled:cursor-not-allowed",
                "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
              )}
              aria-label="Send message"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
          <p className="text-[11px] text-muted-foreground mt-2 text-center">
            Press Enter to send, Shift+Enter for new line
          </p>
        </div>
      </div>
    </>
  )
}
