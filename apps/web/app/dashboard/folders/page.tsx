"use client";
import { useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { foldersApi, documentsApi } from "@/lib/api";
import { toast } from "sonner";
import { Loader2, Plus, ArrowLeft, Trash2, Upload, FileText, X, Eye } from "lucide-react";
import { useDropzone } from "react-dropzone";
import DocumentViewerModal, { type DocMeta } from "./DocumentViewerModal";

interface Folder { id: string; name: string; description?: string; document_count: number; created_at: string; }
interface Document { id: string; name: string; status: string; page_count: number; language?: string; created_at: string; size_bytes: number; content_type: string; }

export default function FoldersPage() {
  const qc = useQueryClient();
  const [selectedFolder, setSelectedFolder] = useState<Folder | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [viewingDoc, setViewingDoc] = useState<DocMeta | null>(null);

  const { data: folders = [], isLoading: foldersLoading } = useQuery<Folder[]>({
    queryKey: ["folders"],
    queryFn: () => foldersApi.list().then(r => r.data),
  });

  const { data: documents = [], isLoading: docsLoading } = useQuery<Document[]>({
    queryKey: ["documents", selectedFolder?.id],
    queryFn: () => documentsApi.listByFolder(selectedFolder!.id).then(r => r.data),
    enabled: !!selectedFolder,
  });

  const createFolder = useMutation({
    mutationFn: (name: string) => foldersApi.create({ name }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["folders"] }); setShowCreateModal(false); setNewFolderName(""); toast.success("Folder created"); },
    onError: () => toast.error("Failed to create folder"),
  });

  const deleteFolder = useMutation({
    mutationFn: (id: string) => foldersApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["folders"] }); setSelectedFolder(null); toast.success("Folder deleted"); },
    onError: () => toast.error("Failed to delete folder"),
  });

  const deleteDoc = useMutation({
    mutationFn: (id: string) => documentsApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["documents", selectedFolder?.id] }); qc.invalidateQueries({ queryKey: ["folders"] }); toast.success("Document deleted"); },
  });

  const uploadDoc = async (file: File) => {
    if (!selectedFolder) return;
    try {
      setUploadProgress(0);
      await documentsApi.upload(selectedFolder.id, file, setUploadProgress);
      qc.invalidateQueries({ queryKey: ["documents", selectedFolder.id] });
      qc.invalidateQueries({ queryKey: ["folders"] });
      toast.success(`"${file.name}" uploaded. Indexing in background...`);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Upload failed");
    } finally {
      setUploadProgress(null);
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: (files) => files.forEach(uploadDoc),
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "text/plain": [".txt"],
    },
    disabled: !selectedFolder,
  });

  return (
    <div className="fade-in">
      {!selectedFolder ? (
        <>
          <div className="toolbar">
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <div className="page-header" style={{ marginBottom: 0 }}>
                <h1>Folders</h1>
              </div>
              <span className="count-badge">{folders.length} folders</span>
            </div>
            <button className="btn-accent" onClick={() => setShowCreateModal(true)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ width: "14px", height: "14px" }}>
                <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
              </svg>
              New Folder
            </button>
          </div>

          <div className="folders-grid">
            {foldersLoading ? (
               <div style={{ padding: "40px", textAlign: "center" }}>
                 <Loader2 className="animate-spin mx-auto" />
               </div>
            ) : folders.map((folder) => (
              <div key={folder.id} className="folder-item" onClick={() => setSelectedFolder(folder)}>
                <div className="fi-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                </div>
                <div className="fi-info">
                  <strong>{folder.name}</strong>
                  <span>{folder.description || "Collection of legal documents"}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                  <span className="fi-docs">{folder.document_count} documents</span>
                  <div style={{ color: "var(--text3)" }}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: "14px", height: "14px" }}><polyline points="9 18 15 12 9 6"/></svg>
                  </div>
                  <div className="fi-delete" onClick={(e) => { e.stopPropagation(); deleteFolder.mutate(folder.id); }}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: "13px", height: "13px" }}><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <>
          <div className="back-btn" onClick={() => setSelectedFolder(null)}>
            <ArrowLeft className="w-4 h-4" /> Back to Folders
          </div>
          <div className="page-header">
            <h1>{selectedFolder.name}</h1>
            <p>{selectedFolder.description || "Manage documents in this folder"}</p>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
             <div {...getRootProps()} className="card" style={{ padding: "32px", textAlign: "center", border: "2px dashed var(--border)", background: isDragActive ? "var(--accent-light)" : "var(--white)", cursor: "pointer" }}>
                <input {...getInputProps()} />
                <Upload className="mx-auto mb-2 text-accent" />
                <p style={{ fontSize: "14px", fontWeight: 500 }}>{isDragActive ? "Drop files here" : "Click or drag files to upload"}</p>
                <p style={{ fontSize: "12px", color: "var(--text3)", marginTop: "4px" }}>PDF, DOCX, TXT up to 50MB</p>
                {uploadProgress !== null && (
                   <div style={{ marginTop: "16px", background: "var(--bg2)", height: "4px", borderRadius: "2px", overflow: "hidden" }}>
                     <div style={{ background: "var(--accent)", height: "100%", width: `${uploadProgress}%`, transition: "width 0.2s" }} />
                   </div>
                )}
             </div>

             <div className="card" style={{ padding: "0", overflow: "hidden" }}>
                {docsLoading ? (
                  <div style={{ padding: "20px", textAlign: "center" }}><Loader2 className="animate-spin mx-auto" /></div>
                ) : documents.length === 0 ? (
                  <div className="empty-state">
                    <FileText className="mx-auto mb-2 opacity-40" />
                    <p>No documents in this folder</p>
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    {documents.map(doc => (
                      <div
                        key={doc.id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          padding: "12px 18px",
                          borderBottom: "1px solid var(--border)",
                          cursor: "pointer",
                          transition: "background 0.1s",
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg)")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                        onClick={() => setViewingDoc({ id: doc.id, name: doc.name, status: doc.status, page_count: doc.page_count })}
                      >
                        <FileText className="mr-3" style={{ width: "16px", color: "var(--accent)", flexShrink: 0 }} />
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: "14px", fontWeight: 600 }}>{doc.name}</div>
                          <div style={{ fontSize: "11px", color: "var(--text3)" }}>
                            <span style={{
                              display: "inline-block",
                              padding: "1px 6px",
                              borderRadius: "4px",
                              background: doc.status === "indexed" ? "var(--green-light)" : doc.status === "error" ? "var(--red-light)" : "var(--amber-light)",
                              color: doc.status === "indexed" ? "var(--green)" : doc.status === "error" ? "var(--red)" : "var(--amber)",
                              fontWeight: 600,
                              fontSize: "10px",
                              marginRight: "6px",
                            }}>
                              {doc.status}
                            </span>
                            {(doc.size_bytes / 1024).toFixed(1)} KB
                            {doc.page_count > 0 && ` · ${doc.page_count} pages`}
                          </div>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setViewingDoc({ id: doc.id, name: doc.name, status: doc.status, page_count: doc.page_count });
                          }}
                          style={{
                            padding: "5px 10px",
                            background: "var(--accent-light)",
                            color: "var(--accent)",
                            border: "1px solid rgba(91,79,207,0.2)",
                            borderRadius: "6px",
                            cursor: "pointer",
                            fontSize: "11px",
                            fontWeight: 600,
                            display: "flex",
                            alignItems: "center",
                            gap: "4px",
                            marginRight: "8px",
                            flexShrink: 0,
                          }}
                        >
                          <Eye size={12} />
                          View
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); deleteDoc.mutate(doc.id); }}
                          style={{ padding: "6px", color: "var(--red)", background: "transparent", border: "none", cursor: "pointer", flexShrink: 0 }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
             </div>
          </div>
        </>
      )}

      {showCreateModal && typeof document !== "undefined" && createPortal(
        <div 
          onClick={() => setShowCreateModal(false)}
          style={{ 
            position: "fixed", inset: 0, zIndex: 9999,
            background: "rgba(255, 255, 255, 0.4)", backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: "20px"
          }}
        >
          <div 
            className="login-card" 
            onClick={e => e.stopPropagation()}
            style={{ 
              maxWidth: "420px", width: "100%", 
              boxShadow: "0 32px 64px -12px rgba(0, 0, 0, 0.14)",
              border: "1px solid rgba(255,255,255,0.7)"
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "28px" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                <h2 style={{ margin: 0, fontSize: "18px", fontWeight: 700 }}>New Folder</h2>
                <p style={{ margin: 0, fontSize: "12px", color: "var(--text3)" }}>Organize your legal documents</p>
              </div>
              <button 
                onClick={() => setShowCreateModal(false)} 
                style={{ background: "var(--bg2)", border: "none", cursor: "pointer", width: "32px", height: "32px", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text2)" }}
              >
                <X size={18}/>
              </button>
            </div>
            
            <div className="form-group" style={{ marginBottom: "24px" }}>
              <label style={{ fontSize: "12px", fontWeight: 600, color: "var(--text2)", marginBottom: "8px", display: "block" }}>FOLDER NAME</label>
              <input 
                value={newFolderName} 
                onChange={e => setNewFolderName(e.target.value)} 
                placeholder="e.g. Vendor Contracts" 
                autoFocus 
                style={{ height: "48px", borderRadius: "10px", fontSize: "15px" }}
              />
            </div>

            <div style={{ display: "flex", gap: "12px" }}>
              <button 
                onClick={() => setShowCreateModal(false)}
                style={{ flex: 1, height: "44px", borderRadius: "10px", border: "1px solid var(--border)", background: "white", color: "var(--text2)", fontWeight: 600, cursor: "pointer" }}
              >
                Cancel
              </button>
              <button 
                className="btn-primary" 
                onClick={() => createFolder.mutate(newFolderName)} 
                disabled={!newFolderName || createFolder.isPending}
                style={{ flex: 2, margin: 0, height: "44px", borderRadius: "10px" }}
              >
                {createFolder.isPending ? <Loader2 className="animate-spin" size={16} /> : "Create Folder"}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Document Viewer Modal */}
      {viewingDoc && (
        <DocumentViewerModal
          doc={viewingDoc}
          onClose={() => setViewingDoc(null)}
        />
      )}
    </div>
  );
}
