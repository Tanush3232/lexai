import re
from typing import List


_LEADING_PREFIX_PATTERN = re.compile(r"^(?:the\s+indian\s+|indian\s+|the\s+)", re.IGNORECASE)


def strip_leading_legal_prefixes(title: str) -> str:
    value = re.sub(r"\s+", " ", title or "").strip()
    while value:
        updated = _LEADING_PREFIX_PATTERN.sub("", value, count=1).strip()
        if updated == value:
            break
        value = updated
    return value


def normalize_legal_act_title(title: str) -> str:
    value = strip_leading_legal_prefixes(title)
    value = value.lower()
    # Replace punctuation with space (not empty string) so "Act,1962" → "act 1962" not "act1962"
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def strip_year(title: str) -> str:
    """Remove trailing year like ', 1948' or ' 1948' from act title."""
    return re.sub(r",?\s*\b(1[0-9]{3}|20[0-9]{2})\b\s*$", "", title).strip()


def build_legal_act_search_queries(title: str) -> List[str]:
    original = re.sub(r"\s+", " ", title or "").strip()
    base = strip_leading_legal_prefixes(original)

    # Year-stripped variants: IndiaCode search often fails when year is in the query
    original_no_year = strip_year(original)
    base_no_year = strip_year(base)

    variants = [
        # Year-stripped first — NIC's search engine finds acts more reliably without year
        f"The {base_no_year}" if base_no_year else "",
        f"The Indian {base_no_year}" if base_no_year else "",
        f"Indian {base_no_year}" if base_no_year else "",
        original_no_year,
        base_no_year,
        # Then with year for scoring precision
        original,
        base,
        f"The Indian {base}" if base else "",
        f"Indian {base}" if base else "",
        f"The {base}" if base else "",
    ]

    seen = set()
    ordered: List[str] = []
    for variant in variants:
        candidate = re.sub(r"\s+", " ", variant).strip()
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)
    return ordered