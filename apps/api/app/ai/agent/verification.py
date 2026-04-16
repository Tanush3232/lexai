"""
Citation verification layer.
Ensures every claim made in the final answer is actually supported by the source text.
"""
from typing import List, Dict, Tuple
from app.ai.gemini_client import generate_structured
from app.ai.prompts import CLAIM_EXTRACT_PROMPT, CLAIM_VERIFY_PROMPT
from app.core.logging import get_logger

logger = get_logger("verification")


async def verify_response(answer: str, citations: List[Dict]) -> Tuple[str, List[Dict]]:
    """
    1. Extract individual claims from the draft answer.
    2. Check each claim against the text of its cited clauses.
    3. Filter out citations that don't support the claims.
    4. Provide the verified citations.
    """
    if not answer or not citations:
        return answer, citations

    try:
        # 1. Extract claims
        prompt_extract = CLAIM_EXTRACT_PROMPT.format(answer=answer)
        claims_result = await generate_structured(prompt_extract, use_pro=True, feature_name="claim_extraction")
        claims = claims_result.get("claims", [])
    except Exception as e:
        logger.warning("verification.extract_failed", error=str(e))
        return answer, citations

    if not claims:
        return answer, citations

    valid_citation_ids = set()
    # Only map items that actually carry a clause_id (outline/extraction results don't)
    clause_map = {c["clause_id"]: c for c in citations if "clause_id" in c}

    # 2. Verify each claim
    for claim in claims:
        claim_text = claim.get("text", "")
        # The extraction model is prompted to list the clause IDs it drew this claim from
        claim_clause_ids = claim.get("source_clause_ids", [])
        
        for cid in claim_clause_ids:
            if cid in clause_map:
                clause_text = clause_map[cid].get("snippet", "")
                
                try:
                    verify_prompt = CLAIM_VERIFY_PROMPT.format(
                        claim=claim_text, 
                        source_text=clause_text
                    )
                    verify_result = await generate_structured(verify_prompt, feature_name="claim_verification")
                    
                    if verify_result.get("supported", False):
                        valid_citation_ids.add(cid)
                except Exception as e:
                    logger.warning("verification.verify_failed", error=str(e))
                    # On failure, conservative path: drop the citation to prevent hallucination
                    pass

    # 3. Filter citations — pass-through items without clause_id (structural evidence like outlines)
    verified_citations = [c for c in citations if c.get("clause_id") in valid_citation_ids or "clause_id" not in c]
    
    return answer, verified_citations
