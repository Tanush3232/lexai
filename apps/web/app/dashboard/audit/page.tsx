"use client";
import { useQuery } from "@tanstack/react-query";
import { auditApi } from "@/lib/api";
import { Loader2, ClipboardCheck } from "lucide-react";

export default function AuditPage() {
  const { data: logs = [], isLoading } = useQuery({
    queryKey: ["audit"],
    queryFn: () => auditApi.list().then(r => r.data),
    refetchInterval: 30000,
  });

  return (
    <div className="fade-in">
      <div className="page-header">
        <h1>Audit Trail</h1>
        <p>A immutable record of all document accesses, AI generations, and security events.</p>
      </div>

      <div className="card" style={{ padding: "0", overflow: "hidden" }}>
        <table className="audit-table" style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: "var(--bg2)", borderBottom: "1.5px solid var(--border)" }}>
              <th style={{ padding: "14px 20px", textAlign: "left", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Event Type</th>
              <th style={{ padding: "14px 20px", textAlign: "left", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Resource</th>
              <th style={{ padding: "14px 20px", textAlign: "left", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Reference ID</th>
              <th style={{ padding: "14px 20px", textAlign: "left", fontSize: "11px", fontWeight: 700, color: "var(--text3)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Timestamp</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={4} style={{ padding: "40px", textAlign: "center" }}>
                   <Loader2 size={24} className="animate-spin mx-auto" style={{ color: "var(--accent)" }} />
                </td>
              </tr>
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: "40px", textAlign: "center", color: "var(--text3)" }}>
                   <ClipboardCheck size={24} className="mx-auto mb-2 opacity-20" />
                   No events recorded in the last 30 days.
                </td>
              </tr>
            ) : logs.map((log: any) => (
              <tr key={log.id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "14px 20px" }}>
                  <span className={`badge ${
                    ['upload', 'create', 'approve'].includes(log.action) ? 'green' : 
                    ['delete'].includes(log.action) ? 'red' :
                    ['chat', 'draft'].includes(log.action) ? 'purple' : 'amber'
                  }`}>{log.action}</span>
                </td>
                <td style={{ padding: "14px 20px", fontSize: "13px", fontWeight: 500, color: "var(--text)", textTransform: "capitalize" }}>
                  {log.resource_type}
                </td>
                <td style={{ padding: "14px 20px", fontSize: "12px", fontFamily: "monospace", color: "var(--text3)" }}>
                  {log.resource_id?.slice(0, 16) || "N/A"}
                </td>
                <td style={{ padding: "14px 20px", fontSize: "13px", color: "var(--text2)" }}>
                  {new Date(log.created_at).toLocaleString('en-US', { 
                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
