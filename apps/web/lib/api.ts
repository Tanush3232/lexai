// Axios API client with auth token injection
import axios from "axios";
import { useAuthStore } from "@/lib/stores/auth-store";

export const api = axios.create({
  timeout: 150000, // 2.5 min — vectorless path takes ~70-90s, ReAct loop at most ~120s
});

// Resolve baseURL lazily at request time (avoids SSR/client hydration mismatch)
api.interceptors.request.use((config) => {
  if (!config.baseURL) {
    const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

    // Production: NEXT_PUBLIC_API_URL="/api" (relative) → baseURL = "/api/v1"
    // Local dev:  NEXT_PUBLIC_API_URL="http://localhost:8000" → baseURL = "http://localhost:8000/api/v1"
    // This prevents the /api/api/v1 double-prefix bug when behind Nginx.
    if (base.startsWith("/")) {
      // Relative path (production behind Nginx) — just append /v1
      config.baseURL = `${base}/v1`;
    } else {
      // Absolute URL (local dev) — append /api/v1 as before
      config.baseURL = `${base}/api/v1`;
    }
  }
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});


// Handle 401 globally
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout();
      // Only redirect if we are NOT already on the login page
      // This prevents the page from refreshing when a login attempt fails
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// Auth
export const authApi = {
  login: (email: string, password: string) => {
    const form = new URLSearchParams();
    form.append("username", email.trim());
    form.append("password", password);
    return api.post("/auth/token", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
  },
  register: (data: { email: string; full_name: string; password: string; role?: string }) =>
    api.post("/auth/register", data),
  me: () => api.get("/auth/me"),
  /** Get the Microsoft OAuth2 redirect URL from the backend.
   *  Pass current window.location.origin so backend picks correct redirect_uri. */
  getMicrosoftLoginUrl: (origin: string) =>
    api.get<{ url: string }>("/auth/microsoft/login", { params: { origin } }),
  /** Exchange the code+state received from Microsoft for a LexAI JWT */
  microsoftCallback: (code: string, state?: string) =>
    api.get<{ access_token: string; token_type: string; user: any }>(
      "/auth/microsoft/callback",
      { params: { code, state } }
    ),
};

// Folders
export const foldersApi = {
  list: () => api.get("/folders/"),
  create: (data: { name: string; description?: string }) => api.post("/folders/", data),
  get: (id: string) => api.get(`/folders/${id}`),
  delete: (id: string) => api.delete(`/folders/${id}`),
};

// Documents
export const documentsApi = {
  upload: (folderId: string, file: File, onProgress?: (p: number) => void) => {
    const form = new FormData();
    form.append("folder_id", folderId);
    form.append("file", file);
    return api.post("/documents/upload", form, {
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded * 100) / e.total));
        }
      },
    });
  },
  listByFolder: (folderId: string) => api.get(`/documents/folder/${folderId}`),
  get: (id: string) => api.get(`/documents/${id}`),
  download: (id: string) => api.get(`/documents/${id}/download`, { responseType: "blob" }),
  delete: (id: string) => api.delete(`/documents/${id}`),
  /** Page-index tree + AI summary (used by Document Viewer Modal) */
  getTree: (id: string) => api.get(`/documents/${id}/tree`),
  /** Formatted text content from Postgres clauses (used by Document Viewer center panel) */
  getTextContent: (id: string) => api.get(`/documents/${id}/text-content`),
  /** Direct MinIO public URL for the original uploaded file */
  getFileUrl: (id: string) => api.get(`/documents/${id}/file-url`),
};

// Chat
export const chatApi = {
  createSession: (data: {
    title?: string;
    scope_type: string;
    scope_folder_ids: string[];
    scope_document_ids: string[];
  }) => api.post("/chat/sessions", data),
  listSessions: () => api.get("/chat/sessions"),
  sendMessage: (sessionId: string, content: string) =>
    api.post(`/chat/sessions/${sessionId}/messages`, { content }),
  getMessages: (sessionId: string) => api.get(`/chat/sessions/${sessionId}/messages`),
  improvePrompt: (content: string) => api.post("/chat/improve", { content }),
};

