"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { useAuthStore } from "@/lib/stores/auth-store";
import { api } from "@/lib/api";
import { RelativeTime, formatRelative, formatAbsolute } from "@/lib/relative-time";

/* ═══════════════════════════════════════════════
   Types
═══════════════════════════════════════════════ */
type TicketSummary = {
  id: string;
  ticket_number: string;
  title: string;
  status: string;
  priority: string;
  entity?: string;
  updated_at: string;
  created_at: string;
};

type Participant = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  assigned_at: string;
};

type MessageDetail = {
  id: string;
  content: string;
  source: string;           // "sharepoint" | "app"
  timestamp: string;
  sender_id: string;
  sender_name: string;
  sender_email?: string;
  event_id?: string;
  message_id?: string;
  direction?: string;       // "inbound" | "outbound"
  has_attachment?: boolean;
  attachment_names?: string;
  attachment_links?: string;
  to_emails?: string;
  cc_emails?: string;
  bcc_emails?: string;
};

type AttachmentDetail = {
  id: string;
  file_name: string;
  file_url: string;
  entity?: string;
  created_at: string;
  event_id?: string;
  document_id?: string;
  modified_by?: string;
  sp_modified_at?: string;
};

type TicketDetails = {
  ticket: TicketSummary & { conversation_id?: string };
  participants: Participant[];
  messages: MessageDetail[];
  attachments: AttachmentDetail[];
};

type MentionUser = { id: string; full_name: string; email: string };



function splitPipe(val?: string): string[] {
  if (!val) return [];
  return val.split("|").map(s => s.trim()).filter(Boolean);
}

/* ─── Search scoring (for left list panel) ──── */
function scoreMatch(ticket: TicketSummary, query: string): number {
  if (!query.trim()) return 1;
  const q = query.toLowerCase();
  const title  = (ticket.title || "").toLowerCase();
  const num    = (ticket.ticket_number || "").toLowerCase();
  const entity = (ticket.entity || "").toLowerCase();
  if (title === q || num === q) return 100;
  if (num.startsWith(q))       return 90;
  if (title.startsWith(q))     return 80;
  if (title.includes(q))       return 65;
  if (num.includes(q))         return 55;
  if (entity.includes(q))      return 45;
  const words = q.split(/\s+/).filter(Boolean);
  const matched = words.filter(w => title.includes(w) || num.includes(w));
  if (matched.length === words.length) return 70;
  if (matched.length > 0) return 25 + (matched.length / words.length) * 20;
  return 0;
}

