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
        elif best.confidence_score >= 60:
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
    Given a handle_id, visit the act detail page, find the PDF bitstream link,
    and download the PDF bytes.
    """
    result = ScrapeResult()
    session = _create_session()

    if not _init_session(session):
        result.status = "failed"
        result.error_message = "Failed to initialize session"
        return result

    # Get act detail page
    detail_url = f"{BASE_URL}/handle/123456789/{handle_id}"
    try:
        resp = session.get(detail_url, timeout=30)
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        result.status = "failed"
        result.error_message = f"Detail page request failed: {e}"
        return result

    # Find PDF bitstream link
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        pdf_href = None
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            # Strip #fragment before extension check (NIC appends #search=...)
            href_no_frag = href.split("#")[0]
            if "/bitstream/" in href and href_no_frag.lower().endswith(".pdf"):
                # Prefer English PDF: filename starts with A not H (Hindi)
                filename = href_no_frag.split("/")[-1].upper()
                if not filename.startswith("H"):
                    pdf_href = href_no_frag
                    break
        # Fallback: any .pdf bitstream
        if not pdf_href:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                href_no_frag = href.split("#")[0]
                if "/bitstream/" in href and href_no_frag.lower().endswith(".pdf"):
                    pdf_href = href_no_frag
                    break

        if not pdf_href:
            # Check if IndiaCode explicitly says the PDF is temporarily unavailable
            page_text = soup.get_text(" ", strip=True).lower()
            if "under updation" in page_text or "will be uploaded shortly" in page_text:
                result.status = "pdf_unavailable"
                result.error_message = "PDF not available – Act under updation"
            else:
                result.status = "failed"
                result.error_message = "No PDF bitstream link found on detail page"
            result.indiacode_url = detail_url
            return result

        # Build full URL
        if pdf_href.startswith("/"):
            pdf_url = f"{BASE_URL}{pdf_href}"
        elif pdf_href.startswith("http"):
            pdf_url = pdf_href
        else:
            pdf_url = f"{BASE_URL}/{pdf_href}"

        result.bitstream_url = pdf_url
        result.indiacode_url = detail_url
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        result.status = "failed"
        result.error_message = f"PDF link extraction failed: {e}"
        return result

    # Download PDF
    try:
        pdf_resp = session.get(pdf_url, stream=True, timeout=120)
        pdf_resp.raise_for_status()
        result.pdf_bytes = pdf_resp.content
        result.status = "completed"
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        result.status = "failed"
        result.error_message = f"PDF download failed: {e}"

    return result


def search_and_get_candidates(act_name: str, top_n: int = 3) -> List[SearchCandidate]:
    """
    Search for an act and return top N candidates with scores.
    Used by the /api/acts/add endpoint.
    """
    result = search_act(act_name)
    return result.candidates[:top_n]
