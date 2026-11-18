"""RAG deduplication, scope, citation, and fallback-cache checks."""
import unittest
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.services import rag_service


class RAGOptimizationTests(unittest.TestCase):
    def _sdk_modules(self, client):
        class EmbedContentConfig:
            def __init__(self, *, task_type=None, output_dimensionality=None):
                self.task_type = task_type
                self.output_dimensionality = output_dimensionality

        class HttpOptions:
            def __init__(self, *, timeout, retry_options):
                self.timeout = timeout
                self.retry_options = retry_options

        class HttpRetryOptions:
            def __init__(self, *, attempts):
                self.attempts = attempts

        genai_module = ModuleType("google.genai")
        genai_module.types = SimpleNamespace(
            EmbedContentConfig=EmbedContentConfig,
            HttpOptions=HttpOptions,
            HttpRetryOptions=HttpRetryOptions,
        )
        def create_client(**kwargs):
            client.client_options = kwargs
            return client
        genai_module.Client = create_client
        google_module = ModuleType("google")
        google_module.genai = genai_module
        return {"google": google_module, "google.genai": genai_module}

    def test_retrieval_deduplicates_same_document_text_and_preserves_citation(self):
        vector = np.asarray([1.0, 0.0], dtype=np.float32)
        chunks = [
            {"id": "dup-first", "document_id": "selected", "filename": "selected.pdf",
             "chunk_index": 0, "page_number": 4, "content": "Repeated useful passage.",
             "embedding": vector.tobytes(), "embedding_model": rag_service.DEFAULT_EMBEDDING_MODEL},
            {"id": "dup-second", "document_id": "selected", "filename": "selected.pdf",
             "chunk_index": 1, "page_number": 5, "content": "Repeated   useful passage.",
             "embedding": vector.tobytes(), "embedding_model": rag_service.DEFAULT_EMBEDDING_MODEL},
            {"id": "other-doc", "document_id": "unselected", "filename": "other.pdf",
             "chunk_index": 0, "page_number": 1, "content": "Out of scope passage.",
             "embedding": vector.tobytes(), "embedding_model": rag_service.DEFAULT_EMBEDDING_MODEL},
        ]
        with patch.object(rag_service, "create_embedding", return_value=vector):
            results = rag_service.retrieve_relevant_chunks(
                "Explain the passage", chunks, top_k=5,
                score_threshold=0.0, document_ids=["selected"],
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["document_id"], "selected")
        self.assertIn(results[0]["page_number"], {4, 5})
        self.assertEqual(rag_service.extract_sources(results)[0]["page_number"], results[0]["page_number"])

    def test_missing_chunk_embedding_is_cached_for_unchanged_content(self):
        vector = np.asarray([1.0, 0.0], dtype=np.float32)
        chunk = {
            "id": "fallback-cache-unique-test",
            "document_id": "fallback-cache-document-test",
            "filename": "fallback.txt", "chunk_index": 0,
            "page_number": None, "content": "Stable legacy chunk for cache test.",
            "embedding": None,
        }
        with patch.object(rag_service, "create_embedding", return_value=vector), patch.object(
            rag_service, "generate_embeddings", return_value=vector.reshape(1, -1)
        ) as embed:
            for _ in range(2):
                results = rag_service.retrieve_relevant_chunks(
                    "A question", [chunk], top_k=1, score_threshold=0.0,
                    document_ids=[chunk["document_id"]],
                )
                self.assertEqual(len(results), 1)
            embed.assert_called_once_with([chunk["content"]])

    def test_untagged_legacy_vector_is_regenerated(self):
        vector = np.asarray([1.0, 0.0], dtype=np.float32)
        chunk = {
            "id": "legacy-vector-test", "document_id": "legacy-document-test",
            "filename": "legacy.txt", "chunk_index": 0, "content": "legacy content",
            "embedding": vector.tobytes(), "embedding_model": None,
        }
        regenerated = np.asarray([[0.0, 1.0]], dtype=np.float32)
        with patch.object(rag_service, "create_embedding", return_value=vector), patch.object(
            rag_service, "generate_embeddings", return_value=regenerated
        ) as embed:
            results = rag_service.retrieve_relevant_chunks(
                "question", [chunk], top_k=1, score_threshold=-1,
                document_ids=[chunk["document_id"]],
            )
        embed.assert_called_once_with([chunk["content"]])
        self.assertEqual(len(results), 1)

    def test_gemini_request_uses_retrieval_task_dimension_and_normalization(self):
        class FakeClient:
            def __init__(self):
                self.requests = []
                self.closed = False
                self.models = SimpleNamespace(embed_content=self.embed_content)

            def embed_content(self, **kwargs):
                self.requests.append(kwargs)
                return SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766)])

            def close(self):
                self.closed = True

        client = FakeClient()
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "test-key",
            "GEMINI_EMBEDDING_TIMEOUT_SECONDS": "7",
        }, clear=True), patch.dict(
            sys.modules, self._sdk_modules(client)
        ):
            vectors = rag_service.generate_embeddings(["passage"])
            query = rag_service.create_embedding("question")
        document_request, query_request = client.requests
        self.assertEqual(document_request["model"], "gemini-embedding-001")
        self.assertEqual(document_request["contents"], ["passage"])
        self.assertEqual(document_request["config"].task_type, "RETRIEVAL_DOCUMENT")
        self.assertEqual(document_request["config"].output_dimensionality, 768)
        self.assertEqual(query_request["config"].task_type, "RETRIEVAL_QUERY")
        self.assertEqual(query_request["config"].output_dimensionality, 768)
        self.assertEqual(client.client_options["api_key"], "test-key")
        self.assertEqual(client.client_options["http_options"].timeout, 7000)
        self.assertEqual(client.client_options["http_options"].retry_options.attempts, 1)
        self.assertEqual(vectors.shape, (1, 768))
        self.assertEqual(query.shape, (768,))
        vector = vectors[0]
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=6)
        self.assertTrue(client.closed)

    def test_wrong_sdk_vector_dimension_is_rejected_without_truncation(self):
        class FakeClient:
            models = SimpleNamespace(embed_content=lambda **_kwargs: SimpleNamespace(
                embeddings=[SimpleNamespace(values=[1.0] + [0.0] * 3071)]
            ))
            close = lambda _self: None

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), patch.dict(
            sys.modules, self._sdk_modules(FakeClient())
        ):
            with self.assertRaisesRegex(ValueError, "must have 768 dimensions"):
                rag_service.generate_embeddings(["passage"])

    def test_non_retryable_sdk_api_error_is_propagated(self):
        class BadRequest(Exception):
            code = 400

        class FakeClient:
            def __init__(self):
                self.calls = 0
                self.models = SimpleNamespace(embed_content=self.embed_content)

            def embed_content(self, **_kwargs):
                self.calls += 1
                raise BadRequest("invalid embedding request")

            def close(self):
                pass

        client = FakeClient()
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), patch.dict(
            sys.modules, self._sdk_modules(client)
        ), patch.object(rag_service.time, "sleep") as sleep:
            with self.assertRaisesRegex(BadRequest, "invalid embedding request"):
                rag_service.generate_embeddings(["passage"])
        self.assertEqual(client.calls, 1)
        sleep.assert_not_called()

    def test_rate_limiter_counts_texts_and_retry_after_is_honored(self):
        rag_service._embedding_rate_events.clear()
        with patch.dict("os.environ", {"GEMINI_EMBEDDING_REQUESTS_PER_MINUTE": "3"}, clear=True), patch.object(
            rag_service.time, "monotonic", side_effect=[0.0, 0.0, 60.0]
        ), patch.object(rag_service.time, "sleep") as sleep:
            rag_service._wait_for_rate_capacity(2)
            rag_service._wait_for_rate_capacity(2)
        sleep.assert_called_once_with(60.0)
        rag_service._embedding_rate_events.clear()

        class RateLimited(Exception):
            code = 429
            headers = {"Retry-After": "0"}

        class RetryClient:
            def __init__(self):
                self.calls = 0
                self.models = SimpleNamespace(embed_content=self.embed_content)

            def embed_content(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RateLimited("HTTP 429")
                return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0] + [0.0] * 767)])

            def close(self):
                pass

        retry_client = RetryClient()
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), patch.object(
            rag_service, "_get_embedding_api_key", return_value="test-key"
        ), patch.dict(
            sys.modules, self._sdk_modules(retry_client)
        ), patch.object(rag_service.time, "sleep") as retry_sleep:
            vector = rag_service.create_embedding("retry question")
        self.assertEqual(retry_client.calls, 2)
        retry_sleep.assert_called_once_with(0.0)
        self.assertEqual(vector.shape, (768,))
        rag_service._embedding_rate_events.clear()

    def test_embedding_batches_are_bounded_and_timeout_is_configured(self):
        class FakeClient:
            def __init__(self):
                self.batch_sizes = []
                self.models = SimpleNamespace(embed_content=self.embed_content)

            def embed_content(self, **kwargs):
                self.batch_sizes.append(len(kwargs["contents"]))
                return SimpleNamespace(embeddings=[
                    SimpleNamespace(values=[1.0] + [0.0] * 767)
                    for _ in kwargs["contents"]
                ])

            def close(self):
                pass

        client = FakeClient()
        rag_service._embedding_rate_events.clear()
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "test-key",
            "GEMINI_EMBEDDING_BATCH_SIZE": "2",
            "GEMINI_EMBEDDING_TIMEOUT_SECONDS": "7",
        }, clear=True), patch.dict(sys.modules, self._sdk_modules(client)):
            vectors = rag_service.generate_embeddings(["a", "b", "c", "d", "e"])
        self.assertEqual(client.batch_sizes, [2, 2, 1])
        self.assertEqual(client.client_options["http_options"].timeout, 7000)
        self.assertEqual(vectors.shape, (5, 768))
        rag_service._embedding_rate_events.clear()


if __name__ == "__main__":
    unittest.main()
