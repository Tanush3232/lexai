"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, actsApi } from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowLeft, FileText, ExternalLink, Loader2, ChevronDown, ChevronUp,
  CheckCircle2, XCircle, Upload, Lock, AlertTriangle, Check, Trash2, X,
} from "lucide-react";
import Link from "next/link";

function StatusBadge({ status }: { status: string }) {
  const completed = status === "completed";
  return (
    <span className={`acts-badge ${completed ? "acts-badge-green" : "acts-badge-amber"}`}>
      {completed ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
      {completed ? "Completed" : "Not Completed"}
    </span>
  );
}

export default function ActDetailPage() {
  const params = useParams();
  const router = useRouter();
  const qc = useQueryClient();
  const actId = params.id as string;
  const [showFullText, setShowFullText] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [pendingReview, setPendingReview] = useState<string | null | undefined>(undefined);

  const { data: act, isLoading } = useQuery({
    queryKey: ["act", actId],
    queryFn: () => actsApi.get(actId).then((r) => r.data),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      return ["pending", "searching", "downloading", "ocr_processing"].includes(data.ingestion_status)
        ? 5000
        : false;
    },
  });

  useEffect(() => {
    if (act?.minio_path && act.ingestion_status === "completed") {
      api.get(`/acts/${actId}/pdf`, { responseType: "blob" })
        .then((r) => {
          const blob = new Blob([r.data], { type: "application/pdf" });
          setPdfUrl(URL.createObjectURL(blob));
        })
        .catch((e) => {
          console.error("PDF fetch failed:", e?.response?.status, e?.response?.data);
          toast.error(`PDF load failed: ${e?.response?.status ?? "network error"}`);
        });
    }
    return () => {
      if (pdfUrl?.startsWith("blob:")) URL.revokeObjectURL(pdfUrl);
    };
  }, [act?.minio_path, act?.ingestion_status, actId]);



  const reviewMutation = useMutation({
    mutationFn: (data: { review_status: string | null; lock?: boolean }) =>
      actsApi.updateReview(actId, { review_status: data.review_status ?? "none", lock: data.lock }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["act", actId] });
      qc.invalidateQueries({ queryKey: ["acts"] });
      qc.invalidateQueries({ queryKey: ["acts-stats"] });
    },
    onError: (e: any) => toast.error(e.response?.data?.detail || "Review update failed"),
  });

  const handleUpload = useCallback(async (file: File) => {
    setUploading(true);
    setUploadProgress(0);
    try {
      await actsApi.uploadPdf(actId, file, setUploadProgress);
      toast.success("PDF uploaded — act is now completed");
      setSelectedFile(null);
      qc.invalidateQueries({ queryKey: ["act", actId] });
      qc.invalidateQueries({ queryKey: ["acts"] });
      qc.invalidateQueries({ queryKey: ["acts-stats"] });
    } catch (e: any) {
      toast.error(e.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [actId, qc]);

  const removePdfMutation = useMutation({
    mutationFn: () => actsApi.removePdf(actId),
    onSuccess: () => {
      toast.success("PDF removed");
      setPdfUrl(null);
      qc.invalidateQueries({ queryKey: ["act", actId] });
      qc.invalidateQueries({ queryKey: ["acts"] });
      qc.invalidateQueries({ queryKey: ["acts-stats"] });
    },
    onError: (e: any) => toast.error(e.response?.data?.detail || "PDF removal failed"),
  });

  const chooseFile = () => fileRef.current?.click();
  const finalizeUpload = () => {
    if (!selectedFile) return;
    void handleUpload(selectedFile);
  };

  if (isLoading) {
    return <div style={{ padding: 60, textAlign: "center" }}><Loader2 size={24} className="spin" /></div>;
  }

  if (!act) {
    return (
      <div className="fade-in">
        <button onClick={() => router.back()} className="back-btn"><ArrowLeft size={16} /> Back</button>
        <p style={{ color: "var(--text3)", textAlign: "center", marginTop: 40 }}>Act not found</p>
      </div>
    );
  }

  const textPreview = act.pdf_text
    ? showFullText ? act.pdf_text : act.pdf_text.slice(0, 1000)
    : null;

  return (
    <div className="fade-in">
      <button onClick={() => router.push("/dashboard/acts")} className="back-btn" style={{ marginBottom: 20 }}>
        <ArrowLeft size={16} /> Back to Acts
      </button>

      <div className="acts-detail-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h1 className="acts-title" style={{ marginBottom: 0 }}>{act.title}</h1>
          <StatusBadge status={act.ingestion_status} />
          {act.review_locked ? (
            <span className="acts-badge acts-badge-locked"><Lock size={11} /> Reviewed</span>
          ) : act.review_status === "manually_reviewed" ? (
            <span className="acts-badge acts-badge-green" style={{ cursor: "default" }}>Reviewed</span>
          ) : act.review_status === "flagged" ? (
            <span className="acts-badge acts-badge-amber" style={{ cursor: "default" }}>Flagged</span>
          ) : null}
        </div>

        {/* Review controls */}
        <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
          {!act.review_locked && (
            <>
              <select
                value={pendingReview !== undefined ? (pendingReview ?? "none") : (act.review_status || "none")}
                onChange={(e) => {
                  const val = e.target.value === "none" ? null : e.target.value;
                  setPendingReview(val);
                }}
                className="acts-review-select"
              >
                <option value="none">No Review</option>
                <option value="manually_reviewed">Manually Reviewed</option>
                <option value="flagged">Flagged</option>
              </select>
              {pendingReview !== undefined ? (
                <>
                  <button
                    className="acts-upload-confirm"
                    onClick={() => {
                      reviewMutation.mutate({ review_status: pendingReview });
                      setPendingReview(undefined);
                    }}
                    disabled={reviewMutation.isPending}
                    title="Confirm review status"
                  >
                    {reviewMutation.isPending ? <Loader2 size={13} className="spin" /> : <Check size={13} />}
                  </button>
                  <button
                    className="acts-upload-cancel"
                    onClick={() => setPendingReview(undefined)}
                    disabled={reviewMutation.isPending}
                    title="Cancel"
                  >
                    <X size={13} />
                  </button>
                </>
              ) : (
                act.review_status === "manually_reviewed" && (
                  <button
                    className="acts-lock-btn-detail"
                    onClick={() => {
                      if (!act.minio_path) {
                        toast.error("Upload a PDF before locking the review");
                        return;
                      }
                      reviewMutation.mutate({ review_status: "manually_reviewed", lock: true });
                    }}
                  >
                    <Lock size={13} /> Lock Permanently
                  </button>
                )
              )}
            </>
          )}
          {act.ingestion_status !== "completed" && (
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
                <button className="acts-upload-btn" onClick={chooseFile} disabled={uploading}>
                  {uploading ? (
                    <><Loader2 size={13} className="spin" /> {uploadProgress}%</>
                  ) : (
                    <><Upload size={13} /> Select PDF</>
                  )}
                </button>
              ) : (
                <div className="acts-upload-pending">
                  <button className="acts-upload-btn" onClick={chooseFile} disabled={uploading} title={selectedFile.name}>
                    <Upload size={13} /> Change
                  </button>
                  <button className="acts-upload-confirm" onClick={finalizeUpload} disabled={uploading} title="Confirm and finalize upload">
                    {uploading ? <Loader2 size={13} className="spin" /> : <Check size={13} />}
                  </button>
                  <button className="acts-upload-cancel" onClick={() => setSelectedFile(null)} disabled={uploading} title="Cancel selected file">
                    <X size={13} />
                  </button>
                </div>
              )}
            </>
          )}
          {act.ingestion_status === "completed" && !act.review_locked && act.minio_path && (
            <button
              className="acts-remove-btn"
              onClick={() => removePdfMutation.mutate()}
              disabled={removePdfMutation.isPending}
            >
              <Trash2 size={13} /> Remove PDF
            </button>
          )}
        </div>
      </div>

      <div className="two-col" style={{ gridTemplateColumns: "320px 1fr", marginTop: 20, alignItems: "start" }}>
        {/* Metadata */}
        <div className="card" style={{ padding: 24 }}>
          <div className="section-title">Metadata</div>
          <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: "12px 16px", fontSize: 13, marginTop: 16 }}>
            <span style={{ color: "var(--text3)", fontWeight: 600 }}>Act Number</span>
            <span>{act.act_number || "—"}</span>
            <span style={{ color: "var(--text3)", fontWeight: 600 }}>Enactment Date</span>
            <span>{act.enactment_date || "—"}</span>
            <span style={{ color: "var(--text3)", fontWeight: 600 }}>Ministry</span>
            <span>{act.ministry || "—"}</span>
            <span style={{ color: "var(--text3)", fontWeight: 600 }}>Total Pages</span>
            <span>{act.total_pages ?? "—"}</span>
            <span style={{ color: "var(--text3)", fontWeight: 600 }}>Text Layer</span>
            <span>{act.has_text_layer === null ? "—" : act.has_text_layer ? "Yes" : "No (OCR)"}</span>
            <span style={{ color: "var(--text3)", fontWeight: 600 }}>Confidence</span>
            <span>{act.confidence_score ? `${act.confidence_score}%` : "—"}</span>
            <span style={{ color: "var(--text3)", fontWeight: 600 }}>Handle ID</span>
            <span>{act.handle_id || "—"}</span>
            {act.indiacode_url && (
              <>
                <span style={{ color: "var(--text3)", fontWeight: 600 }}>IndiaCode</span>
                <a href={act.indiacode_url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", display: "flex", alignItems: "center", gap: 4 }}>
                  View on IndiaCode <ExternalLink size={12} />
                </a>
              </>
            )}
          </div>

          {act.error_message && (
            <div className="acts-error-box">
              <AlertTriangle size={14} />
              <span>{act.error_message}</span>
            </div>
          )}
        </div>

        {/* PDF Viewer */}
        <div className="card" style={{ padding: 0, minHeight: 700, display: "flex", flexDirection: "column", position: "sticky", top: 20 }}>
          <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--border)", background: "var(--bg)", fontSize: 12, fontWeight: 600, color: "var(--text2)" }}>
            PDF Document
          </div>
          {pdfUrl ? (
            <iframe src={pdfUrl} style={{ flex: 1, border: "none", minHeight: 660 }} title="Act PDF" />
          ) : (
            <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", fontSize: 13 }}>
              <div style={{ textAlign: "center" }}>
                <FileText size={32} style={{ opacity: 0.3, marginBottom: 12 }} />
                <p>{act.ingestion_status === "completed" ? "PDF loading..." : "PDF not yet available"}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Extracted Text */}
      {textPreview && (
        <div className="card" style={{ padding: 24, marginTop: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div className="section-title">Extracted Text</div>
            {act.pdf_text && act.pdf_text.length > 1000 && (
              <button className="btn-secondary" style={{ fontSize: 12, padding: "4px 10px" }} onClick={() => setShowFullText(!showFullText)}>
                {showFullText ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                {showFullText ? "Collapse" : `Show All (${Math.round(act.pdf_text.length / 1000)}k chars)`}
              </button>
            )}
          </div>
          <pre style={{
            marginTop: 16, fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap",
            wordBreak: "break-word", maxHeight: showFullText ? "none" : 400, overflow: "auto",
            padding: 16, background: "var(--bg)", borderRadius: 8, border: "1px solid var(--border)",
          }}>
            {textPreview}
          </pre>
        </div>
      )}
    </div>
  );
}
