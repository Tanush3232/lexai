"""
ReAct Agent execution loop.
THINK -> ACT -> OBSERVE -> SYNTHESIZE -> VERIFY
"""
import json
import re
from typing import Dict, List, Any, Tuple
from app.ai.gemini_client import generate_structured
from app.ai.prompts import REACT_THINK_PROMPT, REACT_SYNTHESIZE_PROMPT, VECTORLESS_ANALYSIS_PROMPT, TREE_NAVIGATION_PROMPT
from app.ai.agent.confidence import compute_confidence, should_retry, ConfidenceLevel
from app.ai.agent.verification import verify_response
from app.ai.agent.tools import (
    search_clauses,
    get_document_outline,
    get_section,
    traverse_references,
    search_definitions,
    extract_fields,
    compare_clauses,
    get_full_document,
    get_document_tree_data,
    get_sections_by_page_range,
)
from app.core.logging import get_logger

logger = get_logger("agent.react")

# Common stop-words to skip when keyword-matching evidence to the question
_STOPWORDS = {
    'the','a','an','is','are','was','were','what','which','who','how','why','when',
    'where','in','on','at','to','for','of','and','or','but','not','this','that',
    'it','be','do','have','i','me','my','we','you','he','she','they','its','their',
    'about','from','with','tell','give','show','please','can','could','would','will',
}


def _filter_relevant_evidence(prior: List[Dict], question: str, top_k: int = 20) -> List[Dict]:
    """
    Return only the evidence items whose text/snippet contains keywords from the question.
    Returns [] when nothing matches (new topic) so the agent does a fresh search instead
    of hallucinating answers from unrelated prior evidence.
    """
    if not prior:
        return []
    q_tokens = set(re.findall(r"[a-z]{3,}", question.lower())) - _STOPWORDS
    if not q_tokens:
        return prior[:top_k]  # Very short query — pass everything
    scored = []
    for item in prior:
        text = " ".join([
            str(item.get("snippet") or ""),
            str(item.get("text") or ""),
            str(item.get("section_heading") or ""),
            str(item.get("clause_type") or ""),
        ]).lower()
        score = sum(1 for tok in q_tokens if tok in text)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


