"""
India Code scraper — searches and downloads legal act PDFs from indiacode.nic.in.

Key rules:
  - Always create a fresh requests.Session per act (NIC requires cookies)
  - Hit homepage first to seed session cookies
  - 500ms sleep between every request
  - Use rapidfuzz for fuzzy title matching
"""
import re
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from app.core.logging import get_logger
from app.core.title_matching import build_legal_act_search_queries, normalize_legal_act_title

logger = get_logger("indiacode_scraper")

BASE_URL = "https://www.indiacode.nic.in"
SEARCH_PATH = "/handle/123456789/1362/simple-search"
# DSpace collection handle for Central Acts — produces more relevant results
# than searchradio=acts (which can omit acts not in the top-10 relevance window)
SEARCH_LOCATION = "/"  # All of Indiacode — broadest scope, covers all legislation
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY = 0.5  # seconds between requests


@dataclass
class SearchCandidate:
    title: str
    handle_id: str
    act_number: Optional[str] = None
    enactment_date: Optional[str] = None
    confidence_score: float = 0.0


@dataclass
class ScrapeResult:
    status: str = "pending"  # completed | failed | not_found | needs_review
    candidates: List[SearchCandidate] = field(default_factory=list)
    best_match: Optional[SearchCandidate] = None
    bitstream_url: Optional[str] = None
    pdf_bytes: Optional[bytes] = None
    error_message: Optional[str] = None
    indiacode_url: Optional[str] = None


def _normalize_title(title: str) -> str:
    """Normalize act title for fuzzy matching."""
    return normalize_legal_act_title(title)


def _extract_year(text: str) -> Optional[str]:
    """Extract the first 4-digit year from a string, if present."""
    m = re.search(r'\b(1[0-9]{3}|20[0-9]{2})\b', text)
    return m.group(1) if m else None


def _extract_candidates_from_response(resp_text: str, act_name: str) -> List[SearchCandidate]:
    soup = BeautifulSoup(resp_text, "html.parser")
    table = soup.find("table", class_="table") or soup.find("table")
    if not table:
        return []

    rows = table.find_all("tr")[1:]
    if not rows:
        return []

    normalized_query = _normalize_title(act_name)
    query_year = _extract_year(act_name)
    candidates: List[SearchCandidate] = []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        enactment_date = cells[0].get_text(separator=" ", strip=True)
        act_number = cells[1].get_text(separator=" ", strip=True)
        raw_title = cells[2].get_text(separator=" ", strip=True)
        short_title = re.sub(r'\s+', ' ', raw_title).strip()

        view_link = None
        for a_tag in row.find_all("a", href=True):
            href = a_tag["href"]
            if "/handle/123456789/" in href:
                view_link = href
                break

        if not view_link:
            continue

        handle_match = re.search(r'/handle/123456789/(\d+)', view_link)
        if not handle_match:
            continue
        handle_id = handle_match.group(1)

        normalized_result = _normalize_title(short_title)
        score = fuzz.token_sort_ratio(normalized_query, normalized_result)

        # Year-mismatch penalty: if both query and candidate have a year and
        # they differ, the candidate cannot be a true match — cap at 60 so
        # it can never cross the 85-threshold into auto-completion.
        candidate_year = _extract_year(short_title)
        if query_year and candidate_year and query_year != candidate_year:
            score = min(score, 60)

        candidates.append(
            SearchCandidate(
                title=short_title,
                handle_id=handle_id,
                act_number=act_number,
                enactment_date=enactment_date,
                confidence_score=round(score, 2),
            )
        )

    return candidates


