"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuthStore } from "@/lib/stores/auth-store";
import { ticketsApi, foldersApi, actsApi, draftsApi } from "@/lib/api";

interface OverviewStats {
  tickets: { total: number; open: number; in_progress: number; resolved: number };
  folders: number;
  acts: { total: number; pending: number; ready: number };
  drafts: number;
}

function StatCard({
  label,
  value,
  sub,
  href,
  color,
  icon,
}: {
  label: string;
  value: number | string;
  sub?: string;
  href: string;
  color: string;
  icon: React.ReactNode;
}) {
  return (
    <Link href={href} style={{ textDecoration: "none" }}>
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "16px",
          padding: "24px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          cursor: "pointer",
          transition: "all 0.2s ease",
          position: "relative",
          overflow: "hidden",
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLDivElement).style.transform = "translateY(-2px)";
          (e.currentTarget as HTMLDivElement).style.boxShadow = `0 8px 32px ${color}22`;
          (e.currentTarget as HTMLDivElement).style.borderColor = color;
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLDivElement).style.transform = "translateY(0)";
          (e.currentTarget as HTMLDivElement).style.boxShadow = "none";
          (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border)";
        }}
      >
        {/* Glow accent */}
        <div style={{
          position: "absolute", top: 0, right: 0, width: "120px", height: "120px",
          background: `radial-gradient(circle at top right, ${color}18, transparent 70%)`,
          pointerEvents: "none",
        }} />

        <div style={{
          width: "40px", height: "40px", borderRadius: "10px",
          background: `${color}20`, display: "flex", alignItems: "center", justifyContent: "center",
          color: color,
        }}>
          {icon}
        </div>

        <div>
          <div style={{ fontSize: "28px", fontWeight: 700, color: "var(--text)", lineHeight: 1 }}>
            {value}
          </div>
          <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--text2)", marginTop: "4px" }}>
            {label}
          </div>
          {sub && (
            <div style={{ fontSize: "12px", color: "var(--text3)", marginTop: "2px" }}>{sub}</div>
          )}
        </div>
      </div>
    </Link>
  );
}

function QuickActionCard({ href, label, description, icon, color }: {
  href: string; label: string; description: string; icon: React.ReactNode; color: string;
}) {
  return (
    <Link href={href} style={{ textDecoration: "none" }}>
      <div style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
        padding: "16px 20px",
        display: "flex", alignItems: "center", gap: "14px",
        cursor: "pointer",
        transition: "all 0.2s ease",
      }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLDivElement).style.borderColor = color;
          (e.currentTarget as HTMLDivElement).style.background = `${color}08`;
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border)";
          (e.currentTarget as HTMLDivElement).style.background = "var(--surface)";
        }}
      >
        <div style={{
          width: "36px", height: "36px", borderRadius: "8px", flexShrink: 0,
          background: `${color}20`, display: "flex", alignItems: "center", justifyContent: "center",
          color: color,
        }}>
          {icon}
        </div>
        <div>
          <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text)" }}>{label}</div>
          <div style={{ fontSize: "12px", color: "var(--text3)" }}>{description}</div>
        </div>
        <svg style={{ marginLeft: "auto", color: "var(--text3)", flexShrink: 0 }} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M9 18l6-6-6-6" />
        </svg>
      </div>
    </Link>
  );
}

