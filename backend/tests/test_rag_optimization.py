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
             "embedding": vector.tobytes()},
            {"id": "dup-second", "document_id": "selected", "filename": "selected.pdf",
             "chunk_index": 1, "page_number": 5, "content": "Repeated   useful passage.",
             "embedding": vector.tobytes()},
            {"id": "other-doc", "document_id": "unselected", "filename": "other.pdf",
             "chunk_index": 0, "page_number": 1, "content": "Out of scope passage.",
             "embedding": vector.tobytes()},
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


if __name__ == "__main__":
    unittest.main()
