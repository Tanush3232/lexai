"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { draftsApi } from "@/lib/api";
import { toast } from "sonner";
import { Loader2, Plus, FileText, CheckCircle, Clock, AlertTriangle, ArrowLeft, Send, Save, Check, Info } from "lucide-react";

const CONTRACT_TYPES = [
  { 
    value: "NDA", 
    label: "Non-Disclosure Agreement", 
    desc: "Mutual or one-way confidentiality",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
      </svg>
    )
  },
  { 
    value: "CNF", 
    label: "CNF Agreement", 
    desc: "Confirmation and framework agreement",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
      </svg>
    )
  },
  { 
    value: "software_license", 
    label: "Software License", 
    desc: "IP licensing and usage rights",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
      </svg>
    )
  },
  { 
    value: "lease", 
    label: "Lease & License Agreement", 
    desc: "Property or asset lease terms",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>
      </svg>
    )
  },
  { 
    value: "vendor_contract", 
    label: "Vendor Contract", 
    desc: "Services and supply agreements",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>
      </svg>
    )
  },
  { 
    value: "purchase_order", 
    label: "Purchase Order", 
    desc: "Goods and procurement orders",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>
      </svg>
    )
  },
];

const FORM_FIELDS: Record<string, { label: string; type: string; required?: boolean }[]> = {
  NDA: [
    { label: "Disclosing Party", type: "text", required: true },
    { label: "Receiving Party", type: "text", required: true },
    { label: "Effective Date", type: "date", required: true },
    { label: "Jurisdiction", type: "text" },
    { label: "Governing Law", type: "text" },
    { label: "Confidentiality Term (months)", type: "number" },
    { label: "Purpose of Disclosure", type: "textarea" },
  ],
  vendor_contract: [
    { label: "Vendor Name", type: "text", required: true },
    { label: "Client Name", type: "text", required: true },
    { label: "Effective Date", type: "date", required: true },
    { label: "Scope of Services", type: "textarea" },
    { label: "Payment Terms", type: "text" },
  ],
};
const DEFAULT_FIELDS = [
  { label: "Party A", type: "text", required: true },
  { label: "Party B", type: "text", required: true },
  { label: "Effective Date", type: "date" },
  { label: "Special Clauses", type: "textarea" },
];

interface Draft { id: string; contract_type: string; title: string; status: string; created_at: string; }