export default function OverviewPage() {
  const { user } = useAuthStore();
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [ticketsRes, foldersRes, actsRes, draftsRes] = await Promise.allSettled([
          ticketsApi.list({ view: "all", limit: 500 }),
          foldersApi.list(),
          actsApi.stats(),
          draftsApi.list(),
        ]);

        const tickets = ticketsRes.status === "fulfilled" ? ticketsRes.value.data : [];
        const folders = foldersRes.status === "fulfilled" ? foldersRes.value.data : [];
        const actsStats = actsRes.status === "fulfilled" ? actsRes.value.data : null;
        const drafts = draftsRes.status === "fulfilled" ? draftsRes.value.data : [];

        const ticketList: any[] = Array.isArray(tickets) ? tickets : tickets?.tickets ?? [];

        setStats({
          tickets: {
            total: ticketList.length,
            open: ticketList.filter((t: any) => t.status === "open").length,
            in_progress: ticketList.filter((t: any) => t.status === "in_progress").length,
            resolved: ticketList.filter((t: any) => t.status === "resolved" || t.status === "closed").length,
          },
          folders: Array.isArray(folders) ? folders.length : 0,
          acts: {
            total: actsStats?.total ?? 0,
            pending: actsStats?.pending ?? 0,
            ready: actsStats?.ready ?? 0,
          },
          drafts: Array.isArray(drafts) ? drafts.length : 0,
        });
      } catch {
        // silently leave null — page will show skeleton
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const greeting = () => {
    const h = new Date().getHours();
    if (h < 12) return "Good morning";
    if (h < 17) return "Good afternoon";
    return "Good evening";
  };

  const isAdmin = ["ops_admin", "super_admin"].includes(user?.role || "");

  return (
    <div style={{ maxWidth: "1100px", margin: "0 auto", paddingBottom: "40px" }}>
      {/* Header */}
      <div style={{ marginBottom: "32px" }}>
        <h1 style={{ fontSize: "26px", fontWeight: 700, color: "var(--text)", margin: 0 }}>
          {greeting()}, {user?.full_name?.split(" ")[0] || "there"} 👋
        </h1>
        <p style={{ fontSize: "14px", color: "var(--text3)", marginTop: "6px" }}>
          Here's what's happening across LexAI today.
        </p>
      </div>

      {/* Stats Grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
        gap: "16px",
        marginBottom: "32px",
      }}>
        <StatCard
          label="Total Tickets"
          value={loading ? "—" : stats?.tickets.total ?? 0}
          sub={loading ? "" : `${stats?.tickets.open ?? 0} open · ${stats?.tickets.in_progress ?? 0} in progress`}
          href="/dashboard/tickets"
          color="#7c5cfc"
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
              <path d="M14 2v4a2 2 0 0 0 2 2h4" /><path d="m9 15 2 2 4-4" />
            </svg>
          }
        />
        <StatCard
          label="Document Vaults"
          value={loading ? "—" : stats?.folders ?? 0}
          sub="Folders in your vault"
          href="/dashboard/folders"
          color="#06b6d4"
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
          }
        />
        <StatCard
          label="Legal Acts"
          value={loading ? "—" : stats?.acts.total ?? 0}
          sub={loading ? "" : `${stats?.acts.ready ?? 0} ready · ${stats?.acts.pending ?? 0} pending`}
          href="/dashboard/acts"
          color="#f59e0b"
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
          }
        />
        <StatCard
          label="Contract Drafts"
          value={loading ? "—" : stats?.drafts ?? 0}
          sub="All time drafts"
          href="/dashboard/drafts"
          color="#10b981"
          icon={
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          }
        />
      </div>

      {/* Ticket Status Breakdown */}
      {stats && stats.tickets.total > 0 && (
        <div style={{
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: "16px", padding: "24px", marginBottom: "32px",
        }}>
          <div style={{ fontSize: "15px", fontWeight: 600, color: "var(--text)", marginBottom: "16px" }}>
            Ticket Status Breakdown
          </div>
          <div style={{ display: "flex", gap: "0", borderRadius: "8px", overflow: "hidden", height: "8px", marginBottom: "16px" }}>
            {stats.tickets.open > 0 && (
              <div style={{ flex: stats.tickets.open, background: "#7c5cfc" }} title={`Open: ${stats.tickets.open}`} />
            )}
            {stats.tickets.in_progress > 0 && (
              <div style={{ flex: stats.tickets.in_progress, background: "#f59e0b" }} title={`In Progress: ${stats.tickets.in_progress}`} />
            )}
            {stats.tickets.resolved > 0 && (
              <div style={{ flex: stats.tickets.resolved, background: "#10b981" }} title={`Resolved: ${stats.tickets.resolved}`} />
            )}
          </div>
          <div style={{ display: "flex", gap: "24px", flexWrap: "wrap" }}>
            {[
              { label: "Open", count: stats.tickets.open, color: "#7c5cfc" },
              { label: "In Progress", count: stats.tickets.in_progress, color: "#f59e0b" },
              { label: "Resolved / Closed", count: stats.tickets.resolved, color: "#10b981" },
            ].map((s) => (
              <div key={s.label} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <div style={{ width: "10px", height: "10px", borderRadius: "50%", background: s.color }} />
                <span style={{ fontSize: "13px", color: "var(--text2)" }}>{s.label}</span>
                <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)" }}>{s.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Quick Actions */}
      <div style={{ marginBottom: "12px" }}>
        <div style={{ fontSize: "15px", fontWeight: 600, color: "var(--text)", marginBottom: "12px" }}>
          Quick Access
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "10px" }}>
          <QuickActionCard href="/dashboard/tickets" label="View Tickets" description="Review and manage legal tickets" color="#7c5cfc"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" /><path d="M14 2v4a2 2 0 0 0 2 2h4" /><path d="m9 15 2 2 4-4" /></svg>}
          />
          <QuickActionCard href="/dashboard/intelligence" label="Document Intelligence" description="AI-powered document analysis" color="#06b6d4"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>}
          />
          <QuickActionCard href="/dashboard/drafts" label="Contract Drafting" description="Create and review contracts" color="#10b981"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>}
          />
          <QuickActionCard href="/dashboard/web-search" label="Legal Web Search" description="Search and cite legal sources" color="#f59e0b"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></svg>}
          />
          <QuickActionCard href="/dashboard/translations" label="Translations" description="Translate legal documents" color="#ec4899"
            icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M5 8l6 6" /><path d="M4 14l6-6 2-3" /><path d="M2 5h12" /><path d="M7 2h1" /><path d="M22 22l-5-10-5 10" /><path d="M14 18h6" /></svg>}
          />
          {isAdmin && (
            <QuickActionCard href="/dashboard/audit" label="Audit Log" description="Review system activity logs" color="#8b5cf6"
              icon={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="16" y1="13" x2="8" y2="13" /><line x1="16" y1="17" x2="8" y2="17" /></svg>}
            />
          )}
        </div>
      </div>
    </div>
  );
}
