"""
Evaluation harness for LexAI retrieval and groundedness.
Run: python -m evaluation.harness
"""
import asyncio
import json
from typing import List, Dict

# ── Golden test dataset ──────────────────────────────────────
# Each entry: question + expected answer keywords + expected sources
GOLDEN_DATASET: List[Dict] = [
    {
        "id": "gt-001",
        "description": "Retrieve governing law from NDA",
        "question": "What is the governing law for the NDA?",
        "expected_keywords": ["governing law", "jurisdiction"],
        "expected_source_fields": ["page_number", "document_name", "clause_number"],
        "must_not_hallucinate": True,
    },
    {
        "id": "gt-002",
        "description": "Retrieve payment terms from vendor contract",
        "question": "What are the payment terms in the vendor agreement?",
        "expected_keywords": ["payment", "net", "due"],
        "expected_source_fields": ["page_number", "document_name"],
        "must_not_hallucinate": True,
    },
    {
        "id": "gt-003",
        "description": "Identify confidentiality duration",
        "question": "How long does the confidentiality obligation last?",
        "expected_keywords": ["year", "month", "confidential", "period"],
        "expected_source_fields": ["clause_number", "document_name"],
        "must_not_hallucinate": True,
    },
    {
        "id": "gt-004",
        "description": "Insufficient evidence case",
        "question": "What is the termination notice period?",
        "expected_keywords": ["insufficient evidence", "days", "notice"],
        "expected_source_fields": [],
        "must_not_hallucinate": True,
    },
]


class EvaluationResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures: List[Dict] = []

    def record(self, test_id: str, passed: bool, reason: str = ""):
        if passed:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append({"id": test_id, "reason": reason})

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"EVALUATION RESULTS: {self.passed}/{total} passed")
        print(f"{'='*50}")
        if self.failures:
            print("\nFailed tests:")
            for f in self.failures:
                print(f"  ✗ [{f['id']}] {f['reason']}")
        else:
            print("\n✅ All evaluation tests passed!")
        return self.failed == 0


def evaluate_answer(answer_data: Dict, golden: Dict) -> tuple[bool, str]:
    """
    Evaluate a single answer against a golden test case.
    Returns (passed, reason).
    """
    answer_text = answer_data.get("answer", "").lower()

    # 1. Check sources are present when expected
    sources = answer_data.get("sources", [])
    if golden["expected_source_fields"] and not sources:
        return False, "Expected sources but got none — possible hallucination or retrieval failure"

    # 2. Check source fields are populated
    for src in sources:
        for field in golden["expected_source_fields"]:
            if not src.get(field):
                return False, f"Source is missing required field: {field}"

    # 3. Check expected keywords appear in answer
    matched_keywords = [kw for kw in golden["expected_keywords"] if kw in answer_text]
    if golden["expected_keywords"] and not matched_keywords:
        # If none matched AND it's not an "insufficient evidence" scenario
        is_insufficient = "insufficient evidence" in answer_text
        if not is_insufficient:
            return False, f"Answer missing expected keywords: {golden['expected_keywords']}"

    # 4. Check confidence is populated
    confidence = answer_data.get("confidence")
    if confidence not in ("high", "medium", "low"):
        return False, f"Invalid confidence value: {confidence}"

    return True, "passed"


async def run_evaluation():
    """
    Run the evaluation harness against the golden dataset.
    In a real run, this would call the actual chat workflow with real docs.
    Here we validate the schema structure and logic paths.
    """
    from app.ai.workflows.chat_workflow import chat_workflow

    results = EvaluationResult()

    # Schema validation test — ensures the workflow output always has correct fields
    for golden in GOLDEN_DATASET:
        print(f"\nRunning [{golden['id']}]: {golden['description']}")

        # Simulate a minimal state with empty scope (tests schema integrity)
        try:
            state = await chat_workflow.ainvoke({
                "question": golden["question"],
                "rewritten_question": "",
                "scope_filter": {},
                "folder_names": ["Test Folder"],
                "document_names": ["test.pdf"],
                "raw_chunks": [],
                "reranked_chunks": [],
                "graph_facts": "",
                "answer": {},
            })
            answer_data = state["answer"]

            # Validate required answer keys exist
            required_keys = ["answer", "sources", "conflicts", "confidence", "missing_evidence"]
            for key in required_keys:
                if key not in answer_data:
                    results.record(golden["id"], False, f"Answer missing required key: {key}")
                    break
            else:
                passed, reason = evaluate_answer(answer_data, golden)
                results.record(golden["id"], passed, reason)
                print(f"  → Confidence: {answer_data.get('confidence')} | Sources: {len(answer_data.get('sources', []))}")

        except Exception as e:
            results.record(golden["id"], False, f"Workflow error: {str(e)}")

    return results.summary()


if __name__ == "__main__":
    success = asyncio.run(run_evaluation())
    exit(0 if success else 1)
