"""
Critical-path tests for LexAI.

Covers:
  1.  Upload flow (validation, MinIO, task dispatch)
  2.  Ingestion pipeline (parse → segment → embed → index)
  3.  Delete flow (all stores cleaned up)
  4.  Single-doc intelligence — vectorless path (never returns empty)
  5.  Multi-doc intelligence — hybrid retrieval fallback
  6.  ReAct agent fallback chain
  7.  Query routing through translations (route ordering fix)
  8.  generate_structured schema-kwarg compatibility
  9.  storage.py non-blocking wrappers
  10. elasticsearch.py keyword args (no body=)

All external I/O (MinIO, Qdrant, Elasticsearch, Neo4j, Gemini) is mocked so
these tests run without any live services.

Run: cd apps/api && pytest tests/test_critical_paths.py -v
"""
import asyncio
import io
import json
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import pytest_asyncio
from httpx import AsyncClient

# Pre-import app submodules so that unittest.mock.patch() can resolve dotted
# paths like "app.core.vector_store.init_vector_store" via pkgutil.resolve_name.
# Without this, Python's __import__('app') doesn't expose .core.vector_store as
# an attribute and resolve_name raises AttributeError.
import app.core.database       # noqa: E402
import app.core.storage        # noqa: E402
import app.core.vector_store   # noqa: E402
import app.core.elasticsearch  # noqa: E402
import app.core.graph_db       # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

SMALL_PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Contents 4 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Length 44 >>\nstream\n"
    b"BT /F1 12 Tf 100 700 Td (This is a test NDA clause.) Tj ET\n"
    b"endstream\nendobj\nxref\n0 5\n0000000000 65535 f \n"
    b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n0\n%%EOF"
)

SAMPLE_TEXT = (
    "1. CONFIDENTIALITY\n"
    "Each Party shall keep the Confidential Information of the other Party "
    "strictly confidential and shall not disclose it to any third party "
    "without prior written consent.\n\n"
    "2. TERM\n"
    "This Agreement shall commence on the Effective Date and shall remain "
    "in full force and effect for a period of three (3) years thereafter.\n\n"
    "3. GOVERNING LAW\n"
    "This Agreement shall be governed by and construed in accordance with "
    "the laws of the State of Delaware.\n"
)


