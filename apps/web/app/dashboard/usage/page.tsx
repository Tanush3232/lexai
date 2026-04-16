"use client";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/stores/auth-store";
import { usageApi } from "@/lib/api";

// ─── Types ──────────────────────────────────────────────────────────────────

interface Summary {
  total_calls: number;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
}

interface ModelRow {
  provider: string;
  model: string;
  calls: number;
  total_tokens: number;
  total_cost_usd: number;
}

interface FeatureRow {
  feature_name: string;
  calls: number;
  total_tokens: number;
  total_cost_usd: number;
}

interface LogRow {
  id: string;
  provider: string;
  model: string;
  feature_name: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  user_id: string | null;
  created_at: string;
  // cost breakdown from server
  input_rate_per_1m: number;
  output_rate_per_1m: number;
  input_cost_usd: number;
  output_cost_usd: number;
  tier_note: string;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function fmtCost(v: number) {
  if (v === 0) return "$0.00";
  if (v < 0.000001) return `$${v.toExponential(2)}`;
  if (v < 0.01) return `$${v.toFixed(6)}`;
  return `$${v.toFixed(4)}`;
}

function fmtTokens(v: number) {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(v);
}

const TH_STYLE: React.CSSProperties = {
  padding: "12px 16px",
  textAlign: "left",
  fontSize: "11px",
  fontWeight: 700,
  color: "var(--text3)",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  whiteSpace: "nowrap",
};

const TD_STYLE: React.CSSProperties = {
  padding: "12px 16px",
  fontSize: "13px",
  color: "var(--text)",
  borderBottom: "1px solid var(--border)",
};

// ─── Page ───────────────────────────────────────────────────────────────────

export default function UsagePage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user, _hasHydrated } = useAuthStore();
  const [logsPage, setLogsPage] = useState(1);
  const [tooltipRowId, setTooltipRowId] = useState<string | null>(null);
  const [recalcState, setRecalcState] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [recalcMsg, setRecalcMsg] = useState("");
  const [showPricing, setShowPricing] = useState(false);

  // Guard: ops admin only
  useEffect(() => {
    if (_hasHydrated && user?.role !== "ops_admin") {
      router.replace("/dashboard");
    }
  }, [_hasHydrated, user]);

  const { data: summary, isLoading: loadSummary } = useQuery<Summary>({
    queryKey: ["usage-summary"],
    queryFn: () => usageApi.summary().then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: byModel = [], isLoading: loadModel } = useQuery<ModelRow[]>({
    queryKey: ["usage-by-model"],
    queryFn: () => usageApi.byModel().then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: byFeature = [], isLoading: loadFeature } = useQuery<FeatureRow[]>({
    queryKey: ["usage-by-feature"],
    queryFn: () => usageApi.byFeature().then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: logs = [], isLoading: loadLogs } = useQuery<LogRow[]>({
    queryKey: ["usage-logs", logsPage],
    queryFn: () => usageApi.logs(logsPage, 50).then((r) => r.data),
    refetchInterval: 30_000,
  });

  if (!_hasHydrated || user?.role !== "ops_admin") return null;

  async function handleRecalculate() {
    setRecalcState("loading");
    setRecalcMsg("");
    try {
      const res = await usageApi.recalculateCosts();
      setRecalcMsg(res.data.message ?? "Costs updated.");
      setRecalcState("done");
      // Refresh all usage data
      await queryClient.invalidateQueries();
    } catch {
      setRecalcMsg("Recalculation failed.");
      setRecalcState("error");
    }
  }

  return (
    <div className="fade-in">
      {/* ── Header ── */}
      <div className="page-header" style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: "12px" }}>
        <div>
          <h1>LLM Usage Analytics</h1>
          <p>Token consumption and cost breakdown per model and feature — updated every minute.</p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
          <button
            onClick={handleRecalculate}
            disabled={recalcState === "loading"}
            style={{
              padding: "8px 16px", fontSize: "13px", fontWeight: 600,
              border: "1px solid var(--border)", borderRadius: "8px",
              background: recalcState === "loading" ? "var(--bg2)" : "var(--white)",
              color: recalcState === "error" ? "#dc2626" : recalcState === "done" ? "#16a34a" : "var(--text)",
              cursor: recalcState === "loading" ? "not-allowed" : "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {recalcState === "loading" ? "Recalculating…" : "↻ Recalculate Costs"}
          </button>
          {recalcMsg && (
            <span style={{ fontSize: "11px", color: recalcState === "error" ? "#dc2626" : "#16a34a" }}>{recalcMsg}</span>
          )}
        </div>
      </div>

      {/* ── Summary Cards ── */}
      <div className="stats-grid" style={{ marginBottom: "20px" }}>
        <div className="stat-card">
          <div className="stat-icon purple">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
            </svg>
          </div>
          <div className="stat-val">{loadSummary ? "—" : summary?.total_calls.toLocaleString()}</div>
          <div className="stat-label">Total API Calls</div>
        </div>

        <div className="stat-card">
          <div className="stat-icon blue">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
            </svg>
          </div>
          <div className="stat-val">{loadSummary ? "—" : fmtTokens(summary?.total_tokens ?? 0)}</div>
          <div className="stat-label">Total Tokens</div>
        </div>

        <div className="stat-card">
          <div className="stat-icon amber">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
            </svg>
          </div>
          <div className="stat-val">{loadSummary ? "—" : fmtCost(summary?.total_cost_usd ?? 0)}</div>
          <div className="stat-label">Total Cost (USD)</div>
        </div>

        <div className="stat-card">
          <div className="stat-icon green">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
            </svg>
          </div>
          <div className="stat-val">{loadSummary ? "—" : fmtTokens(summary?.total_input_tokens ?? 0)}</div>
          <div className="stat-label">Input Tokens</div>
        </div>
      </div>

      {/* ── Pricing Reference (collapsible) ── */}
      <div style={{
        marginBottom: "20px", border: "1px solid var(--border)", borderRadius: "10px",
        background: "var(--white)", overflow: "hidden",
      }}>
        <button
          onClick={() => setShowPricing((v) => !v)}
          style={{
            width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "12px 20px", border: "none", background: "none", cursor: "pointer",
            fontSize: "13px", fontWeight: 600, color: "var(--text)", textAlign: "left",
          }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ fontSize: "15px" }}>💲</span>
            Model Pricing Reference
            <span style={{ fontSize: "11px", fontWeight: 400, color: "var(--text3)" }}>— click to {showPricing ? "hide" : "expand"}</span>
          </span>
          <span style={{ fontSize: "11px", color: "var(--text3)", transition: "transform 0.2s", transform: showPricing ? "rotate(180deg)" : "rotate(0)" }}>▼</span>
        </button>
        {showPricing && (
          <div style={{ padding: "0 20px 16px 20px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
              {/* Pro card */}
              <div style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "14px 16px" }}>
                <div style={{ fontWeight: 700, fontSize: "13px", marginBottom: "8px", display: "flex", alignItems: "center", gap: "6px" }}>
                  <span style={{ display: "inline-block", width: "8px", height: "8px", borderRadius: "50%", background: "#7c3aed" }} />
                  Gemini 2.5 Pro
                </div>
                <div style={{ fontSize: "12px", lineHeight: "1.8", color: "var(--text2)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}><span>Input (≤200k prompt)</span><span style={{ fontWeight: 600 }}>$1.25 /1M</span></div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}><span>Input (&gt;200k prompt)</span><span style={{ fontWeight: 600 }}>$2.50 /1M</span></div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}><span>Output (≤200k prompt)</span><span style={{ fontWeight: 600 }}>$10.00 /1M</span></div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}><span>Output (&gt;200k prompt)</span><span style={{ fontWeight: 600 }}>$15.00 /1M</span></div>
                  <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--text3)" }}>Output includes thinking tokens. Tier based on prompt size.</div>
                </div>
              </div>
              {/* Flash card */}
              <div style={{ border: "1px solid var(--border)", borderRadius: "8px", padding: "14px 16px" }}>
                <div style={{ fontWeight: 700, fontSize: "13px", marginBottom: "8px", display: "flex", alignItems: "center", gap: "6px" }}>
                  <span style={{ display: "inline-block", width: "8px", height: "8px", borderRadius: "50%", background: "#2563eb" }} />
                  Gemini 2.5 Flash
                </div>
                <div style={{ fontSize: "12px", lineHeight: "1.8", color: "var(--text2)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}><span>Input (text / image / video)</span><span style={{ fontWeight: 600 }}>$0.30 /1M</span></div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}><span>Input (audio)</span><span style={{ fontWeight: 600 }}>$1.00 /1M</span></div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}><span>Output</span><span style={{ fontWeight: 600 }}>$2.50 /1M</span></div>
                  <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--text3)" }}>Output includes thinking tokens. Flat rate, no tier.</div>
                </div>
              </div>
            </div>
            <div style={{ marginTop: "10px", fontSize: "11px", color: "var(--text3)", lineHeight: "1.5" }}>
              Cost per call = (input tokens × rate + output tokens × rate) ÷ 1,000,000. Prices are Google Gemini API Tier-1 paid rates.
            </div>
          </div>
        )}
      </div>

      {/* ── Breakdown Tables (side by side) ── */}
      <div className="two-col" style={{ marginBottom: "20px" }}>
        {/* By Model */}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: "8px" }}>
            <span className="section-title" style={{ margin: 0 }}>By Model</span>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg2)" }}>
                <th style={TH_STYLE}>Model</th>
                <th style={{ ...TH_STYLE, textAlign: "right" }}>Calls</th>
                <th style={{ ...TH_STYLE, textAlign: "right" }}>Tokens</th>
                <th style={{ ...TH_STYLE, textAlign: "right" }}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {loadModel ? (
                <tr><td colSpan={4} style={{ padding: "30px", textAlign: "center", color: "var(--text3)" }}>Loading…</td></tr>
              ) : byModel.length === 0 ? (
                <tr><td colSpan={4} style={{ padding: "30px", textAlign: "center", color: "var(--text3)", fontSize: "13px" }}>No data yet</td></tr>
              ) : byModel.map((row) => (
                <tr key={row.model} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={TD_STYLE}>
                    <div style={{ fontWeight: 600, fontSize: "12px" }}>{row.model}</div>
                    <div style={{ fontSize: "11px", color: "var(--text3)", textTransform: "capitalize" }}>{row.provider}</div>
                  </td>
                  <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{row.calls.toLocaleString()}</td>
                  <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{fmtTokens(row.total_tokens)}</td>
                  <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>{fmtCost(row.total_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* By Feature */}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
            <span className="section-title" style={{ margin: 0 }}>By Feature</span>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--bg2)" }}>
                <th style={TH_STYLE}>Feature</th>
                <th style={{ ...TH_STYLE, textAlign: "right" }}>Calls</th>
                <th style={{ ...TH_STYLE, textAlign: "right" }}>Tokens</th>
                <th style={{ ...TH_STYLE, textAlign: "right" }}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {loadFeature ? (
                <tr><td colSpan={4} style={{ padding: "30px", textAlign: "center", color: "var(--text3)" }}>Loading…</td></tr>
              ) : byFeature.length === 0 ? (
                <tr><td colSpan={4} style={{ padding: "30px", textAlign: "center", color: "var(--text3)", fontSize: "13px" }}>No data yet</td></tr>
              ) : byFeature.map((row) => (
                <tr key={row.feature_name} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ ...TD_STYLE, fontWeight: 600, fontFamily: "monospace", fontSize: "12px" }}>{row.feature_name}</td>
                  <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{row.calls.toLocaleString()}</td>
                  <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{fmtTokens(row.total_tokens)}</td>
                  <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>{fmtCost(row.total_cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Raw Logs Table ── */}
      <div className="card" style={{ padding: 0, overflow: "hidden", marginBottom: "20px" }}>
        <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
          <div>
            <span className="section-title" style={{ margin: 0 }}>Recent Calls</span>
            <span style={{ fontSize: "11px", color: "var(--text3)", marginLeft: "10px" }}>
              Cost = (input tokens × in‑rate + output tokens × out‑rate) ÷ 1,000,000 — hover ⓘ on any row for full breakdown
            </span>
          </div>
          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <button
              disabled={logsPage === 1}
              onClick={() => setLogsPage((p) => Math.max(1, p - 1))}
              style={{
                padding: "4px 10px", fontSize: "12px", border: "1px solid var(--border)",
                borderRadius: "6px", background: "var(--white)", cursor: logsPage === 1 ? "not-allowed" : "pointer",
                color: logsPage === 1 ? "var(--text3)" : "var(--text)",
              }}
            >← Prev</button>
            <span style={{ fontSize: "12px", color: "var(--text3)" }}>Page {logsPage}</span>
            <button
              disabled={logs.length < 50}
              onClick={() => setLogsPage((p) => p + 1)}
              style={{
                padding: "4px 10px", fontSize: "12px", border: "1px solid var(--border)",
                borderRadius: "6px", background: "var(--white)", cursor: logs.length < 50 ? "not-allowed" : "pointer",
                color: logs.length < 50 ? "var(--text3)" : "var(--text)",
              }}
            >Next →</button>
          </div>
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: "var(--bg2)" }}>
              <th style={TH_STYLE}>Timestamp</th>
              <th style={TH_STYLE}>Feature</th>
              <th style={TH_STYLE}>Model</th>
              <th style={{ ...TH_STYLE, textAlign: "right" }}>In Tokens</th>
              <th style={{ ...TH_STYLE, textAlign: "right" }}>Out Tokens</th>
              <th style={{ ...TH_STYLE, textAlign: "right" }}>Cost / Call</th>
            </tr>
          </thead>
          <tbody>
            {loadLogs ? (
              <tr><td colSpan={6} style={{ padding: "40px", textAlign: "center", color: "var(--text3)" }}>Loading…</td></tr>
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ padding: "40px", textAlign: "center", color: "var(--text3)", fontSize: "13px" }}>
                  No LLM calls logged yet. Usage is recorded automatically when AI features are used.
                </td>
              </tr>
            ) : logs.map((row) => (
              <tr key={row.id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ ...TD_STYLE, fontSize: "12px", color: "var(--text2)", whiteSpace: "nowrap" }}>
                  {new Date(row.created_at).toLocaleString("en-US", {
                    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
                  })}
                </td>
                <td style={{ ...TD_STYLE, fontFamily: "monospace", fontSize: "12px" }}>{row.feature_name}</td>
                <td style={{ ...TD_STYLE, fontSize: "12px" }}>
                  <div style={{ fontWeight: 600 }}>{row.model}</div>
                  <div style={{ fontSize: "11px", color: "var(--text3)", textTransform: "capitalize" }}>{row.provider}</div>
                </td>
                <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums", fontSize: "12px" }}>{row.input_tokens.toLocaleString()}</td>
                <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums", fontSize: "12px" }}>{row.output_tokens.toLocaleString()}</td>
                {/* Cost cell with ⓘ tooltip */}
                <td style={{ ...TD_STYLE, textAlign: "right", fontVariantNumeric: "tabular-nums", fontSize: "12px", fontWeight: 600, position: "relative", whiteSpace: "nowrap" }}>
                  {fmtCost(row.cost_usd)}
                  <button
                    onMouseEnter={() => setTooltipRowId(row.id)}
                    onMouseLeave={() => setTooltipRowId(null)}
                    style={{
                      marginLeft: "6px", background: "none", border: "none", cursor: "pointer",
                      color: "var(--text3)", fontSize: "12px", padding: 0, lineHeight: 1,
                    }}
                    aria-label="Cost breakdown"
                  >ⓘ</button>
                  {tooltipRowId === row.id && (
                    <div style={{
                      position: "absolute", right: 0, bottom: "calc(100% + 6px)", zIndex: 50,
                      background: "var(--white)", border: "1px solid var(--border)", borderRadius: "8px",
                      padding: "12px 14px", minWidth: "230px", boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
                      fontSize: "12px", textAlign: "left", lineHeight: "1.7", whiteSpace: "nowrap",
                    }}>
                      <div style={{ fontWeight: 700, marginBottom: "6px", borderBottom: "1px solid var(--border)", paddingBottom: "6px" }}>Cost breakdown</div>
                      <div style={{ display: "grid", gridTemplateColumns: "auto auto", gap: "2px 12px" }}>
                        <span style={{ color: "var(--text3)" }}>In rate</span>
                        <span style={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}>${row.input_rate_per_1m ?? "?"}/1M</span>
                        <span style={{ color: "var(--text3)" }}>Out rate</span>
                        <span style={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}>${row.output_rate_per_1m ?? "?"}/1M</span>
                        <span style={{ color: "var(--text3)" }}>In cost</span>
                        <span style={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{fmtCost(row.input_cost_usd ?? 0)}</span>
                        <span style={{ color: "var(--text3)" }}>Out cost</span>
                        <span style={{ fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{fmtCost(row.output_cost_usd ?? 0)}</span>
                        <span style={{ fontWeight: 700 }}>Total</span>
                        <span style={{ fontWeight: 700, fontVariantNumeric: "tabular-nums", textAlign: "right" }}>{fmtCost(row.cost_usd)}</span>
                      </div>
                      {row.tier_note && (
                        <div style={{ marginTop: "8px", paddingTop: "6px", borderTop: "1px solid var(--border)", fontSize: "11px", color: "var(--text3)", whiteSpace: "normal", maxWidth: "220px" }}>
                          {row.tier_note}
                        </div>
                      )}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
