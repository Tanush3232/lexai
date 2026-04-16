"""
Confidence evaluation and retry logic for the ReAct agent.
"""
from enum import Enum
from typing import List, Dict, Any

class ConfidenceLevel(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"

def compute_confidence(hits: List[Dict], is_vectorless: bool = False) -> ConfidenceLevel:
    """
    Compute confidence based on retrieved clause evidence.
    """
    if is_vectorless:
        return ConfidenceLevel.HIGH if hits else ConfidenceLevel.INSUFFICIENT

    if not hits:
        return ConfidenceLevel.LOW

    # Score analysis
    highest_score = max([h.get("score", 0.0) for h in hits])
    
    if highest_score > 0.05 and len(hits) >= 2:
        # RRF scores can be small (e.g. 1/61 + 1/61 = 0.032). 
        # A hit in top 3 of both sources will have score > 0.03.
        return ConfidenceLevel.HIGH
    elif highest_score > 0.015:
        # Hit in top 10 of at least one source
        return ConfidenceLevel.MEDIUM
    else:
        return ConfidenceLevel.LOW

def should_retry(confidence: ConfidenceLevel, current_attempt: int, max_retries: int) -> bool:
    """Determine if retrieval should be retried based on confidence and attempt count."""
    return confidence == ConfidenceLevel.LOW and current_attempt < max_retries
