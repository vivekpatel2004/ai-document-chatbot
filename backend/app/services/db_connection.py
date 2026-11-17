"""Database provider configuration and connection lifecycle.

Application queries use the DB-API surface exposed here. Provider-specific
credentials and connection settings stay on the backend.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env", override=False)
load_dotenv(BACKEND_DIR.parent / ".env", override=False)

DEFAULT_DB_PATH = BACKEND_DIR / "database" / "chatbot.db"
# Kept for existing local scripts and callers that still inspect DB_PATH.
DB_PATH = Path(os.getenv("DB_PATH", str(DEFAULT_DB_PATH)))
_request_connection: ContextVar["_RequestConnection | None"] = ContextVar(
    "request_database_connection",
    default=None,
)


def _provider() -> str:
    provider = os.getenv("DATABASE_PROVIDER", "sqlite").strip().lower()
    if provider in {"sqlite", "turso", "libsql"}:
        return "turso" if provider in {"turso", "libsql"} else "sqlite"
    raise ValueError("DATABASE_PROVIDER must be 'sqlite' or 'turso'.")


def current_provider() -> str:
    """Return the normalized provider name without connection details."""
    return _provider()


def connection_target() -> tuple[str, str, str]:
    """Return safe startup-log details without exposing credentials or URLs."""
    provider = _provider()
    if provider == "turso":
        url = os.getenv("DATABASE_URL", "").strip()
        host = urlsplit(url).hostname if url else None
        return "Turso", host or "configured host unavailable", "DATABASE_URL"

    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        source = "DATABASE_URL"
    elif os.getenv("DB_PATH", "").strip():
        source = "DB_PATH"
    else:
        source = "default"
    return "SQLite", str(_sqlite_path().resolve()), source


def _sqlite_path() -> Path:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return DB_PATH
    if url.startswith("sqlite:///"):
        value = url[len("sqlite:///"):]
        # sqlite:////absolute/path is the canonical absolute URL form.
        if url.startswith("sqlite:////"):
            value = "/" + value.lstrip("/")
        return Path(value)
    if url.startswith("file:"):
        return Path(url[len("file:"):])
    return Path(url)


def _open_connection():
    """Create and configure a provider connection."""
    provider = _provider()
    if provider == "sqlite":
        path = _sqlite_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Request middleware may close the connection from the ASGI thread
        # after a synchronous dependency used it in Starlette's worker pool.
        connection = sqlite3.connect(
            str(path), timeout=30, check_same_thread=False
        )
    else:
        url = os.getenv("DATABASE_URL", "").strip()
        token = os.getenv("DATABASE_AUTH_TOKEN") or os.getenv("TURSO_AUTH_TOKEN")
        if not url:
            raise ValueError("DATABASE_URL is required when DATABASE_PROVIDER=turso.")
        if not token:
            raise ValueError(
                "DATABASE_AUTH_TOKEN (or TURSO_AUTH_TOKEN) is required for Turso."
            )
        try:
            import turso_serverless
        except ImportError as exc:
            raise RuntimeError(
                "Turso provider selected but 'turso_serverless' is not installed."
            ) from exc
        connection = turso_serverless.connect(url, auth_token=token)

    try:
        # Foreign key enforcement is connection scoped in SQLite-compatible DBs.
        connection.execute("PRAGMA foreign_keys = ON")
        if provider == "sqlite":
            connection.execute("PRAGMA busy_timeout = 30000")
    except Exception:
        connection.close()
        raise
    return connection


class _RequestConnection:
    """Reuse request connections per execution thread for DB-API safety."""

    def __init__(self) -> None:
        self._connections: dict[int, Any] = {}
        self._lock = threading.RLock()
        self._turso_connection: Any | None = None
        self._turso_use_lock = threading.RLock()

    def acquire(self):
        if _provider() == "turso":
            with self._lock:
                if self._turso_connection is None:
                    self._turso_connection = _open_connection()
                return self._turso_connection
        thread_id = threading.get_ident()
        with self._lock:
            if thread_id not in self._connections:
                self._connections[thread_id] = _open_connection()
            return self._connections[thread_id]

    def close(self) -> None:
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            if self._turso_connection is not None:
                connections.append(self._turso_connection)
                self._turso_connection = None
        for connection in connections:
            connection.close()


@contextmanager
def request_connection_scope():
    """Share DB-API connection setup across related calls in one request."""
    existing = _request_connection.get()
    if existing is not None:
        yield existing
        return

    request_connection = _RequestConnection()
    token = _request_connection.set(request_connection)
    try:
        yield request_connection
    finally:
        _request_connection.reset(token)
        request_connection.close()


class RequestDatabaseSessionMiddleware:
    """Keep a lazy connection alive through the complete HTTP/SSE request."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        with request_connection_scope():
            await self.app(scope, receive, send)


@contextmanager
def get_connection():
    """Yield a transaction on the request connection or a one-off connection."""
    request_connection = _request_connection.get()
    owns_connection = request_connection is None
    use_lock = request_connection is not None and _provider() == "turso"
    if use_lock:
        request_connection._turso_use_lock.acquire()
    try:
        connection = (
            _open_connection()
            if request_connection is None
            else request_connection.acquire()
        )
        try:
            yield connection
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        finally:
            if owns_connection:
                connection.close()
    finally:
        if use_lock:
            request_connection._turso_use_lock.release()
