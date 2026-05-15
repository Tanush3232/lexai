"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/stores/auth-store";
import { api } from "@/lib/api";

type Ticket = {
  id: string;
  title: string;
  status: string;
  priority: string;
  updated_at: string;
};

export default function TicketsPage() {
  const { user } = useAuthStore();
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [view, setView] = useState("all");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Force view to 'involved' if user is not admin/reviewer
    if (user && !["ops_admin", "super_admin", "reviewer"].includes(user.role) && view === "all") {
      setView("involved");
    }
  }, [user, view]);

  useEffect(() => {
    async function fetchTickets() {
      try {
        setLoading(true);
        const res = await api.get(`/tickets?view=${view}`);
        setTickets(res.data);
      } catch (err) {
        console.error("Failed to load tickets", err);
      } finally {
        setLoading(false);
      }
    }
    fetchTickets();
  }, [view]);

  if (!user) return null;

  const isAdmin = ["ops_admin", "super_admin", "reviewer"].includes(user.role);

  return (
    <div className="tickets-container" style={{ padding: "20px", maxWidth: "1200px", margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
        <h1 style={{ fontSize: "24px", fontWeight: "600", color: "#1e293b" }}>Legal Tickets</h1>
        <div style={{ fontSize: "14px", color: "#64748b" }}>Tickets can only be created by sending an email to app.info@adventz.com with [TICKET] in the subject.</div>
      </div>

      <div style={{ display: "flex", gap: "10px", marginBottom: "20px", borderBottom: "1px solid #e2e8f0" }}>
        {isAdmin && (
          <button 
            onClick={() => setView("all")}
            style={{ padding: "10px 20px", borderBottom: view === "all" ? "2px solid #2563eb" : "none", color: view === "all" ? "#2563eb" : "#64748b", background: "none", borderTop: "none", borderLeft: "none", borderRight: "none", cursor: "pointer", fontWeight: view === "all" ? "600" : "400" }}
          >
            All Tickets
          </button>
        )}
        <button 
          onClick={() => setView("assigned")}
          style={{ padding: "10px 20px", borderBottom: view === "assigned" ? "2px solid #2563eb" : "none", color: view === "assigned" ? "#2563eb" : "#64748b", background: "none", borderTop: "none", borderLeft: "none", borderRight: "none", cursor: "pointer", fontWeight: view === "assigned" ? "600" : "400" }}
        >
          Assigned to Me
        </button>
        <button 
          onClick={() => setView("involved")}
          style={{ padding: "10px 20px", borderBottom: view === "involved" ? "2px solid #2563eb" : "none", color: view === "involved" ? "#2563eb" : "#64748b", background: "none", borderTop: "none", borderLeft: "none", borderRight: "none", cursor: "pointer", fontWeight: view === "involved" ? "600" : "400" }}
        >
          Involved In
        </button>
      </div>

      {loading ? (
        <div style={{ textAlign: "center", padding: "40px", color: "#64748b" }}>Loading tickets...</div>
      ) : tickets.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px", backgroundColor: "#f8fafc", borderRadius: "8px", border: "1px dashed #cbd5e1", color: "#64748b" }}>
          No tickets found for this view.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {tickets.map(ticket => (
            <Link href={`/dashboard/tickets/${ticket.id}`} key={ticket.id} style={{ textDecoration: "none" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "15px 20px", backgroundColor: "white", borderRadius: "8px", border: "1px solid #e2e8f0", boxShadow: "0 1px 2px rgba(0,0,0,0.05)", transition: "all 0.2s" }}>
                <div>
                  <h3 style={{ margin: "0 0 5px 0", color: "#0f172a", fontSize: "16px", fontWeight: "600" }}>{ticket.title}</h3>
                  <div style={{ fontSize: "12px", color: "#64748b" }}>Updated: {new Date(ticket.updated_at).toLocaleString()}</div>
                </div>
                <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                  <span style={{ padding: "4px 8px", borderRadius: "4px", fontSize: "12px", fontWeight: "500", backgroundColor: ticket.priority === "high" || ticket.priority === "urgent" ? "#fee2e2" : "#f1f5f9", color: ticket.priority === "high" || ticket.priority === "urgent" ? "#ef4444" : "#64748b" }}>
                    {ticket.priority.toUpperCase()}
                  </span>
                  <span style={{ padding: "4px 8px", borderRadius: "4px", fontSize: "12px", fontWeight: "500", backgroundColor: ticket.status === "open" ? "#dcfce7" : ticket.status === "in_progress" ? "#fef9c3" : "#f1f5f9", color: ticket.status === "open" ? "#166534" : ticket.status === "in_progress" ? "#854d0e" : "#475569" }}>
                    {ticket.status.toUpperCase()}
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
