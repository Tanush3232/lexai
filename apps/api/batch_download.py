"""
batch_download.py — LexAI Legal Act PDF Downloader
=====================================================
Searches IndiaCode (indiacode.nic.in) for all acts listed in acts.txt,
downloads their official PDFs, and saves them to the local `downloaded_acts/`
directory alongside a CSV summary.

This script does NOT touch any database or MinIO. It only downloads files.
Run bulk_ingest.py AFTER this to ingest the PDFs into MinIO and all stores.

Usage (from apps/api/ directory):
    python batch_download.py                     # all acts in acts.txt
    python batch_download.py --limit 20          # first 20 acts only
    python batch_download.py --dry-run           # show plan, no downloads

Usage (from inside the Docker API container):
    docker compose exec api python batch_download.py
    docker compose exec api python batch_download.py --limit 20
"""
import argparse
import csv
import io
import os
import re
import sys
import time
import traceback
from datetime import datetime
from typing import List, Optional

# ── Make 'app' package importable ─────────────────────────────────────────────
API_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, API_DIR)

from app.services.indiacode_scraper import search_act, download_act_pdf

# ── Config ─────────────────────────────────────────────────────────────────────
ACTS_FILE = os.path.normpath(os.path.join(API_DIR, "..", "..", "acts.txt"))
OUTPUT_DIR = os.path.join(API_DIR, "downloaded_acts")
REQUEST_DELAY = 0.7   # seconds between each HTTP request (be polite to NIC)
MIN_PDF_SIZE = 1_000  # bytes — anything smaller is likely an error page


# ── Acts file parser ───────────────────────────────────────────────────────────
def _parse_acts_file() -> List[str]:
    if not os.path.exists(ACTS_FILE):
        print(f"  ✗ ERROR: acts.txt not found at: {ACTS_FILE}")
        print("    Make sure acts.txt is at the root of the project.")
        sys.exit(1)

    acts: List[str] = []
    with open(ACTS_FILE, encoding="utf-8") as fh:
        for line in fh:
            name = line.strip()
            if not name:
                continue
            # Skip comment/header lines
            if name.lower().startswith("here is") or name.startswith("---"):
                continue
            # Strip leading numbering like "1. " or "1) "
            name = re.sub(r"^\d+[.)]\s*", "", name).strip()
            if len(name) > 5:
                acts.append(name)
    return acts


def _safe_filename(title: str) -> str:
    """Convert act title to a safe filesystem filename (max 80 chars)."""
    safe = re.sub(r"[^\w\s\-]", "", title)
    safe = re.sub(r"\s+", "_", safe.strip())
    return safe[:80]


