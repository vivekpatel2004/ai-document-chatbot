
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import httpx
from app.services.performance import record_stage


logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

REQUEST_TIMEOUT = 120.0
_shared_http_client: Optional[httpx.AsyncClient] = None


def open_shared_http_client() -> httpx.AsyncClient:
    """Create the application-scoped HTTP client during startup."""
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    return _shared_http_client


async def close_shared_http_client() -> None:
    """Close the application-scoped HTTP client during shutdown."""
    global _shared_http_client
    client = _shared_http_client
    _shared_http_client = None
    if client is not None and not client.is_closed:
        await client.aclose()


@asynccontextmanager
async def _http_client():
    """Use the app-scoped client, with a safe fallback outside lifespan."""
    client = _shared_http_client
    if client is not None and not client.is_closed:
        yield client
        return

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as temporary_client:
        yield temporary_client


class LLMServiceError(Exception):
    """Raised when an LLM provider request fails."""


def _get_provider(provider: Optional[str] = None) -> str:
    selected = (
        provider or os.getenv("LLM_PROVIDER", "gemini")
    ).strip().lower()

    if selected not in {"gemini", "openai"}:
        raise LLMServiceError(
            f"Unsupported AI provider: {selected}"
        )

    return selected


def _get_api_key(provider: str) -> str:
    if provider == "gemini":
        api_key = (
            os.getenv("GOOGLE_API_KEY")
            or os.getenv("GEMINI_API_KEY")
        )
    else:
        api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise LLMServiceError(
            f"Missing API key for {provider}. Check your .env file."
        )

    return api_key


def _get_model(
    provider: str,
    model: Optional[str] = None,
) -> str:
    if model:
        return model.strip()

    if provider == "gemini":
        return os.getenv(
            "GEMINI_MODEL",
            DEFAULT_GEMINI_MODEL,
        ).strip()

    return os.getenv(
        "OPENAI_MODEL",
        DEFAULT_OPENAI_MODEL,
    ).strip()


def _build_messages(prompt: str, history: Optional[list] = None):
    messages = [
        {
            "role": "system",
            "content": (
                "You are an accurate AI document assistant. "
                "Follow the user's instructions and use supplied "
                "document context carefully. Never invent citations "
                "or claim that unsupported information exists."
            ),
        }
    ]

    if history:
        for item in history[-10:]:
            role = item.get("role")
            content = item.get("content", "")

            if role not in {"user", "assistant"} or not content:
                continue

            messages.append({
                "role": role,
                "content": str(content),
            })

    messages.append({
        "role": "user",
        "content": prompt,
    })

    return messages


def _build_gemini_contents(
    prompt: str,
    history: Optional[list] = None,
):
    contents = []

    if history:
        for item in history[-10:]:
            role = item.get("role")
            content = item.get("content", "")

            if role not in {"user", "assistant"} or not content:
                continue

            contents.append({
                "role": "user" if role == "user" else "model",
                "parts": [{"text": str(content)}],
            })

    contents.append({
        "role": "user",
        "parts": [{"text": prompt}],
    })

    return contents


def _extract_gemini_text(data: dict) -> str:
    candidates = data.get("candidates", [])

    if not candidates:
        feedback = data.get("promptFeedback", {})
        block_reason = feedback.get("blockReason")

        if block_reason:
            raise LLMServiceError(
                f"Gemini blocked the request: {block_reason}"
            )

        raise LLMServiceError(
            "Gemini returned no response candidates."
        )

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    return "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
    )


async def _generate_gemini(
    prompt: str,
    history: Optional[list],
    model: str,
    api_key: str,
    temperature: float,
) -> str:
    url = (
        f"{GEMINI_BASE_URL}/{model}:generateContent"
        f"?key={api_key}"
    )

    payload = {
        "contents": _build_gemini_contents(prompt, history),
        "generationConfig": {
            "temperature": temperature,
        },
    }

    started = time.perf_counter()
    try:
        async with _http_client() as client:
            response = await client.post(url, json=payload)
        if response.is_error:
            logger.error(
                "Gemini API error: %s",
                response.text[:1000],
            )
            raise LLMServiceError(
                f"Gemini API returned HTTP {response.status_code}."
            )
        return _extract_gemini_text(response.json())
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        record_stage("gemini_total_generation", elapsed_ms)
        logger.info("gemini_generation provider=gemini stream=false total_ms=%.1f", elapsed_ms)


