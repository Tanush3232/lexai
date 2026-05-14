"use client";
import { useState, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { webSearchApi, SearchMode, SearchEvent, Citation, ReasoningStep } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth-store";

const MODES = [
  { id: "fast" as SearchMode, label: "Fast", icon: "⚡", desc: "Gemini Flash · Quick legal retrieval" },
  { id: "pro" as SearchMode, label: "Pro", icon: "✦", desc: "Gemini Pro · Deep reasoning & clause analysis" },
  { id: "deep" as SearchMode, label: "Deep Research", icon: "◈", desc: "Multi-stage · Verify, re-rank, synthesize" },
];

const EXAMPLES = [
  "What are the grounds for termination under the Industrial Disputes Act?",
  "Supreme Court judgment on right to privacy – Puttaswamy case",
  "SEBI regulations on insider trading 2024",
  "GST implications on export of services",
  "RBI guidelines on digital lending platforms",
];

type Phase = "idle" | "planning" | "searching" | "done" | "error";

export default function NewSearchPage() {
  const router = useRouter();
  const { token } = useAuthStore();
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("fast");
  const [phase, setPhase] = useState<Phase>("idle");
  const [steps, setSteps] = useState<ReasoningStep[]>([]);
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [error, setError] = useState("");
  const [showSteps, setShowSteps] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState("");
  const [expandedCitation, setExpandedCitation] = useState<string | null>(null);
  const [researchPlan, setResearchPlan] = useState<string[] | null>(null);
  const [readButNotUsed, setReadButNotUsed] = useState<Citation[]>([]);
  const [showUsedSources, setShowUsedSources] = useState(true);
  const [showReadSources, setShowReadSources] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const executeSearch = useCallback(() => {
    setPhase("searching");
    setSteps([]);
    setAnswer("");
    setCitations([]);
    setError("");
    setShowSteps(false);

    cleanupRef.current = webSearchApi.startSearch(
      query.trim(),
      mode,
      researchPlan || undefined,
      sessionId || undefined,
      (event: SearchEvent) => {
        if (event.type === "session_created" && event.session_id) {
          setSessionId(event.session_id);
        } else if (event.type === "thinking_step") {
          setSteps((prev) => [...prev, { step: event.step || "", detail: event.detail, timestamp: event.timestamp }]);
        } else if (event.type === "complete") {
          setAnswer(event.answer || "");
          setCitations(event.citations || []);
          setReadButNotUsed(event.read_but_not_used || []);
          setPhase("done");
        } else if (event.type === "error") {
          setError(event.message || "Search failed");
          setPhase("error");
        }
      },
      (err) => { setError(err.message); setPhase("error"); }
    );
  }, [query, mode, researchPlan, sessionId]);

  const handleSubmit = useCallback(async () => {
    if (!query.trim() || phase === "searching" || phase === "planning") return;

    if (mode === "deep" && !researchPlan) {
      setPhase("planning");
      try {
        const res = await webSearchApi.generatePlan(query.trim());
        setResearchPlan(res.data.steps || []);
      } catch (e) {
        console.error("Plan generation failed:", e);
        executeSearch();
      }
      return;
    }

    executeSearch();
  }, [query, mode, phase, researchPlan, executeSearch]);

  const handleFollowUpSubmit = useCallback(() => {
    if (!followUp.trim() || phase === "searching") return;
    const combinedQuery = `Previous context: ${query}\nPrevious answer summary: ${answer.slice(0, 1000)}...\n\nFollow-up question: ${followUp.trim()}`;
    
    setQuery(prev => `${prev}\n|FOLLOWUP|\n${followUp}`);
    setFollowUp("");
    setPhase("searching");
    setSteps([]);
    setError("");
    setShowSteps(false);

    cleanupRef.current = webSearchApi.startSearch(
      combinedQuery,
      mode,
      undefined,
      sessionId || undefined,
      (event: SearchEvent) => {
        if (event.type === "session_created" && event.session_id) {
          setSessionId(event.session_id);
        } else if (event.type === "thinking_step") {
          setSteps((prev) => [...prev, { step: event.step || "", detail: event.detail, timestamp: event.timestamp }]);
        } else if (event.type === "complete") {
          setAnswer(prev => prev ? `${prev}\n\n---\n\n${event.answer || ""}` : (event.answer || ""));
          setCitations(prev => [...prev, ...(event.citations || [])]);
          setReadButNotUsed(prev => [...prev, ...(event.read_but_not_used || [])]);
          setPhase("done");
        } else if (event.type === "error") {
          setError(event.message || "Search failed");
          setPhase("error");
        }
      },
      (err) => { setError(err.message); setPhase("error"); }
    );
  }, [followUp, query, answer, mode, phase, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { 
      e.preventDefault(); 
      if (phase === "idle") handleSubmit();
      else handleFollowUpSubmit();
    }
  };

  const handleStop = () => {
    if (cleanupRef.current) {
      cleanupRef.current();
      setPhase(answer ? "done" : "idle");
    }
  };

  const reset = () => {
    cleanupRef.current?.();
    setPhase("idle");
    setQuery("");
    setSteps([]);
    setAnswer("");
    setCitations([]);
    setError("");
    setSessionId(null);
  };

  useEffect(() => () => { cleanupRef.current?.(); }, []);

  const ctypeColor: Record<string, string> = {
    act: "#34d399", judgement: "#818cf8", circular: "#f59e0b", notification: "#fb923c", reference: "#94a3b8"
  };

  return (
    <div style={{ minHeight: "100%", color: "#e2e8f0" }}>
      <style>{`
        .ns-root { max-width:860px; margin:0 auto; padding:0 24px 80px; }
        /* IDLE HERO */
        .ns-hero { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:calc(100vh - 180px); padding-top:40px; }
        .ns-hero-badge { display:inline-flex; align-items:center; gap:6px; background:var(--accent-light); border:1px solid var(--accent-light); color:var(--accent); padding:5px 14px; border-radius:20px; font-size:12px; font-weight:600; letter-spacing:.5px; text-transform:uppercase; margin-bottom:24px; }
        .ns-hero-title { font-size:clamp(28px,5vw,46px); font-weight:700; text-align:center; margin:0 0 10px; background:linear-gradient(135deg,var(--accent),var(--accent-mid)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; letter-spacing:-1px; line-height:1.15; }
        .ns-hero-sub { font-size:15px; color:var(--text2); text-align:center; margin:0 0 36px; max-width:520px; line-height:1.6; }
        /* BOX */
        .ns-box { width:100%; background:var(--white); border:1px solid var(--border); border-radius:16px; overflow:hidden; box-shadow:var(--shadow); transition:all .3s ease; }
        .ns-box:focus-within { border-color:var(--accent); box-shadow:var(--shadow-lg),0 0 0 1px var(--accent); }
        .ns-box-compact { background:var(--white); border:1px solid var(--border); border-radius:12px; overflow:hidden; box-shadow:var(--shadow-sm); margin-bottom:28px; }
        .ns-textarea { width:100%; background:transparent; border:none; outline:none; padding:20px 20px 0; font-size:16px; color:var(--text); resize:none; min-height:72px; line-height:1.6; }
        .ns-textarea-compact { min-height:52px; padding:14px 16px 0; font-size:15px; }
        .ns-textarea::placeholder { color:var(--text3); }
        .ns-toolbar { display:flex; align-items:center; gap:8px; padding:12px 16px; border-top:1px solid var(--border); background:var(--bg); }
        .ns-mode-btn { display:flex; align-items:center; gap:6px; padding:6px 12px; border-radius:8px; border:1px solid var(--border); background:var(--white); color:var(--text2); font-size:13px; font-weight:500; cursor:pointer; transition:all .2s; white-space:nowrap; }
        .ns-mode-btn.active { border-color:var(--accent); background:var(--accent-light); color:var(--accent); }
        .ns-mode-btn:hover:not(.active) { border-color:var(--border2); color:var(--text); background:var(--bg2); }
        .ns-submit-btn { margin-left:auto; display:flex; align-items:center; gap:7px; background:var(--accent); color:#fff; border:none; border-radius:9px; padding:9px 18px; font-size:14px; font-weight:600; cursor:pointer; transition:all .2s; box-shadow:var(--shadow-sm); }
        .ns-submit-btn:hover:not(:disabled) { transform:translateY(-1px); box-shadow:var(--shadow); background:var(--accent-mid); }
        .ns-submit-btn:disabled { opacity:.5; cursor:not-allowed; transform:none; }
        /* EXAMPLES */
        .ns-examples { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:20px; }
        .ns-chip { background:var(--white); border:1px solid var(--border); color:var(--text2); padding:6px 14px; border-radius:20px; font-size:12px; cursor:pointer; transition:all .2s; }
        .ns-chip:hover { border-color:var(--border2); color:var(--text); background:var(--bg2); }
        /* THINKING */
        .ns-thinking-section { margin-bottom:24px; }
        .ns-thinking-header { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
        .ns-thinking-label { font-size:14px; font-weight:600; color:var(--accent); }
        .ns-dots span { display:inline-block; width:4px; height:4px; background:var(--accent); border-radius:50%; margin:0 2px; animation:nsdot .8s ease infinite; }
        .ns-dots span:nth-child(2){animation-delay:.15s} .ns-dots span:nth-child(3){animation-delay:.3s}
        @keyframes nsdot { 0%,80%,100%{transform:scale(.6);opacity:.4} 40%{transform:scale(1);opacity:1} }
        .ns-steps { display:flex; flex-direction:column; gap:6px; }
        .ns-step { display:flex; align-items:flex-start; gap:10px; padding:10px 14px; background:var(--white); border:1px solid var(--border); border-radius:8px; animation:nsslide .3s ease; box-shadow:var(--shadow-sm); }
        @keyframes nsslide { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
        .ns-step-dot { width:6px; height:6px; background:var(--accent); border-radius:50%; flex-shrink:0; margin-top:5px; box-shadow:0 0 6px var(--accent-light); }
        .ns-step-text { font-size:13px; color:var(--text); font-weight:500; }
        .ns-step-detail { font-size:11px; color:var(--text2); margin-top:2px; }
        /* RESULT */
        .ns-result { }
        .ns-steps-toggle { display:flex; align-items:center; gap:8px; background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:10px 14px; cursor:pointer; width:100%; font-size:13px; color:var(--text2); font-weight:500; margin-bottom:20px; transition:all .2s; }
        .ns-steps-toggle:hover { background:var(--bg2); color:var(--text); }
        .ns-steps-toggle svg { color:var(--accent); flex-shrink:0; }
        .ns-answer-card { background:var(--white); border:1px solid var(--border); border-radius:14px; padding:28px; margin-bottom:24px; box-shadow:var(--shadow-sm); }
        .ns-answer-content { font-size:14px; color:var(--text); line-height:1.8; white-space:pre-wrap; }
        .ns-answer-content h2 { font-size:16px; font-weight:700; color:var(--text); margin:20px 0 8px; padding-bottom:6px; border-bottom:1px solid var(--border); }
        .ns-answer-content h3 { font-size:14px; font-weight:600; color:var(--text); margin:14px 0 6px; }
        .ns-answer-content strong { color:var(--text); font-weight:600; }
        .ns-citations-title { font-size:14px; font-weight:700; color:var(--text2); text-transform:uppercase; letter-spacing:.8px; margin-bottom:14px; }
        .ns-citation-card { background:var(--white); border:1px solid var(--border); border-radius:10px; padding:14px 16px; margin-bottom:10px; transition:all .2s; box-shadow:var(--shadow-sm); }
        .ns-citation-card:hover { border-color:var(--accent); background:var(--bg); transform:translateY(-1px); }
        .ns-citation-top { display:flex; align-items:flex-start; gap:10px; }
        .ns-citation-name { font-size:14px; font-weight:600; color:var(--text); flex:1; line-height:1.4; }
        .ns-citation-link { color:var(--accent); text-decoration:none; font-size:12px; display:flex; align-items:center; gap:4px; white-space:nowrap; }
        .ns-citation-link:hover { color:var(--accent-mid); text-decoration:underline; }
        .ns-citation-meta { display:flex; align-items:center; gap:8px; margin-top:6px; flex-wrap:wrap; }
        .ns-domain-badge { font-size:11px; color:var(--text2); background:var(--bg2); border:1px solid var(--border); padding:2px 8px; border-radius:4px; }
        .ns-ctype-badge { font-size:10px; font-weight:700; padding:2px 8px; border-radius:4px; text-transform:uppercase; letter-spacing:.4px; }
        .ns-snippet-toggle { font-size:12px; color:var(--text2); cursor:pointer; margin-top:6px; display:inline-flex; align-items:center; gap:4px; }
        .ns-snippet-toggle:hover { color:var(--text); }
        .ns-snippet { font-size:12px; color:var(--text2); line-height:1.6; margin-top:8px; padding:10px 12px; background:var(--bg); border-radius:6px; border-left:2px solid var(--accent); }
        .ns-actions { display:flex; gap:10px; margin-top:24px; padding-bottom: 120px; }
        .ns-new-btn { display:inline-flex; align-items:center; gap:7px; background:var(--accent); color:#fff; border:none; border-radius:9px; padding:11px 22px; font-size:14px; font-weight:600; cursor:pointer; box-shadow:var(--shadow-sm); transition:all .2s; }
        .ns-new-btn:hover { transform:translateY(-1px); box-shadow:var(--shadow); background:var(--accent-mid); }
        .ns-error { background:var(--red-light); border:1px solid var(--red); border-radius:10px; padding:16px 20px; color:var(--red); font-size:14px; margin-top:16px; }
        /* Markdown-like rendering */
        .ns-answer-content p { margin:8px 0; }
        .ns-answer-content ul, .ns-answer-content ol { padding-left:20px; margin:8px 0; }
        .ns-answer-content li { margin:4px 0; }
        
        .ns-fixed-bottom { position: sticky; bottom: 0; padding: 20px 0 30px; background: linear-gradient(to top, var(--bg) 80%, transparent); display: flex; justify-content: center; z-index: 100; pointer-events: none; margin-top: auto; }
        .ns-fixed-bottom > * { pointer-events: auto; }
        
        .ns-stop-btn { display:flex; align-items:center; gap:6px; padding:6px 14px; border-radius:20px; border:1px solid var(--border); background:var(--white); color:var(--text); font-size:13px; font-weight:600; cursor:pointer; transition:all .2s; box-shadow:var(--shadow-sm); margin: 0 auto 16px; }
        .ns-stop-btn:hover { background:var(--bg2); border-color:var(--border2); }
      `}</style>

      <div className="ns-root">
        {/* ── IDLE PHASE ──────────────────────────────────────────── */}
        {phase === "idle" && (
          <div className="ns-hero">
            <div className="ns-hero-badge">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:12,height:12}}>
                <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
              </svg>
              Legal Web Research
            </div>
            <h1 className="ns-hero-title">Research Indian Law<br/>with AI Precision</h1>
            <p className="ns-hero-sub">
              Grounded retrieval from Supreme Court, High Courts, IndiaCode, SEBI, RBI, GST portals & 28 more trusted sources.
            </p>

            <div className="ns-box" style={{width:"100%",maxWidth:720}}>
              <textarea
                ref={textareaRef}
                className="ns-textarea"
                placeholder="Ask any legal question — acts, judgements, regulations, circulars…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={3}
                autoFocus
              />
              <div className="ns-toolbar">
                {MODES.map((m) => (
                  <button
                    key={m.id}
                    className={`ns-mode-btn ${mode === m.id ? "active" : ""}`}
                    onClick={() => setMode(m.id)}
                    title={m.desc}
                  >
                    <span>{m.icon}</span>{m.label}
                  </button>
                ))}
                <button
                  className="ns-submit-btn"
                  onClick={handleSubmit}
                  disabled={!query.trim()}
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{width:14,height:14}}>
                    <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
                  </svg>
                  Search
                </button>
              </div>
            </div>

            <div className="ns-examples">
              {EXAMPLES.map((ex) => (
                <button key={ex} className="ns-chip" onClick={() => setQuery(ex)}>
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── SEARCHING / DONE / ERROR PHASES ─────────────────────── */}
        {phase !== "idle" && (
          <div style={{paddingTop:32}}>
            <h1 style={{fontSize: 24, fontWeight: 700, color: "var(--text)", marginBottom: 24}}>{query.split(/\n\|FOLLOWUP\|\n/)[0]}</h1>

            {/* Deep Research Planning */}
            {phase === "planning" && (
              <div style={{background: "var(--white)", border: "1px solid var(--border)", borderRadius: 12, padding: 24, marginBottom: 24, boxShadow: "var(--shadow-sm)"}}>
                <h3 style={{fontSize: 16, fontWeight: 600, color: "var(--text)", marginBottom: 16, display: "flex", alignItems: "center", gap: 8}}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:16,height:16, color: "var(--accent)"}}><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
                  Deep Research Plan
                </h3>
                
                {!researchPlan ? (
                  <div style={{display: "flex", alignItems: "center", gap: 12, color: "var(--text2)", fontSize: 14}}>
                    <div className="spin" style={{width: 16, height: 16, border: "2px solid var(--border)", borderTopColor: "var(--accent)", borderRadius: "50%"}} />
                    <span>Analyzing objective and structuring legal strategy...</span>
                  </div>
                ) : (
                  <div style={{display: "flex", flexDirection: "column", gap: 12}}>
                    {researchPlan.map((step, idx) => (
                      <div key={idx} style={{display: "flex", gap: 12, alignItems: "flex-start"}}>
                        <div style={{width: 24, height: 24, borderRadius: "50%", background: "var(--bg)", border: "1px dashed var(--border2)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 600, color: "var(--text3)", flexShrink: 0, marginTop: 6}}>
                          {idx + 1}
                        </div>
                        <textarea 
                          value={step}
                          onChange={(e) => {
                            const newPlan = [...researchPlan];
                            newPlan[idx] = e.target.value;
                            setResearchPlan(newPlan);
                          }}
                          rows={2}
                          style={{flex: 1, padding: "10px 14px", border: "1px solid var(--border)", borderRadius: 8, fontSize: 14, color: "var(--text)", outline: "none", background: "var(--bg)", resize: "vertical"}}
                        />
                      </div>
                    ))}
                    <div style={{display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 12}}>
                      <button 
                        onClick={() => { setPhase("idle"); setResearchPlan(null); }}
                        style={{padding: "8px 16px", border: "1px solid var(--border)", borderRadius: 8, background: "var(--white)", cursor: "pointer", fontSize: 13, fontWeight: 600}}
                      >
                        Cancel
                      </button>
                      <button 
                        onClick={executeSearch}
                        className="ns-submit-btn"
                        style={{margin: 0}}
                      >
                        Start Search
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Chat Flow */}
            <div className="ns-result" style={{display: "flex", flexDirection: "column", gap: 32}}>
              {(() => {
                const queryParts = query.split(/\n\|FOLLOWUP\|\n/);
                const answerParts = answer ? answer.split(/\n\n---\n\n/) : [];
                
                return queryParts.map((userQuery, index) => {
                  const isFirst = index === 0;
                  const answerPart = answerParts[index];
                  const isLastAndSearching = index === queryParts.length - 1 && phase === "searching";
                  const turnCitations = citations.filter((c: Citation) => (c.turn_index ?? 0) === index);
                  const turnReadButNotUsed = readButNotUsed.filter((c: Citation) => (c.turn_index ?? 0) === index);
                  
                  return (
                    <div key={index} style={{display: "flex", flexDirection: "column", gap: 16}}>
                      {/* User Bubble */}
                      {!isFirst && (
                        <div style={{alignSelf: "flex-end", background: "var(--accent)", color: "#fff", padding: "12px 18px", borderRadius: "18px 18px 0 18px", fontSize: 15, fontWeight: 500, maxWidth: "80%", boxShadow: "var(--shadow-sm)", lineHeight: 1.5}}>
                          {userQuery}
                        </div>
                      )}
                      
                      {/* AI Answer Bubble */}
                      {answerPart && (
                        <div className="ns-answer-card" style={{margin: 0, alignSelf: "flex-start", width: "100%", borderRadius: isFirst ? 14 : "0 18px 18px 18px"}}>
                          <div
                            className="ns-answer-content"
                            dangerouslySetInnerHTML={{
                              __html: answerPart
                                .replace(/## (.+)/g, "<h2>$1</h2>")
                                .replace(/### (.+)/g, "<h3>$1</h3>")
                                .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
                                .replace(/\*(.+?)\*/g, "<em>$1</em>")
                                .replace(/\[(\d+)\]/g, (match, p1) => {
                                  const idx = parseInt(p1, 10) - 1;
                                  const url = turnCitations[idx]?.url || `#cit-${index}-${p1}`;
                                  const target = turnCitations[idx]?.url ? "_blank" : "_self";
                                  return `<sup style='margin-left:2px'><a href='${url}' target='${target}' rel='noopener noreferrer' style='color:var(--accent);text-decoration:none;font-weight:700;'>[${p1}]</a></sup>`;
                                })
                                .replace(/\n/g, "<br/>")
                            }}
                          />
                        </div>
                      )}
                      
                      {/* Thinking Steps (Only on the active searching one) */}
                      {isLastAndSearching && (
                        <div className="ns-thinking-section" style={{alignSelf: "flex-start", width: "100%", marginTop: 8}}>
                          <div className="ns-thinking-header">
                            <div className="ns-thinking-label">Researching</div>
                            <div className="ns-dots">
                              <span/><span/><span/>
                            </div>
                          </div>
                          <div className="ns-steps">
                            {steps.map((s, i) => (
                              <div key={i} className="ns-step">
                                <div className="ns-step-dot"/>
                                <div>
                                  <div className="ns-step-text">{s.step}</div>
                                  {s.detail && <div className="ns-step-detail">{s.detail}</div>}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      
                      {/* Citations for this turn */}
                      {answerPart && (() => {
                        if (turnCitations.length === 0 && turnReadButNotUsed.length === 0) return null;
                        
                        return (
                          <div style={{marginTop: 16}}>
                            {turnCitations.length > 0 && (
                              <div style={{marginBottom: turnReadButNotUsed.length > 0 ? 16 : 0}}>
                                <button 
                                  onClick={() => setShowUsedSources(!showUsedSources)}
                                  style={{display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", color: "var(--text)", fontSize: 14, fontWeight: 600, cursor: "pointer", padding: 0, marginBottom: showUsedSources ? 12 : 0}}
                                >
                                  Sources used in this response
                                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width: 14, height: 14, transform: showUsedSources ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s"}}><path d="M6 9l6 6 6-6"/></svg>
                                </button>
                                
                                {showUsedSources && (
                                  <div style={{display: "flex", flexDirection: "column", gap: 8}}>
                                    {turnCitations.map((c: Citation, i: number) => (
                                      <div key={i} id={`cit-${index}-${i+1}`} className="ns-citation-card" style={{margin: 0, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 16px"}}>
                                        <div className="ns-citation-top" style={{marginBottom: 0}}>
                                          <div className="ns-citation-name" style={{fontSize: 13}}>
                                            <img src={`https://www.google.com/s2/favicons?domain=${c.domain}&sz=32`} style={{width: 14, height: 14, marginRight: 6, verticalAlign: "middle", borderRadius: 2}} />
                                            <span style={{color: "var(--text2)", marginRight: 8}}>{c.domain}</span>
                                            {c.source_name}
                                          </div>
                                          <a href={c.url} target="_blank" rel="noopener noreferrer" className="ns-citation-link" style={{fontSize: 12}}>
                                            Open ↗
                                          </a>
                                        </div>
                                        {c.snippet && (
                                          <div style={{marginTop: 8}}>
                                            <div
                                              className="ns-snippet-toggle"
                                              onClick={() => setExpandedCitation(expandedCitation === `${index}-${i}` ? null : `${index}-${i}`)}
                                              style={{fontSize: 12, padding: 0}}
                                            >
                                              {expandedCitation === `${index}-${i}` ? "▲" : "▼"} {expandedCitation === `${index}-${i}` ? "Hide" : "Show"} excerpt
                                            </div>
                                            {expandedCitation === `${index}-${i}` && (
                                              <div className="ns-snippet">{c.snippet}</div>
                                            )}
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}

                            {turnReadButNotUsed.length > 0 && (
                              <div>
                                <button 
                                  onClick={() => setShowReadSources(!showReadSources)}
                                  style={{display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", color: "var(--text)", fontSize: 14, fontWeight: 600, cursor: "pointer", padding: 0, marginBottom: showReadSources ? 12 : 0}}
                                >
                                  Sources read but not used
                                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width: 14, height: 14, transform: showReadSources ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s"}}><path d="M6 9l6 6 6-6"/></svg>
                                </button>
                                
                                {showReadSources && (
                                  <div style={{display: "flex", flexDirection: "column", gap: 8}}>
                                    {turnReadButNotUsed.map((c: Citation, i: number) => (
                                      <div key={`read-${i}`} className="ns-citation-card" style={{margin: 0, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 16px"}}>
                                        <div className="ns-citation-top" style={{marginBottom: 0}}>
                                          <div className="ns-citation-name" style={{fontSize: 13}}>
                                            <img src={`https://www.google.com/s2/favicons?domain=${c.domain}&sz=32`} style={{width: 14, height: 14, marginRight: 6, verticalAlign: "middle", borderRadius: 2}} />
                                            <span style={{color: "var(--text2)", marginRight: 8}}>{c.domain}</span>
                                            {c.source_name}
                                          </div>
                                          <a href={c.url} target="_blank" rel="noopener noreferrer" className="ns-citation-link" style={{fontSize: 12}}>
                                            Open ↗
                                          </a>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  );
                });
              })()}
            </div>

                <div className="ns-actions">
                  <button className="ns-new-btn" onClick={reset}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{width:14,height:14}}>
                      <path d="M12 5v14M5 12h14"/>
                    </svg>
                    Start a New Search
                  </button>
                </div>
          </div>
        )}

        {/* Fixed bottom chatbox */}
        {phase !== "idle" && (
          <div className="ns-fixed-bottom">
            <div style={{width: "100%", maxWidth: 812, display: "flex", flexDirection: "column", alignItems: "center"}}>
              {phase === "searching" && (
                <button className="ns-stop-btn" onClick={handleStop}>
                  <svg viewBox="0 0 24 24" fill="currentColor" style={{width:14,height:14}}><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
                  Stop generation
                </button>
              )}
              <div className="ns-box-compact" style={{width: "100%", margin: 0, boxShadow: "var(--shadow-lg)"}}>
                <textarea
                  className="ns-textarea ns-textarea-compact"
                  placeholder="Follow up with questions or adjustments..."
                  value={followUp}
                  onChange={(e) => {
                    if (phase !== "searching") {
                      setFollowUp(e.target.value);
                    }
                  }}
                  onKeyDown={handleKeyDown}
                  rows={1}
                  readOnly={phase === "searching"}
                />
                <div className="ns-toolbar">
                  {MODES.map((m) => (
                    <button
                      key={m.id}
                      className={`ns-mode-btn ${mode === m.id ? "active" : ""}`}
                      onClick={() => { if (phase !== "searching") setMode(m.id); }}
                      title={m.desc}
                      style={{opacity: phase==="searching"?0.5:1}}
                    >
                      <span>{m.icon}</span>{m.label}
                    </button>
                  ))}
                  <button
                    className="ns-submit-btn"
                    onClick={phase === "searching" ? undefined : handleFollowUpSubmit}
                    disabled={!followUp.trim() || phase === "searching"}
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{width:14,height:14}}>
                      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
                    </svg>
                    Search
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
