"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { foldersApi, documentsApi, translationsApi } from "@/lib/api";
import { toast } from "sonner";
import { X, Loader2, Folder, ChevronDown, Save, Trash2, CheckCircle, AlertTriangle, Languages, ArrowLeft, FileText, Plus } from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth-store";
import DocumentViewerModal from "@/app/dashboard/folders/DocumentViewerModal";

// ─── HTML-safe section renderer ───────────────────────────────────────────────

function markdownTableToHTML(text: string) {
  const lines = text.trim().split("\n");
  if (lines.length < 2) return text;
  
  const hasPipes = lines.some(l => l.includes("|"));
  const hasSeparator = lines.some(l => /\|[-: ]+\|/.test(l));
  
  if (!hasPipes || !hasSeparator) return text;

  let html = "<table class='markdown-pipe-table'><thead>";
  let inBody = false;
  
  for (const line of lines) {
    if (/\|[-: ]+\|/.test(line)) {
      html += "</thead><tbody>";
      inBody = true;
      continue;
    }
    const cells = line.split("|").filter((_, i, arr) => i > 0 && i < arr.length - 1);
    if (cells.length === 0) continue;
    
    html += "<tr>";
    for (const cell of cells) {
      html += inBody ? `<td>${cell.trim()}</td>` : `<th>${cell.trim()}</th>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";
  return html;
}

function parseMarkdown(text: string) {
  if (!text) return "";
  const clean = text.replace(/[\u25A0-\u25FF\u25CF\u2022\u00B7]/g, "");
  
  let processed = clean;
  if (processed.includes("|")) {
    processed = processed.split("\n\n").map(part => {
      if (part.includes("|") && part.includes("--")) {
        return markdownTableToHTML(part);
      }
      return part;
    }).join("\n\n");
  }

  return processed
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.*?)\*/g, "<em>$1</em>")
    .replace(/^###\s+(.*)$/gm, "<h3 style='font-size:14px;font-weight:700;margin-top:12px;margin-bottom:6px;'>$1</h3>")
    .replace(/^##\s+(.*)$/gm, "<h2 style='font-size:15px;font-weight:700;margin-top:14px;margin-bottom:8px;'>$1</h2>")
    .replace(/^#\s+(.*)$/gm, "<h1 style='font-size:16px;font-weight:700;margin-top:16px;margin-bottom:10px;'>$1</h1>")
    .replace(/\n/g, "<br/>");
}

function BlockRenderer({ block, isTranslated }: { block: any, isTranslated: boolean }) {
  const type = block.type || "paragraph";
  
  let content = "";
  if (type === "html_table" || type === "kv_table" || type === "table") {
    if (isTranslated) {
      // Priority: dedicated HTML table field → translated_content (set by our fix) → original content
      content = block.translated_html_table || block.translated_content || block.markdown_content || block.content || "";
    } else {
      // Original view: show original HTML
      content = block.markdown_content || block.content || "";
    }
  } else {
    content = isTranslated 
      ? (block.translated_content || block.content || "")
      : (block.content || "");
  }

  const notes = isTranslated && block.uncertainty_flags ? block.uncertainty_flags : [];

  if (!content && type !== "page_break") return null;

  let renderedContent;
  switch (type) {
    case "paragraph":
    case "heading":
    case "list":
    case "document_title":
      renderedContent = <div dangerouslySetInnerHTML={{ __html: parseMarkdown(content) }} />;
      break;
    case "html_table":
    case "kv_table":
    case "table":
      // Content is already HTML — inject directly, do NOT run through parseMarkdown
      renderedContent = (
        <div 
          className="html-table-section" 
          dangerouslySetInnerHTML={{ __html: content }} 
        />
      );
      break;
    case "page_break":
      renderedContent = <hr style={{ margin: "24px 0", borderColor: "var(--border)" }} />;
      break;
    case "signatures":
      renderedContent = <div style={{ marginTop: "24px", fontStyle: "italic" }} dangerouslySetInnerHTML={{ __html: parseMarkdown(content) }} />;
      break;
    default:
      console.warn("Unrecognised block type:", type, block);
      renderedContent = <div>{content}</div>;
  }

  return (
    <div style={{ marginBottom: "20px" }}>
      {renderedContent}
      {notes.map((n: string, i: number) => (
        <div key={i} style={{ fontSize: "11px", fontStyle: "italic", color: "var(--amber)", marginTop: "6px" }}>
          Note: {n.replace(/[\u25A0-\u25FF\u25CF\u2022\u00B7]/g, "")}
        </div>
      ))}
    </div>
  );
}

// ─── Constants ────────────────────────────────────────────────────────────────

const ACTIVE_JOB_KEY = "lexai_active_translation_job_id";

const INDIAN_LANGUAGES = [
  "Assamese", "Bengali", "Bodo", "Dogri", "Gujarati", "Hindi",
  "Kannada", "Kashmiri", "Konkani", "Maithili", "Malayalam", "Manipuri",
  "Marathi", "Nepali", "Odia", "Punjabi", "Sanskrit", "Santali",
  "Sindhi", "Tamil", "Telugu", "Urdu",
];
const INTL_LANGUAGES = [
  "English", "French", "German", "Spanish", "Italian", "Portuguese",
  "Dutch", "Japanese", "Chinese (Simplified)", "Arabic", "Russian",
  "Korean", "Turkish", "Vietnamese", "Thai", "Indonesian",
];
const GROUPED_LANGUAGES = [
  { group: "Indian Regional Languages", options: INDIAN_LANGUAGES },
  { group: "International Languages",  options: INTL_LANGUAGES },
];

const STEPS = [
  { key: "detecting",    baseLabel: "Detecting language",           secs: 0   },
  { key: "parsing",      baseLabel: "Parsing document structure",   secs: 12  },
  { key: "translating",  baseLabel: "Translating sections",         secs: 20  },
  { key: "assembling",   baseLabel: "Assembling translated text",   secs: 80  },
];

// ─── Types ────────────────────────────────────────────────────────────────────

interface TranslationJob {
  id: string;
  document_id: string;
  source_language: string;
  target_language: string;
  status: string;
  created_at: string;
  completed_at?: string;
  original_text?: string;
  translated_text?: string;
  structure_map?: string;
  uncertainty_flags?: string;
  error_message?: string;
  saved_storage_key?: string;
  saved_document_id?: string;
  saved_at?: string;
}

// ─── Language Dropdown ────────────────────────────────────────────────────────

function LanguageDropdown({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", userSelect: "none" }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{
          width: "100%", height: "52px", borderRadius: "14px",
          border: open ? "2px solid var(--accent)" : "1.5px solid #d7d1fb",
          background: open ? "var(--accent-light)" : "linear-gradient(180deg,#ffffff 0%,#fcfbff 100%)",
          color: "var(--text)", padding: "0 48px 0 16px", fontSize: "15px",
          fontWeight: 600, textAlign: "left", cursor: "pointer",
          display: "flex", alignItems: "center",
          boxShadow: open ? "0 0 0 4px rgba(91,79,207,0.14)" : "none",
          transition: "border-color 0.18s, box-shadow 0.18s, background 0.18s", outline: "none",
        }}
      >
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</span>
        <span style={{
          position: "absolute", right: "14px", top: "50%",
          transform: `translateY(-50%) rotateZ(${open ? "180deg" : "0deg"})`,
          transition: "transform 0.25s cubic-bezier(0.4,0,0.2,1)",
          color: "var(--accent)", display: "flex", pointerEvents: "none",
        }}>
          <ChevronDown size={20} strokeWidth={2.4} />
        </span>
      </button>

      {open && (
        <div style={{
          position: "absolute", bottom: "calc(100% + 6px)", left: 0, right: 0, zIndex: 200,
          background: "white", border: "1.5px solid #d7d1fb", borderRadius: "14px",
          boxShadow: "0 -8px 32px rgba(91,79,207,0.14)", maxHeight: "260px", overflowY: "auto", padding: "6px",
        }}>
          {GROUPED_LANGUAGES.map(({ group, options }) => (
            <div key={group}>
              <div style={{ padding: "6px 12px 4px", fontSize: "10px", fontWeight: 700, letterSpacing: "0.08em", color: "var(--accent)", textTransform: "uppercase" }}>{group}</div>
              {options.map(lang => (
                <div
                  key={lang}
                  onClick={() => { onChange(lang); setOpen(false); }}
                  style={{
                    padding: "10px 14px", borderRadius: "10px", fontSize: "14px",
                    fontWeight: lang === value ? 700 : 500,
                    color: lang === value ? "var(--accent)" : "var(--text)",
                    background: lang === value ? "var(--accent-light)" : "transparent",
                    cursor: "pointer", transition: "background 0.12s",
                  }}
                  onMouseEnter={e => { if (lang !== value) (e.currentTarget as HTMLElement).style.background = "var(--bg)"; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = lang === value ? "var(--accent-light)" : "transparent"; }}
                >
                  {lang}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Progress ─────────────────────────────────────────────────────────────────

function TranslationProgress({ job }: { job: TranslationJob }) {
  const [elapsed, setElapsed] = useState(() => {
    if (job.created_at) {
      return Math.max(0, Math.floor((Date.now() - new Date(job.created_at).getTime()) / 1000));
    }
    return 0;
  });

  useEffect(() => {
    const timer = setInterval(() => {
      if (job.created_at) {
        setElapsed(Math.max(0, Math.floor((Date.now() - new Date(job.created_at).getTime()) / 1000)));
      } else {
        setElapsed(s => s + 1);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [job.created_at]);

  const activeStep = STEPS.reduce((acc, s) => (elapsed >= s.secs ? s.key : acc), STEPS[0].key);

  const stepLabel = (step: typeof STEPS[0]) => {
    if (step.key === "detecting" && job.source_language)
      return `Language detected: ${job.source_language}`;
    return step.baseLabel;
  };

  return (
    <div className="card" style={{ padding: "48px 40px", maxWidth: 480, margin: "0 auto", textAlign: "center" }}>
      <div style={{ margin: "0 auto 28px", width: 52, height: 52, position: "relative" }}>
        <svg className="spin" viewBox="0 0 52 52" width={52} height={52} style={{ position: "absolute", inset: 0 }}>
          <circle cx="26" cy="26" r="22" fill="none" stroke="var(--accent-light)" strokeWidth="4" />
          <circle cx="26" cy="26" r="22" fill="none" stroke="var(--accent)" strokeWidth="4"
            strokeDasharray="138" strokeDashoffset="103" strokeLinecap="round" />
        </svg>
      </div>

      <div style={{ fontWeight: 700, fontSize: "16px", marginBottom: "4px" }}>Translating Document</div>
      <div style={{ fontSize: "13px", color: "var(--text3)", marginBottom: "28px" }}>
        {job.source_language
          ? <><strong style={{ color: "var(--accent)" }}>{job.source_language}</strong> → <strong style={{ color: "var(--accent)" }}>{job.target_language}</strong> · Gemini Pro</>
          : "Detecting language…"}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "10px", textAlign: "left" }}>
        {STEPS.map((step, i) => {
          const stepKeys = STEPS.map(s => s.key);
          const activeIdx = stepKeys.indexOf(activeStep);
          const thisIdx = stepKeys.indexOf(step.key);
          const done = thisIdx < activeIdx;
          const current = thisIdx === activeIdx;
          return (
            <div key={step.key} style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <div style={{
                width: 22, height: 22, borderRadius: "50%", flexShrink: 0,
                display: "flex", alignItems: "center", justifyContent: "center",
                background: done ? "var(--accent)" : current ? "var(--accent-light)" : "var(--bg2)",
                border: current ? "2px solid var(--accent)" : "2px solid transparent",
                fontSize: "11px", fontWeight: 700,
                color: done ? "#fff" : current ? "var(--accent)" : "var(--text3)",
              }}>
                {done ? "✓" : i + 1}
              </div>
              <span style={{
                fontSize: "13px", fontWeight: current ? 600 : 400,
                color: done ? "var(--text2)" : current ? "var(--text)" : "var(--text3)",
              }}>
                {stepLabel(step)}
                {current && <span className="thinking-dots" />}
              </span>
            </div>
          );
        })}
      </div>

      <div style={{ marginTop: "24px", fontSize: "11px", color: "var(--text3)" }}>
        Task runs in background · safe to switch tabs or refresh
      </div>
    </div>
  );
}

// ─── Save / Discard Modal ─────────────────────────────────────────────────────

function SaveDiscardModal({
  job,
  onSave,
  onDiscard,
  onClose,
  saving,
}: {
  job: TranslationJob;
  onSave: () => void;
  onDiscard: () => void;
  onClose: () => void;
  saving: boolean;
}) {
  return (
    <div 
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 900,
        background: "rgba(15,13,30,0.55)", backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div 
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--white)", borderRadius: "20px",
          padding: "40px 36px", maxWidth: 460, width: "100%", margin: "20px",
          boxShadow: "0 32px 80px rgba(0,0,0,0.22)", border: "1px solid var(--border)",
          textAlign: "center",
          position: "relative",
        }}
      >
        <button 
          onClick={onClose}
          style={{
            position: "absolute", top: "16px", right: "16px",
            background: "transparent", border: "none", color: "var(--text3)",
            cursor: "pointer", padding: "4px"
          }}
        >
          <X size={20} />
        </button>
        <div style={{
          width: 56, height: 56, borderRadius: "16px",
          background: "var(--accent-light)", color: "var(--accent)",
          display: "flex", alignItems: "center", justifyContent: "center",
          margin: "0 auto 20px",
        }}>
          <Save size={26} />
        </div>
        <h2 style={{ fontSize: "20px", fontWeight: 700, marginBottom: "10px" }}>
          Translation Complete
        </h2>
        <p style={{ fontSize: "14px", color: "var(--text3)", lineHeight: 1.6, marginBottom: "28px" }}>
          Your document has been translated from{" "}
          <strong>{job.source_language || "Auto"}</strong> to{" "}
          <strong>{job.target_language}</strong>.
          <br />
          Do you want to save it to your Vault?
        </p>

        <div style={{ background: "var(--bg2)", borderRadius: "12px", padding: "14px 16px", marginBottom: "28px", textAlign: "left" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--text3)", marginBottom: "6px", letterSpacing: "0.06em" }}>WILL BE SAVED AS</div>
          <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)", display: "flex", alignItems: "center", gap: "8px" }}>
            <FileText size={14} style={{ color: "var(--accent)" }} />
            translated_document.pdf
          </div>
          <div style={{ fontSize: "11px", color: "var(--text3)", marginTop: "4px" }}>Same folder as original</div>
        </div>

        <div style={{ display: "flex", gap: "12px" }}>
          <button
            onClick={onDiscard}
            disabled={saving}
            style={{
              flex: 1, height: "44px", borderRadius: "12px",
              border: "1.5px solid var(--border)", background: "var(--white)",
              color: "var(--text2)", fontSize: "14px", fontWeight: 600,
              cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: "8px",
            }}
          >
            <Trash2 size={15} /> Discard
          </button>
          <button
            onClick={onSave}
            disabled={saving}
            style={{
              flex: 2, height: "44px", borderRadius: "12px",
              border: "none", background: "var(--accent)",
              color: "white", fontSize: "14px", fontWeight: 700,
              cursor: saving ? "not-allowed" : "pointer",
              display: "flex", alignItems: "center", justifyContent: "center", gap: "8px",
              opacity: saving ? 0.7 : 1,
            }}
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
            {saving ? "Saving…" : "Save to Vault"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function TranslationsPage() {
  const qc = useQueryClient();
  const [step, setStep] = useState<"list" | "new" | "view">("list");
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<{ id: string; name: string } | null>(null);
  const [targetLang, setTargetLang] = useState("English");
  const [activeJob, setActiveJob] = useState<TranslationJob | null>(null);
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [savingDoc, setSavingDoc] = useState(false);
  const prevStatus = useRef<string | null>(null);

  // ── Restore persisted job on mount ──
  useEffect(() => {
    const savedJobId = localStorage.getItem(ACTIVE_JOB_KEY);
    if (savedJobId && !activeJob) {
      translationsApi.get(savedJobId)
        .then(r => {
          setActiveJob(r.data);
          setStep("view");
        })
        .catch(() => {
          localStorage.removeItem(ACTIVE_JOB_KEY);
        });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Persist job id whenever activeJob changes ──
  useEffect(() => {
    if (activeJob?.id) {
      localStorage.setItem(ACTIVE_JOB_KEY, activeJob.id);
    }
  }, [activeJob?.id]);


  const { data: folders = [] } = useQuery({
    queryKey: ["folders"],
    queryFn: () => foldersApi.list().then(r => r.data),
  });

  const { data: docs = [] } = useQuery({
    queryKey: ["docs-translate", selectedFolder],
    queryFn: () => documentsApi.listByFolder(selectedFolder!).then(r => r.data),
    enabled: !!selectedFolder,
  });

  // ── Derived State for List View ──
  const translationsFolder = folders.find((f: any) => f.name === "Translations");
  const { data: translationDocs = [], isLoading: loadingTranslations } = useQuery({
    queryKey: ["docs-translate-folder", translationsFolder?.id],
    queryFn: () => documentsApi.listByFolder(translationsFolder!.id).then(r => r.data),
    enabled: !!translationsFolder?.id,
  });

  const [searchQuery, setSearchQuery] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  const [viewDoc, setViewDoc] = useState<{ id: string; name: string } | null>(null);

  // ── Live-poll active job (even after refresh — purely DB-driven) ──
  const { data: liveJob } = useQuery<TranslationJob>({
    queryKey: ["translation-job-live", activeJob?.id],
    queryFn: () => translationsApi.get(activeJob!.id).then(r => r.data),
    enabled: !!activeJob?.id && (activeJob?.status === "processing" || activeJob?.status === "pending"),
    refetchInterval: 3000,
  });
  useEffect(() => {
    if (liveJob) setActiveJob(liveJob);
  }, [liveJob]);

  // ── Start translation ──
  const startTranslation = useMutation({
    mutationFn: () => translationsApi.start(selectedDoc!.id, targetLang),
    onSuccess: (res) => {
      toast.success("Translation started! It will continue even if you navigate away.");
      setActiveJob(res.data);
      setStep("view");
    },
    onError: () => toast.error("Failed to start translation"),
  });

  // ── Reset Translation State ──
  const resetTranslationState = useCallback(() => {
    setStep("list");
    setActiveJob(null);
    setSelectedDoc(null);
    localStorage.removeItem(ACTIVE_JOB_KEY);
    setShowSaveModal(false);
  }, []);

  // ── Save translated document ──
  const handleSave = useCallback(async () => {
    if (!activeJob) return;
    setSavingDoc(true);
    try {
      await translationsApi.save(activeJob.id);
      // Refresh job to get saved_storage_key etc.
      const updated = await translationsApi.get(activeJob.id);
      setActiveJob(updated.data);
      toast.success("Translated document saved to Vault!");
      // Invalidate folder docs so it appears immediately
      qc.invalidateQueries({ queryKey: ["docs-translate"] });
      
      // After saving, go back as requested
      resetTranslationState();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to save document");
    } finally {
      setSavingDoc(false);
    }
  }, [activeJob, qc, resetTranslationState]);

  // ── Discard ──
  const handleDiscard = useCallback(() => {
    toast("Translation discarded.");
    resetTranslationState();
  }, [resetTranslationState]);

  // ── Go back / reset ──
  const handleBack = () => {
    // If job is done but NOT saved yet, intercept 'Back' with the modal
    if (activeJob && activeJob.status === "done" && !activeJob.saved_storage_key) {
      setShowSaveModal(true);
      return;
    }

    if (activeJob && (activeJob.status === "processing" || activeJob.status === "pending")) {
      const confirmed = window.confirm("Leaving this page will stop all active translations. Are you sure you want to stop?");
      if (!confirmed) return;
      translationsApi.cancelAll().catch(() => {});
    }
    
    resetTranslationState();
  };

  // ── Cancel translation ──
  const [cancelling, setCancelling] = useState(false);
  const handleCancel = async () => {
    if (!activeJob) return;
    setCancelling(true);
    try {
      await translationsApi.cancelAll();
      toast("All active translations stopped.");
      setActiveJob(prev => prev ? { ...prev, status: "error", error_message: "Translation cancelled by user" } : null);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to cancel translation");
    } finally {
      setCancelling(false);
    }
  };

  const parsedSections = activeJob?.structure_map ? (() => { try { return JSON.parse(activeJob.structure_map); } catch { return null; } })() : null;
  const flagsData = activeJob?.uncertainty_flags ? (() => { try { return JSON.parse(activeJob.uncertainty_flags); } catch { return {}; } })() : {};
  const flags = flagsData.flags || [];
  const translatedBlocks = flagsData.translated_blocks || [];
  const useBlocks = translatedBlocks.length > 0;

  // ── VIEW step ──────────────────────────────────────────────────────────────
  if (step === "view" && activeJob) {
    const isDone = activeJob.status === "done";
    const isProcessing = activeJob.status === "pending" || activeJob.status === "processing";
    const isError = activeJob.status === "error";

    return (
      <div className="fade-in">
        {/* Save/Discard modal — blocks leaving without action */}
        {showSaveModal && (
          <SaveDiscardModal
            job={activeJob}
            onSave={handleSave}
            onDiscard={handleDiscard}
            onClose={() => setShowSaveModal(false)}
            saving={savingDoc}
          />
        )}

        <div className="toolbar">
          <button
            onClick={handleBack}
            className="back-btn"
            style={{ marginBottom: 0 }}
          >
            <ArrowLeft size={16} /> Back to Translations
          </button>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            {isDone && !activeJob.saved_storage_key && (
              <button
                className="btn-accent"
                onClick={() => setShowSaveModal(true)}
              >
                <Save size={14} /> Save Translation
              </button>
            )}
            {isProcessing && (
              <button
                disabled={cancelling}
                onClick={handleCancel}
                style={{
                  padding: "0 16px", height: "36px", borderRadius: "10px",
                  background: "var(--red-light)", color: "var(--red)",
                  border: "1px solid var(--red)", fontSize: "13px", fontWeight: 600,
                  display: "flex", alignItems: "center", gap: "6px", cursor: cancelling ? "not-allowed" : "pointer",
                  opacity: cancelling ? 0.6 : 1
                }}
              >
                {cancelling ? <Loader2 size={14} className="animate-spin" /> : <X size={14} />}
                Stop Translation
              </button>
            )}
            {activeJob.saved_storage_key && (
              <span style={{
                display: "flex", alignItems: "center", gap: "6px",
                padding: "6px 14px", background: "var(--green-light)",
                borderRadius: "8px", fontSize: "12px", fontWeight: 700, color: "var(--green)",
              }}>
                <CheckCircle size={13} /> Saved to Vault
              </span>
            )}
            {!isProcessing && (
              <button className="btn-accent" onClick={() => {
                handleBack();
                setTimeout(() => setStep("new"), 10);
              }}>
                <Plus size={14} /> New Translation
              </button>
            )}
          </div>
        </div>

        <div className="page-header">
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <h1>Legal Translation</h1>
            <span className={`badge ${isDone ? "green" : isError ? "red" : "amber"}`}>{activeJob.status}</span>
          </div>
          <p>{activeJob.source_language || "Auto"} → {activeJob.target_language} · Flash Structure Preserving Engine</p>
        </div>

        {isProcessing ? (
          <TranslationProgress job={activeJob} />
        ) : isError ? (
          <div className="card" style={{ padding: "40px", textAlign: "center", border: "1px solid var(--red)" }}>
            <AlertTriangle style={{ margin: "0 auto 12px", color: "var(--red)" }} />
            <p style={{ fontWeight: 600, marginBottom: "8px" }}>Translation Failed</p>
            {activeJob.error_message && (
              <p style={{ fontSize: "13px", color: "var(--text2)", maxWidth: "480px", margin: "0 auto" }}>{activeJob.error_message}</p>
            )}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
              <div className="card" style={{ padding: "0" }}>
                <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--border)", background: "var(--bg)", fontSize: "11px", fontWeight: 700, color: "var(--text3)" }}>
                  ORIGINAL — {activeJob.source_language || "Source"}
                </div>
                <div style={{ padding: "24px", fontSize: "13px", lineHeight: "1.6", maxHeight: "60vh", overflowY: "auto" }}>
                  {parsedSections ? parsedSections.map((s: any, i: number) => (
                    <div key={i} style={{ marginBottom: "20px" }}>
                      {s.original_heading && <div style={{ fontWeight: 700, marginBottom: "8px" }}>{s.original_heading}</div>}
                      <div>{s.original_text}</div>
                    </div>
                  )) : activeJob.original_text}
                </div>
              </div>

              <div className="card" style={{ padding: "0" }}>
                <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--border)", background: "var(--accent-light)", fontSize: "11px", fontWeight: 700, color: "var(--accent)" }}>
                  TRANSLATED — {activeJob.target_language}
                </div>
                <div style={{ padding: "24px", fontSize: "13px", lineHeight: "1.6", maxHeight: "60vh", overflowY: "auto" }}>
                  {parsedSections ? parsedSections.map((s: any, i: number) => (
                    <div key={i} style={{ marginBottom: "20px" }}>
                      {s.translated_heading && <div style={{ fontWeight: 700, color: "var(--accent)", marginBottom: "8px" }}>{s.translated_heading}</div>}
                      <div style={{ color: "var(--text)" }}>{s.translated_text}</div>
                      {s.translator_notes && s.translator_notes.map((n: string, ni: number) => (
                        <div key={ni} style={{ fontSize: "11px", fontStyle: "italic", color: "var(--text3)", marginTop: "6px" }}>Note: {n}</div>
                      ))}
                    </div>
                  )) : activeJob.translated_text}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ── NEW step ───────────────────────────────────────────────────────────────
  if (step === "new") {
    const indexedDocs = docs.filter((d: any) => d.status === "indexed");
    const canStart = !!selectedDoc && !startTranslation.isPending;

    const folderDocCount = (folder: any) => {
      const count = folder.indexed_documents_count ?? folder.documents_count ?? folder.doc_count ?? folder.count;
      if (typeof count === "number") return `${count} docs`;
      if (folder.id === selectedFolder) return `${indexedDocs.length} docs`;
      return "—";
    };

    return (
      <div className="fade-in">
        <button
          onClick={() => setStep("list")}
          className="back-btn"
          style={{ background: "white", padding: "8px 16px", borderRadius: "8px", display: "flex", alignItems: "center", gap: "8px", border: "1px solid var(--border)", cursor: "pointer", fontSize: "14px", fontWeight: 500 }}
        >
          <ArrowLeft size={16} /> Back
        </button>

        <div className="page-header" style={{ marginTop: "24px" }}>
          <h1>New Translation</h1>
          <p>Select a folder and document to translate</p>
        </div>

        <div className="two-col" style={{ gridTemplateColumns: "1.1fr 1fr", gap: "20px", alignItems: "stretch" }}>
          {/* Folder picker */}
          <div className="card" style={{ padding: "24px", height: "min(68vh, 620px)", display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="section-title" style={{ marginBottom: "14px", display: "flex", alignItems: "center", gap: "10px" }}>
              <span style={{ width: "26px", height: "26px", borderRadius: "999px", background: "var(--accent)", color: "white", fontSize: "13px", fontWeight: 700, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>1</span>
              SELECT FOLDER
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", overflow: "auto", paddingRight: "4px", minHeight: 0, flex: 1 }}>
              {folders.map((f: any) => {
                const isSelected = selectedFolder === f.id;
                return (
                  <div
                    key={f.id}
                    onClick={() => { setSelectedFolder(f.id); setSelectedDoc(null); }}
                    style={{
                      display: "flex", alignItems: "center", gap: "12px",
                      padding: "14px 16px", borderRadius: "12px",
                      border: isSelected ? "2px solid var(--accent)" : "1.5px solid var(--border)",
                      background: isSelected ? "var(--accent-light)" : "var(--white)", cursor: "pointer",
                    }}
                  >
                    <div style={{ color: "var(--accent)" }}><Folder size={18} /></div>
                    <div style={{ fontWeight: 600, fontSize: "18px", color: "var(--text)", flex: 1 }}>{f.name}</div>
                    <div style={{ fontSize: "14px", color: "var(--text3)" }}>{folderDocCount(f)}</div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Document + language picker */}
          <div className="card" style={{ padding: "24px", height: "min(68vh, 620px)", display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="section-title" style={{ marginBottom: "14px", display: "flex", alignItems: "center", gap: "10px" }}>
              <span style={{ width: "26px", height: "26px", borderRadius: "999px", background: "var(--accent)", color: "white", fontSize: "13px", fontWeight: 700, display: "inline-flex", alignItems: "center", justifyContent: "center" }}>2</span>
              SELECT DOCUMENT
            </div>

            {!selectedFolder ? (
              <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", textAlign: "center", color: "var(--text3)", fontSize: "16px" }}>
                Choose a folder to list indexed documents
              </div>
            ) : indexedDocs.length === 0 ? (
              <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", color: "var(--text3)", gap: "10px" }}>
                <FileText size={34} style={{ opacity: 0.4 }} />
                <div style={{ fontSize: "16px", maxWidth: "280px" }}>No indexed documents in this folder</div>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "10px", overflow: "auto", paddingRight: "4px", minHeight: 0, flex: 1 }}>
                {indexedDocs.map((d: any) => {
                  const isSelected = selectedDoc?.id === d.id;
                  return (
                    <div
                      key={d.id}
                      onClick={() => setSelectedDoc({ id: d.id, name: d.name })}
                      style={{
                        display: "flex", alignItems: "center", gap: "10px",
                        borderRadius: "10px",
                        border: isSelected ? "2px solid var(--accent)" : "1.5px solid var(--border)",
                        background: isSelected ? "var(--accent-light)" : "var(--white)",
                        padding: "12px 14px", cursor: "pointer",
                      }}
                    >
                      <FileText size={16} style={{ color: "var(--accent)" }} />
                      <span style={{ fontSize: "14px", color: "var(--text)", fontWeight: isSelected ? 600 : 500, flex: 1 }}>{d.name}</span>
                      {isSelected && <CheckCircle size={16} style={{ color: "var(--accent)" }} />}
                    </div>
                  );
                })}
              </div>
            )}

            <div style={{ marginTop: "16px", borderTop: "1px solid var(--border)", paddingTop: "16px", background: "var(--white)", position: "sticky", bottom: 0 }}>
              <div className="section-title" style={{ marginBottom: "10px" }}>3. TARGET LANGUAGE</div>
              <LanguageDropdown value={targetLang} onChange={setTargetLang} />
              <button
                className="btn-continue"
                onClick={() => startTranslation.mutate()}
                disabled={!canStart}
                style={{ width: "100%", justifyContent: "center", marginTop: "12px", height: "42px", borderRadius: "10px", fontSize: "14px" }}
              >
                {startTranslation.isPending ? <Loader2 className="animate-spin" size={16} /> : <Plus size={16} />}
                Start Translation
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const filteredDocs = translationDocs.filter((d: any) => 
    d.name.toLowerCase().includes(searchQuery.toLowerCase())
  ).sort((a: any, b: any) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  
  const totalPages = Math.ceil(filteredDocs.length / itemsPerPage) || 1;
  const paginatedDocs = filteredDocs.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage);

  const handleDownload = async (docId: string, docName: string) => {
    try {
      const res = await documentsApi.download(docId);
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", docName || "document.pdf");
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch {
      toast.error("Download failed");
    }
  };

  const handleViewPdf = (docId: string, docName: string) => {
    setViewDoc({ id: docId, name: docName, status: "indexed" } as any);
  };

  // ── LIST step (default landing) ────────────────────────────────────────────
  return (
    <div className="fade-in">
      <div className="toolbar">
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div className="page-header" style={{ marginBottom: 0 }}>
            <h1>Translations</h1>
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: "24px", flexDirection: "column" }}>
        
        {/* Header / Hero */}
        <div className="card" style={{ padding: "40px 30px", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "20px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "20px" }}>
            <div style={{ width: "56px", height: "56px", borderRadius: "14px", background: "var(--accent-light)", color: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Languages size={28} />
            </div>
            <div>
              <h3 style={{ fontSize: "20px", marginBottom: "4px" }}>Structure-Preserving Translation</h3>
              <p style={{ color: "var(--text2)", margin: 0, maxWidth: "500px" }}>
                Translate complex legal documents across 12+ languages while maintaining layout and clause logic.
              </p>
            </div>
          </div>
          <button 
            className="btn-accent" 
            onClick={() => setStep("new")} 
            style={{ 
              padding: "0 24px", height: "46px", borderRadius: "12px", 
              fontSize: "14px", fontWeight: 600, display: "flex", 
              alignItems: "center", justifyContent: "center", gap: "8px", 
              width: "auto", minWidth: "200px" 
            }}
          >
            <Plus size={16} /> Start New Translation
          </button>
        </div>

        {/* Translation Files List */}
        <div className="card" style={{ padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
          <div style={{ padding: "20px 24px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "10px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <h3 style={{ fontSize: "16px", margin: 0, fontWeight: 700 }}>Translated Files</h3>
              <span style={{ fontSize: "12px", background: "var(--bg2)", padding: "4px 10px", borderRadius: "999px", fontWeight: 600, color: "var(--text2)" }}>
                {filteredDocs.length} Total
              </span>
            </div>
            <div style={{ position: "relative", width: "240px" }}>
              <input 
                type="text" 
                placeholder="Search translations..." 
                value={searchQuery}
                onChange={e => { setSearchQuery(e.target.value); setCurrentPage(1); }}
                style={{ 
                  width: "100%", padding: "8px 14px", paddingLeft: "36px", 
                  borderRadius: "8px", border: "1.5px solid var(--border)", 
                  fontSize: "13px", outline: "none"
                }} 
              />
              <svg style={{ position: "absolute", left: "12px", top: "50%", transform: "translateY(-50%)", color: "var(--text3)", width: "16px", height: "16px" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: "600px" }}>
              <thead>
                <tr style={{ background: "var(--bg)", borderBottom: "1px solid var(--border)" }}>
                  <th style={{ padding: "14px 24px", textAlign: "left", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase" }}>#</th>
                  <th style={{ padding: "14px 24px", textAlign: "left", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase" }}>Document Name</th>
                  <th style={{ padding: "14px 24px", textAlign: "left", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase" }}>Date Saved</th>
                  <th style={{ padding: "14px 24px", textAlign: "right", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {loadingTranslations ? (
                  <tr><td colSpan={4} style={{ padding: "40px", textAlign: "center", color: "var(--text3)" }}>Loading documents...</td></tr>
                ) : filteredDocs.length === 0 ? (
                  <tr><td colSpan={4} style={{ padding: "60px 40px", textAlign: "center", color: "var(--text3)" }}>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "10px" }}>
                      <FileText size={32} style={{ opacity: 0.4 }} />
                      <span>{searchQuery ? "No translations match your search." : "No translated documents found in Vault."}</span>
                    </div>
                  </td></tr>
                ) : paginatedDocs.map((doc: any, i: number) => (
                  <tr key={doc.id} style={{ borderBottom: "1px solid var(--border)", transition: "background 0.2s" }} className="hover-bg">
                    <td style={{ padding: "16px 24px", fontSize: "13px", color: "var(--text3)", fontWeight: 600 }}>
                      {(currentPage - 1) * itemsPerPage + i + 1}
                    </td>
                    <td style={{ padding: "16px 24px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                        <div style={{ background: "var(--accent-light)", color: "var(--accent)", padding: "8px", borderRadius: "8px" }}>
                          <FileText size={16} />
                        </div>
                        <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--text)" }}>{doc.name}</span>
                      </div>
                    </td>
                    <td style={{ padding: "16px 24px", fontSize: "13px", color: "var(--text2)" }}>
                      {new Date(doc.created_at).toLocaleDateString()} at {new Date(doc.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                    </td>
                    <td style={{ padding: "16px 24px", textAlign: "right" }}>
                      <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                        <button 
                          onClick={() => handleViewPdf(doc.id, doc.name)}
                          style={{ padding: "6px 12px", borderRadius: "6px", border: "1.5px solid var(--border)", background: "white", fontSize: "12px", fontWeight: 600, color: "var(--text2)", cursor: "pointer", display: "flex", alignItems: "center", gap: "6px" }}
                        >
                          View
                        </button>
                        <button 
                          onClick={() => handleDownload(doc.id, doc.name)}
                          style={{ padding: "6px 12px", borderRadius: "6px", border: "none", background: "var(--accent)", fontSize: "12px", fontWeight: 600, color: "white", cursor: "pointer", display: "flex", alignItems: "center", gap: "6px" }}
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                          Download
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination Controls */}
          {totalPages > 1 && (
            <div style={{ padding: "14px 24px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--bg)" }}>
              <span style={{ fontSize: "13px", color: "var(--text3)" }}>
                Showing {(currentPage - 1) * itemsPerPage + 1} to {Math.min(currentPage * itemsPerPage, filteredDocs.length)} of {filteredDocs.length} entries
              </span>
              <div style={{ display: "flex", gap: "6px" }}>
                <button 
                  disabled={currentPage === 1}
                  onClick={() => setCurrentPage(p => p - 1)}
                  style={{ padding: "6px 12px", borderRadius: "6px", border: "1px solid var(--border)", background: "white", fontSize: "13px", fontWeight: 500, cursor: currentPage === 1 ? "not-allowed" : "pointer", opacity: currentPage === 1 ? 0.5 : 1 }}
                >
                  Previous
                </button>
                <div style={{ display: "flex", gap: "4px", margin: "0 8px", alignItems: "center" }}>
                  {Array.from({ length: totalPages }).map((_, i) => (
                    <button
                      key={i}
                      onClick={() => setCurrentPage(i + 1)}
                      style={{
                        width: "28px", height: "28px", borderRadius: "6px", border: "none",
                        background: currentPage === i + 1 ? "var(--accent)" : "transparent",
                        color: currentPage === i + 1 ? "white" : "var(--text2)",
                        fontSize: "13px", fontWeight: 600, cursor: "pointer"
                      }}
                    >
                      {i + 1}
                    </button>
                  ))}
                </div>
                <button 
                  disabled={currentPage === totalPages}
                  onClick={() => setCurrentPage(p => p + 1)}
                  style={{ padding: "6px 12px", borderRadius: "6px", border: "1px solid var(--border)", background: "white", fontSize: "13px", fontWeight: 500, cursor: currentPage === totalPages ? "not-allowed" : "pointer", opacity: currentPage === totalPages ? 0.5 : 1 }}
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
      
      {viewDoc && (
        <DocumentViewerModal
          doc={viewDoc as any}
          onClose={() => setViewDoc(null)}
        />
      )}
    </div>
  );
}