async def _generate_openai(
    prompt: str,
    history: Optional[list],
    model: str,
    api_key: str,
    temperature: float,
) -> str:
    payload = {
        "model": model,
        "messages": _build_messages(prompt, history),
        "temperature": temperature,
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with _http_client() as client:
        response = await client.post(
            OPENAI_URL,
            headers=headers,
            json=payload,
        )

    if response.is_error:
        logger.error(
            "OpenAI API error: %s",
            response.text[:1000],
        )
        raise LLMServiceError(
            f"OpenAI API returned HTTP {response.status_code}."
        )

    data = response.json()
    choices = data.get("choices", [])

    if not choices:
        raise LLMServiceError(
            "OpenAI returned an empty response."
        )

    return choices[0]["message"]["content"] or ""


async def _stream_openai(
    prompt: str,
    history: Optional[list],
    model: str,
    api_key: str,
    temperature: float,
) -> AsyncGenerator[str, None]:
    payload = {
        "model": model,
        "messages": _build_messages(prompt, history),
        "temperature": temperature,
        "stream": True,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with _http_client() as client:
        async with client.stream(
            "POST",
            OPENAI_URL,
            headers=headers,
            json=payload,
        ) as response:

            if response.is_error:
                error_body = await response.aread()

                logger.error(
                    "OpenAI streaming error: %s",
                    error_body[:1000],
                )

                raise LLMServiceError(
                    f"OpenAI API returned HTTP {response.status_code}."
                )

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue

                data = line[5:].strip()

                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                    choices = chunk.get("choices", [])

                    if not choices:
                        continue

                    token = (
                        choices[0]
                        .get("delta", {})
                        .get("content")
                    )

                    if token:
                        yield token

                except json.JSONDecodeError:
                    logger.warning(
                        "Ignoring invalid OpenAI stream chunk."
                    )


async def _stream_gemini(
    prompt: str,
    history: Optional[list],
    model: str,
    api_key: str,
    temperature: float,
) -> AsyncGenerator[str, None]:
    started = time.perf_counter()
    first_token_ms = None
    url = (
        f"{GEMINI_BASE_URL}/{model}:streamGenerateContent"
        f"?alt=sse&key={api_key}"
    )

    payload = {
        "contents": _build_gemini_contents(prompt, history),
        "generationConfig": {
            "temperature": temperature,
        },
    }

    try:
        async with _http_client() as client:
            async with client.stream(
                "POST",
                url,
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.is_error:
                    error_body = await response.aread()
                    logger.error(
                        "Gemini streaming error: %s",
                        error_body[:1000],
                    )
                    raise LLMServiceError(
                        f"Gemini API returned HTTP {response.status_code}."
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw_data = line[5:].strip()
                    if not raw_data or raw_data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(raw_data)
                        text = _extract_gemini_text(chunk)
                        if text:
                            if first_token_ms is None:
                                first_token_ms = (time.perf_counter() - started) * 1000
                                record_stage("gemini_time_to_first_token", first_token_ms)
                                logger.info(
                                    "gemini_stream provider=gemini ttft_ms=%.1f",
                                    first_token_ms,
                                )
                            # Yield each provider event immediately; no local buffering.
                            yield text
                    except json.JSONDecodeError:
                        logger.warning("Ignoring invalid Gemini stream chunk.")
    finally:
        total_ms = (time.perf_counter() - started) * 1000
        record_stage("gemini_total_generation", total_ms)
        logger.info(
            "gemini_stream provider=gemini ttft_ms=%s total_ms=%.1f",
            "unavailable" if first_token_ms is None else f"{first_token_ms:.1f}",
            total_ms,
        )


async def generate_response(
    prompt: str,
    history: Optional[list] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    stream: bool = False,
):
    """
    Generate an AI response using Gemini or OpenAI.

    Non-streaming:
        Returns a dictionary containing content.

    Streaming:
        Returns an async generator yielding text tokens.
    """

    if not prompt or not prompt.strip():
        raise LLMServiceError("Prompt cannot be empty.")

    if not 0 <= temperature <= 2:
        raise LLMServiceError(
            "Temperature must be between 0 and 2."
        )

    selected_provider = _get_provider(provider)
    api_key = _get_api_key(selected_provider)
    selected_model = _get_model(selected_provider, model)

    # Temporary diagnostic log. Never log the API key.
    logger.info(
        "LLM runtime config | provider=%s | model=%s | "
        "passed_model=%r | env_model=%r",
        selected_provider,
        selected_model,
        model,
        os.getenv("GEMINI_MODEL")
        if selected_provider == "gemini"
        else os.getenv("OPENAI_MODEL"),
    )

    if stream:
        if selected_provider == "openai":
            return _stream_openai(
                prompt,
                history,
                selected_model,
                api_key,
                temperature,
            )

        return _stream_gemini(
            prompt,
            history,
            selected_model,
            api_key,
            temperature,
        )

    try:
        if selected_provider == "openai":
            content = await _generate_openai(
                prompt,
                history,
                selected_model,
                api_key,
                temperature,
            )
        else:
            content = await _generate_gemini(
                prompt,
                history,
                selected_model,
                api_key,
                temperature,
            )

        return {
            "content": content,
            "provider": selected_provider,
            "model": selected_model,
        }

    except httpx.TimeoutException as exc:
        logger.exception("LLM request timed out")
        raise LLMServiceError(
            "AI provider request timed out. Please try again."
        ) from exc

    except httpx.RequestError as exc:
        logger.exception("LLM connection failed")
        raise LLMServiceError(
            "Could not connect to the AI provider."
        ) from exc