# ── Per-act processor ─────────────────────────────────────────────────────────
def _download_one(act_name: str, index: int, total: int) -> dict:
    """
    Search IndiaCode for the act, download its PDF, and save to disk.
    Returns a result dict for the CSV summary.
    """
    label = f"[{index:>3}/{total}]"
    print(f"\n{label} {act_name}")
    t0 = time.time()

    result = {
        "index": index,
        "act_name": act_name,
        "status": "pending",
        "matched_title": "",
        "handle_id": "",
        "confidence_score": 0.0,
        "pdf_bytes": 0,
        "saved_path": "",
        "error": "",
    }

    # ── 1. Search IndiaCode ────────────────────────────────────────────────────
    print(f"       Searching IndiaCode…")
    try:
        sr = search_act(act_name)
    except Exception as e:
        msg = f"Search exception: {e}"
        print(f"       ✗ FAILED: {msg}")
        result.update(status="failed", error=msg)
        return result

    if sr.status == "not_found":
        msg = sr.error_message or "No match found on IndiaCode"
        print(f"       ✗ NOT FOUND: {msg}")
        result.update(status="not_found", error=msg)
        return result

    if sr.status == "failed":
        msg = sr.error_message or "Search request failed"
        print(f"       ✗ SEARCH FAILED: {msg[:120]}")
        result.update(status="failed", error=msg)
        return result

    # Could be "matched" (score ≥ 85) or "needs_review" (60-84)
    best = sr.best_match
    print(f"       ✓ Matched: '{best.title}' (score={best.confidence_score})")
    result.update(
        matched_title=best.title,
        handle_id=best.handle_id,
        confidence_score=best.confidence_score,
    )

    if sr.status == "needs_review":
        print(f"       ⚠ Low confidence ({best.confidence_score}) — downloading anyway for review")

    # ── 2. Download PDF ────────────────────────────────────────────────────────
    print(f"       Downloading PDF (handle_id={best.handle_id})…")
    time.sleep(REQUEST_DELAY)
    try:
        dl = download_act_pdf(best.handle_id)
    except Exception as e:
        msg = f"Download exception: {e}\n{traceback.format_exc()}"
        print(f"       ✗ DOWNLOAD EXCEPTION: {e}")
        result.update(status="failed", error=msg)
        return result

    if dl.status == "pdf_unavailable":
        msg = dl.error_message or "PDF not available — Act under updation on IndiaCode"
        print(f"       ~ PDF UNAVAILABLE: {msg}")
        result.update(status="pdf_unavailable", error=msg)
        return result

    if dl.status != "completed" or not dl.pdf_bytes:
        msg = dl.error_message or "Download returned no bytes"
        print(f"       ✗ DOWNLOAD FAILED: {msg[:120]}")
        result.update(status="failed", error=msg)
        return result

    pdf_size = len(dl.pdf_bytes)
    if pdf_size < MIN_PDF_SIZE:
        msg = f"PDF too small ({pdf_size} bytes) — likely an error page"
        print(f"       ✗ PDF TOO SMALL: {msg}")
        result.update(status="failed", error=msg)
        return result

    print(f"       ✓ Downloaded: {pdf_size:,} bytes")

    # ── 3. Save to disk ────────────────────────────────────────────────────────
    fname = f"{best.handle_id}_{_safe_filename(best.title)}.pdf"
    out_path = os.path.join(OUTPUT_DIR, fname)
    with open(out_path, "wb") as f:
        f.write(dl.pdf_bytes)

    elapsed = time.time() - t0
    print(f"       ✓ Saved: {fname}  ({elapsed:.1f}s)")
    result.update(status="completed", pdf_bytes=pdf_size, saved_path=out_path)
    return result


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Download legal act PDFs from IndiaCode to downloaded_acts/"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Only process the first N acts from acts.txt (0 = all)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the plan and exit — no HTTP requests or file writes"
    )
    args = parser.parse_args()

    # ── Setup ──────────────────────────────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 70)
    print("  LexAI — IndiaCode Batch PDF Downloader")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Output  : {OUTPUT_DIR}")
    print("=" * 70)

    # ── Load acts ──────────────────────────────────────────────────────────────
    all_acts = _parse_acts_file()
    if args.limit > 0:
        all_acts = all_acts[: args.limit]

    total = len(all_acts)
    print(f"\n[Acts] {total} acts to process")

    if args.dry_run:
        print("\n[DRY RUN] Would process:")
        for i, name in enumerate(all_acts, 1):
            print(f"  {i:>3}. {name}")
        print(f"\nTotal: {total} acts")
        return

    # ── Check which are already downloaded ────────────────────────────────────
    existing_pdfs = {f for f in os.listdir(OUTPUT_DIR) if f.endswith(".pdf")}
    print(f"[Acts] {len(existing_pdfs)} PDFs already in {OUTPUT_DIR} (will be skipped by bulk_ingest.py)")

    # ── Process ────────────────────────────────────────────────────────────────
    results = []
    for i, act_name in enumerate(all_acts, 1):
        r = _download_one(act_name, i, total)
        results.append(r)
        # Small pause between acts to avoid hammering NIC
        time.sleep(REQUEST_DELAY)

    # ── Write CSV summary ──────────────────────────────────────────────────────
    csv_path = os.path.join(OUTPUT_DIR, "download_summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "index", "act_name", "status", "matched_title", "handle_id",
            "confidence_score", "pdf_bytes", "saved_path", "error"
        ])
        writer.writeheader()
        writer.writerows(results)

    # ── Final summary ──────────────────────────────────────────────────────────
    completed  = [r for r in results if r["status"] == "completed"]
    pdf_unavail = [r for r in results if r["status"] == "pdf_unavailable"]
    not_found  = [r for r in results if r["status"] == "not_found"]
    failed     = [r for r in results if r["status"] == "failed"]

    print("\n" + "=" * 70)
    print("  DOWNLOAD SUMMARY")
    print("=" * 70)
    print(f"  Total processed    : {total}")
    print(f"  ✓ Completed         : {len(completed)}")
    print(f"  ~ PDF Unavailable   : {len(pdf_unavail)}  (Act under updation on IndiaCode)")
    print(f"  ✗ Not Found         : {len(not_found)}  (no matching act on IndiaCode)")
    print(f"  ✗ Failed            : {len(failed)}  (download/network error)")
    print(f"\n  CSV summary: {csv_path}")
    print("=" * 70)

    if failed or not_found:
        print("\n  Failed / Not Found:")
        for r in failed + not_found:
            print(f"    [{r['index']:>3}] [{r['status'].upper():<12}] {r['act_name']}")
            if r["error"]:
                print(f"          ↳ {r['error'][:100]}")

    if completed:
        print(f"\n  Total PDFs in {OUTPUT_DIR}:")
        for f_name in sorted(os.listdir(OUTPUT_DIR)):
            if f_name.endswith(".pdf"):
                size = os.path.getsize(os.path.join(OUTPUT_DIR, f_name)) // 1024
                print(f"    {f_name}  ({size} KB)")

    print()
    print("  NEXT STEP:")
    print("  Run:  docker compose exec api python bulk_ingest.py")
    print()


if __name__ == "__main__":
    main()
