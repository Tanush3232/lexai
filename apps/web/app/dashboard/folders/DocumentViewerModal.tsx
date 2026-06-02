"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { api, documentsApi } from "@/lib/api";
import {
  X, FileText, BookOpen, ChevronDown, ChevronRight,
  AlertCircle, Clock, Bold, Italic, Underline,
  List, ListOrdered, Sparkles,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface TreeNode {
  id: string;
  title: string;
  page_range: [number, number];
  summary: string;
  children?: TreeNode[];
}

interface DocumentTreeResponse {
  status: "pending" | "ready" | "error";
  summary: string | null;
  tree: TreeNode[];
  document_name?: string;
  doc_type?: string;
  error_message?: string;
}

interface ContentSection {
  heading: string;
  text: string;
  page_start: number;
}

interface DocumentContentResponse {
  content: string;
  status: string;
  sections: ContentSection[];
}

export interface DocMeta {
  id: string;
  name: string;
  status: string;
  page_count?: number;
}

// ─── Skeleton helpers ─────────────────────────────────────────────────────────

function SkeletonLine({
  width = "100%",
  height = "13px",
  style = {},
}: {
  width?: string;
  height?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className="skeleton-shimmer"
      style={{ width, height, borderRadius: "4px", marginBottom: "6px", ...style }}
    />
  );
}

function SkeletonSummaryCard() {
  return (
    <div
      style={{
        borderRadius: "12px",
        padding: "16px",
        background: "rgba(91,79,207,0.08)",
        border: "1px solid rgba(91,79,207,0.15)",
        minHeight: "130px",
        display: "flex",
        flexDirection: "column",
        gap: 0,
      }}
    >
      {[0.9, 1.0, 0.8, 0.95, 0.65].map((w, i) => (
        <SkeletonLine key={i} width={`${w * 100}%`} style={{ background: "rgba(91,79,207,0.15)" }} />
      ))}
    </div>
  );
}

function SkeletonSectionCard() {
  return (
    <div
      style={{
        background: "var(--white)",
        border: "1px solid var(--border)",
        borderRadius: "8px",
        padding: "12px",
        marginBottom: "8px",
      }}
    >
      <SkeletonLine width="68%" height="12px" />
      <SkeletonLine width="100%" height="10px" />
      <SkeletonLine width="82%" height="10px" />
    </div>
  );
}

function SkeletonContent() {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {[0.9, 0.6, 1.0, 0.7, 0.85, 0.5, 0.95, 0.75].map((w, i) => (
        <SkeletonLine key={i} width={`${w * 100}%`} height="14px" />
      ))}
      <div style={{ height: "24px" }} />
      <SkeletonLine width="40%" height="16px" style={{ borderRadius: "6px" }} />
      <div style={{ height: "8px" }} />
      {[1.0, 0.9, 0.75, 1.0, 0.6, 0.8, 0.95, 0.7, 0.85, 0.55].map((w, i) => (
        <SkeletonLine key={i + 10} width={`${w * 100}%`} height="14px" />
      ))}
    </div>
  );
}

// ─── Rich-text toolbar ────────────────────────────────────────────────────────

