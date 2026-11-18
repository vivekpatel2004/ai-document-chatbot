"""RAG deduplication, scope, citation, and fallback-cache checks."""
import unittest
from unittest.mock import patch

import numpy as np

from app.services import rag_service


class RAGOptimizationTests(unittest.TestCase):
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
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"embeddings": [{"values": [3.0] + [4.0] + [0.0] * 766}]}

            @staticmethod
            def raise_for_status():
                return None

        class FakeClient:
            payload = None
            headers = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def post(self, url, headers, json):
                self.payload = json
                self.headers = headers
                return FakeResponse()

        client = FakeClient()
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), patch.object(
            rag_service.httpx, "Client", return_value=client
        ):
            vector = rag_service.generate_embeddings(["passage"])[0]
        request = client.payload["requests"][0]
        self.assertEqual(client.headers["x-goog-api-key"], "test-key")
        self.assertEqual(request["model"], "models/gemini-embedding-001")
        self.assertEqual(request["embedContentConfig"]["taskType"], "RETRIEVAL_DOCUMENT")
        self.assertEqual(request["embedContentConfig"]["outputDimensionality"], 768)
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1.0, places=6)
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), patch.object(
            rag_service.httpx, "Client", return_value=client
        ):
            rag_service.create_embedding("question")
        self.assertEqual(
            client.payload["requests"][0]["embedContentConfig"]["taskType"],
            "RETRIEVAL_QUERY",
        )

    def test_rate_limiter_counts_texts_and_retry_after_is_honored(self):
        rag_service._embedding_rate_events.clear()
        with patch.dict("os.environ", {"GEMINI_EMBEDDING_REQUESTS_PER_MINUTE": "3"}, clear=True), patch.object(
            rag_service.time, "monotonic", side_effect=[0.0, 0.0, 60.0]
        ), patch.object(rag_service.time, "sleep") as sleep:
            rag_service._wait_for_rate_capacity(2)
            rag_service._wait_for_rate_capacity(2)
        sleep.assert_called_once_with(60.0)
        rag_service._embedding_rate_events.clear()

        class FakeResponse:
            def __init__(self, status_code):
                self.status_code = status_code
                self.headers = {"Retry-After": "0"}

            @staticmethod
            def json():
                return {"embeddings": [{"values": [1.0] + [0.0] * 767}]}

            def raise_for_status(self):
                if self.status_code != 200:
                    raise AssertionError("429 response should be retried before status validation")

        class RetryClient:
            def __init__(self):
                self.calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def post(self, *_args, **_kwargs):
                self.calls += 1
                return FakeResponse(429 if self.calls == 1 else 200)

        retry_client = RetryClient()
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True), patch.object(
            rag_service.httpx, "Client", return_value=retry_client
        ), patch.object(rag_service.time, "sleep") as retry_sleep:
            vector = rag_service.create_embedding("retry question")
        self.assertEqual(retry_client.calls, 2)
        retry_sleep.assert_called_once_with(0.0)
        self.assertEqual(vector.shape, (768,))
        rag_service._embedding_rate_events.clear()

    def test_embedding_batches_are_bounded_and_timeout_is_configured(self):
        class FakeResponse:
            status_code = 200
            headers = {}

            def __init__(self, count):
                self.count = count

            def json(self):
                return {"embeddings": [
                    {"values": [1.0] + [0.0] * 767}
                    for _ in range(self.count)
                ]}

            @staticmethod
            def raise_for_status():
                return None

        class FakeClient:
            def __init__(self):
                self.batch_sizes = []
                self.timeout = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def post(self, _url, headers, json):
                self.batch_sizes.append(len(json["requests"]))
                return FakeResponse(len(json["requests"]))

        client = FakeClient()
        rag_service._embedding_rate_events.clear()
        with patch.dict("os.environ", {
            "GEMINI_API_KEY": "test-key",
            "GEMINI_EMBEDDING_BATCH_SIZE": "2",
            "GEMINI_EMBEDDING_TIMEOUT_SECONDS": "7",
        }, clear=True), patch.object(
            rag_service.httpx, "Client", side_effect=lambda timeout: setattr(client, "timeout", timeout) or client
        ):
            vectors = rag_service.generate_embeddings(["a", "b", "c", "d", "e"])
        self.assertEqual(client.batch_sizes, [2, 2, 1])
        self.assertEqual(client.timeout, 7.0)
        self.assertEqual(vectors.shape, (5, 768))
        rag_service._embedding_rate_events.clear()


if __name__ == "__main__":
    unittest.main()
