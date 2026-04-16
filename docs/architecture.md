# LexAI Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     LexAI Platform                          │
├───────────────┬─────────────────────┬───────────────────────┤
│  Frontend     │     API Layer       │     Workers           │
│  Next.js 14   │     FastAPI         │     Celery            │
│  TypeScript   │     Python 3.12     │     Python 3.12       │
│  Tailwind CSS │     SQLModel        │                       │
│  Zustand      │     LangGraph       │                       │
└───────┬───────┴──────────┬──────────┴───────────────────────┘
        │                  │
        │    REST API       │
        └──────────────────┘
                  │
    ┌─────────────┼──────────────────────────┐
    │             │                          │
┌───▼───┐  ┌─────▼─────┐  ┌────────┐  ┌────▼───┐
│Postgres│  │  Qdrant   │  │ Neo4j  │  │  Redis │
│ SQLite │  │  Vectors  │  │ Graph  │  │ Queues │
└────────┘  └───────────┘  └────────┘  └────────┘
                  │
            ┌─────▼─────┐
            │   MinIO   │
            │  Storage  │
            └───────────┘
```

## Layer Descriptions

### 1. UI Layer (apps/web)
- **Next.js 14 App Router** with TypeScript
- **Zustand** for auth state (persisted)
- **TanStack Query** for server state and caching
- **React Hook Form + Zod** for form validation
- Dark glassmorphism design system with purple accent

### 2. API Layer (apps/api)
- **FastAPI** with async SQLAlchemy
- JWT authentication (OAuth2 password flow)
- Role-based access control (legal_team / reviewer / ops_admin / super_admin)
- Audit logging on every action
- All endpoints verified against folder/document ownership

### 3. Document Parsing Layer
- **Docling** primary parser (PDF, DOCX, structured extraction)
- **PyMuPDF** fallback for OCR/scanned documents
- Semantic chunking by section > clause > table > paragraph
- Page numbers, headings, and clause numbers preserved

### 4. Retrieval Layer (Qdrant)
- Dense vectors via Gemini `embedding-001`
- Sparse keyword indices for exact clause matching
- Hybrid retrieval with **Reciprocal Rank Fusion (RRF)**
- Metadata filters: folder_id, document_id, language, page, clause_type
- Scope enforcement — never retrieves outside user-selected scope

### 5. Graph Layer (Neo4j)
- Nodes: Document, Folder, Clause, Entity
- Relations: CONTAINS, MENTIONS, TRANSLATED_FROM, CONFLICTS_WITH
- Cross-document conflict detection
- Entity/party consistency checking

### 6. Workflow Layer (LangGraph)
- **chat_workflow**: query rewrite → hybrid retrieval → reranking → graph facts → grounded answer
- **draft_workflow**: clause selection → precedent retrieval → conflict check → structured draft
- **translation_workflow**: language detection → structure extraction → section translation

### 7. LLM Layer (Gemini only)
- Single API key, single model family
- `gemini-1.5-flash` for fast tasks (reranking, extraction, language detection)
- `gemini-1.5-pro` for complex tasks (answer generation, drafting, translation)
- Structured JSON outputs enforced with `response_mime_type=application/json`
- All prompts: explicit schema, no-hallucinate rule, insufficient-evidence rule

### 8. Observability
- Structlog structured logging
- AuditLog table (every read/write/approve/translate/delete)
- Retrieval trace stored per chat message
- Evaluation harness with golden test dataset

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Single Gemini key | Enforces cost control and auditability |
| LangGraph (not pure agents) | Deterministic workflows with limited agentic nodes |
| RRF hybrid search | Better than pure dense for legal exact-term matching |
| Semantic chunking | Legal clauses have strong conceptual boundaries |
| Scope enforcement | Legal teams must control what the AI can see |
| Human review gate | No draft or translation goes out without approval |
| Structured JSON outputs | Reduces hallucination, enables source mapping |

## Data Flow: Document Intelligence

```
User selects scope (folder/files)
       ↓
Create chat session (stores scope_filter in Postgres)
       ↓
User asks question
       ↓
[LangGraph chat_workflow]
  1. Query rewriting (Gemini Flash)
  2. Embed query (Gemini embedding-001)
  3. Hybrid search in Qdrant (dense + sparse + metadata filter)
  4. Reranking (Gemini Flash scores by legal relevance)
  5. Graph facts (Neo4j cross-document conflicts)
  6. Grounded answer (Gemini Pro + strict prompt)
       ↓
Return: answer + sources (doc, page, clause, snippet) + confidence
```

## Data Flow: Contract Drafting

```
User selects contract type + fills form
       ↓
[LangGraph draft_workflow]
  1. Determine required clauses by contract type
  2. Retrieve precedent clauses from Qdrant (per clause type)
  3. Check graph for cross-document conflicts
  4. Generate structured draft (Gemini Pro)
       ↓
Return: clause list + provenance + issues flagged
       ↓
Legal team edits in rich text editor (TipTap)
       ↓
Reviewer approves → audit logged → status = approved
```
