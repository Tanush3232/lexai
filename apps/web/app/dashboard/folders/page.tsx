"use client";
import { useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { foldersApi, documentsApi, Folder } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth-store";
import { toast } from "sonner";
import { 
  Loader2, Plus, ArrowLeft, Trash2, Upload, FileText, X, Eye, 
  Globe, Lock, ChevronRight, Folder as FolderIcon, FolderPlus 
} from "lucide-react";
import { useDropzone } from "react-dropzone";
import DocumentViewerModal, { type DocMeta } from "./DocumentViewerModal";

interface Document { 
  id: string; 
  name: string; 
  status: string; 
  page_count: number; 
  language?: string; 
  created_at: string; 
  size_bytes: number; 
  content_type: string;
  owner_id?: string;
  is_global?: boolean;
}

export default function FoldersPage() {
  const qc = useQueryClient();
  const user = useAuthStore(s => s.user);

  // Tab State: "personal" vs "global"
  const [vaultTab, setVaultTab] = useState<"personal" | "global">("personal");

  // Navigation hierarchy: breadcrumbs stack of selected folders
  const [folderPath, setFolderPath] = useState<Folder[]>([]);
  const selectedFolder = folderPath.length > 0 ? folderPath[folderPath.length - 1] : null;
  const currentDepth = folderPath.length; // 0 = root, 1 = root folder, max 5

  // Modal State
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [newFolderDesc, setNewFolderDesc] = useState("");
  const [newFolderIsGlobal, setNewFolderIsGlobal] = useState(false);

  // Upload & Viewer State
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
    mutationFn: (data: { name: string; description?: string; is_global: boolean; parent_id?: string | null }) => 
      foldersApi.create(data),
    onSuccess: () => { 
      qc.invalidateQueries({ queryKey: ["folders"] }); 
      setShowCreateModal(false); 
      setNewFolderName(""); 
      setNewFolderDesc("");
      toast.success(selectedFolder ? "Subfolder created" : "Folder created"); 
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || "Failed to create folder"),
  });

  const deleteFolder = useMutation({
    mutationFn: (id: string) => foldersApi.delete(id),
    onSuccess: () => { 
      qc.invalidateQueries({ queryKey: ["folders"] }); 
      if (selectedFolder) {
        setFolderPath(prev => prev.filter(f => f.id !== selectedFolder.id));
      }
      toast.success("Folder deleted"); 
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || "Failed to delete folder"),
  });

  const deleteDoc = useMutation({
    mutationFn: (id: string) => documentsApi.delete(id),
    onSuccess: () => { 
      qc.invalidateQueries({ queryKey: ["documents", selectedFolder?.id] }); 
      qc.invalidateQueries({ queryKey: ["folders"] }); 
      toast.success("Document deleted"); 
    },
    onError: (err: any) => toast.error(err?.response?.data?.detail || "Failed to delete document"),
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

  // Root level folders for current tab
  const personalRootFolders = folders.filter(f => !f.is_global && !f.parent_id);
  const globalRootFolders = folders.filter(f => f.is_global && !f.parent_id);
  const displayedRootFolders = vaultTab === "personal" ? personalRootFolders : globalRootFolders;

  // Subfolders of the currently active folder
  const currentSubfolders = selectedFolder 
    ? folders.filter(f => f.parent_id === selectedFolder.id) 
    : [];

  const handleOpenCreateModal = () => {
    if (selectedFolder) {
      if (currentDepth >= 5) {
        toast.error("Maximum folder nesting depth of 5 levels reached");
        return;
      }
      // Inherit parent folder's global status
      setNewFolderIsGlobal(selectedFolder.is_global);
    } else {
      // Default to active tab
      setNewFolderIsGlobal(vaultTab === "global");
    }
    setNewFolderName("");
    setNewFolderDesc("");
    setShowCreateModal(true);
  };

  const handleCreateSubmit = () => {
    if (!newFolderName.trim()) return;
    createFolder.mutate({
      name: newFolderName.trim(),
      description: newFolderDesc.trim() || undefined,
      is_global: selectedFolder ? selectedFolder.is_global : newFolderIsGlobal,
      parent_id: selectedFolder ? selectedFolder.id : null,
    });
  };

  return (
    <div className="fade-in">
      {!selectedFolder ? (
        <>
          {/* Main Toolbar */}
          <div className="toolbar">
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <div className="page-header" style={{ marginBottom: 0 }}>
                <h1>Legal Vault</h1>
              </div>
              <span className="count-badge">
                {displayedRootFolders.length} {vaultTab === "personal" ? "personal" : "global"} folders
              </span>
            </div>
            <button className="btn-accent" onClick={handleOpenCreateModal}>
              <Plus size={15} strokeWidth={2.5} />
              New Folder
            </button>
          </div>

          {/* Personal vs Global Vault Tabs */}
          <div className="scope-tabs" style={{ marginBottom: "22px" }}>
            <div 
              className={`scope-tab ${vaultTab === "personal" ? "active" : ""}`}
              onClick={() => setVaultTab("personal")}
              style={{ display: "flex", alignItems: "center", gap: "6px" }}
            >
              <Lock size={13} style={{ color: vaultTab === "personal" ? "var(--text)" : "var(--text3)" }} />
              <span>Personal Vault</span>
              <span style={{ fontSize: "11px", opacity: 0.7 }}>({personalRootFolders.length})</span>
            </div>
            <div 
              className={`scope-tab ${vaultTab === "global" ? "active" : ""}`}
              onClick={() => setVaultTab("global")}
              style={{ display: "flex", alignItems: "center", gap: "6px" }}
            >
              <Globe size={13} style={{ color: vaultTab === "global" ? "var(--accent)" : "var(--text3)" }} />
              <span>Global Vault</span>
              <span style={{ 
                background: vaultTab === "global" ? "var(--accent)" : "var(--border2)", 
                color: vaultTab === "global" ? "#fff" : "var(--text2)",
                fontSize: "10px", fontWeight: 700, padding: "1px 6px", borderRadius: "10px" 
              }}>
                Pool ({globalRootFolders.length})
              </span>
            </div>
          </div>

          {/* Folders Grid */}
          <div className="folders-grid">
            {foldersLoading ? (
               <div style={{ padding: "40px", textAlign: "center" }}>
                 <Loader2 className="animate-spin mx-auto" />
               </div>
            ) : displayedRootFolders.length === 0 ? (
              <div className="card" style={{ padding: "48px 24px", textAlign: "center" }}>
                <FolderIcon className="mx-auto mb-3 opacity-30" size={36} />
                <h3 style={{ fontSize: "15px", fontWeight: 600, color: "var(--text)" }}>
                  {vaultTab === "personal" ? "No Personal Folders Yet" : "No Global Repository Folders Yet"}
                </h3>
                <p style={{ fontSize: "13px", color: "var(--text3)", maxWidth: "380px", margin: "6px auto 16px" }}>
                  {vaultTab === "personal" 
                    ? "Create private folders for your confidential client documents and draft agreements." 
                    : "Create shared folders to build the universal organization legal knowledge base accessible to all team members."}
                </p>
                <button className="btn-accent" style={{ margin: "0 auto" }} onClick={handleOpenCreateModal}>
                  <Plus size={14} />
                  {vaultTab === "personal" ? "Create Personal Folder" : "Create Global Folder"}
                </button>
              </div>
            ) : displayedRootFolders.map((folder) => {
              const isOwner = folder.owner_id === user?.id;
              return (
                <div key={folder.id} className="folder-item" onClick={() => setFolderPath([folder])}>
                  <div className="fi-icon" style={{
                    background: folder.is_global ? "rgba(79, 70, 229, 0.12)" : "var(--bg2)",
                    color: folder.is_global ? "var(--accent)" : "var(--text2)"
                  }}>
                    {folder.is_global ? <Globe size={16} /> : <FolderIcon size={16} />}
                  </div>
                  <div className="fi-info">
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <strong>{folder.name}</strong>
                      {folder.is_global && (
                        <span style={{
                          fontSize: "10px", fontWeight: 700, padding: "1px 6px",
                          borderRadius: "4px", background: "var(--accent-light)", color: "var(--accent)",
                          textTransform: "uppercase", letterSpacing: "0.04em"
                        }}>
                          Global Pool
                        </span>
                      )}
                    </div>
                    <span>{folder.description || (folder.is_global ? "Organization-wide legal repository" : "Personal legal documents")}</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    <span className="fi-docs">{folder.document_count} documents</span>
                    
                    {/* Delete: only creator can delete */}
                    {isOwner ? (
                      <div 
                        className="fi-delete" 
                        title="Delete folder"
                        onClick={(e) => { e.stopPropagation(); deleteFolder.mutate(folder.id); }}
                      >
                        <Trash2 size={13} />
                      </div>
                    ) : (
                      <div 
                        style={{ padding: "6px", color: "var(--text3)", cursor: "not-allowed" }}
                        title="Only the original creator can delete this folder"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Lock size={13} />
                      </div>
                    )}
                    
                    <div style={{ color: "var(--text3)" }}>
                      <ChevronRight size={14} />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <>
          {/* Breadcrumbs Navigation */}
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "16px", flexWrap: "wrap" }}>
            <div 
              className="back-btn" 
              onClick={() => setFolderPath([])}
              style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", color: "var(--text2)", fontSize: "13px", fontWeight: 500 }}
            >
              <ArrowLeft className="w-4 h-4" /> Vault Root
            </div>
            
            <span style={{ color: "var(--border2)" }}>/</span>
            
            {folderPath.map((f, idx) => {
              const isLast = idx === folderPath.length - 1;
              return (
                <div key={f.id} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span 
                    onClick={() => !isLast && setFolderPath(prev => prev.slice(0, idx + 1))}
                    style={{
                      fontSize: "13px",
                      fontWeight: isLast ? 700 : 500,
                      color: isLast ? "var(--text)" : "var(--accent)",
                      cursor: isLast ? "default" : "pointer"
                    }}
                  >
                    {f.name}
                  </span>
                  {!isLast && <span style={{ color: "var(--border2)" }}>/</span>}
                </div>
              );
            })}

            <span style={{
              marginLeft: "auto", fontSize: "11px", fontWeight: 600,
              padding: "2px 8px", borderRadius: "12px", background: "var(--bg2)", color: "var(--text3)"
            }}>
              Depth: {currentDepth} / 5
            </span>
          </div>

          {/* Folder Header */}
          <div className="toolbar" style={{ marginBottom: "20px" }}>
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <h1 style={{ margin: 0, fontSize: "22px", fontWeight: 700 }}>{selectedFolder.name}</h1>
                {selectedFolder.is_global ? (
                  <span style={{
                    fontSize: "10.5px", fontWeight: 700, padding: "2px 8px",
                    borderRadius: "6px", background: "var(--accent-light)", color: "var(--accent)"
                  }}>
                    🌐 Global Pool
                  </span>
                ) : (
                  <span style={{
                    fontSize: "10.5px", fontWeight: 600, padding: "2px 8px",
                    borderRadius: "6px", background: "var(--bg2)", color: "var(--text2)"
                  }}>
                    🔒 Personal
                  </span>
                )}
              </div>
              <p style={{ margin: "4px 0 0", fontSize: "12px", color: "var(--text2)" }}>
                {selectedFolder.description || "Manage documents and subfolders in this legal repository"}
              </p>
            </div>

            <div style={{ display: "flex", gap: "8px" }}>
              {currentDepth < 5 && (
                <button 
                  className="btn-accent" 
                  onClick={handleOpenCreateModal}
                  style={{ background: "var(--white)", color: "var(--text)", border: "1px solid var(--border)" }}
                >
                  <FolderPlus size={14} />
                  New Subfolder
                </button>
              )}
            </div>
          </div>

          {/* Subfolders Grid (if any) */}
          {currentSubfolders.length > 0 && (
            <div style={{ marginBottom: "24px" }}>
              <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "8px" }}>
                Subfolders ({currentSubfolders.length})
              </div>
              <div className="folders-grid">
                {currentSubfolders.map(sub => {
                  const isOwner = sub.owner_id === user?.id;
                  return (
                    <div key={sub.id} className="folder-item" onClick={() => setFolderPath(prev => [...prev, sub])}>
                      <div className="fi-icon" style={{ background: sub.is_global ? "rgba(79, 70, 229, 0.12)" : "var(--bg2)", color: sub.is_global ? "var(--accent)" : "var(--text2)" }}>
                        {sub.is_global ? <Globe size={15} /> : <FolderIcon size={15} />}
                      </div>
                      <div className="fi-info">
                        <strong>{sub.name}</strong>
                        <span>{sub.description || `${sub.document_count} documents`}</span>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                        <span className="fi-docs">{sub.document_count} docs</span>
                        {isOwner ? (
                          <div 
                            className="fi-delete" 
                            title="Delete subfolder"
                            onClick={(e) => { e.stopPropagation(); deleteFolder.mutate(sub.id); }}
                          >
                            <Trash2 size={13} />
                          </div>
                        ) : (
                          <div 
                            style={{ padding: "6px", color: "var(--text3)", cursor: "not-allowed" }}
                            title="Only original creator can delete"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <Lock size={13} />
                          </div>
                        )}
                        <ChevronRight size={14} style={{ color: "var(--text3)" }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Documents Section */}
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
             <div {...getRootProps()} className="card" style={{ padding: "32px", textAlign: "center", border: "2px dashed var(--border)", background: isDragActive ? "var(--accent-light)" : "var(--white)", cursor: "pointer" }}>
                <input {...getInputProps()} />
                <Upload className="mx-auto mb-2 text-accent" />
                <p style={{ fontSize: "14px", fontWeight: 500 }}>{isDragActive ? "Drop files here" : "Click or drag legal documents to upload"}</p>
                <p style={{ fontSize: "12px", color: "var(--text3)", marginTop: "4px" }}>
                  {selectedFolder.is_global 
                    ? "Uploaded files will be indexed into the Global Legal Pool for the entire team" 
                    : "PDF, DOCX, TXT up to 50MB (private to your account)"}
                </p>
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
                  <div className="empty-state" style={{ padding: "36px 16px", textAlign: "center" }}>
                    <FileText className="mx-auto mb-2 opacity-40" />
                    <p style={{ color: "var(--text3)" }}>No documents in this folder yet</p>
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    {documents.map(doc => {
                      const isDocOwner = !doc.owner_id || doc.owner_id === user?.id;
                      return (
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
                          
                          {/* Deletion check: creator only */}
                          {isDocOwner ? (
                            <button
                              onClick={(e) => { e.stopPropagation(); deleteDoc.mutate(doc.id); }}
                              title="Delete document"
                              style={{ padding: "6px", color: "var(--red)", background: "transparent", border: "none", cursor: "pointer", flexShrink: 0 }}
                            >
                              <Trash2 size={14} />
                            </button>
                          ) : (
                            <div
                              title="Only the original uploader can delete this document"
                              style={{ padding: "6px", color: "var(--text3)", cursor: "not-allowed", flexShrink: 0 }}
                            >
                              <Lock size={13} />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
             </div>
          </div>
        </>
      )}

      {/* Creation Modal */}
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
              maxWidth: "460px", width: "100%", 
              boxShadow: "0 32px 64px -12px rgba(0, 0, 0, 0.14)",
              border: "1px solid rgba(255,255,255,0.7)"
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                <h2 style={{ margin: 0, fontSize: "18px", fontWeight: 700 }}>
                  {selectedFolder ? `New Subfolder (Depth ${currentDepth + 1}/5)` : "New Legal Folder"}
                </h2>
                <p style={{ margin: 0, fontSize: "12px", color: "var(--text3)" }}>
                  {selectedFolder 
                    ? `Creating nested subfolder under "${selectedFolder.name}"`
                    : "Create a repository for legal agreements and regulatory documents"}
                </p>
              </div>
              <button 
                onClick={() => setShowCreateModal(false)} 
                style={{ background: "var(--bg2)", border: "none", cursor: "pointer", width: "32px", height: "32px", borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text2)" }}
              >
                <X size={18}/>
              </button>
            </div>

            {/* Folder Type Selector (Only in root level) */}
            {!selectedFolder ? (
              <div style={{ marginBottom: "18px" }}>
                <label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: "6px" }}>
                  REPOSITORY TYPE
                </label>
                <div className="scope-tabs" style={{ width: "100%", marginBottom: "10px" }}>
                  <div 
                    className={`scope-tab ${!newFolderIsGlobal ? "active" : ""}`}
                    onClick={() => setNewFolderIsGlobal(false)}
                    style={{ flex: 1, textAlign: "center", display: "flex", justifyContent: "center", alignItems: "center", gap: "6px" }}
                  >
                    <Lock size={13} />
                    <span>Personal Folder</span>
                  </div>
                  <div 
                    className={`scope-tab ${newFolderIsGlobal ? "active" : ""}`}
                    onClick={() => setNewFolderIsGlobal(true)}
                    style={{ flex: 1, textAlign: "center", display: "flex", justifyContent: "center", alignItems: "center", gap: "6px" }}
                  >
                    <Globe size={13} />
                    <span>Global Vault</span>
                  </div>
                </div>
              </div>
            ) : null}

            {/* Global Legal Repository Disclaimer / Notification Banner */}
            {(selectedFolder ? selectedFolder.is_global : newFolderIsGlobal) && (
              <div style={{
                background: "rgba(79, 70, 229, 0.08)",
                border: "1px solid rgba(79, 70, 229, 0.2)",
                borderRadius: "10px",
                padding: "12px 14px",
                marginBottom: "18px",
                display: "flex",
                gap: "10px"
              }}>
                <Globe size={18} style={{ color: "var(--accent)", flexShrink: 0, marginTop: "2px" }} />
                <div style={{ fontSize: "12px", lineHeight: "1.45" }}>
                  <strong style={{ display: "block", color: "var(--accent)", marginBottom: "2px" }}>
                    Organization-Wide Legal Pool
                  </strong>
                  This folder and all uploaded legal documents will be indexed into the Global Knowledge Base. Every member of your organization will have read, upload, and search access across LexAI.
                </div>
              </div>
            )}
            
            <div className="form-group" style={{ marginBottom: "16px" }}>
              <label style={{ fontSize: "12px", fontWeight: 600, color: "var(--text2)", marginBottom: "6px", display: "block" }}>
                FOLDER NAME *
              </label>
              <input 
                value={newFolderName} 
                onChange={e => setNewFolderName(e.target.value)} 
                placeholder="e.g. Master Vendor Agreements" 
                autoFocus 
                style={{ height: "44px", borderRadius: "8px", fontSize: "14px" }}
              />
            </div>

            <div className="form-group" style={{ marginBottom: "24px" }}>
              <label style={{ fontSize: "12px", fontWeight: 600, color: "var(--text2)", marginBottom: "6px", display: "block" }}>
                DESCRIPTION (OPTIONAL)
              </label>
              <input 
                value={newFolderDesc} 
                onChange={e => setNewFolderDesc(e.target.value)} 
                placeholder="e.g. Standard terms, SLAs, and commercial clauses" 
                style={{ height: "44px", borderRadius: "8px", fontSize: "14px" }}
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
                onClick={handleCreateSubmit} 
                disabled={!newFolderName.trim() || createFolder.isPending}
                style={{ flex: 2, margin: 0, height: "44px", borderRadius: "10px" }}
              >
                {createFolder.isPending ? <Loader2 className="animate-spin" size={16} /> : (selectedFolder ? "Create Subfolder" : "Create Folder")}
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
