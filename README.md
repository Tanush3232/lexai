# LexAI — Legal Operations Platform

A production-grade legal AI platform powered by Gemini API.

## Features
- **Document Intelligence** — Hybrid RAG Q&A with source pinpointing
- **Contract Drafting** — Template + precedent-augmented generation  
- **Translation** — Structure-preserving legal document translation
- **Folder Management** — Scoped document organization

## Quick Start

```bash
# 1. Copy environment file
cp .env.example .env
# 2. Fill in your GOOGLE_API_KEY and other values in .env
# 3. Start everything
make dev
```

Then open http://localhost:3000

## Architecture
See `/docs/architecture.md` for full system design.
