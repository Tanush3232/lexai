"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/stores/auth-store";
import { api } from "@/lib/api";
import { RelativeTime, formatRelative } from "@/lib/relative-time";

/* ─────────────────────────────────────────
   Types
───────────────────────────────────────── */
type TicketSummary = {
  id: string;
  ticket_number: string;   // "TKT-0001"
  title: string;
  status: string;
  priority: string;
  entity?: string;
  updated_at: string;
  created_at: string;
};


/* ─────────────────────────────────────────
   Search scoring — multi-field fuzzy rank
───────────────────────────────────────── */
function scoreMatch(ticket: TicketSummary, query: string): number {
  if (!query) return 1;
  const q = query.toLowerCase().trim();
  const title  = (ticket.title || "").toLowerCase();
  const num    = (ticket.ticket_number || "").toLowerCase();
  const entity = (ticket.entity || "").toLowerCase();
  const status = (ticket.status || "").toLowerCase();

  if (title === q || num === q) return 100;
  if (num.startsWith(q))        return 90;
  if (title.startsWith(q))      return 80;
  if (title.includes(q))        return 65;
  if (num.includes(q))          return 55;
  if (entity.includes(q))       return 45;
  if (status.includes(q))       return 35;

  const words = q.split(/\s+/).filter(Boolean);
  const matched = words.filter(w => title.includes(w) || num.includes(w));
  if (matched.length === words.length) return 70;
  if (matched.length > 0) return 25 + (matched.length / words.length) * 20;
  return 0;
}

function filterAndSort(tickets: TicketSummary[], query: string): TicketSummary[] {
  if (!query.trim()) return tickets;
  return tickets
    .map(t => ({ t, score: scoreMatch(t, query) }))
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score)
    .map(({ t }) => t);
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="tkt-highlight">{text.slice(idx, idx + query.length)}</mark>
      {text.slice(idx + query.length)}
    </>
  );
}

function SkeletonCard() {
  return (
    <div className="tkt-skeleton">
      <div className="tkt-skel-line" style={{ width: "30%" }} />
      <div className="tkt-skel-line" style={{ width: "70%" }} />
      <div className="tkt-skel-line" style={{ width: "50%" }} />
    </div>
  );
}

/* ─────────────────────────────────────────
   Main Page
───────────────────────────────────────── */
export default function TicketsPage() {
  const { user } = useAuthStore();
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [view, setView]       = useState("all");
  const [loading, setLoading] = useState(true);
  const [search, setSearch]   = useState("");
  const searchRef             = useRef<HTMLInputElement>(null);

  const isAdmin = user ? ["ops_admin", "super_admin", "reviewer"].includes(user.role) : false;

  // Admins default to "all", non-admins default to "assigned"
  useEffect(() => {
    if (user && !isAdmin && view === "all") setView("assigned");
  }, [user, isAdmin]);

  const fetchTickets = useCallback(async (v: string) => {
    setLoading(true);
    try {
      const res = await api.get(`/tickets?view=${v}&limit=200`);
      setTickets(res.data);
    } catch {
      setTickets([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTickets(view); }, [view]);

  const displayed = filterAndSort(tickets, search);

  // Tabs: admins see only "All Tickets" (all tickets are visible to them, no role split needed)
  // Non-admins see "Assigned to Me" and "Involved In" separately
  const tabs = isAdmin
    ? [{ key: "all", label: "All Tickets" }]
    : [
        { key: "assigned", label: "Assigned to Me" },
        { key: "involved", label: "Involved In"    },
      ];

  return (
    <div className="tkt-fullbleed">
      <div className="tkt-list-page">

        {/* ── Header ─────────────────────────────────────── */}
        <div className="tkt-list-header">
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
            <div>
              <h1>Legal Requests</h1>
              <p>Tickets are created via the SharePoint + Power Automate pipeline · Read-only from SharePoint, internal notes via the chat panel</p>
            </div>
            <div className="tkt-synced" style={{ marginTop: 4 }}>
              <div className="tkt-synced-dot" />
              SharePoint Sync
            </div>
          </div>

          {/* Search */}
          <div className="tkt-search-wrap">
            <svg className="tkt-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <input
              ref={searchRef}
              id="tkt-search"
              className="tkt-search-input"
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search tickets by subject, number, entity…"
              autoComplete="off"
            />
            {search && (
              <button className="tkt-search-clear" onClick={() => { setSearch(""); searchRef.current?.focus(); }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M18 6 6 18M6 6l12 12"/>
                </svg>
              </button>
            )}
          </div>
        </div>

        {/* ── Tabs ───────────────────────────────────────── */}
        <div className="tkt-tabs">
          {tabs.map(tab => (
            <button
              key={tab.key}
              className={`tkt-tab ${view === tab.key ? "active" : ""}`}
              onClick={() => { setView(tab.key); setSearch(""); }}
            >
              {tab.label}
              {view === tab.key && !loading && (
                <span className="tkt-tab-count">{displayed.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* ── List body ──────────────────────────────────── */}
        <div className="tkt-list-body">
          {loading ? (
            <>
              <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
            </>
          ) : displayed.length === 0 ? (
            <div className="tkt-empty">
              <div className="tkt-empty-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>
                  <path d="M14 2v4a2 2 0 0 0 2 2h4"/>
                  <path d="m9 15 2 2 4-4"/>
                </svg>
              </div>
              {search ? (
                <>
                  <p>No tickets match &ldquo;<strong>{search}</strong>&rdquo;</p>
                  <span>Try a different search term or clear the filter.</span>
                </>
              ) : (
                <>
                  <p>No tickets in this view.</p>
                  <span>
                    {view === "assigned"
                      ? "Tickets assigned to you by an admin will appear here."
                      : view === "involved"
                      ? "Tickets where you were in To, CC, or BCC will appear here automatically."
                      : "Tickets arrive automatically via the SharePoint pipeline."}
                  </span>
                </>
              )}
            </div>
          ) : (
            <>
              <div className="tkt-list-count">{displayed.length} ticket{displayed.length !== 1 ? "s" : ""}</div>
              {displayed.map(ticket => (
                <TicketCard key={ticket.id} ticket={ticket} query={search} />
              ))}
            </>
          )}
        </div>

      </div>
    </div>
  );
}

/* ─────────────────────────────────────────
   TicketCard
───────────────────────────────────────── */
function TicketCard({ ticket, query = "", selected = false }: { ticket: TicketSummary; query?: string; selected?: boolean }) {
  return (
    <Link href={`/dashboard/tickets/${ticket.id}`} className={`tkt-card ${selected ? "selected" : ""}`}>
      <div className="tkt-card-body">
        <div className="tkt-card-top">
          <span className="tkt-card-num">{ticket.ticket_number}</span>
          {ticket.entity && <span className="tkt-pill entity">{ticket.entity}</span>}
        </div>
        <div className="tkt-card-title">
          <Highlight text={ticket.title} query={query} />
        </div>
        <div className="tkt-card-footer">
          <div className="tkt-card-pills">
            <span className={`tkt-pill ${ticket.status}`}>{ticket.status.replace("_", " ")}</span>
            <span className={`tkt-pill ${ticket.priority}`}>{ticket.priority}</span>
          </div>
          <RelativeTime iso={ticket.updated_at} className="tkt-card-time" />
        </div>
      </div>
      {/* Chevron */}
      <div style={{ display: "flex", alignItems: "center", flexShrink: 0, color: "var(--text3)" }}>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="m9 18 6-6-6-6"/>
        </svg>
      </div>
    </Link>
  );
}