// Drafts — Legacy (still used for viewing existing drafts)
export const draftsApi = {
  create: (data: { contract_type: string; title?: string; inputs: any }) => api.post("/drafts/", data),
  list: () => api.get("/drafts/"),
  get: (id: string) => api.get(`/drafts/${id}`),
  update: (id: string, data: any) => api.patch(`/drafts/${id}`, data),
  delete: (id: string) => api.delete(`/drafts/${id}`),
  approve: (id: string) => api.post(`/drafts/${id}/approve`),
  exportDocx: (id: string) =>
    api.post(`/drafts/${id}/export`, {}, { responseType: "blob" }),
  /** Insert auto-fixed clause content for a specific adversarial finding */
  autoInsertFix: (draftId: string, blockId: string, finding: AdversarialFinding) =>
    api.post(`/drafts/${draftId}/auto-insert-fix`, { finding, block_id: blockId }),
};



// ─── Enterprise Agentic Drafting API ─────────────────────────────────────────

export interface DraftEvent {
  type:
    | "session_created"
    | "stage"
    | "intent_analyzed"
    | "needs_input"
    | "redaction_complete"
    | "research_update"
    | "sources_ready"
    | "resuming"
    | "assembly_started"
    | "red_team_complete"
    | "draft_ready"
    | "saved"
    | "error";
  timestamp?: string;
  session_id?: string;
  stage?: string;
  label?: string;
  progress?: number;
  intent?: DraftIntent;
  questions?: DraftQuestion[];
  sources?: DraftSource[];
  all_sources?: DraftSource[];
  ranking_summary?: string;
  sufficient?: boolean;
  blocks?: ContractBlock[];
  html_content?: string;
  issues?: DraftIssue[];
  defined_terms?: { term: string; definition: string }[];
  missing_clauses?: string[];
  title?: string;
  contract_type?: string;
  draft_id?: string;
  agent?: string;
  count?: number;
  tokens_masked?: number;
  message?: string;
  approved_count?: number;
  // Adversarial red-team fields
  findings?: AdversarialFinding[];
  overall_risk?: string;
  overall_assessment?: string;
  missing_sections?: string[];
  finding_count?: number;
  critical_count?: number;
  adversarial_findings?: AdversarialFinding[];
  adversarial_risk?: string;
  adversarial_assessment?: string;
  adversarial_missing_sections?: string[];
}

export interface DraftIntent {
  contract_type: string;
  contract_type_label: string;
  parties: { name: string | null; role: string }[];
  jurisdiction: string;
  purpose: string;
  risk_level: "low" | "medium" | "high";
  governing_law: string;
  can_proceed: boolean;
}

export interface DraftQuestion {
  id: string;
  question: string;
  type: "text" | "date" | "number" | "textarea" | "select";
  options?: string[];
}

export interface DraftSource {
  source_id: string;
  source_name: string;
  url: string;
  domain: string;
  snippet: string;
  source_type: "statute" | "case_law" | "regulation" | "template" | "precedent";
  jurisdiction: string;
  relevance_note?: string;
  relevance_score?: number;
  authority_score?: number;
  combined_score?: number;
  include?: boolean;
  reason?: string;
  key_provisions?: string[];
  clause_type?: string;
}

export interface ContractBlock {
  block_id: string;
  clause_number: string;
  clause_type: string;
  heading: string;
  content: string;
  is_locked: boolean;
  provenance: "template" | "precedent" | "generated";
  precedent_source?: string;
  needs_review: boolean;
  review_reason?: string;
  variable_slots_filled?: Record<string, string>;
}

export interface DraftIssue {
  severity: "high" | "medium" | "low";
  description: string;
  clause_affected?: string;
}

export interface AdversarialFinding {
  block_id: string;
  clause_ref: string;
  problematic_text: string;
  exploit: string;
  severity: "critical" | "high" | "medium";
  category: string;
  suggested_fix: string;
}