class ReActAgent:
    def __init__(self, doc_ids: List[str], folder_ids: List[str],
                 prior_evidence: List[Dict] | None = None, question: str = ""):
        self.doc_ids = doc_ids
        self.folder_ids = folder_ids
        # Filter prior evidence to items relevant to THIS question.
        # Harvey AI evidence is irrelevant when asking about sick leave —
        # filtering returns [] and the agent does a fresh search.
        # Follow-up questions on the same topic get the relevant items back
        # so THINK can SYNTHESIZE without re-searching.
        if prior_evidence and question:
            self.evidence = _filter_relevant_evidence(prior_evidence, question)
            if self.evidence:
                logger.info("agent.prior_evidence_loaded",
                            total_prior=len(prior_evidence), relevant=len(self.evidence),
                            question_preview=question[:80])
            else:
                logger.info("agent.prior_evidence_irrelevant_new_search",
                            total_prior=len(prior_evidence), question_preview=question[:80])
        else:
            self.evidence = list(prior_evidence) if prior_evidence else []
        self.tool_log: List[str] = []
        
    async def run(self, question: str, previous_messages: str = "") -> Dict[str, Any]:
        """Main entrypoint for the agent."""

        # 1. Single-document: try vectorless analysis first (fastest + most thorough)
        logger.info(
            "agent.run.start",
            doc_count=len(self.doc_ids),
            folder_count=len(self.folder_ids),
            doc_ids=self.doc_ids,
        )
        if len(self.doc_ids) == 1:
            logger.info("agent.vectorless_attempt", doc_id=self.doc_ids[0])
            full_text = await get_full_document(self.doc_ids[0])
            if full_text is None:
                # Document is too large for vectorless.
                # Try Page-Index RAG if a tree has been built for this doc.
                logger.info("agent.vectorless_skipped.too_large", doc_id=self.doc_ids[0])
                tree_data = await get_document_tree_data(self.doc_ids[0])
                if tree_data and tree_data.get("tree"):
                    logger.info("agent.page_index_attempt", doc_id=self.doc_ids[0])
                    return await self._run_page_index(question, tree_data)
            elif full_text == "":
                # Document has NO clauses — Celery worker hasn't indexed it yet
                logger.warning("agent.document_not_indexed", doc_id=self.doc_ids[0])
                return {
                    "answer": (
                        "This document has not been indexed yet. "
                        "Please wait a moment for the background indexing to complete "
                        "(the status will change from 'processing' to 'indexed'), then try again."
                    ),
                    "sources": [],
                    "confidence": "insufficient",
                }
            else:
                logger.info("agent.full_doc_fetched", doc_id=self.doc_ids[0])
                return await self._run_vectorless(question, full_text)

        # 2. Standard ReAct retrieval loop (multi-doc or folder scope)
        return await self._run_react_loop(question, previous_messages)

    async def _run_react_loop(self, question: str, previous_messages: str) -> Dict[str, Any]:
        """ReAct loop: THINK → ACT → OBSERVE → SYNTHESIZE → VERIFY."""
        from app.core.config import settings
        max_steps = settings.AGENT_MAX_STEPS
        retries = 0
        step = 0

        while step < max_steps:
            step += 1

            # For THINK we only need brief context to decide the next action.
            # Limit to top-8 items × 100-char snippets to keep the Flash prompt small.
            think_evidence = [
                {
                    "clause_id": e.get("clause_id"),
                    "snippet": str(e.get("snippet") or "")[:100],
                    "score": e.get("score"),
                }
                for e in self.evidence[:8]
            ]
            # Tool-log: only the last 8 entries, each capped at 120 chars to avoid bloat.
            trimmed_tool_log = [
                t[:120] + ("…" if len(t) > 120 else "")
                for t in self.tool_log[-8:]
            ]

            state = {
                "question": question,
                "previous_messages": previous_messages,
                "doc_ids": self.doc_ids,
                "evidence": json.dumps(think_evidence) if think_evidence else "None yet",
                "tool_history": "\n".join(trimmed_tool_log) if trimmed_tool_log else "None",
            }

            # THINK — use Flash model (routing decision only, not legal reasoning)
            try:
                think_prompt = REACT_THINK_PROMPT.format(**state)
                logger.info("agent.think.start", step=step)
                decision = await generate_structured(think_prompt, use_pro=False, feature_name="agent_think")
                logger.info("agent.think.decision", step=step, action=decision.get("action"), tool=decision.get("tool"))
            except Exception as e:
                logger.error("agent.think_failed", step=step, error=str(e), exc_info=True)
                break

            action = decision.get("action")
            reasoning = decision.get("reasoning", "")
            self.tool_log.append(f"THINK[{step}]: {reasoning}")

            if action == "SYNTHESIZE":
                logger.info("agent.think.synthesize_decided", step=step)
                break

            # ACT
            tool_name = decision.get("tool")
            args = decision.get("args", {})
            self.tool_log.append(f"ACT[{step}]: {tool_name} args={args}")
            logger.info("agent.act", step=step, tool=tool_name, args=args)

            # OBSERVE
            try:
                result = await self._execute_tool(tool_name, args)
                if result:
                    self._merge_evidence(result)
                    logger.info("agent.observe", step=step, new_items=len(result), total_evidence=len(self.evidence))
                else:
                    logger.info("agent.observe.empty", step=step, tool=tool_name)
            except Exception as e:
                logger.error("agent.tool_error", step=step, tool=tool_name, error=str(e), exc_info=True)
                self.tool_log.append(f"OBSERVE[{step}] ERROR: {str(e)}")

            # CONFIDENCE CHECK
            confidence = compute_confidence(self.evidence)
            if should_retry(confidence, retries, settings.AGENT_MAX_RETRIES):
                retries += 1
                self.tool_log.append(f"RETRY[{retries}]: confidence={confidence.name}")

        # SYNTHESIZE
        confidence = compute_confidence(self.evidence)
        logger.info("agent.synthesize.start", evidence_count=len(self.evidence), confidence=confidence.name)

        if confidence == ConfidenceLevel.INSUFFICIENT:
            # Fallback: if we have any evidence at all, try anyway; only give up if truly empty
            if not self.evidence:
                logger.warning("agent.synthesize.insufficient_evidence")
                return {
                    "answer": (
                        "No relevant clauses were found in the selected documents for this question. "
                        "Please check that the documents have been successfully indexed, "
                        "or broaden your scope."
                    ),
                    "sources": [],
                    "confidence": "insufficient",
                }
            logger.warning("agent.synthesize.low_confidence_proceeding", evidence_count=len(self.evidence))

        # Cap evidence for SYNTHESIZE: top 15 items, 600-char snippets maximum.
        # Full evidence JSON with 22+ items causes 54k+ char prompts → timeouts.
        synth_evidence = []
        for item in self.evidence[:15]:
            e = dict(item)
            if isinstance(e.get("snippet"), str) and len(e["snippet"]) > 600:
                e["snippet"] = e["snippet"][:600] + "…"
            synth_evidence.append(e)

        synthesize_prompt = REACT_SYNTHESIZE_PROMPT.format(
            question=question,
            evidence=json.dumps(synth_evidence, indent=2),
        )

        try:
            logger.info("agent.synthesize.calling_gemini")
            draft_answer_obj = await generate_structured(synthesize_prompt, use_pro=True, feature_name="agent_synthesis")
            draft_answer = draft_answer_obj.get("answer", "").strip()
            if not draft_answer:
                raise ValueError("Synthesis returned an empty answer.")
            logger.info("agent.synthesize.success", answer_len=len(draft_answer))
        except Exception as e:
            logger.error("agent.synthesize_failed", error=str(e), exc_info=True)
            return {
                "answer": f"An error occurred while synthesising the answer: {str(e)}",
                "sources": [],
                "confidence": "low",
            }

        return {
            "answer": draft_answer,
            "sources": self.evidence,
            "confidence": confidence.name.lower(),
        }

    def _pick_doc_id(self, section_id: str = "") -> str:
        """Pick a doc_id for single-doc tools. In folder scope, infer from evidence."""
        if self.doc_ids:
            return self.doc_ids[0]
        # Folder scope: try to infer doc_id from the section_id prefix or evidence
        if section_id:
            for e in self.evidence:
                if e.get("section_id", "") == section_id or \
                   str(e.get("clause_id", "")).startswith(section_id.split("_s")[0]):
                    return e.get("doc_id", "")
        # Fall back to the doc_id of the highest-scored evidence item
        scored = [e for e in self.evidence if e.get("doc_id") and e.get("score", 0) > 0]
        if scored:
            return max(scored, key=lambda x: x.get("score", 0))["doc_id"]
        return ""

    async def _execute_tool(self, tool_name: str, args: Dict) -> List[Dict]:
        if tool_name == "search_clauses":
            return await search_clauses(
                query=args.get("query", ""),
                doc_ids=self.doc_ids,
                clause_types=args.get("clause_types"),
            )
        elif tool_name == "get_document_outline":
            doc_id = self._pick_doc_id()
            if not doc_id:
                return []
            res = await get_document_outline(doc_id)
            return [{"type": "outline", "data": res}]
        elif tool_name == "get_section":
            section_id = args.get("section_id", "")
            doc_id = self._pick_doc_id(section_id)
            if not doc_id:
                return []
            return await get_section(doc_id, section_id)
        elif tool_name == "traverse_references":
            return traverse_references(args.get("clause_id", ""))
        elif tool_name == "search_definitions":
            return await search_definitions(args.get("term", ""), self.doc_ids)
        elif tool_name == "extract_fields":
            doc_id = self._pick_doc_id()
            if not doc_id:
                return []
            res = await extract_fields(doc_id, args.get("fields", []))
            return [{"type": "extraction", "data": res}]
        elif tool_name == "compare_clauses":
            res = await compare_clauses(args.get("clause_id_a", ""), args.get("clause_id_b", ""))
            return [{"type": "comparison", "data": res}]
        else:
            return []
            
    def _merge_evidence(self, new_hits: List[Dict]):
        # Keep track of unique clause IDs
        existing_ids = {e.get("clause_id") for e in self.evidence if "clause_id" in e}
        
        for hit in new_hits:
            if "payload" in hit:
                # From vector store
                cid = hit["payload"].get("clause_id")
                if cid and cid not in existing_ids:
                    self.evidence.append({
                        "clause_id": cid,
                        "doc_id": hit["payload"].get("doc_id"),
                        "section_id": hit["payload"].get("section_id"),
                        "snippet": hit["payload"].get("text", ""),
                        "score": hit.get("score", 0),
                        "page": hit["payload"].get("page_start"),
                    })
                    existing_ids.add(cid)
            elif "id" in hit:
                # From graph/database direct mapping
                cid = hit["id"]
                if cid not in existing_ids:
                    self.evidence.append({
                        "clause_id": cid,
                        "doc_id": hit.get("doc_id", self.doc_ids[0] if self.doc_ids else ""),
                        "snippet": hit.get("text", ""),
                    })
                    existing_ids.add(cid)
            else:
                # generic data like outline or extraction
                self.evidence.append(hit)

    async def _run_page_index(self, question: str, tree_data: Dict) -> Dict[str, Any]:
        """
        Page-Index RAG path — for large documents (>50 pages) that have a pre-built tree.

        Algorithm:
          1. Flash selects 2-5 relevant tree nodes by reasoning over titles + summaries.
          2. For each selected node, fetch clauses within that page range from Postgres.
          3. Pro synthesises the final answer from extracted section text.

        This gives similar quality to vectorless (full doc reasoning) but scales to
        arbitrarily large documents by reading only the relevant pages.
        """
        import json as _json
        doc_id = self.doc_ids[0]
        tree_nodes = tree_data.get("tree", [])

        logger.info("agent.page_index.start", doc_id=doc_id, nodes=len(tree_nodes))

        # ── Step 1: Navigate tree with Flash ─────────────────────
        # Flatten tree for the prompt (titles + summaries only — keep it small)
        def _flatten(nodes, depth=0):
            flat = []
            for n in nodes:
                flat.append({
                    "id": n.get("id"),
                    "title": n.get("title", ""),
                    "page_range": n.get("page_range", [1, 1]),
                    "summary": (n.get("summary") or "")[:150],
                })
                if depth < 2:
                    flat.extend(_flatten(n.get("children", []), depth + 1))
            return flat

        flat_tree = _flatten(tree_nodes)
        try:
            nav_prompt = TREE_NAVIGATION_PROMPT.format(
                question=question,
                tree_json=_json.dumps(flat_tree, indent=2)[:8_000],
            )
            nav_result = await generate_structured(nav_prompt, use_pro=False, feature_name="page_navigation")
            selected_ids = nav_result.get("selected_ids", []) or []
            nav_reasoning = nav_result.get("reasoning", "")
            logger.info(
                "agent.page_index.nav_done",
                doc_id=doc_id,
                selected=selected_ids,
                reasoning=nav_reasoning[:120],
            )
        except Exception as e:
            logger.error("agent.page_index.nav_failed", error=str(e))
            # Fall back to first 5 nodes if navigation fails
            selected_ids = [n["id"] for n in flat_tree[:5]]

        if not selected_ids:
            logger.warning("agent.page_index.no_nodes_selected")
            selected_ids = [n["id"] for n in flat_tree[:5]]

        # Build a map of node id → page_range for all (including children)
        def _build_page_map(nodes):
            m = {}
            for n in nodes:
                m[n.get("id")] = n.get("page_range", [1, 1])
                m.update(_build_page_map(n.get("children", [])))
            return m

        page_map = _build_page_map(tree_nodes)

        # ── Step 2: Extract section text ──────────────────────────
        extracted_texts = []
        for node_id in selected_ids:
            pr = page_map.get(node_id)
            if not pr or len(pr) < 2:
                continue
            text = await get_sections_by_page_range(doc_id, pr[0], pr[1])
            if text:
                extracted_texts.append(text[:6_000])  # cap each section

        if not extracted_texts:
            logger.warning("agent.page_index.no_text_extracted.falling_back")
            return await self._run_react_loop(question, previous_messages="")

        combined_text = "\n\n".join(extracted_texts)[:40_000]

        # ── Step 3: Synthesise with Pro ───────────────────────────
        from app.ai.prompts import VECTORLESS_ANALYSIS_PROMPT
        synth_prompt = VECTORLESS_ANALYSIS_PROMPT.format(
            question=question,
            full_text=combined_text,
        )
        try:
            result = await generate_structured(synth_prompt, use_pro=True, feature_name="page_synthesis")
            answer = result.get("answer", "").strip()
            if not answer:
                raise ValueError("Page-index synthesis returned an empty answer.")

            logger.info("agent.page_index.success", answer_len=len(answer))
            sources = [
                {
                    "clause_id": f"page_index_{nid}",
                    "snippet": f"Section node {nid} (pages {page_map.get(nid, [0, 0])})",
                    "doc_id": doc_id,
                    "page": page_map.get(nid, [1, 1])[0],
                }
                for nid in selected_ids
                if nid in page_map
            ]
            return {"answer": answer, "sources": sources, "confidence": "high"}
        except Exception as e:
            logger.error("agent.page_index.synth_failed", error=str(e))
            return await self._run_react_loop(question, previous_messages="")

    async def _run_vectorless(self, question: str, full_text: str) -> Dict[str, Any]:
        """
        Bypass retrieval — provide the full document text directly to Gemini for deep reasoning.
        Fallback chain:
          1. Attempt full-text vectorless analysis (best path).
          2. If that fails, fall through to the standard ReAct retrieval loop.
          3. If still no usable answer, synthesise from whatever evidence exists.
        """
        prompt = VECTORLESS_ANALYSIS_PROMPT.format(
            question=question,
            # Cap at ~40k chars (~30k tokens) — enough for thorough analysis, avoids slow/hung Gemini calls
            full_text=full_text[:40_000],
        )
        try:
            logger.info("agent.vectorless_analysis.start", doc_id=self.doc_ids[0] if self.doc_ids else "?")
            result = await generate_structured(prompt, use_pro=True, feature_name="vectorless_analysis")
            draft_answer = result.get("answer", "").strip()

            if not draft_answer:
                raise ValueError("Vectorless analysis returned an empty answer.")

            logger.info("agent.vectorless_analysis.success", answer_len=len(draft_answer))

            return {
                "answer": draft_answer,
                "sources": [{"clause_id": "doc_level", "snippet": "Analyzed full document", "doc_id": self.doc_ids[0] if self.doc_ids else ""}],
                "confidence": "high",
            }

        except Exception as e:
            logger.error(
                "agent.vectorless_failed",
                error=str(e),
                exc_info=True,
                hint="Falling back to ReAct retrieval loop.",
            )
            # Fall through to the standard retrieval-based ReAct loop
            return await self._run_react_loop(question, previous_messages="")
