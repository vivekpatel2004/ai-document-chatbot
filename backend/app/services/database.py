
import json
import hashlib
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from app.services.db_connection import DB_PATH, current_provider, get_connection
from app.services.performance import measure_stage


def get_db():
    """Compatibility alias for the database connection context manager."""
    @contextmanager
    def measured_connection():
        with measure_stage("database"):
            with get_connection() as connection:
                yield connection

    return measured_connection()


_DOCUMENT_CACHE_TTL_SECONDS = 10.0
_DOCUMENT_CACHE_MAX_ENTRIES = 512
_document_cache_lock = threading.RLock()
_document_cache: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
_document_list_cache: OrderedDict[
    tuple[str, str | None], tuple[float, list[dict]]
] = OrderedDict()


def _cache_read(cache: OrderedDict, key):
    now = time.monotonic()
    with _document_cache_lock:
        value = cache.get(key)
        if value is None:
            return None
        expires_at, payload = value
        if expires_at <= now:
            cache.pop(key, None)
            return None
        cache.move_to_end(key)
        return payload


def _cache_write(cache: OrderedDict, key, payload) -> None:
    with _document_cache_lock:
        cache[key] = (time.monotonic() + _DOCUMENT_CACHE_TTL_SECONDS, payload)
        cache.move_to_end(key)
        while len(cache) > _DOCUMENT_CACHE_MAX_ENTRIES:
            cache.popitem(last=False)


def _invalidate_document_cache(owner_id: str | None = None, document_id: str | None = None) -> None:
    """Invalidate private document metadata and list entries after mutations."""
    with _document_cache_lock:
        if owner_id is None and document_id is None:
            _document_cache.clear()
            _document_list_cache.clear()
            return
        if document_id is not None:
            for key in list(_document_cache):
                if key[1] == document_id and (owner_id is None or key[0] == owner_id):
                    _document_cache.pop(key, None)
        elif owner_id is not None:
            for key in list(_document_cache):
                if key[0] == owner_id:
                    _document_cache.pop(key, None)
        for key in list(_document_list_cache):
            if owner_id is None or key[0] == owner_id:
                _document_list_cache.pop(key, None)


def _row_to_dict(row, description):
    if row is None:
        return None
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return {column[0]: value for column, value in zip(description, row)}


