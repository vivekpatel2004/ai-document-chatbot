"""Tests for the shared HTTP client's lifecycle and streaming behavior."""
import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.services import llm_service


class LLMClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await llm_service.close_shared_http_client()

    async def test_non_streaming_request_reuses_application_client(self):
        response_body = {
            "candidates": [{"content": {"parts": [{"text": "A concise answer."}]}}]
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_body)
        )
        client = httpx.AsyncClient(transport=transport, timeout=10)
        with patch.object(llm_service, "_shared_http_client", client), patch.dict(
            os.environ, {"GOOGLE_API_KEY": "test-key", "LLM_PROVIDER": "gemini"}
        ):
            result = await llm_service.generate_response("Question")
            self.assertEqual(result["content"], "A concise answer.")
            self.assertFalse(client.is_closed)
            await llm_service.close_shared_http_client()
            self.assertTrue(client.is_closed)

    async def test_gemini_stream_uses_and_preserves_shared_client(self):
        payload = {
            "candidates": [{"content": {"parts": [{"text": "streamed token"}]}}]
        }
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(payload)}\n\n".encode(),
        ))
        client = httpx.AsyncClient(transport=transport, timeout=10)
        with patch.object(llm_service, "_shared_http_client", client), patch.dict(
            os.environ, {"GOOGLE_API_KEY": "test-key", "LLM_PROVIDER": "gemini"}
        ):
            stream = await llm_service.generate_response("Question", stream=True)
            tokens = [token async for token in stream]
            self.assertEqual(tokens, ["streamed token"])
            self.assertFalse(client.is_closed)
            await llm_service.close_shared_http_client()
            self.assertTrue(client.is_closed)


if __name__ == "__main__":
    unittest.main()
