"use client";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { chatApi, ChatHistorySession } from "@/lib/api";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(dateStr: string) {
  const d = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

const SCOPE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  hybrid:               { label: "All Docs",       color: "#818cf8", bg: "rgba(129,140,248,0.1)" },
  single_folder:        { label: "Folder",          color: "#34d399", bg: "rgba(52,211,153,0.1)"  },
  multiple_folders:     { label: "Multi-Folder",    color: "#f59e0b", bg: "rgba(245,158,11,0.1)"  },
  files_single_folder:  { label: "Selected Files",  color: "#a78bfa", bg: "rgba(167,139,250,0.1)" },
  files_multi_folder:   { label: "Selected Files",  color: "#a78bfa", bg: "rgba(167,139,250,0.1)" },
};

// ─── Component ────────────────────────────────────────────────────────────────

export default function IntelligenceHistoryPage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<ChatHistorySession[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    try {
      setLoading(true);
      const res = await chatApi.listSessions();
      setSessions(res.data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDeleting(id);
    try {
      await chatApi.deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
    } catch {
      // silent
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div style={{ minHeight: "100%", padding: "0" }}>
      <style>{`
        .di-root {
          max-width: 900px;
          margin: 0 auto;
          padding: 32px 24px 64px;
        }

        .di-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 32px;
          gap: 16px;
        }

        .di-title-block h1 {
          font-size: 26px;
          font-weight: 700;
          color: var(--text);
          margin: 0 0 4px;
          letter-spacing: -0.5px;
        }

        .di-title-block p {
          font-size: 13px;
          color: var(--text2);
          margin: 0;
        }

        .di-new-btn {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          background: var(--accent);
          color: #fff;
          border: none;
          border-radius: 10px;
          padding: 10px 20px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s ease;
          text-decoration: none;
          box-shadow: var(--shadow-sm);
          flex-shrink: 0;
        }
        .di-new-btn:hover {
          transform: translateY(-1px);
          box-shadow: var(--shadow);
          background: var(--accent-mid);
        }

        .di-divider {
          height: 1px;
          background: var(--border);
          margin-bottom: 28px;
        }

        .di-empty {
          text-align: center;
          padding: 80px 0;
          color: var(--text2);
        }
        .di-empty-icon {
          width: 64px; height: 64px;
          margin: 0 auto 20px;
          color: var(--border2);
        }
        .di-empty h3 { font-size: 20px; font-weight: 600; color: var(--text); margin: 0 0 8px; }
        .di-empty p  { font-size: 14px; color: var(--text2); margin: 0 0 24px; }

        .di-session-list {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .di-session-card {
          background: var(--white);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 18px 20px;
          cursor: pointer;
          text-decoration: none;
          display: block;
          transition: all 0.2s ease;
          position: relative;
          box-shadow: var(--shadow-sm);
        }
        .di-session-card:hover {
          background: var(--bg);
          border-color: var(--accent);
          transform: translateY(-1px);
          box-shadow: 0 4px 20px rgba(79,70,229,0.12);
        }

        .di-session-top {
          display: flex;
          align-items: flex-start;
          gap: 14px;
        }

        .di-icon-wrap {
          width: 38px; height: 38px;
          background: var(--accent-light);
          border-radius: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }

        .di-session-body {
          flex: 1;
          min-width: 0;
        }

        .di-session-title {
          font-size: 15px;
          font-weight: 600;
          color: var(--text);
          line-height: 1.4;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          margin-bottom: 4px;
        }

        .di-session-query {
          font-size: 13px;
          color: var(--text2);
          line-height: 1.5;
          display: -webkit-box;
          -webkit-line-clamp: 1;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .di-session-meta {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-top: 10px;
          flex-wrap: wrap;
        }

        .di-badge {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 11px;
          font-weight: 600;
          padding: 3px 9px;
          border-radius: 20px;
          letter-spacing: 0.2px;
        }

        .di-msg-count {
          font-size: 12px;
          color: var(--text3);
          display: flex;
          align-items: center;
          gap: 4px;
        }

        .di-time {
          font-size: 12px;
          color: var(--text3);
          margin-left: auto;
        }

        .di-delete-btn {
          background: none;
          border: none;
          cursor: pointer;
          color: var(--text3);
          padding: 6px;
          border-radius: 8px;
          transition: all 0.15s;
          flex-shrink: 0;
          opacity: 0;
        }
        .di-session-card:hover .di-delete-btn { opacity: 1; }
        .di-delete-btn:hover { color: #ef4444; background: rgba(239,68,68,0.08); }

        .di-skeleton {
          background: var(--white);
          border: 1px solid var(--border);
          border-radius: 14px;
          padding: 18px 20px;
          animation: di-pulse 1.5s ease infinite;
        }
        .di-skel-line {
          height: 12px;
          background: var(--bg2);
          border-radius: 6px;
          margin-bottom: 10px;
        }
        @keyframes di-pulse {
          0%,100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>

      <div className="di-root">
        {/* Header */}
        <div className="di-header">
          <div className="di-title-block">
            <h1>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:24,height:24,display:"inline",marginRight:10,verticalAlign:"middle",color:"var(--accent)"}}>
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
              Document Intelligence
            </h1>
            <p>AI-powered analysis of your legal documents — ask anything, get cited answers</p>
          </div>
          <Link href="/dashboard/intelligence/new" className="di-new-btn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{width:15,height:15}}>
              <path d="M12 5v14M5 12h14"/>
            </svg>
            New Analysis
          </Link>
        </div>

        <div className="di-divider" />

        {/* Loading skeletons */}
        {loading && sessions.length === 0 && (
          <div style={{display:"flex",flexDirection:"column",gap:10}}>
            {[1,2,3].map(i => (
              <div key={i} className="di-skeleton">
                <div className="di-skel-line" style={{width:"60%"}} />
                <div className="di-skel-line" style={{width:"40%"}} />
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {!loading && sessions.length === 0 && (
          <div className="di-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="di-empty-icon">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
            <h3>No analyses yet</h3>
            <p>Start by asking a question about your legal documents</p>
            <Link href="/dashboard/intelligence/new" className="di-new-btn" style={{display:"inline-flex"}}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{width:15,height:15}}>
                <path d="M12 5v14M5 12h14"/>
              </svg>
              Start New Analysis
            </Link>
          </div>
        )}

        {/* Session list */}
        {sessions.length > 0 && (
          <div className="di-session-list">
            {sessions.map((s) => {
              const scope = SCOPE_CONFIG[s.scope_type] || SCOPE_CONFIG.hybrid;
              return (
                <Link key={s.id} href={`/dashboard/intelligence/${s.id}`} className="di-session-card">
                  <div className="di-session-top">
                    <div className="di-icon-wrap">
                      <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" style={{width:18,height:18}}>
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                      </svg>
                    </div>
                    <div className="di-session-body">
                      <div className="di-session-title">{s.title}</div>
                      {s.last_query && (
                        <div className="di-session-query">"{s.last_query}"</div>
                      )}
                      <div className="di-session-meta">
                        <span
                          className="di-badge"
                          style={{ color: scope.color, background: scope.bg }}
                        >
                          {scope.label}
                        </span>
                        <span className="di-msg-count">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:11,height:11}}>
                            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                          </svg>
                          {Math.floor(s.message_count / 2)} exchange{Math.floor(s.message_count / 2) !== 1 ? "s" : ""}
                        </span>
                        <span className="di-time">{formatDate(s.updated_at)}</span>
                      </div>
                    </div>
                    <button
                      className="di-delete-btn"
                      onClick={(e) => handleDelete(e, s.id)}
                      title="Delete session"
                    >
                      {deleting === s.id ? (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14,animation:"di-pulse 1s infinite"}}>
                          <circle cx="12" cy="12" r="10"/>
                        </svg>
                      ) : (
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14}}>
                          <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        </svg>
                      )}
                    </button>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
