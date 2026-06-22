"use client";
import { useState, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter, useParams } from "next/navigation";
import { foldersApi, documentsApi, chatApi } from "@/lib/api";
import { toast } from "sonner";
import {
  Loader2, FileText, X, ChevronDown, CheckCircle,
  Sparkles, Square, MessageSquare, Database, Plus, ArrowUp, ArrowLeft,
} from "lucide-react";

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceRef[];
  created_at: string;
}
interface SourceRef {
  document_name: string; folder_name: string; page_number: number;
  section?: string; clause_number?: string; snippet: string; relevance_score: number;
}
interface DocItem { id: string; name: string; folder_name: string; }

// ─────────────────────────────────────────────
// Markdown renderer (tables + bold)
// ─────────────────────────────────────────────

function escapeHtml(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inlineMd(s: string) {
  return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}
function renderMarkdown(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    if (trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|")) {
      const tbl: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|") && lines[i].trim().endsWith("|")) {
        tbl.push(lines[i].trim()); i++;
      }
      const data = tbl.filter(l => !/^\|[\s\-:]+\|$/.test(l));
      if (data.length) {
        const pr = (r: string) => r.replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
        const hdr = pr(data[0]);
        const body = data.slice(1).map(pr);
        let t = `<table style="width:100%;border-collapse:collapse;margin:12px 0 16px;font-size:13.5px;">`;
        t += `<thead><tr>${hdr.map(h => `<th style="text-align:left;padding:10px 14px;background:#5b4fcf;color:#fff;font-weight:600;border:1px solid #4a40b0;">${inlineMd(h)}</th>`).join("")}</tr></thead>`;
        t += `<tbody>${body.map((cells, ri) => `<tr>${hdr.map((_, ci) => `<td style="padding:9px 14px;border:1px solid #e8e6f0;background:${ri%2===0?"#faf9ff":"#fff"};vertical-align:top;">${inlineMd(cells[ci]??'')}</td>`).join("")}</tr>`).join("")}</tbody>`;
        t += `</table>`;
        out.push(t);
      }
    } else {
      out.push(inlineMd(escapeHtml(lines[i]))); i++;
    }
  }
  return out.join("\n");
}

const THINKING_STEPS = [
  "Reading documents",
  "Analyzing structure",
  "Selecting relevant clauses",
  "Synthesizing legal reasoning",
  "Generating response",
];

// ─────────────────────────────────────────────
// Session Page (loads existing messages)
// ─────────────────────────────────────────────

