"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { usersApi } from "@/lib/api";
import { toast } from "sonner";
import { Users, UserPlus, Upload, Trash2, Loader2, Save, Check, X } from "lucide-react";

interface User {
  id: string;
  email: string;
  full_name: string;
  title: string | null;
  organization: string | null;
  role: string;
  is_active: boolean;
}

export default function UsersPage() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<"all" | "add">("all");
  const [pendingRoles, setPendingRoles] = useState<Record<string, string>>({});

  const { data: users, isLoading } = useQuery<User[]>({
    queryKey: ["users"],
    queryFn: () => usersApi.list().then((res) => res.data),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => usersApi.delete(id),
    onSuccess: () => {
      toast.success("User deleted successfully.");
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e: any) => {
      const detail = e.response?.data?.detail;
      const message = typeof detail === "string" 
        ? detail 
        : (detail && JSON.stringify(detail)) || "Delete failed";
      toast.error(message);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => usersApi.update(id, data),
    onSuccess: (_, variables) => {
      toast.success("User updated successfully.");
      setPendingRoles((prev) => {
        const next = { ...prev };
        delete next[variables.id];
        return next;
      });
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e: any) => {
      const detail = e.response?.data?.detail;
      const message = typeof detail === "string" 
        ? detail 
        : (detail && JSON.stringify(detail)) || "Update failed";
      toast.error(message);
    },
  });

  const [addForm, setAddForm] = useState({
    full_name: "",
    title: "",
    email: "",
    organization: "",
      role: "legal_team",
      password: "law@4321",
    });

  const createMutation = useMutation({
    mutationFn: (data: any) => usersApi.create(data),
    onSuccess: () => {
      toast.success("User created successfully.");
      qc.invalidateQueries({ queryKey: ["users"] });
      setActiveTab("all");
      setAddForm({
        full_name: "",
        title: "",
        email: "",
        organization: "",
        role: "legal_team",
        password: "law@4321",
      });
    },
    onError: (e: any) => {
      const detail = e.response?.data?.detail;
      const message = typeof detail === "string" 
        ? detail 
        : (detail && JSON.stringify(detail)) || "Create failed";
      toast.error(message);
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    createMutation.mutate(addForm);
  };

  const handleRoleChange = (id: string, newRole: string) => {
    updateMutation.mutate({ id, data: { role: newRole } });
  };

  const setPendingRole = (id: string, role: string) => {
    setPendingRoles((prev) => ({ ...prev, [id]: role }));
  };

  const cancelPendingRole = (id: string) => {
    setPendingRoles((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  return (
    <div className="fade-in">
      <div style={{ marginBottom: "24px" }}>
        <h1 style={{ fontSize: "24px", fontWeight: "bold", margin: "0 0 8px 0" }}>User Management</h1>
        <p style={{ color: "var(--text3)", margin: 0 }}>Create, view, and manage all system users.</p>
      </div>

      <div style={{ display: "flex", gap: "10px", marginBottom: "24px" }}>
        <button
          onClick={() => setActiveTab("all")}
          style={{
            padding: "8px 16px",
            borderRadius: "6px",
            border: "1px solid var(--border)",
            background: activeTab === "all" ? "var(--accent)" : "transparent",
            color: activeTab === "all" ? "white" : "var(--text1)",
            display: "flex",
            alignItems: "center",
            gap: "8px",
            cursor: "pointer",
            fontWeight: 500,
          }}
        >
          <Users size={16} /> All Users
        </button>
        <button
          onClick={() => setActiveTab("add")}
          style={{
            padding: "8px 16px",
            borderRadius: "6px",
            border: "1px solid var(--border)",
            background: activeTab === "add" ? "var(--accent)" : "transparent",
            color: activeTab === "add" ? "white" : "var(--text1)",
            display: "flex",
            alignItems: "center",
            gap: "8px",
            cursor: "pointer",
            fontWeight: 500,
          }}
        >
          <UserPlus size={16} /> Add User
        </button>
      </div>

      {activeTab === "all" && (
        <div style={{ background: "var(--bg2)", borderRadius: "12px", border: "1px solid var(--border)", overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left" }}>
            <thead>
              <tr style={{ background: "var(--bg3)", borderBottom: "1px solid var(--border)" }}>
                <th style={{ padding: "16px", fontWeight: 600, color: "var(--text2)", fontSize: "14px" }}>Name</th>
                <th style={{ padding: "16px", fontWeight: 600, color: "var(--text2)", fontSize: "14px" }}>Title</th>
                <th style={{ padding: "16px", fontWeight: 600, color: "var(--text2)", fontSize: "14px" }}>Email</th>
                <th style={{ padding: "16px", fontWeight: 600, color: "var(--text2)", fontSize: "14px" }}>Organization</th>
                <th style={{ padding: "16px", fontWeight: 600, color: "var(--text2)", fontSize: "14px" }}>Role</th>
                <th style={{ padding: "16px", fontWeight: 600, color: "var(--text2)", fontSize: "14px", textAlign: "center" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", padding: "32px" }}>
                    <Loader2 size={24} className="spin" style={{ margin: "auto" }} />
                  </td>
                </tr>
              ) : users?.length === 0 ? (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", padding: "32px", color: "var(--text3)" }}>
                    No users found
                  </td>
                </tr>
              ) : (
                users?.map((u) => (
                  <tr key={u.id} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "16px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                        <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "var(--bg3)", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: "bold", color: "var(--accent)" }}>
                          {u.full_name.charAt(0).toUpperCase()}
                        </div>
                        <span style={{ fontWeight: 500, color: "var(--text1)" }}>{u.full_name}</span>
                      </div>
                    </td>
                    <td style={{ padding: "16px", color: "var(--text2)" }}>{u.title || "—"}</td>
                    <td style={{ padding: "16px", color: "var(--text2)" }}>{u.email}</td>
                    <td style={{ padding: "16px", color: "var(--text2)" }}>{u.organization || "—"}</td>
                    <td style={{ padding: "16px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <select
                          value={pendingRoles[u.id] || u.role}
                          onChange={(e) => setPendingRole(u.id, e.target.value)}
                          style={{
                            padding: "6px 12px",
                            borderRadius: "4px",
                            border: "1px solid var(--border)",
                            background: "var(--bg1)",
                            color: "var(--text1)",
                            cursor: "pointer",
                          }}
                        >
                          <option value="legal_team">Counsel</option>
                          <option value="reviewer">Reviewer</option>
                          <option value="ops_admin">Admin</option>
                        </select>
                        {pendingRoles[u.id] && pendingRoles[u.id] !== u.role && (
                          <div style={{ display: "flex", gap: "4px" }}>
                            <button
                              onClick={() => handleRoleChange(u.id, pendingRoles[u.id])}
                              disabled={updateMutation.isPending}
                              title="Confirm Role Change"
                              style={{
                                background: "var(--green-bg, #dcfce7)",
                                color: "var(--green, #16a34a)",
                                border: "none",
                                borderRadius: "4px",
                                padding: "4px",
                                cursor: "pointer",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center"
                              }}
                            >
                              {updateMutation.isPending ? <Loader2 size={14} className="spin" /> : <Check size={14} />}
                            </button>
                            <button
                              onClick={() => cancelPendingRole(u.id)}
                              title="Cancel"
                              style={{
                                background: "var(--bg3)",
                                color: "var(--text3)",
                                border: "none",
                                borderRadius: "4px",
                                padding: "4px",
                                cursor: "pointer",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center"
                              }}
                            >
                              <X size={14} />
                            </button>
                          </div>
                        )}
                      </div>
                    </td>
                    <td style={{ padding: "16px", textAlign: "center" }}>
                      <button
                        onClick={() => {
                          if (confirm("Are you sure you want to delete this user?")) {
                            deleteMutation.mutate(u.id);
                          }
                        }}
                        style={{ background: "#fee2e2", color: "#ef4444", border: "none", borderRadius: "4px", padding: "6px", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}
                      >
                        <Trash2 size={16} />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {activeTab === "add" && (
        <div style={{ background: "var(--bg2)", borderRadius: "12px", border: "1px solid var(--border)", padding: "24px", maxWidth: "600px" }}>
          <h2 style={{ fontSize: "18px", margin: "0 0 8px 0", display: "flex", alignItems: "center", gap: "8px", color: "var(--accent)" }}>
            <UserPlus size={20} /> Add New User
          </h2>
          <p style={{ color: "var(--text3)", margin: "0 0 24px 0", fontSize: "14px" }}>
            Fill in the details below. Default password will be "law@4321".
          </p>

          <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            <div>
              <label style={{ display: "block", marginBottom: "6px", fontWeight: 500, fontSize: "14px" }}>
                Name <span style={{ color: "#ef4444" }}>*</span>
              </label>
              <input
                type="text"
                placeholder="Full name"
                required
                value={addForm.full_name}
                onChange={(e) => setAddForm({ ...addForm, full_name: e.target.value })}
                style={{ width: "100%", padding: "10px", borderRadius: "6px", border: "1px solid var(--border)", background: "var(--bg1)", color: "var(--text1)" }}
              />
            </div>
            
            <div>
              <label style={{ display: "block", marginBottom: "6px", fontWeight: 500, fontSize: "14px" }}>
                Title
              </label>
              <input
                type="text"
                placeholder="e.g. Engineer, Manager"
                value={addForm.title}
                onChange={(e) => setAddForm({ ...addForm, title: e.target.value })}
                style={{ width: "100%", padding: "10px", borderRadius: "6px", border: "1px solid var(--border)", background: "var(--bg1)", color: "var(--text1)" }}
              />
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "6px", fontWeight: 500, fontSize: "14px" }}>
                Email <span style={{ color: "#ef4444" }}>*</span>
              </label>
              <input
                type="email"
                placeholder="user@company.com"
                required
                value={addForm.email}
                onChange={(e) => setAddForm({ ...addForm, email: e.target.value })}
                style={{ width: "100%", padding: "10px", borderRadius: "6px", border: "1px solid var(--border)", background: "var(--bg1)", color: "var(--text1)" }}
              />
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "6px", fontWeight: 500, fontSize: "14px" }}>
                Organization
              </label>
              <input
                type="text"
                placeholder="e.g. Simon, Sugar"
                value={addForm.organization}
                onChange={(e) => setAddForm({ ...addForm, organization: e.target.value })}
                style={{ width: "100%", padding: "10px", borderRadius: "6px", border: "1px solid var(--border)", background: "var(--bg1)", color: "var(--text1)" }}
              />
            </div>

            <div>
              <label style={{ display: "block", marginBottom: "6px", fontWeight: 500, fontSize: "14px" }}>
                Role <span style={{ color: "#ef4444" }}>*</span>
              </label>
              <select
                value={addForm.role}
                onChange={(e) => setAddForm({ ...addForm, role: e.target.value })}
                style={{ width: "100%", padding: "10px", borderRadius: "6px", border: "1px solid var(--border)", background: "var(--bg1)", color: "var(--text1)" }}
              >
                <option value="legal_team">Counsel</option>
                <option value="reviewer">Reviewer</option>
                <option value="ops_admin">Admin</option>
              </select>
            </div>

            <div style={{ marginTop: "16px", borderTop: "1px solid var(--border)", paddingTop: "24px" }}>
              <button
                type="submit"
                disabled={createMutation.isPending}
                style={{
                  width: "100%",
                  padding: "12px",
                  borderRadius: "6px",
                  background: "var(--accent)",
                  color: "white",
                  border: "none",
                  fontWeight: 600,
                  fontSize: "14px",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "8px",
                }}
              >
                {createMutation.isPending ? <Loader2 className="spin" size={18} /> : <UserPlus size={18} />}
                Create User
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