def _create_session() -> requests.Session:
    """Create a new session with correct User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _init_session(session: requests.Session) -> bool:
    """Hit homepage to seed NIC session cookies. Returns True on success."""
    try:
        resp = session.get(BASE_URL, timeout=30)
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return True
    except Exception as e:
        logger.error("scraper.init_session_failed", error=str(e))
        return False


def search_act(act_name: str) -> ScrapeResult:
    """
    Search indiacode.nic.in for the given act name.
    Returns a ScrapeResult with candidates and fuzzy-match scores.
    Does NOT download the PDF — call download_act_pdf() for that.
    """
    result = ScrapeResult()
    session = _create_session()

    if not _init_session(session):
        result.status = "failed"
        result.error_message = "Failed to initialize session with indiacode.nic.in"
        return result

    search_url = f"{BASE_URL}{SEARCH_PATH}"
    last_request_error = None
    merged_candidates = {}

    try:
        for query in build_legal_act_search_queries(act_name):
            try:
                params = {
                    "query": query,
                    "location": SEARCH_LOCATION,
                    "rpp": "20",
                    "sort_by": "score",
                    "order": "desc",
                }
                resp = session.get(search_url, params=params, timeout=30)
                resp.raise_for_status()
                time.sleep(REQUEST_DELAY)
                if not result.indiacode_url:
                    result.indiacode_url = resp.url

                for candidate in _extract_candidates_from_response(resp.text, act_name):
                    existing = merged_candidates.get(candidate.handle_id)
                    if existing is None or candidate.confidence_score > existing.confidence_score:
                        merged_candidates[candidate.handle_id] = candidate
            except Exception as e:
                last_request_error = e

        result.candidates = sorted(
            merged_candidates.values(),
            key=lambda c: c.confidence_score,
            reverse=True,
        )

        if not result.candidates:
            if last_request_error:
                result.status = "failed"
                result.error_message = f"Search request failed: {last_request_error}"
            else:
                result.status = "not_found"
                result.error_message = "No matching candidates found in search results"
            return result

        best = result.candidates[0]

        if best.confidence_score >= 85:
            result.best_match = best
            result.status = "matched"
        elif best.confidence_score >= 50:  # was 60 — try these too, user can verify
            result.best_match = best
            result.status = "needs_review"
        else:
            result.status = "not_found"
            result.error_message = f"Best match score too low: {best.confidence_score}"

    except Exception as e:
        result.status = "failed"
        result.error_message = f"Result parsing failed: {e}\n{traceback.format_exc()}"

    return result


def download_act_pdf(handle_id: str) -> ScrapeResult:
    """
    Given a handle_id, visit the act detail page, collect ALL bitstream PDF links,
    then try them in priority order: English first, then null/numeric, then Hindi.
    Validates PDF by magic bytes. Retries up to 3x on network errors.
    """
    import urllib.parse as _urlparse

    result = ScrapeResult()
    session = _create_session()

    if not _init_session(session):
        result.status = "failed"
        result.error_message = "Failed to initialize session"
        return result

    detail_url = f"{BASE_URL}/handle/123456789/{handle_id}"
    try:
        resp = session.get(detail_url, timeout=30)
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        result.status = "failed"
        result.error_message = f"Detail page request failed: {e}"
        return result

    result.indiacode_url = detail_url

    # ── Collect ALL bitstream links ─────────────────────────────────────────
    soup = BeautifulSoup(resp.text, "html.parser")
    seen_hrefs: set = set()
    english_pdfs: list = []  # highest priority
    hindi_pdfs: list = []    # last resort
    fallback_bitstreams: list = []  # bitstream links without .pdf extension

    for a_tag in soup.find_all("a", href=True):
        href_raw = a_tag["href"].strip()
        href_clean = href_raw.split("#")[0].strip()  # strip #fragment
        if "/bitstream/" not in href_clean or href_clean in seen_hrefs:
            continue
        seen_hrefs.add(href_clean)

        href_lower = href_clean.lower()
        if href_lower.endswith(".pdf"):
            # Decode URL for accurate filename check
            raw_filename = _urlparse.unquote(href_clean.split("/")[-1]).upper()
            # NIC convention: Hindi PDFs → filename starts with H followed by
            # underscore or digit (H_ACT_123.pdf, H123.pdf). 'HO', 'HOME' etc are not Hindi.
            if re.match(r'^H[_\d]', raw_filename) or raw_filename.upper().startswith("HINDI"):
                hindi_pdfs.append(href_clean)
            else:
                # English, null.pdf, numeric, A_ACT_123.pdf → all treated as English
                english_pdfs.append(href_clean)
        elif "/bitstream/" in href_clean:
            # No .pdf extension — might still be a PDF (some NIC links lack extension)
            fallback_bitstreams.append(href_clean)

    # Order: English → fallback → Hindi
    to_try = english_pdfs + fallback_bitstreams + hindi_pdfs

    if not to_try:
        page_text = soup.get_text(" ", strip=True).lower()
        if "under updation" in page_text or "will be uploaded shortly" in page_text:
            result.status = "pdf_unavailable"
            result.error_message = "PDF not available – Act under updation"
        else:
            result.status = "failed"
            result.error_message = "No PDF bitstream link found on detail page"
        return result

    # ── Try each link, validate PDF, retry on network error ────────────────
    last_error = None
    for pdf_href in to_try:
        if pdf_href.startswith("/"):
            pdf_url = f"{BASE_URL}{pdf_href}"
        elif pdf_href.startswith("http"):
            pdf_url = pdf_href
        else:
            pdf_url = f"{BASE_URL}/{pdf_href}"

        for attempt in range(3):
            try:
                time.sleep(REQUEST_DELAY * (attempt + 1))
                pdf_resp = session.get(pdf_url, stream=False, timeout=120)
                if pdf_resp.status_code == 404:
                    last_error = f"404 for {pdf_url}"
                    break  # dead link — try next
                pdf_resp.raise_for_status()
                pdf_data = pdf_resp.content
                content_type = pdf_resp.headers.get("content-type", "").lower()

                # Validate: magic bytes %PDF OR content-type says pdf, AND reasonable size
                is_pdf = (
                    pdf_data[:4] == b"%PDF"
                    or "pdf" in content_type
                )
                if is_pdf and len(pdf_data) > 1_000:
                    result.pdf_bytes = pdf_data
                    result.bitstream_url = pdf_url
                    result.status = "completed"
                    logger.info(
                        "scraper.pdf_downloaded",
                        handle_id=handle_id,
                        url=pdf_url,
                        bytes=len(pdf_data),
                    )
                    return result
                else:
                    last_error = f"Invalid PDF content from {pdf_url} (size={len(pdf_data)}, ct={content_type})"
                    break  # bad content from this URL — try next
            except Exception as e:
                last_error = str(e)
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    logger.warning("scraper.retry", attempt=attempt + 1, url=pdf_url, error=str(e))
                # on last attempt: fall through to try next href

    result.status = "failed"
    result.error_message = (
        f"All {len(to_try)} PDF link(s) tried, none produced valid PDF. "
        f"Last error: {last_error}"
    )
    return result


def search_and_get_candidates(act_name: str, top_n: int = 3) -> List[SearchCandidate]:
    """
    Search for an act and return top N candidates with scores.
    Used by the /api/acts/add endpoint.
    """
    result = search_act(act_name)
    return result.candidates[:top_n]