def _make_fake_clauses(doc_id: str, folder_id: str, count: int = 3):
    """Return fake ClauseData-like dicts for mocking."""
    from app.ingestion.pipeline import ClauseData

    return [
        ClauseData(
            clause_id=str(uuid.uuid4()),
            doc_id=doc_id,
            folder_id=folder_id,
            section_id=f"{doc_id}_s{i}",
            section_heading=f"Section {i}",
            clause_type="obligation",
            text=f"Clause {i}: The parties shall comply with all applicable laws.",
            page_start=1,
            page_end=1,
            position_in_doc=i,
        )
        for i in range(count)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client():
    """ASGI test client with all external services mocked at import time."""
    # Patch init_* calls so app lifespan doesn't need live infrastructure
    with (
        patch("app.core.database.init_db", new_callable=AsyncMock),
        patch("app.core.storage.init_storage", new_callable=AsyncMock),
        patch("app.core.vector_store.init_vector_store", new_callable=AsyncMock),
        patch("app.core.elasticsearch.init_elasticsearch", new_callable=AsyncMock),
        patch("app.core.graph_db.init_graph_db", return_value=None),
    ):
        from app.main import app
        async with AsyncClient(app=app, base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def auth_headers(client):
    """Register + login once per session, return auth headers."""
    email = f"critpath_{uuid.uuid4().hex[:8]}@lexai.test"
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "full_name": "Critical Path Tester",
        "password": "TestPass123!",
        "role": "legal_team",
    })
    res = await client.post(
        "/api/v1/auth/token",
        data={"username": email, "password": "TestPass123!"},
    )
    assert res.status_code == 200, f"Login failed: {res.text}"
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_folder_id(client, auth_headers):
    res = await client.post(
        "/api/v1/folders/",
        json={"name": "Critical Path Tests", "description": ""},
        headers=auth_headers,
    )
    assert res.status_code == 201
    return res.json()["id"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Upload Flow
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadFlow:
    @pytest.mark.asyncio
    async def test_upload_pdf_success(self, client, auth_headers, test_folder_id):
        """Upload a valid PDF - should create a Document record and queue ingestion."""
        with (
            patch("app.core.storage.upload_file", new_callable=AsyncMock, return_value="http://minio/test/doc.pdf"),
            patch("app.tasks.ingestion_tasks.ingest_document.delay", return_value=MagicMock(id="task-123")),
        ):
            res = await client.post(
                "/api/v1/documents/upload",
                headers=auth_headers,
                files={"file": ("contract.pdf", SMALL_PDF_BYTES, "application/pdf")},
                data={"folder_id": test_folder_id},
            )
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["name"] == "contract.pdf"
        assert data["status"] == "uploaded"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_upload_rejects_unsupported_type(self, client, auth_headers, test_folder_id):
        """Executable files must be rejected regardless of filename tricks."""
        with patch("app.core.storage.upload_file", new_callable=AsyncMock):
            res = await client.post(
                "/api/v1/documents/upload",
                headers=auth_headers,
                files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
                data={"folder_id": test_folder_id},
            )
        assert res.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_rejects_oversized_file(self, client, auth_headers, test_folder_id):
        """Files over 50 MB must be rejected before hitting storage."""
        huge = b"x" * (51 * 1024 * 1024)
        with patch("app.core.storage.upload_file", new_callable=AsyncMock):
            res = await client.post(
                "/api/v1/documents/upload",
                headers=auth_headers,
                files={"file": ("big.pdf", huge, "application/pdf")},
                data={"folder_id": test_folder_id},
            )
        assert res.status_code == 413

    @pytest.mark.asyncio
    async def test_upload_wrong_folder(self, client, auth_headers):
        """Upload to a non-existent folder must return 404, not 500."""
        with patch("app.core.storage.upload_file", new_callable=AsyncMock):
            res = await client.post(
                "/api/v1/documents/upload",
                headers=auth_headers,
                files={"file": ("x.pdf", SMALL_PDF_BYTES, "application/pdf")},
                data={"folder_id": "does-not-exist"},
            )
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_upload_without_auth_returns_401(self, client, test_folder_id):
        res = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("x.pdf", SMALL_PDF_BYTES, "application/pdf")},
            data={"folder_id": test_folder_id},
        )
        assert res.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 2. Ingestion Pipeline (Unit tests — no live services)
# ─────────────────────────────────────────────────────────────────────────────

