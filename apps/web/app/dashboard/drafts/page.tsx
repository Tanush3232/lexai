"use client";
import { useState, useRef, useCallback, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  draftsApi, draftSessionsApi,
  DraftEvent, DraftSource, ContractBlock, DraftQuestion, DraftIssue, DraftIntent, AdversarialFinding,
} from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth-store";
import { toast } from "sonner";
import {
  Plus, FileText, ArrowLeft, Download, CheckCircle, AlertTriangle,
  Clock, ChevronRight, Globe, BookOpen, Database, Zap, Lock, Unlock,
  Edit3, Eye, Shield, Star, X, Check, Loader2, Search, Filter,
  ExternalLink, Info, AlertCircle, Sparkles, Building2, Scale, BookMarked, Trash2, Swords, Activity,
} from "lucide-react";

// ─────────────────────────────────────────────────────────────────────────────
// Types & helpers
// ─────────────────────────────────────────────────────────────────────────────

type View = "list" | "prompt" | "orchestrating" | "sources" | "editor";

const STAGE_STEPS = [
  { id: "analyzing_intent", label: "Analyzing Request" },
  { id: "needs_input", label: "Details" },
  { id: "redacting", label: "Privacy" },
  { id: "researching", label: "Searching References" },
  { id: "ranking_sources", label: "Ranking" },
  { id: "sources_ready", label: "Source Review" },
  { id: "assembling", label: "Building Draft" },
  { id: "red_teaming", label: "Review" },
  { id: "complete", label: "Complete" },
];

