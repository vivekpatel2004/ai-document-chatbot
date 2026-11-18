# Configuration reference

The backend reads `backend/.env` first and then the repository-root `.env` as a local fallback. Real process environment variables take precedence. Copy `backend/.env.example` to `backend/.env`; never commit real credentials. The root `.env.example` is for the public Vite API URL only.

## Database

| Variable | Local value | Hosted value |
|---|---|---|
| `DATABASE_PROVIDER` | `sqlite` (default) | `turso` |
| `DATABASE_URL` | Optional SQLite path/URL | `libsql://<database-host>` |
| `DATABASE_AUTH_TOKEN` | Unset | Turso database token |
| `TURSO_AUTH_TOKEN` | Unset | Backward-compatible token alias |
| `DB_PATH` | Optional local SQLite path | Not used for Turso |

`DB_PATH` remains a SQLite fallback when `DATABASE_URL` is unset. Do not change SQLite data files during provider switching. The application stores users, sessions, documents, chunks/embeddings, conversations, messages, and conversation-document links in the configured database.

## AI provider

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `gemini` (default) or `openai` |
| `GEMINI_API_KEY` | Existing Gemini key used for chat and embeddings; `GOOGLE_API_KEY` remains an accepted alias |
| `GEMINI_MODEL` | Optional Gemini model override; current default is `gemini-3.8-flash` |
| `OPENAI_API_KEY` | Required only when selecting OpenAI for chat |
| `OPENAI_MODEL` | Optional OpenAI model override; current default is `gpt-4o-mini` |

Document embeddings use `gemini-embedding-001` with 768 dimensions. Document vectors use `RETRIEVAL_DOCUMENT`, question vectors use `RETRIEVAL_QUERY`, and both are L2-normalized before cosine similarity. The existing Gemini key is reused even when OpenAI is selected for chat. `OPENAI_API_KEY` does not select the embedding model.

On first startup after this upgrade, the database migration adds a model tag column without rewriting or deleting any embedding BLOBs. Untagged/legacy vectors are ignored by retrieval; tagged Gemini vectors are preserved. Chunk text and document data remain unchanged. Run `python -m scripts.reembed_gemini_embeddings --dry-run` to inspect counts without changing schema/data or calling Gemini, then run `python -m scripts.reembed_gemini_embeddings` from `backend/` to regenerate and persist missing vectors. The job reports progress and remaining counts, commits successful batches, and exits nonzero if any vectors remain; rerunning resumes unfinished work. Until it finishes, RAG generates compatible Gemini vectors on demand for missing chunks.

Optional embedding controls: `GEMINI_EMBEDDING_BATCH_SIZE` (1–100, default 64), `GEMINI_EMBEDDING_TIMEOUT_SECONDS` (positive seconds, default 30), and `GEMINI_EMBEDDING_REQUESTS_PER_MINUTE` (default 60). The limiter counts each text in a batch, waits on a rolling per-process window, and retries 429/5xx/network failures with bounded backoff and `Retry-After`. Tune this cap to the project's current Gemini embedding quota. Multiple backend processes/replicas do not share this in-memory limiter, so divide the project quota across them or use a shared limiter at higher scale.

## Document storage

| Variable | Purpose |
|---|---|
| `STORAGE_PROVIDER` | `local` (default) or `r2` |
| `R2_ENDPOINT` | HTTPS S3-compatible endpoint. For B2 use `https://s3.<region>.backblazeb2.com`; for Cloudflare R2 use the account endpoint. |
| `R2_ACCESS_KEY_ID` | S3-compatible application/access key ID |
| `R2_SECRET_ACCESS_KEY` | Secret access key; backend only |
| `R2_BUCKET_NAME` | Private bucket name |
| `R2_ACCOUNT_ID` | Optional for Cloudflare endpoint construction; not required when an explicit endpoint is set |

The adapter's legacy `r2` provider name covers Backblaze B2 and Cloudflare R2. Files remain private and database records store internal object keys, not public URLs. Existing local-file records remain readable after selecting `r2`; changing the setting does not migrate files.

## API and runtime

| Variable | Purpose |
|---|---|
| `PORT` | Server port; supplied by Render/Cloud Run, defaults to 8080 in the backend image |
| `FRONTEND_URL` | Comma-separated allowed browser origins, such as `https://chat.example.com` |
| `LOG_LEVEL` | Backend log level (default `INFO`) |
| `VITE_API_BASE_URL` | Public API URL baked into the frontend build; never a secret |

Never expose `DATABASE_AUTH_TOKEN`, `TURSO_AUTH_TOKEN`, Gemini keys, or `R2_SECRET_ACCESS_KEY` through `VITE_*` variables, frontend bundles, logs, or checked-in files.
