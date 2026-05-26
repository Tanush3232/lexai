"""
Draft Redaction Service
=======================
Server-side PII masking before any AI call.
Replaces sensitive values with stable tokens, stores the reverse map per session.

Patterns masked:
  - Email addresses
  - Indian/international phone numbers
  - GSTIN (Goods and Services Tax Identification Number)
  - PAN (Permanent Account Number)
  - Aadhaar numbers
  - Company legal suffixes: Pvt Ltd, Private Limited, Inc, Corp, LLP, LLC, Ltd
  - Generic identifiers: CIN, DIN numbers

Token format: {{TYPE_INDEX}}  e.g. {{EMAIL_1}}, {{COMPANY_1}}

Role-Preserving Masking:
  When role_hints is provided as {original_name: "ROLE_LABEL"},
  company tokens use the role label instead of COMPANY:
    {{PARTY_SERVICE_PROVIDER_1}} instead of {{COMPANY_1}}
  This lets Claude Opus understand party relationships (who pays, who performs)
  for correct indemnity/liability clause drafting without seeing real names.
"""
import re
from typing import Tuple, Dict, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns — ordered by specificity (most specific first)
# ─────────────────────────────────────────────────────────────────────────────

_PATTERNS = [
    # Email
    ("EMAIL",    re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)),
    # GSTIN: 15-char alphanumeric starting with 2 digits (state code)
    ("GSTIN",    re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b")),
    # PAN: 5 letters, 4 digits, 1 letter
    ("PAN",      re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b")),
    # Aadhaar: 12 digits (with optional spaces every 4)
    ("AADHAAR",  re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    # CIN: Corporate Identity Number
    ("CIN",      re.compile(r"\b[LUu]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")),
    # Indian phone: +91 prefix or 10-digit starting with 6-9
    ("PHONE",    re.compile(r"(?:\+91[\s\-]?)?[6-9]\d{9}\b")),
    # Generic phone: international format
    ("PHONE",    re.compile(r"\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{4}")),
    # Company names: word(s) followed by legal suffix
    ("COMPANY",  re.compile(
        r"\b[A-Z][A-Za-z0-9\s&,\.]+?\s(?:Private\s+Limited|Pvt\.?\s*Ltd\.?|Limited|"
        r"Incorporated|Inc\.?|Corporation|Corp\.?|LLP|LLC|Ltd\.?)\b",
        re.IGNORECASE
    )),
]


def _role_to_token(role: str) -> str:
    """Sanitize a role string into a safe token segment.
    e.g. "Service Provider" → "SERVICE_PROVIDER"
    """
    clean = re.sub(r"[^A-Za-z0-9\s]", "", role).strip().upper()
    return re.sub(r"\s+", "_", clean) or "PARTY"


def redact(
    text: str,
    role_hints: Optional[Dict[str, str]] = None,
) -> Tuple[str, Dict[str, str]]:
    """
    Redact sensitive values from text.

    Args:
        text:       The raw text to redact.
        role_hints: Optional mapping of {original_name_fragment: role_label}.
                    e.g. {"Acme Corp": "SERVICE_PROVIDER", "Client Inc": "CLIENT"}
                    When a matched company name contains a key from role_hints,
                    the token becomes {{PARTY_SERVICE_PROVIDER_1}} instead of {{COMPANY_1}}.

    Returns:
        (redacted_text, token_map) where token_map maps token → original_value.
    """
    token_map: Dict[str, str] = {}
    counters: Dict[str, int] = {}
    result = text

    # Build a quick lookup: lowercase fragment → role token segment
    _role_lookup: Dict[str, str] = {}
    if role_hints:
        for name_fragment, role in role_hints.items():
            _role_lookup[name_fragment.lower()] = _role_to_token(role)

    # We do multiple passes so that GSTIN/PAN are masked before generic word patterns
    for label, pattern in _PATTERNS:
        def _replace(m: re.Match, _label: str = label) -> str:
            original = m.group(0)
            # Check if already tokenized
            if original.startswith("{{") and original.endswith("}}"):
                return original
            # Check if same value already has a token (dedup)
            for tok, val in token_map.items():
                if val == original:
                    return tok

            # Resolve label: for COMPANY matches, check role_hints
            effective_label = _label
            if _label == "COMPANY" and _role_lookup:
                original_lower = original.lower()
                for frag, role_seg in _role_lookup.items():
                    if frag in original_lower:
                        effective_label = f"PARTY_{role_seg}"
                        break
                else:
                    effective_label = "COMPANY"

            counters[effective_label] = counters.get(effective_label, 0) + 1
            token = f"{{{{{effective_label}_{counters[effective_label]}}}}}"
            token_map[token] = original
            return token

        result = pattern.sub(_replace, result)

    return result, token_map


def restore(text: str, token_map: Dict[str, str]) -> str:
    """Re-insert original values from token map."""
    result = text
    for token, original in token_map.items():
        result = result.replace(token, original)
    return result


def partial_restore(text: str, token_map: Dict[str, str], safe_keys: list) -> str:
    """
    Selectively restore only the tokens that are safe to expose.
    safe_keys: list of token keys to restore (e.g. ["{{COMPANY_1}}", "{{EMAIL_1}}"])
    Used when de-redacting only variable slots in locked boilerplate.
    """
    result = text
    for token in safe_keys:
        if token in token_map:
            result = result.replace(token, token_map[token])
    return result