function stripHtmlToText(html: string): string {
  if (!html) return "";
  let text = html.replace(/<br\s*\/?>/gi, "\n")
                 .replace(/<\/p>/gi, "\n\n")
                 .replace(/<\/div>/gi, "\n\n")
                 .replace(/<\/li>/gi, "\n")
                 .replace(/<li>/gi, "• ");
  text = text.replace(/<[^>]*>?/gm, '');
  text = text.replace(/&nbsp;/g, ' ')
             .replace(/&amp;/g, '&')
             .replace(/&lt;/g, '<')
             .replace(/&gt;/g, '>')
             .replace(/&quot;/g, '"')
             .replace(/&#39;/g, "'");
  return text.trim();
}

const STAGE_INDEX: Record<string, number> = Object.fromEntries(
  STAGE_STEPS.map((s, i) => [s.id, i])
);

const SOURCE_TYPE_ICON: Record<string, React.ReactNode> = {
  statute: <Scale size={14} />,
  case_law: <BookMarked size={14} />,
  regulation: <Shield size={14} />,
  template: <FileText size={14} />,
  precedent: <Database size={14} />,
};

const SOURCE_TYPE_COLOR: Record<string, string> = {
  statute: "#7c5cfc",
  case_law: "#0ea5e9",
  regulation: "#f59e0b",
  template: "#10b981",
  precedent: "#e879f9",
};

interface DraftListItem {
  id: string;
  contract_type: string;
  title: string;
  status: string;
  session_id?: string;
  created_at: string;
  updated_at: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Page
// ─────────────────────────────────────────────────────────────────────────────

export default function DraftsPage() {
  const qc = useQueryClient();
  const { user } = useAuthStore();
  const userRole = user?.role || "legal_team";
  const [view, setView] = useState<View>("list");
  const [showInfoModal, setShowInfoModal] = useState<boolean>(false);

  // Orchestration state
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentStage, setCurrentStage] = useState<string>("analyzing_intent");
  const [stageLabel, setStageLabel] = useState<string>("Starting…");
  const [progress, setProgress] = useState<number>(0);
  const [intent, setIntent] = useState<DraftIntent | null>(null);
  const [questions, setQuestions] = useState<DraftQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [researchLog, setResearchLog] = useState<{ label: string; count?: number; agent?: string }[]>([]);
  const [rankedSources, setRankedSources] = useState<DraftSource[]>([]);
  const [allSources, setAllSources] = useState<DraftSource[]>([]);
  const [rankingSummary, setRankingSummary] = useState<string>("");
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  // Editor state
  const [activeDraftId, setActiveDraftId] = useState<string | null>(null);
  const [activeDraftTitle, setActiveDraftTitle] = useState<string>("");
  const [blocks, setBlocks] = useState<ContractBlock[]>([]);
  const [issues, setIssues] = useState<DraftIssue[]>([]);
  const [editorSources, setEditorSources] = useState<DraftSource[]>([]);
  const [activeBlock, setActiveBlock] = useState<string | null>(null);
  const [editingBlock, setEditingBlock] = useState<{ id: string; content: string } | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [draftStatus, setDraftStatus] = useState<string>("draft");

  // Adversarial red-team state
  const [adversarialFindings, setAdversarialFindings] = useState<AdversarialFinding[]>([]);
  const [adversarialRisk, setAdversarialRisk] = useState<string>("");
  const [adversarialAssessment, setAdversarialAssessment] = useState<string>("");
  const [adversarialMissingSections, setAdversarialMissingSections] = useState<string[]>([]);
  const [contractTypeForFix, setContractTypeForFix] = useState<string>("");

  // Search
  const [searchQuery, setSearchQuery] = useState("");

  const abortRef = useRef<(() => void) | null>(null);

  const { data: drafts = [], isLoading } = useQuery<DraftListItem[]>({
    queryKey: ["drafts"],
    queryFn: () => draftsApi.list().then(r => r.data),
  });

  const filteredDrafts = drafts.filter(d =>
    !searchQuery || d.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    d.contract_type.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Helper to safely transition views and update sessionStorage
  const changeView = useCallback((newView: View) => {
    setView(newView);
    sessionStorage.setItem("lexai_draft_view", newView);
  }, []);

  // ── Restore State on Reload ────────────────────────────────────────────────
  useEffect(() => {
    const savedView = sessionStorage.getItem("lexai_draft_view") as View | null;
    const savedSessionId = sessionStorage.getItem("lexai_active_session_id");
    const savedDraftId = sessionStorage.getItem("lexai_active_draft_id");

    const restoreSession = async (id: string, targetView: View) => {
      try {
        const res = await draftSessionsApi.getSession(id);
        const st = res.data;
        setSessionId(st.id);
        setCurrentStage(st.stage);
        if (st.intent) setIntent(st.intent);
        if (st.questions) setQuestions(st.questions);
        if (st.sources_data) {
          setRankedSources(st.sources_data.sources || []);
          setAllSources(st.sources_data.all_sources || []);
          setRankingSummary(st.sources_data.ranking_summary || "");
          const recommended = new Set<string>(
            (st.sources_data.sources || []).filter((s: any) => s.include !== false).map((s: any) => s.source_id as string)
          );
          setSelectedSourceIds(recommended);
        }
        changeView(targetView);
      } catch (e) {
        console.error("Failed to restore session state", e);
        sessionStorage.removeItem("lexai_active_session_id");
      }
    };

    if (savedView === "editor" && savedDraftId) {
      // Defer to prevent racing with openDraft definition if it was outside, but openDraft is down below.
      // We can just call the draftsApi directly.
      draftsApi.get(savedDraftId).then(res => {
        const d = res.data;
        const parsedBlocks = d.blocks_json ? JSON.parse(d.blocks_json) : [];
        setBlocks(parsedBlocks.map((b: any) => ({ ...b, content: stripHtmlToText(b.content) })));
        setIssues(d.issues_json ? JSON.parse(d.issues_json) : []);
        setEditorSources(d.sources_json ? JSON.parse(d.sources_json) : []);
        setDraftStatus(d.status);
        setActiveDraftTitle(d.title);
        setActiveDraftId(d.id);
        if (d.adversarial_report_json) {
          const rt = JSON.parse(d.adversarial_report_json);
          setAdversarialFindings(rt.findings || []);
          setAdversarialRisk(rt.overall_risk || "");
          setAdversarialAssessment(rt.overall_assessment || "");
          setAdversarialMissingSections(rt.missing_sections || []);
        }
        if (d.contract_type) setContractTypeForFix(d.contract_type);
        changeView("editor");
      }).catch(() => {
        sessionStorage.removeItem("lexai_active_draft_id");
      });
    } else if (savedSessionId && (savedView === "orchestrating" || savedView === "sources")) {
      restoreSession(savedSessionId, savedView);
    } else if (savedView) {
      changeView(savedView);
    }
  }, [changeView]);

  // ── Orchestration event handler ────────────────────────────────────────────
  const handleEvent = useCallback((event: DraftEvent) => {
    const { type } = event;

    if (event.stage) setCurrentStage(event.stage);
    if (event.label) setStageLabel(event.label);
    if (event.progress !== undefined) setProgress(event.progress);

    switch (type) {
      case "session_created":
        if (event.session_id) setSessionId(event.session_id);
        break;

      case "intent_analyzed":
        if (event.intent) setIntent(event.intent);
        break;

      case "needs_input":
        setQuestions(event.questions || []);
        setCurrentStage("needs_input");
        break;

      case "research_update":
        if (event.label)
          setResearchLog(prev => [...prev, {
            label: event.label!,
            count: event.count,
            agent: event.agent,
          }]);
        break;

      case "sources_ready":
        setRankedSources(event.sources || []);
        setAllSources(event.all_sources || []);
        setRankingSummary(event.ranking_summary || "");
        // Pre-select recommended sources
        const recommended = new Set(
          (event.sources || []).filter(s => s.include !== false).map(s => s.source_id)
        );
        setSelectedSourceIds(recommended);
        changeView("sources");
        break;

      case "red_team_complete":
        setAdversarialFindings(event.findings || []);
        setAdversarialRisk(event.overall_risk || "");
        setAdversarialAssessment(event.overall_assessment || "");
        setAdversarialMissingSections(event.missing_sections || []);
        if (event.finding_count !== undefined) {
          const ct = event.critical_count || 0;
          setResearchLog(prev => [...prev, {
            label: `⚔ Adversarial review: ${event.finding_count} vulnerabilities (${ct} critical)`,
            agent: "red_team",
          }]);
        }
        break;

      case "draft_ready":
        const cleanedBlocks = (event.blocks || []).map((b: any) => ({ ...b, content: stripHtmlToText(b.content) }));
        setBlocks(cleanedBlocks);
        setIssues(event.issues || []);
        if (event.title) setActiveDraftTitle(event.title);
        // Merge adversarial data from draft_ready if available (fallback)
        if (event.adversarial_findings && event.adversarial_findings.length > 0) {
          setAdversarialFindings(event.adversarial_findings);
          setAdversarialRisk(event.adversarial_risk || "");
          setAdversarialAssessment(event.adversarial_assessment || "");
          setAdversarialMissingSections(event.adversarial_missing_sections || []);
        }
        if (event.contract_type) setContractTypeForFix(event.contract_type);
        break;

      case "saved":
        if (event.draft_id) {
          setActiveDraftId(event.draft_id);
          sessionStorage.setItem("lexai_active_draft_id", event.draft_id);
          sessionStorage.removeItem("lexai_active_session_id");
          qc.invalidateQueries({ queryKey: ["drafts"] });
          changeView("editor");
          toast.success("Contract assembled and saved!");
        }
        break;

      case "error":
        setError(event.message || "An error occurred");
        toast.error(event.message || "Drafting failed");
        break;
    }
  }, [qc]);

  // ── Start a new session ────────────────────────────────────────────────────
  const startDraft = useCallback((prompt: string) => {
    setError(null);
    setResearchLog([]);
    setQuestions([]);
    setAnswers({});
    setIntent(null);
    setProgress(0);
    setCurrentStage("analyzing_intent");
    setStageLabel("Analyzing your request with Claude…");
    changeView("orchestrating");

    abortRef.current?.();
    abortRef.current = draftSessionsApi.startSession(
      prompt,
      handleEvent,
      (err) => { setError(err.message); },
    );
  }, [handleEvent]);

  // ── Submit follow-up answers ───────────────────────────────────────────────
  const submitAnswers = useCallback(() => {
    if (!sessionId) return;
    setQuestions([]);
    setCurrentStage("redacting");
    setStageLabel("Resuming with your answers…");

    abortRef.current?.();
    abortRef.current = draftSessionsApi.submitContext(
      sessionId,
      answers,
      handleEvent,
      (err) => setError(err.message),
    );
  }, [sessionId, answers, handleEvent]);

  const approveSources = useCallback(() => {
    if (!sessionId) return;
    changeView("orchestrating");
    setCurrentStage("assembling");
    setStageLabel("Claude Opus is assembling your contract…");
    setProgress(75);

    abortRef.current?.();
    abortRef.current = draftSessionsApi.approveSources(
      sessionId,
      Array.from(selectedSourceIds),
      handleEvent,
      (err) => setError(err.message),
    );
  }, [sessionId, selectedSourceIds, handleEvent]);

  // ── Load existing draft ────────────────────────────────────────────────────
  const openDraft = useCallback(async (id: string) => {
    try {
      const res = await draftsApi.get(id);
      const draft = res.data;
      setActiveDraftId(id);
      setActiveDraftTitle(draft.title);
      setDraftStatus(draft.status);
      setEditorSources(draft.sources_json ? JSON.parse(draft.sources_json) : []);
      setIssues(draft.issues_json ? JSON.parse(draft.issues_json) : []);
      if (draft.blocks_json) {
        const parsed = JSON.parse(draft.blocks_json);
        setBlocks(parsed.map((b: any) => ({ ...b, content: stripHtmlToText(b.content) })));
      }
      // Load adversarial report
      if (draft.adversarial_report_json) {
        const report = JSON.parse(draft.adversarial_report_json);
        setAdversarialFindings(report.findings || []);
        setAdversarialRisk(report.overall_risk || "");
        setAdversarialAssessment(report.overall_assessment || "");
        setAdversarialMissingSections(report.missing_sections || []);
      }
      setContractTypeForFix(draft.contract_type || "");
      setView("editor");
    } catch {
      toast.error("Failed to load draft");
    }
  }, []);

  // ── Delete draft ───────────────────────────────────────────────────────────
  const deleteDraft = useCallback(async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (window.confirm("Are you sure you want to delete this draft? This will be deleted forever.")) {
      try {
        await draftsApi.delete(id);
        toast.success("Draft deleted forever");
        qc.invalidateQueries({ queryKey: ["drafts"] });
      } catch {
        toast.error("Failed to delete draft");
      }
    }
  }, [qc]);

  // ── Save block edit ────────────────────────────────────────────────────────
  const saveBlockEdit = useCallback(async () => {
    if (!editingBlock || !activeDraftId) return;
    const updatedBlocks = blocks.map(b =>
      b.block_id === editingBlock.id ? { ...b, content: editingBlock.content } : b
    );
    setBlocks(updatedBlocks);
    setEditingBlock(null);

    setIsSaving(true);
    try {
      await draftsApi.update(activeDraftId, { blocks_json: JSON.stringify(updatedBlocks) });
      toast.success("Block saved");
    } catch {
      toast.error("Save failed");
    } finally {
      setIsSaving(false);
    }
  }, [editingBlock, blocks, activeDraftId]);

  // ── Export to Word ─────────────────────────────────────────────────────────
  const exportDocx = useCallback(async () => {
    if (!activeDraftId) return;
    setIsExporting(true);
    try {
      const res = await draftsApi.exportDocx(activeDraftId);
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${activeDraftTitle || "contract"}.docx`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Word document downloaded");
    } catch {
      toast.error("Export failed");
    } finally {
      setIsExporting(false);
    }
  }, [activeDraftId, activeDraftTitle]);

  useEffect(() => () => { abortRef.current?.(); }, []);

  // ─────────────────────────────────────────────────────────────────────────
  // RENDER
  // ─────────────────────────────────────────────────────────────────────────

  if (view === "prompt") return <PromptView onStart={startDraft} onBack={() => setView("list")} />;

  if (view === "orchestrating") return (
    <OrchestrationView
      stage={currentStage}
      label={stageLabel}
      progress={progress}
      intent={intent}
      questions={questions}
      answers={answers}
      researchLog={researchLog}
      error={error}
      onAnswerChange={(id, val) => setAnswers(prev => ({ ...prev, [id]: val }))}
      onSubmitAnswers={submitAnswers}
      onBack={() => { abortRef.current?.(); changeView("list"); }}
    />
  );

  if (view === "sources") return (
    <SourcesView
      sources={rankedSources}
      rankingSummary={rankingSummary}
      selectedIds={selectedSourceIds}
      onToggle={(id) => setSelectedSourceIds(prev => {
        const next = new Set(prev);
        next.has(id) ? next.delete(id) : next.add(id);
        return next;
      })}
      onApprove={approveSources}
      onBack={() => changeView("list")}
      intent={intent}
    />
  );

  if (view === "editor") return (
    <EditorView
      draftId={activeDraftId}
      title={activeDraftTitle}
      blocks={blocks}
      issues={issues}
      sources={editorSources}
      status={draftStatus}
      activeBlock={activeBlock}
      editingBlock={editingBlock}
      isSaving={isSaving}
      isExporting={isExporting}
      adversarialFindings={adversarialFindings}
      adversarialRisk={adversarialRisk}
      adversarialAssessment={adversarialAssessment}
      adversarialMissingSections={adversarialMissingSections}
      contractType={contractTypeForFix}
      userRole={userRole}
      onSelectBlock={setActiveBlock}
      onStartEdit={(id, content) => setEditingBlock({ id, content })}
      onEditChange={(content) => setEditingBlock(prev => prev ? { ...prev, content } : null)}
      onSaveEdit={saveBlockEdit}
      onCancelEdit={() => setEditingBlock(null)}
      onExport={exportDocx}
      onBack={() => changeView("list")}
      onBlocksUpdated={setBlocks}
      onAdversarialFindingsUpdated={setAdversarialFindings}
      onStatusChange={setDraftStatus}
    />
  );

  // List view
  return (
    <div className="fade-in">
      <style>{STYLES}</style>

      {/* Header */}
      <div className="draft-header">
        <div className="draft-header-content">
          <div>
            <h1 className="draft-title">Contract Drafting</h1>
            <p className="draft-subtitle">Enterprise-grade AI-powered contract assembly with Claude</p>
          </div>
          <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
            <button className="btn-info" onClick={() => setShowInfoModal(true)}>
              <Info size={16} /> Workflow Info
            </button>
            <button className="btn-new-draft" onClick={() => changeView("prompt")}>
              <Sparkles size={16} />
              New Draft with AI
            </button>
          </div>
        </div>

        {/* Search bar */}
        <div className="draft-search">
          <Search size={15} className="search-icon" />
          <input
            placeholder="Search drafts by title or type…"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="search-input"
          />
        </div>
      </div>

      {/* Draft list */}
      {isLoading ? (
        <div className="loading-center"><Loader2 className="animate-spin" size={28} /></div>
      ) : filteredDrafts.length === 0 ? (
        <EmptyState onNew={() => changeView("prompt")} />
      ) : (
        <div className="draft-list">
          {filteredDrafts.map(draft => (
            <DraftCard key={draft.id} draft={draft} onClick={() => openDraft(draft.id)} onDelete={(e) => deleteDraft(draft.id, e)} />
          ))}
        </div>
      )}

      {showInfoModal && <DraftingInfoModal onClose={() => setShowInfoModal(false)} />}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Sub-views
// ─────────────────────────────────────────────────────────────────────────────

function PromptView({ onStart, onBack }: { onStart: (p: string) => void; onBack: () => void }) {
  const [prompt, setPrompt] = useState("");

  const EXAMPLES = [
    "Draft an NDA between Acme Corp and a software vendor for sharing API credentials",
    "I need a vendor contract for IT services, monthly billing, 12 month term",
    "Software license agreement for SaaS product, Indian jurisdiction",
    "Employment agreement for senior engineer role, Mumbai, CTC 30 LPA",
  ];

  return (
    <div className="fade-in prompt-view">
      <style>{STYLES}</style>
      <button onClick={onBack} className="back-link">
        <ArrowLeft size={15} /> Back to Drafts
      </button>

      <div className="prompt-card">
        <div className="prompt-header">
          <div className="prompt-icon-wrap">
            <Sparkles size={24} />
          </div>
          <div>
            <h2 className="prompt-title">Describe Your Contract</h2>
            <p className="prompt-desc">
              Tell Claude what contract you need — naturally, in your own words.
            </p>
          </div>
        </div>

        <textarea
          className="prompt-textarea"
          placeholder="e.g. Draft a mutual NDA between our company and a technology vendor for sharing confidential code and architecture…"
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (prompt.trim()) onStart(prompt.trim());
            }
          }}
          rows={6}
          autoFocus
        />

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: "16px" }}>
          <button
            className="btn-start"
            onClick={() => prompt.trim() && onStart(prompt.trim())}
            disabled={!prompt.trim()}
          >
            Start Drafting
            <ChevronRight size={16} />
          </button>
        </div>

        <div className="prompt-examples">
          <p className="examples-label">Try an example:</p>
          <div className="examples-grid">
            {EXAMPLES.map((ex, i) => (
              <button key={i} className="example-chip" onClick={() => setPrompt(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </div>

        <div className="prompt-footer" style={{ justifyContent: "center" }}>
          <div className="claude-badge">
            <Zap size={13} />
            <span>Powered by Claude Opus + Sonnet</span>
          </div>
        </div>
      </div>
    </div>
  );
}


function OrchestrationView({
  stage, label, progress, intent, questions, answers, researchLog, error,
  onAnswerChange, onSubmitAnswers, onBack,
}: {
  stage: string; label: string; progress: number; intent: DraftIntent | null;
  questions: DraftQuestion[]; answers: Record<string, string>;
  researchLog: { label: string; count?: number; agent?: string }[];
  error: string | null;
  onAnswerChange: (id: string, val: string) => void;
  onSubmitAnswers: () => void;
  onBack: () => void;
}) {
  const currentIdx = STAGE_INDEX[stage] ?? 0;

  return (
    <div className="fade-in orch-view">
      <style>{STYLES}</style>
      <button onClick={onBack} className="back-link">
        <ArrowLeft size={15} /> Cancel
      </button>

      {/* Progress bar */}
      <div className="orch-progress-bar">
        <div className="orch-progress-fill" style={{ width: `${progress}%` }} />
      </div>

      {/* Stage stepper */}
      <div className="stage-stepper">
        {STAGE_STEPS.slice(0, 8).map((step, i) => (
          <div
            key={step.id}
            className={`stage-step ${i < currentIdx ? "done" : i === currentIdx ? "active" : "pending"}`}
          >
            <div className="step-dot">
              {i < currentIdx ? <Check size={10} /> : i === currentIdx ? <Loader2 size={10} className="animate-spin" /> : null}
            </div>
            <span className="step-label">{step.label}</span>
          </div>
        ))}
      </div>

      {/* Current activity */}
      {!error && (
        <div className="orch-card">
          <div className="orch-thinking">
            <div className="thinking-pulse" />
            <p className="thinking-label">{label}</p>
          </div>

          {/* Intent card */}
          {intent && (
            <div className="intent-card">
              <div className="intent-type">{intent.contract_type_label}</div>
              <div className="intent-meta">
                <span><Globe size={12} /> {intent.jurisdiction}</span>
                <span><Scale size={12} /> {intent.governing_law}</span>
                <span className={`risk-badge risk-${intent.risk_level}`}>{intent.risk_level} risk</span>
              </div>
              {intent.purpose && <p className="intent-purpose">{intent.purpose}</p>}
            </div>
          )}

          {/* Research log */}
          {researchLog.length > 0 && (
            <div className="research-log">
              {researchLog.map((item, i) => (
                <div key={i} className="log-item">
                  <div className={`log-dot agent-${item.agent || "default"}`} />
                  <span>{item.label}</span>
                  {item.count !== undefined && <span className="log-count">{item.count}</span>}
                </div>
              ))}
            </div>
          )}

          {/* Questions form */}
          {questions.length > 0 && (
            <div className="questions-form">
              <h3 className="questions-title">
                <Info size={15} /> A few details needed to proceed
              </h3>
              {questions.map(q => (
                <div key={q.id} className="question-field">
                  <label className="q-label">{q.question}</label>
                  {q.type === "textarea" ? (
                    <textarea
                      className="q-input"
                      value={answers[q.id] || ""}
                      onChange={e => onAnswerChange(q.id, e.target.value)}
                      rows={3}
                    />
                  ) : q.type === "select" && q.options ? (
                    <select
                      className="q-input"
                      value={answers[q.id] || ""}
                      onChange={e => onAnswerChange(q.id, e.target.value)}
                    >
                      <option value="">Select…</option>
                      {q.options.map(opt => <option key={opt} value={opt}>{opt}</option>)}
                    </select>
                  ) : (
                    <input
                      type={q.type}
                      className="q-input"
                      value={answers[q.id] || ""}
                      onChange={e => onAnswerChange(q.id, e.target.value)}
                    />
                  )}
                </div>
              ))}
              <button className="btn-submit-answers" onClick={onSubmitAnswers}>
                Continue <ChevronRight size={15} />
              </button>
            </div>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="error-card">
          <AlertCircle size={20} />
          <div>
            <strong>Something went wrong</strong>
            <p>{error}</p>
          </div>
        </div>
      )}
    </div>
  );
}


function SourcesView({
  sources, rankingSummary, selectedIds, onToggle, onApprove, onBack, intent,
}: {
  sources: DraftSource[];
  rankingSummary: string;
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
  onApprove: () => void;
  onBack: () => void;
  intent: DraftIntent | null;
}) {
  const [filter, setFilter] = useState<string>("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const types = ["all", ...Array.from(new Set(sources.map(s => s.source_type)))];
  const displayed = filter === "all" ? sources : sources.filter(s => s.source_type === filter);

  const toggleExpand = (id: string) => {
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExpanded(next);
  };

  return (
    <div className="fade-in sources-view">
      <style>{STYLES}</style>

      <div className="sources-header">
        <button onClick={onBack} className="back-link">
          <ArrowLeft size={15} /> Back
        </button>
        <div>
          <h2 className="sources-title">Research Sources</h2>
          {intent && <p className="sources-sub">For: {intent.contract_type_label} · {intent.jurisdiction}</p>}
        </div>
        <button className="btn-approve-sources" onClick={onApprove}>
          <Sparkles size={16} />
          Approve & Draft with Claude Opus
          <ChevronRight size={15} />
        </button>
      </div>

      {rankingSummary && (
        <div className="ranking-summary">
          <Info size={14} />
          <p>{rankingSummary}</p>
        </div>
      )}

      {/* Filter tabs */}
      <div className="source-filters">
        {types.map(t => (
          <button
            key={t}
            className={`source-filter-btn ${filter === t ? "active" : ""}`}
            onClick={() => setFilter(t)}
          >
            {t.replace("_", " ")}
          </button>
        ))}
        <span className="selected-count">{selectedIds.size} / {sources.length} selected</span>
      </div>

      {/* Sources grid */}
      <div className="sources-grid">
        {displayed.map(src => {
          const isSelected = selectedIds.has(src.source_id);
          return (
            <div
              key={src.source_id}
              className={`source-card ${isSelected ? "selected" : ""}`}
              onClick={() => toggleExpand(src.source_id)}
            >
              <div className="source-card-header">
                <div className="source-type-badge" style={{ color: SOURCE_TYPE_COLOR[src.source_type] || "#888" }}>
                  {SOURCE_TYPE_ICON[src.source_type]}
                  <span>{src.source_type.replace("_", " ")}</span>
                </div>
                <div className={`source-checkbox ${isSelected ? "checked" : ""}`} onClick={e => { e.stopPropagation(); onToggle(src.source_id); }}>
                  {isSelected && <Check size={11} />}
                </div>
              </div>

              <h4 className="source-name">{src.source_name}</h4>
              <p className="source-snippet" style={{ marginBottom: expanded.has(src.source_id) ? 12 : 0 }}>
                {expanded.has(src.source_id) ? src.snippet : src.snippet?.slice(0, 120) + (src.snippet?.length > 120 ? "…" : "")}
              </p>

              {expanded.has(src.source_id) && (
                <div onClick={e => e.stopPropagation()}>
                  {src.relevance_score !== undefined && (
                    <div className="source-scores">
                      <div className="score-bar">
                        <span>Relevance</span>
                        <div className="bar-track"><div className="bar-fill" style={{ width: `${src.relevance_score}%` }} /></div>
                        <span>{src.relevance_score}</span>
                      </div>
                      {src.authority_score !== undefined && (
                        <div className="score-bar">
                          <span>Authority</span>
                          <div className="bar-track"><div className="bar-fill authority" style={{ width: `${src.authority_score}%` }} /></div>
                          <span>{src.authority_score}</span>
                        </div>
                      )}
                    </div>
                  )}

                  {src.url && (
                    <a href={src.url} target="_blank" rel="noopener noreferrer"
                      className="source-link">
                      <ExternalLink size={11} /> {src.domain}
                    </a>
                  )}

                  {src.reason && <p className="source-reason">{src.reason}</p>}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Sticky footer */}
      <div className="sources-footer">
        <span className="footer-note">
          <CheckCircle size={14} /> {selectedIds.size} source{selectedIds.size !== 1 ? "s" : ""} selected · Claude Opus will use these to assemble your contract
        </span>
        <button className="btn-approve-sources" onClick={onApprove}>
          <Sparkles size={16} />
          Approve & Draft
        </button>
      </div>
    </div>
  );
}


function EditorView({
  draftId, title, blocks, issues, sources, status,
  activeBlock, editingBlock, isSaving, isExporting,
  adversarialFindings, adversarialRisk, adversarialAssessment, adversarialMissingSections,
  contractType, userRole,
  onSelectBlock, onStartEdit, onEditChange, onSaveEdit, onCancelEdit,
  onExport, onBack, onBlocksUpdated, onAdversarialFindingsUpdated, onStatusChange,
}: {
  draftId: string | null;
  title: string;
  blocks: ContractBlock[];
  issues: DraftIssue[];
  sources: DraftSource[];
  status: string;
  activeBlock: string | null;
  editingBlock: { id: string; content: string } | null;
  isSaving: boolean;
  isExporting: boolean;
  adversarialFindings: AdversarialFinding[];
  adversarialRisk: string;
  adversarialAssessment: string;
  adversarialMissingSections: string[];
  contractType: string;
  userRole: string;
  onSelectBlock: (id: string) => void;
  onStartEdit: (id: string, content: string) => void;
  onEditChange: (content: string) => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onExport: () => void;
  onBack: () => void;
  onBlocksUpdated: (blocks: ContractBlock[]) => void;
  onAdversarialFindingsUpdated: (findings: AdversarialFinding[]) => void;
  onStatusChange: (status: string) => void;
}) {
  const [rightPanel, setRightPanel] = useState<"issues" | "sources" | "provenance" | "redteam">("issues");
  const [insertingFix, setInsertingFix] = useState<string | null>(null); // block_id being fixed
  const [expandedFindings, setExpandedFindings] = useState<Set<string>>(new Set());
  const [isApproving, setIsApproving] = useState(false);
  const blockRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const highIssues = issues.filter(i => i.severity === "high");
  const reviewNeeded = blocks.filter(b => b.needs_review);
  const criticalFindings = adversarialFindings.filter(f => f.severity === "critical");
  const canApprove = ["reviewer", "ops_admin", "super_admin"].includes(userRole);

  const toggleFinding = (id: string) => {
    const next = new Set(expandedFindings);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setExpandedFindings(next);
  };

  // Jump to clause from red-team panel
  const jumpToBlock = (blockId: string) => {
    onSelectBlock(blockId);
    const el = blockRefs.current[blockId];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.style.outline = "3px solid #dc2626";
      setTimeout(() => { if (el) el.style.outline = ""; }, 2000);
    }
  };

  // Approve draft — only for reviewer/admin
  const approveDraft = async () => {
    if (!draftId) return;
    setIsApproving(true);
    try {
      await draftsApi.approve(draftId);
      onStatusChange("approved");
      toast.success("Draft approved successfully!");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to approve draft");
    } finally {
      setIsApproving(false);
    }
  };

  // Insert fix: call API, update block content in state
  const handleInsertFix = async (finding: AdversarialFinding) => {
    if (!draftId) return;
    setInsertingFix(finding.block_id);
    try {
      const res = await draftsApi.autoInsertFix(draftId, finding.block_id, finding);
      const { corrected_content, change_summary, updated_findings } = res.data;
      const updatedBlocks = blocks.map(b =>
        b.block_id === finding.block_id
          ? { ...b, content: corrected_content, needs_review: false, review_reason: `Auto-fixed: ${change_summary}` }
          : b
      );
      onBlocksUpdated(updatedBlocks);
      if (updated_findings) {
        onAdversarialFindingsUpdated(updated_findings);
      }
      toast.success(`Fix applied: ${change_summary}`);
    } catch (e: any) {
      console.error("Fix insertion failed:", e);
      toast.error(e?.message || "Failed to generate fix. Please try again.");
    } finally {
      setInsertingFix(null);
    }
  };

  return (
    <div className="fade-in editor-view">
      <style>{STYLES}</style>

      {/* Toolbar */}
      <div className="editor-toolbar">
        <button onClick={onBack} className="back-link" style={{ marginBottom: 0 }}>
          <ArrowLeft size={15} /> Back to Drafts
        </button>
        <div className="toolbar-center">
          <h2 className="editor-title">{title}</h2>
          <span className={`status-badge status-${status}`}>{status}</span>
          {highIssues.length > 0 && (
            <span className="issue-badge-toolbar">
              <AlertTriangle size={12} /> {highIssues.length} issue{highIssues.length > 1 ? "s" : ""}
            </span>
          )}
          {criticalFindings.length > 0 && (
            <span className="rt-badge-toolbar">
              <Swords size={12} /> {criticalFindings.length} critical
            </span>
          )}
        </div>
        <div className="toolbar-actions">
          {canApprove && status !== "approved" && (
            <button
              className="btn-approve-draft"
              onClick={approveDraft}
              disabled={isApproving}
              title="Approve this contract draft"
            >
              {isApproving ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle size={14} />}
              Approve Draft
            </button>
          )}
          <button className="btn-export" onClick={onExport} disabled={isExporting}>
            {isExporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            Export Word
          </button>
        </div>
      </div>

      {/* Main layout: left = blocks, right = panels */}
      <div className="editor-layout">
        {/* Left: Contract Blocks */}
        <div className="blocks-panel">
          {reviewNeeded.length > 0 && (
            <div className="review-banner">
              <AlertTriangle size={14} />
              <span>{reviewNeeded.length} clause{reviewNeeded.length > 1 ? "s" : ""} require legal review</span>
            </div>
          )}

          {blocks.length === 0 ? (
            <div className="blocks-empty">
              <FileText size={40} />
              <p>No blocks yet</p>
            </div>
          ) : (
            <div className="blocks-list">
              {blocks.map(block => {
                const isActive = activeBlock === block.block_id;
                const isEditing = editingBlock?.id === block.block_id;
                const hasAdversarialFlag = adversarialFindings.some(f => f.block_id === block.block_id);

                return (
                  <div
                    key={block.block_id}
                    ref={el => { blockRefs.current[block.block_id] = el; }}
                    className={`block-item ${isActive ? "active" : ""} ${block.needs_review ? "needs-review" : ""} ${hasAdversarialFlag ? "has-adversarial" : ""}`}
                    onClick={() => onSelectBlock(block.block_id)}
                  >
                    <div className="block-header">
                      <div className="block-number">{block.clause_number}</div>
                      <h3 className="block-heading">{block.heading}</h3>
                      <div className="block-badges">
                        {block.is_locked && <span className="badge-locked"><Lock size={10} /> Locked</span>}
                        {block.needs_review && <span className="badge-review"><AlertTriangle size={10} /> Review</span>}
                        {hasAdversarialFlag && <span className="badge-adversarial"><Swords size={10} /> Risk</span>}
                        <span className={`badge-prov badge-${block.provenance}`}>{block.provenance}</span>
                      </div>
                    </div>

                    {block.needs_review && block.review_reason && (
                      <div className="review-note">
                        <AlertCircle size={11} /> {block.review_reason}
                      </div>
                    )}

                    {isEditing ? (
                      <div className="block-edit">
                        <textarea
                          className="block-edit-ta"
                          value={editingBlock.content}
                          onChange={e => onEditChange(e.target.value)}
                          rows={8}
                          onClick={e => e.stopPropagation()}
                        />
                        <div className="block-edit-actions">
                          <button className="btn-cancel-edit" onClick={(e) => { e.stopPropagation(); onCancelEdit(); }}>
                            Cancel
                          </button>
                          <button className="btn-save-edit" onClick={(e) => { e.stopPropagation(); onSaveEdit(); }}>
                            {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                            Save
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="block-content" style={{ whiteSpace: "pre-wrap" }}>
                        {block.content}
                      </div>
                    )}

                    {isActive && !isEditing && !block.is_locked && (
                      <button
                        className="btn-edit-block"
                        onClick={e => { e.stopPropagation(); onStartEdit(block.block_id, block.content); }}
                      >
                        <Edit3 size={12} /> Edit Clause
                      </button>
                    )}

                    {block.precedent_source && (
                      <div className="block-source-ref">
                        <Database size={10} /> {block.precedent_source}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right: Info panels */}
        <div className="info-panel">
          <div className="panel-tabs">
            <button className={`panel-tab ${rightPanel === "issues" ? "active" : ""}`}
              onClick={() => setRightPanel("issues")}>
              <AlertTriangle size={13} /> Issues {issues.length > 0 && `(${issues.length})`}
            </button>
            <button className={`panel-tab ${rightPanel === "redteam" ? "active" : ""}`}
              onClick={() => setRightPanel("redteam")}>
              <Swords size={13} />
              <span>Red Team</span>
              {adversarialFindings.length > 0 && (
                <span className={`rt-tab-badge ${criticalFindings.length > 0 ? "critical" : ""}`}>
                  {adversarialFindings.length}
                </span>
              )}
            </button>
            <button className={`panel-tab ${rightPanel === "sources" ? "active" : ""}`}
              onClick={() => setRightPanel("sources")}>
              <Globe size={13} /> Sources
            </button>
            <button className={`panel-tab ${rightPanel === "provenance" ? "active" : ""}`}
              onClick={() => setRightPanel("provenance")}>
              <BookOpen size={13} /> Provenance
            </button>
          </div>

          <div className="panel-content">
            {rightPanel === "issues" && (
              <div className="issues-list">
                {issues.length === 0 ? (
                  <div className="panel-empty">
                    <CheckCircle size={28} className="text-green" />
                    <p>No issues flagged</p>
                  </div>
                ) : issues.map((issue, i) => (
                  <div key={i} className={`issue-item severity-${issue.severity}`}>
                    <div className="issue-severity">{issue.severity.toUpperCase()}</div>
                    <p className="issue-desc">{issue.description}</p>
                    {issue.clause_affected && (
                      <span className="issue-clause">Clause: {issue.clause_affected}</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {rightPanel === "redteam" && (
              <div className="rt-panel">
                {/* Overall risk header */}
                {adversarialRisk && (
                  <div className={`rt-risk-header rt-risk-${adversarialRisk}`}>
                    <Swords size={14} />
                    <div>
                      <div className="rt-risk-label">Overall Risk: <strong>{adversarialRisk.toUpperCase()}</strong></div>
                      {adversarialAssessment && <div className="rt-risk-assessment">{adversarialAssessment}</div>}
                    </div>
                  </div>
                )}

                {/* Missing sections warning */}
                {adversarialMissingSections.length > 0 && (
                  <div className="rt-missing">
                    <AlertTriangle size={12} />
                    <span><strong>Missing sections:</strong> {adversarialMissingSections.join(", ")}</span>
                  </div>
                )}

                {adversarialFindings.length === 0 ? (
                  <div className="panel-empty">
                    <CheckCircle size={28} style={{ color: "#10b981" }} />
                    <p>No vulnerabilities found</p>
                    <p style={{ fontSize: 11, color: "var(--text3)" }}>Adversarial agent found no exploitable gaps</p>
                  </div>
                ) : (
                  <div className="rt-findings">
                    <div className="rt-header-note">
                      <Swords size={11} />
                      <span>Adversarial Claude Agent — {adversarialFindings.length} vulnerabilities found</span>
                    </div>
                    {adversarialFindings.map((finding, i) => {
                      const isInserting = insertingFix === finding.block_id;
                      const targetBlock = blocks.find(b => b.block_id === finding.block_id);
                      return (
                        <div key={i} className={`rt-finding-card rt-sev-${finding.severity}`} onClick={() => toggleFinding(i.toString())}>
                          <div className="rt-finding-header">
                            <span className={`rt-sev-badge rt-badge-${finding.severity}`}>
                              {finding.severity.toUpperCase()}
                            </span>
                            <span className="rt-clause-ref">{finding.clause_ref}</span>
                          </div>

                          <blockquote className="rt-problematic-text" style={{ cursor: "pointer", marginBottom: expandedFindings.has(i.toString()) ? 10 : 0 }}>
                            "{finding.problematic_text.slice(0, 80)}{finding.problematic_text.length > 80 ? "..." : ""}"
                            {!expandedFindings.has(i.toString()) && <span style={{fontSize: 10, color: "var(--text3)", display: "block", marginTop: 4}}>Click to view details</span>}
                          </blockquote>

                          {expandedFindings.has(i.toString()) && (
                            <div onClick={e => e.stopPropagation()}>
                              <blockquote className="rt-problematic-text" style={{ marginTop: -10, borderTop: "none", borderTopRightRadius: 0, paddingTop: 0 }}>
                                "{finding.problematic_text}"
                              </blockquote>
                              
                              <div className="rt-exploit-label">How opposing counsel exploits this:</div>
                              <p className="rt-exploit">{finding.exploit}</p>

                              <div className="rt-fix-label">Recommended fix:</div>
                              <p className="rt-fix">{finding.suggested_fix}</p>

                              <div className="rt-actions">
                                {targetBlock && (
                                  <button
                                    className="btn-jump-clause"
                                    onClick={() => jumpToBlock(finding.block_id)}
                                  >
                                    <ArrowLeft size={10} style={{ transform: "rotate(180deg)" }} />
                                    Jump to Clause
                                  </button>
                                )}
                                {targetBlock && !targetBlock.is_locked && (
                                  <button
                                    className="btn-insert-fix"
                                    disabled={isInserting}
                                    onClick={() => handleInsertFix(finding)}
                                  >
                                    {isInserting
                                      ? <><Loader2 size={10} className="animate-spin" /> Generating fix…</>
                                      : <><Check size={10} /> Insert Fix</>}
                                  </button>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {rightPanel === "sources" && (
              <div className="panel-sources">
                {sources.length === 0 ? (
                  <div className="panel-empty"><Globe size={28} /><p>No sources</p></div>
                ) : sources.map((src, i) => (
                  <div key={i} className="mini-source">
                    <div className="mini-source-type" style={{ color: SOURCE_TYPE_COLOR[src.source_type] }}>
                      {SOURCE_TYPE_ICON[src.source_type]} {src.source_type}
                    </div>
                    <strong>{src.source_name}</strong>
                    {src.url && <a href={src.url} target="_blank" rel="noopener noreferrer"
                      className="mini-source-link"><ExternalLink size={10} /></a>}
                  </div>
                ))}
              </div>
            )}

            {rightPanel === "provenance" && (
              <div className="panel-prov">
                {blocks.map(b => (
                  <div key={b.block_id} className="prov-item">
                    <span className="prov-num">{b.clause_number}</span>
                    <span className="prov-heading">{b.heading}</span>
                    <span className={`badge-prov badge-${b.provenance}`}>{b.provenance}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><FileText size={44} /></div>
      <h2>No Drafts Yet</h2>
      <p>Start your first AI-powered contract with Claude</p>
      <button className="btn-new-draft" onClick={onNew}>
        <Sparkles size={15} /> New Draft with AI
      </button>
    </div>
  );
}


function DraftCard({ draft, onClick, onDelete }: { draft: DraftListItem; onClick: () => void; onDelete: (e: React.MouseEvent) => void }) {
  return (
    <div className="draft-card" onClick={onClick}>
      <div className="dc-icon">
        <FileText size={20} />
      </div>
      <div className="dc-info">
        <strong className="dc-title">{draft.title}</strong>
        <span className="dc-meta">
          {draft.contract_type.replace("_", " ")} ·{" "}
          {new Date(draft.created_at).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
        </span>
      </div>
      <div className="dc-right">
        <span className={`status-badge status-${draft.status}`}>{draft.status}</span>
        <button className="btn-delete-draft" onClick={onDelete} title="Delete Draft">
          <Trash2 size={16} />
        </button>
        <ChevronRight size={16} className="dc-arrow" />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Drafting Info Modal
// ─────────────────────────────────────────────────────────────────────────────

function DraftingInfoModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop fade-in" onClick={onClose}>
      <style>{STYLES}</style>
      <div className="info-modal-content" onClick={e => e.stopPropagation()}>
        <button className="btn-close-modal" onClick={onClose}><X size={20} /></button>
        
        <div className="info-modal-header">
          <div className="im-icon"><Shield size={24} /></div>
          <div>
            <h2 className="im-title">Secure AI Drafting Workflow</h2>
            <p className="im-subtitle">Transparent, enterprise-grade contract assembly built for legal professionals.</p>
          </div>
        </div>

        <div className="im-body">
          <div className="im-section">
            <h3><Lock size={16} /> Strict Data Security & PII Masking</h3>
            <p>Your client's confidentiality is our top priority. Before any data leaves your secure environment or interacts with external AI models, our proprietary redaction engine automatically masks all Personally Identifiable Information (PII) and Protected Health Information (PHI).</p>
            <div style={{ marginTop: "10px", padding: "12px", background: "var(--bg)", borderLeft: "3px solid var(--accent)", borderRadius: "0 8px 8px 0", fontSize: "12px" }}>
              <strong>Example of Auto-Masking:</strong><br />
              <span style={{ color: "var(--text3)", textDecoration: "line-through" }}>"Draft an NDA for Zuari Company located in Mumbai..."</span><br />
              <span style={{ color: "var(--green)", fontWeight: 600 }}>"Draft an NDA for [CLIENT_COMPANY_1] located in [CITY_1]..."</span>
            </div>
            <p style={{ marginTop: "10px" }}>The data remains masked throughout the entire legal research and drafting process, and is only unmasked locally when the final draft is presented to you on this screen.</p>
          </div>

          <div className="im-section">
            <h3><Activity size={16} /> How Your Contract is Built</h3>
            <div className="im-timeline">
              <div className="im-step">
                <div className="im-step-dot">1</div>
                <div className="im-step-info">
                  <strong>Intent Analysis & Masking</strong>
                  <span>The system securely identifies the contract type, governing law, and jurisdiction while redacting sensitive entities.</span>
                </div>
              </div>
              <div className="im-step">
                <div className="im-step-dot">2</div>
                <div className="im-step-info">
                  <strong>Grounded Legal Research</strong>
                  <span>Our orchestrator queries your secure internal database for relevant precedents, and retrieves up-to-date statutes and case laws.</span>
                </div>
              </div>
              <div className="im-step">
                <div className="im-step-dot">3</div>
                <div className="im-step-info">
                  <strong>Structural Assembly</strong>
                  <span>The contract is drafted clause-by-clause. Every generated clause is directly linked to its citing source for complete transparency and explainability.</span>
                </div>
              </div>
              <div className="im-step">
                <div className="im-step-dot">4</div>
                <div className="im-step-info">
                  <strong>Adversarial Red-Team Review</strong>
                  <span>Before you see the draft, an independent AI agent acts as "opposing counsel" to actively hunt for loopholes, ambiguities, and missing protections, providing you with actionable, one-click fixes.</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        
        <div className="im-footer">
          <button className="btn-modal-primary" onClick={onClose}>Understood</button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Styles
// ─────────────────────────────────────────────────────────────────────────────

const STYLES = `
  /* Layout */
  .draft-header { margin-bottom: 32px; }
  .draft-header-content { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
  .draft-title { font-size: 28px; font-weight: 800; margin: 0 0 6px; color: #000; }
  .draft-subtitle { color: var(--text3); font-size: 14px; margin: 0; }

  .btn-info { display: flex; align-items: center; gap: 8px; padding: 11px 18px; background: var(--bg2); color: var(--text2); border: 1px solid var(--border); border-radius: 12px; font-weight: 600; font-size: 13px; cursor: pointer; transition: all 0.2s; }
  .btn-info:hover { background: var(--bg); color: var(--text1); border-color: var(--accent); }

  .btn-new-draft { display: flex; align-items: center; gap: 8px; padding: 11px 22px; background: linear-gradient(135deg, var(--accent), #7c5cfc); color: #fff; border: none; border-radius: 12px; font-weight: 700; font-size: 14px; cursor: pointer; box-shadow: 0 4px 20px rgba(124,92,252,0.4); transition: all 0.2s; }
  .empty-state .btn-new-draft { margin: 0 auto; }
  .btn-new-draft:hover { transform: translateY(-2px); box-shadow: 0 6px 28px rgba(124,92,252,0.5); }

  .draft-search { position: relative; max-width: 420px; }
  .search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text3); }
  .search-input { width: 100%; padding: 10px 12px 10px 36px; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; color: var(--text1); font-size: 14px; outline: none; transition: border-color 0.2s; box-sizing: border-box; }
  .search-input:focus { border-color: var(--accent); }

  .loading-center { display: flex; justify-content: center; align-items: center; padding: 80px; color: var(--text3); }
  .empty-state { text-align: center; padding: 100px 20px; }
  .empty-icon { width: 80px; height: 80px; background: var(--accent-light); border-radius: 20px; display: flex; align-items: center; justify-content: center; margin: 0 auto 24px; color: var(--accent); }
  .empty-state h2 { font-size: 22px; font-weight: 700; margin: 0 0 8px; }
  .empty-state p { color: var(--text3); margin: 0 0 24px; }

  /* Draft list */
  .draft-list { display: flex; flex-direction: column; gap: 10px; }
  .draft-card { display: flex; align-items: center; gap: 16px; padding: 18px 20px; background: var(--bg2); border: 1px solid var(--border); border-radius: 14px; cursor: pointer; transition: all 0.2s; }
  .draft-card:hover { border-color: var(--accent); background: var(--bg3, var(--bg2)); transform: translateY(-1px); box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
  .dc-icon { width: 44px; height: 44px; background: var(--accent-light); border-radius: 12px; display: flex; align-items: center; justify-content: center; color: var(--accent); flex-shrink: 0; }
  .dc-info { flex: 1; min-width: 0; }
  .dc-title { display: block; font-size: 15px; font-weight: 600; color: var(--text1); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .dc-meta { font-size: 12px; color: var(--text3); margin-top: 3px; display: block; }
  .dc-right { display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
  .btn-delete-draft { background: none; border: none; color: #ef4444; padding: 8px; border-radius: 8px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.2s; }
  .btn-delete-draft:hover { background: rgba(239, 68, 68, 0.1); }
  .dc-arrow { color: var(--text3); }

  /* Status badges */
  .status-badge { padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
  .status-draft { background: rgba(245,158,11,0.12); color: #f59e0b; }
  .status-approved { background: rgba(16,185,129,0.12); color: #10b981; }
  .status-in_review { background: rgba(59,130,246,0.12); color: #3b82f6; }

  /* Back link */
  .back-link { display: flex; align-items: center; gap: 6px; color: var(--text3); background: none; border: none; cursor: pointer; font-size: 13px; font-weight: 500; margin-bottom: 20px; padding: 0; transition: color 0.15s; }
  .back-link:hover { color: var(--text1); }

  /* ── Prompt view ── */
  .prompt-view { max-width: 720px; margin: 0 auto; }
  .prompt-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 16px; padding: 40px; }
  .prompt-header { display: flex; align-items: flex-start; gap: 20px; margin-bottom: 28px; }
  .prompt-icon-wrap { width: 48px; height: 48px; background: linear-gradient(135deg, var(--accent), #7c5cfc); border-radius: 14px; display: flex; align-items: center; justify-content: center; color: #fff; flex-shrink: 0; }
  .prompt-title { font-size: 22px; font-weight: 800; margin: 0 0 6px; }
  .prompt-desc { color: var(--text3); font-size: 14px; margin: 0; }
  .prompt-textarea { width: 100%; padding: 16px; background: var(--white); border: 1.5px solid var(--border); border-radius: 12px; color: var(--text1); font-size: 15px; font-family: inherit; resize: vertical; outline: none; transition: border-color 0.2s; box-sizing: border-box; }
  .prompt-textarea:focus { border-color: var(--accent); }
  .examples-label { font-size: 12px; font-weight: 600; color: var(--text3); margin: 20px 0 10px; text-transform: uppercase; letter-spacing: 0.04em; }
  .examples-grid { display: flex; flex-direction: column; gap: 6px; }
  .example-chip { text-align: left; padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text2); font-size: 13px; cursor: pointer; transition: all 0.15s; }
  .example-chip:hover { border-color: var(--accent); color: var(--accent); }
  .prompt-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--border); }
  .claude-badge { display: flex; align-items: center; gap: 6px; color: var(--text3); font-size: 12px; }
  .btn-start { display: flex; align-items: center; gap: 8px; padding: 12px 28px; background: linear-gradient(135deg, var(--accent), #7c5cfc); color: #fff; border: none; border-radius: 12px; font-weight: 700; font-size: 15px; cursor: pointer; transition: all 0.2s; }
  .btn-start:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-start:not(:disabled):hover { transform: translateY(-2px); box-shadow: 0 6px 24px rgba(124,92,252,0.4); }

  /* ── Orchestration view ── */
  .orch-view { max-width: 800px; margin: 0 auto; }
  .orch-progress-bar { height: 4px; background: var(--border); border-radius: 4px; margin-bottom: 28px; overflow: hidden; }
  .orch-progress-fill { height: 100%; background: linear-gradient(90deg, var(--accent), #7c5cfc); border-radius: 4px; transition: width 0.6s ease; }

  .stage-stepper { display: flex; gap: 0; margin-bottom: 32px; overflow-x: auto; }
  .stage-step { display: flex; flex-direction: column; align-items: center; gap: 6px; flex: 1; min-width: 60px; position: relative; }
  .stage-step:not(:last-child)::after { content: ""; position: absolute; top: 11px; left: 50%; width: 100%; height: 2px; background: var(--border); z-index: 0; }
  .stage-step.done::after, .stage-step.active::after { background: var(--accent); }
  .step-dot { width: 22px; height: 22px; border-radius: 50%; border: 2px solid var(--border); background: var(--bg2); display: flex; align-items: center; justify-content: center; position: relative; z-index: 1; transition: all 0.3s; }
  .stage-step.done .step-dot { background: var(--accent); border-color: var(--accent); color: #fff; }
  .stage-step.active .step-dot { border-color: var(--accent); background: var(--accent-light); color: var(--accent); box-shadow: 0 0 0 4px rgba(124,92,252,0.15); }
  .step-label { font-size: 10px; color: var(--text3); text-align: center; white-space: nowrap; }
  .stage-step.done .step-label, .stage-step.active .step-label { color: var(--accent); font-weight: 600; }

  .orch-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 18px; padding: 32px; }
  .orch-thinking { display: flex; align-items: center; gap: 14px; margin-bottom: 24px; }
  .thinking-pulse { width: 10px; height: 10px; border-radius: 50%; background: var(--accent); animation: pulse 1.4s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity:1; transform: scale(1); } 50% { opacity: 0.5; transform: scale(0.8); } }
  .thinking-label { font-size: 16px; font-weight: 600; color: var(--text1); }

  .intent-card { background: var(--bg); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
  .intent-type { font-size: 18px; font-weight: 800; color: var(--text1); margin-bottom: 10px; }
  .intent-meta { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; font-size: 12px; color: var(--text3); }
  .intent-meta span { display: flex; align-items: center; gap: 4px; }
  .risk-badge { padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 700; }
  .risk-badge.risk-low { background: rgba(16,185,129,0.12); color: #10b981; }
  .risk-badge.risk-medium { background: rgba(245,158,11,0.12); color: #f59e0b; }
  .risk-badge.risk-high { background: rgba(239,68,68,0.12); color: #ef4444; }
  .intent-purpose { font-size: 13px; color: var(--text2); margin: 0; }

  .research-log { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
  .log-item { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--text2); }
  .log-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .log-dot.agent-web { background: #0ea5e9; }
  .log-dot.agent-internal { background: #7c5cfc; }
  .log-dot.agent-acts { background: #f59e0b; }
  .log-dot.agent-query_planner { background: #10b981; }
  .log-dot.agent-default { background: var(--text3); }
  .log-count { margin-left: auto; font-weight: 700; color: var(--accent); font-size: 12px; }

  .questions-form { border-top: 1px solid var(--border); padding-top: 24px; }
  .questions-title { display: flex; align-items: center; gap: 8px; font-size: 15px; font-weight: 700; margin: 0 0 20px; color: var(--text1); }
  .question-field { margin-bottom: 16px; }
  .q-label { display: block; font-size: 13px; font-weight: 600; color: var(--text2); margin-bottom: 8px; }
  .q-input { width: 100%; padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 10px; color: var(--text1); font-size: 14px; font-family: inherit; outline: none; box-sizing: border-box; }
  .q-input:focus { border-color: var(--accent); }
  .btn-submit-answers { display: flex; align-items: center; gap: 8px; padding: 12px 24px; background: var(--accent); color: #fff; border: none; border-radius: 10px; font-weight: 700; cursor: pointer; margin-top: 8px; transition: all 0.2s; }
  .btn-submit-answers:hover { background: var(--accent-dark, var(--accent)); opacity: 0.9; }

  .error-card { display: flex; gap: 14px; align-items: flex-start; background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.25); border-radius: 14px; padding: 20px; margin-top: 20px; color: #ef4444; }
  .error-card strong { display: block; font-weight: 700; margin-bottom: 4px; }
  .error-card p { margin: 0; font-size: 13px; color: var(--text2); }

  /* ── Sources view ── */
  .sources-view { max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; min-height: calc(100vh - 80px); }
  .sources-header { display: flex; align-items: flex-start; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }
  .sources-title { font-size: 22px; font-weight: 800; margin: 0 0 4px; }
  .sources-sub { color: var(--text3); font-size: 13px; margin: 0; }
  .btn-approve-sources { display: flex; align-items: center; gap: 8px; padding: 11px 22px; background: linear-gradient(135deg, var(--accent), #7c5cfc); color: #fff; border: none; border-radius: 12px; font-weight: 700; font-size: 14px; cursor: pointer; white-space: nowrap; margin-left: auto; box-shadow: 0 4px 16px rgba(124,92,252,0.35); transition: all 0.2s; }
  .btn-approve-sources:hover { transform: translateY(-2px); box-shadow: 0 6px 24px rgba(124,92,252,0.45); }

  .ranking-summary { display: flex; gap: 10px; align-items: flex-start; background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; font-size: 13px; color: var(--text2); }

  .source-filters { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 20px; }
  .source-filter-btn { padding: 6px 14px; background: var(--bg2); border: 1px solid var(--border); border-radius: 20px; color: var(--text2); font-size: 12px; font-weight: 600; cursor: pointer; text-transform: capitalize; transition: all 0.15s; }
  .source-filter-btn.active, .source-filter-btn:hover { background: var(--accent-light); border-color: var(--accent); color: var(--accent); }
  .selected-count { margin-left: auto; font-size: 12px; color: var(--text3); font-weight: 600; }

  .sources-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; padding-bottom: 40px; margin-bottom: auto; }
  .source-card { background: var(--white); border: 1px solid var(--border); border-radius: 12px; padding: 18px; cursor: pointer; transition: all 0.2s; position: relative; }
  .source-card:hover { border-color: var(--accent); transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
  .source-card.selected { border-color: var(--accent); background: var(--accent-light); }
  .source-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .source-type-badge { display: flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; }
  .source-checkbox { width: 20px; height: 20px; border-radius: 6px; border: 2px solid var(--border); display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: all 0.15s; }
  .source-checkbox.checked { background: var(--accent); border-color: var(--accent); color: #fff; }
  .source-name { font-size: 14px; font-weight: 700; color: var(--text1); margin: 0 0 8px; }
  .source-snippet { font-size: 12px; color: var(--text3); margin: 0 0 12px; line-height: 1.5; }
  .source-scores { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
  .score-bar { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--text3); }
  .score-bar > span:first-child { width: 55px; flex-shrink: 0; }
  .bar-track { flex: 1; height: 4px; background: var(--border); border-radius: 4px; overflow: hidden; }
  .bar-fill { height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.3s; }
  .bar-fill.authority { background: #0ea5e9; }
  .source-link { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text3); text-decoration: none; transition: color 0.15s; }
  .source-link:hover { color: var(--accent); }
  .source-reason { font-size: 11px; color: var(--text3); margin: 8px 0 0; font-style: italic; }

  .sources-footer { position: sticky; bottom: 0; background: rgba(255, 255, 255, 0.9); border-top: 1px solid var(--border); border-radius: 16px 16px 0 0; padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; z-index: 100; backdrop-filter: blur(12px); box-shadow: 0 -4px 20px rgba(0,0,0,0.05); margin-top: 20px; }
  .footer-note { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text2); }
  .text-green { color: #10b981; }

  /* ── Editor view ── */
  .editor-view { display: flex; flex-direction: column; height: 100%; }
  .editor-toolbar { display: flex; align-items: center; gap: 16px; padding: 0 0 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
  .toolbar-center { flex: 1; display: flex; align-items: center; gap: 10px; min-width: 0; }
  .editor-title { font-size: 18px; font-weight: 800; margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .issue-badge-toolbar { display: flex; align-items: center; gap: 5px; padding: 3px 10px; background: rgba(245,158,11,0.12); color: #f59e0b; border-radius: 20px; font-size: 11px; font-weight: 700; flex-shrink: 0; }
  .toolbar-actions { display: flex; gap: 10px; flex-shrink: 0; }
  .btn-export { display: flex; align-items: center; gap: 6px; padding: 9px 18px; background: var(--accent); color: #fff; border: none; border-radius: 10px; font-weight: 700; font-size: 13px; cursor: pointer; transition: all 0.2s; }
  .btn-export:disabled { opacity: 0.6; cursor: not-allowed; }
  .btn-approve-draft { display: flex; align-items: center; gap: 6px; padding: 9px 18px; background: linear-gradient(135deg, #059669, #047857); color: #fff; border: none; border-radius: 10px; font-weight: 700; font-size: 13px; cursor: pointer; transition: all 0.2s; box-shadow: 0 2px 8px rgba(5,150,105,0.35); }
  .btn-approve-draft:hover:not(:disabled) { background: linear-gradient(135deg, #10b981, #059669); transform: translateY(-1px); box-shadow: 0 4px 14px rgba(5,150,105,0.45); }
  .btn-approve-draft:disabled { opacity: 0.6; cursor: not-allowed; }


  .editor-layout { display: grid; grid-template-columns: 1fr 340px; gap: 24px; flex: 1; min-height: 0; }

  /* Blocks panel */
  .blocks-panel { overflow-y: auto; }
  .review-banner { display: flex; align-items: center; gap: 8px; background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.25); border-radius: 10px; padding: 10px 16px; font-size: 13px; color: #f59e0b; margin-bottom: 16px; }
  .blocks-empty { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 60px; color: var(--text3); }
  .blocks-list { display: flex; flex-direction: column; gap: 12px; }
  .block-item { background: var(--white); border: 1px solid var(--border); border-radius: 12px; padding: 24px; cursor: pointer; transition: all 0.15s; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
  .block-item:hover { border-color: var(--border2); }
  .block-item.active { border-color: var(--accent); box-shadow: 0 4px 12px rgba(91,79,207,0.06); }
  .block-item.needs-review { border-left: 3px solid #f59e0b; }
  .block-header { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
  .block-number { font-size: 13px; font-weight: 700; color: var(--text3); width: 28px; flex-shrink: 0; }
  .block-heading { font-size: 15px; font-weight: 600; margin: 0; flex: 1; color: var(--text1); }
  .block-badges { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }
  .badge-locked { display: flex; align-items: center; gap: 3px; padding: 2px 7px; background: rgba(100,100,100,0.06); border-radius: 20px; font-size: 10px; font-weight: 600; color: var(--text3); }
  .badge-review { display: flex; align-items: center; gap: 3px; padding: 2px 7px; background: rgba(245,158,11,0.08); border-radius: 20px; font-size: 10px; font-weight: 600; color: #d97706; }
  .badge-prov { padding: 2px 7px; border-radius: 20px; font-size: 10px; font-weight: 700; }
  .badge-template { background: rgba(16,185,129,0.12); color: #10b981; }
  .badge-precedent { background: rgba(232,121,249,0.12); color: #e879f9; }
  .badge-generated { background: rgba(59,130,246,0.12); color: #3b82f6; }
  .review-note { display: flex; align-items: center; gap: 6px; font-size: 11px; color: #f59e0b; background: rgba(245,158,11,0.06); border-radius: 6px; padding: 6px 10px; margin-bottom: 10px; }
  .block-content { font-size: 13px; color: var(--text2); line-height: 1.6; max-height: 200px; overflow-y: auto; }
  .btn-edit-block { display: flex; align-items: center; gap: 6px; padding: 7px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text2); font-size: 12px; font-weight: 600; cursor: pointer; margin-top: 12px; transition: all 0.15s; }
  .btn-edit-block:hover { border-color: var(--accent); color: var(--accent); }
  .block-source-ref { display: flex; align-items: center; gap: 5px; font-size: 10px; color: var(--text3); margin-top: 8px; }

  .block-edit { margin-top: 12px; }
  .block-edit-ta { width: 100%; padding: 12px; background: var(--bg); border: 1.5px solid var(--accent); border-radius: 10px; color: var(--text1); font-size: 13px; font-family: inherit; resize: vertical; outline: none; box-sizing: border-box; }
  .block-edit-actions { display: flex; gap: 8px; margin-top: 10px; justify-content: flex-end; }
  .btn-cancel-edit { padding: 7px 14px; background: none; border: 1px solid var(--border); border-radius: 8px; color: var(--text2); font-size: 12px; cursor: pointer; }
  .btn-save-edit { display: flex; align-items: center; gap: 5px; padding: 7px 14px; background: var(--accent); color: #fff; border: none; border-radius: 8px; font-size: 12px; font-weight: 700; cursor: pointer; }

  /* Info panel */
  .info-panel { background: var(--bg2); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; display: flex; flex-direction: column; height: fit-content; position: sticky; top: 0; }
  .panel-tabs { display: flex; border-bottom: 1px solid var(--border); }
  .panel-tab { flex: 1; display: flex; align-items: center; justify-content: center; gap: 5px; padding: 12px 8px; background: none; border: none; color: var(--text3); font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.15s; border-bottom: 2px solid transparent; margin-bottom: -1px; }
  .panel-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .panel-content { padding: 16px; max-height: 600px; overflow-y: auto; }
  .panel-empty { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 40px 16px; color: var(--text3); font-size: 13px; }
  .panel-empty .text-green { color: #10b981; }
  .issues-list { display: flex; flex-direction: column; gap: 10px; }
  .issue-item { padding: 12px; border-radius: 10px; border: 1px solid; }
  .issue-item.severity-high { background: rgba(239,68,68,0.06); border-color: rgba(239,68,68,0.2); }
  .issue-item.severity-medium { background: rgba(245,158,11,0.06); border-color: rgba(245,158,11,0.2); }
  .issue-item.severity-low { background: rgba(100,100,100,0.04); border-color: var(--border); }
  .issue-severity { font-size: 10px; font-weight: 800; letter-spacing: 0.05em; margin-bottom: 4px; }
  .severity-high .issue-severity { color: #ef4444; }
  .severity-medium .issue-severity { color: #f59e0b; }
  .severity-low .issue-severity { color: var(--text3); }
  .issue-desc { font-size: 12px; color: var(--text2); margin: 0 0 4px; }
  .issue-clause { font-size: 11px; color: var(--text3); }
  .panel-sources { display: flex; flex-direction: column; gap: 10px; }
  .mini-source { display: flex; flex-direction: column; gap: 3px; padding: 10px; background: var(--bg); border-radius: 8px; }
  .mini-source-type { display: flex; align-items: center; gap: 5px; font-size: 10px; font-weight: 700; text-transform: uppercase; }
  .mini-source strong { font-size: 12px; color: var(--text1); }
  .mini-source-link { display: inline-flex; align-items: center; color: var(--text3); transition: color 0.15s; }
  .mini-source-link:hover { color: var(--accent); }
  .panel-prov { display: flex; flex-direction: column; gap: 8px; }
  .prov-item { display: flex; align-items: center; gap: 8px; font-size: 12px; }
  .prov-num { font-weight: 700; color: var(--accent); width: 24px; flex-shrink: 0; }
  .prov-heading { flex: 1; color: var(--text2); }

  /* ── Red Team panel ── */
  .log-dot.agent-red_team { background: #dc2626; }

  .rt-badge-toolbar { display: flex; align-items: center; gap: 5px; padding: 3px 10px; background: rgba(220,38,38,0.1); color: #dc2626; border-radius: 20px; font-size: 11px; font-weight: 700; flex-shrink: 0; }
  .rt-tab-badge { display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 18px; padding: 0 5px; background: rgba(100,100,100,0.15); color: var(--text3); border-radius: 20px; font-size: 10px; font-weight: 800; margin-left: 4px; }
  .rt-tab-badge.critical { background: rgba(220,38,38,0.1); color: #dc2626; }

  .badge-adversarial { display: flex; align-items: center; gap: 3px; padding: 2px 7px; background: rgba(220,38,38,0.06); border-radius: 20px; font-size: 10px; font-weight: 600; color: #dc2626; }
  .block-item.has-adversarial { border-left: 3px solid rgba(220,38,38,0.2); }

  .rt-panel { display: flex; flex-direction: column; gap: 10px; }
  .rt-risk-header { display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px; border-radius: 10px; margin-bottom: 4px; }
  .rt-risk-header.rt-risk-critical { background: rgba(139,0,0,0.08); border: 1px solid rgba(139,0,0,0.2); color: #8b0000; }
  .rt-risk-header.rt-risk-high { background: rgba(220,38,38,0.08); border: 1px solid rgba(220,38,38,0.2); color: #dc2626; }
  .rt-risk-header.rt-risk-medium { background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.2); color: #d97706; }
  .rt-risk-header.rt-risk-low { background: rgba(16,185,129,0.08); border: 1px solid rgba(16,185,129,0.2); color: #059669; }
  .rt-risk-label { font-size: 12px; font-weight: 700; }
  .rt-risk-assessment { font-size: 11px; opacity: 0.85; margin-top: 4px; line-height: 1.5; }

  .rt-missing { display: flex; align-items: flex-start; gap: 8px; background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.2); border-radius: 8px; padding: 10px 12px; font-size: 11px; color: #d97706; }

  .rt-header-note { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text3); padding: 4px 0 8px; border-bottom: 1px solid var(--border); margin-bottom: 4px; }

  .rt-findings { display: flex; flex-direction: column; gap: 12px; }
  .rt-finding-card { padding: 14px; border-radius: 10px; border: 1px solid; transition: box-shadow 0.2s; }
  .rt-finding-card:hover { box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
  .rt-sev-critical { background: rgba(139,0,0,0.05); border-color: rgba(139,0,0,0.25); }
  .rt-sev-high { background: rgba(220,38,38,0.04); border-color: rgba(220,38,38,0.2); }
  .rt-sev-medium { background: rgba(245,158,11,0.04); border-color: rgba(245,158,11,0.2); }

  .rt-finding-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .rt-sev-badge { padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 800; letter-spacing: 0.03em; }
  .rt-badge-critical { background: rgba(139,0,0,0.15); color: #8b0000; }
  .rt-badge-high { background: rgba(220,38,38,0.12); color: #dc2626; }
  .rt-badge-medium { background: rgba(245,158,11,0.12); color: #d97706; }
  .rt-clause-ref { font-size: 12px; font-weight: 600; color: var(--text1); }

  .rt-problematic-text { margin: 0 0 10px; padding: 8px 12px; background: rgba(0,0,0,0.04); border-left: 3px solid rgba(220,38,38,0.4); border-radius: 0 6px 6px 0; font-size: 11px; color: var(--text2); font-style: italic; line-height: 1.5; word-break: break-word; }

  .rt-exploit-label { font-size: 10px; font-weight: 700; color: var(--text3); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
  .rt-exploit { font-size: 12px; color: var(--text2); margin: 0 0 10px; line-height: 1.5; }
  .rt-fix-label { font-size: 10px; font-weight: 700; color: #059669; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
  .rt-fix { font-size: 12px; color: #059669; margin: 0 0 10px; line-height: 1.5; }

  .rt-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
  .btn-jump-clause { display: flex; align-items: center; gap: 5px; padding: 6px 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 7px; color: var(--text2); font-size: 11px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
  .btn-jump-clause:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-light); }
  .btn-insert-fix { display: flex; align-items: center; gap: 5px; padding: 6px 12px; background: linear-gradient(135deg, #059669, #10b981); border: none; border-radius: 7px; color: #fff; font-size: 11px; font-weight: 700; cursor: pointer; transition: all 0.2s; }
  .btn-insert-fix:disabled { opacity: 0.6; cursor: not-allowed; }
  .btn-insert-fix:not(:disabled):hover { transform: translateY(-1px); box-shadow: 0 3px 10px rgba(16,185,129,0.35); }

  /* Info Modal Styles */
  .modal-backdrop { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.4); backdrop-filter: blur(4px); display: flex; justify-content: center; align-items: center; z-index: 9999; padding: 20px; box-sizing: border-box; }
  .info-modal-content { background: var(--white); border-radius: 16px; width: 100%; max-width: 560px; max-height: 90vh; overflow-y: auto; padding: 32px; position: relative; box-shadow: var(--shadow-lg); box-sizing: border-box; margin: 0 auto; }
  .btn-close-modal { position: absolute; top: 20px; right: 20px; background: none; border: none; color: var(--text3); cursor: pointer; padding: 4px; border-radius: 6px; transition: all 0.2s; }
  .btn-close-modal:hover { background: var(--bg2); color: var(--text1); }
  
  .info-modal-header { display: flex; align-items: center; gap: 16px; margin-bottom: 28px; }
  .im-icon { width: 48px; height: 48px; border-radius: 12px; background: linear-gradient(135deg, var(--accent), #7c5cfc); color: white; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
  .im-title { font-size: 20px; font-weight: 800; margin: 0 0 4px; color: var(--text1); }
  .im-subtitle { font-size: 13px; color: var(--text2); margin: 0; }
  
  .im-body { display: flex; flex-direction: column; gap: 24px; }
  .im-section h3 { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 700; color: var(--text1); margin: 0 0 10px; }
  .im-section p { font-size: 13px; color: var(--text2); line-height: 1.6; margin: 0; }
  
  .im-timeline { display: flex; flex-direction: column; gap: 16px; margin-top: 12px; }
  .im-step { display: flex; gap: 12px; }
  .im-step-dot { width: 24px; height: 24px; border-radius: 50%; background: var(--accent-light); color: var(--accent); font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
  .im-step-info { display: flex; flex-direction: column; gap: 2px; }
  .im-step-info strong { font-size: 13px; font-weight: 600; color: var(--text1); }
  .im-step-info span { font-size: 12px; color: var(--text2); }
  
  .im-footer { margin-top: 32px; display: flex; justify-content: flex-end; }
  .btn-modal-primary { background: var(--accent); color: white; border: none; border-radius: 10px; padding: 10px 20px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
  .btn-modal-primary:hover { background: var(--accent-mid); }
`;