export default function DraftsPage() {
  const qc = useQueryClient();
  const [view, setView] = useState<"list" | "new" | "inputs" | "detail">("new");
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [formData, setFormData] = useState<Record<string, string>>({});
  const [draftTitle, setDraftTitle] = useState("");
  const [activeDraft, setActiveDraft] = useState<{id: string; content: string; issues_json?: string; provenance_json?: string; status: string; title: string} | null>(null);
  const [editContent, setEditContent] = useState("");

  const { data: drafts = [], isLoading } = useQuery<Draft[]>({
    queryKey: ["drafts"],
    queryFn: () => draftsApi.list().then(r => r.data),
  });

  const createDraft = useMutation({
    mutationFn: () => draftsApi.create({
      contract_type: selectedType!,
      title: draftTitle || `Draft ${selectedType?.toUpperCase()}`,
      inputs: formData,
    }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["drafts"] });
      toast.success("Draft generated!");
      setActiveDraft(res.data);
      setEditContent(res.data.content || "");
      setView("detail");
    },
    onError: () => toast.error("Draft generation failed"),
  });

  const updateDraft = useMutation({
    mutationFn: () => draftsApi.update(activeDraft!.id, { content: editContent }),
    onSuccess: () => toast.success("Draft saved"),
  });

  const approveDraft = useMutation({
    mutationFn: () => draftsApi.approve(activeDraft!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] });
      toast.success("Draft approved!");
      if (activeDraft) setActiveDraft({ ...activeDraft, status: "approved" });
    },
    onError: (e: any) => toast.error(e.response?.data?.detail || "Cannot approve"),
  });

  const fields = selectedType ? (FORM_FIELDS[selectedType] || DEFAULT_FIELDS) : [];

  if (view === "detail" && activeDraft) {
    const issues = activeDraft.issues_json ? JSON.parse(activeDraft.issues_json) : [];
    const prov = activeDraft.provenance_json ? JSON.parse(activeDraft.provenance_json) : [];
    return (
      <div className="fade-in">
        <div className="toolbar">
           <button onClick={() => setView("list")} className="back-btn" style={{ marginBottom: 0 }}>
             <ArrowLeft size={16} /> Back to Drafts
           </button>
           <div style={{ display: "flex", gap: "10px" }}>
              {activeDraft.status !== "approved" && (
                <>
                  <button className="btn-secondary" onClick={() => updateDraft.mutate()}>
                    <Save size={14} /> Save Changes
                  </button>
                  <button className="btn-accent" onClick={() => approveDraft.mutate()}>
                    <Check size={14} /> Finalize & Approve
                  </button>
                </>
              )}
           </div>
        </div>

        <div className="page-header">
           <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
             <h1>{activeDraft.title}</h1>
             <span className={`badge ${activeDraft.status === "approved" ? "green" : "amber"}`}>{activeDraft.status}</span>
           </div>
           <p>Last edited: {new Date().toLocaleDateString()}</p>
        </div>

        <div className="two-col" style={{ gridTemplateColumns: "1fr 340px" }}>
           <div className="card" style={{ padding: "0", minHeight: "600px", display: "flex", flexDirection: "column" }}>
              <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--border)", background: "var(--bg)", fontSize: "12px", fontWeight: 600, color: "var(--text2)" }}>
                Drafting Editor
              </div>
              <textarea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                disabled={activeDraft.status === "approved"}
                style={{ flex: 1, padding: "32px", border: "none", outline: "none", fontSize: "14px", lineHeight: "1.7", fontFamily: "inherit", resize: "none" }}
              />
           </div>

           <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div className="card" style={{ padding: "20px" }}>
                 <div className="section-title">AI Policy Audit</div>
                 {issues.length === 0 ? (
                   <div style={{ fontSize: "13px", color: "var(--text3)", padding: "10px 0" }}>
                     <CheckCircle size={14} className="text-green mr-2" style={{ display: "inline" }}/>
                     No high-risk issues found.
                   </div>
                 ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "12px" }}>
                       {issues.map((issue: any, i: number) => (
                         <div key={i} style={{ padding: "10px", background: "var(--amber-light)", border: "1px solid var(--amber)", borderRadius: "8px", fontSize: "12px" }}>
                            <div style={{ fontWeight: 700, color: "var(--amber)", marginBottom: "2px" }}>{issue.severity?.toUpperCase()} SEVERITY</div>
                            <div style={{ color: "var(--text2)" }}>{issue.description}</div>
                         </div>
                       ))}
                    </div>
                 )}
              </div>

              <div className="card" style={{ padding: "20px" }}>
                 <div className="section-title">Clause Provenance</div>
                 <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "12px" }}>
                    {prov.length > 0 ? prov.map((p: any, i: number) => (
                      <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "12px" }}>
                         <span style={{ color: "var(--text2)" }}>Clause {p.clause_number}</span>
                         <span className="badge-indexed" style={{ fontSize: "10px", background: p.provenance === "template" ? "var(--bg2)" : "var(--accent-light)", color: p.provenance === "template" ? "var(--text2)" : "var(--accent)" }}>{p.provenance}</span>
                      </div>
                    )) : (
                      <div style={{ fontSize: "13px", color: "var(--text3)" }}>No provenance data recorded.</div>
                    )}
                 </div>
              </div>
           </div>
        </div>
      </div>
    );
  }

  if (view === "new") {
    const selectedItem = CONTRACT_TYPES.find(c => c.value === selectedType);
    return (
      <div className="fade-in">
        <button onClick={() => setView("list")} className="back-btn" style={{ background: "white", padding: "8px 16px", borderRadius: "8px", display: "flex", alignItems: "center", gap: "8px", border: "1px solid var(--border)", cursor: "pointer", fontSize: "14px", fontWeight: 500 }}>
          <ArrowLeft size={16} /> Back
        </button>
        <div className="page-header" style={{ marginTop: "24px" }}>
           <h1>New Contract Draft</h1>
           <p>Choose a contract type to get started</p>
        </div>

        <div className="drafting-grid">
           {CONTRACT_TYPES.map(ct => (
              <div 
                key={ct.value} 
                className={`contract-type-card ${selectedType === ct.value ? "selected" : ""}`}
                onClick={() => setSelectedType(ct.value)}
              >
                 {selectedType === ct.value && (
                   <div style={{ position: "absolute", right: "18px", top: "50%", transform: "translateY(-50%)", width: "20px", height: "20px", borderRadius: "999px", background: "var(--accent)", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px", fontWeight: 700 }}>
                     •
                   </div>
                 )}
                 <div className="ct-icon-box">
                    <div style={{ width: "20px", height: "20px" }}>{ct.icon}</div>
                 </div>
                 <div className="ct-content">
                    <div className="ct-title">{ct.label}</div>
                    <div className="ct-desc">{ct.desc}</div>
                 </div>
              </div>
           ))}
        </div>

        {selectedType && (
           <div className="floating-footer">
              <div className="footer-text">
                {selectedItem?.label} selected
              </div>
              <button 
                className="btn-continue"
                onClick={() => setView("inputs")}
              >
                Continue with this type
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ width: "16px", height: "16px" }}>
                  <polyline points="9 18 15 12 9 6"/>
                </svg>
              </button>
           </div>
        )}
      </div>
    );
  }

  if (view === "inputs") {
    return (
      <div className="fade-in" style={{ maxWidth: "700px", margin: "0 auto" }}>
        <button onClick={() => setView("new")} className="back-btn">
          <ArrowLeft size={16} /> Change Template
        </button>
        <div className="page-header">
           <h1>Input Details</h1>
           <p>Provide specific details for your {selectedType} draft.</p>
        </div>

        <div className="card" style={{ padding: "32px" }}>
           <div className="form-group">
            <label>System Title</label>
            <input value={draftTitle} onChange={e => setDraftTitle(e.target.value)} placeholder="e.g. Master Service Agreement - Acme Corp" />
          </div>

          {fields.map(f => (
            <div className="form-group" key={f.label}>
               <label>{f.label} {f.required && "*"}</label>
               {f.type === "textarea" ? (
                 <textarea value={formData[f.label] || ""} onChange={e => setFormData(d => ({ ...d, [f.label]: e.target.value }))} rows={3}  />
               ) : (
                 <input type={f.type} value={formData[f.label] || ""} onChange={e => setFormData(d => ({ ...d, [f.label]: e.target.value }))} />
               )}
            </div>
          ))}

          <button className="btn-primary" onClick={() => createDraft.mutate()} disabled={createDraft.isPending} style={{ width: "100%", marginTop: "24px" }}>
            {createDraft.isPending ? <Loader2 className="animate-spin mr-2" /> : <Send size={14} className="mr-2" />}
            Generate Draft with LexAI
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="fade-in">
      <div className="page-header" style={{ textAlign: "center", marginBottom: "48px" }}>
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: "16px", marginBottom: "8px" }}>
          <h1 style={{ margin: 0 }}>Contract Drafting</h1>
          {drafts.length > 0 && <span className="count-badge" style={{ margin: 0 }}>{drafts.length} drafts</span>}
        </div>
        <p style={{ margin: "0 auto", color: "var(--text2)", fontSize: "16px" }}>Start generating high-quality legal contracts using AI grounded in your precedents.</p>
        
        {/* Consolidated Primary Action - Centered Top */}
        <div style={{ marginTop: "32px", display: "flex", justifyContent: "center" }}>
          <button className="btn-accent" style={{ padding: "12px 28px", height: "auto", fontSize: "14px" }} onClick={() => setView("new")}>
            <Plus size={18} className="mr-2" /> New Draft
          </button>
        </div>
      </div>

      <div style={{ paddingBottom: "100px" }}>
        {isLoading ? (
          <div style={{ padding: "40px", textAlign: "center" }}><Loader2 className="animate-spin mx-auto text-accent" /></div>
        ) : drafts.length === 0 ? (
          /* Empty State - Centered Horizontally & Vertically in natural flow area */
          <div style={{ minHeight: "30vh", display: "flex", alignItems: "center", justifyContent: "center", marginTop: "40px" }}>
            <div style={{ textAlign: "center" }}>
              <div style={{ width: "80px", height: "80px", background: "var(--accent-light)", borderRadius: "20px", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 32px" }}>
                <FileText size={40} className="text-accent" />
              </div>
              <h2 style={{ fontSize: "24px", fontWeight: 700, marginBottom: "8px", fontFamily: "var(--font-playfair)" }}>No drafts yet</h2>
              <p style={{ color: "var(--text2)", fontSize: "16px" }}>Create your first draft</p>
            </div>
          </div>
        ) : (
          /* List View - Centered Grid Flow */
          <div className="folders-grid" style={{ gridTemplateColumns: "1fr", maxWidth: "900px", margin: "0 auto" }}>
            {drafts.map((draft) => (
               <div key={draft.id} className="folder-item" onClick={async () => {
                  const res = await draftsApi.get(draft.id);
                  setActiveDraft(res.data);
                  setEditContent(res.data.content || "");
                  setView("detail");
               }}>
                 <div className="fi-icon" style={{ background: "var(--accent-light)", color: "var(--accent)" }}>
                   <FileText size={18} />
                 </div>
                 <div className="fi-info" style={{ marginLeft: "14px" }}>
                   <strong style={{ fontSize: "15px" }}>{draft.title}</strong>
                   <span>{draft.contract_type} · Created {new Date(draft.created_at).toLocaleDateString()}</span>
                 </div>
                 <div style={{ display: "flex", alignItems: "center", gap: "20px", marginLeft: "auto" }}>
                    <span className={`badge ${draft.status === "approved" ? "green" : "amber"}`}>{draft.status}</span>
                    <div style={{ color: "var(--text3)" }}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: "16px", height: "16px" }}><polyline points="9 18 15 12 9 6"/></svg>
                    </div>
                 </div>
               </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
