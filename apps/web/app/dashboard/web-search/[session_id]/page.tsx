"use client";
import { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { webSearchApi, SearchSession, Citation, ReasoningStep, SearchMode } from "@/lib/api";

const ctypeColor: Record<string, string> = {
  act: "#34d399", judgement: "#818cf8", circular: "#f59e0b", notification: "#fb923c", reference: "#94a3b8"
};
const MODE_LABEL: Record<string, string> = { fast: "⚡ Fast", pro: "✦ Pro", deep: "◈ Deep Research" };
const MODE_COLOR: Record<string, string> = { fast: "#34d399", pro: "#818cf8", deep: "#f59e0b" };

function formatDate(s: string) {
  return new Date(s).toLocaleString("en-IN", { day:"numeric", month:"short", year:"numeric", hour:"2-digit", minute:"2-digit" });
}

export default function SessionPage() {
  const { session_id } = useParams<{ session_id: string }>();
  const router = useRouter();
  const [data, setData] = useState<SearchSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showSteps, setShowSteps] = useState(false);
  const [expandedCit, setExpandedCit] = useState<string | null>(null);
  const [showUsedSources, setShowUsedSources] = useState(true);
  const [showReadSources, setShowReadSources] = useState(false);
  
  // Follow-up state
  const [followUp, setFollowUp] = useState("");
  const [activePrompt, setActivePrompt] = useState("");
  const [followUpMode, setFollowUpMode] = useState<SearchMode>("fast");
  const [isSearching, setIsSearching] = useState(false);
  const [followUpAnswer, setFollowUpAnswer] = useState("");
  const [followUpSteps, setFollowUpSteps] = useState<ReasoningStep[]>([]);
  const [followUpCitations, setFollowUpCitations] = useState<Citation[]>([]);
  const [followUpReadButNotUsed, setFollowUpReadButNotUsed] = useState<Citation[]>([]);
  const cleanupRef = useRef<(() => void) | null>(null);

  const MODES: {id: SearchMode; label: string; icon: any; desc: string}[] = [
    {id: "fast", label: "Fast", desc: "Quick legal retrieval using Gemini Flash", icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14}}><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>},
    {id: "pro", label: "Pro", desc: "Structured legal plan + deep analysis", icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14}}><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>},
    {id: "deep", label: "Deep Research", desc: "Multi-stage retrieval and synthesis", icon: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14}}><path d="M12 2a10 10 0 1 0 10 10"/><path d="M12 6v6l4 2"/></svg>}
  ];

  useEffect(() => {
    (async () => {
      try {
        const res = await webSearchApi.getSession(session_id);
        setData(res.data);
        if (res.data.mode) setFollowUpMode(res.data.mode as SearchMode);
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Session not found");
      } finally {
        setLoading(false);
      }
    })();
  }, [session_id]);

  const handleFollowUpSubmit = () => {
    if (!followUp.trim() || isSearching || !data) return;
    const combinedQuery = `Previous context: ${data.query}\nPrevious answer summary: ${data.full_answer?.slice(0, 1000)}...\n\nFollow-up question: ${followUp.trim()}`;
    
    setIsSearching(true);
    setActivePrompt(followUp.trim());
    setFollowUp("");
    setFollowUpAnswer("");
    setFollowUpSteps([]);
    setFollowUpCitations([]);

    cleanupRef.current = webSearchApi.startSearch(
      combinedQuery,
      followUpMode,
      undefined,
      session_id,
      (event) => {
        if (event.type === "thinking_step") {
          setFollowUpSteps((prev) => [...prev, { step: event.step || "", detail: event.detail, timestamp: event.timestamp }]);
        } else if (event.type === "complete") {
          setFollowUpAnswer(event.answer || "");
          setFollowUpCitations(event.citations || []);
          setFollowUpReadButNotUsed(event.read_but_not_used || []);
          setIsSearching(false);
          
          // Once complete, reload data to get the updated session state directly from the backend
          setTimeout(() => {
            webSearchApi.getSession(session_id).then(res => {
              setData(res.data);
              setActivePrompt("");
              setFollowUpAnswer("");
              setFollowUpSteps([]);
              setFollowUpCitations([]);
              setFollowUpReadButNotUsed([]);
            }).catch(console.error);
          }, 500);
        } else if (event.type === "error") {
          setIsSearching(false);
          alert(event.message || "Search failed");
        }
      },
      (err) => { 
        setIsSearching(false); 
        alert(err.message); 
      }
    );
  };

  const handleStop = () => {
    if (cleanupRef.current) {
      cleanupRef.current();
      setIsSearching(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { 
      e.preventDefault(); 
      handleFollowUpSubmit();
    }
  };

  if (loading) return (
    <div style={{fontFamily:"'Inter',sans-serif",maxWidth:860,margin:"0 auto",padding:"40px 24px",color:"var(--text)"}}>
      <div style={{background:"var(--white)",border:"1px solid var(--border)",borderRadius:14,padding:28,animation:"pulse 1.5s infinite",boxShadow:"var(--shadow-sm)"}}>
        {[1,2,3].map(i=><div key={i} style={{height:14,background:"var(--bg2)",borderRadius:6,marginBottom:14,width:i===1?"70%":i===2?"45%":"80%"}}/>)}
      </div>
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}`}</style>
    </div>
  );

  if (error || !data) return (
    <div style={{fontFamily:"'Inter',sans-serif",maxWidth:860,margin:"40px auto",padding:"0 24px",color:"var(--red)",textAlign:"center"}}>
      <p>{error || "Session not found"}</p>
      <Link href="/dashboard/web-search" style={{color:"var(--accent)",fontSize:14}}>← Back to searches</Link>
    </div>
  );

  const modeColor = MODE_COLOR[data.mode] || "var(--accent)";

  return (
    <div style={{fontFamily:"'Inter',sans-serif",maxWidth:860,margin:"0 auto",padding:"32px 24px 80px",color:"var(--text)"}}>
      <style>{`
        .sv-answer h2{font-size:16px;font-weight:700;color:var(--text);margin:20px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--border)}
        .sv-answer h3{font-size:14px;font-weight:600;color:var(--text);margin:14px 0 6px}
        .sv-answer strong{color:var(--text);font-weight:600}
        .sv-answer p{margin:8px 0}
        .sv-answer ul,.sv-answer ol{padding-left:20px;margin:8px 0}
        .sv-answer li{margin:4px 0}
        .sv-step{display:flex;gap:10px;padding:9px 13px;background:var(--white);border:1px solid var(--border);border-radius:8px;margin-bottom:6px;box-shadow:var(--shadow-sm)}
        .sv-cit{background:var(--white);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:10px;transition:all .2s;box-shadow:var(--shadow-sm)}
        .sv-cit:hover{border-color:var(--accent);background:var(--bg);transform:translateY(-1px)}
        
        .ns-box-compact { background:var(--white); border:1px solid var(--border); border-radius:12px; padding:12px; transition:all .2s; }
        .ns-textarea-compact { width:100%; border:none; background:transparent; font-size:15px; color:var(--text); resize:none; outline:none; font-family:inherit; }
        .ns-toolbar { display:flex; gap:8px; margin-top:12px; align-items:center; }
        .ns-mode-btn { display:flex; align-items:center; gap:5px; background:var(--bg2); border:1px solid var(--border); border-radius:6px; padding:6px 10px; font-size:12px; font-weight:600; color:var(--text2); cursor:pointer; transition:all .2s; }
        .ns-mode-btn.active { background:rgba(99,102,241,.1); border-color:var(--accent); color:var(--accent); }
        .ns-submit-btn { margin-left:auto; display:flex; align-items:center; gap:6px; background:var(--accent); color:#fff; border:none; border-radius:6px; padding:7px 14px; font-size:13px; font-weight:600; cursor:pointer; transition:all .2s; box-shadow:var(--shadow-sm); }
        .ns-submit-btn:disabled { opacity:0.5; cursor:not-allowed; }
        .ns-fixed-bottom { position: sticky; bottom: 0; padding: 20px 0 30px; background: linear-gradient(to top, var(--bg) 80%, transparent); display: flex; justify-content: center; z-index: 100; pointer-events: none; margin-top: auto; }
        .ns-fixed-bottom > * { pointer-events: auto; }
        .ns-stop-btn { display:flex; align-items:center; gap:6px; padding:6px 14px; border-radius:20px; border:1px solid var(--border); background:var(--white); color:var(--text); font-size:13px; font-weight:600; cursor:pointer; transition:all .2s; box-shadow:var(--shadow-sm); margin: 0 auto 16px; }
        .ns-stop-btn:hover { background:var(--bg2); border-color:var(--border2); }
      `}</style>

      {/* Back */}
      <Link href="/dashboard/web-search" style={{display:"inline-flex",alignItems:"center",gap:6,color:"var(--text2)",fontSize:13,textDecoration:"none",marginBottom:24}}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14}}><path d="M19 12H5M12 5l-7 7 7 7"/></svg>
        All Searches
      </Link>

      {/* Header */}
      <div style={{marginBottom:28}}>
        <div style={{display:"flex",alignItems:"flex-start",gap:12,marginBottom:12}}>
          <h1 style={{fontSize:22,fontWeight:700,color:"var(--text)",margin:0,lineHeight:1.4,flex:1}}>{data.query.split(/\n\|FOLLOWUP\|\n/)[0]}</h1>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
          <span style={{fontSize:12,fontWeight:700,color:modeColor,background:`${modeColor}15`,border:`1px solid ${modeColor}30`,padding:"3px 10px",borderRadius:20,textTransform:"uppercase",letterSpacing:.4}}>
            {MODE_LABEL[data.mode] || data.mode}
          </span>
          <span style={{fontSize:12,color:data.status==="completed"?"var(--green)":"var(--text3)",background:data.status==="completed"?"var(--green-light)":"var(--bg2)",border:`1px solid ${data.status==="completed"?"var(--green)":"var(--border)"}`,padding:"3px 10px",borderRadius:20}}>
            {data.status === "completed" ? "✓ Completed" : data.status}
          </span>
          <span style={{fontSize:12,color:"var(--text3)",marginLeft:"auto"}}>{formatDate(data.created_at)}</span>
        </div>
      </div>

      <div style={{height:1,background:"var(--border)",marginBottom:28}}/>

      {/* Steps toggle */}
      {data.reasoning_steps && data.reasoning_steps.length > 0 && (
        <div style={{marginBottom:20}}>
          <button
            onClick={() => setShowSteps(!showSteps)}
            style={{display:"flex",alignItems:"center",gap:8,background:"var(--bg)",border:"1px solid var(--border)",borderRadius:8,padding:"10px 14px",cursor:"pointer",width:"100%",fontSize:13,color:"var(--text2)",fontWeight:500,transition:"all .2s"}}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" style={{width:14,height:14,flexShrink:0}}>
              <path d={showSteps ? "M18 15l-6-6-6 6" : "M6 9l6 6 6-6"}/>
            </svg>
            {showSteps ? "Hide" : "Show"} research steps ({data.reasoning_steps.length})
          </button>
          {showSteps && (
            <div style={{marginTop:10}}>
              {data.reasoning_steps.map((s: ReasoningStep, i: number) => (
                <div key={i} className="sv-step">
                  <div style={{width:6,height:6,background:"var(--accent)",borderRadius:"50%",flexShrink:0,marginTop:5,boxShadow:"0 0 6px var(--accent-light)"}}/>
                  <div>
                    <div style={{fontSize:13,color:"var(--text)",fontWeight:500}}>{s.step}</div>
                    {s.detail && <div style={{fontSize:11,color:"var(--text2)",marginTop:2}}>{s.detail}</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Chat Flow */}
      <div style={{display: "flex", flexDirection: "column", gap: 32, marginBottom: 24}}>
        
        {/* Existing Answers from Database */}
        {data.full_answer && data.full_answer.split(/\n\n---\n\n/).map((answerPart: string, index: number) => {
          const queryParts = data.query.split(/\n\|FOLLOWUP\|\n/);
          const userQuery = queryParts[index] || queryParts[queryParts.length - 1];
          const turnCitations = (data.citations ?? []).filter((c: Citation) => (c.turn_index ?? 0) === index);
          const usedCits = turnCitations.filter((c: Citation) => (c.relevance_score ?? 0) >= 0);
          const unusedCits = turnCitations.filter((c: Citation) => (c.relevance_score ?? 0) < 0);

          return (
            <div key={index} style={{display: "flex", flexDirection: "column", gap: 16}}>
              {index > 0 && (
                <div style={{alignSelf: "flex-end", background: "var(--accent)", color: "#fff", padding: "12px 18px", borderRadius: "18px 18px 0 18px", fontSize: 15, fontWeight: 500, maxWidth: "80%", boxShadow: "var(--shadow-sm)", lineHeight: 1.5}}>
                  {userQuery}
                </div>
              )}
              <div
                className="sv-answer"
                style={{fontSize:14,color:"var(--text)",lineHeight:1.8, background:"var(--white)",border:"1px solid var(--border)",borderRadius:index===0?14:"0 18px 18px 18px",padding:28,boxShadow:"var(--shadow-sm)"}}
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
              
              {/* Turn Citations */}
              {(usedCits.length > 0 || unusedCits.length > 0) && (
                <div style={{marginTop: 8}}>
                  {usedCits.length > 0 && (
                    <div style={{marginBottom: unusedCits.length > 0 ? 16 : 0}}>
                      <button 
                        onClick={() => setShowUsedSources(!showUsedSources)}
                        style={{display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", color: "var(--text)", fontSize: 14, fontWeight: 600, cursor: "pointer", padding: 0, marginBottom: showUsedSources ? 12 : 0}}
                      >
                        Sources used in this response
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width: 14, height: 14, transform: showUsedSources ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s"}}><path d="M6 9l6 6 6-6"/></svg>
                      </button>
                      
                      {showUsedSources && (
                        <div style={{display: "flex", flexDirection: "column", gap: 8}}>
                          {usedCits.map((c: Citation, i: number) => (
                            <div key={`used-${i}`} id={`cit-${index}-${i+1}`} className="sv-cit" style={{margin: 0, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 16px"}}>
                              <div style={{display:"flex",alignItems:"flex-start",gap:10}}>
                                <div style={{flex:1,fontSize:13,fontWeight:600,color:"var(--text)",lineHeight:1.4}}>
                                  <img src={`https://www.google.com/s2/favicons?domain=${c.domain}&sz=32`} style={{width: 14, height: 14, marginRight: 6, verticalAlign: "middle", borderRadius: 2}} />
                                  <span style={{color: "var(--text2)", marginRight: 8}}>{c.domain}</span>
                                  {c.source_name}
                                </div>
                                <a href={c.url} target="_blank" rel="noopener noreferrer" style={{color:"var(--accent)",fontSize:12,textDecoration:"none",whiteSpace:"nowrap"}}>Open ↗</a>
                              </div>
                              {c.snippet && (
                                <div style={{marginTop: 8}}>
                                  <div onClick={()=>setExpandedCit(expandedCit===`${index}-${i}`?null:`${index}-${i}`)} style={{fontSize:12,color:"var(--text2)",cursor:"pointer",display:"inline-flex",alignItems:"center",gap:4}}>
                                    {expandedCit===`${index}-${i}`?"▲ Hide":"▼ Show"} excerpt
                                  </div>
                                  {expandedCit===`${index}-${i}` && (
                                    <div style={{fontSize:12,color:"var(--text)",lineHeight:1.6,marginTop:8,padding:"10px 12px",background:"var(--white)",borderRadius:6,borderLeft:"2px solid var(--accent)"}}>
                                      {c.snippet}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {unusedCits.length > 0 && (
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
                          {unusedCits.map((c: Citation, i: number) => (
                            <div key={`unused-${i}`} className="sv-cit" style={{margin: 0, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 16px"}}>
                              <div style={{display:"flex",alignItems:"flex-start",gap:10}}>
                                <div style={{flex:1,fontSize:13,fontWeight:600,color:"var(--text)",lineHeight:1.4}}>
                                  <img src={`https://www.google.com/s2/favicons?domain=${c.domain}&sz=32`} style={{width: 14, height: 14, marginRight: 6, verticalAlign: "middle", borderRadius: 2}} />
                                  <span style={{color: "var(--text2)", marginRight: 8}}>{c.domain}</span>
                                  {c.source_name}
                                </div>
                                <a href={c.url} target="_blank" rel="noopener noreferrer" style={{color:"var(--accent)",fontSize:12,textDecoration:"none",whiteSpace:"nowrap"}}>Open ↗</a>
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* Live Active Follow Up Context */}
        {(isSearching || activePrompt) && (
          <div style={{display: "flex", flexDirection: "column", gap: 16}}>
            {/* User prompt for the current follow up */}
            {activePrompt && (
              <div style={{alignSelf: "flex-end", background: "var(--accent)", color: "#fff", padding: "12px 18px", borderRadius: "18px 18px 0 18px", fontSize: 15, fontWeight: 500, maxWidth: "80%", boxShadow: "var(--shadow-sm)", lineHeight: 1.5}}>
                {activePrompt}
              </div>
            )}
            
            {/* AI Streaming Answer / Loading */}
            <div className="sv-answer" style={{alignSelf: "flex-start", width: "100%", background:"var(--white)",border:"1px solid var(--border)",borderRadius:"0 18px 18px 18px",padding:28,boxShadow:"var(--shadow-sm)"}}>
              {/* Thinking Steps */}
              {followUpSteps.length > 0 && (
                <div style={{marginBottom: followUpAnswer ? 20 : 0}}>
                  <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:12}}>
                    <div style={{fontSize:14,fontWeight:600,color:"var(--accent)"}}>Researching</div>
                    {isSearching && (
                      <div className="ns-dots">
                        <span/><span/><span/>
                      </div>
                    )}
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:6}}>
                    {followUpSteps.map((s, i) => (
                      <div key={i} className="sv-step">
                        <div style={{width:6,height:6,background:"var(--accent)",borderRadius:"50%",flexShrink:0,marginTop:5,boxShadow:"0 0 6px var(--accent-light)"}}/>
                        <div>
                          <div style={{fontSize:13,color:"var(--text)",fontWeight:500}}>{s.step}</div>
                          {s.detail && <div style={{fontSize:11,color:"var(--text2)",marginTop:2}}>{s.detail}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              
              {/* Active Streaming Answer */}
              {followUpAnswer && (
                <div>
                  <div
                    style={{fontSize:14,color:"var(--text)",lineHeight:1.8}}
                    dangerouslySetInnerHTML={{
                      __html: followUpAnswer
                        .replace(/## (.+)/g, "<h2>$1</h2>")
                        .replace(/### (.+)/g, "<h3>$1</h3>")
                        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
                        .replace(/\*(.+?)\*/g, "<em>$1</em>")
                        .replace(/\[(\d+)\]/g, (match, p1) => {
                          const idx = parseInt(p1, 10) - 1;
                          const cits = followUpCitations ?? [];
                          const url = cits[idx]?.url || `#cit-stream-${p1}`;
                          const target = cits[idx]?.url ? "_blank" : "_self";
                          return `<sup style='margin-left:2px'><a href='${url}' target='${target}' rel='noopener noreferrer' style='color:var(--accent);text-decoration:none;font-weight:700;'>[${p1}]</a></sup>`;
                        })
                        .replace(/\n/g, "<br/>")
                    }}
                  />

                  {/* Streaming Turn Citations */}
                  {(followUpCitations.length > 0 || followUpReadButNotUsed.length > 0) && (
                    <div style={{marginTop: 8}}>
                      {followUpCitations.length > 0 && (
                        <div style={{marginBottom: followUpReadButNotUsed.length > 0 ? 16 : 0}}>
                          <button 
                            onClick={() => setShowUsedSources(!showUsedSources)}
                            style={{display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", color: "var(--text)", fontSize: 14, fontWeight: 600, cursor: "pointer", padding: 0, marginBottom: showUsedSources ? 12 : 0}}
                          >
                            Sources used in this response
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width: 14, height: 14, transform: showUsedSources ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s"}}><path d="M6 9l6 6 6-6"/></svg>
                          </button>
                          
                          {showUsedSources && (
                            <div style={{display: "flex", flexDirection: "column", gap: 8}}>
                              {followUpCitations.map((c: Citation, i: number) => (
                                <div key={`stream-used-${i}`} id={`cit-stream-${i+1}`} className="sv-cit" style={{margin: 0, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 16px"}}>
                                  <div style={{display:"flex",alignItems:"flex-start",gap:10}}>
                                    <div style={{flex:1,fontSize:13,fontWeight:600,color:"var(--text)",lineHeight:1.4}}>
                                      <img src={`https://www.google.com/s2/favicons?domain=${c.domain}&sz=32`} style={{width: 14, height: 14, marginRight: 6, verticalAlign: "middle", borderRadius: 2}} />
                                      <span style={{color: "var(--text2)", marginRight: 8}}>{c.domain}</span>
                                      {c.source_name}
                                    </div>
                                    <a href={c.url} target="_blank" rel="noopener noreferrer" style={{color:"var(--accent)",fontSize:12,textDecoration:"none",whiteSpace:"nowrap"}}>Open ↗</a>
                                  </div>
                                  {c.snippet && (
                                    <div style={{marginTop: 8}}>
                                      <div onClick={()=>setExpandedCit(expandedCit===`stream-${i}`?null:`stream-${i}`)} style={{fontSize:12,color:"var(--text2)",cursor:"pointer",display:"inline-flex",alignItems:"center",gap:4}}>
                                        {expandedCit===`stream-${i}`?"▲ Hide":"▼ Show"} excerpt
                                      </div>
                                      {expandedCit===`stream-${i}` && (
                                        <div style={{fontSize:12,color:"var(--text)",lineHeight:1.6,marginTop:8,padding:"10px 12px",background:"var(--white)",borderRadius:6,borderLeft:"2px solid var(--accent)"}}>
                                          {c.snippet}
                                        </div>
                                      )}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {followUpReadButNotUsed.length > 0 && (
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
                              {followUpReadButNotUsed.map((c: Citation, i: number) => (
                                <div key={`stream-unused-${i}`} className="sv-cit" style={{margin: 0, background: "var(--bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 16px"}}>
                                  <div style={{display:"flex",alignItems:"flex-start",gap:10}}>
                                    <div style={{flex:1,fontSize:13,fontWeight:600,color:"var(--text)",lineHeight:1.4}}>
                                      <img src={`https://www.google.com/s2/favicons?domain=${c.domain}&sz=32`} style={{width: 14, height: 14, marginRight: 6, verticalAlign: "middle", borderRadius: 2}} />
                                      <span style={{color: "var(--text2)", marginRight: 8}}>{c.domain}</span>
                                      {c.source_name}
                                    </div>
                                    <a href={c.url} target="_blank" rel="noopener noreferrer" style={{color:"var(--accent)",fontSize:12,textDecoration:"none",whiteSpace:"nowrap"}}>Open ↗</a>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Spacer to prevent content from being hidden behind fixed chatbox */}
      <div style={{height: 140}} />

      {/* Fixed bottom chatbox */}
      <div className="ns-fixed-bottom">
        <div style={{width: "100%", maxWidth: 812, display: "flex", flexDirection: "column", alignItems: "center"}}>
          {isSearching && (
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
                if (!isSearching) setFollowUp(e.target.value);
              }}
              onKeyDown={handleKeyDown}
              rows={1}
              readOnly={isSearching}
            />
            <div className="ns-toolbar">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  className={`ns-mode-btn ${followUpMode === m.id ? "active" : ""}`}
                  onClick={() => { if (!isSearching) setFollowUpMode(m.id); }}
                  title={m.desc}
                  style={{opacity: isSearching?0.5:1}}
                >
                  <span>{m.icon}</span>{m.label}
                </button>
              ))}
              <button
                className="ns-submit-btn"
                onClick={isSearching ? undefined : handleFollowUpSubmit}
                disabled={!followUp.trim() || isSearching}
              >
                {isSearching ? (
                  <>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{width:14,height:14,animation:"nsdot 1s linear infinite"}}>
                      <circle cx="12" cy="12" r="10"/>
                    </svg>
                    Searching…
                  </>
                ) : (
                  <>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{width:14,height:14}}>
                      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
                    </svg>
                    Search
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