/* ═══════════════════════════════════════════════
   Left panel — mini ticket list
═══════════════════════════════════════════════ */
function TicketListPanel({
  currentId,
  user,
}: {
  currentId: string;
  user: { role: string } | null;
}) {
  const [tickets, setTickets]   = useState<TicketSummary[]>([]);
  const [view, setView]         = useState("all");
  const [search, setSearch]     = useState("");
  const [loading, setLoading]   = useState(true);
  const isAdmin = user ? ["ops_admin", "super_admin", "reviewer"].includes(user.role) : false;

  useEffect(() => {
    if (user && !isAdmin && view === "all") setView("involved");
  }, [user, isAdmin]);

  useEffect(() => {
    setLoading(true);
    api.get(`/tickets?view=${view}&limit=200`)
      .then(r => setTickets(r.data))
      .catch(() => setTickets([]))
      .finally(() => setLoading(false));
  }, [view]);

  const tabs = [
    ...(isAdmin ? [{ k: "all",      l: "All"      }] : []),
    { k: "assigned", l: "Mine"     },
    { k: "involved", l: "Involved" },
  ];

  const displayed = search.trim()
    ? tickets.map(t => ({ t, s: scoreMatch(t, search) })).filter(x => x.s > 0).sort((a, b) => b.s - a.s).map(x => x.t)
    : tickets;

  return (
    <>
      <div className="tkt-panel-list-header">
        <span className="tkt-panel-list-title">Legal Requests</span>
        {/* Mini search */}
        <div className="tkt-search-wrap" style={{ maxWidth: "100%", marginTop: 0 }}>
          <svg className="tkt-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
          </svg>
          <input
            className="tkt-search-input"
            placeholder="Search…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            autoComplete="off"
          />
          {search && (
            <button className="tkt-search-clear" onClick={() => setSearch("")}>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M18 6 6 18M6 6l12 12"/>
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="tkt-panel-list-tabs">
        {tabs.map(tab => (
          <button key={tab.k} className={`tkt-panel-tab ${view === tab.k ? "active" : ""}`} onClick={() => setView(tab.k)}>
            {tab.l}
          </button>
        ))}
      </div>

      {/* Ticket list */}
      <div className="tkt-panel-list-body">
        {loading ? (
          [1,2,3,4].map(i => (
            <div key={i} className="tkt-skeleton">
              <div className="tkt-skel-line" style={{ width: "35%" }} />
              <div className="tkt-skel-line" style={{ width: "75%" }} />
              <div className="tkt-skel-line" style={{ width: "45%" }} />
            </div>
          ))
        ) : displayed.length === 0 ? (
          <div style={{ textAlign: "center", padding: "24px 8px", color: "var(--text3)", fontSize: 11 }}>
            No tickets found.
          </div>
        ) : (
          displayed.map(t => (
            <Link
              key={t.id}
              href={`/dashboard/tickets/${t.id}`}
              className={`tkt-card ${t.id === currentId ? "selected" : ""}`}
              style={{ marginBottom: 6 }}
            >
              <div className="tkt-card-body">
                <div className="tkt-card-top">
                  <span className="tkt-card-num">{t.ticket_number}</span>
                  {t.entity && <span className="tkt-pill entity">{t.entity}</span>}
                </div>
                <div className="tkt-card-title" style={{ fontSize: 12, WebkitLineClamp: 1 }}>{t.title}</div>
                <div className="tkt-card-footer">
                  <div className="tkt-card-pills">
                    <span className={`tkt-pill ${t.status}`}>{t.status.replace("_"," ")}</span>
                  </div>
                  <RelativeTime iso={t.updated_at} className="tkt-card-time" />
                </div>
              </div>
            </Link>
          ))
        )}
      </div>
    </>
  );
}

/* ═══════════════════════════════════════════════
   EventCard — single email / event in timeline
═══════════════════════════════════════════════ */
function EventCard({ msg, currentUserId }: { msg: MessageDetail; currentUserId: string }) {
  const [open, setOpen] = useState(false);
  const isSharePoint = msg.source === "sharepoint";
  const direction = (msg.direction || "").toLowerCase();
  const cardClass = isSharePoint
    ? (direction === "outbound" ? "outbound" : "inbound")
    : "internal";

  const names   = splitPipe(msg.attachment_names);
  const links   = splitPipe(msg.attachment_links);
  const toList  = splitPipe(msg.to_emails);
  const ccList  = splitPipe(msg.cc_emails);
  const bccList = splitPipe(msg.bcc_emails);
  const preview = msg.content ? msg.content.slice(0, 80) + (msg.content.length > 80 ? "…" : "") : "";

  return (
    <div className={`tkt-event-card ${cardClass}`}>
      {/* Header (always visible) */}
      <div className="tkt-event-hdr" onClick={() => setOpen(o => !o)}>
        <div className="tkt-event-hdr-l">
          {isSharePoint ? (
            <span className={`tkt-dir-pill ${direction || "inbound"}`}>
              {direction === "outbound" ? (
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M5 12h14M12 5l7 7-7 7"/>
                </svg>
              ) : (
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M19 12H5M12 19l-7-7 7-7"/>
                </svg>
              )}
              {direction || "inbound"}
            </span>
          ) : (
            <span className="tkt-dir-pill app">Internal</span>
          )}

          <span className="tkt-event-sender">
            {msg.sender_email || msg.sender_name}
          </span>

          {!open && <span className="tkt-event-preview">{preview}</span>}

          {msg.has_attachment && (
            <span className="tkt-attach-indicator">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 10, height: 10 }}>
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
              </svg>
              {names.length || 1}
            </span>
          )}
        </div>

        <div className="tkt-event-hdr-r">
          <span className="tkt-event-time">{formatAbsolute(msg.timestamp)}</span>
          <svg className={`tkt-event-chevron ${open ? "open" : ""}`} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="m6 9 6 6 6-6"/>
          </svg>
        </div>
      </div>

      {/* Expanded body */}
      {open && (
        <div className="tkt-event-body-wrap">
          {/* To / CC / BCC */}
          {(toList.length > 0 || ccList.length > 0 || bccList.length > 0) && (
            <div className="tkt-event-recips">
              {msg.sender_email && (
                <div className="tkt-recip-row">
                  <span className="tkt-recip-key">From</span>
                  <span className="tkt-recip-val">{msg.sender_email}</span>
                </div>
              )}
              {toList.length > 0 && (
                <div className="tkt-recip-row">
                  <span className="tkt-recip-key">To</span>
                  <span className="tkt-recip-val">{toList.join(", ")}</span>
                </div>
              )}
              {ccList.length > 0 && (
                <div className="tkt-recip-row">
                  <span className="tkt-recip-key">CC</span>
                  <span className="tkt-recip-val">{ccList.join(", ")}</span>
                </div>
              )}
              {bccList.length > 0 && (
                <div className="tkt-recip-row">
                  <span className="tkt-recip-key">BCC</span>
                  <span className="tkt-recip-val">{bccList.join(", ")}</span>
                </div>
              )}
            </div>
          )}

          {/* Body */}
          <div className="tkt-event-content">{msg.content || "(No body)"}</div>

          {/* Attachments */}
          {names.length > 0 && (
            <div className="tkt-event-atts">
              {names.map((name, i) => (
                links[i] ? (
                  <a key={i} className="tkt-att-chip" href={links[i]} target="_blank" rel="noopener noreferrer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                    </svg>
                    {name}
                  </a>
                ) : (
                  <span key={i} className="tkt-att-chip">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                    </svg>
                    {name}
                  </span>
                )
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════
   Internal Discussion (chat)
═══════════════════════════════════════════════ */
function InternalChat({
  ticketId,
  messages,
  currentUserId,
  currentUserName,
  onRefresh,
}: {
  ticketId: string;
  messages: MessageDetail[];
  currentUserId: string;
  currentUserName: string;
  onRefresh: () => void;
}) {
  const [expanded, setExpanded]         = useState(true);
  const [text, setText]                 = useState("");
  const [mentionUsers, setMentionUsers] = useState<MentionUser[]>([]);
  const [showMention, setShowMention]   = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const [sending, setSending]           = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const internalMessages = messages.filter(m => m.source === "app");

  useEffect(() => {
    if (expanded) chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [internalMessages.length, expanded]);

  const loadMentionUsers = async () => {
    if (mentionUsers.length > 0) return;
    const res = await api.get(`/tickets/${ticketId}/mentionable_users`);
    setMentionUsers(res.data);
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    const lastWord = val.split(/\s/).pop() || "";
    if (lastWord.startsWith("@")) {
      setMentionFilter(lastWord.slice(1).toLowerCase());
      setShowMention(true);
      loadMentionUsers();
    } else {
      setShowMention(false);
    }
  };

  const insertMention = async (u: MentionUser) => {
    try {
      await api.post(`/tickets/${ticketId}/mentions`, { user_id: u.id });
      const words = text.split(/\s/);
      words.pop();
      setText(words.join(" ") + ` @${u.full_name} `);
    } catch (err: any) {
      alert(err.response?.data?.detail || "Cannot mention this user.");
    }
    setShowMention(false);
  };

  const send = async () => {
    const content = text.trim();
    if (!content || sending) return;
    setSending(true);
    setText("");
    try {
      await api.post(`/tickets/${ticketId}/messages`, { content });
      onRefresh();
    } catch {
      setText(content);
    } finally {
      setSending(false);
    }
  };

  const filteredMentions = mentionUsers.filter(u =>
    u.full_name.toLowerCase().includes(mentionFilter) ||
    u.email.toLowerCase().includes(mentionFilter)
  );

  return (
    <div className={`tkt-internal-panel ${expanded ? "expanded" : "collapsed"}`}>
      {/* Header */}
      <div className="tkt-internal-hdr" onClick={() => setExpanded(e => !e)}>
        <div className="tkt-internal-hdr-l">
          <div className="tkt-internal-dot" />
          <span className="tkt-internal-title">
            Internal Discussion
            {internalMessages.length > 0 && ` · ${internalMessages.length}`}
          </span>
        </div>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          style={{ color: "var(--text3)", transform: expanded ? "rotate(180deg)" : "none", transition: "transform .18s" }}>
          <path d="m6 9 6 6 6-6"/>
        </svg>
      </div>

      {expanded && (
        <>
          {/* Messages */}
          <div className="tkt-chat-body">
            {internalMessages.length === 0 ? (
              <div style={{ textAlign: "center", color: "var(--text3)", fontSize: 11, padding: "12px 0" }}>
                No internal notes yet. Type below to add one.
              </div>
            ) : (
              internalMessages.map(m => {
                const isMe = m.sender_id === currentUserId;
                return (
                  <div key={m.id} className={`tkt-chat-msg ${isMe ? "mine" : "theirs"}`}>
                    <div className="tkt-chat-meta">
                      {isMe ? "You" : m.sender_name} · {formatAbsolute(m.timestamp)}
                    </div>
                    <div className={`tkt-bubble ${isMe ? "mine" : "theirs"}`}>
                      {m.content}
                    </div>
                  </div>
                );
              })
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Input */}
          <div className="tkt-chat-input-row">
            {showMention && filteredMentions.length > 0 && (
              <div className="tkt-mention-menu">
                {filteredMentions.map(u => (
                  <div key={u.id} className="tkt-mention-item" onClick={() => insertMention(u)}>
                    <strong>{u.full_name}</strong>
                    <span>{u.email}</span>
                  </div>
                ))}
              </div>
            )}

            <textarea
              className="tkt-chat-input"
              value={text}
              onChange={handleChange}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder="Type an internal note… Use @ to mention a colleague"
              rows={1}
            />
            <button className="tkt-send-btn" onClick={send} disabled={!text.trim() || sending}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>
              </svg>
              Post
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════
   Main Page
═══════════════════════════════════════════════ */
export default function TicketDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuthStore();

  const [data, setData]     = useState<TicketDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [allUsers, setAllUsers] = useState<MentionUser[]>([]);

  const isAdmin = user ? ["ops_admin", "super_admin", "reviewer"].includes(user.role) : false;

  const fetchTicket = useCallback(async () => {
    try {
      const res = await api.get(`/tickets/${id}`);
      setData(res.data);
    } catch (err: any) {
      if (err.response?.status === 403) {
        alert("You do not have access to this ticket.");
        router.push("/dashboard/tickets");
      }
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchTicket();
    const interval = setInterval(fetchTicket, 8000);   // poll for updates
    return () => clearInterval(interval);
  }, [fetchTicket]);

  const loadAllUsers = useCallback(async () => {
    if (allUsers.length > 0) return;
    const res = await api.get(`/tickets/${id}/mentionable_users`);
    setAllUsers(res.data);
  }, [id, allUsers.length]);

  const handleStatusChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    await api.patch(`/tickets/${id}`, { status: e.target.value });
    fetchTicket();
  };

  const handlePriorityChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    await api.patch(`/tickets/${id}`, { priority: e.target.value });
    fetchTicket();
  };

  const handleAssign = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const uid = e.target.value;
    if (!uid) return;
    try {
      await api.post(`/tickets/${id}/assign`, { user_id: uid });
      fetchTicket();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to assign.");
    }
    e.target.value = "";
  };

  if (loading || !user) {
    return (
      <div className="tkt-fullbleed">
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", fontSize: 13 }}>
          Loading ticket…
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="tkt-fullbleed">
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", fontSize: 13 }}>
          Ticket not found.{" "}
          <Link href="/dashboard/tickets" style={{ color: "var(--accent)", marginLeft: 8 }}>Go back</Link>
        </div>
      </div>
    );
  }

  const { ticket, participants, messages, attachments } = data;

  // Separate email events (from SharePoint) from internal notes
  const emailEvents   = messages.filter(m => m.source === "sharepoint");
  const internalNotes = messages.filter(m => m.source === "app");

  return (
    <div className="tkt-fullbleed">
      <div className="tkt-split">

        {/* ══ LEFT — ticket list ═══════════════════════════════════ */}
        <div className="tkt-panel-list">
          <TicketListPanel currentId={ticket.id} user={user} />
        </div>

        {/* ══ RIGHT — ticket detail ═══════════════════════════════ */}
        <div className="tkt-panel-detail">

          {/* Detail header */}
          <div className="tkt-detail-hdr">
            <Link href="/dashboard/tickets" className="tkt-back-btn">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m15 18-6-6 6-6"/>
              </svg>
              Back
            </Link>

            <div className="tkt-detail-hdr-left">
              <div className="tkt-detail-hdr-meta">
                <span className="tkt-tkt-label">{ticket.ticket_number}</span>
                {ticket.entity && <span className="tkt-pill entity">{ticket.entity}</span>}
                <span className={`tkt-pill ${ticket.priority}`}>{ticket.priority}</span>
                <div className="tkt-synced">
                  <div className="tkt-synced-dot" />
                  SharePoint
                </div>
              </div>
              <h2>{ticket.title}</h2>
            </div>

            <div className="tkt-detail-hdr-right">
              {/* Priority selector — admin + reviewer can change, others see pill */}
              {isAdmin ? (
                <select
                  className="tkt-status-sel"
                  value={ticket.priority}
                  onChange={handlePriorityChange}
                  title="Priority"
                >
                  <option value="low">🟢 Low</option>
                  <option value="medium">🟡 Medium</option>
                  <option value="high">🔴 High</option>
                  <option value="urgent">🚨 Urgent</option>
                </select>
              ) : (
                <span className={`tkt-pill ${ticket.priority}`}>{ticket.priority}</span>
              )}

              {/* Status selector */}
              {isAdmin ? (
                <select
                  className="tkt-status-sel"
                  value={ticket.status}
                  onChange={handleStatusChange}
                  title="Status"
                >
                  <option value="open">Open</option>
                  <option value="in_progress">In Progress</option>
                  <option value="closed">Closed</option>
                </select>
              ) : (
                <span className={`tkt-pill ${ticket.status}`}>{ticket.status.replace("_"," ")}</span>
              )}
            </div>
          </div>

          {/* Detail body: info panel + timeline + chat */}
          <div className="tkt-detail-body">

            {/* ── Info panel ───────────────────────────────────── */}
            <div className="tkt-info-panel">

              {/* Properties */}
              <div className="tkt-info-section">
                <div className="tkt-info-lbl">Properties</div>
                <div className="tkt-info-row">
                  <span className="tkt-info-k">Status</span>
                  <span className={`tkt-pill ${ticket.status}`}>{ticket.status.replace("_"," ")}</span>
                </div>
                <div className="tkt-info-row">
                  <span className="tkt-info-k">Priority</span>
                  <span className={`tkt-pill ${ticket.priority}`}>{ticket.priority}</span>
                </div>
                {ticket.entity && (
                  <div className="tkt-info-row">
                    <span className="tkt-info-k">Entity</span>
                    <span className="tkt-info-v">{ticket.entity}</span>
                  </div>
                )}
                <div className="tkt-info-row">
                  <span className="tkt-info-k">Created</span>
                  <span className="tkt-info-v">{formatAbsolute(ticket.created_at)}</span>
                </div>
                <div className="tkt-info-row">
                  <span className="tkt-info-k">Updated</span>
                  <RelativeTime iso={ticket.updated_at} className="tkt-info-v" />
                </div>
                <div className="tkt-info-row">
                  <span className="tkt-info-k">Emails</span>
                  <span className="tkt-info-v">{emailEvents.length}</span>
                </div>
              </div>

              {/* Participants */}
              <div className="tkt-info-section">
                <div className="tkt-info-lbl">Participants ({participants.length})</div>
                {participants.map(p => (
                  <div key={p.id} className="tkt-participant">
                    <div className={`tkt-avatar ${p.role}`}>
                      {p.full_name.charAt(0).toUpperCase()}
                    </div>
                    <div className="tkt-participant-info">
                      <strong>{p.full_name}</strong>
                      <span>{p.role}</span>
                    </div>
                  </div>
                ))}
              </div>

              {/* Admin: Assign */}
              {isAdmin && (
                <div className="tkt-info-section">
                  <div className="tkt-info-lbl">Assign</div>
                  <select
                    className="tkt-assign-sel"
                    onChange={handleAssign}
                    onClick={loadAllUsers}
                    defaultValue=""
                  >
                    <option value="" disabled>Select user to assign…</option>
                    {allUsers.map(u => (
                      <option key={u.id} value={u.id}>{u.full_name}</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Attachments */}
              {attachments.length > 0 && (
                <div className="tkt-info-section">
                  <div className="tkt-info-lbl">Attachments ({attachments.length})</div>
                  {attachments.map(att => (
                    <a
                      key={att.id}
                      className="tkt-att-link"
                      href={att.file_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <div className="tkt-att-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                        </svg>
                      </div>
                      <div className="tkt-att-info">
                        <strong title={att.file_name}>{att.file_name}</strong>
                        <span>{att.entity || att.modified_by || ""}</span>
                      </div>
                    </a>
                  ))}
                </div>
              )}
            </div>

            {/* ── Timeline + internal chat ──────────────────────── */}
            <div className="tkt-timeline-panel">

              {/* Email timeline */}
              <div className="tkt-timeline-body">
                {emailEvents.length === 0 ? (
                  <div className="tkt-timeline-empty">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ width: 32, height: 32, opacity: .4 }}>
                      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 3.6 1.17h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8 8.09a16 16 0 0 0 6 6l.61-.61a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 21.17 16h.75z"/>
                    </svg>
                    <p>No email events yet. SharePoint will push them here automatically.</p>
                  </div>
                ) : (
                  <>
                    <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>
                      Email Thread · {emailEvents.length} event{emailEvents.length !== 1 ? "s" : ""}
                    </div>
                    {emailEvents.map(m => (
                      <EventCard key={m.id} msg={m} currentUserId={user.id} />
                    ))}
                  </>
                )}
              </div>

              {/* Internal Discussion (collapsible) */}
              <InternalChat
                ticketId={ticket.id}
                messages={messages}
                currentUserId={user.id}
                currentUserName={user.full_name}
                onRefresh={fetchTicket}
              />

            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
