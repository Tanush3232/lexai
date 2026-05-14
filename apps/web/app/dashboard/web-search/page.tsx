"use client";
import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { webSearchApi, SearchSession } from "@/lib/api";

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

const MODE_CONFIG: Record<string, { label: string; color: string; bg: string; border: string }> = {
  fast:  { label: "Fast",          color: "#34d399", bg: "rgba(52,211,153,0.08)",  border: "rgba(52,211,153,0.25)" },
  pro:   { label: "Pro",           color: "#818cf8", bg: "rgba(129,140,248,0.08)", border: "rgba(129,140,248,0.25)" },
  deep:  { label: "Deep Research", color: "#f59e0b", bg: "rgba(245,158,11,0.08)",  border: "rgba(245,158,11,0.25)" },
};

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  completed: { label: "Completed", color: "#34d399" },
  searching: { label: "Searching…", color: "#818cf8" },
  failed:    { label: "Failed",    color: "#f87171" },
  pending:   { label: "Pending",   color: "#94a3b8" },
};

// ─── Component ────────────────────────────────────────────────────────────────

export default function WebSearchHistoryPage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<SearchSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [showInfo, setShowInfo] = useState(false);
  const LIMIT = 15;

  const loadSessions = useCallback(async (p: number) => {
    try {
      setLoading(true);
      const res = await webSearchApi.listSessions(p, LIMIT);
      const data: SearchSession[] = res.data;
      if (p === 1) setSessions(data);
      else setSessions((prev) => [...prev, ...data]);
      setHasMore(data.length === LIMIT);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSessions(1); }, [loadSessions]);

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDeleting(id);
    try {
      await webSearchApi.deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
    } catch {
      // silent
    } finally {
      setDeleting(null);
    }
  };

  const loadMore = () => {
    const nextPage = page + 1;
    setPage(nextPage);
    loadSessions(nextPage);
  };

  return (
    <div style={{ minHeight: "100%", padding: "0" }}>
      <style>{`
        .ws-history-root {
          max-width: 900px;
          margin: 0 auto;
          padding: 32px 24px 64px;
        }

        .ws-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 32px;
          gap: 16px;
        }

        .ws-title-block h1 {
          font-size: 26px;
          font-weight: 700;
          color: var(--text);
          margin: 0 0 4px;
          letter-spacing: -0.5px;
        }

        .ws-title-block p {
          font-size: 13px;
          color: var(--text2);
          margin: 0;
        }

        .ws-new-btn {
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
        .ws-new-btn:hover {
          transform: translateY(-1px);
          box-shadow: var(--shadow);
          background: var(--accent-mid);
        }
        .ws-new-btn:active { transform: translateY(0); }

        .ws-divider {
          height: 1px;
          background: var(--border);
          margin-bottom: 28px;
        }

        .ws-empty {
          text-align: center;
          padding: 80px 0;
          color: var(--text2);
        }
        .ws-empty-icon {
          width: 56px; height: 56px;
          margin: 0 auto 16px;
          color: var(--border2);
        }
        .ws-empty h3 { font-size: 18px; font-weight: 600; color: var(--text); margin: 0 0 8px; }
        .ws-empty p  { font-size: 14px; color: var(--text2); margin: 0; }

        .ws-session-list {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .ws-session-card {
          background: var(--white);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 18px 20px;
          cursor: pointer;
          text-decoration: none;
          display: block;
          transition: all 0.2s ease;
          position: relative;
          box-shadow: var(--shadow-sm);
        }
        .ws-session-card:hover {
          background: var(--bg);
          border-color: var(--accent);
          transform: translateY(-1px);
        }

        .ws-session-top {
          display: flex;
          align-items: flex-start;
          gap: 12px;
        }

        .ws-search-icon {
          width: 32px; height: 32px;
          background: var(--accent-light);
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
          margin-top: 1px;
        }
        .ws-search-icon svg { width: 15px; height: 15px; color: var(--accent); }

        .ws-session-query {
          flex: 1;
          font-size: 15px;
          font-weight: 600;
          color: var(--text);
          line-height: 1.5;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .ws-delete-btn {
          background: none;
          border: none;
          cursor: pointer;
          color: var(--text3);
          padding: 4px;
          border-radius: 6px;
          transition: all 0.15s;
          flex-shrink: 0;
          opacity: 0;
          transition: opacity 0.2s;
        }
        .ws-session-card:hover .ws-delete-btn { opacity: 1; }
        .ws-delete-btn:hover { color: var(--red); background: var(--red-light); }

        .ws-session-meta {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }

        .ws-badge {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 11px;
          font-weight: 600;
          padding: 3px 9px;
          border-radius: 20px;
          letter-spacing: 0.3px;
          text-transform: uppercase;
        }

        .ws-time {
          font-size: 12px;
          color: var(--text3);
        }

        .ws-summary {
          font-size: 13px;
          color: var(--text2);
          line-height: 1.5;
          margin-top: 8px;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }

        .ws-load-more {
          text-align: center;
          margin-top: 24px;
        }
        .ws-load-more-btn {
          background: var(--white);
          border: 1px solid var(--border);
          color: var(--text2);
          padding: 10px 28px;
          border-radius: 8px;
          font-size: 14px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s;
        }
        .ws-load-more-btn:hover {
          background: var(--bg2);
          border-color: var(--border2);
          color: var(--text);
        }

        .ws-skeleton {
          background: var(--white);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 18px 20px;
          animation: ws-pulse 1.5s ease infinite;
        }
        .ws-skel-line {
          height: 12px;
          background: var(--bg2);
          border-radius: 6px;
          margin-bottom: 10px;
        }
        @keyframes ws-pulse {
          0%,100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>

      <div className="ws-history-root">
        {/* Header */}
        <div className="ws-header">
          <div className="ws-title-block">
            <h1>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:24,height:24,display:"inline",marginRight:10,verticalAlign:"middle",color:"#818cf8"}}>
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
              </svg>
              Legal Web Search
            </h1>
            <p>Grounded legal research from authoritative Indian legal sources</p>
          </div>
          <div style={{display: "flex", gap: "10px", alignItems: "center"}}>
            <button 
              onClick={() => setShowInfo(true)}
              style={{
                width: 38, height: 38, borderRadius: "50%", background: "var(--bg2)", 
                border: "1px solid var(--border)", display: "flex", alignItems: "center", 
                justifyContent: "center", cursor: "pointer", color: "var(--text2)",
                transition: "all 0.2s"
              }}
              onMouseOver={e => {e.currentTarget.style.color = "var(--text)"; e.currentTarget.style.borderColor = "var(--accent)"}}
              onMouseOut={e => {e.currentTarget.style.color = "var(--text2)"; e.currentTarget.style.borderColor = "var(--border)"}}
              title="About Search Modes"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{width: 18, height: 18}}>
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
              </svg>
            </button>
            <Link href="/dashboard/web-search/new" className="ws-new-btn">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{width:15,height:15}}>
                <path d="M12 5v14M5 12h14"/>
              </svg>
              New Search
            </Link>
          </div>
        </div>

        <div className="ws-divider" />

        {/* Loading skeletons */}
        {loading && sessions.length === 0 && (
          <div style={{display:"flex",flexDirection:"column",gap:10}}>
            {[1,2,3].map(i => (
              <div key={i} className="ws-skeleton">
                <div className="ws-skel-line" style={{width:"70%"}} />
                <div className="ws-skel-line" style={{width:"45%"}} />
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {!loading && sessions.length === 0 && (
          <div className="ws-empty">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="ws-empty-icon">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <h3>No searches yet</h3>
            <p>Start your first legal research query</p>
            <Link href="/dashboard/web-search/new" className="ws-new-btn" style={{marginTop:20,display:"inline-flex"}}>
              Begin Research
            </Link>
          </div>
        )}

        {/* Session list */}
        {sessions.length > 0 && (
          <div className="ws-session-list">
            {sessions.map((s) => {
              const mode = MODE_CONFIG[s.mode] || MODE_CONFIG.fast;
              const status = STATUS_CONFIG[s.status] || STATUS_CONFIG.pending;
              return (
                <Link key={s.id} href={`/dashboard/web-search/${s.id}`} className="ws-session-card">
                  <div className="ws-session-top">
                    <div className="ws-session-query">{s.query}</div>
                    
                    <div style={{display: "flex", alignItems: "center", gap: 16, flexShrink: 0}}>
                      <span className="ws-time">{formatDate(s.created_at)}</span>
                      <button
                        className="ws-delete-btn"
                        onClick={(e) => handleDelete(e, s.id)}
                        title="Delete search"
                      >
                        {deleting === s.id ? (
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14,animation:"ws-pulse 1s infinite"}}>
                            <circle cx="12" cy="12" r="10"/>
                          </svg>
                        ) : (
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14}}>
                            <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                          </svg>
                        )}
                      </button>
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}

        {/* Load more */}
        {hasMore && !loading && sessions.length > 0 && (
          <div className="ws-load-more">
            <button className="ws-load-more-btn" onClick={loadMore}>
              Load more
            </button>
          </div>
        )}
      </div>

      {/* Info Modal */}
      {showInfo && (
        <div style={{position:"fixed", top:0, left:0, right:0, bottom:0, background:"rgba(0,0,0,0.4)", backdropFilter:"blur(4px)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:9999, padding: 24}}>
          <div style={{background:"var(--white)", width:"100%", maxWidth: 500, borderRadius: 16, padding: 32, boxShadow: "var(--shadow-lg)", position:"relative", animation: "ws-pulse 0.2s ease-out"}}>
            <button 
              onClick={() => setShowInfo(false)}
              style={{position:"absolute", top: 20, right: 20, background:"none", border:"none", cursor:"pointer", color:"var(--text2)"}}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:20, height:20}}><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
            <h2 style={{fontSize: 20, fontWeight: 700, margin: "0 0 24px", color: "var(--text)", display: "flex", alignItems: "center", gap: 10}}>
              <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" style={{width:24, height:24}}><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
              Search Modes
            </h2>
            <div style={{display:"flex", flexDirection:"column", gap: 20}}>
              <div style={{display:"flex", gap:16, alignItems:"flex-start"}}>
                <div style={{width:40, height:40, borderRadius:8, background:"rgba(52,211,153,0.1)", color:"#34d399", display:"flex", alignItems:"center", justifyContent:"center", flexShrink:0}}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:20, height:20}}><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                </div>
                <div>
                  <h4 style={{margin: "0 0 4px", fontSize: 15, fontWeight: 600, color: "var(--text)"}}>Fast (Gemini Flash)</h4>
                  <p style={{margin: 0, fontSize: 13, color: "var(--text2)", lineHeight: 1.5}}>Quick, single-pass retrieval for immediate legal answers. Best for straightforward questions and statutory lookups.</p>
                </div>
              </div>
              <div style={{display:"flex", gap:16, alignItems:"flex-start"}}>
                <div style={{width:40, height:40, borderRadius:8, background:"rgba(129,140,248,0.1)", color:"#818cf8", display:"flex", alignItems:"center", justifyContent:"center", flexShrink:0}}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:20, height:20}}><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
                </div>
                <div>
                  <h4 style={{margin: "0 0 4px", fontSize: 15, fontWeight: 600, color: "var(--text)"}}>Pro (Gemini Pro)</h4>
                  <p style={{margin: 0, fontSize: 13, color: "var(--text2)", lineHeight: 1.5}}>Generates a structured research plan before querying. Best for complex interpretations requiring clause-level analysis.</p>
                </div>
              </div>
              <div style={{display:"flex", gap:16, alignItems:"flex-start"}}>
                <div style={{width:40, height:40, borderRadius:8, background:"rgba(245,158,11,0.1)", color:"#f59e0b", display:"flex", alignItems:"center", justifyContent:"center", flexShrink:0}}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:20, height:20}}><path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 6v6l4 2"/></svg>
                </div>
                <div>
                  <h4 style={{margin: "0 0 4px", fontSize: 15, fontWeight: 600, color: "var(--text)"}}>Deep Research</h4>
                  <p style={{margin: 0, fontSize: 13, color: "var(--text2)", lineHeight: 1.5}}>Multi-stage orchestration (Plan → Retrieve → Gap Analysis → Refine → Synthesize). Best for comprehensive legal opinions and verifying exhaustive case law.</p>
                </div>
              </div>
            </div>
            <button 
              onClick={() => setShowInfo(false)}
              style={{width:"100%", marginTop: 32, padding: "12px", background:"var(--bg2)", border:"none", borderRadius: 8, color:"var(--text)", fontWeight: 600, cursor:"pointer"}}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
