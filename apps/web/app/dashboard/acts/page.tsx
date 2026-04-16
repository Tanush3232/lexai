"use client";
import { useState, useRef, useCallback, Fragment } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { actsApi } from "@/lib/api";
import { toast } from "sonner";
import {
  Loader2, Plus, Search, BookOpen, RefreshCw, X, Upload,
  CheckCircle2, XCircle, AlertTriangle, Lock, Eye, Check, Trash2,
  ChevronLeft, ChevronRight,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

/* ── Types ──────────────────────────────────────────────────── */

interface LegalAct {
  id: string;
  title: string;
  act_number: string | null;
  enactment_date: string | null;
  ministry: string | null;
  ingestion_status: string;
  minio_path: string | null;
  has_text_layer: boolean | null;
  confidence_score: number | null;
  error_message: string | null;
  review_status: string | null;
  review_locked: boolean;
  created_at: string;
}

interface ListResponse {
  items: LegalAct[];
  total: number;
  page: number;
  limit: number;
}

interface Candidate {
  title: string;
  handle_id: string;
  act_number: string | null;
  enactment_date: string | null;
  confidence_score: number;
}

interface Stats {
  total: number;
  completed: number;
  not_completed: number;
  reviewed: number;
}

/* ── Helpers ────────────────────────────────────────────────── */

const COMPLETED_STATUSES = new Set(["completed"]);

function isCompleted(status: string) {
  return COMPLETED_STATUSES.has(status);
}

function friendlyStatus(status: string): string {
  return isCompleted(status) ? "Completed" : "Not Completed";
}

function friendlyReason(act: LegalAct): string | null {
  if (isCompleted(act.ingestion_status)) return null;
  const map: Record<string, string> = {
    pending: "Queued for processing",
    searching: "Searching IndiaCode…",
    downloading: "Downloading PDF…",
    ocr_processing: "Extracting text (OCR)…",
    failed: act.error_message || "Processing failed",
    not_found: "Act not found on IndiaCode",
    needs_review: `Fuzzy match (score: ${act.confidence_score ?? "?"}%) — needs manual review`,
    pdf_unavailable: "PDF not available — Act is under updation on IndiaCode",
  };
  return map[act.ingestion_status] || act.error_message || act.ingestion_status;
}

/* ── Status Badge ───────────────────────────────────────────── */

function StatusBadge({ status }: { status: string }) {
  const completed = isCompleted(status);
  return (
    <span className={`acts-badge ${completed ? "acts-badge-green" : "acts-badge-amber"}`}>
      {completed ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
      {friendlyStatus(status)}
    </span>
  );
}

/* ── Review Badge ───────────────────────────────────────────── */

function ReviewBadge({ act, onUpdate }: { act: LegalAct; onUpdate: (status: string | null, lock?: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const [pendingReviewed, setPendingReviewed] = useState(false);

  if (act.review_locked) {
    return (
      <span className="acts-badge acts-badge-locked">
        <Lock size={11} /> Reviewed
      </span>
    );
  }

  const current = act.review_status;
  const showReviewed = current === "manually_reviewed" || pendingReviewed;

  return (
    <div style={{ position: "relative" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <button
          className={`acts-review-btn ${showReviewed ? "reviewed" : current === "flagged" ? "flagged" : ""}`}
          onClick={() => setOpen(!open)}
        >
          {showReviewed ? "Reviewed" : current === "flagged" ? "Flagged" : "None"}
        </button>
        {showReviewed && (
          <button
            className="acts-lock-btn"
            title="Lock review permanently"
            onClick={(e) => {
              e.stopPropagation();
              if (!act.minio_path) {
                toast.error("Upload a PDF before locking the review");
                return;
              }
              onUpdate("manually_reviewed", true);
              setPendingReviewed(false);
            }}
          >
            ✔
          </button>
        )}
      </div>
      {open && (
        <>
          <div className="acts-dropdown-overlay" onClick={() => setOpen(false)} />
          <div className="acts-dropdown">
            <button
              onClick={() => { if (current) onUpdate(null); setPendingReviewed(false); setOpen(false); }}
              className={!showReviewed && !current ? "active" : ""}
            >
              None
            </button>
            <button
              onClick={() => { setPendingReviewed(true); setOpen(false); }}
              className={showReviewed ? "active" : ""}
            >
              Manually Reviewed
            </button>
            <button
              onClick={() => { if (current !== "flagged") onUpdate("flagged"); setPendingReviewed(false); setOpen(false); }}
              className={current === "flagged" ? "active" : ""}
            >
              Flagged
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/* ── Upload Button ──────────────────────────────────────────── */

function UploadPdfButton({ act, onSuccess }: { act: LegalAct; onSuccess: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  const handleFile = useCallback(async (file: File) => {
    setUploading(true);
    setProgress(0);
    try {
      await actsApi.uploadPdf(act.id, file, setProgress);
      toast.success(`PDF uploaded — ${act.title} is now completed`);
      setSelectedFile(null);
      onSuccess();
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [act.id, act.title, onSuccess]);

  const chooseFile = () => fileRef.current?.click();

  const finalizeUpload = () => {
    if (!selectedFile) return;
    void handleFile(selectedFile);
  };

  const clearSelected = () => {
    setSelectedFile(null);
    setProgress(0);
  };

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept=".pdf"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) setSelectedFile(f);
          e.target.value = "";
        }}
      />
      {!selectedFile ? (
        <button
          className="acts-upload-btn"
          onClick={chooseFile}
          disabled={uploading}
          title="Select PDF manually"
        >
          {uploading ? (
            <><Loader2 size={13} className="spin" /> {progress}%</>
          ) : (
            <><Upload size={13} /> Select PDF</>
          )}
        </button>
      ) : (
        <div className="acts-upload-pending">
          <button className="acts-upload-btn" onClick={chooseFile} disabled={uploading} title={selectedFile.name}>
            <Upload size={13} /> Change
          </button>
          <button
            className="acts-upload-confirm"
            onClick={finalizeUpload}
            disabled={uploading}
            title="Confirm and finalize upload"
          >
            {uploading ? <Loader2 size={13} className="spin" /> : <Check size={13} />}
          </button>
          <button
            className="acts-upload-cancel"
            onClick={clearSelected}
            disabled={uploading}
            title="Cancel selected file"
          >
            <X size={13} />
          </button>
        </div>
      )}
    </>
  );
}

/* ── Expanded Row ───────────────────────────────────────────── */

function ExpandedRow({ act }: { act: LegalAct }) {
  const reason = friendlyReason(act);
  if (!reason) return null;
  return (
    <tr>
      <td colSpan={6} style={{ padding: 0 }}>
        <div className="acts-expanded-row">
          <AlertTriangle size={14} />
          <span>{reason}</span>
        </div>
      </td>
    </tr>
  );
}

/* ── Main Page ──────────────────────────────────────────────── */

export default function ActsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [reviewFilter, setReviewFilter] = useState("");
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const limit = 10;

  // Debounced search
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const handleSearchChange = (val: string) => {
    setSearch(val);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setDebouncedSearch(val);
      setPage(1);
    }, 300);
  };

  // Modal state
  const [showAddModal, setShowAddModal] = useState(false);
  const [addInput, setAddInput] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [searchingAct, setSearchingAct] = useState(false);

  /* ── Queries ── */

  const { data, isLoading } = useQuery<ListResponse>({
    queryKey: ["acts", page, statusFilter, reviewFilter, debouncedSearch],
    queryFn: () =>
      actsApi
        .list({ page, limit, status: statusFilter || undefined, review: reviewFilter || undefined, search: debouncedSearch || undefined })
        .then((r) => r.data),
    refetchInterval: (query) => {
      const d = query.state.data as ListResponse | undefined;
      if (!d) return false;
      const hasActive = d.items.some((a) =>
        ["pending", "searching", "downloading", "ocr_processing"].includes(a.ingestion_status)
      );
      return hasActive ? 5000 : false;
    },
  });

  const { data: stats } = useQuery<Stats>({
    queryKey: ["acts-stats"],
    queryFn: () => actsApi.stats().then((r) => r.data),
    refetchInterval: 30000,
  });

  const acts = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / limit));

  /* ── Mutations ── */


  const searchMutation = useMutation({
    mutationFn: (name: string) => actsApi.add(name),
    onSuccess: (res) => {
      setCandidates(res.data);
      setSearchingAct(false);
      if (res.data.length === 0) toast.info("No candidates found");
    },
    onError: (e: any) => {
      setSearchingAct(false);
      toast.error(e.response?.data?.detail || "Search failed");
    },
  });

  const confirmMutation = useMutation({
    mutationFn: (c: Candidate) =>
      actsApi.confirm({
        handle_id: c.handle_id,
        title: c.title,
        act_number: c.act_number || undefined,
        enactment_date: c.enactment_date || undefined,
      }),
    onSuccess: (res) => {
      if (res.data?.already_exists) {
        toast.info(`Act already exists (${res.data.status || "unknown"})`);
      } else {
        toast.success("Ingestion started");
      }
      closeAddModal();
      qc.invalidateQueries({ queryKey: ["acts"] });
      qc.invalidateQueries({ queryKey: ["acts-stats"] });
      if (res.data?.id) router.push(`/dashboard/acts/${res.data.id}`);
    },
    onError: (e: any) => toast.error(e.response?.data?.detail || "Confirm failed"),
  });

  const reviewMutation = useMutation({
    mutationFn: ({ actId, review_status, lock }: { actId: string; review_status: string | null; lock?: boolean }) =>
      actsApi.updateReview(actId, { review_status: review_status ?? "none", lock }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["acts"] });
      qc.invalidateQueries({ queryKey: ["acts-stats"] });
    },
    onError: (e: any) => toast.error(e.response?.data?.detail || "Review update failed"),
  });

  const removePdfMutation = useMutation({
    mutationFn: (actId: string) => actsApi.removePdf(actId),
    onSuccess: () => {
      toast.success("PDF removed");
      qc.invalidateQueries({ queryKey: ["acts"] });
      qc.invalidateQueries({ queryKey: ["acts-stats"] });
    },
    onError: (e: any) => toast.error(e.response?.data?.detail || "PDF removal failed"),
  });

  /* ── Handlers ── */

  const handleAddSearch = () => {
    if (!addInput.trim()) return;
    setSearchingAct(true);
    setCandidates([]);
    searchMutation.mutate(addInput.trim());
  };

  const closeAddModal = () => {
    setShowAddModal(false);
    setCandidates([]);
    setAddInput("");
  };

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["acts"] });
    qc.invalidateQueries({ queryKey: ["acts-stats"] });
  };

  /* ── Pagination helpers ── */

  const pageNumbers = (): number[] => {
    const pages: number[] = [];
    const start = Math.max(1, page - 2);
    const end = Math.min(totalPages, page + 2);
    for (let i = start; i <= end; i++) pages.push(i);
    return pages;
  };

  /* ── Render ── */

  return (
    <div className="fade-in">
      {/* Header */}
      <div className="acts-header">
        <div>
          <h1 className="acts-title">Legal Acts</h1>
          <p className="acts-subtitle">India Code act database — search, ingest, and review legal acts</p>
        </div>
        <div className="acts-header-actions">
          <button className="btn-accent" onClick={() => setShowAddModal(true)}>
            <Plus size={14} /> Add Act
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="acts-stats-row">
          <div className="acts-stat-card" onClick={() => { setStatusFilter(""); setReviewFilter(""); setPage(1); }}>
            <span className="acts-stat-val">{stats.total}</span>
            <span className="acts-stat-label">Total Acts</span>
          </div>
          <div className="acts-stat-card acts-stat-green" onClick={() => { setStatusFilter("completed"); setReviewFilter(""); setPage(1); }}>
            <span className="acts-stat-val">{stats.completed}</span>
            <span className="acts-stat-label">Completed</span>
          </div>
          <div className="acts-stat-card acts-stat-amber" onClick={() => { setStatusFilter("not_completed"); setReviewFilter(""); setPage(1); }}>
            <span className="acts-stat-val">{stats.not_completed}</span>
            <span className="acts-stat-label">Not Completed</span>
          </div>
          <div className={`acts-stat-card acts-stat-purple ${reviewFilter === "reviewed" ? "active" : ""}`} onClick={() => { setReviewFilter(reviewFilter === "reviewed" ? "" : "reviewed"); setStatusFilter(""); setPage(1); }}>
            <span className="acts-stat-val">{stats.reviewed}</span>
            <span className="acts-stat-label">Reviewed</span>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="acts-filters">
        <div className="acts-search-wrapper">
          <Search size={15} className="acts-search-icon" />
          <input
            type="text"
            placeholder="Search by act name…"
            value={search}
            onChange={(e) => handleSearchChange(e.target.value)}
            className="acts-search-input"
          />
          {search && (
            <button className="acts-search-clear" onClick={() => { setSearch(""); setDebouncedSearch(""); setPage(1); }}>
              <X size={14} />
            </button>
          )}
        </div>
        <div className="acts-status-filter">
          {["", "completed", "not_completed"].map((val) => (
            <button
              key={val}
              className={`acts-filter-tab ${statusFilter === val ? "active" : ""}`}
              onClick={() => { setStatusFilter(val); setPage(1); }}
            >
              {val === "" ? "All" : val === "completed" ? "Completed" : "Not Completed"}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="acts-table-wrapper">
        {isLoading ? (
          <div className="acts-empty"><Loader2 size={24} className="spin" /></div>
        ) : acts.length === 0 ? (
          <div className="acts-empty">
            <BookOpen size={32} style={{ opacity: 0.3, marginBottom: 12 }} />
            <p>No acts found. Add an act or refine your search.</p>
          </div>
        ) : (
          <table className="acts-table">
            <thead>
              <tr>
                <th style={{ width: "40%" }}>Title</th>
                <th style={{ width: "10%" }}>Act No.</th>
                <th style={{ width: "8%" }}>Year</th>
                <th style={{ width: "12%" }}>Status</th>
                <th style={{ width: "14%" }}>Review</th>
                <th style={{ width: "16%", textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {acts.map((act) => (
                <Fragment key={act.id}>
                  <tr
                    className={`acts-row ${expandedId === act.id ? "expanded" : ""}`}
                    onClick={() => !isCompleted(act.ingestion_status) && setExpandedId(expandedId === act.id ? null : act.id)}
                  >
                    <td className="acts-title-cell">
                      <Link href={`/dashboard/acts/${act.id}`} onClick={(e) => e.stopPropagation()}>
                        {act.title}
                      </Link>
                    </td>
                    <td className="acts-meta-cell">{act.act_number || "—"}</td>
                    <td className="acts-meta-cell">{act.enactment_date || "—"}</td>
                    <td><StatusBadge status={act.ingestion_status} /></td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <ReviewBadge
                        act={act}
                        onUpdate={(status, lock) =>
                          reviewMutation.mutate({ actId: act.id, review_status: status, lock })
                        }
                      />
                    </td>
                    <td className="acts-actions-cell" onClick={(e) => e.stopPropagation()}>
                      {!isCompleted(act.ingestion_status) && (
                        <UploadPdfButton act={act} onSuccess={invalidateAll} />
                      )}
                      {isCompleted(act.ingestion_status) && !act.review_locked && act.minio_path && (
                        <button
                          className="acts-remove-btn"
                          onClick={() => removePdfMutation.mutate(act.id)}
                          disabled={removePdfMutation.isPending}
                          title="Remove manually uploaded PDF"
                        >
                          <Trash2 size={13} /> Remove PDF
                        </button>
                      )}
                      <Link href={`/dashboard/acts/${act.id}`} className="acts-view-btn">
                        <Eye size={13} /> View
                      </Link>
                    </td>
                  </tr>
                  {expandedId === act.id && !isCompleted(act.ingestion_status) && (
                    <ExpandedRow key={`${act.id}-exp`} act={act} />
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {total > 0 && (
        <div className="acts-pagination">
          <div className="acts-pagination-info">
            Showing {Math.min((page - 1) * limit + 1, total)}–{Math.min(page * limit, total)} of {total} acts
          </div>
          <div className="acts-pagination-controls">
            <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="acts-page-btn">
              <ChevronLeft size={16} />
            </button>
            {pageNumbers().map((p) => (
              <button
                key={p}
                className={`acts-page-btn ${p === page ? "active" : ""}`}
                onClick={() => setPage(p)}
              >
                {p}
              </button>
            ))}
            <button disabled={page >= totalPages} onClick={() => setPage(page + 1)} className="acts-page-btn">
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}

      {/* Add Act Modal */}
      {showAddModal && (
        <div className="acts-modal-overlay" onClick={closeAddModal}>
          <div className="acts-modal" onClick={(e) => e.stopPropagation()}>
            <div className="acts-modal-header">
              <h2>Add New Act</h2>
              <button className="acts-modal-close" onClick={closeAddModal}><X size={18} /></button>
            </div>
            <div className="acts-modal-body">
              <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
                <input
                  type="text"
                  placeholder="Enter official act name, e.g. Arms Act, 1959"
                  value={addInput}
                  onChange={(e) => setAddInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleAddSearch()}
                  className="acts-search-input"
                  style={{ flex: 1 }}
                />
                <button className="btn-accent" onClick={handleAddSearch} disabled={searchingAct}>
                  {searchingAct ? <Loader2 size={14} className="spin" /> : <Search size={14} />}
                  Search
                </button>
              </div>
              {candidates.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <p style={{ fontSize: 12, color: "var(--text3)", margin: 0 }}>
                    Select the best match to begin ingestion.
                  </p>
                  {candidates.map((c, i) => (
                    <div key={i} className="acts-candidate">
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 14 }}>{c.title}</div>
                        <div style={{ fontSize: 12, color: "var(--text3)", marginTop: 4 }}>
                          Act No: {c.act_number || "—"} | Year: {c.enactment_date || "—"} | Score: {c.confidence_score}%
                        </div>
                      </div>
                      <button
                        className="btn-accent"
                        style={{ fontSize: 12, padding: "6px 14px", whiteSpace: "nowrap" }}
                        onClick={() => confirmMutation.mutate(c)}
                        disabled={confirmMutation.isPending}
                      >
                        {confirmMutation.isPending ? <Loader2 size={12} className="spin" /> : "Confirm"}
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {searchingAct && (
                <div style={{ textAlign: "center", padding: 30, color: "var(--text3)" }}>
                  <Loader2 size={20} className="spin" />
                  <p style={{ marginTop: 10, fontSize: 13 }}>Searching IndiaCode…</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