function ToolbarBtn({
  onClick,
  title,
  active = false,
  children,
}: {
  onClick: () => void;
  title: string;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onMouseDown={(e) => {
        e.preventDefault();
        onClick();
      }}
      title={title}
      style={{
        width: "30px",
        height: "28px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: active ? "var(--accent-light)" : "transparent",
        border: `1px solid ${active ? "rgba(91,79,207,0.25)" : "transparent"}`,
        borderRadius: "5px",
        cursor: "pointer",
        color: active ? "var(--accent)" : "var(--text2)",
        transition: "all 0.1s",
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  );
}

function ToolbarSep() {
  return (
    <div
      style={{
        width: "1px",
        height: "18px",
        background: "var(--border)",
        margin: "0 2px",
        flexShrink: 0,
      }}
    />
  );
}

function EditorToolbar({ editorRef }: { editorRef: React.RefObject<HTMLDivElement | null> }) {
  const [active, setActive] = useState(new Set<string>());

  useEffect(() => {
    const update = () => {
      const s = new Set<string>();
      if (document.queryCommandState("bold")) s.add("b");
      if (document.queryCommandState("italic")) s.add("i");
      if (document.queryCommandState("underline")) s.add("u");
      if (document.queryCommandState("insertUnorderedList")) s.add("ul");
      if (document.queryCommandState("insertOrderedList")) s.add("ol");
      setActive(s);
    };
    document.addEventListener("selectionchange", update);
    return () => document.removeEventListener("selectionchange", update);
  }, []);

  const exec = (cmd: string, val?: string) => {
    editorRef.current?.focus();
    document.execCommand(cmd, false, val);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "2px",
        padding: "6px 12px",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg)",
        flexShrink: 0,
        flexWrap: "wrap",
        minHeight: "44px",
      }}
    >
      {/* Block format */}
      <select
        onChange={(e) => { exec("formatBlock", e.target.value); }}
        defaultValue="p"
        style={{
          height: "28px",
          border: "1px solid var(--border)",
          borderRadius: "5px",
          fontSize: "12px",
          color: "var(--text)",
          background: "var(--white)",
          padding: "0 6px",
          cursor: "pointer",
          flexShrink: 0,
        }}
      >
        <option value="p">Paragraph</option>
        <option value="h1">Heading 1</option>
        <option value="h2">Heading 2</option>
        <option value="h3">Heading 3</option>
      </select>

      {/* Font size */}
      <select
        defaultValue="14"
        onChange={(e) => {
          const sz = e.target.value;
          exec("fontSize", "7");
          const els = editorRef.current?.querySelectorAll('font[size="7"]');
          els?.forEach((el) => {
            (el as HTMLElement).removeAttribute("size");
            (el as HTMLElement).style.fontSize = `${sz}px`;
          });
        }}
        style={{
          height: "28px",
          width: "54px",
          border: "1px solid var(--border)",
          borderRadius: "5px",
          fontSize: "12px",
          color: "var(--text)",
          background: "var(--white)",
          padding: "0 4px",
          cursor: "pointer",
          marginLeft: "4px",
          flexShrink: 0,
        }}
      >
        {["10", "11", "12", "13", "14", "16", "18", "20", "24", "28"].map((s) => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>

      <ToolbarSep />

      <ToolbarBtn onClick={() => exec("bold")} title="Bold (Ctrl+B)" active={active.has("b")}>
        <Bold size={13} />
      </ToolbarBtn>
      <ToolbarBtn onClick={() => exec("italic")} title="Italic (Ctrl+I)" active={active.has("i")}>
        <Italic size={13} />
      </ToolbarBtn>
      <ToolbarBtn onClick={() => exec("underline")} title="Underline (Ctrl+U)" active={active.has("u")}>
        <Underline size={13} />
      </ToolbarBtn>

      <ToolbarSep />

      <ToolbarBtn onClick={() => exec("insertUnorderedList")} title="Bullet List" active={active.has("ul")}>
        <List size={13} />
      </ToolbarBtn>
      <ToolbarBtn onClick={() => exec("insertOrderedList")} title="Numbered List" active={active.has("ol")}>
        <ListOrdered size={13} />
      </ToolbarBtn>

      <ToolbarSep />

      <ToolbarBtn onClick={() => exec("undo")} title="Undo">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M3 7v6h6" /><path d="M21 17a9 9 0 00-9-9 9 9 0 00-6 2.3L3 13" />
        </svg>
      </ToolbarBtn>
      <ToolbarBtn onClick={() => exec("redo")} title="Redo">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M21 7v6h-6" /><path d="M3 17a9 9 0 019-9 9 9 0 016 2.3l3 2.7" />
        </svg>
      </ToolbarBtn>
      <ToolbarBtn onClick={() => exec("removeFormat")} title="Clear Formatting">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M4 7V4h16v3" /><path d="M5 20h6" /><path d="M13 4L8 20" />
          <line x1="19" y1="12" x2="22" y2="21" /><line x1="22" y1="12" x2="19" y2="21" />
        </svg>
      </ToolbarBtn>

      <div style={{ flex: 1 }} />

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "5px",
          padding: "3px 10px",
          background: "var(--accent-light)",
          borderRadius: "5px",
          fontSize: "10px",
          fontWeight: 600,
          color: "var(--accent)",
          letterSpacing: ".04em",
        }}
      >
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
        </svg>
        EDITING MODE
      </div>
    </div>
  );
}

// ─── Section node (left panel) ────────────────────────────────────────────────

