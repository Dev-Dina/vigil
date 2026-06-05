"use client"

import * as React from "react"
import { X, Send, MessageSquare, Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"
import { openConversation, postMessage, pollJob } from "@/lib/stubs"

type AgentType = "retention" | "report" | "operations"

interface ToolCall {
  text: string
  status: "running" | "complete"
}

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  agent?: AgentType
  toolCalls?: ToolCall[]
  timestamp?: string
}

const agentConfig: Record<AgentType, { label: string; color: string }> = {
  retention: { label: "Retention agent", color: "bg-status-calm/10 text-status-calm" },
  report: { label: "Report agent", color: "bg-status-watch/10 text-status-watch" },
  operations: { label: "Operations agent", color: "bg-[#15323A]/10 text-foreground" },
}

function AgentChip({ agent }: { agent: AgentType }) {
  const config = agentConfig[agent]
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium",
        config.color
      )}
    >
      <Sparkles className="w-3 h-3" />
      {config.label}
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
      <div className="max-w-[85%] bg-primary text-primary-foreground px-3 py-2 rounded-lg rounded-br-sm text-sm">
        {content}
      </div>
    </div>
  )
}

function AssistantMessage({
  content,
  agent,
  toolCalls,
}: {
  content: string
  agent?: AgentType
  toolCalls?: ToolCall[]
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
      {agent && <AgentChip agent={agent} />}
      {toolCalls && toolCalls.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-1">
          {toolCalls.map((tool, i) => (
            <ToolCallChip key={i} tool={tool} />
          ))}
        </div>
      )}
      <div className="max-w-[95%] bg-card border border-border px-3 py-2 rounded-lg rounded-bl-sm text-sm text-foreground shadow-sm">
        {renderContent(content)}
      </div>
    </div>
  )
}

const sampleConversation: Message[] = [
  {
    id: "1",
    role: "user",
    content: "Why is participant P-0412 flagged as high risk?",
    timestamp: "2:34 PM",
  },
  {
    id: "2",
    role: "assistant",
    agent: "retention",
    toolCalls: [
      { text: "pulling participant P-0412", status: "complete" },
      { text: "analyzing risk factors", status: "complete" },
    ],
    content:
      "Participant P-0412 has a risk score of 78%, placing them in the at-risk category. The primary concerns are: they missed their last 2 scheduled check-ins, reported increased side effects during week 6, and their engagement score dropped from 85% to 42% over the past 14 days. Travel distance to the site (47 miles) may also be a contributing factor.",
    timestamp: "2:34 PM",
  },
  {
    id: "3",
    role: "user",
    content: "Can you draft a summary report of at-risk participants this week?",
    timestamp: "2:35 PM",
  },
  {
    id: "4",
    role: "assistant",
    agent: "report",
    toolCalls: [
      { text: "querying at-risk cohort", status: "complete" },
      { text: "drafting summary", status: "complete" },
    ],
    content:
      "Weekly At-Risk Summary (Week 24): 12 participants are currently flagged at-risk, up from 8 last week. Top reasons: missed appointments (5), reported side effects (4), and declining engagement (3). Recommended actions: schedule outreach calls for 7 participants, consider protocol adjustment review for 2. Average risk score for flagged group: 71%.",
    timestamp: "2:36 PM",
  },
]

interface AssistantPanelProps {
  className?: string
}

// Poll interval for the stubbed job poller (ms).
const POLL_INTERVAL_MS = 600

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function AssistantPanel({ className }: AssistantPanelProps) {
  const [isOpen, setIsOpen] = React.useState(false)
  const [messages, setMessages] = React.useState<Message[]>(sampleConversation)
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

  // Async flow per api.md: POST message -> 202 JobAccepted, then poll
  // GET /assistant/jobs/{job_id} until an AssistantTurn (never inline).
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
      agent: "retention",
      toolCalls: [{ text: "enqueuing request", status: "running" }],
      content: "",
    }
    setMessages((prev) => [...prev, userMessage, pendingMessage])

    try {
      // TODO(phase4/5): wire to POST /assistant/conversations
      if (!conversationIdRef.current) {
        const conv = await openConversation()
        conversationIdRef.current = conv.conversation_id
      }
      const conversationId = conversationIdRef.current

      // TODO(phase4/5): wire to POST /assistant/conversations/{conversation_id}/messages (202)
      const job = await postMessage(conversationId, { content })

      // TODO(phase4/5): wire to GET /assistant/jobs/{job_id} — poll until AssistantTurn
      let turn = await pollJob(job.job_id, conversationId)
      while ("status" in turn) {
        await sleep(POLL_INTERVAL_MS)
        turn = await pollJob(job.job_id, conversationId)
      }

      const finalTurn = turn // narrowed to AssistantTurn
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? {
                ...m,
                content: finalTurn.content,
                toolCalls: [{ text: "answer ready", status: "complete" }],
                timestamp: new Date(finalTurn.created_at).toLocaleTimeString([], {
                  hour: "numeric",
                  minute: "2-digit",
                }),
              }
            : m,
        ),
      )
    } catch {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? { ...m, content: "Something went wrong. Please try again.", toolCalls: [] }
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
          "fixed top-0 right-0 z-50 h-full w-[400px] max-w-full bg-card border-l border-border shadow-xl",
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
          {messages.map((message) => (
            <div key={message.id}>
              {message.role === "user" ? (
                <UserMessage content={message.content} />
              ) : (
                <AssistantMessage
                  content={message.content}
                  agent={message.agent}
                  toolCalls={message.toolCalls}
                />
              )}
            </div>
          ))}
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
                  "w-full min-h-[44px] max-h-[120px] px-3 py-2.5 pr-10",
                  "bg-background border border-border rounded-lg",
                  "text-sm text-foreground placeholder:text-muted-foreground",
                  "focus:outline-none focus:ring-1 focus:ring-ring",
                  "resize-none"
                )}
                rows={1}
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
