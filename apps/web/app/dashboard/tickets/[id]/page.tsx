"use client";

import { useEffect, useState, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/stores/auth-store";
import { api } from "@/lib/api";

type Participant = { id: string; email: string; full_name: string; role: string };
type Message = { id: string; content: string; source: string; timestamp: string; sender_name: string; sender_id: string };
type TicketDetails = {
  ticket: { id: string; title: string; status: string; priority: string; created_at: string };
  participants: Participant[];
  messages: Message[];
  attachments: { id: string; file_name: string; file_url: string; entity?: string; created_at: string }[];
};

export default function TicketDetailsPage() {
  const { id } = useParams();
  const router = useRouter();
  const { user } = useAuthStore();
  const [data, setData] = useState<TicketDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [newMessage, setNewMessage] = useState("");
  const [mentionableUsers, setMentionableUsers] = useState<{id: string, full_name: string, email: string}[]>([]);
  const [showMentionMenu, setShowMentionMenu] = useState(false);
  const [mentionFilter, setMentionFilter] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);

  const fetchTicket = async () => {
    try {
      const res = await api.get(`/tickets/${id}`);
      setData(res.data);
    } catch (err: any) {
      if (err.response?.status === 403) {
        alert("Access Denied.");
        router.push("/dashboard/tickets");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTicket();
    // Poll every 5s for realtime feel
    const interval = setInterval(fetchTicket, 5000);
    return () => clearInterval(interval);
  }, [id]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [data?.messages]);

  const loadMentionableUsers = async () => {
    const res = await api.get(`/tickets/${id}/mentionable_users`);
    setMentionableUsers(res.data);
  };

  const handleMessageChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setNewMessage(val);
    
    // Simple mention logic
    const lastWord = val.split(" ").pop();
    if (lastWord?.startsWith("@")) {
      setMentionFilter(lastWord.slice(1).toLowerCase());
      setShowMentionMenu(true);
      if (mentionableUsers.length === 0) loadMentionableUsers();
    } else {
      setShowMentionMenu(false);
    }
  };

  const insertMention = async (userId: string, userName: string) => {
    try {
      await api.post(`/tickets/${id}/mentions`, { user_id: userId });
      const words = newMessage.split(" ");
      words.pop();
      setNewMessage(words.join(" ") + ` @${userName} `);
      setShowMentionMenu(false);
    } catch (err: any) {
      alert(err.response?.data?.detail || "Cannot mention this user.");
      setShowMentionMenu(false);
    }
  };

  const sendMessage = async () => {
    if (!newMessage.trim()) return;
    const content = newMessage;
    setNewMessage("");
    // Optimistic update
    if (data && user) {
      setData({
        ...data,
        messages: [...data.messages, { id: "temp", content, source: "app", timestamp: new Date().toISOString(), sender_id: user.id, sender_name: user.full_name }]
      });
    }
    await api.post(`/tickets/${id}/messages`, { content });
    fetchTicket();
  };

  const handleAssign = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const userId = e.target.value;
    if (!userId) return;
    try {
      await api.post(`/tickets/${id}/assign`, { user_id: userId });
      fetchTicket();
    } catch (err: any) {
      alert(err.response?.data?.detail || "Failed to assign.");
    }
    e.target.value = "";
  };

  if (loading || !data || !user) return <div style={{ padding: "40px", textAlign: "center", color: "#64748b" }}>Loading ticket...</div>;

  const isAdmin = ["ops_admin", "super_admin", "reviewer"].includes(user.role);
  const filteredMentions = mentionableUsers.filter(u => u.full_name.toLowerCase().includes(mentionFilter));

  return (
    <div style={{ display: "flex", height: "calc(100vh - 80px)", gap: "20px", padding: "20px", background: "#f8fafc" }}>
      
      {/* LEFT: Metadata */}
      <div style={{ width: "350px", background: "white", borderRadius: "12px", border: "1px solid #e2e8f0", padding: "24px", display: "flex", flexDirection: "column", gap: "24px", overflowY: "auto" }}>
        <div>
          <div style={{ fontSize: "12px", fontWeight: 600, color: "#64748b", textTransform: "uppercase", marginBottom: "8px" }}>Ticket Info</div>
          <h2 style={{ margin: "0 0 12px 0", fontSize: "18px", color: "#0f172a" }}>{data.ticket.title}</h2>
          <div style={{ display: "flex", gap: "8px" }}>
            <span style={{ padding: "4px 8px", borderRadius: "4px", fontSize: "12px", fontWeight: "500", backgroundColor: data.ticket.priority === "high" || data.ticket.priority === "urgent" ? "#fee2e2" : "#f1f5f9", color: data.ticket.priority === "high" || data.ticket.priority === "urgent" ? "#ef4444" : "#64748b" }}>
              {data.ticket.priority.toUpperCase()}
            </span>
            <span style={{ padding: "4px 8px", borderRadius: "4px", fontSize: "12px", fontWeight: "500", backgroundColor: data.ticket.status === "open" ? "#dcfce7" : data.ticket.status === "in_progress" ? "#fef9c3" : "#f1f5f9", color: data.ticket.status === "open" ? "#166534" : data.ticket.status === "in_progress" ? "#854d0e" : "#475569" }}>
              {data.ticket.status.toUpperCase()}
            </span>
          </div>
        </div>

        {isAdmin && (
          <div>
            <div style={{ fontSize: "12px", fontWeight: 600, color: "#64748b", textTransform: "uppercase", marginBottom: "8px" }}>Assign User</div>
            <select onChange={handleAssign} style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1" }} onClick={loadMentionableUsers}>
              <option value="">-- Select user to assign --</option>
              {mentionableUsers.map(u => <option key={u.id} value={u.id}>{u.full_name} ({u.email})</option>)}
            </select>
          </div>
        )}

        <div>
          <div style={{ fontSize: "12px", fontWeight: 600, color: "#64748b", textTransform: "uppercase", marginBottom: "8px" }}>Participants ({data.participants.length})</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {data.participants.map(p => (
              <div key={p.id} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "8px", borderRadius: "6px", backgroundColor: "#f1f5f9" }}>
                <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "#3b82f6", color: "white", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "14px", fontWeight: "bold" }}>
                  {p.full_name.charAt(0).toUpperCase()}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "14px", fontWeight: "500", color: "#0f172a", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{p.full_name}</div>
                  <div style={{ fontSize: "12px", color: "#64748b" }}>{p.role}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {data.attachments && data.attachments.length > 0 && (
          <div>
            <div style={{ fontSize: "12px", fontWeight: 600, color: "#64748b", textTransform: "uppercase", marginBottom: "8px" }}>Attachments ({data.attachments.length})</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {data.attachments.map(att => (
                <a key={att.id} href={att.file_url} target="_blank" rel="noopener noreferrer" style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px", borderRadius: "6px", border: "1px solid #e2e8f0", textDecoration: "none", color: "#0f172a", backgroundColor: "#f8fafc" }}>
                  <span style={{ fontSize: "16px" }}>📎</span>
                  <span style={{ fontSize: "13px", fontWeight: "500", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{att.file_name}</span>
                </a>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* RIGHT: Chat */}
      <div style={{ flex: 1, background: "white", borderRadius: "12px", border: "1px solid #e2e8f0", display: "flex", flexDirection: "column", position: "relative" }}>
        
        {/* Messages */}
        <div style={{ flex: 1, overflowY: "auto", padding: "24px", display: "flex", flexDirection: "column", gap: "16px" }}>
          {data.messages.map(m => {
            const isMe = m.sender_id === user.id;
            return (
              <div key={m.id} style={{ display: "flex", flexDirection: "column", alignItems: isMe ? "flex-end" : "flex-start", maxWidth: "80%", alignSelf: isMe ? "flex-end" : "flex-start" }}>
                <div style={{ fontSize: "12px", color: "#64748b", marginBottom: "4px", padding: "0 4px" }}>
                  {m.sender_name} • {new Date(m.timestamp).toLocaleTimeString()} {m.source === "email" ? "📧" : ""}
                </div>
                <div style={{ padding: "12px 16px", borderRadius: "12px", backgroundColor: isMe ? "#2563eb" : "#f1f5f9", color: isMe ? "white" : "#0f172a", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {m.content}
                </div>
              </div>
            );
          })}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div style={{ padding: "16px", borderTop: "1px solid #e2e8f0", background: "#f8fafc", borderBottomLeftRadius: "12px", borderBottomRightRadius: "12px", position: "relative" }}>
          
          {showMentionMenu && filteredMentions.length > 0 && (
            <div style={{ position: "absolute", bottom: "100%", left: "16px", background: "white", border: "1px solid #e2e8f0", borderRadius: "8px", boxShadow: "0 4px 6px -1px rgba(0,0,0,0.1)", maxHeight: "200px", overflowY: "auto", zIndex: 10, minWidth: "250px" }}>
              {filteredMentions.map(u => (
                <div key={u.id} onClick={() => insertMention(u.id, u.full_name)} style={{ padding: "8px 12px", cursor: "pointer", borderBottom: "1px solid #f1f5f9" }}>
                  <div style={{ fontSize: "14px", fontWeight: "500" }}>{u.full_name}</div>
                  <div style={{ fontSize: "12px", color: "#64748b" }}>{u.email}</div>
                </div>
              ))}
            </div>
          )}

          <div style={{ display: "flex", gap: "10px" }}>
            <textarea
              value={newMessage}
              onChange={handleMessageChange}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
              placeholder="Type a message... Use @ to mention"
              style={{ flex: 1, padding: "12px", borderRadius: "8px", border: "1px solid #cbd5e1", resize: "none", minHeight: "44px", maxHeight: "120px", outline: "none", fontFamily: "inherit" }}
              rows={1}
            />
            <button onClick={sendMessage} disabled={!newMessage.trim()} style={{ background: "#2563eb", color: "white", border: "none", borderRadius: "8px", padding: "0 20px", cursor: newMessage.trim() ? "pointer" : "not-allowed", fontWeight: "600", opacity: newMessage.trim() ? 1 : 0.5 }}>
              Send
            </button>
          </div>
        </div>
        
      </div>
    </div>
  );
}