def init_db():
    with get_db() as conn:
        if current_provider() == "sqlite":
            conn.execute("PRAGMA journal_mode = WAL")
        # A write lock serializes concurrent startup migrations on both
        # SQLite and hosted SQLite-compatible providers.
        conn.execute("BEGIN IMMEDIATE")

        schema_script = """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New Chat',
            owner_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            conversation_id TEXT,
            owner_id TEXT,
            content_hash TEXT,
            filename TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            total_pages INTEGER NOT NULL DEFAULT 0,
            total_chunks INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id)
                REFERENCES conversations(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            page_number INTEGER,
            embedding BLOB,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (document_id)
                REFERENCES documents(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id)
                REFERENCES conversations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_documents_conversation
            ON documents(conversation_id);

        CREATE INDEX IF NOT EXISTS idx_chunks_document
            ON chunks(document_id);

        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id);

        CREATE INDEX IF NOT EXISTS idx_conversations_updated
            ON conversations(updated_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_expiry
            ON sessions(expires_at);
        """
        # Each statement is executed separately so the same migration works
        # with DB-API drivers that do not implement sqlite3.executescript.
        for statement in schema_script.split(";"):
            if statement.strip():
                conn.execute(statement)

        # Add ownership columns to existing databases without changing any
        # legacy rows. NULL ownership is intentionally inaccessible to users.
        for table in ("conversations", "documents"):
            cursor = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            )
            row = cursor.fetchone()
            definition = (row[0] if row else "") or ""
            if not re.search(r"\bowner_id\b", definition, re.IGNORECASE):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
            if table == "documents" and not re.search(
                r"\bcontent_hash\b", definition, re.IGNORECASE
            ):
                conn.execute("ALTER TABLE documents ADD COLUMN content_hash TEXT")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner_id, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id, created_at)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_owner_hash ON documents(owner_id, content_hash) WHERE content_hash IS NOT NULL")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_documents (
                conversation_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                PRIMARY KEY (conversation_id, document_id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_documents_owner ON conversation_documents(owner_id, conversation_id)")
        conn.execute("""
            INSERT OR IGNORE INTO conversation_documents (conversation_id, document_id, owner_id)
            SELECT d.conversation_id, d.id, d.owner_id
            FROM documents d JOIN conversations c
              ON c.id = d.conversation_id AND c.owner_id = d.owner_id
            WHERE d.conversation_id IS NOT NULL AND d.owner_id IS NOT NULL
        """)



def execute(query: str, params: tuple = ()) -> int:
    with get_db() as conn:
        cursor = conn.execute(query, params)
        return cursor.rowcount


def fetch_one(query: str, params: tuple = ()):
    with get_db() as conn:
        cursor = conn.execute(query, params)
        return _row_to_dict(cursor.fetchone(), cursor.description)


def fetch_all(query: str, params: tuple = ()):
    with get_db() as conn:
        cursor = conn.execute(query, params)
        return [_row_to_dict(row, cursor.description) for row in cursor.fetchall()]


# Conversations

def create_conversation(conversation_id: str, title: str = "New Chat", owner_id: str | None = None):
    execute(
        "INSERT INTO conversations (id, title, owner_id) VALUES (?, ?, ?)",
        (conversation_id, title, owner_id),
    )


def get_conversation(conversation_id: str):
    return fetch_one(
        "SELECT * FROM conversations WHERE id = ?",
        (conversation_id,),
    )


def list_conversations(owner_id: str | None = None):
    return fetch_all("""
        SELECT c.*,
            (SELECT COUNT(*) FROM conversation_documents cd
             WHERE cd.conversation_id = c.id AND cd.owner_id = c.owner_id) AS document_count,
            (SELECT COUNT(*) FROM messages m
             WHERE m.conversation_id = c.id) AS message_count
        FROM conversations c
        WHERE c.owner_id = ?
        ORDER BY c.updated_at DESC, c.rowid DESC
    """, (owner_id,))


def rename_conversation(conversation_id: str, title: str):
    return execute("""
        UPDATE conversations
        SET title = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (title, conversation_id))


def delete_conversation(conversation_id: str, owner_id: str | None = None):
    if owner_id is None:
        result = execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    else:
        result = execute(
            "DELETE FROM conversations WHERE id = ? AND owner_id = ?",
            (conversation_id, owner_id),
        )
    if result:
        _invalidate_document_cache(owner_id)
    return result


def clear_conversation(conversation_id: str):
    return execute(
        "DELETE FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    )


def claim_unowned_data(owner_id: str) -> tuple[int, int]:
    """Assign legacy ownerless conversations and documents atomically."""
    with get_db() as conn:
        conversations = conn.execute(
            "UPDATE conversations SET owner_id = ? WHERE owner_id IS NULL",
            (owner_id,),
        ).rowcount
        documents = conn.execute(
            "UPDATE documents SET owner_id = ? WHERE owner_id IS NULL",
            (owner_id,),
        ).rowcount
        # Existing document/chat relationships can now be safely backfilled.
        conn.execute("""
            INSERT OR IGNORE INTO conversation_documents
                (conversation_id, document_id, owner_id)
            SELECT d.conversation_id, d.id, d.owner_id
            FROM documents d JOIN conversations c
              ON c.id = d.conversation_id AND c.owner_id = d.owner_id
            WHERE d.conversation_id IS NOT NULL AND d.owner_id = ?
        """, (owner_id,))
        return conversations, documents


# Documents

def create_document(
    document_id: str,
    conversation_id: str | None,
    filename: str,
    file_type: str,
    file_size: int,
    file_path: str,
    owner_id: str | None = None,
    content_hash: str | None = None,
):
    execute("""
        INSERT INTO documents (
            id, conversation_id, owner_id, content_hash, filename,
            file_type, file_size, file_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        document_id, conversation_id, owner_id, content_hash, filename,
        file_type, file_size, file_path,
    ))
    _invalidate_document_cache(owner_id, document_id)

    if conversation_id and owner_id:
        attach_document_to_conversation(document_id, conversation_id, owner_id)


def find_document_by_hash(owner_id: str, content_hash: str, file_size: int):
    """Find an identical owned upload, backfilling its hash lazily if needed."""
    candidates = fetch_all("""
        SELECT * FROM (
            SELECT * FROM documents
            WHERE owner_id = ? AND file_size = ? AND content_hash = ?
            UNION ALL
            SELECT * FROM documents
            WHERE owner_id = ? AND file_size = ? AND content_hash IS NULL
        ) candidates
        ORDER BY CASE WHEN content_hash = ? THEN 0 ELSE 1 END, created_at DESC
    """, (owner_id, file_size, content_hash, owner_id, file_size, content_hash))
    for document in candidates:
        if document.get("content_hash") == content_hash:
            return document
        path = Path(document["file_path"])
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() == content_hash:
            execute("UPDATE documents SET content_hash = ? WHERE id = ? AND owner_id = ? AND content_hash IS NULL", (content_hash, document["id"], owner_id))
            _invalidate_document_cache(owner_id, document["id"])
            return fetch_one("SELECT * FROM documents WHERE id = ? AND owner_id = ?", (document["id"], owner_id))
    return None


def get_document(document_id: str):
    return fetch_one(
        "SELECT * FROM documents WHERE id = ?",
        (document_id,),
    )


def get_documents_for_owner(document_ids: list[str], owner_id: str) -> list[dict]:
    """Return owned documents in caller order with one cache-aware query."""
    unique_ids = list(dict.fromkeys(document_ids))
    if not unique_ids:
        return []

    found: dict[str, dict] = {}
    missing = []
    for document_id in unique_ids:
        cached = _cache_read(_document_cache, (owner_id, document_id))
        if cached is None:
            missing.append(document_id)
        else:
            found[document_id] = cached

    for start in range(0, len(missing), 500):
        batch = missing[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = fetch_all(
            f"SELECT * FROM documents WHERE owner_id = ? AND id IN ({placeholders})",
            (owner_id, *batch),
        )
        for document in rows:
            found[document["id"]] = document
            _cache_write(_document_cache, (owner_id, document["id"]), document)

    return [dict(found[document_id]) for document_id in unique_ids if document_id in found]


def get_document_for_owner(document_id: str, owner_id: str):
    documents = get_documents_for_owner([document_id], owner_id)
    return documents[0] if documents else None


def list_documents(conversation_id: str | None = None, owner_id: str | None = None):
    if owner_id is not None:
        cached = _cache_read(_document_list_cache, (owner_id, conversation_id))
        if cached is not None:
            return [dict(document) for document in cached]
    if conversation_id:
        documents = fetch_all("""
            SELECT d.* FROM documents d
            JOIN conversation_documents cd ON cd.document_id = d.id
            WHERE cd.conversation_id = ? AND cd.owner_id = ? AND d.owner_id = ?
            ORDER BY d.created_at DESC, d.rowid DESC
        """, (conversation_id, owner_id, owner_id))
        if owner_id is not None:
            _cache_write(_document_list_cache, (owner_id, conversation_id), documents)
        return documents

    documents = fetch_all(
        "SELECT * FROM documents WHERE owner_id = ? ORDER BY created_at DESC, rowid DESC",
        (owner_id,),
    )
    if owner_id is not None:
        _cache_write(_document_list_cache, (owner_id, conversation_id), documents)
    return documents


def get_conversation_documents_for_owner(
    conversation_id: str, owner_id: str
) -> tuple[bool, list[dict]]:
    """Validate a conversation and list its owned documents in one read."""
    rows = fetch_all("""
        SELECT c.id AS conversation_exists, d.*
        FROM conversations c
        LEFT JOIN conversation_documents cd
          ON cd.conversation_id = c.id AND cd.owner_id = c.owner_id
        LEFT JOIN documents d
          ON d.id = cd.document_id AND d.owner_id = c.owner_id
        WHERE c.id = ? AND c.owner_id = ?
        ORDER BY d.created_at DESC, d.rowid DESC
    """, (conversation_id, owner_id))
    if not rows:
        return False, []
    documents = []
    for row in rows:
        if row.get("id") is None:
            continue
        row.pop("conversation_exists", None)
        documents.append(row)
        _cache_write(_document_cache, (owner_id, row["id"]), row)
    _cache_write(_document_list_cache, (owner_id, conversation_id), documents)
    return True, documents


def update_document_status(
    document_id: str,
    status: str,
    total_pages: int = 0,
    total_chunks: int = 0,
):
    result = execute("""
        UPDATE documents
        SET status = ?, total_pages = ?, total_chunks = ?
        WHERE id = ?
    """, (status, total_pages, total_chunks, document_id))
    _invalidate_document_cache(document_id=document_id)
    return result


def attach_document_to_conversation(document_id: str, conversation_id: str, owner_id: str):
    attached = attach_documents_to_conversation(
        [document_id], conversation_id, owner_id
    )
    return int(document_id in attached)


def attach_documents_to_conversation(
    document_ids: list[str], conversation_id: str, owner_id: str
) -> set[str]:
    """Validate and attach multiple owned documents in bounded SQL calls."""
    unique_ids = list(dict.fromkeys(document_ids))
    if not unique_ids:
        return set()
    attached_ids = set()
    # 250 document ids stay comfortably below SQLite/libSQL parameter limits.
    for start in range(0, len(unique_ids), 250):
        batch_ids = unique_ids[start:start + 250]
        placeholders = ",".join("?" for _ in batch_ids)
        with get_db() as conn:
            valid_rows = conn.execute(f"""
                SELECT d.id FROM documents d
                JOIN conversations c ON c.id = ? AND c.owner_id = ?
                WHERE d.owner_id = ? AND d.id IN ({placeholders})
            """, (conversation_id, owner_id, owner_id, *batch_ids)).fetchall()
            valid_ids = {row[0] for row in valid_rows}
            if not valid_ids:
                continue
            ordered_valid_ids = [item for item in batch_ids if item in valid_ids]
            conn.execute(f"""
                INSERT OR IGNORE INTO conversation_documents
                    (conversation_id, document_id, owner_id)
                VALUES {','.join('(?, ?, ?)' for _ in ordered_valid_ids)}
            """, tuple(value for document_id in ordered_valid_ids
                       for value in (conversation_id, document_id, owner_id)))
            valid_placeholders = ",".join("?" for _ in ordered_valid_ids)
            conn.execute(f"""
                UPDATE documents SET conversation_id = COALESCE(conversation_id, ?)
                WHERE id IN ({valid_placeholders}) AND owner_id = ?
            """, (conversation_id, *ordered_valid_ids, owner_id))
            attached_ids.update(valid_ids)
    _invalidate_document_cache(owner_id)
    return attached_ids


def delete_document(document_id: str, owner_id: str | None = None):
    if owner_id is None:
        result = execute("DELETE FROM documents WHERE id = ?", (document_id,))
    else:
        result = execute(
            "DELETE FROM documents WHERE id = ? AND owner_id = ?",
            (document_id, owner_id),
        )
    if result:
        _invalidate_document_cache(owner_id, document_id)
    return result


# Chunks

def add_chunk(
    chunk_id: str,
    document_id: str,
    chunk_index: int,
    content: str,
    page_number: int | None = None,
    embedding: bytes | None = None,
):
    execute("""
        INSERT INTO chunks (
            id, document_id, chunk_index,
            content, page_number, embedding
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        chunk_id, document_id, chunk_index,
        content, page_number, embedding,
    ))


def add_chunks(document_id: str, chunks: list[dict], embeddings: list[bytes]) -> None:
    """Persist chunk metadata and embeddings in bounded multi-row inserts.

    A single transaction is used for the whole document. Multi-value INSERTs
    are intentional: the Turso DB-API executemany implementation sends one
    request per row, while each execute here sends at most 50 rows at once.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("Chunk and embedding counts must match.")
    if not chunks:
        return

    batch_size = 50
    row_sql = "(?, ?, ?, ?, ?, ?)"
    with get_db() as conn:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            batch_embeddings = embeddings[start:start + batch_size]
            values = ",".join(row_sql for _ in batch)
            params = []
            for chunk, embedding in zip(batch, batch_embeddings):
                params.extend((
                    chunk["chunk_id"], document_id, chunk["chunk_index"],
                    chunk["content"], chunk.get("page_number"), embedding,
                ))
            conn.execute(f"""
                INSERT INTO chunks (
                    id, document_id, chunk_index, content, page_number, embedding
                ) VALUES {values}
            """, tuple(params))


def get_document_chunks(document_id: str):
    return fetch_all("""
        SELECT * FROM chunks
        WHERE document_id = ?
        ORDER BY chunk_index
    """, (document_id,))


def get_chunks_for_documents(document_ids: list[str]):
    if not document_ids:
        return []

    placeholders = ",".join("?" for _ in document_ids)

    return fetch_all(f"""
        SELECT chunks.*, documents.filename
        FROM chunks
        JOIN documents ON documents.id = chunks.document_id
        WHERE chunks.document_id IN ({placeholders})
        ORDER BY documents.filename, chunks.chunk_index
    """, tuple(document_ids))


# Messages

def add_message(
    message_id: str,
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
):
    execute("""
        INSERT INTO messages (
            id, conversation_id, role, content, sources
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        message_id,
        conversation_id,
        role,
        content,
        json.dumps(sources or []),
    ))

    execute("""
        UPDATE conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (conversation_id,))


def get_messages(conversation_id: str):
    messages = fetch_all("""
        SELECT * FROM messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC, rowid ASC
    """, (conversation_id,))

    for message in messages:
        try:
            message["sources"] = json.loads(message["sources"])
        except (TypeError, json.JSONDecodeError):
            message["sources"] = []

    return messages