export default function IntelligenceSessionPage() {
  const router = useRouter();
  const params = useParams();
  const sessionId = params.session_id as string;

  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [improving, setImproving] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState<DocItem[]>([]);
  const [selectedFolderIds, setSelectedFolderIds] = useState<string[]>([]);
  const [vaultOpen, setVaultOpen] = useState(false);
  const [plusOpen, setPlusOpen] = useState(false);
  const [thinkingStep, setThinkingStep] = useState(0);
  const [sessionTitle, setSessionTitle] = useState("Document Intelligence");
  const plusRef = useRef<HTMLDivElement>(null);
  const abortedRef = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const thinkingTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data: folders = [] } = useQuery({
    queryKey: ["folders"],
    queryFn: () => foldersApi.list().then(r => r.data),
  });

  // Load existing messages on mount
  useEffect(() => {
    if (!sessionId) return;
    (async () => {
      try {
        setLoadingMessages(true);
        const res = await chatApi.getMessages(sessionId);
        setMessages(res.data);
        // Derive title from first user message
        const firstUser = (res.data as Message[]).find(m => m.role === "user");
        if (firstUser) setSessionTitle(firstUser.content.slice(0, 60));
      } catch {
        toast.error("Failed to load session");
        router.push("/dashboard/intelligence");
      } finally {
        setLoadingMessages(false);
      }
    })();
  }, [sessionId]);

  // Close the + popover when clicking outside
  useEffect(() => {
    if (!plusOpen) return;
    const handler = (e: MouseEvent) => {
      if (plusRef.current && !plusRef.current.contains(e.target as Node)) setPlusOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [plusOpen]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending, loadingMessages]);

  useEffect(() => {
    if (sending) {
      setThinkingStep(0);
      thinkingTimer.current = setInterval(() => {
        setThinkingStep(s => Math.min(s + 1, THINKING_STEPS.length - 1));
      }, 2500);
    } else {
      if (thinkingTimer.current) { clearInterval(thinkingTimer.current); thinkingTimer.current = null; }
    }
    return () => { if (thinkingTimer.current) clearInterval(thinkingTimer.current); };
  }, [sending]);

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };

  const submit = async () => {
    if (!input.trim() || sending) return;
    const q = input.trim();
    setInput("");
    setTimeout(autoResize, 0);
    setSending(true);
    abortedRef.current = false;

    setMessages(prev => [...prev, {
      id: `u-${Date.now()}`, role: "user", content: q,
      created_at: new Date().toISOString(),
    }]);

    try {
      const res = await chatApi.sendMessage(sessionId, q);
      if (!abortedRef.current) setMessages(prev => [...prev, res.data]);
    } catch (err: any) {
      if (!abortedRef.current) toast.error(err?.response?.data?.detail ?? "Reasoning failed");
    } finally {
      setSending(false);
    }
  };

  const stop = () => { abortedRef.current = true; setSending(false); };

  const improve = async () => {
    if (improving) { setImproving(false); return; }
    if (!input.trim()) return;
    setImproving(true);
    try {
      const res = await chatApi.improvePrompt(input.trim());
      setInput(res.data.improved);
      setTimeout(autoResize, 0);
    } catch { toast.error("Failed to improve prompt"); }
    finally { setImproving(false); }
  };

  const folderName = (id: string) => folders.find((f: any) => f.id === id)?.name ?? id;
  const hasContext = selectedDocs.length > 0 || selectedFolderIds.length > 0;
  const contextCount = selectedDocs.length + selectedFolderIds.length;

  return (
    <>
      {vaultOpen && (
        <VaultModal
          folders={folders}
          selectedDocs={selectedDocs}
          selectedFolderIds={selectedFolderIds}
          onToggleDoc={doc =>
            setSelectedDocs(prev =>
              prev.find(d => d.id === doc.id)
                ? prev.filter(d => d.id !== doc.id)
                : [...prev, doc]
            )
          }
          onToggleFolder={id =>
            setSelectedFolderIds(prev =>
              prev.includes(id) ? prev.filter(f => f !== id) : [...prev, id]
            )
          }
          onClose={() => setVaultOpen(false)}
        />
      )}

      <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 48px)", maxWidth: "1100px", margin: "-20px auto -48px" }}>

        {/* Top bar with back button and session title */}
        <div style={{
          padding: "14px 32px 10px",
          flexShrink: 0,
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 14,
          background: "var(--white)",
        }}>
          <button
            onClick={() => router.push("/dashboard/intelligence")}
            style={{
              display: "flex", alignItems: "center", gap: 6,
              background: "none", border: "none", cursor: "pointer",
              color: "var(--text2)", fontSize: 13, fontWeight: 600,
              padding: "4px 8px 4px 0",
              flexShrink: 0,
            }}
          >
            <ArrowLeft size={14} /> All analyses
          </button>
          <div style={{ width: 1, height: 18, background: "var(--border)", flexShrink: 0 }} />
          <span style={{
            fontSize: 14, fontWeight: 600, color: "var(--text)",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}>
            {loadingMessages ? "Loading…" : sessionTitle}
          </span>
        </div>

        {/* ── Messages ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: "24px 32px 16px" }}>
          {loadingMessages ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              {[1, 2, 3].map(i => (
                <div key={i} style={{ animation: "pulse 1.5s ease infinite" }}>
                  <div style={{ height: 12, background: "var(--bg2)", borderRadius: 6, marginBottom: 10, width: i === 1 ? "45%" : i === 2 ? "80%" : "60%" }} />
                  <div style={{ height: 12, background: "var(--bg2)", borderRadius: 6, width: i === 1 ? "70%" : i === 2 ? "55%" : "75%" }} />
                </div>
              ))}
            </div>
          ) : messages.length === 0 ? (
            <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 16 }}>
              <div style={{ width: 60, height: 60, background: "var(--accent-light)", borderRadius: 16, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <MessageSquare size={30} style={{ color: "var(--accent)" }} />
              </div>
              <p style={{ color: "var(--text2)", fontSize: 14, margin: 0 }}>Ask your first question to continue this analysis</p>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "44px" }}>
              {messages.map((msg, i) => (
                <div key={i} className="fade-in">
                  <div style={{ fontSize: "10.5px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.12em", color: "var(--text3)", marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
                    <div style={{ width: "20px", height: "1px", background: "var(--border)" }} />
                    {msg.role === "user" ? "Query" : "Verified Reasoning"}
                  </div>
                  <div
                    style={{ fontSize: "15px", lineHeight: "1.85", color: "var(--text)", background: msg.role === "user" ? "var(--bg)" : "transparent", padding: msg.role === "user" ? "16px 20px" : "0", borderRadius: "10px", whiteSpace: "pre-wrap" }}
                    {...(msg.role === "assistant"
                      ? { dangerouslySetInnerHTML: { __html: renderMarkdown(msg.content) } }
                      : { children: msg.content }
                    )}
                  />
                  {msg.sources && msg.sources.length > 0 && (
                    <div style={{ marginTop: "24px" }}>
                      <div style={{ fontSize: "10px", fontWeight: 700, color: "var(--text3)", marginBottom: "12px", textTransform: "uppercase", letterSpacing: "0.1em" }}>
                        Sources Referenced ({msg.sources.length})
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: "10px" }}>
                        {msg.sources.map((src, si) => (
                          <div key={si} style={{ padding: "12px 14px", border: "1px solid var(--border)", borderRadius: "10px", background: "var(--white)" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "6px" }}>
                              <FileText size={12} style={{ color: "var(--accent)", flexShrink: 0 }} />
                              <span style={{ fontWeight: 700, fontSize: "12px" }}>{src.document_name}</span>
                            </div>
                            <div style={{ fontSize: "11.5px", color: "var(--text2)", fontStyle: "italic", lineHeight: "1.5", borderLeft: "2px solid var(--accent-light)", paddingLeft: "8px" }}>
                              &ldquo;{src.snippet.slice(0, 120)}...&rdquo;
                            </div>
                            <div style={{ fontSize: "10px", color: "var(--text3)", marginTop: "8px", fontWeight: 700, textTransform: "uppercase" }}>
                              Pg {src.page_number} &bull; {Math.round(src.relevance_score * 100)}% match
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}

              {sending && (
                <div className="fade-in" style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <Loader2 size={15} className="animate-spin" style={{ color: "var(--accent)" }} />
                    <span style={{ fontSize: "14px", fontWeight: 700, color: "var(--accent)" }}>
                      Thinking<span className="thinking-dots" />
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px", paddingLeft: "25px" }}>
                    {THINKING_STEPS.map((step, si) => (
                      <div key={si} style={{ display: "flex", alignItems: "center", gap: "8px", opacity: si <= thinkingStep ? 1 : 0.28, transition: "opacity 0.5s" }}>
                        {si < thinkingStep
                          ? <CheckCircle size={13} style={{ color: "#22c55e", flexShrink: 0 }} />
                          : si === thinkingStep
                            ? <Loader2 size={13} className="animate-spin" style={{ color: "var(--accent)", flexShrink: 0 }} />
                            : <div style={{ width: 13, height: 13, borderRadius: "50%", border: "1.5px solid var(--border)", flexShrink: 0 }} />
                        }
                        <span style={{ fontSize: "13px", color: si <= thinkingStep ? "var(--text)" : "var(--text3)", fontWeight: si === thinkingStep ? 600 : 400 }}>
                          {step}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* ── Pill input bar ── */}
        <div style={{ padding: "0 20px 20px", flexShrink: 0 }}>

          {/* Context chips */}
          {hasContext && (
            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "8px", flexWrap: "wrap" }}>
              {selectedFolderIds.map(fid => (
                <span key={fid} style={{ display: "inline-flex", alignItems: "center", gap: "5px", background: "var(--accent-light)", color: "var(--accent)", borderRadius: "20px", padding: "4px 10px", fontSize: "12px", fontWeight: 600, border: "1px solid rgba(79,70,229,0.2)" }}>
                  <Database size={11} /> {folderName(fid)}
                  <button onClick={() => setSelectedFolderIds(p => p.filter(f => f !== fid))} disabled={sending} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, lineHeight: 1, color: "var(--accent)", display: "flex", alignItems: "center", marginLeft: "2px" }}>
                    <X size={11} />
                  </button>
                </span>
              ))}
              {selectedDocs.map(doc => (
                <span key={doc.id} style={{ display: "inline-flex", alignItems: "center", gap: "5px", background: "var(--bg2)", color: "var(--text)", borderRadius: "20px", padding: "4px 10px", fontSize: "12px", fontWeight: 500, border: "1px solid var(--border)" }}>
                  <FileText size={11} style={{ color: "var(--text2)" }} /> {doc.name}
                  <button onClick={() => setSelectedDocs(p => p.filter(d => d.id !== doc.id))} disabled={sending} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, lineHeight: 1, color: "var(--text3)", display: "flex", alignItems: "center", marginLeft: "2px" }}>
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* Pill bar */}
          <div style={{
            display: "flex", alignItems: "center", gap: "8px",
            background: "var(--white)", border: "1.5px solid var(--border)",
            borderRadius: "28px", padding: "8px 10px",
            boxShadow: "0 4px 24px rgba(0,0,0,0.07), 0 1px 4px rgba(0,0,0,0.04)",
          }}>
            {/* + button */}
            <div ref={plusRef} style={{ position: "relative", flexShrink: 0 }}>
              <button
                onClick={() => setPlusOpen(o => !o)}
                disabled={sending}
                style={{
                  width: "34px", height: "34px", borderRadius: "50%",
                  border: "1.5px solid var(--border)",
                  background: plusOpen ? "var(--text)" : "var(--bg)",
                  color: plusOpen ? "var(--white)" : "var(--text2)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  cursor: sending ? "default" : "pointer", transition: "all 0.18s", padding: 0,
                }}
              >
                <Plus size={16} style={{ transition: "transform 0.25s cubic-bezier(0.4,0,0.2,1)", transform: plusOpen ? "rotate(45deg)" : "rotate(0deg)" }} />
                {hasContext && !plusOpen && (
                  <span style={{ position: "absolute", top: "-3px", right: "-3px", width: "14px", height: "14px", borderRadius: "50%", background: "var(--accent)", color: "#fff", fontSize: "8px", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", border: "1.5px solid var(--white)" }}>
                    {contextCount}
                  </span>
                )}
              </button>

              {plusOpen && (
                <div style={{ position: "absolute", bottom: "calc(100% + 10px)", left: 0, background: "var(--white)", border: "1px solid var(--border)", borderRadius: "14px", boxShadow: "0 8px 32px rgba(0,0,0,0.14)", padding: "6px", minWidth: "180px", zIndex: 50 }}>
                  <button
                    onClick={() => { setVaultOpen(true); setPlusOpen(false); }}
                    style={{ display: "flex", alignItems: "center", gap: "10px", width: "100%", padding: "9px 12px", borderRadius: "9px", border: "none", background: hasContext ? "var(--accent-light)" : "transparent", color: hasContext ? "var(--accent)" : "var(--text)", fontSize: "13px", fontWeight: 600, cursor: "pointer", textAlign: "left" }}
                    onMouseEnter={e => { if (!hasContext) (e.currentTarget as HTMLButtonElement).style.background = "var(--bg)"; }}
                    onMouseLeave={e => { if (!hasContext) (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
                  >
                    <span style={{ width: 28, height: 28, borderRadius: "8px", background: hasContext ? "rgba(79,70,229,0.12)" : "var(--bg2)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                      <Database size={14} style={{ color: hasContext ? "var(--accent)" : "var(--text2)" }} />
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: "13px", fontWeight: 600 }}>Choose Vault</div>
                      <div style={{ fontSize: "11px", color: "var(--text3)", marginTop: "1px" }}>{hasContext ? `${contextCount} selected` : "Select documents"}</div>
                    </div>
                    {hasContext && <span style={{ background: "var(--accent)", color: "#fff", borderRadius: "10px", padding: "1px 7px", fontSize: "10px", fontWeight: 700 }}>{contextCount}</span>}
                  </button>

                  <div style={{ height: "1px", background: "var(--border)", margin: "4px 6px" }} />

                  <button
                    onClick={() => { improve(); setPlusOpen(false); }}
                    disabled={!input.trim() || improving}
                    style={{ display: "flex", alignItems: "center", gap: "10px", width: "100%", padding: "9px 12px", borderRadius: "9px", border: "none", background: "transparent", color: (!input.trim() || improving) ? "var(--text3)" : "var(--text)", fontSize: "13px", fontWeight: 600, cursor: (!input.trim() || improving) ? "default" : "pointer", textAlign: "left", opacity: (!input.trim() || improving) ? 0.5 : 1 }}
                    onMouseEnter={e => { if (input.trim() && !improving) (e.currentTarget as HTMLButtonElement).style.background = "var(--bg)"; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}
                  >
                    <span style={{ width: 28, height: 28, borderRadius: "8px", background: improving ? "var(--accent-light)" : "var(--bg2)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                      {improving ? <Loader2 size={14} className="animate-spin" style={{ color: "var(--accent)" }} /> : <Sparkles size={14} style={{ color: "var(--text2)" }} />}
                    </span>
                    <div>
                      <div style={{ fontSize: "13px", fontWeight: 600 }}>Improve Prompt</div>
                      <div style={{ fontSize: "11px", color: "var(--text3)", marginTop: "1px" }}>Rewrite with AI</div>
                    </div>
                  </button>
                </div>
              )}
            </div>

            <textarea
              ref={textareaRef}
              value={input}
              onChange={e => { setInput(e.target.value); autoResize(); }}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
              disabled={sending || loadingMessages}
              placeholder={loadingMessages ? "Loading session…" : "Ask a follow-up question…"}
              style={{ flex: 1, border: "none", outline: "none", resize: "none", padding: "7px 4px", fontSize: "15px", lineHeight: "1.6", background: "transparent", color: "var(--text)", minHeight: "34px", maxHeight: "200px", boxSizing: "border-box", fontFamily: "inherit" }}
            />

            {sending ? (
              <button onClick={stop} style={{ width: "34px", height: "34px", borderRadius: "50%", border: "none", background: "var(--text)", color: "var(--white)", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flexShrink: 0, padding: 0 }}>
                <Square size={13} fill="currentColor" />
              </button>
            ) : input.trim() ? (
              <button onClick={submit} style={{ width: "34px", height: "34px", borderRadius: "50%", border: "none", background: "linear-gradient(135deg, var(--accent) 0%, #6366f1 100%)", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flexShrink: 0, boxShadow: "0 3px 12px rgba(79,70,229,0.35)", padding: 0 }}>
                <ArrowUp size={17} strokeWidth={2.5} />
              </button>
            ) : null}
          </div>

          <p style={{ textAlign: "center", fontSize: "11px", color: "var(--text3)", margin: "8px 0 0" }}>
            {hasContext ? `${contextCount} vault item(s) in context · Shift+Enter for new line` : "Shift+Enter for new line"}
          </p>
        </div>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────
// Vault Modal (same as new page)
// ─────────────────────────────────────────────

interface VaultModalProps {
  folders: any[];
  selectedDocs: DocItem[];
  selectedFolderIds: string[];
  onToggleDoc: (doc: DocItem) => void;
  onToggleFolder: (id: string) => void;
  onClose: () => void;
}

function VaultModal({ folders, selectedDocs, selectedFolderIds, onToggleDoc, onToggleFolder, onClose }: VaultModalProps) {
  const [expanded, setExpanded] = useState<string[]>([]);
  const selCount = selectedDocs.length + selectedFolderIds.length;

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.45)", backdropFilter: "blur(4px)", display: "flex", alignItems: "center", justifyContent: "center" }} onClick={onClose}>
      <div style={{ width: "520px", maxHeight: "68vh", background: "var(--white)", borderRadius: "18px", boxShadow: "0 24px 80px rgba(0,0,0,0.18)", display: "flex", flexDirection: "column", overflow: "hidden" }} onClick={e => e.stopPropagation()}>
        <div style={{ padding: "22px 26px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <h2 style={{ fontSize: "16px", fontWeight: 700, margin: "0 0 4px" }}>Choose Vault</h2>
            <p style={{ fontSize: "12px", color: "var(--text2)", margin: 0 }}>Select files or entire folders to use as context</p>
          </div>
          <button onClick={onClose} style={{ border: "none", background: "var(--bg)", borderRadius: "8px", width: "32px", height: "32px", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flexShrink: 0 }}>
            <X size={15} />
          </button>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "10px 14px" }}>
          {folders.length === 0 ? (
            <div style={{ padding: "40px", textAlign: "center", color: "var(--text3)" }}>No folders found. Upload documents in the Vault tab first.</div>
          ) : folders.map((f: any) => (
            <div key={f.id} style={{ marginBottom: "3px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "6px", padding: "9px 10px", borderRadius: "10px", background: selectedFolderIds.includes(f.id) ? "var(--accent-light)" : "transparent", transition: "background 0.15s" }}>
                <button onClick={() => setExpanded(p => p.includes(f.id) ? p.filter(e => e !== f.id) : [...p, f.id])} style={{ background: "none", border: "none", cursor: "pointer", padding: "2px", color: "var(--text3)", flexShrink: 0 }}>
                  <ChevronDown size={14} style={{ transform: expanded.includes(f.id) ? "rotate(0)" : "rotate(-90deg)", transition: "transform 0.2s" }} />
                </button>
                <div onClick={() => onToggleFolder(f.id)} style={{ flex: 1, display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                  <Database size={14} style={{ color: selectedFolderIds.includes(f.id) ? "var(--accent)" : "var(--text2)", flexShrink: 0 }} />
                  <span style={{ fontSize: "13.5px", fontWeight: 600, color: selectedFolderIds.includes(f.id) ? "var(--accent)" : "var(--text)" }}>{f.name}</span>
                  <span style={{ fontSize: "11px", color: "var(--text3)" }}>({f.document_count} files)</span>
                </div>
                {selectedFolderIds.includes(f.id) && <CheckCircle size={14} style={{ color: "var(--accent)", flexShrink: 0 }} />}
              </div>
              {expanded.includes(f.id) && (
                <VaultFolderFiles folderId={f.id} folderName={f.name} selectedDocs={selectedDocs} onToggleDoc={onToggleDoc} />
              )}
            </div>
          ))}
        </div>

        <div style={{ padding: "14px 24px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: "13px", color: "var(--text2)" }}>
            {selCount === 0 ? "Nothing selected — all docs will be searched" : `${selCount} item(s) selected`}
          </span>
          <button onClick={onClose} className="btn-accent" style={{ padding: "8px 22px", borderRadius: "9px", fontSize: "13px" }}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

function VaultFolderFiles({ folderId, folderName, selectedDocs, onToggleDoc }: {
  folderId: string; folderName: string; selectedDocs: DocItem[]; onToggleDoc: (doc: DocItem) => void;
}) {
  const { data: docs = [] } = useQuery({
    queryKey: ["docs-in-folder", folderId],
    queryFn: () => documentsApi.listByFolder(folderId).then(r => r.data),
  });

  if (docs.length === 0) return (
    <div style={{ padding: "6px 14px 6px 38px", color: "var(--text3)", fontSize: "12px" }}>No documents in this folder</div>
  );

  return (
    <div style={{ paddingLeft: "30px", paddingBottom: "4px", display: "flex", flexDirection: "column", gap: "2px" }}>
      {docs.map((d: any) => {
        const sel = selectedDocs.some(s => s.id === d.id);
        return (
          <div key={d.id} onClick={() => onToggleDoc({ id: d.id, name: d.name, folder_name: folderName })} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "7px 10px", borderRadius: "8px", background: sel ? "var(--accent-light)" : "transparent", cursor: "pointer", transition: "background 0.15s" }}>
            <FileText size={13} style={{ color: sel ? "var(--accent)" : "var(--text3)", flexShrink: 0 }} />
            <span style={{ fontSize: "13px", fontWeight: sel ? 600 : 400, color: sel ? "var(--accent)" : "var(--text)", flex: 1 }}>{d.name}</span>
            {sel && <CheckCircle size={13} style={{ color: "var(--accent)", flexShrink: 0 }} />}
          </div>
        );
      })}
    </div>
  );
}