function SectionNode({
  node,
  onSelect,
  selected,
}: {
  node: TreeNode;
  onSelect: (node: TreeNode) => void;
  selected: string | null;
}) {
  const [open, setOpen] = useState(false);
  const hasChildren = (node.children?.length ?? 0) > 0;
  const isSelected = selected === node.id;

  return (
    <div>
      <div
        onClick={() => {
          onSelect(node);
          if (hasChildren) setOpen((v) => !v);
        }}
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: "6px",
          padding: "10px 12px",
          borderRadius: "8px",
          cursor: "pointer",
          background: isSelected ? "var(--accent-light)" : "var(--white)",
          border: `1px solid ${isSelected ? "rgba(91,79,207,0.35)" : "var(--border)"}`,
          marginBottom: "6px",
          transition: "all 0.12s",
          boxShadow: isSelected ? "0 2px 8px rgba(91,79,207,0.12)" : "none",
        }}
      >
        {hasChildren ? (
          <div style={{ marginTop: "3px", flexShrink: 0, color: "var(--text3)" }}>
            {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          </div>
        ) : (
          <div style={{ width: "11px", flexShrink: 0 }} />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{
            fontSize: "12px",
            fontWeight: 600,
            color: isSelected ? "var(--accent)" : "var(--text)",
            lineHeight: 1.35,
            marginBottom: "4px",
          }}>
            {node.title}
          </p>
          <p style={{
            fontSize: "11px",
            color: "var(--text3)",
            lineHeight: 1.45,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}>
            {node.summary}
          </p>
          {node.page_range && (
            <span style={{
              display: "inline-block",
              marginTop: "5px",
              fontSize: "10px",
              color: isSelected ? "var(--accent)" : "var(--text3)",
              background: isSelected ? "rgba(91,79,207,0.1)" : "var(--bg2)",
              borderRadius: "4px",
              padding: "1px 6px",
              fontWeight: 500,
            }}>
              p.{node.page_range[0]}–{node.page_range[1]}
            </span>
          )}
        </div>
      </div>

      {hasChildren && open && (
        <div style={{ paddingLeft: "14px" }}>
          {node.children!.map((child) => (
            <SectionNode key={child.id} node={child} onSelect={onSelect} selected={selected} />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Modal ────────────────────────────────────────────────────────────────────

export default function DocumentViewerModal({
  doc,
  onClose,
}: {
  doc: DocMeta;
  onClose: () => void;
}) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [treeData, setTreeData] = useState<DocumentTreeResponse | null>(null);
  const [treeLoading, setTreeLoading] = useState(true);
  const [treeTimedOut, setTreeTimedOut] = useState(false);
  const [contentData, setContentData] = useState<DocumentContentResponse | null>(null);
  const [contentLoading, setContentLoading] = useState(true);
  const [editorReady, setEditorReady] = useState(false);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [pollCount, setPollCount] = useState(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [fileUrlLoading, setFileUrlLoading] = useState(true);
  const [fileContentType, setFileContentType] = useState<string | null>(null);
  const [docxHtml, setDocxHtml] = useState<string | null>(null);
  const hasFetchedFile = useRef(false);

  // Fetch original file as a blob via authenticated API — works in production
  // because it goes through Nginx at /api/v1/documents/{id}/download, not port 9000.
  // hasFetchedFile guards against React Strict Mode double-invocation in dev
  // which would cause the file to download twice.
  useEffect(() => {
    if (hasFetchedFile.current) return;
    hasFetchedFile.current = true;
    let objectUrl: string | null = null;
    api
      .get(`/documents/${doc.id}/download`, { responseType: "blob" })
      .then(async (r) => {
        const ct: string = r.headers["content-type"] || "application/octet-stream";
        setFileContentType(ct);
        if (ct.includes("pdf")) {
          // PDF: render inline via iframe with a blob URL
          objectUrl = URL.createObjectURL(new Blob([r.data], { type: ct }));
          setFileUrl(objectUrl);
        } else if (
          ct.includes("wordprocessingml") ||
          ct.includes("msword") ||
          ct.includes("officedocument") ||
          doc.name.toLowerCase().endsWith(".docx") ||
          doc.name.toLowerCase().endsWith(".doc")
        ) {
          // DOCX: convert to HTML via mammoth and display inline
          try {
            const mammoth = (await import("mammoth")).default;
            const arrayBuffer = await (r.data as Blob).arrayBuffer();
            const result = await mammoth.convertToHtml({ arrayBuffer });
            setDocxHtml(result.value || "<p>(Empty document)</p>");
          } catch {
            // mammoth failed — fall through to extracted text panel
          }
        }
        // TXT / other: fall through to extracted text panel
      })
      .catch(() => {})
      .finally(() => setFileUrlLoading(false));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [doc.id, doc.name]);

  // Fetch AI tree – polls while pending (max 30 polls = 150s then fall through)
  const fetchTree = useCallback(async () => {
    try {
      const res = await documentsApi.getTree(doc.id);
      const data: DocumentTreeResponse = res.data;
      setTreeData(data);
      if (data.status === "pending" && pollCount < 30) {
        pollTimer.current = setTimeout(() => setPollCount((c) => c + 1), 5000);
      } else {
        setTreeLoading(false);
        if (data.status === "pending") setTreeTimedOut(true);
      }
    } catch {
      setTreeLoading(false);
      setTreeTimedOut(true);
    }
  }, [doc.id, pollCount]);

  useEffect(() => {
    fetchTree();
    return () => { if (pollTimer.current) clearTimeout(pollTimer.current); };
  }, [pollCount]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (treeData?.status === "ready") setTreeLoading(false);
  }, [treeData?.status]);

  // Fetch text content
  useEffect(() => {
    documentsApi
      .getTextContent(doc.id)
      .then((r) => setContentData(r.data))
      .catch(() => {})
      .finally(() => setContentLoading(false));
  }, [doc.id]);

  // Populate editable div once content arrives
  useEffect(() => {
    if (!editorRef.current || !contentData || contentLoading || editorReady) return;
    const sections = contentData.sections ?? [];
    if (!sections.length) { setEditorReady(true); return; }

    // Convert markdown-style table lines into an HTML <table>
    function mdTableToHtml(lines: string[]): string {
      // Filter out separator rows (e.g. |---|---|)
      const dataRows = lines.filter(l => !/^\s*\|[\s\-:]+\|\s*$/.test(l));
      if (dataRows.length === 0) return "";
      const parseRow = (row: string) =>
        row.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(c => c.trim());

      const headerCells = parseRow(dataRows[0]);
      const bodyRows = dataRows.slice(1).map(parseRow);

      let t = `<table style="width:100%;border-collapse:collapse;margin:16px 0 20px;font-size:13px;line-height:1.6;">`;
      t += `<thead><tr>`;
      for (const h of headerCells) {
        t += `<th style="text-align:left;padding:10px 14px;background:#5b4fcf;color:#fff;font-weight:600;border:1px solid #4a40b0;white-space:nowrap;">${renderInlineMarkdown(h)}</th>`;
      }
      t += `</tr></thead><tbody>`;
      bodyRows.forEach((cells, ri) => {
        const bg = ri % 2 === 0 ? "#faf9ff" : "#fff";
        t += `<tr>`;
        for (let ci = 0; ci < headerCells.length; ci++) {
          t += `<td style="padding:9px 14px;border:1px solid #e8e6f0;background:${bg};vertical-align:top;">${renderInlineMarkdown(cells[ci] ?? "")}</td>`;
        }
        t += `</tr>`;
      });
      t += `</tbody></table>`;
      return t;
    }

    // Render **bold** inside table cells
    function renderInlineMarkdown(text: string): string {
      return text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    }

    // Check if a line looks like part of a markdown table
    function isTableLine(line: string): boolean {
      const trimmed = line.trim();
      return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|");
    }

    let html = "";
    for (const s of sections) {
      if (s.heading) {
        html += `<h2 style="font-size:17px;font-weight:700;color:#1a1916;margin:24px 0 8px;">${s.heading}</h2>`;
      }
      // Split into lines and detect table blocks
      const allLines = s.text.split("\n");
      let i = 0;
      while (i < allLines.length) {
        if (isTableLine(allLines[i])) {
          // Collect consecutive table lines
          const tableLines: string[] = [];
          while (i < allLines.length && isTableLine(allLines[i])) {
            tableLines.push(allLines[i]);
            i++;
          }
          html += mdTableToHtml(tableLines);
        } else {
          // Collect non-table lines into a paragraph
          const paraLines: string[] = [];
          while (i < allLines.length && !isTableLine(allLines[i])) {
            paraLines.push(allLines[i]);
            i++;
          }
          const text = paraLines.join("\n").trim();
          if (text) {
            // Split on double newlines for paragraph breaks
            for (const para of text.split(/\n{2,}/).filter(Boolean)) {
              html += `<p style="margin:0 0 12px;line-height:1.8;">${renderInlineMarkdown(para.replace(/\n/g, "<br/>"))}</p>`;
            }
          }
        }
      }
    }
    editorRef.current.innerHTML = html;
    setEditorReady(true);
  }, [contentData, contentLoading, editorReady]);

  // ESC to close + block body scroll
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  // Scroll center panel to matching heading when tree node clicked
  const handleNodeSelect = (node: TreeNode) => {
    setSelectedNode(node.id);
    if (!editorRef.current) return;
    const headings = Array.from(editorRef.current.querySelectorAll("h1,h2,h3,h4"));
    for (const el of headings) {
      if (el.textContent?.toLowerCase().includes(node.title.toLowerCase().slice(0, 22))) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        break;
      }
    }
  };

  const isLoadingAI = treeLoading;
  const aiReady = treeData?.status === "ready";
  const aiError = treeTimedOut || treeData?.status === "error";
  const hasContent = !contentLoading && (contentData?.sections?.length ?? 0) > 0;
  const docTypeLabel = (treeData?.doc_type ?? "DOCUMENT").replace(/_/g, " ").toUpperCase();

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 500,
          background: "rgba(15,13,30,0.6)",
          backdropFilter: "blur(6px)",
          WebkitBackdropFilter: "blur(6px)",
        }}
      />

      {/* Centering wrapper */}
      <div
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 501,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "20px",
          pointerEvents: "none",
        }}
      >
        {/* Modal */}
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            pointerEvents: "auto",
            width: "100%",
            maxWidth: "1180px",
            height: "calc(100vh - 40px)",
            maxHeight: "900px",
            background: "var(--white)",
            borderRadius: "16px",
            boxShadow: "0 24px 80px rgba(0,0,0,0.22), 0 8px 24px rgba(0,0,0,0.12)",
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            border: "1px solid var(--border)",
          }}
        >
          {/* ── Header ─────────────────────────────────────────────────────── */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0 16px 0 20px",
              height: "56px",
              borderBottom: "1px solid var(--border)",
              background: "var(--white)",
              flexShrink: 0,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <div style={{
                width: "32px", height: "32px",
                background: "var(--accent-light)",
                borderRadius: "8px",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0,
              }}>
                <FileText size={15} style={{ color: "var(--accent)" }} />
              </div>
              <div>
                <p style={{
                  fontSize: "14px", fontWeight: 700, color: "var(--text)",
                  margin: 0, lineHeight: 1.2,
                  maxWidth: "480px", overflow: "hidden",
                  textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {doc.name}
                </p>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "2px" }}>
                  <span style={{ fontSize: "9px", fontWeight: 700, color: "var(--accent)", letterSpacing: ".08em" }}>
                    {docTypeLabel}
                  </span>
                  <span style={{ fontSize: "9px", color: "var(--border2)" }}>·</span>
                  <span style={{ fontSize: "9px", fontWeight: 500, color: "var(--text3)", letterSpacing: ".05em" }}>
                    GEMINI 2.5 POWERED
                  </span>
                </div>
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              {isLoadingAI && (
                <span style={{
                  display: "flex", alignItems: "center", gap: "5px",
                  padding: "3px 8px", background: "var(--accent-light)",
                  borderRadius: "5px", fontSize: "10px", color: "var(--accent)", fontWeight: 600,
                }}>
                  <Clock size={10} /> Generating AI analysis…
                </span>
              )}
              {aiReady && !isLoadingAI && (
                <span style={{
                  display: "flex", alignItems: "center", gap: "4px",
                  padding: "3px 8px", background: "var(--green-light)",
                  borderRadius: "5px", fontSize: "10px", color: "var(--green)", fontWeight: 600,
                }}>
                  <Sparkles size={10} /> AI Ready
                </span>
              )}
              <button
                onClick={onClose}
                title="Close (Esc)"
                onMouseEnter={(e) => {
                  const b = e.currentTarget;
                  b.style.background = "var(--red-light)";
                  b.style.color = "var(--red)";
                  b.style.borderColor = "var(--red)";
                }}
                onMouseLeave={(e) => {
                  const b = e.currentTarget;
                  b.style.background = "var(--bg2)";
                  b.style.color = "var(--text2)";
                  b.style.borderColor = "var(--border)";
                }}
                style={{
                  width: "32px", height: "32px",
                  background: "var(--bg2)",
                  border: "1px solid var(--border)",
                  borderRadius: "8px", cursor: "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "var(--text2)", flexShrink: 0, transition: "all 0.12s",
                }}
              >
                <X size={15} />
              </button>
            </div>
          </div>

          {/* ── Body ───────────────────────────────────────────────────────── */}
          <div style={{ display: "flex", flex: 1, overflow: "hidden", minHeight: 0 }}>

            {/* LEFT PANEL */}
            <div style={{
              width: "268px", flexShrink: 0,
              borderRight: "1px solid var(--border)",
              display: "flex", flexDirection: "column",
              overflow: "hidden", background: "var(--bg)",
            }}>
              <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>

                {/* AI SUMMARY */}
                <div style={{ marginBottom: "20px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "5px", marginBottom: "10px" }}>
                    <Sparkles size={10} style={{ color: "var(--accent)" }} />
                    <p style={{
                      fontSize: "10px", fontWeight: 700, color: "var(--accent)",
                      textTransform: "uppercase", letterSpacing: ".08em", margin: 0,
                    }}>
                      AI Summary
                    </p>
                    <span style={{
                      fontSize: "9px", padding: "1px 5px",
                      background: "var(--accent)", color: "white",
                      borderRadius: "3px", fontWeight: 700,
                    }}>AI</span>
                  </div>

                  {treeLoading ? <SkeletonSummaryCard /> :
                   aiReady && treeData?.summary ? (
                    <div style={{
                      background: "var(--accent)", color: "white",
                      borderRadius: "12px", padding: "14px 16px",
                      fontSize: "12px", lineHeight: 1.75,
                      boxShadow: "0 4px 20px rgba(91,79,207,0.28)",
                    }}>
                      {treeData.summary}
                    </div>
                  ) : aiError ? (
                    <div style={{
                      background: "var(--bg2)", borderRadius: "10px", padding: "12px",
                      fontSize: "11px", color: "var(--text3)",
                      display: "flex", gap: "8px", alignItems: "flex-start",
                    }}>
                      <AlertCircle size={12} style={{ flexShrink: 0, marginTop: "1px" }} />
                      AI summary not available for this document.
                    </div>
                  ) : (
                    <div style={{
                      background: "var(--amber-light)", borderRadius: "10px", padding: "12px",
                      fontSize: "11px", color: "var(--amber)",
                      display: "flex", gap: "8px", alignItems: "flex-start",
                    }}>
                      <Clock size={12} style={{ flexShrink: 0, marginTop: "1px" }} />
                      AI summary pending…
                    </div>
                  )}
                </div>

                {/* SECTIONS */}
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "5px", marginBottom: "10px" }}>
                    <BookOpen size={10} style={{ color: "var(--text3)" }} />
                    <p style={{
                      fontSize: "10px", fontWeight: 700, color: "var(--text3)",
                      textTransform: "uppercase", letterSpacing: ".08em", margin: 0,
                    }}>
                      Section Breakdown
                    </p>
                    <span style={{
                      fontSize: "9px", padding: "1px 5px",
                      background: "var(--accent-light)", color: "var(--accent)",
                      borderRadius: "3px", fontWeight: 700,
                    }}>AI</span>
                  </div>

                  {treeLoading ? (
                    <><SkeletonSectionCard /><SkeletonSectionCard /><SkeletonSectionCard /></>
                  ) : aiReady && treeData?.tree?.length ? (
                    treeData.tree.map((n) => (
                      <SectionNode key={n.id} node={n} onSelect={handleNodeSelect} selected={selectedNode} />
                    ))
                  ) : aiError ? (
                    <p style={{ fontSize: "11px", color: "var(--text3)", lineHeight: 1.6 }}>
                      AI section tree unavailable.
                    </p>
                  ) : contentData?.sections?.filter((s) => s.heading).length ? (
                    contentData.sections.filter((s) => s.heading).map((s, i) => (
                      <div
                        key={i}
                        onClick={() => {
                          if (!editorRef.current) return;
                          const els = Array.from(editorRef.current.querySelectorAll("h2"));
                          for (const el of els) {
                            if (el.textContent?.includes(s.heading.slice(0, 20))) {
                              el.scrollIntoView({ behavior: "smooth", block: "start" });
                              break;
                            }
                          }
                        }}
                        style={{
                          padding: "9px 12px", borderRadius: "8px", cursor: "pointer",
                          border: "1px solid var(--border)", background: "var(--white)",
                          marginBottom: "6px", fontSize: "12px", fontWeight: 600,
                          color: "var(--text)", lineHeight: 1.4, transition: "all 0.12s",
                        }}
                      >
                        {s.heading}
                      </div>
                    ))
                  ) : (
                    <p style={{ fontSize: "12px", color: "var(--text3)" }}>No sections detected.</p>
                  )}
                </div>
              </div>
            </div>

            {/* CENTER PANEL — Original File Viewer */}
            <div style={{
              flex: 1, display: "flex", flexDirection: "column",
              overflow: "hidden", minWidth: 0,
            }}>
              {/* Document area — shows the original uploaded file */}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--bg2)" }}>
                {fileUrlLoading ? (
                  <div style={{ padding: "40px 56px" }}><SkeletonContent /></div>
                ) : fileUrl ? (
                  // PDF — render in iframe using blob URL
                  <iframe
                    src={fileUrl}
                    title={doc.name}
                    style={{ width: "100%", height: "100%", border: "none", background: "#fff" }}
                  />
                ) : docxHtml ? (
                  // DOCX — mammoth-converted HTML, fully scrollable
                  <div
                    style={{
                      flex: 1,
                      height: "100%",
                      overflowY: "auto",
                      background: "#fff",
                      padding: "48px 64px 80px",
                      fontSize: "14px",
                      lineHeight: 1.85,
                      fontFamily: "'Segoe UI', Arial, sans-serif",
                      color: "#1a1916",
                      boxSizing: "border-box",
                    }}
                    // eslint-disable-next-line react/no-danger
                    dangerouslySetInnerHTML={{ __html: docxHtml }}
                  />
                ) : (
                  /* Fallback: extracted text (TXT files, or when mammoth fails) */
                  <>
                    {hasContent && <EditorToolbar editorRef={editorRef} />}
                    <div style={{ flex: 1, overflowY: "auto", background: "var(--white)" }}>
                      {contentLoading ? (
                        <div style={{ padding: "40px 56px" }}><SkeletonContent /></div>
                      ) : contentData?.status === "uploaded" ? (
                        <div style={{
                          display: "flex", flexDirection: "column",
                          alignItems: "center", justifyContent: "center",
                          padding: "80px 20px", gap: "14px", textAlign: "center",
                        }}>
                          <Clock size={40} style={{ color: "var(--accent)", opacity: 0.4 }} />
                          <p style={{ fontSize: "15px", fontWeight: 600, color: "var(--text)" }}>
                            Indexing in progress
                          </p>
                          <p style={{ fontSize: "13px", color: "var(--text3)", maxWidth: "300px", lineHeight: 1.6 }}>
                            Your document is being parsed. Please check back shortly.
                          </p>
                        </div>
                      ) : (
                        <div
                          ref={editorRef}
                          contentEditable
                          suppressContentEditableWarning
                          spellCheck
                          style={{
                            outline: "none",
                            minHeight: "100%",
                            padding: "48px 64px 80px",
                            fontSize: "14px",
                            lineHeight: 1.85,
                            fontFamily: "Georgia, 'Times New Roman', serif",
                            color: "var(--text)",
                            caretColor: "var(--accent)",
                          }}
                        />
                      )}
                    </div>
                  </>
                )}
              </div>

              {/* Footer */}
              <div style={{
                flexShrink: 0, borderTop: "1px solid var(--border)",
                padding: "6px 16px", display: "flex",
                alignItems: "center", justifyContent: "space-between",
                background: "var(--bg)",
              }}>
                <span style={{ fontSize: "11px", color: "var(--text3)" }}>
                  {fileUrl ? "PDF · Original Document" : docxHtml ? "Word Document · Rendered" : "Extracted Text · Editable"}
                </span>
                <span style={{ fontSize: "11px", color: "var(--text3)" }}>
                  {doc.page_count ? `${doc.page_count} pages` : ""}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  , document.body);
}

