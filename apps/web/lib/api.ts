// Axios API client with auth token injection
import axios from "axios";
import { useAuthStore } from "@/lib/stores/auth-store";

export const api = axios.create({
  timeout: 150000, // 2.5 min — vectorless path takes ~70-90s, ReAct loop at most ~120s
});

// Resolve baseURL lazily at request time (avoids SSR/client hydration mismatch)
api.interceptors.request.use((config) => {
  if (!config.baseURL) {
    // 1. Get base from env var (or fallback to localhost)
    let base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    
    // 2. If we are in the browser, and the configured base is "localhost", 
    // but the user is accessing the app via a LAN IP (e.g. 192.168.X.X),
    // dynamically override localhost with their actual hostname.
    if (typeof window !== "undefined" && base.includes("localhost")) {
      base = `${window.location.protocol}//${window.location.hostname}:8000`;
    }
    
    config.baseURL = `${base}/api/v1`;
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

// Drafts
export const draftsApi = {
  create: (data: { contract_type: string; title: string; inputs: Record<string, unknown> }) =>
    api.post("/drafts/", data),
  list: () => api.get("/drafts/"),
  get: (id: string) => api.get(`/drafts/${id}`),
  update: (id: string, data: { content?: string; title?: string }) =>
    api.patch(`/drafts/${id}`, data),
  approve: (id: string) => api.post(`/drafts/${id}/approve`),
};

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
};

// Users
export const usersApi = {
  list: () => api.get("/users/"),
  create: (data: any) => api.post("/users/", data),
  update: (id: string, data: any) => api.put(`/users/${id}`, data),
  delete: (id: string) => api.delete(`/users/${id}`),
};
