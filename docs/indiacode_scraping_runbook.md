# IndiaCode Legal Act Scraping Runbook

## Purpose
This document captures the production-safe scraping pattern used to ingest legal acts from IndiaCode, download PDFs, extract text, and persist metadata for future runs.

## End-to-End Flow
1. Create a fresh HTTP session per act.
2. Hit IndiaCode homepage first to initialize session state.
3. Search by act name in the Central Acts collection.
4. Parse search result rows and extract candidates.
5. Fuzzy-match candidate titles against requested act name.
6. Open detail page for selected handle.
7. Extract English PDF bitstream link.
8. Download PDF.
9. Upload PDF to MinIO.
10. Extract text (pdfplumber, fallback OCR via Gemini if needed).
11. Store metadata + text in Postgres.
12. Update ingestion status and seed logs.

## Required Request Pattern
- Base site: `https://www.indiacode.nic.in`
- Search endpoint:
  `https://www.indiacode.nic.in/handle/123456789/1362/simple-search?query={QUERY}&btngo=&searchradio=acts`

### Session Rules
- Always use a new `requests.Session()` for each act.
- Always call homepage first:
  `GET https://www.indiacode.nic.in`
- Set User-Agent on every request:
  `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36`
- Sleep about 500ms to 700ms between requests to avoid NIC throttling.

## Search Result Parsing Rules
Search results are in a table (typically class `table table-hover`) with columns:
- Enactment Date
- Act Number
- Short Title
- View

### Important Parsing Details
- Use `get_text(separator=" ", strip=True)` when reading title cells.
- Normalize repeated whitespace after extraction.
- Why: IndiaCode wraps title words in nested tags; plain `get_text(strip=True)` merges words and lowers fuzzy-match score.

### Handle ID Extraction
- Use the `View...` link in each row.
- Pattern:
  `/handle/123456789/{handle_id}?view_type=search&col=123456789/1362`
- Extract `{handle_id}` via regex.
- Ignore collection handle `1362`.

## Matching Strategy
Use `rapidfuzz.fuzz.token_sort_ratio` with normalization:
- lowercase
- remove leading `the`
- remove punctuation
- collapse extra spaces

Decision thresholds:
- score >= 85: auto proceed
- score 60 to 84: mark `needs_review`, log and skip auto-ingestion
- score < 60: `not_found`

## Detail Page and PDF Extraction
Detail URL pattern:
`https://www.indiacode.nic.in/handle/123456789/{handle_id}`

On detail page:
- Find all anchors where href contains `/bitstream/`.
- Strip URL fragments before extension checks:
  - convert `/bitstream/.../A1887-07.pdf#search=...` to `/bitstream/.../A1887-07.pdf`
- Prefer English PDF over Hindi:
  - usually English filename starts with `A`
  - Hindi often starts with `H`

Final PDF URL format:
`https://www.indiacode.nic.in/bitstream/123456789/{handle_id}/{seq}/{file}.pdf`

## Storage Pattern
- MinIO bucket: `legal-acts`
- Object path: `acts/{handle_id}/original.pdf`

Recommended DB fields to persist:
- title
- normalized_title
- act_number
- enactment_date
- handle_id
- indiacode_url
- bitstream_url
- minio_bucket
- minio_path
- pdf_text
- has_text_layer
- total_pages
- ingestion_status
- confidence_score
- error_message

## Text Extraction Pattern
1. Use `pdfplumber` page-by-page.
2. Compute average chars per page.
3. If avg >= 100 chars/page:
   - treat as native text PDF (`has_text_layer = true`).
4. If avg < 100 chars/page:
   - treat as scanned (`has_text_layer = false`).
   - run OCR fallback with Gemini.

## Error Handling
For each act, never stop whole batch on failure:
- Catch exception
- Save traceback/error in `error_message`
- Set `ingestion_status = failed`
- Continue next act

Recommended statuses:
- pending
- searching
- downloading
- ocr_processing
- completed
- failed
- not_found
- needs_review

## Known Edge Cases
1. Title words merged in HTML text extraction.
   - Fix: `separator=" "` + whitespace normalization.
2. PDF links include fragment (`#search=...`) causing `.endswith(".pdf")` checks to fail.
   - Fix: split on `#` before checks.
3. Hindi and English PDF both present.
   - Fix: prefer non-`H` filename first.
4. Some acts map to renamed canonical titles (example: requested `Contract Act, 1872` maps to `Indian Contract Act, 1872`).
   - Handle with fuzzy score and log confidence.

## Validation Checklist (Before Full Seed)
- Verify one act end-to-end (example: Suits Valuation Act, 1887).
- Confirm PDF downloads successfully.
- Confirm file appears in MinIO under expected path.
- Confirm DB row contains metadata + text.
- Confirm status transitions to `completed`.

## Batch Execution Checklist
- API running
- Celery worker running
- MinIO reachable
- Postgres reachable
- Trigger seed endpoint once
- Monitor counts by status: completed, failed, not_found, needs_review
- Export unresolved (`not_found`, `needs_review`) for manual review

## Reusability Notes
This pattern works broadly for acts on IndiaCode where the flow is:
search page -> view link -> detail page -> bitstream PDF.
For future expansion, keep parser logic tolerant to minor HTML changes by:
- searching table by header text, not class alone
- searching links by path tokens (`/handle/`, `/bitstream/`) and regex, not brittle DOM positions
