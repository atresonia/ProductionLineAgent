import { useEffect, useRef, useState } from 'react'
import { ArrowDown, Activity } from 'lucide-react'
import TraceEntry from './TraceEntry.jsx'

function EmptyState({ status }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
      <div className="w-12 h-12 rounded-full border border-r-border flex items-center justify-center">
        <Activity size={20} className="text-r-muted" />
      </div>
      <div>
        <p className="font-mono text-[12px] text-r-dim mb-1">Monitoring — all services healthy</p>
        <p className="font-mono text-[10px] text-r-muted">
          Investigation trace will appear here when an incident fires.
        </p>
        <p className="font-mono text-[10px] text-r-muted mt-1">
          Use the <span className="text-r-amber">inject</span> menu to trigger a demo fault.
        </p>
      </div>
    </div>
  )
}

export default function InvestigationTrace({
  entries, status, approvalEntryId, onApprove, onReject
}) {
  const bottomRef  = useRef(null)
  const containerRef = useRef(null)
  const [userScrolled, setUserScrolled] = useState(false)
  const lastLen = useRef(0)

  // Auto-scroll on new entries unless user has scrolled up
  useEffect(() => {
    if (entries.length === lastLen.current) return
    lastLen.current = entries.length

    if (!userScrolled) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [entries, userScrolled])

  // Detect manual scroll
  const onScroll = () => {
    const el = containerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setUserScrolled(!atBottom)
  }

  const jumpToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    setUserScrolled(false)
  }

  return (
    <main className="flex-1 min-w-0 flex flex-col border-r border-r-border bg-r-bg overflow-hidden">

      {/* Panel header */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-r-border bg-r-panel flex-shrink-0">
        <span className="font-mono text-[9px] uppercase tracking-widest text-r-dim">
          Investigation Trace
        </span>
        {entries.length > 0 && (
          <span className="font-mono text-[9px] px-1.5 py-0.5 rounded
            bg-r-cyan-d border border-r-cyan/20 text-r-cyan ml-1">
            {entries.length} events
          </span>
        )}
        {status === 'investigating' && (
          <div className="flex items-center gap-1 ml-2">
            <div className="w-1.5 h-1.5 rounded-full bg-r-amber animate-dot-pulse" />
            <span className="font-mono text-[9px] text-r-amber">live</span>
          </div>
        )}
        {status === 'awaiting_approval' && (
          <div className="flex items-center gap-1 ml-2 px-2 py-0.5 rounded
            bg-r-amb-d border border-r-amber/30">
            <div className="w-1.5 h-1.5 rounded-full bg-r-amber animate-dot-pulse" />
            <span className="font-mono text-[9px] text-r-amber font-semibold">awaiting approval</span>
          </div>
        )}
        {status === 'resolved' && (
          <div className="ml-2 px-2 py-0.5 rounded bg-r-grn-d border border-r-green/20">
            <span className="font-mono text-[9px] text-r-green font-semibold">resolved</span>
          </div>
        )}
      </div>

      {/* Scrollable trace */}
      <div
        ref={containerRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto scrollbar-thin px-4 py-3 relative"
      >
        {entries.length === 0 ? (
          <EmptyState status={status} />
        ) : (
          <div className="flex flex-col gap-2 max-w-3xl mx-auto">
            {entries.map((entry) => (
              <div key={entry._id} className="animate-slide-up">
                <TraceEntry
                  entry={entry}
                  approvalEntryId={approvalEntryId}
                  onApprove={onApprove}
                  onReject={onReject}
                />
              </div>
            ))}
            <div ref={bottomRef} className="h-4" />
          </div>
        )}

        {/* Jump to latest */}
        {userScrolled && entries.length > 0 && (
          <button
            onClick={jumpToBottom}
            className="fixed bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-1.5
              px-3 py-1.5 rounded-full bg-r-surface border border-r-bright shadow-xl
              font-mono text-[10px] text-r-cyan hover:bg-r-raised transition-colors z-10"
          >
            <ArrowDown size={10} />
            jump to latest
          </button>
        )}
      </div>
    </main>
  )
}