class TestIngestionPipeline:
    def test_document_parser_plain_text(self):
        from app.ingestion.pipeline import DocumentParser
        parser = DocumentParser()
        result = parser.parse(SAMPLE_TEXT.encode(), "nda.txt")
        assert result["page_count"] == 1
        assert result["parser"] == "text"
        assert "CONFIDENTIALITY" in result["pages"][0]["text"]

    def test_clause_segmenter_produces_clauses(self):
        from app.ingestion.pipeline import DocumentParser, ClauseSegmenter
        doc_id = str(uuid.uuid4())
        folder_id = str(uuid.uuid4())
        parser = DocumentParser()
        parsed = parser.parse(SAMPLE_TEXT.encode(), "nda.txt")
        segmenter = ClauseSegmenter()
        clauses = segmenter.segment(parsed, doc_id, folder_id)
        assert len(clauses) >= 1, "At least one clause must be produced"
        for c in clauses:
            assert c.doc_id == doc_id
            assert c.folder_id == folder_id
            assert len(c.text) >= 15, "Clause text too short"

    def test_clause_segmenter_assigns_correct_types(self):
        from app.ingestion.pipeline import DocumentParser, ClauseSegmenter
        doc_id, folder_id = str(uuid.uuid4()), str(uuid.uuid4())
        parsed = DocumentParser().parse(SAMPLE_TEXT.encode(), "nda.txt")
        clauses = ClauseSegmenter().segment(parsed, doc_id, folder_id)
        types = {c.clause_type for c in clauses}
        # SAMPLE_TEXT has confidentiality and governing_law patterns
        assert types & {"confidentiality", "governing_law", "obligation", "miscellaneous"}

    def test_cross_reference_resolver(self):
        from app.ingestion.pipeline import DocumentParser, ClauseSegmenter, CrossReferenceResolver
        text = (
            "1. DEFINITIONS\nAs used in this Agreement, the following terms shall have the following meanings.\n\n"
            "2. OBLIGATIONS\nEach Party shall comply with Section 1 of this Agreement.\n"
        )
        doc_id, folder_id = str(uuid.uuid4()), str(uuid.uuid4())
        parsed = DocumentParser().parse(text.encode(), "ref.txt")
        clauses = ClauseSegmenter().segment(parsed, doc_id, folder_id)
        resolved = CrossReferenceResolver().resolve(clauses)
        # Should not raise and should return the same list
        assert isinstance(resolved, list)

    def test_empty_document_segmenter(self):
        """Segmenter on an empty document produces zero clauses (not a crash)."""
        from app.ingestion.pipeline import ClauseSegmenter
        result = ClauseSegmenter().segment({"pages": []}, "doc-x", "folder-y")
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_and_store_clauses_calls_upsert(self):
        """embed_and_store_clauses must call upsert_clauses exactly once."""
        from app.ingestion.pipeline import embed_and_store_clauses
        fake_clauses = _make_fake_clauses("doc1", "folder1", count=2)
        fake_embeddings = [[0.1] * 768, [0.2] * 768]

        with (
            patch("app.ingestion.pipeline.generate_embeddings_async",
                  new_callable=AsyncMock, return_value=fake_embeddings),
            patch("app.core.vector_store.upsert_clauses", new_callable=AsyncMock) as mock_upsert,
        ):
            await embed_and_store_clauses(fake_clauses, "my_doc.pdf", "My Folder")

        mock_upsert.assert_called_once()
        points = mock_upsert.call_args[0][0]
        assert len(points) == 2

    @pytest.mark.asyncio
    async def test_index_to_elasticsearch_calls_bulk_index(self):
        from app.ingestion.pipeline import index_clauses_to_elasticsearch
        fake_clauses = _make_fake_clauses("doc2", "folder2", count=3)

        with patch("app.core.elasticsearch.bulk_index_clauses", new_callable=AsyncMock, return_value=3) as mock_bulk:
            count = await index_clauses_to_elasticsearch(fake_clauses, "doc.pdf", "Folder")

        mock_bulk.assert_called_once()
        assert count == 3

    @pytest.mark.asyncio
    async def test_metadata_extraction_returns_defaults_on_error(self):
        """If Gemini fails during metadata extraction, a safe default dict is returned."""
        from app.ingestion.pipeline import extract_document_metadata
        with patch("app.ai.gemini_client.generate_structured",
                   new_callable=AsyncMock, side_effect=Exception("API down")):
            result = await extract_document_metadata(SAMPLE_TEXT, "doc-err")
        assert isinstance(result, dict)
        assert "language" in result
        assert "document_type" in result


