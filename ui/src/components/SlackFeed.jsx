import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { Hash } from 'lucide-react'

const slackMdComponents = {
  h2: ({ children }) => (
    <div className="font-mono text-[11px] font-semibold text-r-text mb-0.5">{children}</div>
  ),
  h3: ({ children }) => (
    <div className="font-mono text-[11px] font-semibold text-r-text/80 mb-0.5">{children}</div>
  ),
  p: ({ children }) => (
    <div className="font-mono text-[10px] text-r-dim leading-relaxed">{children}</div>
  ),
  strong: ({ children }) => <span className="font-semibold text-r-text">{children}</span>,
  code: ({ children }) => (
    <code className="font-mono bg-white/[.08] px-1 py-px rounded text-[10px]">{children}</code>
  ),
  ul: ({ children }) => <ul className="ml-3 flex flex-col gap-0.5">{children}</ul>,
  li: ({ children }) => (
    <div className="flex gap-1.5">
      <span className="text-r-dim flex-shrink-0">•</span>
      <span className="font-mono text-[10px] text-r-dim">{children}</span>
    </div>
  ),
  hr: () => <hr className="border-0 border-t border-r-border/40 my-1" />,
}

const RESOLVE_AVATAR = (
  <div className="w-7 h-7 rounded flex items-center justify-center flex-shrink-0
    bg-gradient-to-br from-r-cyan to-blue-600 text-[9px] font-mono font-bold text-black">
    R
  </div>
)

const SEVERITY_STYLES = {
  critical: {
    bar:    'bg-r-red',
    label:  'bg-r-red-d border-r-red/20 text-r-red',
    border: 'border-r-red/30',
  },
  resolved: {
    bar:    'bg-r-green',
    label:  'bg-r-grn-d border-r-green/20 text-r-green',
    border: 'border-r-green/30',
  },
  warning: {
    bar:    'bg-r-amber',
    label:  'bg-r-amb-d border-r-amber/20 text-r-amber',
    border: 'border-r-amber/30',
  },
  info: {
    bar:    'bg-r-dim',
    label:  'bg-r-muted border-r-border text-r-dim',
    border: 'border-r-border',
  },
}

function SlackMessage({ msg }) {
  const sev = SEVERITY_STYLES[msg.severity] || SEVERITY_STYLES.info

  return (
    <div className="flex gap-2 p-3 hover:bg-r-raised/50 transition-colors group">
      {RESOLVE_AVATAR}
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-1.5 mb-1">
          <span className="font-mono text-[11px] font-semibold text-r-cyan">Resolve</span>
          <span className="font-mono text-[9px] text-r-muted">{msg.ts}</span>
        </div>

        {/* Slack-style attachment card */}
        <div className={`flex rounded overflow-hidden border ${sev.border}`}>
          {/* Colored sidebar bar */}
          <div className={`w-1 flex-shrink-0 ${sev.bar}`} />

          <div className="flex-1 p-2 bg-r-raised">
            {/* Severity badge */}
            <div className="flex items-center gap-1.5 mb-1.5">
              <span className={`font-mono text-[9px] font-semibold px-1.5 py-0.5 rounded border ${sev.label}`}>
                {(msg.severity || 'info').toUpperCase()}
              </span>
            </div>

            {/* Message lines */}
            <ReactMarkdown components={slackMdComponents}>{msg.message || ''}</ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  )
}

function EmptySlack() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-2 px-6 text-center">
      <Hash size={16} className="text-r-muted" />
      <p className="font-mono text-[10px] text-r-muted">No messages yet</p>
      <p className="font-mono text-[9px] text-r-muted opacity-60">
        Resolve will post here when an incident fires
      </p>
    </div>
  )
}

export default function SlackFeed({ messages }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <aside className="w-[260px] flex-shrink-0 flex flex-col bg-r-panel overflow-hidden">

      {/* Header */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-r-border flex-shrink-0">
        <Hash size={11} className="text-r-dim" />
        <span className="font-mono text-[10px] text-r-dim">incidents</span>
        {messages.length > 0 && (
          <span className="ml-auto font-mono text-[9px] px-1.5 py-0.5 rounded
            bg-r-muted border border-r-border text-r-dim">
            {messages.length}
          </span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto scrollbar-thin divide-y divide-r-border/30">
        {messages.length === 0
          ? <EmptySlack />
          : messages.map(m => <SlackMessage key={m._id} msg={m} />)
        }
        <div ref={bottomRef} />
      </div>

      {/* Footer bar — mimics Slack input */}
      <div className="px-3 py-2 border-t border-r-border flex-shrink-0">
        <div className="flex items-center gap-1.5 px-2 py-1.5 rounded
          border border-r-border bg-r-raised">
          <span className="font-mono text-[9px] text-r-muted">Message #incidents</span>
        </div>
      </div>
    </aside>
  )
}
