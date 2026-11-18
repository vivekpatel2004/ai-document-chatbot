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
from app.services.performance import measure_stage, record_pipeline

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
    with measure_stage("database_connect"):
        return _create_connection()


def _create_connection():
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


def execute_transaction_batch(connection, statements, *, commit: bool = True):
    """Run bound SQL statements in one Turso pipeline transaction.

    The installed Turso client exposes its Hrana pipeline on the session and
    the matching parameter encoder in ``turso_serverless.protocol``. The
    normal DB-API methods remain the fallback for SQLite. Pipeline steps are
    conditional so a failed statement skips later writes and rolls back.

    With ``commit=False``, a successful transaction remains open for the
    caller to validate row counts before the surrounding get_connection
    context commits it.
    """
    statements = list(statements)
    if not statements:
        return []

    driver_connection = connection
    while hasattr(driver_connection, "_connection"):
        driver_connection = driver_connection._connection

    session = getattr(driver_connection, "_session", None)
    normalized_statements = [
        (statement[0], statement[1], bool(statement[2]) if len(statement) > 2 else False)
        for statement in statements
    ]
    if session is None:
        output = []
        for sql, params, want_rows in normalized_statements:
            cursor = connection.execute(sql, params)
            if want_rows:
                output.append({"rowcount": cursor.rowcount, "rows": cursor.fetchall()})
            else:
                output.append(getattr(cursor, "rowcount", 0))
        return output

    from turso_serverless.protocol import ProtocolError, build_batch_step, decode_value

    steps = [build_batch_step("BEGIN", want_rows=False)]
    for index, (sql, params, want_rows) in enumerate(normalized_statements, start=1):
        steps.append(build_batch_step(
            sql,
            args=list(params),
            want_rows=want_rows,
            condition={"type": "ok", "step": index - 1},
        ))

    last_statement_step = len(statements)
    if commit:
        commit_step = len(steps)
        steps.append(build_batch_step(
            "COMMIT",
            want_rows=False,
            condition={"type": "ok", "step": last_statement_step},
        ))
        rollback_condition = {"type": "not", "cond": {"type": "ok", "step": commit_step}}
    else:
        rollback_condition = {
            "type": "not",
            "cond": {"type": "ok", "step": last_statement_step},
        }
    steps.append(build_batch_step(
        "ROLLBACK", want_rows=False, condition=rollback_condition
    ))

    with measure_stage("database_pipeline"):
        for attempt in range(2):
            record_pipeline(len(statements))
            try:
                results = session.execute_pipeline([
                    {"type": "batch", "batch": {"steps": steps}}
                ])
                break
            except ProtocolError as exc:
                message = str(exc).casefold()
                expired_stream = (
                    "404" in message
                    and "stream not found" in message
                )
                if attempt or not expired_stream:
                    raise

                # Session._post() resets its baton on every HTTP error. A
                # stream-not-found response means this pipeline was rejected
                # before its statements ran; its prior transaction no longer
                # exists. Re-establish the connection-scoped FK setting on the
                # new stream, then retry the complete atomic batch once.
                with measure_stage("database_stream_recovery"):
                    driver_connection.execute("PRAGMA foreign_keys = ON")

    if len(results) != 1:
        raise RuntimeError("Turso pipeline returned an unexpected result count.")
    pipeline_result = results[0]
    if pipeline_result.get("type") == "error":
        error = pipeline_result.get("error") or {}
        raise RuntimeError(error.get("message", "Turso pipeline failed."))

    response = pipeline_result.get("response") or {}
    if response.get("type") != "batch":
        raise RuntimeError("Turso pipeline did not return a batch result.")
    batch_result = response.get("result") or {}
    step_results = batch_result.get("step_results") or []
    step_errors = batch_result.get("step_errors") or []
    if len(step_results) != len(steps) or len(step_errors) != len(steps):
        raise RuntimeError("Turso pipeline returned incomplete batch results.")

    errors = [error for error in step_errors if error]
    if errors:
        raise RuntimeError(errors[0].get("message", "Turso batch statement failed."))

    output = []
    for index, (_sql, _params, want_rows) in enumerate(normalized_statements, start=1):
        result = step_results[index] or {}
        if want_rows:
            output.append({
                "rowcount": result.get("affected_row_count", 0),
                "rows": [
                    tuple(decode_value(value) for value in row)
                    for row in result.get("rows", [])
                ],
            })
        else:
            output.append(result.get("affected_row_count", 0))
    return output


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
            with measure_stage("database_commit"):
                connection.commit()
        except Exception:
            try:
                with measure_stage("database_rollback"):
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