# ─────────────────────────────────────────────────────────────────────────────
# 3. Delete Flow
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteFlow:
    @pytest.mark.asyncio
    async def test_delete_document_cleans_all_stores(self, client, auth_headers, test_folder_id):
        """
        Deleting a document must call MinIO, Qdrant, ES, and Neo4j delete functions.
        A failure in one store must be logged but must NOT prevent DB cleanup.
        """
        # Upload a document first
        with (
            patch("app.core.storage.upload_file", new_callable=AsyncMock, return_value="http://minio/x"),
            patch("app.tasks.ingestion_tasks.ingest_document.delay", return_value=MagicMock(id="t")),
        ):
            up_res = await client.post(
                "/api/v1/documents/upload",
                headers=auth_headers,
                files={"file": ("delete_me.txt", b"hello world clause", "text/plain")},
                data={"folder_id": test_folder_id},
            )
        assert up_res.status_code == 201
        doc_id = up_res.json()["id"]

        with (
            patch("app.core.storage.delete_file", new_callable=AsyncMock) as mock_storage,
            patch("app.core.vector_store.delete_document_clauses", new_callable=AsyncMock) as mock_qdrant,
            patch("app.core.elasticsearch.delete_document_clauses_es", new_callable=AsyncMock) as mock_es,
            patch("app.core.graph_db.delete_document_graph", return_value=None) as mock_graph,
        ):
            del_res = await client.delete(f"/api/v1/documents/{doc_id}", headers=auth_headers)

        assert del_res.status_code == 204, del_res.text
        mock_storage.assert_called_once()
        mock_qdrant.assert_called_once_with(doc_id)
        mock_es.assert_called_once_with(doc_id)
        mock_graph.assert_called_once_with(doc_id)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_document_returns_404(self, client, auth_headers):
        res = await client.delete("/api/v1/documents/no-such-id", headers=auth_headers)
        assert res.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_continues_when_minio_fails(self, client, auth_headers, test_folder_id):
        """If MinIO delete fails, DB cleanup should still succeed (warning logged)."""
        with (
            patch("app.core.storage.upload_file", new_callable=AsyncMock, return_value="http://minio/x"),
            patch("app.tasks.ingestion_tasks.ingest_document.delay", return_value=MagicMock(id="t")),
        ):
            up_res = await client.post(
                "/api/v1/documents/upload",
                headers=auth_headers,
                files={"file": ("partial_del.txt", b"clause text here", "text/plain")},
                data={"folder_id": test_folder_id},
            )
        doc_id = up_res.json()["id"]

        with (
            patch("app.core.storage.delete_file", new_callable=AsyncMock, side_effect=Exception("MinIO down")),
            patch("app.core.vector_store.delete_document_clauses", new_callable=AsyncMock),
            patch("app.core.elasticsearch.delete_document_clauses_es", new_callable=AsyncMock),
            patch("app.core.graph_db.delete_document_graph", return_value=None),
        ):
            del_res = await client.delete(f"/api/v1/documents/{doc_id}", headers=auth_headers)

        # Must still succeed — MinIO failure is non-fatal at the API level
        assert del_res.status_code == 204, del_res.text


# ─────────────────────────────────────────────────────────────────────────────
# 4. Document Intelligence — Single Doc (vectorless path)
# ─────────────────────────────────────────────────────────────────────────────