export const draftSessionsApi = {
  /**
   * Start a new agentic drafting session (SSE stream).
   * Returns abort function.
   */
  startSession: (
    prompt: string,
    onEvent: (event: DraftEvent) => void,
    onError?: (err: Error) => void,
    onDone?: () => void
  ): (() => void) => {
    const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const baseUrl = base.startsWith("/") ? `${base}/v1` : `${base}/api/v1`;
    const token = useAuthStore.getState().token || "";
    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${baseUrl}/drafts/sessions`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ prompt }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`Session start failed: ${res.status}`);
        await _consumeSSE(res, onEvent);
        onDone?.();
      } catch (err: any) {
        if (err.name !== "AbortError")
          onError?.(err instanceof Error ? err : new Error(String(err)));
      }
    })();

    return () => controller.abort();
  },

  /**
   * Submit follow-up answers and resume orchestration (SSE stream).
   */
  submitContext: (
    sessionId: string,
    answers: Record<string, string>,
    onEvent: (event: DraftEvent) => void,
    onError?: (err: Error) => void,
    onDone?: () => void
  ): (() => void) => {
    const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const baseUrl = base.startsWith("/") ? `${base}/v1` : `${base}/api/v1`;
    const token = useAuthStore.getState().token || "";
    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${baseUrl}/drafts/sessions/${sessionId}/context`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ answers }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`Context submit failed: ${res.status}`);
        await _consumeSSE(res, onEvent);
        onDone?.();
      } catch (err: any) {
        if (err.name !== "AbortError")
          onError?.(err instanceof Error ? err : new Error(String(err)));
      }
    })();

    return () => controller.abort();
  },

  /**
   * Approve selected sources and trigger Claude Opus assembly (SSE stream).
   */
  approveSources: (
    sessionId: string,
    approvedSourceIds: string[],
    onEvent: (event: DraftEvent) => void,
    onError?: (err: Error) => void,
    onDone?: () => void
  ): (() => void) => {
    const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const baseUrl = base.startsWith("/") ? `${base}/v1` : `${base}/api/v1`;
    const token = useAuthStore.getState().token || "";
    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${baseUrl}/drafts/sessions/${sessionId}/approve-sources`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ approved_source_ids: approvedSourceIds }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`Source approval failed: ${res.status}`);
        await _consumeSSE(res, onEvent);
        onDone?.();
      } catch (err: any) {
        if (err.name !== "AbortError")
          onError?.(err instanceof Error ? err : new Error(String(err)));
      }
    })();

    return () => controller.abort();
  },

  getSession: (sessionId: string) => api.get(`/drafts/sessions/${sessionId}`),
  listSessions: () => api.get("/drafts/sessions"),
};

async function _consumeSSE(response: Response, onEvent: (e: DraftEvent) => void) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        try {
          onEvent(JSON.parse(line.slice(6)));
        } catch {
          // skip malformed chunk
        }
      }
    }
  }
}


// Translations
export const translationsApi = {
  start: (documentId: string, targetLanguage: string) =>
    api.post("/translations/", { document_id: documentId, target_language: targetLanguage }),
  get: (jobId: string) => api.get(`/translations/${jobId}`),
  listByDocument: (documentId: string) => api.get(`/translations/document/${documentId}`),
  save: (jobId: string) => api.post(`/translations/${jobId}/save`),
  cancel: (jobId: string) => api.post(`/translations/${jobId}/cancel`),
  cancelAll: () => api.post(`/translations/action/cancel-all`),
};

// Audit
export const auditApi = {
  list: (resourceType?: string) =>
    api.get("/audit/", { params: resourceType ? { resource_type: resourceType } : {} }),
};

// Usage Analytics (superadmin only)
export const usageApi = {
  summary: () => api.get("/usage/summary"),
  byModel: () => api.get("/usage/by-model"),
  byFeature: () => api.get("/usage/by-feature"),
  logs: (page = 1, limit = 50, featureName?: string, model?: string) =>
    api.get("/usage/logs", {
      params: { page, limit, ...(featureName ? { feature_name: featureName } : {}), ...(model ? { model } : {}) },
    }),
  recalculateCosts: () => api.post("/usage/recalculate-costs"),
};

// Legal Acts
export const actsApi = {
  list: (params?: { page?: number; limit?: number; status?: string; review?: string; search?: string }) =>
    api.get("/acts/", { params }),
  get: (id: string) => api.get(`/acts/${id}`),
  getStatus: (id: string) => api.get(`/acts/${id}/status`),
  getPdfUrl: (id: string) => api.get(`/acts/${id}/pdf-url`),
  search: (q: string) => api.get("/acts/search", { params: { q } }),
  seed: () => api.post("/acts/seed"),
  add: (actName: string) => api.post("/acts/add", { act_name: actName }),
  confirm: (data: { handle_id: string; title: string; act_number?: string; enactment_date?: string }) =>
    api.post<{ id: string; status: string; already_exists: boolean; message?: string }>("/acts/confirm", data),
  uploadPdf: (actId: string, file: File, onProgress?: (p: number) => void) => {
    const form = new FormData();
    form.append("file", file);
    return api.post(`/acts/${actId}/upload-pdf`, form, {
      timeout: 300000,
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded * 100) / e.total));
      },
    });
  },
  removePdf: (actId: string) => api.delete(`/acts/${actId}/pdf`),
  updateReview: (actId: string, data: { review_status: string | null; lock?: boolean }) =>
    api.patch(`/acts/${actId}/review`, data),
  stats: () => api.get("/acts/stats/summary"),
  delete: (actId: string) => api.delete(`/acts/${actId}`),
};

// Users
export const usersApi = {
  list: () => api.get("/users/"),
  create: (data: any) => api.post("/users/", data),
  update: (id: string, data: any) => api.put(`/users/${id}`, data),
  delete: (id: string) => api.delete(`/users/${id}`),
};

// Web Search
export type SearchMode = "fast" | "pro" | "deep";

export interface SearchEvent {
  type: "session_created" | "thinking_step" | "complete" | "error";
  session_id?: string;
  step?: string;
  detail?: string;
  timestamp?: string;
  answer?: string;
  citations?: Citation[];
  reasoning_steps?: ReasoningStep[];
  search_plan?: Record<string, unknown>;
  message?: string;
  read_but_not_used?: Citation[];
}

export interface Citation {
  id: string;
  source_name: string;
  url: string;
  snippet?: string;
  domain: string;
  relevance_score?: number;
  jurisdiction?: string;
  citation_type?: string;
  turn_index?: number;
}

export interface ReasoningStep {
  step: string;
  detail?: string;
  timestamp?: string;
}

export interface SearchSession {
  id: string;
  query: string;
  mode: string;
  status: string;
  answer_summary?: string;
  full_answer?: string;
  reasoning_steps?: ReasoningStep[];
  search_plan?: Record<string, unknown>;
  citations?: Citation[];
  error_message?: string;
  created_at: string;
}

export const webSearchApi = {
  generatePlan: (query: string) => api.post("/web-search/plan", { query, mode: "deep" }),
  
  /**
   * Start a legal web search and consume the SSE stream.
   * Returns a cleanup function to abort the fetch.
   */
  startSearch: (
    query: string,
    mode: SearchMode,
    plan: string[] | undefined,
    sessionId: string | undefined,
    onEvent: (event: SearchEvent) => void,
    onError?: (err: Error) => void,
    onDone?: () => void
  ): (() => void) => {
    const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const baseUrl = base.startsWith("/") ? `${base}/v1` : `${base}/api/v1`;
    const authToken = useAuthStore.getState().token || "";

    const controller = new AbortController();

    (async () => {
      try {
        const bodyData: any = { query, mode };
        if (plan && plan.length > 0) bodyData.plan = plan;
        if (sessionId) bodyData.session_id = sessionId;

        const response = await fetch(`${baseUrl}/web-search/search`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${authToken}`,
          },
          body: JSON.stringify(bodyData),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Search request failed: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const event: SearchEvent = JSON.parse(line.slice(6));
                onEvent(event);
              } catch {
                // malformed chunk — skip
              }
            }
          }
        }
        onDone?.();
      } catch (err: any) {
        if (err.name !== "AbortError") {
          onError?.(err instanceof Error ? err : new Error(String(err)));
        }
      }
    })();

    return () => controller.abort();
  },

  listSessions: (page = 1, limit = 20) =>
    api.get("/web-search/sessions", { params: { page, limit } }),

  getSession: (sessionId: string) =>
    api.get<SearchSession>(`/web-search/sessions/${sessionId}`),

  deleteSession: (sessionId: string) =>
    api.delete(`/web-search/sessions/${sessionId}`),
};