class TestDocumentIntelligenceSingleDoc:
    @pytest.mark.asyncio
    async def test_single_doc_always_returns_answer(self):
        """
        With a single document in scope, the agent MUST always return a non-empty answer
        even when vectorless analysis succeeds immediately.
        """
        from app.ai.agent.react_agent import ReActAgent

        doc_id = str(uuid.uuid4())
        full_text = SAMPLE_TEXT

        with (
            patch("app.ai.agent.tools.get_full_document",
                  new_callable=AsyncMock, return_value=full_text),
            patch("app.ai.gemini_client.generate_structured",
                  new_callable=AsyncMock,
                  return_value={"answer": "This NDA has a 3-year term and Delaware governing law."}),
            patch("app.ai.agent.verification.verify_response",
                  new_callable=AsyncMock,
                  return_value=("This NDA has a 3-year term and Delaware governing law.", [])),
        ):
            agent = ReActAgent(doc_ids=[doc_id], folder_ids=[])
            result = await agent.run("Summarize this NDA")

        assert result["answer"], "Answer must not be empty for single-doc vectorless mode"
        assert result["confidence"] in ("high",), f"Expected high confidence, got {result['confidence']}"

    @pytest.mark.asyncio
    async def test_single_doc_vectorless_fallback_to_react_on_error(self):
        """
        If vectorless analysis throws, the agent must fall back to the ReAct loop
        without propagating the exception.
        """
        from app.ai.agent.react_agent import ReActAgent
        from app.core.config import settings

        doc_id = str(uuid.uuid4())
        full_text = SAMPLE_TEXT

        # generate_structured raises on first call (vectorless), returns valid on second (synthesize)
        call_count = [0]

        async def _fake_structured(prompt, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Simulated Gemini timeout")
            if "action" in prompt or "SYNTHESIZE" in prompt:
                return {"action": "SYNTHESIZE", "reasoning": "enough evidence", "tool": None, "args": {}}
            return {"answer": "Fallback answer from ReAct loop."}

        with (
            patch("app.ai.agent.tools.get_full_document",
                  new_callable=AsyncMock, return_value=full_text),
            patch("app.ai.gemini_client.generate_structured", side_effect=_fake_structured),
            patch("app.ai.agent.tools.search_clauses", new_callable=AsyncMock, return_value=[]),
            patch("app.ai.agent.verification.verify_response",
                  new_callable=AsyncMock,
                  return_value=("Fallback answer from ReAct loop.", [])),
        ):
            agent = ReActAgent(doc_ids=[doc_id], folder_ids=[])
            result = await agent.run("What are the obligations?")

        # Must never raise; must return some answer
        assert "answer" in result
        assert result["answer"]

    @pytest.mark.asyncio
    async def test_vectorless_never_sends_full_text_to_verification(self):
        """
        The verification layer must receive a capped snippet, not the full megabyte text.
        """
        from app.ai.agent.react_agent import ReActAgent

        doc_id = str(uuid.uuid4())
        # A very large document
        huge_text = "This clause grants obligations. " * 5000  # ~160k chars

        captured_evidence = []

        async def fake_verify(answer, evidence):
            captured_evidence.extend(evidence)
            return answer, evidence

        with (
            patch("app.ai.agent.tools.get_full_document",
                  new_callable=AsyncMock, return_value=huge_text),
            patch("app.ai.gemini_client.generate_structured",
                  new_callable=AsyncMock,
                  return_value={"answer": "Obligations are defined throughout the document."}),
            patch("app.ai.agent.verification.verify_response", side_effect=fake_verify),
        ):
            agent = ReActAgent(doc_ids=[doc_id], folder_ids=[])
            await agent.run("List all obligations")

        # The snippet sent to verify must be capped, not the full 160k chars
        for ev in captured_evidence:
            snippet = ev.get("snippet", "")
            assert len(snippet) <= 20_001, (
                f"Verification received {len(snippet)} chars — snippet was not capped"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Multi-Doc Intelligence — Hybrid Retrieval Fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiDocHybridRetrieval:
    @pytest.mark.asyncio
    async def test_react_loop_with_results_produces_answer(self):
        """Multi-doc ReAct loop with some evidence must produce a non-empty answer."""
        from app.ai.agent.react_agent import ReActAgent

        doc_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        fake_clause = {
            "id": str(uuid.uuid4()),
            "score": 0.9,
            "payload": {
                "clause_id": str(uuid.uuid4()),
                "doc_id": doc_ids[0],
                "text": "The parties shall maintain confidentiality for five years.",
                "section_id": "s1",
                "page_start": 2,
            },
        }

        call_seq = [
            # 1st THINK → use search_clauses tool
            {"action": "TOOL", "tool": "search_clauses", "args": {"query": "confidentiality"}, "reasoning": "Need evidence"},
            # 2nd THINK → synthesize
            {"action": "SYNTHESIZE", "tool": None, "args": {}, "reasoning": "Have evidence"},
        ]
        call_idx = [0]

        async def fake_think(prompt, **kwargs):
            idx = min(call_idx[0], len(call_seq) - 1)
            call_idx[0] += 1
            return call_seq[idx]

        async def fake_synthesize(prompt, **kwargs):
            if "evidence" in prompt.lower() or "answer" in prompt:
                return {"answer": "Both documents impose a 5-year confidentiality obligation."}
            return call_seq[min(call_idx[0], len(call_seq) - 1)]

        with (
            patch("app.ai.agent.tools.get_full_document",
                  new_callable=AsyncMock, return_value=None),  # too large → ReAct loop
            patch("app.ai.gemini_client.generate_structured", side_effect=fake_synthesize),
            patch("app.ai.agent.tools.search_clauses",
                  new_callable=AsyncMock, return_value=[fake_clause]),
            patch("app.ai.agent.verification.verify_response",
                  new_callable=AsyncMock,
                  return_value=("Both documents impose a 5-year confidentiality obligation.", [fake_clause["payload"]])),
        ):
            agent = ReActAgent(doc_ids=doc_ids, folder_ids=[])
            # Pre-seed with the THINK sequence
            agent._think_seq = call_seq
            agent._think_idx = 0
            result = await agent.run("Compare confidentiality terms across both documents")

        assert result["answer"]
        assert "confidentiality" in result["answer"].lower()

    @pytest.mark.asyncio
    async def test_insufficient_evidence_returns_specific_message(self):
        """With zero evidence after max steps, the answer message must be descriptive."""
        from app.ai.agent.react_agent import ReActAgent
        from app.core.config import settings

        original_max = settings.AGENT_MAX_STEPS
        settings.AGENT_MAX_STEPS = 1  # Force early termination

        try:
            with (
                patch("app.ai.agent.tools.get_full_document",
                      new_callable=AsyncMock, return_value=None),
                patch("app.ai.gemini_client.generate_structured",
                      new_callable=AsyncMock,
                      return_value={"action": "TOOL", "tool": "search_clauses",
                                    "args": {"query": "x"}, "reasoning": ""}),
                patch("app.ai.agent.tools.search_clauses",
                      new_callable=AsyncMock, return_value=[]),
            ):
                agent = ReActAgent(doc_ids=["doc1", "doc2"], folder_ids=[])
                result = await agent.run("Find the payment schedule")
        finally:
            settings.AGENT_MAX_STEPS = original_max

        assert "answer" in result
        # Should be a descriptive "no evidence" message, not an empty string
        assert len(result["answer"]) > 10


# ─────────────────────────────────────────────────────────────────────────────
# 6. generate_structured — schema kwarg compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestGeminiClientCompat:
    @pytest.mark.asyncio
    async def test_generate_structured_accepts_schema_kwarg(self):
        """Callers that pass schema={} must not get a TypeError."""
        from app.ai.gemini_client import generate_structured

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"answer": "test"}'
        mock_model.generate_content_async = AsyncMock(return_value=mock_response)

        with patch("app.ai.gemini_client.get_flash_model", return_value=mock_model):
            # Must NOT raise TypeError about unexpected keyword argument
            result = await generate_structured("test prompt", schema={})

        assert result == {"answer": "test"}

    @pytest.mark.asyncio
    async def test_generate_structured_strips_markdown_fences(self):
        """JSON wrapped in ```json ... ``` must be parsed correctly."""
        from app.ai.gemini_client import generate_structured

        raw = '```json\n{"key": "value"}\n```'
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = raw
        mock_model.generate_content_async = AsyncMock(return_value=mock_response)

        with patch("app.ai.gemini_client.get_flash_model", return_value=mock_model):
            result = await generate_structured("prompt")

        assert result == {"key": "value"}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Storage — non-blocking wrappers
# ─────────────────────────────────────────────────────────────────────────────

class TestStorageNonBlocking:
    @pytest.mark.asyncio
    async def test_upload_runs_in_executor(self):
        """upload_file must delegate to run_in_executor, not block the loop."""
        from app.core import storage

        minio_mock = MagicMock()
        minio_mock.put_object = MagicMock(return_value=None)
        storage._client = minio_mock

        result = await storage.upload_file("test/key.pdf", b"data", "application/pdf")
        assert "test/key.pdf" in result
        minio_mock.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_does_not_call_release_conn(self):
        """download_file must NOT call .release_conn() — MinIO has no such method."""
        from app.core import storage

        fake_response = MagicMock()
        fake_response.read.return_value = b"document bytes"
        # Ensure release_conn is NOT present on our fake (mimicking real Minio response)
        del fake_response.release_conn

        minio_mock = MagicMock()
        minio_mock.get_object = MagicMock(return_value=fake_response)
        storage._client = minio_mock

        data = await storage.download_file("test/key.pdf")
        assert data == b"document bytes"
        fake_response.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_file_calls_remove_object(self):
        from app.core import storage

        minio_mock = MagicMock()
        minio_mock.remove_object = MagicMock(return_value=None)
        storage._client = minio_mock

        await storage.delete_file("docs/file.pdf")
        minio_mock.remove_object.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 8. Elasticsearch — no deprecated body= calls
# ─────────────────────────────────────────────────────────────────────────────

class TestElasticsearch:
    @pytest.mark.asyncio
    async def test_keyword_search_does_not_use_body_param(self):
        """client.search must be called with keyword args, not body=."""
        from app.core import elasticsearch

        mock_client = AsyncMock()
        mock_client.search = AsyncMock(return_value={
            "hits": {"hits": []}
        })
        elasticsearch._client = mock_client

        await elasticsearch.keyword_search("payment terms", limit=5)

        _, call_kwargs = mock_client.search.call_args
        assert "body" not in call_kwargs, "body= is deprecated in elasticsearch-py >= 8"
        assert "query" in call_kwargs

    @pytest.mark.asyncio
    async def test_init_creates_index_with_mappings_kwarg(self):
        """init_elasticsearch must use mappings= / settings= not body=."""
        from app.core import elasticsearch

        mock_client = AsyncMock()
        mock_client.indices.exists = AsyncMock(return_value=False)
        mock_client.indices.create = AsyncMock(return_value={"acknowledged": True})
        elasticsearch._client = mock_client

        await elasticsearch.init_elasticsearch()

        _, create_kwargs = mock_client.indices.create.call_args
        assert "body" not in create_kwargs, "body= is deprecated in elasticsearch-py >= 8"
        assert "mappings" in create_kwargs
        assert "settings" in create_kwargs

    @pytest.mark.asyncio
    async def test_delete_by_query_no_body_param(self):
        """delete_document_clauses_es must use query= not body=."""
        from app.core import elasticsearch

        mock_client = AsyncMock()
        mock_client.delete_by_query = AsyncMock(return_value={"deleted": 5})
        elasticsearch._client = mock_client

        await elasticsearch.delete_document_clauses_es("doc123")

        _, delete_kwargs = mock_client.delete_by_query.call_args
        assert "body" not in delete_kwargs, "body= is deprecated in elasticsearch-py >= 8"
        assert "query" in delete_kwargs


# ─────────────────────────────────────────────────────────────────────────────
# 9. Translation route ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestTranslationRouteOrder:
    @pytest.mark.asyncio
    async def test_document_route_not_matched_as_job_id(self, client, auth_headers, test_folder_id):
        """
        GET /translations/document/{id} must NOT be caught by the /{job_id} dynamic route.
        Uploading then hitting document listing should return a list, not a 404.
        """
        # Ensure the document listing endpoint returns 200 (not 404 from wrong route match)
        res = await client.get(
            f"/api/v1/translations/document/some-doc-id",
            headers=auth_headers,
        )
        # 200 → static route matched correctly
        # 404 from an actual missing document is acceptable since there's no doc in DB
        # What we must NOT get is a 422 (UUID parse error from treating "document" as job_id)
        assert res.status_code != 422, (
            "Route /translations/document/{id} was matched by /{job_id} — route ordering bug still present"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 10. Logging — no silent failures
# ─────────────────────────────────────────────────────────────────────────────

class TestLogging:
    def test_setup_logging_does_not_raise(self):
        from app.core.logging import setup_logging
        setup_logging()  # Must not raise

    def test_get_logger_returns_bound_logger(self):
        from app.core.logging import get_logger
        logger = get_logger("test_module")
        assert logger is not None

    def test_logger_info_writes_to_stdout(self, capsys):
        from app.core.logging import setup_logging, get_logger
        setup_logging()
        logger = get_logger("test_stdout")
        logger.info("test_event", field="value", number=42)
        captured = capsys.readouterr()
        assert "test_event" in captured.out

    def test_logger_error_with_exc_info(self, capsys):
        from app.core.logging import setup_logging, get_logger
        setup_logging()
        logger = get_logger("test_errors")
        try:
            raise ValueError("intentional test error")
        except ValueError:
            logger.error("caught_error", exc_info=True)
        captured = capsys.readouterr()
        assert "caught_error" in captured.out


# ─────────────────────────────────────────────────────────────────────────────
# 11. Vector store hybrid_search compat shim
# ─────────────────────────────────────────────────────────────────────────────

class TestVectorStoreShim:
    @pytest.mark.asyncio
    async def test_shim_passes_folder_id_filter(self):
        """filter_dict with folder_id must be forwarded to hybrid_search_clauses."""
        from app.core import vector_store

        called_with = {}

        async def fake_hybrid_search_clauses(*args, **kwargs):
            called_with.update(kwargs)
            return []

        with patch.object(vector_store, "hybrid_search_clauses", side_effect=fake_hybrid_search_clauses):
            await vector_store.hybrid_search(
                dense_vector=[0.1] * 768,
                filter_dict={"folder_id": "folder-abc"},
                limit=10,
            )

        assert called_with.get("filter_folder_ids") == ["folder-abc"]

    @pytest.mark.asyncio
    async def test_shim_passes_document_id_filter(self):
        from app.core import vector_store

        called_with = {}

        async def fake_hybrid(*args, **kwargs):
            called_with.update(kwargs)
            return []

        with patch.object(vector_store, "hybrid_search_clauses", side_effect=fake_hybrid):
            await vector_store.hybrid_search(
                dense_vector=[0.0] * 768,
                filter_dict={"document_id": "doc-xyz"},
                limit=5,
            )

        assert called_with.get("filter_doc_ids") == ["doc-xyz"]

    @pytest.mark.asyncio
    async def test_shim_passes_chunk_type_filter(self):
        from app.core import vector_store

        called_with = {}

        async def fake_hybrid(*args, **kwargs):
            called_with.update(kwargs)
            return []

        with patch.object(vector_store, "hybrid_search_clauses", side_effect=fake_hybrid):
            await vector_store.hybrid_search(
                dense_vector=[0.0] * 768,
                filter_dict={"chunk_type": "clause"},
                limit=5,
            )

        assert called_with.get("filter_clause_types") == ["clause"]


# ─────────────────────────────────────────────────────────────────────────────
# 12. tools.py — extract_fields returns strings not Row objects
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentTools:
    @pytest.mark.asyncio
    async def test_extract_fields_joins_strings_not_tuples(self):
        """Row objects from SQLModel must be unwrapped to plain strings."""
        from app.ai.agent import tools

        fake_texts = ["Clause one text.", "Clause two text.", "Clause three text."]
        captured_prompt = []

        async def fake_generate(prompt, **kwargs):
            captured_prompt.append(prompt)
            return {"parties": ["Alpha Corp", "Beta Ltd"]}

        with (
            patch.object(tools, "AsyncSessionLocal") as mock_session_cls,
            patch("app.ai.gemini_client.generate_structured", side_effect=fake_generate),
        ):
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_exec_result = MagicMock()
            mock_exec_result.all.return_value = fake_texts  # plain strings
            mock_session.exec = AsyncMock(return_value=mock_exec_result)
            mock_session_cls.return_value = mock_session

            result = await tools.extract_fields("doc-1", ["parties"])

        assert "parties" in result
        # The prompt should contain the actual text, not tuple representations
        assert "('Clause" not in captured_prompt[0], (
            "extract_fields is joining Row tuples instead of string values"
        )

    @pytest.mark.asyncio
    async def test_compare_clauses_reads_text_inside_session(self):
        """compare_clauses must read ca.text and cb.text within the session context."""
        from app.ai.agent import tools
        from app.models.clause import Clause

        clause_a = Clause(
            id="ca",
            doc_id="d1",
            folder_id="f1",
            section_id="s1",
            section_heading="Section 1",
            clause_type="obligation",
            text="Party A shall deliver goods by January 1.",
            page_start=1,
            page_end=1,
            position_in_doc=0,
        )
        clause_b = Clause(
            id="cb",
            doc_id="d1",
            folder_id="f1",
            section_id="s2",
            section_heading="Section 2",
            clause_type="obligation",
            text="Party B shall deliver goods by March 1.",
            page_start=2,
            page_end=2,
            position_in_doc=1,
        )

        with (
            patch.object(tools, "AsyncSessionLocal") as mock_session_cls,
            patch("app.ai.gemini_client.generate_structured", new_callable=AsyncMock,
                  return_value={"status": "compatible", "explanation": "Similar obligation, different dates."}),
        ):
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            exec_results = [
                MagicMock(first=MagicMock(return_value=clause_a)),
                MagicMock(first=MagicMock(return_value=clause_b)),
            ]
            mock_session.exec = AsyncMock(side_effect=exec_results)
            mock_session_cls.return_value = mock_session

            result = await tools.compare_clauses("ca", "cb")

        assert result.get("status") == "compatible"
        assert "error" not in result
